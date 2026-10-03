"""An isolated, disposable OpsPilot environment for one evaluation run.

Evaluation changes state on purpose — it bumps order versions, ages proposals,
tampers with stored payloads, interrupts deliveries halfway — so it never touches the
development database. Each run gets:

* a fresh PostgreSQL database (``opspilot_eval_<id>_test``), migrated and seeded with
  the deterministic dataset, dropped afterwards unless ``keep`` is set;
* three Redis database indexes of its own (budget, broker, results), flushed before
  and after;
* its own API process on a free port — which is also the simulated ERP boundary;
* its own development signing key.

Worker code runs in this process, synchronously, through the entry points the Celery
tasks call (``process_run``, ``process_action``, ``index_version``). The queue hop is
the one production step not exercised here; ``scripts/probe_worker_recovery.py``
covers it with real worker processes.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "apps" / "api"
SEED_DAY = "2026-09-12"
#: Redis database indexes owned by evaluation runs: budget, broker, results.
REDIS_DBS = (13, 14, 15)


@dataclass
class Environment:
    directory: Path
    database: str
    base_url: str
    api: subprocess.Popen[bytes]
    live: bool


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _redis_url(base: str, db: int) -> str:
    parsed = urlparse(base)
    return urlunparse(parsed._replace(path=f"/{db}"))


def _flush(urls: list[str]) -> None:
    import redis

    for url in urls:
        client = redis.Redis.from_url(url, socket_timeout=3)
        try:
            client.flushdb()
        finally:
            client.close()


def _reset_process_singletons() -> None:
    """Settings and engines are cached per process; drop anything resolved earlier."""
    from opspilot.core.config import get_settings
    from opspilot.db import session

    get_settings.cache_clear()
    if session._engine is not None:
        session._engine.dispose()
    session._engine = None
    session._session_factory = None


@contextmanager
def isolated(*, live: bool, keep: bool = False) -> Iterator[Environment]:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from opspilot.core.config import get_settings

    base = get_settings()
    url = make_url(base.database_url)
    hosts = {url.host, urlparse(base.redis_url).hostname}
    if not base.app_env.is_local or not hosts <= {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Evaluation needs a local development database server and Redis.")

    run_id = uuid.uuid4().hex[:12]
    database = f"opspilot_eval_{run_id}_test"
    directory = ROOT / ".data" / f"eval_{run_id}"
    directory.mkdir(parents=True)
    port = _free_port()
    redis_urls = [_redis_url(base.redis_url, db) for db in REDIS_DBS]
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    saved_environment = dict(os.environ)
    api: subprocess.Popen[bytes] | None = None
    created = False
    log = (directory / "api.log").open("wb")
    try:
        with admin.connect() as connection:
            # The name is generated here from a UUID, never from user input.
            connection.exec_driver_sql(f'CREATE DATABASE "{database}" TEMPLATE template0')
        created = True
        scratch = create_engine(url.set(database=database))
        with scratch.begin() as connection:
            for extension in ("vector", "pg_trgm", "pgcrypto"):
                connection.exec_driver_sql(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
        scratch.dispose()

        environment = {
            "DATABASE_URL": url.set(database=database).render_as_string(hide_password=False),
            "REDIS_URL": redis_urls[0],
            "CELERY_BROKER_URL": redis_urls[1],
            "CELERY_RESULT_BACKEND": redis_urls[2],
            "APP_ENV": "test",
            "AUTH_MODE": "dev",
            "OPS_API_BASE_URL": f"http://127.0.0.1:{port}/ops",
            "DEV_AUTH_SIGNING_KEY_PATH": str(directory / "dev-auth-key.pem"),
            "OPERATIONS_AS_OF": SEED_DAY,
            # One evaluation user runs dozens of investigations in a day by design.
            "BUDGET_PER_USER_DAILY_RUNS": "100000",
            "FEATURE_WRITES_ENABLED": "true",
            "FEATURE_AGENT_ENABLED": "true",
            "LOG_FORMAT": "json",
            "LOG_LEVEL": "WARNING",
            "PYTHONUTF8": "1",
        }
        if not live:
            environment.update({"LLM_PROVIDER": "fake", "EMBEDDING_PROVIDER": "fake"})
        os.environ.update(environment)
        _reset_process_singletons()
        from opspilot.core.logging import configure_logging

        configure_logging(get_settings())  # worker code runs in this process too
        _flush(redis_urls)

        python = sys.executable
        with (directory / "setup.log").open("wb") as setup:
            for command in (
                [python, "-m", "alembic", "upgrade", "head"],
                [python, str(ROOT / "scripts" / "seed_dev_users.py")],
                [
                    python,
                    str(ROOT / "data" / "seed" / "generate.py"),
                    "--scenarios-out",
                    str(directory / "seed-scenarios.json"),
                ],
            ):
                subprocess.run(  # noqa: S603 -- fixed local interpreter and scripts
                    command, cwd=API_DIR, stdout=setup, stderr=subprocess.STDOUT, check=True
                )
        api = subprocess.Popen(  # noqa: S603 -- fixed local interpreter, no shell
            [
                python,
                "-m",
                "uvicorn",
                "opspilot.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=API_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while True:
            if api.poll() is not None:
                raise SystemExit(f"The evaluation API exited; see {directory / 'api.log'}")
            try:
                if httpx.get(base_url + "/health/ready", timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise SystemExit("The evaluation API did not become ready within 60 s.")
            time.sleep(0.3)
        yield Environment(directory, database, base_url, api, live)
    finally:
        if api is not None and api.poll() is None:
            api.kill()
            api.wait(timeout=15)
        log.close()
        _reset_process_singletons()
        os.environ.clear()
        os.environ.update(saved_environment)
        if created and not keep:
            with admin.connect() as connection:
                # The name was generated from a UUID above, never taken from input.
                terminate = (
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "  # noqa: S608
                    f"WHERE datname = '{database}' AND pid <> pg_backend_pid()"
                )
                connection.exec_driver_sql(terminate)
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database}"')
        admin.dispose()
        with contextlib.suppress(Exception):  # best effort: only this run's own keys
            _flush(redis_urls)
