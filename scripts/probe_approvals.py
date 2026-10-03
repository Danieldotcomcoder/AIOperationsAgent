"""Local, synthetic approval/ERP smoke test including concurrency and lost commits.

Requires fake mode, migrated/seeded PostgreSQL, API and investigate/execute workers.
Creates two synthetic approved tasks and retains their audit records.
Run from apps/api: uv run python ../../scripts/probe_approvals.py
"""

import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api" / "src"))

from opspilot.core.config import get_settings  # noqa: E402
from opspilot.db.repository import TenantScopedRepository  # noqa: E402
from opspilot.db.session import session_scope  # noqa: E402
from opspilot.models import OperationTask, OpsTask  # noqa: E402
from opspilot.ops.client import OperationsClient  # noqa: E402
from opspilot.services.execution import deliver, prepare  # noqa: E402
from opspilot.workers.app import celery_app  # noqa: E402
from opspilot.workers.tasks import dispatch_outbox  # noqa: E402


def wait_for(client, path, terminal):
    for _ in range(60):
        row = client.get(path).raise_for_status().json()
        if row["status"] in terminal:
            return row
        time.sleep(0.5)
    raise AssertionError(f"Timed out: {path}")


def proposal(client):
    run = client.post("/runs", json={
        "request_text": "Investigate SO-2084. Synthetic approval probe.",
        "order_number": "SO-2084"}).raise_for_status().json()
    run = wait_for(client, f"/runs/{run['id']}", {"needs_review", "completed", "failed"})
    assert run["proposal_id"], run
    action = client.get(f"/actions/{run['proposal_id']}").raise_for_status().json()
    assert action["payload_valid"]
    return action


def main():
    config = get_settings()
    assert config.llm_provider == "fake", "Probe only runs with the no-key fake provider"
    config = config.model_copy(update={"ops_api_base_url": "http://127.0.0.1:8000/ops"})
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=20) as client:
        token = client.post("/auth/dev/token", json={"email": "sam.supervisor@northwind.example.com"})
        client.headers["Authorization"] = "Bearer " + token.raise_for_status().json()["access_token"]
        org_id = uuid.UUID(client.get("/me").raise_for_status().json()["organization_id"])
        action = proposal(client)

        def approve():
            return client.post(f"/actions/{action['id']}/approve",
                               json={"payload_hash": action["payload_hash"]}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: approve(), range(2)))
        assert sorted(results) == [200, 409], results
        print("PASS concurrent approvals: one accepted, one conflicts")
        dispatch_outbox.run()
        finished = wait_for(client, f"/actions/{action['id']}", {"succeeded", "failed", "expired"})
        assert finished["status"] == "succeeded", finished
        for _ in range(3):
            task = celery_app.send_task("action.execute", args=[action["id"]], queue="execute")
            task.get(timeout=20)
        with session_scope() as session:
            repo = TenantScopedRepository(session, org_id)
            operation = repo.find_by(OperationTask, proposed_action_id=uuid.UUID(action["id"]))
            tasks = repo.list_by(OpsTask, idempotency_key=operation.idempotency_key)
            assert len(tasks) == 1 and str(tasks[0].id) == finished["downstream_task_id"]
        print("PASS approval -> outbox -> worker -> real HTTP ERP; duplicate deliveries create one task")

        crash_action = proposal(client)
        client.post(f"/actions/{crash_action['id']}/approve", json={
            "payload_hash": crash_action["payload_hash"]}).raise_for_status()
        action_id = uuid.UUID(crash_action["id"])
        with session_scope() as session:
            assert prepare(session, config, org_id, action_id)
        # Failure injection: downstream committed, local acknowledgement transaction lost.
        with session_scope() as session:
            deliver(session, config, org_id, action_id, OperationsClient(config))
            session.rollback()
        task = celery_app.send_task("action.reconcile", args=[str(action_id)], queue="execute")
        task.get(timeout=20)
        recovered = client.get(f"/actions/{action_id}").raise_for_status().json()
        assert recovered["status"] == "succeeded", recovered
        with session_scope() as session:
            repo = TenantScopedRepository(session, org_id)
            operation = repo.find_by(OperationTask, proposed_action_id=action_id)
            assert len(repo.list_by(OpsTask, idempotency_key=operation.idempotency_key)) == 1
        dispatch_outbox.run()  # remaining original message is a harmless duplicate
        print("PASS lost local commit after ERP creation: reconciliation recovers the same task")


if __name__ == "__main__":
    main()
