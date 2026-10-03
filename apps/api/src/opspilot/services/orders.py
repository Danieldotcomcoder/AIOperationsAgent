"""Order reads.

This module is the single definition of "what is an overdue order". The operations
inbox and the agent's ``list_overdue_orders`` tool both call it, because two
implementations would drift and the agent would end up explaining a different world
than the one on the analyst's screen.

Every query is built from a ``TenantScopedRepository``, so the organization filter is
applied by construction rather than remembered by the author.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import OrderStatus, ShipmentStatus
from opspilot.domain.evidence import EvidenceKind, format_ref
from opspilot.models import Customer, Order, OrderLine, Product, Shipment, ShipmentEvent

#: Statuses that still owe the customer something. A delivered or cancelled order is
#: not overdue however old its promised date is.
OPEN_STATUSES = (OrderStatus.open, OrderStatus.partially_shipped, OrderStatus.shipped)

#: Hard ceiling on any list, whoever asks. The agent tool caps lower still (50).
MAX_PAGE = 100


@dataclass(frozen=True, slots=True)
class OrderSummaryRow:
    id: uuid.UUID
    order_number: str
    customer_name: str
    status: OrderStatus
    promised_date: date
    days_overdue: int
    total_amount: Decimal
    has_open_exception: bool
    evidence_id: str


@dataclass(frozen=True, slots=True)
class EventRow:
    id: uuid.UUID
    evidence_id: str
    event_type: str
    occurred_at: datetime
    note: str | None


@dataclass(frozen=True, slots=True)
class OrderDetailRow:
    summary: OrderSummaryRow
    record_version: int
    lines: list[dict[str, Any]]
    shipments: list[dict[str, Any]]
    events: list[EventRow]


def _base_query(repo: TenantScopedRepository) -> Select[tuple[Order, str]]:
    """Orders joined to their customer, already scoped to one organization."""
    return (
        repo.query(Order)
        .join(
            Customer,
            and_(
                Customer.id == Order.customer_id, Customer.organization_id == Order.organization_id
            ),
        )
        .add_columns(Customer.name.label("customer_name"))
    )


def overdue_predicate(as_of: date) -> ColumnElement[bool]:
    """Past its promised date and not yet settled.

    Used identically by the UI list and by the agent tool. Change it here or nowhere.
    """
    return and_(Order.promised_date < as_of, Order.status.in_(OPEN_STATUSES))


def _flagged_order_ids_stmt(repo: TenantScopedRepository) -> Select[tuple[uuid.UUID]]:
    """Order ids with a shipment the carrier has flagged.

    Deliberately narrow: it reports what the carrier said, not what we infer. An
    overdue order with a clean shipment trail is overdue, not "exceptional", and the
    distinction is one the analyst needs.
    """
    return (
        repo.query(Shipment)
        .where(Shipment.status.in_((ShipmentStatus.delayed, ShipmentStatus.exception)))
        .with_only_columns(Shipment.order_id)
    )


def _to_summary(order: Order, customer_name: str, as_of: date, flagged: bool) -> OrderSummaryRow:
    overdue = (as_of - order.promised_date).days
    return OrderSummaryRow(
        id=order.id,
        order_number=order.order_number,
        customer_name=customer_name,
        status=order.status,
        promised_date=order.promised_date,
        days_overdue=max(0, overdue) if order.status in OPEN_STATUSES else 0,
        total_amount=order.total_amount,
        has_open_exception=flagged,
        evidence_id=format_ref(EvidenceKind.order, order.id),
    )


def list_orders(
    session: Session,
    repo: TenantScopedRepository,
    *,
    as_of: date,
    overdue_only: bool = False,
    flagged_only: bool = False,
    search: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> list[OrderSummaryRow]:
    limit = max(1, min(limit, MAX_PAGE))
    stmt = _base_query(repo)
    if overdue_only:
        stmt = stmt.where(overdue_predicate(as_of))
    if flagged_only:
        stmt = stmt.where(Order.id.in_(_flagged_order_ids_stmt(repo)))
    if search and search.strip():
        # Escaped: a % or _ typed into the search box is a character, not a wildcard.
        term = search.strip()
        stmt = stmt.where(
            or_(
                Order.order_number.icontains(term, autoescape=True),
                Customer.name.icontains(term, autoescape=True),
            )
        )
    # Most overdue first when filtering; otherwise most recently promised first.
    stmt = stmt.order_by(Order.promised_date.asc() if overdue_only else Order.promised_date.desc())
    stmt = stmt.limit(limit).offset(offset)

    rows = session.execute(stmt).all()
    flagged_ids = set(session.scalars(_flagged_order_ids_stmt(repo)).all())
    return [_to_summary(order, name, as_of, order.id in flagged_ids) for order, name in rows]


def get_order_detail(
    session: Session, repo: TenantScopedRepository, order_id: uuid.UUID, *, as_of: date
) -> OrderDetailRow:
    """Full context for one order. Raises ``ResourceNotFound`` for another tenant's id."""
    order = repo.get_or_404(Order, order_id)
    customer = repo.get_or_404(Customer, order.customer_id)
    return _assemble_detail(session, repo, order, customer, as_of)


def get_order_by_number(
    session: Session, repo: TenantScopedRepository, order_number: str, *, as_of: date
) -> OrderDetailRow | None:
    """Resolve by the human-readable number *within this organization*.

    Order numbers collide across organizations on purpose. This function is the one
    the agent tool uses, so the tenant filter here is the difference between a correct
    answer and a cross-tenant disclosure.
    """
    order = repo.find_by(Order, order_number=order_number)
    if order is None:
        return None
    customer = repo.get_or_404(Customer, order.customer_id)
    return _assemble_detail(session, repo, order, customer, as_of)


def _assemble_detail(
    session: Session,
    repo: TenantScopedRepository,
    order: Order,
    customer: Customer,
    as_of: date,
) -> OrderDetailRow:
    lines = session.execute(
        repo.query(OrderLine)
        .where(OrderLine.order_id == order.id)
        .join(
            Product,
            and_(
                Product.id == OrderLine.product_id,
                Product.organization_id == OrderLine.organization_id,
            ),
        )
        .add_columns(Product.sku, Product.name.label("product_name"))
    ).all()

    shipments = list(
        session.scalars(repo.query(Shipment).where(Shipment.order_id == order.id)).all()
    )
    shipment_ids = [s.id for s in shipments]

    events: list[ShipmentEvent] = []
    if shipment_ids:
        events = list(
            session.scalars(
                repo.query(ShipmentEvent)
                .where(ShipmentEvent.shipment_id.in_(shipment_ids))
                .order_by(ShipmentEvent.occurred_at.asc())
            ).all()
        )

    flagged = any(s.status in (ShipmentStatus.delayed, ShipmentStatus.exception) for s in shipments)

    return OrderDetailRow(
        summary=_to_summary(order, customer.name, as_of, flagged),
        record_version=order.record_version,
        lines=[
            {
                "id": str(line.id),
                "sku": sku,
                "product_name": product_name,
                "quantity": line.quantity,
                "quantity_shipped": line.quantity_shipped,
                "unit_price": str(line.unit_price),
                # Surfaced explicitly: "incomplete shipment" is a seeded exception
                # class and the analyst should not have to do the subtraction.
                "outstanding": line.quantity - line.quantity_shipped,
            }
            for line, sku, product_name in lines
        ],
        shipments=[
            {
                "id": str(s.id),
                "evidence_id": format_ref(EvidenceKind.shipment, s.id),
                "carrier": s.carrier,
                "tracking_reference": s.tracking_reference,
                "status": s.status.value,
                "shipped_at": s.shipped_at.isoformat() if s.shipped_at else None,
                "estimated_delivery": s.estimated_delivery.isoformat()
                if s.estimated_delivery
                else None,
                "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
            }
            for s in shipments
        ],
        events=[
            EventRow(
                id=e.id,
                evidence_id=format_ref(EvidenceKind.event, e.id),
                event_type=e.event_type.value,
                occurred_at=e.occurred_at,
                # UNTRUSTED carrier text. Returned as data; the SPA renders it as text
                # and never as markup.
                note=e.note,
            )
            for e in events
        ],
    )


@dataclass(frozen=True, slots=True)
class OrderActivity:
    """What has happened around one order: investigations and simulated ERP tasks."""

    runs: list[dict[str, Any]]
    tasks: list[dict[str, Any]]


def order_activity(
    session: Session, repo: TenantScopedRepository, order_id: uuid.UUID, *, limit: int = 20
) -> OrderActivity:
    from opspilot.models import AgentRun, OpsTask

    runs = session.execute(
        repo.query(AgentRun)
        .with_only_columns(
            AgentRun.id, AgentRun.status, AgentRun.incomplete_reason, AgentRun.created_at
        )
        .where(AgentRun.target_order_id == order_id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    ).all()
    tasks = session.scalars(
        repo.query(OpsTask)
        .where(OpsTask.order_id == order_id)
        .order_by(OpsTask.created_at.desc())
        .limit(limit)
    ).all()
    return OrderActivity(
        runs=[
            {
                "id": run.id,
                "status": run.status.value,
                "incomplete_reason": run.incomplete_reason.value if run.incomplete_reason else None,
                "created_at": run.created_at,
            }
            for run in runs
        ],
        tasks=[
            {
                "id": task.id,
                "evidence_id": format_ref(EvidenceKind.task, task.id),
                "description": task.description,
                "status": task.status,
                "due_at": task.due_at,
                "created_at": task.created_at,
            }
            for task in tasks
        ],
    )


def count_orders(session: Session, repo: TenantScopedRepository, *, as_of: date) -> dict[str, int]:
    """Counts for the inbox header."""
    total = session.scalar(select(func.count()).select_from(repo.query(Order).subquery())) or 0
    overdue = (
        session.scalar(
            select(func.count()).select_from(
                repo.query(Order).where(overdue_predicate(as_of)).subquery()
            )
        )
        or 0
    )
    return {"total": total, "overdue": overdue}
