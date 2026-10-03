"""Durable claims and reconciliation before every possible downstream retry."""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from opspilot.core.clock import utcnow
from opspilot.core.config import Settings
from opspilot.core.errors import DownstreamRejected, DownstreamUncertain
from opspilot.db.repository import TenantScopedRepository
from opspilot.db.session import session_scope
from opspilot.domain.approval import ApprovalContext, evaluate_approval
from opspilot.domain.enums import ActionStatus, ApprovalDecision, OperationTaskStatus, Role
from opspilot.domain.hashing import idempotency_key
from opspilot.domain.state import transition_action
from opspilot.models import Approval, Membership, OperationTask, Order, OutboxEvent, ProposedAction
from opspilot.ops.client import OperationsClient
from opspilot.ops.contracts import CreateTaskRequest, TaskResponse
from opspilot.services.actions import FollowupPayload, audit, locked_action, valid_payload


def execution_allowed(repo: TenantScopedRepository, action: ProposedAction) -> bool:
    if not valid_payload(action):
        return False
    approval = repo.find_by(Approval, proposed_action_id=action.id)
    if approval is None or approval.decision != ApprovalDecision.approved:
        return False
    if approval.approver_role_at_decision != Role.supervisor:
        return False
    if approval.source_version_at_decision != action.source_record_version:
        return False
    member = repo.session.scalar(
        repo.query(Membership)
        .where(Membership.id == approval.approver_membership_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    order = repo.session.scalar(
        repo.query(Order)
        .where(Order.id == action.source_record_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if member is None or order is None:
        return False
    # Re-evaluate the original pending decision at its recorded time, using current
    # membership and source state. Delivery after the expiry is explicitly allowed.
    return evaluate_approval(
        ApprovalContext(
            ActionStatus.pending,
            action.organization_id,
            action.payload_hash,
            action.source_record_version,
            action.expires_at,
            member.organization_id,
            member.role,
            member.status,
            approval.payload_hash_at_decision,
            order.record_version,
            approval.decided_at,
        )
    ).permitted


def prepare(
    session: Session, settings: Settings, organization_id: uuid.UUID, action_id: uuid.UUID
) -> bool:
    repo = TenantScopedRepository(session, organization_id)
    action = locked_action(repo, action_id)
    if action.status in {ActionStatus.executing, ActionStatus.reconciliation_required}:
        return True
    if action.status != ActionStatus.approved or not settings.feature_writes_enabled:
        return False
    if not execution_allowed(repo, action):
        action.status = transition_action(action.status, ActionStatus.expired)
        audit(repo, action, "action.invalidated")
        return False
    action.status = transition_action(action.status, ActionStatus.executing)
    repo.add(
        OperationTask(
            proposed_action_id=action.id,
            idempotency_key=idempotency_key(organization_id, action.id, action.payload_hash),
            status=OperationTaskStatus.pending,
            last_attempt_at=utcnow(),
        )
    )
    audit(repo, action, "action.claimed")
    session.flush()
    return True


def _uncertain(
    repo: TenantScopedRepository, action: ProposedAction, operation: OperationTask, code: str
) -> None:
    if action.status == ActionStatus.executing:
        action.status = transition_action(action.status, ActionStatus.reconciliation_required)
    operation.status = OperationTaskStatus.unknown
    operation.response_json = {"error_code": code}
    audit(repo, action, "action.reconciliation_required", error_code=code)


def _success(
    repo: TenantScopedRepository,
    action: ProposedAction,
    operation: OperationTask,
    result: TaskResponse,
) -> None:
    if (
        result.organization_id != action.organization_id
        or result.order_id != action.source_record_id
        or result.idempotency_key != operation.idempotency_key
    ):
        raise DownstreamUncertain("Downstream task does not match this action")
    action.status = transition_action(action.status, ActionStatus.succeeded)
    operation.status = OperationTaskStatus.succeeded
    operation.downstream_task_id = result.task_id
    operation.response_json = {"task_id": result.task_id, "deduplicated": result.deduplicated}
    operation.reconciled_at = utcnow()
    audit(repo, action, "action.succeeded", task_id=result.task_id)


def deliver(
    session: Session,
    settings: Settings,
    organization_id: uuid.UUID,
    action_id: uuid.UUID,
    client: OperationsClient,
) -> None:
    repo = TenantScopedRepository(session, organization_id)
    action = locked_action(repo, action_id)
    if action.status not in {ActionStatus.executing, ActionStatus.reconciliation_required}:
        return
    operation = repo.find_by(OperationTask, proposed_action_id=action.id)
    if operation is None:
        raise RuntimeError("Executing action has no durable operation ledger")
    operation.last_attempt_at = utcnow()
    try:
        # Always establish absence before sending, including crash redeliveries.
        found = client.lookup(operation.idempotency_key)
        if found is not None:
            _success(repo, action, operation, found)
            return
        if not settings.feature_writes_enabled:
            _uncertain(repo, action, operation, "feature.writes_disabled")
            return
        if not execution_allowed(repo, action):
            action.status = transition_action(action.status, ActionStatus.failed)
            operation.status = OperationTaskStatus.failed
            operation.response_json = {"error_code": "approval.stale"}
            audit(repo, action, "action.invalidated")
            return
        if action.status == ActionStatus.reconciliation_required:
            action.status = transition_action(action.status, ActionStatus.executing)
        payload = FollowupPayload.model_validate(action.payload_json)
        operation.attempts += 1
        result = client.create(
            operation.idempotency_key,
            CreateTaskRequest(
                organization_id=organization_id,
                order_id=payload.order_id,
                assignee_id=payload.assignee_id,
                due_at=payload.due_at,
                description=payload.description,
            ),
        )
        _success(repo, action, operation, result)
    except DownstreamUncertain:
        _uncertain(repo, action, operation, "ops.uncertain")
    except DownstreamRejected:
        action.status = transition_action(action.status, ActionStatus.failed)
        operation.status = OperationTaskStatus.failed
        operation.response_json = {"error_code": "ops.rejected"}
        audit(repo, action, "action.failed", error_code="ops.rejected")


def process_action(settings: Settings, organization_id: uuid.UUID, action_id: uuid.UUID) -> None:
    # Persist the claim BEFORE network IO. A crash in the second transaction leaves
    # executing durable, so redelivery/recovery will lookup rather than assume failure.
    with session_scope() as session:
        ready = prepare(session, settings, organization_id, action_id)
    if ready:
        with session_scope() as session:
            deliver(session, settings, organization_id, action_id, OperationsClient(settings))


def publish_event(
    session: Session, organization_id: uuid.UUID, event_id: uuid.UUID, publish: Callable[..., Any]
) -> bool:
    repo = TenantScopedRepository(session, organization_id)
    event = session.scalar(
        repo.query(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update(skip_locked=True)
    )
    if event is None or event.published_at is not None:
        return False
    if event.next_attempt_at and event.next_attempt_at > utcnow():
        return False
    event.attempts += 1
    try:
        if event.event_type != "action.execute" or event.aggregate_type != "proposed_action":
            raise ValueError("Unsupported outbox event")
        action = repo.get_or_404(ProposedAction, event.aggregate_id)
        publish("action.execute", args=[str(action.id)], queue="execute", retry=False)
    except Exception as exc:
        event.last_error = type(exc).__name__
        event.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** min(event.attempts, 8)))
        return False
    event.published_at = utcnow()
    event.last_error = None
    return True
