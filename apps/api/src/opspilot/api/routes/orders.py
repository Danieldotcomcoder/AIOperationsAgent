"""Operations inbox: order list and detail.

The overdue list here and the ``list_overdue_orders`` tool answer the same question
through the same query builder in ``services/orders.py``. Two implementations would
drift, and the agent would then be explaining a different world than the one on screen.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from opspilot.auth.dependencies import CurrentPrincipal, DbSession
from opspilot.core.clock import business_date
from opspilot.core.config import get_settings
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import OrderStatus
from opspilot.services import orders as order_service

router = APIRouter()


class OrderSummary(BaseModel):
    id: uuid.UUID
    order_number: str
    customer_name: str
    status: OrderStatus
    promised_date: date
    days_overdue: int
    total_amount: Decimal
    has_open_exception: bool
    #: Stable reference the agent must cite for any claim about this order.
    evidence_id: str


class ShipmentEventView(BaseModel):
    id: uuid.UUID
    evidence_id: str
    event_type: str
    occurred_at: datetime
    #: Carrier free text. Rendered as text, never as markup (see SECURITY.md).
    note: str | None


class OrderLineView(BaseModel):
    id: uuid.UUID
    sku: str
    product_name: str
    quantity: int
    quantity_shipped: int
    #: Surfaced explicitly: "incomplete shipment" is a seeded exception class.
    outstanding: int
    unit_price: Decimal


class ShipmentView(BaseModel):
    id: uuid.UUID
    evidence_id: str
    carrier: str
    #: Nullable on purpose: "missing tracking" is a seeded exception class.
    tracking_reference: str | None
    status: str
    shipped_at: datetime | None
    estimated_delivery: date | None
    delivered_at: datetime | None


class InvestigationRef(BaseModel):
    id: uuid.UUID
    status: str
    incomplete_reason: str | None
    created_at: datetime


class FollowupTaskView(BaseModel):
    """A task in the SIMULATED ERP, created only by an approved proposal."""

    id: uuid.UUID
    evidence_id: str
    description: str
    status: str
    due_at: datetime
    created_at: datetime


class OrderDetail(OrderSummary):
    #: What an approval binds to (ADR 0004). Surfaced so the approval screen can show
    #: whether the order moved since a proposal was made.
    record_version: int
    lines: list[OrderLineView]
    shipments: list[ShipmentView]
    events: list[ShipmentEventView]
    investigations: list[InvestigationRef]
    followup_tasks: list[FollowupTaskView]


class OrderCounts(BaseModel):
    as_of: date
    total: int
    overdue: int


def _summary(row: order_service.OrderSummaryRow) -> OrderSummary:
    return OrderSummary(
        id=row.id,
        order_number=row.order_number,
        customer_name=row.customer_name,
        status=row.status,
        promised_date=row.promised_date,
        days_overdue=row.days_overdue,
        total_amount=row.total_amount,
        has_open_exception=row.has_open_exception,
        evidence_id=row.evidence_id,
    )


@router.get("")
def list_orders(
    principal: CurrentPrincipal,
    session: DbSession,
    overdue_only: bool = False,
    flagged_only: bool = False,
    q: str | None = Query(default=None, max_length=64),
    as_of: date | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[OrderSummary]:
    """``flagged_only`` keeps orders a carrier reported delayed or in exception;
    ``q`` matches order number or customer name."""
    repo = TenantScopedRepository(session, principal.organization_id)
    rows = order_service.list_orders(
        session,
        repo,
        as_of=as_of or business_date(get_settings()),
        overdue_only=overdue_only,
        flagged_only=flagged_only,
        search=q,
        limit=limit,
        offset=offset,
    )
    return [_summary(r) for r in rows]


@router.get("/counts")
def order_counts(
    principal: CurrentPrincipal, session: DbSession, as_of: date | None = None
) -> OrderCounts:
    repo = TenantScopedRepository(session, principal.organization_id)
    day = as_of or business_date(get_settings())
    return OrderCounts(as_of=day, **order_service.count_orders(session, repo, as_of=day))


@router.get("/{order_id}")
def get_order(
    order_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    as_of: date | None = None,
) -> OrderDetail:
    """A user in organization A requesting B's order id gets 404, never 403.

    This is the Week 1 release gate. The repository raises ``ResourceNotFound`` for a
    row outside the principal's organization, so the disclosure cannot happen here even
    if a future edit forgets about it — and the composite foreign keys mean it cannot
    happen in the database either.
    """
    repo = TenantScopedRepository(session, principal.organization_id)
    detail = order_service.get_order_detail(
        session, repo, order_id, as_of=as_of or business_date(get_settings())
    )
    activity = order_service.order_activity(session, repo, order_id)
    return OrderDetail(
        **_summary(detail.summary).model_dump(),
        record_version=detail.record_version,
        lines=[OrderLineView.model_validate(line) for line in detail.lines],
        shipments=[ShipmentView.model_validate(shipment) for shipment in detail.shipments],
        events=[
            ShipmentEventView(
                id=e.id,
                evidence_id=e.evidence_id,
                event_type=e.event_type,
                occurred_at=e.occurred_at,
                note=e.note,
            )
            for e in detail.events
        ],
        investigations=[InvestigationRef(**run) for run in activity.runs],
        followup_tasks=[FollowupTaskView(**task) for task in activity.tasks],
    )
