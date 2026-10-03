"""Immutable proposals, supervisor decisions and atomic outbox creation."""

import uuid
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from opspilot.auth.principal import Principal
from opspilot.core.clock import utcnow
from opspilot.core.config import Settings
from opspilot.core.errors import (
    IllegalTransition,
    NotAuthorized,
    ProposalExpired,
    ResourceNotFound,
    StaleProposal,
    WritesDisabled,
)
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.approval import ApprovalContext, evaluate_approval
from opspilot.domain.enums import (
    ActionStatus,
    ActionType,
    ActorType,
    ApprovalDecision,
    MembershipStatus,
    Role,
    RunStatus,
)
from opspilot.domain.hashing import payload_hash
from opspilot.domain.state import transition_action, transition_run
from opspilot.models import (
    AgentRun,
    Approval,
    AuditEvent,
    Membership,
    OperationTask,
    Order,
    OutboxEvent,
    ProposedAction,
)


class FollowupPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: ActionType
    order_id: uuid.UUID
    order_number: str
    assignee_id: uuid.UUID
    due_at: AwareDatetime
    description: str = Field(min_length=1, max_length=500)


def locked_action(repo: TenantScopedRepository, action_id: uuid.UUID) -> ProposedAction:
    action = repo.session.scalar(
        repo.query(ProposedAction)
        .where(ProposedAction.id == action_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if action is None:
        raise ResourceNotFound("Action not found")
    return action


def valid_payload(action: ProposedAction) -> bool:
    try:
        payload = FollowupPayload.model_validate(action.payload_json)
        return (
            payload_hash(action.payload_json) == action.payload_hash
            and payload.order_id == action.source_record_id
            and action.source_record_type == "order"
            and payload.action_type == action.action_type
        )
    except (ValidationError, TypeError, ValueError):
        return False


def audit(
    repo: TenantScopedRepository,
    action: ProposedAction,
    event: str,
    actor: uuid.UUID | None = None,
    **metadata: Any,
) -> None:
    repo.add(
        AuditEvent(
            actor_type=ActorType.user if actor else ActorType.worker,
            actor_id=actor,
            event_type=event,
            object_type="proposed_action",
            object_id=action.id,
            occurred_at=utcnow(),
            event_metadata=metadata,
        )
    )


def complete_run(repo: TenantScopedRepository, action: ProposedAction) -> None:
    run = repo.get_or_404(AgentRun, action.run_id)
    if run.status == RunStatus.needs_review:
        run.status = transition_run(run.status, RunStatus.completed)


def _assignee_name(repo: TenantScopedRepository, action: ProposedAction) -> str | None:
    """Display name of the payload's assignee, resolved within this organization only."""
    try:
        assignee_id = uuid.UUID(str(action.payload_json.get("assignee_id")))
    except (TypeError, ValueError, AttributeError):
        return None
    member = repo.get(Membership, assignee_id)
    return member.display_name if member else None


def view(repo: TenantScopedRepository, action: ProposedAction) -> dict[str, Any]:
    order = (
        repo.get(Order, action.source_record_id) if action.source_record_type == "order" else None
    )
    operation = repo.find_by(OperationTask, proposed_action_id=action.id)
    return {
        "assignee_name": _assignee_name(repo, action),
        "id": action.id,
        "run_id": action.run_id,
        "action_type": action.action_type,
        "status": action.status,
        "payload": action.payload_json,
        "payload_hash": action.payload_hash,
        "source_record_type": action.source_record_type,
        "source_record_id": action.source_record_id,
        "source_record_version": action.source_record_version,
        "source_changed": order is None or order.record_version != action.source_record_version,
        "payload_valid": valid_payload(action),
        "decision_expired": utcnow() >= action.expires_at,
        "expires_at": action.expires_at,
        "created_at": action.created_at,
        "downstream_task_id": operation.downstream_task_id if operation else None,
        "execution_error": (operation.response_json or {}).get("error_code") if operation else None,
    }


def list_actions(
    session: Session, principal: Principal, *, status_filter: ActionStatus | None, limit: int
) -> list[dict[str, Any]]:
    repo = TenantScopedRepository(session, principal.organization_id)
    stmt = repo.query(ProposedAction)
    if status_filter is not None:
        stmt = stmt.where(ProposedAction.status == status_filter)
    return [
        view(repo, row)
        for row in session.scalars(
            stmt.order_by(ProposedAction.created_at.desc()).limit(limit)
        ).all()
    ]


def get_action(session: Session, principal: Principal, action_id: uuid.UUID) -> dict[str, Any]:
    repo = TenantScopedRepository(session, principal.organization_id)
    return view(repo, repo.get_or_404(ProposedAction, action_id))


def decide(
    session: Session,
    settings: Settings,
    principal: Principal,
    action_id: uuid.UUID,
    asserted_hash: str,
    comment: str | None,
    *,
    approve: bool,
) -> dict[str, Any]:
    repo = TenantScopedRepository(session, principal.organization_id)
    action = locked_action(repo, action_id)
    membership = session.scalar(
        repo.query(Membership)
        .where(Membership.id == principal.membership_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if (
        membership is None
        or membership.status != MembershipStatus.active
        or membership.role != Role.supervisor
    ):
        raise NotAuthorized("An active supervisor membership is required")
    if action.status != ActionStatus.pending:
        raise IllegalTransition("This proposal has already been decided")
    if action.expires_at <= utcnow():
        raise ProposalExpired("Proposal expired; run a new investigation")
    if asserted_hash != action.payload_hash:
        raise StaleProposal("The proposal differs from the payload displayed")
    if approve:
        if not settings.feature_writes_enabled:
            raise WritesDisabled("Approved writes are disabled")
        if not valid_payload(action):
            raise StaleProposal("Stored payload integrity failed; run a new investigation")
        order = session.scalar(
            repo.query(Order)
            .where(Order.id == action.source_record_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        check = evaluate_approval(
            ApprovalContext(
                action.status,
                action.organization_id,
                action.payload_hash,
                action.source_record_version,
                action.expires_at,
                membership.organization_id,
                membership.role,
                membership.status,
                asserted_hash,
                order.record_version if order else -1,
                utcnow(),
            )
        )
        if not check.permitted:
            raise StaleProposal(check.reason or "Proposal is stale")
    decision = ApprovalDecision.approved if approve else ApprovalDecision.rejected
    repo.add(
        Approval(
            proposed_action_id=action.id,
            decision=decision,
            approver_membership_id=membership.id,
            approver_role_at_decision=membership.role,
            payload_hash_at_decision=asserted_hash,
            source_version_at_decision=action.source_record_version,
            decided_at=utcnow(),
            comment=comment,
        )
    )
    action.status = transition_action(
        action.status, ActionStatus.approved if approve else ActionStatus.rejected
    )
    if approve:
        repo.add(
            OutboxEvent(
                aggregate_type="proposed_action",
                aggregate_id=action.id,
                event_type="action.execute",
                payload_json={"action_id": str(action.id)},
                created_at=utcnow(),
            )
        )
    complete_run(repo, action)
    audit(
        repo,
        action,
        f"action.{decision.value}",
        membership.id,
        payload_hash=action.payload_hash,
        source_version=action.source_record_version,
    )
    session.flush()
    return view(repo, action)


def expire_one(session: Session, organization_id: uuid.UUID, action_id: uuid.UUID) -> bool:
    repo = TenantScopedRepository(session, organization_id)
    action = locked_action(repo, action_id)
    if action.status != ActionStatus.pending:
        return False
    order = repo.get(Order, action.source_record_id)
    if (
        action.expires_at > utcnow()
        and order is not None
        and order.record_version == action.source_record_version
        and valid_payload(action)
    ):
        return False
    action.status = transition_action(action.status, ActionStatus.expired)
    complete_run(repo, action)
    audit(repo, action, "action.expired")
    return True
