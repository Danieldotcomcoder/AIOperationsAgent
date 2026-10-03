"""Real local Celery process-kill/restart and beat recovery drill.

Run from apps/api: uv run python ../../scripts/probe_worker_recovery.py
Requires local PostgreSQL (with permission to create databases) and Redis. Creates
and drops its own database; never changes the normal development database. All child
processes and broker keys belong to this invocation. No model credentials required.
"""

# ruff: noqa: T201 -- standalone drill reports progress to the terminal

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path
from threading import Event

import httpx
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
sys.path.insert(0, str(API / "src"))

from opspilot.core.clock import utcnow  # noqa: E402
from opspilot.core.config import get_settings  # noqa: E402
from opspilot.db.repository import TenantScopedRepository  # noqa: E402
from opspilot.db.session import get_engine, session_scope  # noqa: E402
from opspilot.domain.enums import ActionStatus  # noqa: E402
from opspilot.domain.hashing import idempotency_key  # noqa: E402
from opspilot.models import OperationTask, OpsTask, OutboxEvent, ProposedAction  # noqa: E402


def wait_for(check, description, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {description}")


def configure_celery(namespace):
    from opspilot.workers.app import celery_app

    # Keep this drill's queues, unacknowledged messages and results separate from
    # existing local workers. No FLUSHDB or purge of the developer's queues.
    celery_app.conf.broker_transport_options = {"global_keyprefix": namespace + ":"}
    celery_app.conf.result_backend_transport_options = {"global_keyprefix": namespace + ":"}
    return celery_app


def child(args):
    if args.mode == "seed":
        import runpy

        seed_path = ROOT / "data" / "seed" / "generate.py"
        seed = runpy.run_path(str(seed_path))
        # Keep this fixture's output away from any existing human-reviewed labels.
        seed["main"].__globals__["SCENARIO_OUTPUT"] = args.marker
        sys.argv = [str(seed_path)]
        seed["main"]()
        return

    from opspilot.services.budget import BudgetKeys

    original_keys = BudgetKeys.for_now

    def budget_keys(organization_id, membership_id, now):
        keys = original_keys(organization_id, membership_id, now)
        return BudgetKeys(
            *(args.namespace + ":" + key for key in (keys.day, keys.month, keys.user_runs))
        )

    BudgetKeys.for_now = staticmethod(budget_keys)
    app = configure_celery(args.namespace)
    if args.mode == "api":
        import uvicorn

        uvicorn.run("opspilot.main:app", host="127.0.0.1", port=args.port)
    elif args.mode == "beat":
        # Exercise the actual scheduled task through Redis and a Celery worker.
        # Shorten only the polling interval; the production 60-second age gate is
        # unchanged. The parent explicitly ages the isolated fixture after a kill.
        entry = "outbox-sweep" if args.schedule == "outbox" else "reconcile-uncertain"
        spec = dict(app.conf.beat_schedule[entry])
        spec["schedule"] = 2.0
        app.conf.beat_schedule = {entry: spec}
        app.Beat(schedule=str(args.marker), max_interval=1, loglevel="INFO").run()
    else:
        from celery.signals import task_prerun

        from opspilot.ops.client import OperationsClient

        @task_prerun.connect(weak=False)
        def record_task(task=None, args=None, **kwargs):
            with Path(str(args_path)).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"task": task.name, "args": args}) + "\n")

        args_path = args.marker.with_suffix(".tasks.jsonl")
        original = OperationsClient.create

        def create(client, key, payload):
            def pause():
                args.marker.write_text(json.dumps({"phase": args.phase, "key": key}))
                if not Event().wait(120):
                    raise RuntimeError("Parent failed to terminate the fault-injection worker")

            if key == args.key and args.phase == "before":
                pause()
            result = original(client, key, payload)
            if key == args.key and args.phase == "after":
                pause()
            return result

        OperationsClient.create = create
        app.worker_main(
            [
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "--hostname=" + args.marker.stem + "@%h",
                "-Q",
                args.queues,
            ]
        )


def main(args):
    settings = get_settings()
    url = make_url(settings.database_url)
    if not settings.app_env.is_local or url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("Recovery drill requires a local development/test database server")
    for redis_url in (
        settings.redis_url,
        settings.celery_broker_url,
        settings.celery_result_backend,
    ):
        if make_url(redis_url).host not in {"localhost", "127.0.0.1", "::1"}:
            raise SystemExit("Recovery drill requires local Redis")

    namespace = "recovery_" + uuid.uuid4().hex
    database = "opspilot_" + namespace + "_test"
    directory = ROOT / ".data" / namespace
    directory.mkdir(parents=True)
    children = []
    logs = []
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created = False
    old_environment = os.environ.copy()

    def start(mode, label, **options):
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--mode",
            mode,
            "--namespace",
            namespace,
            "--port",
            str(args.port),
            "--marker",
            str(directory / label),
        ]
        for key, value in options.items():
            command.extend(["--" + key, str(value)])
        log = (directory / (label + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(  # noqa: S603 -- fixed interpreter/script, no shell
            command,
            cwd=API,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        children.append(process)
        return process

    def stop(process):
        if process.poll() is None:
            process.kill()  # TerminateProcess on Windows; SIGKILL on Unix.
        process.wait(timeout=15)

    try:
        with admin.connect() as connection:
            # Name is generated entirely here from a UUID, never from user input.
            connection.exec_driver_sql(f'CREATE DATABASE "{database}" TEMPLATE template0')
        created = True
        fixture_engine = create_engine(url.set(database=database))
        try:
            with fixture_engine.begin() as connection:
                for extension in ("vector", "pg_trgm", "pgcrypto"):
                    connection.exec_driver_sql(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
        finally:
            fixture_engine.dispose()
        os.environ.update(
            {
                "DATABASE_URL": url.set(database=database).render_as_string(hide_password=False),
                "APP_ENV": "test",
                "AUTH_MODE": "dev",
                "LLM_PROVIDER": "fake",
                "EMBEDDING_PROVIDER": "fake",
                "FEATURE_WRITES_ENABLED": "true",
                "FEATURE_AGENT_ENABLED": "true",
                "OPS_API_BASE_URL": f"http://127.0.0.1:{args.port}/ops",
                "DEV_AUTH_SIGNING_KEY_PATH": str(directory / "dev-auth-key.pem"),
            }
        )
        get_settings.cache_clear()
        setup_log = directory / "setup.log"
        with setup_log.open("w", encoding="utf-8") as stream:
            for command in (
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                [sys.executable, str(ROOT / "scripts" / "seed_dev_users.py")],
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--mode",
                    "seed",
                    "--marker",
                    str(directory / "seed-scenarios.json"),
                ],
            ):
                subprocess.run(  # noqa: S603 -- fixed local setup scripts, no shell
                    command,
                    cwd=API,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=60,
                )
        app = configure_celery(namespace)
        api = start("api", "api")
        start("worker", "investigate", queues="investigate")
        with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=5) as client:

            def ready():
                if api.poll() is not None:
                    raise AssertionError("Probe API exited; see api.log")
                try:
                    return client.get("/health/ready").status_code == 200
                except httpx.TransportError:
                    return False

            wait_for(ready, "isolated API")
            token = (
                client.post(
                    "/auth/dev/token", json={"email": "sam.supervisor@northwind.example.com"}
                )
                .raise_for_status()
                .json()["access_token"]
            )
            client.headers["Authorization"] = "Bearer " + token
            org = uuid.UUID(client.get("/me").raise_for_status().json()["organization_id"])
            report = []
            for phase in ("normal", "before", "after"):
                killed_pid = None
                committed_id = None
                run = (
                    client.post(
                        "/runs",
                        json={
                            "request_text": "Investigate SO-2084. Synthetic recovery drill.",
                            "order_number": "SO-2084",
                        },
                    )
                    .raise_for_status()
                    .json()
                )

                def proposal(run=run):
                    row = client.get(f"/runs/{run['id']}").raise_for_status().json()
                    if row["status"] in {"failed", "completed"}:
                        raise AssertionError(f"Investigation produced no proposal: {row['status']}")
                    return row.get("proposal_id")

                action_id = uuid.UUID(wait_for(proposal, "proposal"))
                action = client.get(f"/actions/{action_id}").raise_for_status().json()
                key = idempotency_key(org, action_id, action["payload_hash"])
                worker = start("worker", phase, queues="execute,outbox", phase=phase, key=key)
                client.post(
                    f"/actions/{action_id}/approve", json={"payload_hash": action["payload_hash"]}
                ).raise_for_status()
                beat = start("beat", phase + "-beat", schedule="outbox")

                def succeeded(action_id=action_id):
                    row = client.get(f"/actions/{action_id}").raise_for_status().json()
                    if row["status"] in {"failed", "expired"}:
                        raise AssertionError(f"Unexpected terminal status: {row['status']}")
                    return row if row["status"] == "succeeded" else None

                if phase != "normal":
                    marker = directory / phase
                    wait_for(marker.exists, phase + " downstream commit boundary")
                    assert json.loads(marker.read_text())["key"] == key
                    stop(beat)
                    killed_pid = worker.pid
                    stop(worker)
                    assert worker.returncode != 0
                    with session_scope() as session:
                        repo = TenantScopedRepository(session, org)
                        stored = repo.get_or_404(ProposedAction, action_id)
                        assert stored.status == ActionStatus.executing
                        event = repo.find_by(OutboxEvent, aggregate_id=action_id)
                        assert event.published_at is not None
                        committed = repo.list_by(OpsTask, idempotency_key=key)
                        assert len(committed) == int(phase == "after")
                        committed_id = str(committed[0].id) if committed else None
                        operation = repo.find_by(OperationTask, proposed_action_id=action_id)
                        assert operation.downstream_task_id is None
                        # Keep the real age predicate but avoid a minute of idle time.
                        stored.updated_at = utcnow() - timedelta(seconds=61)
                    print(
                        f"PASS {phase}: killed worker; durable claim and ERP state verified",
                        flush=True,
                    )
                    worker = start("worker", phase + "-restart", queues="execute,outbox")
                    beat = start("beat", phase + "-recovery-beat", schedule="recovery")

                finished = wait_for(succeeded, phase + " scheduled recovery")
                if committed_id:
                    assert finished["downstream_task_id"] == committed_id
                stop(beat)
                if phase != "normal":
                    received = directory / (phase + "-restart.tasks.jsonl")
                    records = [json.loads(line) for line in received.read_text().splitlines()]
                    assert any(
                        row == {"task": "action.reconcile", "args": [str(action_id)]}
                        for row in records
                    )
                for _ in range(3):
                    app.send_task("action.execute", args=[str(action_id)], queue="execute").get(
                        timeout=20
                    )
                with session_scope() as session:
                    repo = TenantScopedRepository(session, org)
                    tasks = repo.list_by(OpsTask, idempotency_key=key)
                    assert len(tasks) == 1
                    assert str(tasks[0].id) == finished["downstream_task_id"]
                    operation = repo.find_by(OperationTask, proposed_action_id=action_id)
                    assert operation.downstream_task_id == str(tasks[0].id)
                stop(worker)
                report.append(
                    {
                        "phase": phase,
                        "action_id": str(action_id),
                        "task_id": finished["downstream_task_id"],
                        "downstream_count": 1,
                        "killed_worker_pid": killed_pid,
                        "task_id_before_recovery": committed_id,
                    }
                )
                print(
                    f"PASS {phase}: live beat -> worker -> one ERP task; duplicates harmless",
                    flush=True,
                )
            (directory / "report.json").write_text(
                json.dumps(
                    {
                        "provider": "fake",
                        "worker_pool": "solo",
                        "platform": sys.platform,
                        "beat_interval_seconds": 2,
                        "fixture_age_seconds": 61,
                        "database": database,
                        "cases": report,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"Recovery evidence: {directory / 'report.json'}", flush=True)
    finally:
        for process in reversed(children):
            stop(process)
        for stream in logs:
            stream.close()
        if created:
            get_engine().dispose()
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        admin.dispose()
        # Delete only this invocation's Redis prefix, including killed-worker unacked
        # entries. Never touch the normal broker/result keys or flush an entire DB.
        import redis

        for redis_url in {
            settings.redis_url,
            settings.celery_broker_url,
            settings.celery_result_backend,
        }:
            with redis.Redis.from_url(redis_url) as connection:
                keys = list(connection.scan_iter(match=namespace + ":*"))
                if keys:
                    connection.delete(*keys)
        os.environ.clear()
        os.environ.update(old_environment)
        get_settings.cache_clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["parent", "api", "worker", "beat", "seed"], default="parent"
    )
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--namespace", default="")
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--queues", default="execute,outbox")
    parser.add_argument("--phase", default="normal")
    parser.add_argument("--key", default="")
    parser.add_argument("--schedule", choices=["outbox", "recovery"], default="outbox")
    arguments = parser.parse_args()
    if arguments.mode == "parent":
        main(arguments)
    else:
        child(arguments)
