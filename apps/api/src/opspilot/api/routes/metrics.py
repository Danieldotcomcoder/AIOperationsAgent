"""Database-backed metrics.

This is the observability story (ADR-adjacent, see ARCHITECTURE §9): rather than
hosting a metrics stack on a 4 GB VPS, run accounting is written to Postgres during
every run and aggregated here. It is what the run-history screen and the release-gate
report both read, so the demo and the evaluation cannot disagree.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel

from opspilot.auth.dependencies import CurrentPrincipal, DbSession
from opspilot.services import metrics as service

router = APIRouter()


class ToolOutcome(BaseModel):
    ok: int
    error: int


class ModelBreakdown(BaseModel):
    """Results are attributable only to (provider, model, prompt version) together."""

    provider: str
    model_name: str
    prompt_version: str
    runs: int
    mean_cost_usd: float
    p95_duration_ms: int


class MetricsSummary(BaseModel):
    window_from: date
    window_to: date
    days: int
    #: True when the window held more runs than one request aggregates.
    truncated: bool
    run_count: int
    completed: int
    needs_review: int
    failed: int
    cancelled: int
    in_flight: int
    #: Distinct from failed: a bound was hit and the run still returned something useful.
    incomplete: int
    by_incomplete_reason: dict[str, int]
    by_error_code: dict[str, int]

    #: Estimated from a price table, not billed truth; free routes are zero.
    mean_cost_usd: float
    p95_cost_usd: float
    total_cost_usd: float
    #: Provider latency and queue wait are reported separately — a slow queue and a
    #: slow model need different fixes, and the gate names them separately.
    mean_duration_ms: int
    p95_duration_ms: int
    mean_provider_latency_ms: int
    p95_provider_latency_ms: int
    p95_queue_wait_ms: int
    tokens_in: int
    tokens_out: int
    retries: int
    tool_calls: int
    tool_outcomes: dict[str, ToolOutcome]
    tool_errors: dict[str, int]

    #: Pre-strip citation accounting (ADR 0010): what the model cited, not what survived.
    citations_cited: int
    citations_unsupported: int
    citation_support_ratio: float | None
    runs_with_unsupported_citations: int

    #: Approved / (approved + rejected) for decisions in the window; null without any.
    approval_rate: float | None
    proposals_created: int
    proposals_pending: int
    proposals_approved: int
    proposals_rejected: int
    proposals_expired: int
    executions_succeeded: int
    executions_failed: int
    #: Should be zero. Anything else is an incident, not a metric.
    reconciliation_required: int
    feedback: dict[str, int]
    by_model: list[ModelBreakdown]


@router.get("/summary")
def summary(
    principal: CurrentPrincipal,
    session: DbSession,
    days: int = Query(default=30, ge=1, le=service.MAX_DAYS),
) -> MetricsSummary:
    """Analysts and supervisors see their own organization's numbers, nobody else's."""
    return MetricsSummary(**service.summary(session, principal, days=days))
