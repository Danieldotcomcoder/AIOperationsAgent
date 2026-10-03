"""Approval inbox — the only path to a write.

The approve request carries the ``payload_hash`` the UI rendered. That is not
ceremony: it is the mechanism that makes "the supervisor approved *this*" verifiable
rather than assumed (ADR 0004). A mismatch is 409, never a silent re-read.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from opspilot.auth.dependencies import CurrentPrincipal, DbSession, RequireSupervisor
from opspilot.core.config import get_settings
from opspilot.domain.enums import ActionStatus, ActionType
from opspilot.services import actions as service

router = APIRouter()


class ActionView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    action_type: ActionType
    status: ActionStatus
    #: Exactly what will execute. The UI renders this and nothing else.
    payload: dict[str, Any]
    payload_hash: str
    source_record_type: str
    source_record_id: uuid.UUID
    source_record_version: int
    #: True when the source record moved since the proposal was created — the UI must
    #: show this prominently and disable approval.
    source_changed: bool
    expires_at: datetime
    created_at: datetime
    payload_valid: bool
    decision_expired: bool
    downstream_task_id: str | None
    execution_error: str | None
    #: Who the task will be assigned to: the analyst who ran the investigation.
    assignee_name: str | None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The hash the client displayed. Required; there is no "approve latest" path.
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: str | None = Field(default=None, max_length=1000)


@router.get("")
def list_actions(
    principal: CurrentPrincipal,
    session: DbSession,
    status_filter: ActionStatus | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[ActionView]:
    """Analysts may read the queue; only supervisors may decide on it."""
    return [
        ActionView(**row)
        for row in service.list_actions(
            session, principal, status_filter=status_filter, limit=limit
        )
    ]


@router.get("/{action_id}")
def get_action(action_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> ActionView:
    return ActionView(**service.get_action(session, principal, action_id))


@router.post("/{action_id}/approve")
def approve(
    action_id: uuid.UUID,
    body: DecisionRequest,
    principal: RequireSupervisor,
    session: DbSession,
) -> ActionView:
    """One transaction: re-read FOR UPDATE, evaluate ``domain.approval``, write the
    approval row, flip the status, insert the outbox event. Commit.

    The Celery message is published by the dispatcher from the outbox — never from
    inside this request (ADR 0003).
    """
    return ActionView(
        **service.decide(
            session,
            get_settings(),
            principal,
            action_id,
            body.payload_hash,
            body.comment,
            approve=True,
        )
    )


@router.post("/{action_id}/reject")
def reject(
    action_id: uuid.UUID,
    body: DecisionRequest,
    principal: RequireSupervisor,
    session: DbSession,
) -> ActionView:
    """Rejection is terminal and produces no outbox event and no downstream effect."""
    return ActionView(
        **service.decide(
            session,
            get_settings(),
            principal,
            action_id,
            body.payload_hash,
            body.comment,
            approve=False,
        )
    )
