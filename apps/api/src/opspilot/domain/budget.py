"""Run budget accounting — pure, so the bounds can be reasoned about in isolation.

Every limit in the plan is counted here, including retries. The orchestrator asks
``check()`` before each turn and each tool call; the answer is either ``None`` (keep
going) or the reason to stop, which becomes ``agent_runs.incomplete_reason``.

A stopped run is not a failed run. It returns what it established.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from opspilot.domain.enums import IncompleteReason


@dataclass(slots=True)
class RunBudget:
    max_turns: int
    max_tool_calls: int
    deadline_seconds: float
    max_input_tokens: int
    max_output_tokens: int

    turns_used: int = 0
    tool_calls_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    elapsed_seconds: float = 0.0
    retries_used: int = 0
    cancelled: bool = False
    _stopped_for: IncompleteReason | None = field(default=None, repr=False)

    # -- consumption -------------------------------------------------------
    def record_turn(self, input_tokens: int, output_tokens: int, elapsed: float) -> None:
        self.turns_used += 1
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens
        self.elapsed_seconds += elapsed

    def record_tool_call(self, elapsed: float) -> None:
        self.tool_calls_used += 1
        self.elapsed_seconds += elapsed

    def record_retry(self) -> None:
        """A retry consumes a turn. The plan is explicit: retries count toward limits."""
        self.retries_used += 1
        self.turns_used += 1

    # -- gates -------------------------------------------------------------
    def check(self, *, synthesis: bool = False) -> IncompleteReason | None:
        """The reason to stop, or ``None`` to continue. Order matters: report the
        bound that was actually hit first, because that is what gets tuned.

        ``synthesis=True`` asks whether one tool-free call for the final answer is
        still allowed. That call needs a turn, time and tokens, but no lookups, so
        a spent tool budget does not forbid it.
        """
        if self.cancelled:
            return IncompleteReason.cancelled
        if self.elapsed_seconds >= self.deadline_seconds:
            return IncompleteReason.deadline
        if self.turns_used >= self.max_turns:
            return IncompleteReason.turn_limit
        if not synthesis and self.tool_calls_used >= self.max_tool_calls:
            return IncompleteReason.tool_limit
        if (
            self.input_tokens_used >= self.max_input_tokens
            or self.output_tokens_used >= self.max_output_tokens
        ):
            return IncompleteReason.budget_exhausted
        return None

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_seconds - self.elapsed_seconds)

    def call_deadline(self, per_call_limit: float) -> float:
        """Never give a provider call more time than the run has left."""
        return max(1.0, min(per_call_limit, self.remaining_seconds))
