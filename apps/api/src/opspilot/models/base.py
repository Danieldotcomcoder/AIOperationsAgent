"""Declarative base, mixins, and the composite-key convention that enforces tenancy
in the database rather than only in Python.

The pattern every tenant table follows:

    class Thing(Base, TenantMixin, TimestampMixin):
        __tablename__ = "things"
        # ... columns ...
        __table_args__ = (
            # lets children reference (organization_id, id) as a unit
            UniqueConstraint("organization_id", "id"),
            # a child can only ever point at a parent in its own organization
            tenant_fk("order_id", "orders"),
        )

With that in place, associating a row with a parent in another organization is a
foreign-key violation, not a code review finding.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, MetaData, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from opspilot.core.ids import new_id

# Predictable constraint names: Alembic autogenerate produces stable diffs and a
# migration can drop a constraint by name without looking it up in the catalogue.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantMixin:
    """Marks a table as tenant-owned.

    Presence of this mixin is what ``TenantScopedRepository`` and the tenancy audit
    script look for. A tenant table that does not inherit it will be reported.
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def tenant_unique() -> UniqueConstraint:
    """Required on every tenant table so children can use a composite foreign key."""
    return UniqueConstraint("organization_id", "id")


def tenant_fk(
    column: str, parent_table: str, *, ondelete: str = "RESTRICT"
) -> ForeignKeyConstraint:
    """A foreign key that carries the tenant with it.

    ``(organization_id, <column>) -> (parent.organization_id, parent.id)``. The child's
    own ``organization_id`` participates, so a row can only reference a parent in the
    same organization — enforced by PostgreSQL, with no application code involved.
    """
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{parent_table}.organization_id", f"{parent_table}.id"],
        ondelete=ondelete,
        name=f"fk_{column}_tenant_{parent_table}",
    )
