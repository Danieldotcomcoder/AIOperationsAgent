"""Investigation runs.

Runs are asynchronous: ``POST /runs`` returns 202 with an id and the client polls.
Streaming is out of scope for v1 — polling is enough for a 30-second workflow and it
keeps the failure model simple.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from opspilot.agent.schemas import InvestigationResult
from opspilot.auth.dependencies import CurrentPrincipal, DbSession
from opspilot.auth.principal import Principal
from opspilot.core.config import Settings, get_settings
from opspilot.domain.enums import ActionStatus, IncompleteReason, Role, RunStatus, Usefulness
from opspilot.models import AgentRun
from opspilot.services import runs as run_service

router = APIRouter()


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: UNTRUSTED. Data for the model; never interpolated into the system prompt.
    request_text: str = Field(min_length=3, max_length=1000)
    order_number: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9\-_]*$")


class RunSummary(BaseModel):
    id: uuid.UUID
    status: RunStatus
    incomplete_reason: IncompleteReason | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    #: Estimated from a price table, not billed truth.
    cost_usd: float
    tokens_in: int
    tokens_out: int
    turn_count: int
    tool_call_count: int
    retry_count: int
    request_text: str
    #: The order the server bound this run to, if any. Never model-supplied.
    order_id: uuid.UUID | None
    order_number: str | None
    #: What actually produced the answer — evaluation results are keyed on these.
    provider: str
    model_name: str
    prompt_version: str
    initiated_by_name: str | None
    initiated_by_me: bool
    has_proposal: bool
    proposal_status: ActionStatus | None
    #: Usefulness ratings people gave this run, oldest first.
    feedback_ratings: list[Usefulness]
    can_cancel: bool
    cancel_requested_at: datetime | None
    trace_pruned_at: datetime | None


class RunStepView(BaseModel):
    ordinal: int
    step_type: str
    tool_name: str | None
    latency_ms: int
    tokens_in: int
    tokens_out: int
    result_row_count: int | None
    error_code: str | None


class ValidationView(BaseModel):
    """Pre-strip citation accounting (ADR 0010)."""

    cited: int
    granted: int
    unsupported: list[str]
    findings_removed: int
    citations_removed: int
    supported_ratio: float | None


class RunResultView(InvestigationResult):
    """The validated answer as persisted: the agent's schema plus its accounting."""

    model_config = ConfigDict(extra="ignore")

    validation: ValidationView | None = None


class FeedbackView(BaseModel):
    usefulness: Usefulness
    failure_class: str | None
    correction_text: str | None
    created_at: datetime
    author: str
    mine: bool


class RunDetail(RunSummary):
    #: Validated InvestigationResult plus the pre-strip citation accounting, or null
    #: while the run is in flight, failed, or after retention pruned it.
    result: RunResultView | None
    steps: list[RunStepView]
    #: The latest proposal, whatever its status — an expired one is still history.
    proposal_id: uuid.UUID | None
    feedback: list[FeedbackView]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usefulness: Usefulness
    correction_text: str | None = Field(default=None, max_length=2000)
    failure_class: str | None = Field(default=None, max_length=64)


def _summary(run: AgentRun, listing: run_service.RunListing, principal: Principal) -> RunSummary:
    proposal = listing.proposals.get(run.id)
    return RunSummary(
        id=run.id,
        status=run.status,
        incomplete_reason=run.incomplete_reason,
        error_code=run.error_code,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        cost_usd=float(run.cost_usd),
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        turn_count=run.turn_count,
        tool_call_count=run.tool_call_count,
        retry_count=run.retry_count,
        request_text=run.request_text,
        order_id=run.target_order_id,
        order_number=listing.order_numbers.get(run.target_order_id)
        if run.target_order_id
        else None,
        provider=run.provider,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        initiated_by_name=listing.names.get(run.initiated_by),
        initiated_by_me=run.initiated_by == principal.membership_id,
        has_proposal=proposal is not None,
        proposal_status=proposal.status if proposal else None,
        feedback_ratings=listing.feedback.get(run.id, []),
        can_cancel=run.status in {RunStatus.queued, RunStatus.investigating}
        and run.cancel_requested_at is None
        and (run.initiated_by == principal.membership_id or principal.role == Role.supervisor),
        cancel_requested_at=run.cancel_requested_at,
        trace_pruned_at=run.trace_pruned_at,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    body: CreateRunRequest, principal: CurrentPrincipal, session: DbSession
) -> RunSummary:
    """Reserve budget, persist the run as ``queued``, enqueue after commit."""
    settings: Settings = get_settings()
    created = run_service.create_run(
        session,
        settings,
        principal,
        request_text=body.request_text,
        order_number=body.order_number,
    )
    run = run_service.get_run(session, principal, created.id)
    return _summary(run, run_service.listing_context(session, principal, [run]), principal)


@router.get("")
def list_runs(
    principal: CurrentPrincipal,
    session: DbSession,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: RunStatus | None = None,
) -> list[RunSummary]:
    runs = run_service.list_runs(
        session, principal, limit=limit, offset=offset, status=status_filter
    )
    listing = run_service.listing_context(session, principal, runs)
    return [_summary(run, listing, principal) for run in runs]


@router.get("/{run_id}")
def get_run(run_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> RunDetail:
    """A run in another organization is a 404, never a 403."""
    run = run_service.get_run(session, principal, run_id)
    listing = run_service.listing_context(session, principal, [run])
    proposal = listing.proposals.get(run.id)
    steps = run_service.get_run_steps(session, principal, run_id)
    return RunDetail(
        **_summary(run, listing, principal).model_dump(),
        result=run.result_json,
        proposal_id=proposal.id if proposal else None,
        steps=[
            RunStepView(
                ordinal=s.ordinal,
                step_type=s.step_type.value,
                tool_name=s.tool_name,
                latency_ms=s.latency_ms,
                tokens_in=s.tokens_in,
                tokens_out=s.tokens_out,
                result_row_count=s.result_row_count,
                error_code=s.error_code,
            )
            for s in steps
        ],
        feedback=[
            FeedbackView(
                usefulness=row.usefulness,
                failure_class=row.failure_class,
                correction_text=row.correction_text,
                created_at=row.created_at,
                author=author,
                mine=row.membership_id == principal.membership_id,
            )
            for row, author in run_service.run_feedback(session, principal, run_id)
        ],
    )


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(run_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> RunSummary:
    """Cancels a queued run. An in-flight run stops at the orchestrator's next check."""
    run = run_service.cancel_run(session, principal, run_id)
    return _summary(run, run_service.listing_context(session, principal, [run]), principal)


@router.post("/{run_id}/feedback", status_code=status.HTTP_201_CREATED)
def submit_feedback(
    run_id: uuid.UUID, body: FeedbackRequest, principal: CurrentPrincipal, session: DbSession
) -> dict[str, str]:
    run_service.record_feedback(
        session,
        principal,
        run_id,
        usefulness=body.usefulness,
        correction_text=body.correction_text,
        failure_class=body.failure_class,
    )
    return {"status": "recorded"}
