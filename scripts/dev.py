"""Run the whole local stack with one command, and stop all of it with Ctrl+C.

    pnpm stack                  # from the repository root
    pnpm stack --fake           # force the scripted model and word-hash embeddings
    uv run --project apps/api python scripts/dev.py --help

Starts PostgreSQL and Redis (Docker Compose) if they are not reachable, applies
migrations, seeds the synthetic dataset when the database is empty, then runs the API,
an investigation worker, an execution/outbox worker, the beat scheduler and the web
dev server. Each process logs to ``.data/dev/<name>.log``; warnings and errors are
echoed here. Nothing is deployed and nothing outside this machine is touched, except
the model provider your ``.env`` selects when you start an investigation.
"""

# ruff: noqa: T201, S603, S607 -- a local developer launcher printing progress and
# starting fixed local executables (python, node, docker) without a shell.

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"
LOGS = ROOT / ".data" / "dev"
WINDOWS = os.name == "nt"

ACCOUNTS = [
    ("ana.analyst@northwind.example.com", "analyst", "Northwind"),
    ("sam.supervisor@northwind.example.com", "supervisor", "Northwind"),
    ("hal.analyst@harbor.example.com", "analyst", "Harbor"),
    ("hana.supervisor@harbor.example.com", "supervisor", "Harbor"),
]
NOISE = ("DeprecationWarning", "_PortalFactoryType", "logfile ->")
VERBOSE = False


def say(message: str) -> None:
    print(f"[stack] {message}", flush=True)


class Child:
    """One supervised process: its own process group, its own log file."""

    def __init__(self, name: str, command: list[str], cwd: Path, env: dict[str, str]) -> None:
        self.name = name
        self.log_path = LOGS / f"{name}.log"
        self._log = self.log_path.open("w", encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=not WINDOWS,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        stream: IO[bytes] | None = self.process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            self._log.write(line + "\n")
            self._log.flush()
            lowered = line.lower()
            if VERBOSE or (
                any(word in lowered for word in ("error", "traceback", "warning", "exception"))
                and not any(noise in line for noise in NOISE)
            ):
                print(f"[{self.name}] {line[:400]}", flush=True)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        if self.alive:
            if WINDOWS:
                # The whole tree: node and Celery can own child processes.
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=10)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    os.killpg(self.process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=10)
        self._reader.join(timeout=5)  # drain what the process wrote before it stopped
        self._log.close()


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def wait(check: Callable[[], bool], what: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.5)
    raise SystemExit(f"[stack] timed out waiting for {what}")


def run(command: list[str], cwd: Path, env: dict[str, str], what: str) -> None:
    say(what)
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stdout[-3000:], result.stderr[-3000:], sep="\n")
        raise SystemExit(f"[stack] failed: {what}")


def ensure_data_services(env: dict[str, str]) -> None:
    from sqlalchemy.engine import make_url

    from opspilot.core.config import get_settings

    settings = get_settings()
    db = make_url(settings.database_url)
    redis = urlparse(settings.redis_url)
    targets = [
        (db.host or "127.0.0.1", db.port or 5432),
        (redis.hostname or "127.0.0.1", redis.port or 6379),
    ]
    if all(port_open(host, port) for host, port in targets):
        return
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("[stack] PostgreSQL/Redis are not reachable and Docker is not installed.")
    say("starting PostgreSQL and Redis containers (docker compose up -d postgres redis)")
    result = subprocess.run(
        [docker, "compose", "up", "-d", "postgres", "redis"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr[-2000:])
        raise SystemExit("[stack] could not start the data containers. Is Docker Desktop running?")
    for host, port in targets:
        wait(lambda h=host, p=port: port_open(h, p), f"{host}:{port}", 90)
    # A published port answers before PostgreSQL accepts logins; give it a moment.
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 3})

    def database_ready() -> bool:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    wait(database_ready, "PostgreSQL to accept connections", 60)
    engine.dispose()


def seed_if_empty(env: dict[str, str]) -> None:
    from sqlalchemy import create_engine, func, select

    from opspilot.core.config import get_settings
    from opspilot.models import Membership, Order

    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        members = connection.scalar(select(func.count()).select_from(Membership)) or 0
        orders = connection.scalar(select(func.count()).select_from(Order)) or 0
    engine.dispose()
    python = sys.executable
    if members == 0:
        run([python, str(ROOT / "scripts" / "seed_dev_users.py")], API, env, "seeding identities")
    if orders == 0:
        run([python, str(ROOT / "data" / "seed" / "generate.py")], API, env, "seeding the dataset")


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--fake", action="store_true", help="scripted model + word-hash embeddings")
    parser.add_argument("--no-web", action="store_true", help="do not start the web dev server")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--verbose", action="store_true", help="echo every log line")
    parser.add_argument(
        "--stop-data", action="store_true", help="also stop PostgreSQL/Redis containers on exit"
    )
    args = parser.parse_args()
    VERBOSE = args.verbose

    env = dict(os.environ)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "LOG_FORMAT": env.get("LOG_FORMAT", "console"),
            "OPS_API_BASE_URL": f"http://127.0.0.1:{args.api_port}/ops",
            "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{args.api_port}",
        }
    )
    if args.fake:
        env.update({"LLM_PROVIDER": "fake", "EMBEDDING_PROVIDER": "fake"})
    # Settings are read once per process; this one must see the same overrides.
    os.environ.update(env)
    sys.path.insert(0, str(API / "src"))

    if not (ROOT / ".env").exists():
        raise SystemExit("[stack] no .env: copy .env.example to .env first (it runs keyless).")
    for port in [args.api_port] + ([] if args.no_web else [args.web_port]):
        if port_open("127.0.0.1", port):
            raise SystemExit(
                f"[stack] port {port} is already in use. Stop that process or pick another port."
            )
    if not args.no_web and not (WEB / "node_modules" / "vite" / "bin" / "vite.js").exists():
        raise SystemExit("[stack] web dependencies are missing: run `pnpm install` first.")

    LOGS.mkdir(parents=True, exist_ok=True)
    ensure_data_services(env)
    python = sys.executable
    run([python, "-m", "alembic", "upgrade", "head"], API, env, "applying migrations")
    seed_if_empty(env)

    from opspilot.core.config import get_settings

    settings = get_settings()
    pool = ["--pool=solo"] if WINDOWS else ["--concurrency=2"]
    celery = [python, "-m", "celery", "-A", "opspilot.workers.app:celery_app"]
    children: list[Child] = []

    def shutdown(code: int) -> int:
        say("stopping…")
        for child in reversed(children):
            child.stop()
        if args.stop_data and shutil.which("docker"):
            subprocess.run(
                ["docker", "compose", "stop", "postgres", "redis"], cwd=ROOT, check=False
            )
            say("data containers stopped (volumes kept)")
        else:
            say("PostgreSQL/Redis still run; stop them with `docker compose stop postgres redis`")
        return code

    try:
        children.append(
            Child(
                "api",
                [
                    python,
                    "-m",
                    "uvicorn",
                    "opspilot.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                ],
                API,
                env,
            )
        )
        wait(lambda: port_open("127.0.0.1", args.api_port) or not children[0].alive, "the API", 60)
        if not children[0].alive:
            return shutdown(1)
        children.append(
            Child(
                "worker-investigate",
                [*celery, "worker", "-Q", "investigate", *pool, "-n", "investigate@%h"],
                API,
                env,
            )
        )
        children.append(
            Child(
                "worker-execute",
                [
                    *celery,
                    "worker",
                    "-Q",
                    "execute,outbox",
                    "--pool=solo" if WINDOWS else "--concurrency=1",
                    "-n",
                    "execute@%h",
                ],
                API,
                env,
            )
        )
        children.append(
            Child(
                "beat", [*celery, "beat", "--schedule", str(LOGS / "celerybeat-schedule")], API, env
            )
        )
        if not args.no_web:
            children.append(
                Child(
                    "web",
                    [
                        "node",
                        str(WEB / "node_modules" / "vite" / "bin" / "vite.js"),
                        "--port",
                        str(args.web_port),
                        "--strictPort",
                    ],
                    WEB,
                    env,
                )
            )
            wait(
                lambda: port_open("127.0.0.1", args.web_port) or not children[-1].alive,
                "the web server",
                60,
            )

        model = (
            "scripted development model"
            if settings.llm_provider == "fake"
            else f"{settings.llm_provider} · {settings.llm_model}"
        )
        print()
        say(
            f"OpsPilot is running    http://127.0.0.1:{args.web_port}"
            if not args.no_web
            else "OpsPilot API is running"
        )
        say(f"API                    http://127.0.0.1:{args.api_port}/docs")
        say(f"model                  {model}")
        say(f"business date          {settings.operations_as_of or 'today (UTC)'}")
        say(f"logs                   {LOGS}")
        say("sign in as (one click on the sign-in page):")
        for email, role, org in ACCOUNTS:
            say(f"    {email:40} {role:10} {org}")
        say("press Ctrl+C to stop everything")
        print()

        while True:
            for child in children:
                if not child.alive:
                    code = child.process.returncode
                    say(f"{child.name} exited with code {code}; see {child.log_path}")
                    return shutdown(1)
            time.sleep(1)
    except KeyboardInterrupt:
        return shutdown(0)


if __name__ == "__main__":
    raise SystemExit(main())
