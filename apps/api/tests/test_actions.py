import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from opspilot.agent.orchestrator import RunContext, RunOutcome
from opspilot.agent.schemas import InvestigationResult, ProposedActionDraft
from opspilot.auth.dependencies import get_principal
from opspilot.core.clock import utcnow
from opspilot.core.config import Settings
from opspilot.core.errors import (
    DownstreamUncertain,
    IllegalTransition,
    NotAuthorized,
    ProposalExpired,
    ResourceNotFound,
    StaleProposal,
    WritesDisabled,
)
from opspilot.db.session import db_session
from opspilot.domain.enums import (
    ActionStatus,
    MembershipStatus,
    Role,
    RunStatus,
)
from opspilot.domain.hashing import payload_hash
from opspilot.main import create_app
from opspilot.models import (
    Approval,
    OperationTask,
    OpsTask,
    OutboxEvent,
)
from opspilot.ops.contracts import TaskResponse
from opspilot.services import actions, execution

CONFIG = Settings(_env_file=None)


def decide(db, case, approve=True, config=CONFIG):
    return actions.decide(
        db, config, case.principal, case.action.id, case.action.payload_hash, None, approve=approve
    )


def test_approval_is_atomic_and_duplicate_decision_conflicts(db, case):
    result = decide(db, case)
    assert result["status"] == ActionStatus.approved
    assert case.run.status == RunStatus.completed
    assert case.repo.find_by(Approval, proposed_action_id=case.action.id) is not None
    assert case.repo.find_by(OutboxEvent, aggregate_id=case.action.id) is not None
    with pytest.raises(IllegalTransition):
        decide(db, case)
    assert case.repo.find_by(OperationTask, proposed_action_id=case.action.id) is None


def test_rejection_never_creates_execution(db, case):
    assert decide(db, case, False)["status"] == ActionStatus.rejected
    assert case.repo.find_by(OutboxEvent, aggregate_id=case.action.id) is None
    assert not execution.prepare(db, CONFIG, case.org.id, case.action.id)


@pytest.mark.parametrize(
    "change,error",
    [
        ("source", StaleProposal),
        ("payload", StaleProposal),
        ("hash", StaleProposal),
        ("expiry", ProposalExpired),
        ("role", NotAuthorized),
        ("revoked", NotAuthorized),
    ],
)
def test_stale_or_unauthorized_approval_has_no_effect(db, case, change, error):
    if change == "source":
        case.order.notes = "changed after proposal"
    elif change == "payload":
        case.action.payload_json = {**case.action.payload_json, "description": "tampered"}
    elif change == "hash":
        case.action.payload_hash = "f" * 64
    elif change == "expiry":
        case.action.expires_at = utcnow() - timedelta(seconds=1)
    elif change == "role":
        case.member.role = Role.analyst
    else:
        case.member.status = MembershipStatus.revoked
    db.flush()
    with pytest.raises(error):
        decide(db, case)
    assert case.repo.find_by(Approval, proposed_action_id=case.action.id) is None
    assert case.repo.find_by(OutboxEvent, aggregate_id=case.action.id) is None


def test_hash_echo_and_cross_tenant_not_found(db, case):
    with pytest.raises(StaleProposal):
        actions.decide(db, CONFIG, case.principal, case.action.id, "0" * 64, None, approve=True)
    with pytest.raises(ResourceNotFound):
        actions.get_action(
            db, replace(case.principal, organization_id=uuid.uuid4()), case.action.id
        )
    with pytest.raises(WritesDisabled):
        decide(db, case, config=CONFIG.model_copy(update={"feature_writes_enabled": False}))


class Downstream:
    def __init__(self, case, *, lose_response=False, unavailable=False):
        self.case = case
        self.lose_response = lose_response
        self.unavailable = unavailable
        self.result = None
        self.writes = 0
        self.lookups = 0

    def lookup(self, key):
        self.lookups += 1
        if self.unavailable:
            raise DownstreamUncertain("lookup unavailable")
        return self.result

    def create(self, key, payload):
        self.writes += 1
        self.result = TaskResponse(
            task_id=str(uuid.uuid4()),
            organization_id=self.case.org.id,
            order_id=self.case.order.id,
            idempotency_key=key,
            status="open",
            created_at=utcnow(),
        )
        if self.lose_response:
            raise DownstreamUncertain("response lost after task created")
        return self.result


def test_success_and_duplicate_worker_delivery(db, case):
    decide(db, case)
    client = Downstream(case)
    assert execution.prepare(db, CONFIG, case.org.id, case.action.id)
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    db.flush()
    assert case.action.status == ActionStatus.succeeded
    assert not execution.prepare(db, CONFIG, case.org.id, case.action.id)
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    assert client.writes == 1


def test_lost_response_reconciles_without_second_write(db, case):
    decide(db, case)
    client = Downstream(case, lose_response=True)
    execution.prepare(db, CONFIG, case.org.id, case.action.id)
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    db.flush()
    assert case.action.status == ActionStatus.reconciliation_required
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    assert case.action.status == ActionStatus.succeeded
    assert client.writes == 1


def test_unavailable_lookup_never_blindly_retries(db, case):
    decide(db, case)
    client = Downstream(case, unavailable=True)
    execution.prepare(db, CONFIG, case.org.id, case.action.id)
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    db.flush()
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    assert case.action.status == ActionStatus.reconciliation_required
    assert client.writes == 0
    client.unavailable = False
    db.flush()
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    assert client.writes == 1


def test_worker_rechecks_revocation_and_allows_late_delivery(db, case):
    decide(db, case)
    # The approval was valid when decided; delivery time is later than the expiry.
    approval = case.repo.find_by(Approval, proposed_action_id=case.action.id)
    approval.decided_at = utcnow() - timedelta(hours=2)
    case.action.expires_at = utcnow() - timedelta(hours=1)
    db.flush()
    assert execution.prepare(db, CONFIG, case.org.id, case.action.id)
    case.member.status = MembershipStatus.revoked
    db.flush()
    client = Downstream(case)
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    assert client.writes == 0
    assert case.action.status == ActionStatus.failed


def test_outbox_failure_is_retained_and_retry_is_idempotent(db, case):
    decide(db, case)
    event = case.repo.find_by(OutboxEvent, aggregate_id=case.action.id)

    def unavailable(*args, **kwargs):
        raise ConnectionError("broker down")

    assert not execution.publish_event(db, case.org.id, event.id, unavailable)
    assert event.published_at is None
    assert event.attempts == 1
    assert event.last_error == "ConnectionError"
    event.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.flush()
    calls = []
    assert execution.publish_event(db, case.org.id, event.id, lambda *a, **k: calls.append((a, k)))
    db.flush()
    assert not execution.publish_event(
        db, case.org.id, event.id, lambda *a, **k: calls.append((a, k))
    )
    assert len(calls) == 1


def test_expiry_and_canonical_proposal_storage(db, case):
    from opspilot.services.runs import _persist_proposal

    outcome = RunOutcome(
        result=InvestigationResult(
            summary="test",
            proposed_action=ProposedActionDraft(
                description="Contact carrier", due_in_days=2, rationale="Delayed shipment"
            ),
        )
    )
    context = RunContext(
        case.run.id, case.org.id, case.member.id, case.org.name, "test", utcnow().date()
    )
    proposal = _persist_proposal(db, CONFIG, case.run, context, outcome)
    assert payload_hash(proposal.payload_json) == proposal.payload_hash
    case.action.expires_at = utcnow() - timedelta(seconds=1)
    db.flush()
    assert actions.expire_one(db, case.org.id, case.action.id)
    assert case.action.status == ActionStatus.expired


def test_api_role_limits_and_simulated_erp_idempotence(db, case):
    app = create_app()
    app.dependency_overrides[db_session] = lambda: db
    app.dependency_overrides[get_principal] = lambda: case.principal
    with TestClient(app) as client:
        assert client.get("/actions?limit=1000").status_code == 422
        assert client.get(f"/actions/{case.action.id}").json()["payload_valid"]
        app.dependency_overrides[get_principal] = lambda: replace(case.principal, role=Role.analyst)
        assert (
            client.post(
                f"/actions/{case.action.id}/approve",
                json={"payload_hash": case.action.payload_hash},
            ).status_code
            == 403
        )
        payload = {
            k: v
            for k, v in case.action.payload_json.items()
            if k not in {"action_type", "order_number"}
        }
        payload["organization_id"] = str(case.org.id)
        assert (
            client.post(
                "/ops/tasks", json=payload, headers={"Idempotency-Key": "a" * 64}
            ).status_code
            == 401
        )
        from opspilot.core.config import get_settings

        headers = {
            "Idempotency-Key": uuid.uuid4().hex * 2,
            "Authorization": "Bearer " + get_settings().ops_api_service_token.get_secret_value(),
        }
        first = client.post("/ops/tasks", json=payload, headers=headers)
        assert first.status_code == 201, first.text
        second = client.post("/ops/tasks", json=payload, headers=headers)
        assert second.status_code == 200
        assert second.json()["task_id"] == first.json()["task_id"]
        assert (
            client.post(
                "/ops/tasks", json={**payload, "description": "different"}, headers=headers
            ).status_code
            == 409
        )
        assert case.repo.get(OpsTask, uuid.UUID(first.json()["task_id"])) is not None


def test_approval_rollback_removes_decision_and_outbox(db, case):
    savepoint = db.begin_nested()
    decide(db, case)
    savepoint.rollback()
    db.expire_all()
    assert case.action.status == ActionStatus.pending
    assert case.repo.find_by(Approval, proposed_action_id=case.action.id) is None
    assert case.repo.find_by(OutboxEvent, aggregate_id=case.action.id) is None


def test_worker_source_change_and_write_switch(db, case):
    decide(db, case)
    execution.prepare(db, CONFIG, case.org.id, case.action.id)
    client = Downstream(case)
    disabled = CONFIG.model_copy(update={"feature_writes_enabled": False})
    execution.deliver(db, disabled, case.org.id, case.action.id, client)
    db.flush()
    assert client.writes == 0
    assert case.action.status == ActionStatus.reconciliation_required
    case.order.notes = "changed before delivery"
    db.flush()
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    assert client.writes == 0
    assert case.action.status == ActionStatus.failed


def test_wrong_downstream_identity_is_not_success(db, case):
    decide(db, case)
    execution.prepare(db, CONFIG, case.org.id, case.action.id)
    client = Downstream(case)
    client.result = TaskResponse(
        task_id=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
        order_id=case.order.id,
        idempotency_key="b" * 64,
        status="open",
        created_at=utcnow(),
    )
    execution.deliver(db, CONFIG, case.org.id, case.action.id, client)
    assert case.action.status == ActionStatus.reconciliation_required
    assert client.writes == 0


@pytest.fixture
def worker_db(db, monkeypatch):
    """Run real sweep queries inside the rollback-isolated test transaction."""
    from opspilot.db import session as database

    @contextmanager
    def scope():
        yield db
        db.flush()

    monkeypatch.setattr(database, "session_scope", scope)
    return db


def test_outbox_backoff_waits_until_due_and_is_bounded(db, case, monkeypatch):
    decide(db, case)
    event = case.repo.find_by(OutboxEvent, aggregate_id=case.action.id)
    now = utcnow()
    monkeypatch.setattr(execution, "utcnow", lambda: now)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        raise ConnectionError("broker unavailable")

    for delay in [2, 4, 8, 16, 32, 64, 128, 256, 256, 256]:
        assert not execution.publish_event(db, case.org.id, event.id, unavailable)
        db.flush()
        assert event.next_attempt_at == now + timedelta(seconds=delay)
        attempts = event.attempts
        now = event.next_attempt_at - timedelta(microseconds=1)
        assert not execution.publish_event(db, case.org.id, event.id, unavailable)
        assert event.attempts == attempts
        assert len(calls) == attempts
        now = event.next_attempt_at
    published = []
    assert execution.publish_event(db, case.org.id, event.id, lambda *a, **k: published.append(k))
    db.flush()
    assert event.last_error is None
    assert event.published_at == now
    assert published == [{"args": [str(case.action.id)], "queue": "execute", "retry": False}]


@pytest.mark.parametrize("batch_size", [0, -1, 101])
def test_outbox_rejects_invalid_batch_before_database_access(batch_size):
    from opspilot.workers.tasks import dispatch_outbox

    with pytest.raises(ValueError, match="batch_size"):
        dispatch_outbox.run(batch_size=batch_size)


def test_outbox_sweep_respects_batch_and_skips_future_retries(worker_db, case, monkeypatch):
    from opspilot.workers.app import celery_app
    from opspilot.workers.tasks import dispatch_outbox

    db = worker_db
    now = utcnow()
    events = []
    for index in range(3):
        events.append(
            case.repo.add(
                OutboxEvent(
                    event_type="action.execute",
                    aggregate_type="proposed_action",
                    aggregate_id=case.action.id,
                    payload_json={},
                    created_at=now - timedelta(days=3650, seconds=3 - index),
                    next_attempt_at=now + timedelta(hours=1) if index == 0 else None,
                )
            )
        )
    db.flush()
    calls = []
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: calls.append((a, k)))
    assert dispatch_outbox.run(batch_size=1) == 1
    assert len(calls) == 1
    assert events[0].published_at is None
    assert events[1].published_at is not None
    assert events[2].published_at is None


@pytest.mark.parametrize("status", list(ActionStatus))
@pytest.mark.parametrize("age_seconds", [59, 60])
def test_recovery_sweep_only_dispatches_due_nonterminal_actions(
    worker_db, case, monkeypatch, status, age_seconds
):
    from opspilot.core import clock
    from opspilot.workers.app import celery_app
    from opspilot.workers.tasks import reconcile_pending

    now = utcnow()
    monkeypatch.setattr(clock, "utcnow", lambda: now)
    case.action.status = status
    case.action.updated_at = now - timedelta(seconds=age_seconds)
    worker_db.flush()
    calls = []
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: calls.append((a, k)))
    reconcile_pending.run()
    matching = [(a, k) for a, k in calls if k["args"] == [str(case.action.id)]]
    eligible = age_seconds == 60 and status in {
        ActionStatus.approved,
        ActionStatus.executing,
        ActionStatus.reconciliation_required,
    }
    assert len(matching) == int(eligible)
    if eligible:
        assert matching == [
            (
                ("action.reconcile",),
                {"args": [str(case.action.id)], "queue": "execute", "retry": False},
            )
        ]


def test_recovery_broker_failure_keeps_action_available_for_next_sweep(
    worker_db, case, monkeypatch
):
    from opspilot.workers.app import celery_app
    from opspilot.workers.tasks import reconcile_pending

    decide(worker_db, case)
    case.action.updated_at = utcnow() - timedelta(days=3650)
    worker_db.flush()

    def unavailable(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(celery_app, "send_task", unavailable)
    with pytest.raises(ConnectionError):
        reconcile_pending.run()
    assert case.action.status == ActionStatus.approved
    calls = []
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: calls.append(k))
    reconcile_pending.run()
    assert any(call["args"] == [str(case.action.id)] for call in calls)
