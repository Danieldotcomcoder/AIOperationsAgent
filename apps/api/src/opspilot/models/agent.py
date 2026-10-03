"""Run state and the per-run accounting that the metrics screen and the release
gates read.

``run_steps`` stores digests and metadata, never content. Traces are pruned after 14
days by a beat job; audit events live for 90.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from opspilot.domain.enums import IncompleteReason, RunStatus, RunStepType
from opspilot.models.base import Base, TenantMixin, TimestampMixin, tenant_fk, tenant_unique
from opspilot.models.enum_types import INCOMPLETE_REASON, RUN_STATUS, RUN_STEP_TYPE


class AgentRun(Base, TenantMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    initiated_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: UNTRUSTED user input. Data for the model, never instruction to the system.
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_order_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    status: Mapped[RunStatus] = mapped_column(RUN_STATUS, nullable=False, default=RunStatus.queued)
    incomplete_reason: Mapped[IncompleteReason | None] = mapped_column(INCOMPLETE_REASON)
    error_code: Mapped[str | None] = mapped_column(String(64))

    # -- reproducibility: which code produced this answer ------------------
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # -- accounting --------------------------------------------------------
    tokens_in: Mapped[int] = mapped_column(nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(nullable=False, default=0)
    #: Estimated from a price table, not billed truth. Labelled as an estimate in the UI.
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    turn_count: Mapped[int] = mapped_column(nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column()
    #: Validated structured output (InvestigationResult). Pruned on trace expiry.
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trace_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_pruned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_reservation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        tenant_unique(),
        Index("ix_agent_runs_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_agent_runs_trace_expiry", "trace_expires_at"),
    )


class RunStep(Base, TenantMixin, TimestampMixin):
    __tablename__ = "run_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(nullable=False)
    step_type: Mapped[RunStepType] = mapped_column(RUN_STEP_TYPE, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    #: Validated tool arguments only. Never raw model text, never document content.
    arguments_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: sha256 of the tool result, so a trace can be compared without storing content.
    result_digest: Mapped[str | None] = mapped_column(String(64))
    result_row_count: Mapped[int | None] = mapped_column()
    latency_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        tenant_unique(),
        tenant_fk("run_id", "agent_runs", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "run_id", "ordinal"),
    )


class EvidenceGrant(Base, TenantMixin):
    """Exactly which evidence ids this run was shown.

    Final-answer citation validation is a set-membership check against these rows.
    A citation the run never received cannot pass, however plausible it looks.
    """

    __tablename__ = "evidence_grants"

    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(80), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        tenant_unique(),
        tenant_fk("run_id", "agent_runs", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "run_id", "evidence_id"),
    )
