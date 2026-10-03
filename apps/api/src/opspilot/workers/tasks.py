"""Task definitions.

Every task in this module must be idempotent. ``acks_late`` means any of them can be
delivered twice; the second delivery must be a no-op, not a second effect.

The pattern that makes that true, used by all of them: claim the work with a
conditional UPDATE, and stop if zero rows changed.

STATUS: signatures, queues and the safety comments are defined. Bodies are Weeks 2–5.
"""

from __future__ import annotations

from uuid import UUID

from celery import shared_task

from opspilot.core.logging import get_logger

log = get_logger(__name__)


@shared_task(name="run.investigate", bind=True, max_retries=2, acks_late=True)
def investigate(self, run_id: str) -> None:  # type: ignore[no-untyped-def]
    """Execute one investigation.

    Order of operations, and none of it is negotiable:

    1. ``UPDATE agent_runs SET status='investigating' WHERE id=:id AND status='queued'``
       — zero rows means another worker already has it, or it was cancelled. Return.
    2. Re-resolve the membership and organization from the database. The message is a
       claim, not a credential (ADR 0002).
    3. Build the budget from Settings, run the orchestrator.
    4. Persist steps, evidence grants, the validated result and the accounting.
    5. Persist any proposed action as ``pending`` and move the run to ``needs_review``.

    **This task never waits for approval.** It ends at ``needs_review`` and the
    approval arrives later as a separate HTTP request.
    """
    from opspilot.core.config import get_settings
    from opspilot.services.runs import process_run

    settings = get_settings()
    outcome = process_run(settings, UUID(run_id))

    if outcome is None:
        # Already claimed, cancelled, or the membership was revoked. A duplicate
        # delivery lands here and does nothing, which is the point of acks_late.
        log.info("run.investigate.noop", run_id=run_id)
        return

    log.info(
        "run.investigate.finished",
        run_id=run_id,
        turns=outcome.turns,
        tool_calls=outcome.tool_calls,
        incomplete=outcome.incomplete_reason.value if outcome.incomplete_reason else None,
        unsupported_citations=len(outcome.validation.unsupported),
    )


@shared_task(name="run.requeue_stranded", acks_late=True)
def requeue_stranded_runs(older_than_seconds: int = 60) -> int:
    """Re-publish runs that are still ``queued`` long after they were created.

    The safety net behind ``create_run``: if the broker was unreachable at the moment
    the run was committed, the row exists and no message does. Re-publishing is safe
    because ``claim_run`` turns a duplicate delivery into a no-op.
    """
    from opspilot.db.session import session_scope
    from opspilot.services.runs import stranded_run_ids
    from opspilot.workers.app import celery_app

    with session_scope() as session:
        run_ids = stranded_run_ids(session, older_than_seconds=older_than_seconds)

    for run_id in run_ids:
        celery_app.send_task("run.investigate", args=[str(run_id)], queue="investigate")
        log.warning("run.requeued_stranded", run_id=str(run_id))
    return len(run_ids)


@shared_task(name="outbox.dispatch", acks_late=True)
def dispatch_outbox(batch_size: int = 50) -> int:
    """Publish committed-but-unpublished outbox events.

    Select a bounded batch, then lock and re-check each event with SKIP LOCKED.
    Publish and mark published while holding that event's lock. A crash between
    publish and mark re-publishes; the consumer must remain idempotent.
    """
    from sqlalchemy import or_, select

    from opspilot.core.clock import utcnow
    from opspilot.db.session import session_scope
    from opspilot.models import OutboxEvent
    from opspilot.services.execution import publish_event
    from opspilot.workers.app import celery_app

    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")

    with session_scope() as session:
        rows = session.execute(
            select(OutboxEvent.organization_id, OutboxEvent.id)
            .where(
                OutboxEvent.published_at.is_(None),
                or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= utcnow()),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
        ).all()
    count = 0
    for org_id, event_id in rows:
        with session_scope() as session:
            count += int(publish_event(session, org_id, event_id, celery_app.send_task))
    return count


@shared_task(name="action.execute", bind=True, max_retries=5, acks_late=True)
def execute_action(self, proposed_action_id: str) -> None:  # type: ignore[no-untyped-def]
    """Perform the approved write.

    1. Re-validate everything with ``domain.approval.evaluate_approval`` against
       freshly read state. Membership revoked, role changed, payload hash different,
       source version moved, expired at decision time — any of these stops the write.
    2. Claim: ``UPDATE ... SET status='executing' WHERE status='approved'``. Zero rows
       means a duplicate delivery. Return.
    3. Derive the idempotency key from stored state (``domain.hashing``) — never from
       the message, never from a fresh uuid.
    4. Call the operations API with that key.
    5. Success: record the downstream task id, status ``succeeded``.
       Terminal rejection: ``failed``, no retry.
       Timeout / transport failure / ambiguous response: ``reconciliation_required``
       and stop. **Never retry a write whose outcome is unknown.**
    """
    from sqlalchemy import select

    from opspilot.core.config import get_settings
    from opspilot.db.session import session_scope
    from opspilot.models import ProposedAction
    from opspilot.services.execution import process_action

    action_id = UUID(proposed_action_id)
    with session_scope() as session:
        org_id = session.scalar(
            select(ProposedAction.organization_id).where(ProposedAction.id == action_id)
        )
    if org_id is not None:
        process_action(get_settings(), org_id, action_id)


@shared_task(name="action.reconcile", acks_late=True)
def reconcile_action(proposed_action_id: str) -> None:
    """Resolve one uncertain outcome by asking the downstream about the key.

    Found: mark succeeded with the returned task id.
    Definitively absent: return to ``executing`` and retry with the same key.
    Lookup itself failed: leave it in ``reconciliation_required`` and surface it.
    There is no fourth branch, and guessing is not one of the three.
    """
    execute_action.run(proposed_action_id)


@shared_task(name="action.reconcile_pending", acks_late=True)
def reconcile_pending() -> int:
    """Periodic sweep over everything in ``reconciliation_required``."""
    from datetime import timedelta

    from sqlalchemy import select

    from opspilot.core.clock import utcnow
    from opspilot.db.session import session_scope
    from opspilot.domain.enums import ActionStatus
    from opspilot.models import ProposedAction
    from opspilot.workers.app import celery_app

    # Includes executing claims abandoned by a killed process and approved actions
    # whose message disappeared. Each consumer re-reads and serializes on the action.
    with session_scope() as session:
        ids = list(
            session.scalars(
                select(ProposedAction.id)
                .where(
                    ProposedAction.status.in_(
                        [
                            ActionStatus.approved,
                            ActionStatus.executing,
                            ActionStatus.reconciliation_required,
                        ]
                    ),
                    ProposedAction.updated_at <= utcnow() - timedelta(seconds=60),
                )
                .order_by(ProposedAction.updated_at)
                .limit(100)
            ).all()
        )
    for action_id in ids:
        celery_app.send_task(
            "action.reconcile", args=[str(action_id)], queue="execute", retry=False
        )
    return len(ids)


@shared_task(name="approval.expire_stale", acks_late=True)
def expire_stale_proposals() -> int:
    """Move ``pending`` proposals past ``expires_at`` to ``expired``.

    Expiry is also checked at decision time; this job keeps the inbox honest rather
    than being the control itself.
    """
    from sqlalchemy import select

    from opspilot.core.clock import utcnow
    from opspilot.db.session import session_scope
    from opspilot.domain.enums import ActionStatus
    from opspilot.models import ProposedAction
    from opspilot.services.actions import expire_one

    with session_scope() as session:
        rows = session.execute(
            select(ProposedAction.organization_id, ProposedAction.id)
            .where(
                ProposedAction.status == ActionStatus.pending,
                ProposedAction.expires_at <= utcnow(),
            )
            .order_by(ProposedAction.expires_at)
            .limit(100)
        ).all()
    count = 0
    for org_id, action_id in rows:
        with session_scope() as session:
            count += int(expire_one(session, org_id, action_id))
    return count


@shared_task(name="retention.prune_traces", acks_late=True)
def prune_traces() -> int:
    """Delete run steps and result payloads past ``trace_expires_at`` (14 days)."""
    from opspilot.core.config import get_settings
    from opspilot.db.session import session_scope
    from opspilot.services.retention import expired_trace_ids, prune_run

    settings = get_settings()
    with session_scope() as session:
        rows = expired_trace_ids(session, settings)
    count = 0
    for org_id, run_id in rows:
        with session_scope() as session:
            count += int(prune_run(session, settings, org_id, run_id))
    return count


@shared_task(name="retention.prune_audit", acks_late=True)
def prune_audit() -> int:
    """Delete audit events older than 90 days."""
    from opspilot.core.config import get_settings
    from opspilot.db.session import session_scope
    from opspilot.services.retention import prune_audit_batch

    with session_scope() as session:
        return prune_audit_batch(session, get_settings())


@shared_task(name="run.fail_abandoned", acks_late=True)
def fail_abandoned_runs() -> int:
    """Close durable claims left by a dead worker; never retry uncertain model spend."""
    from datetime import timedelta

    from sqlalchemy import select

    from opspilot.core.clock import utcnow
    from opspilot.core.config import get_settings
    from opspilot.db.repository import TenantScopedRepository
    from opspilot.db.session import session_scope
    from opspilot.domain.enums import IncompleteReason, RunStatus
    from opspilot.domain.state import transition_run
    from opspilot.models import AgentRun

    cutoff = utcnow() - timedelta(seconds=get_settings().run_deadline_seconds + 120)
    with session_scope() as session:
        rows = session.execute(
            select(AgentRun.organization_id, AgentRun.id)
            .where(
                AgentRun.status == RunStatus.investigating,
                AgentRun.started_at < cutoff,
            )
            .limit(100)
        ).all()
    count = 0
    for org_id, run_id in rows:
        with session_scope() as session:
            repo = TenantScopedRepository(session, org_id)
            run = session.scalar(
                repo.query(AgentRun).where(AgentRun.id == run_id).with_for_update(skip_locked=True)
            )
            if (
                run is None
                or run.status != RunStatus.investigating
                or not run.started_at
                or run.started_at >= cutoff
            ):
                continue
            cancelled = run.cancel_requested_at is not None
            run.status = transition_run(
                run.status, RunStatus.cancelled if cancelled else RunStatus.failed
            )
            run.incomplete_reason = (
                IncompleteReason.cancelled if cancelled else IncompleteReason.deadline
            )
            run.error_code = "worker.lost"
            run.finished_at = utcnow()
            count += 1
    return count


@shared_task(name="document.index", acks_late=True)
def index_document_version(document_version_id: str) -> None:
    """Chunk, embed and store one document version.

    Keyed on ``content_hash``, so a re-delivery re-indexes to the identical result
    rather than duplicating chunks.
    """
    import uuid

    from sqlalchemy import select

    from opspilot.core.config import get_settings
    from opspilot.core.errors import ResourceNotFound
    from opspilot.core.logging import get_logger
    from opspilot.db.repository import TenantScopedRepository
    from opspilot.db.session import session_scope
    from opspilot.domain.enums import DocumentStatus
    from opspilot.models.knowledge import Document, DocumentVersion
    from opspilot.services.documents import index_version

    version_id = uuid.UUID(document_version_id)
    # The internal message only identifies a row. Resolve its organization in the DB,
    # then scope every lifecycle read and write to that resolved organization.
    with session_scope() as session:
        organization_id = session.scalar(
            select(DocumentVersion.organization_id).where(DocumentVersion.id == version_id)
        )
    if organization_id is None:
        return
    try:
        with session_scope() as session:
            index_version(session, organization_id, version_id, get_settings())
    except ResourceNotFound:
        return  # deletion won the race
    except Exception as exc:
        get_logger(__name__).warning(
            "document.index_failed", version_id=str(version_id), error=type(exc).__name__
        )
        with session_scope() as session:
            repo = TenantScopedRepository(session, organization_id)
            version = repo.get_or_404(DocumentVersion, version_id)
            document = session.scalar(
                repo.query(Document).where(Document.id == version.document_id).with_for_update()
            )
            session.refresh(version)
            if document and document.deleted_at is None and version.indexed_at is None:
                document.status = DocumentStatus.failed
