"""Every enumeration in the system, defined once.

These names are persisted as native PostgreSQL enums and are asserted on by the
evaluation harness and the UI. Adding a member is a migration; renaming one is a
breaking change.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    analyst = "analyst"
    supervisor = "supervisor"


class MembershipStatus(StrEnum):
    active = "active"
    revoked = "revoked"


class OrderStatus(StrEnum):
    open = "open"
    partially_shipped = "partially_shipped"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class ShipmentStatus(StrEnum):
    pending = "pending"
    in_transit = "in_transit"
    delayed = "delayed"
    delivered = "delivered"
    exception = "exception"


class ShipmentEventType(StrEnum):
    dispatched = "dispatched"
    in_transit = "in_transit"
    delayed = "delayed"
    exception = "exception"
    delivered = "delivered"
    note = "note"


class DocumentStatus(StrEnum):
    pending = "pending"
    indexing = "indexing"
    active = "active"
    failed = "failed"
    deleted = "deleted"


class RunStatus(StrEnum):
    queued = "queued"
    investigating = "investigating"
    needs_review = "needs_review"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled)


class IncompleteReason(StrEnum):
    """Why a run stopped short. A run can be useful and incomplete at the same time."""

    budget_exhausted = "budget_exhausted"
    turn_limit = "turn_limit"
    tool_limit = "tool_limit"
    deadline = "deadline"
    provider_unavailable = "provider_unavailable"
    insufficient_evidence = "insufficient_evidence"
    cancelled = "cancelled"


class ActionStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"
    executing = "executing"
    succeeded = "succeeded"
    failed = "failed"
    reconciliation_required = "reconciliation_required"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ActionStatus.rejected,
            ActionStatus.expired,
            ActionStatus.succeeded,
            ActionStatus.failed,
        )


class ActionType(StrEnum):
    """The complete set of writes the system can propose. One member, on purpose."""

    create_followup_task = "create_followup_task"


class ApprovalDecision(StrEnum):
    approved = "approved"
    rejected = "rejected"


class OperationTaskStatus(StrEnum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    unknown = "unknown"


class RunStepType(StrEnum):
    model_call = "model_call"
    tool_call = "tool_call"
    validation = "validation"


class ActorType(StrEnum):
    user = "user"
    system = "system"
    worker = "worker"


class Usefulness(StrEnum):
    useful = "useful"
    partly = "partly"
    not_useful = "not_useful"


class ToolName(StrEnum):
    """The tool allowlist. A name outside this enum is never dispatched."""

    get_order_context = "get_order_context"
    list_overdue_orders = "list_overdue_orders"
    search_policies = "search_policies"
