"""The investigation loop.

This is the whole agent. Explicit control flow, on purpose: the bounds, the tenancy,
the evidence discipline and the failure handling are the point of the project, and a
framework would hide all four.

Contract:

* at most ``max_turns`` model calls and ``max_tool_calls`` tool calls, retries included;
* every tool call is validated against its Pydantic model and executed with a
  repository bound to the caller's organization;
* every evidence id shown to the model is recorded as a grant;
* the final answer is parsed into ``InvestigationResult``; unsupported citations are
  stripped from what the user sees and counted before stripping (ADR 0010);
* hitting a bound produces a *useful incomplete result*, never an exception.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from opspilot.agent.providers.base import Message, ModelProvider, ProviderResult, ToolCall
from opspilot.agent.schemas import InvestigationResult
from opspilot.agent.tools.definitions import TOOL_ARGS, all_tool_schemas
from opspilot.core.config import Settings, get_settings
from opspilot.core.errors import ProviderUnavailable, QuotaExceeded, RequestInvalid
from opspilot.core.logging import get_logger
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.budget import RunBudget
from opspilot.domain.enums import IncompleteReason, RunStepType, ToolName
from opspilot.domain.evidence import unsupported
from opspilot.services import tools as tool_service

log = get_logger(__name__)

#: Allowlisted names, for labelling a step without trusting a model-supplied string.
TOOL_NAMES = frozenset(name.value for name in ToolName)


@dataclass(slots=True)
class RunContext:
    """Server-derived. Nothing here can be influenced by the model or the request.

    ``organization_id`` is resolved from the membership row and re-verified before the
    run is claimed (ADR 0002).
    """

    run_id: UUID
    organization_id: UUID
    membership_id: UUID
    organization_name: str
    request_text: str
    as_of: date


@dataclass(slots=True)
class StepRecord:
    """One row for ``run_steps``. Digests and metadata, never content."""

    ordinal: int
    step_type: RunStepType
    tool_name: str | None = None
    arguments_json: dict[str, Any] | None = None
    result_digest: str | None = None
    result_row_count: int | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error_code: str | None = None


@dataclass(slots=True)
class ValidationReport:
    """Pre-strip citation accounting. The citation-support gate reads these numbers.

    Computed *before* anything is removed, so stripping cannot inflate the metric
    (ADR 0010).
    """

    cited: int = 0
    granted: int = 0
    unsupported: list[str] = field(default_factory=list)
    findings_removed: int = 0
    citations_removed: int = 0

    @property
    def supported_ratio(self) -> float | None:
        """Share of cited ids actually granted. ``None`` when nothing was cited."""
        return None if self.cited == 0 else (self.cited - len(self.unsupported)) / self.cited

    def as_dict(self) -> dict[str, Any]:
        return {
            "cited": self.cited,
            "granted": self.granted,
            "unsupported": self.unsupported,
            "findings_removed": self.findings_removed,
            "citations_removed": self.citations_removed,
            "supported_ratio": self.supported_ratio,
        }


@dataclass(slots=True)
class RunOutcome:
    result: InvestigationResult | None
    incomplete_reason: IncompleteReason | None = None
    error_code: str | None = None
    validation: ValidationReport = field(default_factory=ValidationReport)
    steps: list[StepRecord] = field(default_factory=list)
    granted_evidence: list[str] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    model_name: str = ""


class ToolDispatcher:
    """Validates and executes tool calls. The only bridge from model output to data.

    Every call runs against a ``TenantScopedRepository`` built from
    ``RunContext.organization_id``. There is no code path by which a tool argument can
    change which organization is read.
    """

    def __init__(self, ctx: RunContext, session: Session, settings: Settings | None = None) -> None:
        self._ctx = ctx
        self._session = session
        # The run's settings, not whatever the process loaded: retrieval must embed
        # queries in the same model space the caller indexed with.
        self._settings = settings or get_settings()
        self._repo = TenantScopedRepository(session, ctx.organization_id)
        #: Everything the model has been shown, in order. Citation validation is a
        #: membership test against this set.
        self.granted_evidence: list[str] = []

    def execute(self, call: ToolCall, *, deadline_s: float | None = None) -> tuple[str, StepRecord]:
        """Return the JSON to hand back to the model, plus the step to persist.

        Two failure modes are *expected* and are returned to the model as a plain error
        so it can adapt within its remaining budget: an unknown tool name, and arguments
        that fail validation. Neither raises — a model that guesses badly should get a
        chance to correct itself, not crash the run.
        """
        started = time.monotonic()
        try:
            name = ToolName(call.name)
        except ValueError:
            log.warning("tool.unknown", run_id=str(self._ctx.run_id))
            return self._error("unknown tool", "unknown", "tool.unknown", started)

        try:
            args = TOOL_ARGS[name].model_validate(call.arguments)
        except ValidationError as exc:
            log.warning("tool.invalid_arguments", tool=name.value, errors=exc.error_count())
            return self._error("invalid arguments", name.value, "tool.invalid_args", started)

        try:
            result = self._dispatch(name, args, deadline_s=deadline_s)
        except RequestInvalid as exc:
            # Valid against the schema but not usable as given, e.g. a policy query
            # longer than the embedding model accepts. Correctable, so say how.
            return self._error(exc.message, name.value, "tool.invalid_args", started)
        except (ProviderUnavailable, QuotaExceeded) as exc:
            return self._error(
                "Policy retrieval is unavailable; do not infer a policy",
                name.value,
                exc.error_code,
                started,
            )

        # Grant every evidence id this payload exposed, before the model can cite it.
        for ref in result.evidence_ids:
            if ref not in self.granted_evidence:
                self.granted_evidence.append(ref)

        payload = json.dumps(result.payload, default=str)
        step = StepRecord(
            ordinal=0,  # assigned by the caller
            step_type=RunStepType.tool_call,
            tool_name=name.value,
            arguments_json=args.model_dump(mode="json"),
            result_digest=hashlib.sha256(payload.encode()).hexdigest(),
            result_row_count=result.row_count,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return payload, step

    def _dispatch(
        self, name: ToolName, args: object, *, deadline_s: float | None = None
    ) -> tool_service.ToolResult:
        as_of = self._ctx.as_of
        if name is ToolName.get_order_context:
            return tool_service.get_order_context(
                self._session,
                self._repo,
                order_number=args.order_number,  # type: ignore[attr-defined]
                as_of=as_of,
            )
        if name is ToolName.list_overdue_orders:
            return tool_service.list_overdue_orders(
                self._session,
                self._repo,
                as_of_date=args.as_of_date,  # type: ignore[attr-defined]
                limit=args.limit,  # type: ignore[attr-defined]
            )
        if name is ToolName.search_policies:
            return tool_service.search_policies(
                self._session,
                self._repo,
                query=args.query,  # type: ignore[attr-defined]
                order_context=args.order_context,  # type: ignore[attr-defined]
                as_of=as_of,
                settings=self._settings,
                deadline_s=deadline_s,
            )
        raise AssertionError(f"tool {name} is allowlisted but has no dispatch branch")

    def _error(self, message: str, tool: str, code: str, started: float) -> tuple[str, StepRecord]:
        payload = json.dumps({"error": message})
        return payload, StepRecord(
            ordinal=0,
            step_type=RunStepType.tool_call,
            tool_name=tool,
            result_digest=hashlib.sha256(payload.encode()).hexdigest(),
            latency_ms=int((time.monotonic() - started) * 1000),
            error_code=code,
        )


def validate_and_strip(
    result: InvestigationResult, granted: set[str]
) -> tuple[InvestigationResult, ValidationReport]:
    """Remove claims the run was not shown evidence for, counting first (ADR 0010).

    The report is computed on the original object; stripping happens afterwards on a
    copy. That ordering is what keeps the citation-support gate honest — the metric
    reads what the model produced, not what survived.
    """
    cited = result.cited_evidence_ids()
    bad = unsupported(cited, granted)
    report = ValidationReport(cited=len(cited), granted=len(granted), unsupported=sorted(bad))

    if not bad:
        return result, report

    kept_findings = [f for f in result.findings if not (set(f.evidence_ids) & bad)]
    kept_citations = [c for c in result.policy_citations if c.citation_id not in bad]
    report.findings_removed = len(result.findings) - len(kept_findings)
    report.citations_removed = len(result.policy_citations) - len(kept_citations)

    cleaned = result.model_copy(
        update={"findings": kept_findings, "policy_citations": kept_citations}
    )
    return cleaned, report


def run_investigation(
    *,
    ctx: RunContext,
    provider: ModelProvider,
    dispatcher: ToolDispatcher,
    budget: RunBudget,
    system_prompt: str,
    max_output_tokens: int,
    per_call_deadline: float,
    stop_requested: Callable[[], bool] | None = None,
) -> RunOutcome:
    started = time.monotonic()
    messages: list[Message] = [
        Message(role="system", content=system_prompt),
        # The user's text is untrusted data. It goes in a user turn and is never
        # concatenated into the system prompt.
        Message(role="user", content=ctx.request_text),
    ]
    tools = all_tool_schemas()
    steps: list[StepRecord] = []
    model_name = getattr(provider, "model", provider.name)

    def finish(
        result: InvestigationResult | None,
        reason: IncompleteReason | None,
        validation: ValidationReport,
        error_code: str | None = None,
    ) -> RunOutcome:
        return RunOutcome(
            result=result,
            incomplete_reason=reason,
            error_code=error_code,
            validation=validation,
            steps=steps,
            granted_evidence=list(dispatcher.granted_evidence),
            turns=budget.turns_used,
            tool_calls=budget.tool_calls_used,
            input_tokens=budget.input_tokens_used,
            output_tokens=budget.output_tokens_used,
            duration_ms=int((time.monotonic() - started) * 1000),
            model_name=model_name,
        )

    from opspilot.agent.prompts import FINAL_TURN_PROMPT

    final_prompt_sent = False
    #: Requested lookups the tool bound refused. An answer formed without them is
    #: useful, and incomplete.
    lookups_cut_short = False
    while True:
        if stop_requested and stop_requested():
            return finish(None, IncompleteReason.cancelled, ValidationReport())
        # Include failed calls and local work in the hard wall-clock deadline.
        budget.elapsed_seconds = max(budget.elapsed_seconds, time.monotonic() - started)
        stop = budget.check()
        lookups_spent = stop is IncompleteReason.tool_limit
        if stop is not None and (not lookups_spent or budget.check(synthesis=True) is not None):
            return finish(None, stop, ValidationReport())

        # Reserve the last available call for synthesis. Otherwise a model can
        # spend all three calls requesting evidence and never produce an answer.
        # Spent lookups with a call to spare lead to the same tool-free answer.
        final_turn = lookups_spent or budget.turns_used == budget.max_turns - 1
        final_reason = IncompleteReason.tool_limit if lookups_spent else IncompleteReason.turn_limit
        if final_turn and not final_prompt_sent:
            messages.append(Message(role="system", content=FINAL_TURN_PROMPT))
            final_prompt_sent = True
        call_started = time.monotonic()
        try:
            reply: ProviderResult = provider.complete(
                messages=messages,
                tools=[] if final_turn else tools,
                deadline_s=budget.call_deadline(per_call_deadline),
                max_output_tokens=max_output_tokens,
            )
        except ProviderUnavailable:
            # One bounded retry, and the retry consumes a turn — the plan is explicit
            # that retries count toward the limits.
            budget.record_retry()
            steps.append(
                StepRecord(
                    ordinal=len(steps),
                    step_type=RunStepType.model_call,
                    latency_ms=int((time.monotonic() - call_started) * 1000),
                    error_code="provider.unavailable",
                )
            )
            if budget.check(synthesis=final_turn) is not None:
                return finish(None, IncompleteReason.provider_unavailable, ValidationReport())
            continue

        elapsed = time.monotonic() - call_started
        budget.record_turn(reply.usage.input_tokens, reply.usage.output_tokens, elapsed)
        steps.append(
            StepRecord(
                ordinal=len(steps),
                step_type=RunStepType.model_call,
                latency_ms=reply.latency_ms or int(elapsed * 1000),
                tokens_in=reply.usage.input_tokens,
                tokens_out=reply.usage.output_tokens,
            )
        )
        model_name = reply.model or model_name

        # Account for the completed call, but never dispatch its tools or persist a
        # proposal after a cancellation/revocation observed during that call.
        if stop_requested and stop_requested():
            return finish(None, IncompleteReason.cancelled, ValidationReport())

        if reply.wants_tools and final_turn:
            # No tools were offered, so none run. Text alongside the calls may still
            # be the answer; without any, the run ends at the bound that forced this.
            if not (reply.text or "").strip():
                return finish(None, final_reason, ValidationReport())
        elif reply.wants_tools:
            messages.append(
                Message(role="assistant", content=reply.text or "", tool_calls=reply.tool_calls)
            )
            for call in reply.tool_calls:
                if stop_requested and stop_requested():
                    return finish(None, IncompleteReason.cancelled, ValidationReport())
                bound = budget.check()
                if bound is not None:
                    # Answer every requested call, run or not: OpenAI-format APIs
                    # reject a conversation with an unanswered call, and the trace
                    # should show what the bound cut.
                    lookups_cut_short = True
                    payload = json.dumps({"error": "Not run: the lookup budget is spent."})
                    steps.append(
                        StepRecord(
                            ordinal=len(steps),
                            step_type=RunStepType.tool_call,
                            tool_name=call.name if call.name in TOOL_NAMES else "unknown",
                            error_code=f"run.{bound.value}",
                        )
                    )
                else:
                    payload, step = dispatcher.execute(
                        call, deadline_s=budget.call_deadline(per_call_deadline)
                    )
                    step.ordinal = len(steps)
                    steps.append(step)
                    budget.record_tool_call(step.latency_ms / 1000)
                messages.append(
                    Message(role="tool", content=payload, tool_call_id=call.id, name=call.name)
                )
            continue

        # A final answer. Parse strictly: a schema violation is a failed run, not a
        # best-effort rescue of something we cannot interpret.
        try:
            parsed = InvestigationResult.model_validate_json(reply.text or "{}")
        except ValidationError as exc:
            log.warning("run.malformed_output", run_id=str(ctx.run_id), errors=exc.error_count())
            steps.append(
                StepRecord(
                    ordinal=len(steps),
                    step_type=RunStepType.validation,
                    error_code="provider.invalid_output",
                )
            )
            return finish(None, None, ValidationReport(), error_code="provider.invalid_output")

        cleaned, report = validate_and_strip(parsed, set(dispatcher.granted_evidence))
        steps.append(
            StepRecord(
                ordinal=len(steps),
                step_type=RunStepType.validation,
                result_row_count=report.cited,
                error_code="provider.unsupported_citation" if report.unsupported else None,
            )
        )
        if report.unsupported:
            log.warning(
                "run.unsupported_citations",
                run_id=str(ctx.run_id),
                cited=report.cited,
                unsupported=len(report.unsupported),
            )

        # A stripped answer is incomplete, and the UI must say so rather than quietly
        # present a shorter one. So is one formed without lookups it asked for.
        reason = (
            IncompleteReason.insufficient_evidence
            if report.unsupported
            else IncompleteReason.tool_limit
            if lookups_cut_short
            else None
        )
        return finish(cleaned, reason, report)
