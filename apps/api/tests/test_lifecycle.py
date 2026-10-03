import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from opspilot.auth.dependencies import get_principal
from opspilot.core.clock import utcnow
from opspilot.core.config import Settings
from opspilot.core.errors import IllegalTransition, NotAuthorized, ResourceNotFound
from opspilot.db.session import db_session
from opspilot.domain.enums import (
    ActionStatus,
    ActorType,
    IncompleteReason,
    Role,
    RunStatus,
    RunStepType,
    Usefulness,
)
from opspilot.main import create_app
from opspilot.models import AuditEvent, EvidenceGrant, Feedback, RunStep
from opspilot.services import retention, runs

CONFIG = Settings(_env_file=None)


def test_retention_removes_content_preserves_accounting_and_action(db, case):
    case.action.status = ActionStatus.rejected
    case.run.status = RunStatus.completed
    case.run.trace_expires_at = utcnow() - timedelta(seconds=1)
    case.run.tokens_in = 75
    case.run.result_json = {"summary": "sensitive finding"}
    step = case.repo.add(
        RunStep(
            run_id=case.run.id,
            ordinal=0,
            step_type=RunStepType.tool_call,
            arguments_json={"query": "sensitive request"},
        )
    )
    grant = case.repo.add(
        EvidenceGrant(run_id=case.run.id, evidence_id="ord:test", granted_at=utcnow())
    )
    feedback = case.repo.add(
        Feedback(
            run_id=case.run.id,
            membership_id=case.member.id,
            usefulness=Usefulness.useful,
            correction_text="sensitive correction",
        )
    )
    db.flush()
    assert retention.prune_run(db, CONFIG, case.org.id, case.run.id)
    db.flush()
    assert case.run.result_json is None
    assert case.run.request_text == "[removed by retention]"
    assert case.run.tokens_in == 75
    assert case.run.trace_pruned_at is not None
    assert case.repo.get(RunStep, step.id) is None
    assert case.repo.get(EvidenceGrant, grant.id) is None
    db.refresh(feedback)
    assert feedback.correction_text is None and feedback.usefulness == Usefulness.useful
    assert case.action.payload_json["description"] == "Synthetic approval test"
    assert not retention.prune_run(db, CONFIG, case.org.id, case.run.id)
    assert (case.org.id, case.run.id) not in retention.expired_trace_ids(db, CONFIG)


@pytest.mark.parametrize(
    "action_status",
    [
        ActionStatus.pending,
        ActionStatus.approved,
        ActionStatus.executing,
        ActionStatus.reconciliation_required,
    ],
)
def test_retention_preserves_unresolved_approval_evidence(db, case, action_status):
    case.action.status = action_status
    case.run.trace_expires_at = utcnow() - timedelta(days=1)
    db.flush()
    assert not retention.prune_run(db, CONFIG, case.org.id, case.run.id)
    assert (case.org.id, case.run.id) not in retention.expired_trace_ids(db, CONFIG)


@pytest.mark.parametrize("status", [RunStatus.queued, RunStatus.investigating])
def test_retention_does_not_touch_active_run(db, case, status):
    case.action.status = ActionStatus.rejected
    case.run.status = status
    case.run.trace_expires_at = utcnow() - timedelta(days=1)
    db.flush()
    assert not retention.prune_run(db, CONFIG, case.org.id, case.run.id)


def test_retention_due_boundary_legacy_and_tenant_scope(db, case, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(retention, "utcnow", lambda: now)
    case.action.status = ActionStatus.rejected
    case.run.trace_expires_at = now + timedelta(microseconds=1)
    db.flush()
    assert not retention.prune_run(db, CONFIG, case.org.id, case.run.id)
    case.run.trace_expires_at = None
    case.run.created_at = now - timedelta(days=14)
    db.flush()
    assert not retention.prune_run(db, CONFIG, uuid.uuid4(), case.run.id)
    assert (case.org.id, case.run.id) in retention.expired_trace_ids(db, CONFIG)
    assert retention.prune_run(db, CONFIG, case.org.id, case.run.id)


def test_audit_retention_cutoff_and_batch(db, case, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(retention, "utcnow", lambda: now)
    rows = []
    for age in (92, 91, 90, 89):
        rows.append(
            case.repo.add(
                AuditEvent(
                    actor_type=ActorType.system,
                    event_type="test",
                    object_type="test",
                    occurred_at=now - timedelta(days=age),
                )
            )
        )
    db.flush()
    assert retention.prune_audit_batch(db, CONFIG, batch_size=1) == 1
    assert case.repo.get(AuditEvent, rows[0].id) is None
    assert case.repo.get(AuditEvent, rows[1].id) is not None
    assert retention.prune_audit_batch(db, CONFIG) == 1
    assert retention.prune_audit_batch(db, CONFIG) == 0
    assert case.repo.get(AuditEvent, rows[2].id) is not None


@pytest.mark.parametrize("status", [RunStatus.queued, RunStatus.investigating])
def test_cancel_is_idempotent_and_does_not_claim_completion_early(db, case, status):
    case.run.status = status
    db.flush()
    first = runs.cancel_run(db, case.principal, case.run.id)
    timestamp = first.cancel_requested_at
    again = runs.cancel_run(db, case.principal, case.run.id)
    assert timestamp == again.cancel_requested_at
    assert again.status == (RunStatus.cancelled if status == RunStatus.queued else status)
    assert len(case.repo.list_by(AuditEvent, object_id=case.run.id)) == 1
    if status == RunStatus.queued:
        assert again.incomplete_reason == IncompleteReason.cancelled
        assert runs.claim_run(db, case.run.id) is None


def test_cancel_enforces_owner_supervisor_and_tenant(db, case):
    case.run.status = RunStatus.queued
    db.flush()
    other_analyst = replace(case.principal, membership_id=uuid.uuid4(), role=Role.analyst)
    with pytest.raises(NotAuthorized):
        runs.cancel_run(db, other_analyst, case.run.id)
    with pytest.raises(ResourceNotFound):
        runs.cancel_run(db, replace(case.principal, organization_id=uuid.uuid4()), case.run.id)
    assert runs.cancel_run(db, case.principal, case.run.id).status == RunStatus.cancelled


@pytest.mark.parametrize("status", [RunStatus.completed, RunStatus.failed, RunStatus.needs_review])
def test_cancellation_cannot_undo_finished_investigation(db, case, status):
    case.run.status = status
    db.flush()
    with pytest.raises(IllegalTransition):
        runs.cancel_run(db, case.principal, case.run.id)
    assert case.run.cancel_requested_at is None


def test_run_api_bounds_cancel_and_cross_tenant_feedback(db, case):
    case.run.status = RunStatus.queued
    db.flush()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: db
    app.dependency_overrides[get_principal] = lambda: case.principal
    with TestClient(app) as client:
        assert client.get("/runs?limit=-1").status_code == 422
        assert client.get("/runs?offset=-1").status_code == 422
        assert client.get(f"/runs/{case.run.id}").json()["can_cancel"]
        assert client.post(f"/runs/{case.run.id}/cancel").json()["status"] == "cancelled"
        app.dependency_overrides[get_principal] = lambda: replace(
            case.principal, organization_id=uuid.uuid4()
        )
        for suffix in ("", "/cancel", "/feedback"):
            response = (
                client.get(f"/runs/{case.run.id}")
                if not suffix
                else client.post(f"/runs/{case.run.id}{suffix}", json={"usefulness": "useful"})
            )
            assert response.status_code == 404


def test_expired_trace_candidates_are_bounded(db, case):
    case.action.status = ActionStatus.rejected
    case.run.trace_expires_at = utcnow() - timedelta(days=1)
    db.flush()
    assert len(retention.expired_trace_ids(db, CONFIG, batch_size=1)) <= 1


def test_feedback_cannot_reintroduce_pruned_content(db, case):
    case.run.trace_pruned_at = utcnow()
    db.flush()
    app = create_app()
    app.dependency_overrides[db_session] = lambda: db
    app.dependency_overrides[get_principal] = lambda: case.principal
    with TestClient(app) as client:
        response = client.post(
            f"/runs/{case.run.id}/feedback",
            json={"usefulness": "useful", "correction_text": "new sensitive text"},
        )
    assert response.status_code == 409
    assert not case.repo.list_by(Feedback, run_id=case.run.id)
