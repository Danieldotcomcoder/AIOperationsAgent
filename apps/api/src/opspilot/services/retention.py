"""Bounded, idempotent retention. Keep accounting and immutable operational records."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.orm import Session

from opspilot.core.clock import utcnow
from opspilot.core.config import Settings
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import ActionStatus, RunStatus
from opspilot.models import AgentRun, AuditEvent, EvidenceGrant, Feedback, ProposedAction, RunStep


def prune_run(session: Session, settings: Settings, org_id: UUID, run_id: UUID) -> bool:
    repo = TenantScopedRepository(session, org_id)
    run = session.scalar(
        repo.query(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    now = utcnow()
    if (
        run is None
        or run.trace_pruned_at
        or run.status in {RunStatus.queued, RunStatus.investigating}
    ):
        return False
    expires = run.trace_expires_at or run.created_at + timedelta(days=settings.trace_retention_days)
    if expires > now:
        return False
    active = session.scalar(
        repo.query(ProposedAction)
        .with_only_columns(ProposedAction.id)
        .where(
            ProposedAction.run_id == run.id,
            ProposedAction.status.in_(
                [
                    ActionStatus.pending,
                    ActionStatus.approved,
                    ActionStatus.executing,
                    ActionStatus.reconciliation_required,
                ]
            ),
        )
        .limit(1)
    )
    if active:
        return False  # do not remove evidence while a human or worker still needs it
    for model in (RunStep, EvidenceGrant):
        session.execute(
            delete(model).where(model.organization_id == org_id, model.run_id == run.id)
        )
    session.execute(
        update(Feedback)
        .where(Feedback.organization_id == org_id, Feedback.run_id == run.id)
        .values(correction_text=None)
    )
    run.request_text = "[removed by retention]"
    run.result_json = None
    run.budget_reservation = None
    run.trace_pruned_at = now
    return True


def expired_trace_ids(
    session: Session, settings: Settings, batch_size: int = 100
) -> list[tuple[UUID, UUID]]:
    """Internal maintenance discovery; content removal re-scopes and locks each row."""
    now = utcnow()
    active_proposal = exists(
        select(ProposedAction.id).where(
            ProposedAction.organization_id == AgentRun.organization_id,
            ProposedAction.run_id == AgentRun.id,
            ProposedAction.status.in_(
                [
                    ActionStatus.pending,
                    ActionStatus.approved,
                    ActionStatus.executing,
                    ActionStatus.reconciliation_required,
                ]
            ),
        )
    )
    rows = session.execute(
        select(AgentRun.organization_id, AgentRun.id)
        .where(
            AgentRun.trace_pruned_at.is_(None),
            AgentRun.status.not_in([RunStatus.queued, RunStatus.investigating]),
            or_(
                AgentRun.trace_expires_at <= now,
                (AgentRun.trace_expires_at.is_(None))
                & (AgentRun.created_at <= now - timedelta(days=settings.trace_retention_days)),
            ),
            ~active_proposal,
        )
        .order_by(AgentRun.created_at, AgentRun.id)
        .limit(batch_size)
    )
    return [(org_id, run_id) for org_id, run_id in rows]


def prune_audit_batch(session: Session, settings: Settings, batch_size: int = 500) -> int:
    """Internal cross-tenant age sweep, bounded and locked against other sweepers."""
    rows = session.execute(
        select(AuditEvent.organization_id, AuditEvent.id)
        .where(
            AuditEvent.occurred_at < utcnow() - timedelta(days=settings.audit_retention_days),
        )
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    ids = list(rows)
    for org_id, event_id in ids:
        session.execute(
            delete(AuditEvent).where(
                AuditEvent.organization_id == org_id, AuditEvent.id == event_id
            )
        )
    return len(ids)
