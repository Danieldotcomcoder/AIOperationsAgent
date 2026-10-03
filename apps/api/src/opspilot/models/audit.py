"""Audit trail, user feedback, and the simulated ERP's own table.

``ops_tasks`` is written only by :mod:`opspilot.ops`. It is deliberately in its own
module and reached through an HTTP call so that idempotency and reconciliation are
exercised as real network code paths, not as a function import that cannot fail
halfway.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from opspilot.domain.enums import ActorType, Usefulness
from opspilot.models.base import Base, TenantMixin, TimestampMixin, tenant_fk, tenant_unique
from opspilot.models.enum_types import ACTOR_TYPE, USEFULNESS


class AuditEvent(Base, TenantMixin):
    """Append-only. Every authorization denial, approval decision, document deletion
    and execution outcome writes one row. Retained 90 days."""

    __tablename__ = "audit_events"

    actor_type: Mapped[ActorType] = mapped_column(ACTOR_TYPE, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Operational metadata only — no content, no secrets, no prompt text.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        tenant_unique(),
        Index("ix_audit_org_time", "organization_id", "occurred_at"),
        Index("ix_audit_object", "organization_id", "object_type", "object_id"),
    )


class Feedback(Base, TenantMixin, TimestampMixin):
    __tablename__ = "feedback"

    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    usefulness: Mapped[Usefulness] = mapped_column(USEFULNESS, nullable=False)
    correction_text: Mapped[str | None] = mapped_column(Text)
    #: Free-form failure class from the pilot log; kept loose until categories settle.
    failure_class: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        tenant_unique(),
        tenant_fk("run_id", "agent_runs", ondelete="CASCADE"),
        tenant_fk("membership_id", "memberships"),
    )


class OpsTask(Base, TenantMixin, TimestampMixin):
    """The simulated ERP boundary's table. Written only by ``opspilot.ops``.

    A duplicate ``idempotency_key`` returns the existing row with 200 rather than
    creating a second task — that behaviour is the whole point of this table.
    """

    __tablename__ = "ops_tasks"

    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    order_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    assignee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")

    __table_args__ = (
        tenant_unique(),
        tenant_fk("order_id", "orders"),
        Index("ix_ops_tasks_org_order", "organization_id", "order_id"),
    )
