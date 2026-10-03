"""Internal Operations API — SIMULATED ERP BOUNDARY.

This is ours, and it is labelled as simulated everywhere it appears in the product and
the README. It exists so that idempotency, duplicate delivery and lost responses are
exercised as real HTTP with real failure modes, instead of a function call that cannot
plausibly half-happen.

Contract the execute worker depends on:

* ``POST /ops/tasks`` with an ``Idempotency-Key`` header creates at most one task per
  key and returns ``200`` with the *existing* task for a repeat, ``201`` for a new one;
* ``GET /ops/tasks?idempotency_key=...`` answers "did this ever happen?" — the
  reconciler's only question;
* authentication is a service token, never a user token. A human's credentials can
  never reach this boundary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Response, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from opspilot.auth.dependencies import DbSession
from opspilot.core.config import get_settings
from opspilot.core.errors import ConflictError, NotAuthenticated, RequestInvalid, WritesDisabled
from opspilot.core.ids import new_id
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import MembershipStatus
from opspilot.models import Membership, OpsTask, Order
from opspilot.ops.contracts import CreateTaskRequest, TaskResponse

ops_router = APIRouter()


def _require_service_token(authorization: str | None) -> None:
    """Constant-time comparison against the configured service token."""
    import secrets

    from opspilot.core.config import get_settings

    expected = get_settings().ops_api_service_token.get_secret_value()
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not presented or not secrets.compare_digest(presented, expected):
        raise NotAuthenticated("ops api requires a service token")


@ops_router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(
    body: CreateTaskRequest,
    session: DbSession,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", pattern=r"^[0-9a-f]{64}$")],
    authorization: Annotated[str | None, Header()] = None,
) -> TaskResponse:
    """Insert with ``ON CONFLICT (idempotency_key) DO NOTHING``, then read the row back.

    Doing it in that order is what makes two concurrent deliveries safe: the loser of
    the race reads the winner's row and returns the same ``task_id`` with 200. Never
    check-then-insert — the gap between the two is exactly where the duplicate is born.
    """
    _require_service_token(authorization)
    if not get_settings().feature_writes_enabled:
        raise WritesDisabled("Operational writes are disabled")
    repo = TenantScopedRepository(session, body.organization_id)
    repo.get_or_404(Order, body.order_id)
    assignee = repo.get_or_404(Membership, body.assignee_id)
    if assignee.status != MembershipStatus.active:
        raise RequestInvalid("Task assignee is inactive")
    inserted = session.scalar(
        insert(OpsTask)
        .values(
            id=new_id(),
            idempotency_key=idempotency_key,
            **body.model_dump(),
            status="open",
        )
        .on_conflict_do_nothing(index_elements=[OpsTask.idempotency_key])
        .returning(OpsTask.id)
    )
    task = repo.find_by(OpsTask, idempotency_key=idempotency_key)
    if task is None:
        raise ConflictError("Idempotency key is already in use")
    if (
        task.order_id != body.order_id
        or task.assignee_id != body.assignee_id
        or task.due_at != body.due_at
        or task.description != body.description
    ):
        raise ConflictError("Idempotency key is bound to a different task payload")
    response.status_code = 201 if inserted else 200
    return _view(task, deduplicated=inserted is None)


@ops_router.get("/tasks")
def lookup_task(
    session: DbSession,
    idempotency_key: str,
    authorization: Annotated[str | None, Header()] = None,
) -> TaskResponse | None:
    """The reconciler's question: did this key ever produce a task?

    A definitive ``null`` means it is safe to retry with the same key. An error here is
    *not* a null — the caller must keep the action in ``reconciliation_required``.
    """
    _require_service_token(authorization)
    # A service-token-only lookup across organizations is the reconciliation contract.
    task = session.scalar(select(OpsTask).where(OpsTask.idempotency_key == idempotency_key))
    return _view(task, deduplicated=True) if task else None


def _view(task: OpsTask, *, deduplicated: bool) -> TaskResponse:
    return TaskResponse(
        task_id=str(task.id),
        organization_id=task.organization_id,
        order_id=task.order_id,
        idempotency_key=task.idempotency_key,
        status=task.status,
        created_at=task.created_at,
        deduplicated=deduplicated,
    )
