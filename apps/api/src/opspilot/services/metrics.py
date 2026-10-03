"""Run, approval and execution metrics for one organization (ARCHITECTURE §9).

The database-backed dashboard, instead of a hosted monitoring stack. Every number is
computed from rows the caller's organization owns, through the tenant repository, over
a bounded window. Deployment-wide spend counters are deliberately absent: they include
other tenants' activity.

Percentiles are nearest-rank over the rows in the window. At pilot scale that is a few
hundred values, so they are exact and cheap; past ``MAX_RUNS`` the response says it was
truncated rather than quietly sampling.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from opspilot.auth.principal import Principal
from opspilot.core.clock import utcnow
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import (
    ActionStatus,
    ApprovalDecision,
    OperationTaskStatus,
    RunStatus,
    RunStepType,
)
from opspilot.models import AgentRun, Approval, Feedback, OperationTask, ProposedAction, RunStep

#: Ceiling on runs aggregated by one request, whatever the window.
MAX_RUNS = 5000
MAX_DAYS = 90


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (``q`` in 0-100); 0 when there is nothing to rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summary(session: Session, principal: Principal, *, days: int) -> dict[str, Any]:
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")
    repo = TenantScopedRepository(session, principal.organization_id)
    now = utcnow()
    since = now - timedelta(days=days)

    runs = session.execute(
        repo.query(AgentRun)
        .with_only_columns(
            AgentRun.id,
            AgentRun.status,
            AgentRun.incomplete_reason,
            AgentRun.error_code,
            AgentRun.cost_usd,
            AgentRun.duration_ms,
            AgentRun.created_at,
            AgentRun.started_at,
            AgentRun.tokens_in,
            AgentRun.tokens_out,
            AgentRun.retry_count,
            AgentRun.tool_call_count,
            AgentRun.provider,
            AgentRun.model_name,
            AgentRun.prompt_version,
            AgentRun.result_json["validation"].label("validation"),
        )
        .where(AgentRun.created_at >= since)
        .order_by(AgentRun.created_at.desc())
        .limit(MAX_RUNS + 1)
    ).all()
    truncated = len(runs) > MAX_RUNS
    runs = runs[:MAX_RUNS]

    statuses = Counter(run.status for run in runs)
    in_flight = (RunStatus.queued, RunStatus.investigating)
    finished = [run for run in runs if run.status not in in_flight]
    costs = [float(run.cost_usd or Decimal(0)) for run in finished]
    durations = [float(run.duration_ms) for run in finished if run.duration_ms is not None]
    queue_waits = [
        (run.started_at - run.created_at).total_seconds() * 1000
        for run in runs
        if run.started_at is not None
    ]

    cited = unsupported = with_unsupported = 0
    for run in finished:
        validation = run.validation or {}
        cited += int(validation.get("cited") or 0)
        bad = len(validation.get("unsupported") or [])
        unsupported += bad
        with_unsupported += int(bad > 0)

    window_runs = (
        repo.query(AgentRun).with_only_columns(AgentRun.id).where(AgentRun.created_at >= since)
    )
    steps = session.execute(
        repo.query(RunStep)
        .with_only_columns(
            RunStep.step_type, RunStep.tool_name, RunStep.latency_ms, RunStep.error_code
        )
        .where(RunStep.run_id.in_(window_runs))
    ).all()
    model_latency = [
        float(step.latency_ms)
        for step in steps
        if step.step_type == RunStepType.model_call and step.error_code is None
    ]
    tool_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "error": 0})
    tool_errors: Counter[str] = Counter()
    for step in steps:
        if step.step_type != RunStepType.tool_call:
            continue
        tool_outcomes[step.tool_name or "unknown"]["error" if step.error_code else "ok"] += 1
        if step.error_code:
            tool_errors[step.error_code] += 1

    proposals = Counter(
        session.scalars(
            repo.query(ProposedAction)
            .with_only_columns(ProposedAction.status)
            .where(ProposedAction.created_at >= since)
        ).all()
    )
    decisions = Counter(
        session.scalars(
            repo.query(Approval)
            .with_only_columns(Approval.decision)
            .where(Approval.decided_at >= since)
        ).all()
    )
    approved = decisions[ApprovalDecision.approved]
    rejected = decisions[ApprovalDecision.rejected]
    executions = Counter(
        session.scalars(
            repo.query(OperationTask)
            .with_only_columns(OperationTask.status)
            .where(OperationTask.created_at >= since)
        ).all()
    )
    # Not windowed: an uncertain write is an open incident until it is resolved.
    uncertain = len(
        session.scalars(
            repo.query(ProposedAction)
            .with_only_columns(ProposedAction.id)
            .where(ProposedAction.status == ActionStatus.reconciliation_required)
        ).all()
    )
    feedback = Counter(
        session.scalars(
            repo.query(Feedback)
            .with_only_columns(Feedback.usefulness)
            .where(Feedback.created_at >= since)
        ).all()
    )

    by_model: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for run in finished:
        by_model[(run.provider, run.model_name, run.prompt_version)].append(run)

    return {
        "window_from": since.date(),
        "window_to": now.date(),
        "days": days,
        "truncated": truncated,
        "run_count": len(runs),
        "completed": statuses[RunStatus.completed],
        "needs_review": statuses[RunStatus.needs_review],
        "failed": statuses[RunStatus.failed],
        "cancelled": statuses[RunStatus.cancelled],
        "in_flight": statuses[RunStatus.queued] + statuses[RunStatus.investigating],
        "incomplete": sum(1 for run in runs if run.incomplete_reason is not None),
        "by_incomplete_reason": dict(
            Counter(run.incomplete_reason.value for run in runs if run.incomplete_reason)
        ),
        "by_error_code": dict(Counter(run.error_code for run in runs if run.error_code)),
        "mean_cost_usd": _mean(costs),
        "p95_cost_usd": percentile(costs, 95),
        "total_cost_usd": sum(costs),
        "mean_duration_ms": int(_mean(durations)),
        "p95_duration_ms": int(percentile(durations, 95)),
        "mean_provider_latency_ms": int(_mean(model_latency)),
        "p95_provider_latency_ms": int(percentile(model_latency, 95)),
        "p95_queue_wait_ms": int(percentile(queue_waits, 95)),
        "tokens_in": sum(run.tokens_in for run in runs),
        "tokens_out": sum(run.tokens_out for run in runs),
        "retries": sum(run.retry_count for run in runs),
        "tool_calls": sum(run.tool_call_count for run in runs),
        "tool_outcomes": dict(tool_outcomes),
        "tool_errors": dict(tool_errors),
        "citations_cited": cited,
        "citations_unsupported": unsupported,
        "citation_support_ratio": (cited - unsupported) / cited if cited else None,
        "runs_with_unsupported_citations": with_unsupported,
        "approval_rate": approved / (approved + rejected) if approved + rejected else None,
        "proposals_created": sum(proposals.values()),
        "proposals_pending": proposals[ActionStatus.pending],
        "proposals_approved": approved,
        "proposals_rejected": rejected,
        "proposals_expired": proposals[ActionStatus.expired],
        "executions_succeeded": executions[OperationTaskStatus.succeeded],
        "executions_failed": executions[OperationTaskStatus.failed],
        "reconciliation_required": uncertain,
        "feedback": {usefulness.value: count for usefulness, count in feedback.items()},
        "by_model": [
            {
                "provider": provider,
                "model_name": model,
                "prompt_version": prompt,
                "runs": len(rows),
                "mean_cost_usd": _mean([float(row.cost_usd or 0) for row in rows]),
                "p95_duration_ms": int(
                    percentile([float(r.duration_ms) for r in rows if r.duration_ms], 95)
                ),
            }
            for (provider, model, prompt), rows in sorted(by_model.items())
        ],
    }
