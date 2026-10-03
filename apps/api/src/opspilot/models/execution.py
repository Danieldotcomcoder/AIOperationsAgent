"""Proposals, approvals, the outbox, and the downstream effect ledger.

This module is the write path. Everything in it exists to make one sentence true:
*an approved action produces exactly one downstream task, even if the process dies at
the worst possible moment.*
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from opspilot.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalDecision,
    OperationTaskStatus,
    Role,
)
from opspilot.models.base import Base, TenantMixin, TimestampMixin, tenant_fk, tenant_unique
from opspilot.models.enum_types import (
    ACTION_STATUS,
    ACTION_TYPE,
    APPROVAL_DECISION,
    OPERATION_TASK_STATUS,
    ROLE,
)


class ProposedAction(Base, TenantMixin, TimestampMixin):
    """Immutable. There is no update path for ``payload_json`` — an amended payload
    is a different action that needs its own approval (ADR 0004)."""

    __tablename__ = "proposed_actions"

    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action_type: Mapped[ActionType] = mapped_column(ACTION_TYPE, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: sha256 of the canonical serialization; the client must echo it to approve.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: What the proposal was reasoned about. If this record moved, the proposal dies.
    source_record_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_record_version: Mapped[int] = mapped_column(nullable=False)

    status: Mapped[ActionStatus] = mapped_column(
        ACTION_STATUS, nullable=False, default=ActionStatus.pending
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        tenant_unique(),
        tenant_fk("run_id", "agent_runs", ondelete="CASCADE"),
        # A retried run cannot create a second identical proposal.
        UniqueConstraint("organization_id", "run_id", "payload_hash"),
        Index("ix_proposed_actions_org_status", "organization_id", "status", "expires_at"),
    )


class Approval(Base, TenantMixin, TimestampMixin):
    """Append-only record of a decision, capturing the state it was made against."""

    __tablename__ = "approvals"

    proposed_action_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    decision: Mapped[ApprovalDecision] = mapped_column(APPROVAL_DECISION, nullable=False)
    approver_membership_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Role at the moment of decision, not current role. Audit must not drift.
    approver_role_at_decision: Mapped[Role] = mapped_column(ROLE, nullable=False)
    payload_hash_at_decision: Mapped[str] = mapped_column(String(64), nullable=False)
    source_version_at_decision: Mapped[int] = mapped_column(nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        tenant_unique(),
        tenant_fk("proposed_action_id", "proposed_actions", ondelete="CASCADE"),
        tenant_fk("approver_membership_id", "memberships"),
        # One decision per proposal. A re-proposal is a new row in proposed_actions.
        UniqueConstraint("organization_id", "proposed_action_id"),
    )


class OutboxEvent(Base, TenantMixin):
    """Written in the same transaction as the state change it describes (ADR 0003).

    Claimed by the dispatcher with ``FOR UPDATE SKIP LOCKED`` so several dispatchers
    can run without publishing the same row twice.
    """

    __tablename__ = "outbox_events"

    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        tenant_unique(),
        # The sweep query: unpublished, due now, oldest first.
        Index(
            "ix_outbox_unpublished",
            "next_attempt_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )


class OperationTask(Base, TenantMixin, TimestampMixin):
    """Our ledger of the downstream effect — distinct from the downstream's own row.

    ``idempotency_key`` is deterministic (see domain.hashing), so a retry from a
    different process on a different day produces the same key and the operations API
    returns the original task instead of creating a second one.
    """

    __tablename__ = "operation_tasks"

    proposed_action_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    downstream_task_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[OperationTaskStatus] = mapped_column(
        OPERATION_TASK_STATUS, nullable=False, default=OperationTaskStatus.pending
    )
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        tenant_unique(),
        tenant_fk("proposed_action_id", "proposed_actions", ondelete="RESTRICT"),
        UniqueConstraint("organization_id", "proposed_action_id"),
    )
