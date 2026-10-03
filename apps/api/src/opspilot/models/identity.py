"""Organizations and memberships — the only source of tenant and role.

A JWT proves *who*. This table decides *where* and *as what*. Nothing else may.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from opspilot.domain.enums import MembershipStatus, Role
from opspilot.models.base import Base, TenantMixin, TimestampMixin
from opspilot.models.enum_types import MEMBERSHIP_STATUS, ROLE


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Membership(Base, TenantMixin, TimestampMixin):
    __tablename__ = "memberships"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # OIDC "sub". Opaque, provider-issued, never parsed for meaning.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[Role] = mapped_column(ROLE, nullable=False)
    status: Mapped[MembershipStatus] = mapped_column(
        MEMBERSHIP_STATUS, nullable=False, default=MembershipStatus.active
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        # One membership per subject per organization; a subject may belong to several.
        UniqueConstraint("subject", "organization_id", name="uq_memberships_subject_org"),
        # Principal resolution runs on every authenticated request; keep it cheap.
        Index(
            "ix_memberships_active_subject",
            "subject",
            postgresql_where=text("status = 'active'"),
        ),
    )

    @property
    def is_supervisor(self) -> bool:
        return self.role is Role.supervisor and self.status is MembershipStatus.active
