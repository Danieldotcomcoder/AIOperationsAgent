"""Operational records: the facts an investigation reads.

Order numbers deliberately collide across organizations (SO-1042 exists in both
seeded orgs). Any lookup that forgets the tenant filter therefore returns the wrong
organization's order in a test, instead of leaking silently in production.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from opspilot.domain.enums import OrderStatus, ShipmentEventType, ShipmentStatus
from opspilot.models.base import Base, TenantMixin, TimestampMixin, tenant_fk, tenant_unique
from opspilot.models.enum_types import ORDER_STATUS, SHIPMENT_EVENT_TYPE, SHIPMENT_STATUS


class Customer(Base, TenantMixin, TimestampMixin):
    __tablename__ = "customers"

    external_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    address_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (tenant_unique(), UniqueConstraint("organization_id", "external_ref"))


class Product(Base, TenantMixin, TimestampMixin):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    __table_args__ = (tenant_unique(), UniqueConstraint("organization_id", "sku"))


class Order(Base, TenantMixin, TimestampMixin):
    __tablename__ = "orders"

    order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(ORDER_STATUS, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    promised_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    #: Bound by an approval (ADR 0004). Maintained by SQLAlchemy — never assign to it.
    record_version: Mapped[int] = mapped_column(nullable=False, default=1)

    __mapper_args__ = {"version_id_col": record_version}

    __table_args__ = (
        tenant_unique(),
        # Not globally unique. Collisions across organizations are intentional.
        UniqueConstraint("organization_id", "order_number"),
        tenant_fk("customer_id", "customers"),
        # Drives list_overdue_orders.
        Index("ix_orders_org_promised", "organization_id", "promised_date", "status"),
    )


class OrderLine(Base, TenantMixin, TimestampMixin):
    __tablename__ = "order_lines"

    order_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    quantity_shipped: Mapped[int] = mapped_column(nullable=False, default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    __table_args__ = (
        tenant_unique(),
        tenant_fk("order_id", "orders", ondelete="CASCADE"),
        tenant_fk("product_id", "products"),
        Index("ix_order_lines_org_order", "organization_id", "order_id"),
    )


class Shipment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "shipments"

    order_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    carrier: Mapped[str] = mapped_column(String(100), nullable=False)
    #: Nullable on purpose: "missing tracking" is a seeded exception class.
    tracking_reference: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[ShipmentStatus] = mapped_column(SHIPMENT_STATUS, nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_delivery: Mapped[date | None] = mapped_column(Date)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        tenant_unique(),
        tenant_fk("order_id", "orders", ondelete="CASCADE"),
        Index("ix_shipments_org_order", "organization_id", "order_id"),
    )


class ShipmentEvent(Base, TenantMixin, TimestampMixin):
    __tablename__ = "shipment_events"

    shipment_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[ShipmentEventType] = mapped_column(SHIPMENT_EVENT_TYPE, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Carrier-side identifier; unique per organization so replayed feeds are idempotent.
    source_event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: UNTRUSTED. Free text from a carrier feed and a designated prompt-injection
    #: surface in the evaluation set. It reaches the model as data, never as
    #: instruction, and it is never logged.
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        tenant_unique(),
        tenant_fk("shipment_id", "shipments", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "source_event_id"),
        Index(
            "ix_shipment_events_org_shipment_time", "organization_id", "shipment_id", "occurred_at"
        ),
    )
