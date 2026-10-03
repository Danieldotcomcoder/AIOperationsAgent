"""Investigation run lifecycle.

Two halves that must not be confused:

* ``create_run`` runs inside an HTTP request. It reserves budget, persists the run as
  ``queued``, and schedules the Celery message to be published **after the transaction
  commits**. Publishing inside the transaction would leave a message pointing at a row
  that a rollback then removed.
* ``execute_run`` runs inside a worker. It claims the run with a conditional UPDATE,
  re-derives authorization from the database, drives the orchestrator, and persists
  everything. It trusts nothing in the message except the run id.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from opspilot.agent.orchestrator import (
    RunContext,
    RunOutcome,
    ToolDispatcher,
    run_investigation,
)
from opspilot.agent.pricing import configured_cost
from opspilot.agent.prompts import PROMPT_VERSION, build_system_prompt
from opspilot.agent.providers import build_provider
from opspilot.auth.principal import Principal
from opspilot.core.clock import business_date, utcnow
from opspilot.core.config import Settings, get_settings
from opspilot.core.errors import (
    ConflictError,
    IllegalTransition,
    NotAuthorized,
    ResourceNotFound,
    WritesDisabled,
)
from opspilot.core.logging import get_logger
from opspilot.db.repository import TenantScopedRepository
from opspilot.db.session import session_scope
from opspilot.db.worker_context import run_organization
from opspilot.domain.budget import RunBudget
from opspilot.domain.enums import (
    ActionStatus,
    ActionType,
    ActorType,
    IncompleteReason,
    MembershipStatus,
    Role,
    RunStatus,
    Usefulness,
)
from opspilot.domain.hashing import canonical_json, payload_hash
from opspilot.domain.state import transition_run
from opspilot.models import (
    AgentRun,
    AuditEvent,
    EvidenceGrant,
    Feedback,
    Membership,
    Organization,
    ProposedAction,
    RunStep,
)
from opspilot.services import tools as tool_service
from opspilot.services.budget import BudgetService

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CreatedRun:
    id: uuid.UUID
    status: RunStatus


def create_run(
    session: Session,
    settings: Settings,
    principal: Principal,
    *,
    request_text: str,
    order_number: str | None = None,
    budget_service: BudgetService | None = None,
) -> CreatedRun:
    """Reserve budget, persist as queued, enqueue after commit."""
    if not settings.feature_agent_enabled:
        raise WritesDisabled("the agent is disabled by feature flag")

    budget = budget_service or BudgetService(settings)
    repo = TenantScopedRepository(session, principal.organization_id)
    target_order_id = None
    if order_number:
        target_order_id = tool_service.order_id_for_number(
            session, repo, order_number, as_of=business_date(settings)
        )

    receipt = budget.reserve(principal.organization_id, principal.membership_id)

    @event.listens_for(session, "after_rollback", once=True)
    def release_failed_admission(_: Session) -> None:
        budget.release(principal.organization_id, principal.membership_id, receipt=receipt)

    run = AgentRun(
        organization_id=principal.organization_id,
        initiated_by=principal.membership_id,
        request_text=request_text,
        target_order_id=target_order_id,
        status=RunStatus.queued,
        prompt_version=PROMPT_VERSION,
        provider=settings.llm_provider,
        model_name=settings.llm_model,
        trace_expires_at=utcnow() + timedelta(days=settings.trace_retention_days),
        budget_reservation=receipt,
    )
    session.add(run)
    session.flush()

    _enqueue_after_commit(session, run.id)
    log.info("run.created", run_id=str(run.id), org_id=str(principal.organization_id))
    return CreatedRun(id=run.id, status=run.status)


def _enqueue_after_commit(session: Session, run_id: uuid.UUID) -> None:
    """Publish the Celery message only once the row is durably committed.

    A one-shot listener, removed when it fires. Enqueueing inline would publish work
    for a transaction that a later error rolls back, and the worker would then find no
    run — the classic dual-write bug this project takes seriously elsewhere too.

    The publish itself is by task *name* through the configured app, not by importing
    the task function. ``shared_task`` binds to Celery's ``current_app``, which in a web
    process that never imported ``workers.app`` is the default app pointing at
    ``amqp://localhost`` — so the message goes looking for a RabbitMQ that does not
    exist. Sending by name also keeps the web process free of worker imports.
    """

    @event.listens_for(session, "after_commit", once=True)
    def _publish(_: Session) -> None:
        from kombu.exceptions import OperationalError

        from opspilot.workers.app import celery_app

        try:
            celery_app.send_task("run.investigate", args=[str(run_id)], queue="investigate")
        except OperationalError as exc:
            # The row is already committed, so failing the request here would report an
            # error for work that does exist. Leave it queued and let the stranded-run
            # sweep publish it — the same at-least-once reasoning as the outbox
            # (ADR 0003). What must never happen is a run silently lost because the
            # broker blinked.
            log.error("run.enqueue_failed", run_id=str(run_id), error=type(exc).__name__)
            return
        log.info("run.enqueued", run_id=str(run_id))


def list_runs(
    session: Session,
    principal: Principal,
    *,
    limit: int = 25,
    offset: int = 0,
    status: RunStatus | None = None,
) -> list[AgentRun]:
    repo = TenantScopedRepository(session, principal.organization_id)
    stmt = repo.query(AgentRun)
    if status is not None:
        stmt = stmt.where(AgentRun.status == status)
    stmt = stmt.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())


@dataclass(frozen=True, slots=True)
class RunListing:
    """What a list of runs needs from other tables, fetched in three queries, not 3N."""

    #: The latest proposal per run, whatever its status.
    proposals: dict[uuid.UUID, ProposedAction]
    order_numbers: dict[uuid.UUID, str]
    names: dict[uuid.UUID, str]
    #: Usefulness ratings per run, oldest first.
    feedback: dict[uuid.UUID, list[Usefulness]]


def listing_context(session: Session, principal: Principal, runs: list[AgentRun]) -> RunListing:
    from opspilot.models import Order

    repo = TenantScopedRepository(session, principal.organization_id)
    proposals: dict[uuid.UUID, ProposedAction] = {}
    order_numbers: dict[uuid.UUID, str] = {}
    names: dict[uuid.UUID, str] = {}
    feedback: dict[uuid.UUID, list[Usefulness]] = {}
    if not runs:
        return RunListing(proposals, order_numbers, names, feedback)
    for proposal in session.scalars(
        repo.query(ProposedAction)
        .where(ProposedAction.run_id.in_([run.id for run in runs]))
        .order_by(ProposedAction.created_at.desc())
    ):
        proposals.setdefault(proposal.run_id, proposal)
    order_ids = {run.target_order_id for run in runs if run.target_order_id}
    if order_ids:
        order_numbers = dict(
            session.execute(
                repo.query(Order)
                .with_only_columns(Order.id, Order.order_number)
                .where(Order.id.in_(order_ids))
            )
            .tuples()
            .all()
        )
    names = dict(
        session.execute(
            repo.query(Membership)
            .with_only_columns(Membership.id, Membership.display_name)
            .where(Membership.id.in_({run.initiated_by for run in runs}))
        )
        .tuples()
        .all()
    )
    for run_id, usefulness in session.execute(
        repo.query(Feedback)
        .with_only_columns(Feedback.run_id, Feedback.usefulness)
        .where(Feedback.run_id.in_([run.id for run in runs]))
        .order_by(Feedback.created_at)
    ):
        feedback.setdefault(run_id, []).append(usefulness)
    return RunListing(proposals, order_numbers, names, feedback)


def get_run(session: Session, principal: Principal, run_id: uuid.UUID) -> AgentRun:
    repo = TenantScopedRepository(session, principal.organization_id)
    return repo.get_or_404(AgentRun, run_id)


def record_feedback(
    session: Session,
    principal: Principal,
    run_id: uuid.UUID,
    *,
    usefulness: Usefulness,
    correction_text: str | None,
    failure_class: str | None,
) -> Feedback:
    """Serialized with retention, so feedback cannot reintroduce pruned free text."""
    repo = TenantScopedRepository(session, principal.organization_id)
    run = session.scalar(
        repo.query(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ResourceNotFound("run not found")
    if run.trace_pruned_at:
        raise ConflictError("Feedback is closed after trace retention")
    if run.status in {RunStatus.queued, RunStatus.investigating}:
        raise ConflictError("Feedback opens once the investigation has finished")
    feedback = repo.add(
        Feedback(
            run_id=run.id,
            membership_id=principal.membership_id,
            usefulness=usefulness,
            correction_text=(correction_text or "").strip() or None,
            failure_class=(failure_class or "").strip() or None,
        )
    )
    session.flush()
    return feedback


def run_feedback(
    session: Session, principal: Principal, run_id: uuid.UUID
) -> list[tuple[Feedback, str]]:
    """Feedback on one run with each author's display name, oldest first."""
    repo = TenantScopedRepository(session, principal.organization_id)
    rows = session.scalars(
        repo.query(Feedback).where(Feedback.run_id == run_id).order_by(Feedback.created_at)
    ).all()
    names = (
        dict(
            session.execute(
                repo.query(Membership)
                .with_only_columns(Membership.id, Membership.display_name)
                .where(Membership.id.in_({row.membership_id for row in rows}))
            )
            .tuples()
            .all()
        )
        if rows
        else {}
    )
    return [(row, names.get(row.membership_id, "Former member")) for row in rows]


def get_run_steps(session: Session, principal: Principal, run_id: uuid.UUID) -> list[RunStep]:
    repo = TenantScopedRepository(session, principal.organization_id)
    stmt = repo.query(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.ordinal)
    return list(session.scalars(stmt).all())


def cancel_run(session: Session, principal: Principal, run_id: uuid.UUID) -> AgentRun:
    """Serialize with claim/finalization; an active call finishes and is accounted."""
    repo = TenantScopedRepository(session, principal.organization_id)
    run = session.scalar(
        repo.query(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ResourceNotFound("run not found")
    member = session.scalar(
        repo.query(Membership)
        .where(Membership.id == principal.membership_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if (
        member is None
        or member.status != MembershipStatus.active
        or member.subject != principal.subject
    ):
        raise NotAuthorized("no active membership")
    if member.id != run.initiated_by and member.role != Role.supervisor:
        raise NotAuthorized("only the initiator or a supervisor can cancel this run")
    if run.status == RunStatus.cancelled or run.cancel_requested_at:
        return run
    if run.status not in {RunStatus.queued, RunStatus.investigating}:
        raise IllegalTransition("this investigation has already finished")
    run.cancel_requested_at = utcnow()
    if run.status == RunStatus.queued:
        run.status = transition_run(run.status, RunStatus.cancelled)
        run.incomplete_reason = IncompleteReason.cancelled
        run.finished_at = utcnow()
        receipt = run.budget_reservation
        org_id, member_id = run.organization_id, run.initiated_by

        @event.listens_for(session, "after_commit", once=True)
        def release_unused(_: Session) -> None:
            BudgetService(get_settings()).release(org_id, member_id, receipt=receipt)

    repo.add(
        AuditEvent(
            actor_type=ActorType.user,
            actor_id=principal.membership_id,
            event_type="run.cancel_requested",
            object_type="agent_run",
            object_id=run.id,
            occurred_at=utcnow(),
        )
    )
    session.flush()
    return run


def stranded_run_ids(session: Session, *, older_than_seconds: int = 60) -> list[uuid.UUID]:
    """Runs still ``queued`` well past the point a worker should have claimed them.

    Two causes, both real: the broker was unreachable when the run was created, or a
    message was lost. Either way the row is the source of truth and the message is a
    hint, so re-publishing is safe — ``claim_run`` makes a duplicate delivery a no-op.
    """
    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    stmt = (
        select(AgentRun.id)
        .where(AgentRun.status == RunStatus.queued, AgentRun.created_at < cutoff)
        .order_by(AgentRun.created_at)
        .limit(100)
    )
    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------- worker side


def claim_run(session: Session, run_id: uuid.UUID) -> AgentRun | None:
    """Atomically move ``queued -> investigating``.

    Zero rows affected means another worker already has it, or it was cancelled. That
    is the whole duplicate-delivery defence: Celery is at-least-once, so this task will
    sometimes arrive twice, and the second arrival must be a no-op rather than a second
    charge to the budget.
    """
    org_id = run_organization(session, run_id)
    if org_id is None:
        return None
    claimed = session.execute(
        update(AgentRun)
        .where(
            AgentRun.organization_id == org_id,
            AgentRun.id == run_id,
            AgentRun.status == RunStatus.queued,
        )
        .values(status=RunStatus.investigating, started_at=utcnow())
        .returning(AgentRun.id)
    ).scalar_one_or_none()
    if claimed is None:
        return None
    return TenantScopedRepository(session, org_id).get(AgentRun, run_id)


def process_run(settings: Settings, run_id: uuid.UUID) -> RunOutcome | None:
    """Commit the claim before model IO so cancellation never waits on the provider."""
    with session_scope() as session:
        run = claim_run(session, run_id)
        if run is None:
            return None
        org_id = run.organization_id
    with session_scope() as session:
        return execute_run(session, settings, run_id, org_id)


def execute_run(
    session: Session, settings: Settings, run_id: uuid.UUID, organization_id: uuid.UUID
) -> RunOutcome | None:
    """Drive one investigation to completion. Returns ``None`` if the run was not claimed."""
    repo = TenantScopedRepository(session, organization_id)
    run = repo.get(AgentRun, run_id)
    if run is None or run.status != RunStatus.investigating:
        log.info("run.already_claimed", run_id=str(run_id))
        return None

    # Re-derive authorization from the database. The message carried an id, not an
    # entitlement, and the membership may have been revoked since it was queued.
    membership = repo.get(Membership, run.initiated_by)
    if membership is None or membership.status is not MembershipStatus.active:
        run.status = transition_run(run.status, RunStatus.failed)
        run.error_code = "auth.forbidden"
        run.finished_at = utcnow()
        log.warning("run.membership_revoked", run_id=str(run_id))
        return None
    if membership.organization_id != run.organization_id:  # pragma: no cover - defensive
        raise ResourceNotFound("run does not belong to the initiating membership's organization")

    organization = session.get(Organization, run.organization_id)
    as_of = business_date(settings)

    ctx = RunContext(
        run_id=run.id,
        organization_id=run.organization_id,
        membership_id=membership.id,
        organization_name=organization.name if organization else "",
        request_text=run.request_text,
        as_of=as_of,
    )
    budget = RunBudget(
        max_turns=settings.run_max_turns,
        max_tool_calls=settings.run_max_tool_calls,
        deadline_seconds=settings.run_deadline_seconds,
        max_input_tokens=settings.run_max_input_tokens,
        max_output_tokens=settings.run_max_output_tokens,
    )

    def stop_requested() -> bool:
        # Scalar projections avoid the identity map's cached run/membership values.
        cancelled = session.scalar(
            repo.query(AgentRun)
            .with_only_columns(AgentRun.cancel_requested_at)
            .where(AgentRun.id == run.id)
        )
        member_status = session.scalar(
            repo.query(Membership)
            .with_only_columns(Membership.status)
            .where(Membership.id == run.initiated_by)
        )
        return bool(
            cancelled
            or member_status != MembershipStatus.active
            or not settings.feature_agent_enabled
        )

    outcome = run_investigation(
        ctx=ctx,
        provider=build_provider(settings),
        dispatcher=ToolDispatcher(ctx, session, settings),
        budget=budget,
        system_prompt=build_system_prompt(
            organization_name=ctx.organization_name, as_of=as_of.isoformat()
        ),
        max_output_tokens=settings.run_max_output_tokens,
        per_call_deadline=settings.provider_call_deadline_seconds,
        stop_requested=stop_requested,
    )

    # Cancellation and result/proposal persistence are serialized on this lock.
    session.scalar(
        repo.query(AgentRun)
        .where(AgentRun.id == run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session.scalar(
        repo.query(Membership)
        .where(Membership.id == membership.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if run.status != RunStatus.investigating:
        return outcome  # abandoned-run sweep won; never resurrect a terminal run
    if run.cancel_requested_at:
        outcome.result = None
        outcome.incomplete_reason = IncompleteReason.cancelled
    elif membership.status != MembershipStatus.active or not settings.feature_agent_enabled:
        outcome.result = None
        outcome.incomplete_reason = None
        outcome.error_code = (
            "auth.forbidden"
            if membership.status != MembershipStatus.active
            else "feature.agent_disabled"
        )
    _persist_outcome(session, settings, run, ctx, outcome)
    receipt, org_id, member_id, cost = (
        run.budget_reservation,
        run.organization_id,
        membership.id,
        run.cost_usd,
    )
    # A timed-out provider may have charged without returning usage. Keep its full
    # reservation rather than refunding unknown spend. Settlement is idempotent.
    if not any(step.error_code == "provider.unavailable" for step in outcome.steps):

        @event.listens_for(session, "after_commit", once=True)
        def reconcile_completed(_: Session) -> None:
            BudgetService(settings).reconcile(org_id, member_id, cost, receipt=receipt)

    return outcome


def _persist_outcome(
    session: Session,
    settings: Settings,
    run: AgentRun,
    ctx: RunContext,
    outcome: RunOutcome,
) -> None:
    """Write steps, grants, result, accounting and any proposal — one transaction."""
    now = utcnow()

    for step in outcome.steps:
        session.add(
            RunStep(
                organization_id=run.organization_id,
                run_id=run.id,
                ordinal=step.ordinal,
                step_type=step.step_type,
                tool_name=step.tool_name,
                arguments_json=step.arguments_json,
                result_digest=step.result_digest,
                result_row_count=step.result_row_count,
                latency_ms=step.latency_ms,
                tokens_in=step.tokens_in,
                tokens_out=step.tokens_out,
                error_code=step.error_code,
            )
        )

    for ref in outcome.granted_evidence:
        session.add(
            EvidenceGrant(
                organization_id=run.organization_id,
                run_id=run.id,
                evidence_id=ref,
                granted_at=now,
            )
        )

    run.turn_count = outcome.turns
    run.tool_call_count = outcome.tool_calls
    run.tokens_in = outcome.input_tokens
    run.tokens_out = outcome.output_tokens
    run.retry_count = max(
        0, outcome.turns - len([s for s in outcome.steps if s.step_type.value == "model_call"])
    )
    run.cost_usd = configured_cost(
        settings, outcome.model_name, outcome.input_tokens, outcome.output_tokens
    )
    # Attribute the answer to what actually produced it. Configuration can change
    # between admission and execution (a queued run outliving a restart), and
    # evaluation results are keyed on these three values.
    run.provider = settings.llm_provider
    run.prompt_version = PROMPT_VERSION
    run.model_name = outcome.model_name or run.model_name
    run.duration_ms = outcome.duration_ms
    run.finished_at = now
    run.incomplete_reason = outcome.incomplete_reason
    run.error_code = outcome.error_code

    if outcome.result is None:
        # No usable answer. A bound was hit or the output failed validation; either way
        # the run is finished and says why.
        run.status = transition_run(
            run.status,
            RunStatus.cancelled
            if outcome.incomplete_reason == IncompleteReason.cancelled
            else RunStatus.failed,
        )
        run.result_json = None
        session.flush()
        return

    run.result_json = {
        **outcome.result.model_dump(mode="json"),
        # Pre-strip accounting lives beside the answer, so the citation gate can be
        # computed later without replaying the run (ADR 0010).
        "validation": outcome.validation.as_dict(),
    }

    proposal = _persist_proposal(session, settings, run, ctx, outcome)
    run.status = transition_run(
        run.status, RunStatus.needs_review if proposal else RunStatus.completed
    )
    session.flush()


def _persist_proposal(
    session: Session,
    settings: Settings,
    run: AgentRun,
    ctx: RunContext,
    outcome: RunOutcome,
) -> ProposedAction | None:
    """Turn the model's suggestion into an immutable, hash-bound proposal.

    The model supplies a description, a rationale and a relative due date. Everything
    that carries authority — the organization, the target order, its record version,
    the assignee, the absolute due date — is resolved here, server-side. The model
    cannot address another tenant or another order however it phrases itself.
    """
    draft = outcome.result.proposed_action if outcome.result else None
    if draft is None:
        return None
    if run.target_order_id is None:
        # Nothing to bind the approval to. A proposal without a source record cannot be
        # invalidated when the world changes, so it is not offered at all.
        log.info("run.proposal_dropped_no_target", run_id=str(run.id))
        return None

    from opspilot.models import Order

    repo = TenantScopedRepository(session, run.organization_id)
    order = repo.get(Order, run.target_order_id)
    if order is None or order.organization_id != run.organization_id:  # pragma: no cover
        return None

    due_at = utcnow() + timedelta(days=draft.due_in_days)
    payload = {
        "action_type": ActionType.create_followup_task.value,
        "order_id": str(order.id),
        "order_number": order.order_number,
        "assignee_id": str(run.initiated_by),
        "due_at": due_at,
        "description": draft.description,
    }
    digest = payload_hash(payload)

    existing = session.scalar(
        repo.query(ProposedAction).where(
            ProposedAction.run_id == run.id,
            ProposedAction.payload_hash == digest,
        )
    )
    if existing is not None:
        # A retried run must not create a second identical proposal.
        return existing

    proposal = ProposedAction(
        organization_id=run.organization_id,
        run_id=run.id,
        action_type=ActionType.create_followup_task,
        payload_json=json.loads(canonical_json(payload)),
        payload_hash=digest,
        source_record_type="order",
        source_record_id=order.id,
        source_record_version=order.record_version,
        status=ActionStatus.pending,
        expires_at=utcnow() + timedelta(hours=settings.proposal_expiry_hours),
    )
    session.add(proposal)
    session.flush()
    log.info("proposal.created", run_id=str(run.id), action_id=str(proposal.id))
    return proposal


def count_runs(session: Session, principal: Principal) -> int:
    repo = TenantScopedRepository(session, principal.organization_id)
    return session.scalar(select(func.count()).select_from(repo.query(AgentRun).subquery())) or 0
