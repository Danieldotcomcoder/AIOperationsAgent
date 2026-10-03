"""Shared PostgreSQL enum type instances.

Every native enum type is declared **once**, here, and imported by the model modules
that use it. Two separate ``Enum(Role, name="role")`` objects would each try to
``CREATE TYPE role``, and the second one fails — which is exactly the bug that bites
the first time a second table reuses a role or status type.

Reusing one instance also means ``ALTER TYPE ... ADD VALUE`` in a migration has a
single obvious place to correspond to.
"""

from __future__ import annotations

from sqlalchemy import Enum

from opspilot.domain import enums

#: identity
ROLE = Enum(enums.Role, name="role")
MEMBERSHIP_STATUS = Enum(enums.MembershipStatus, name="membership_status")

#: operations
ORDER_STATUS = Enum(enums.OrderStatus, name="order_status")
SHIPMENT_STATUS = Enum(enums.ShipmentStatus, name="shipment_status")
SHIPMENT_EVENT_TYPE = Enum(enums.ShipmentEventType, name="shipment_event_type")

#: knowledge
DOCUMENT_STATUS = Enum(enums.DocumentStatus, name="document_status")

#: agent runs
RUN_STATUS = Enum(enums.RunStatus, name="run_status")
INCOMPLETE_REASON = Enum(enums.IncompleteReason, name="incomplete_reason")
RUN_STEP_TYPE = Enum(enums.RunStepType, name="run_step_type")

#: approval and execution
ACTION_STATUS = Enum(enums.ActionStatus, name="action_status")
ACTION_TYPE = Enum(enums.ActionType, name="action_type")
APPROVAL_DECISION = Enum(enums.ApprovalDecision, name="approval_decision")
OPERATION_TASK_STATUS = Enum(enums.OperationTaskStatus, name="operation_task_status")

#: audit and feedback
ACTOR_TYPE = Enum(enums.ActorType, name="actor_type")
USEFULNESS = Enum(enums.Usefulness, name="usefulness")
