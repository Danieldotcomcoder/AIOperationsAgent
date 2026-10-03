import json
import uuid
from datetime import date

from opspilot.agent.orchestrator import RunContext, StepRecord, run_investigation
from opspilot.agent.providers.base import ProviderResult, ToolCall, Usage
from opspilot.domain.budget import RunBudget
from opspilot.domain.enums import RunStepType


def test_last_call_requests_answer_without_tools():
    class Dispatcher:
        granted_evidence = frozenset()

        def execute(self, call, **kwargs):
            return "{}", StepRecord(ordinal=0, step_type=RunStepType.tool_call)

    class Provider:
        name = "test"
        calls = 0

        def complete(self, *, messages, tools, **kwargs):
            self.calls += 1
            if self.calls < 3:
                assert tools
                return ProviderResult(
                    text=None,
                    tool_calls=[ToolCall(str(self.calls), "get_order_context", {})],
                    usage=Usage(1, 1),
                    finish_reason="tool_calls",
                    latency_ms=1,
                    model="test",
                )
            assert not tools
            assert "final model call" in messages[-1].content
            return ProviderResult(
                text=json.dumps(
                    {
                        "summary": "Insufficient evidence to determine the cause.",
                        "findings": [],
                        "policy_citations": [],
                        "missing_information": ["Shipment records"],
                        "proposed_action": None,
                    }
                ),
                tool_calls=[],
                usage=Usage(1, 1),
                finish_reason="stop",
                latency_ms=1,
                model="test",
            )

    provider = Provider()
    outcome = run_investigation(
        ctx=RunContext(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "Test", "Investigate", date(2026, 9, 25)
        ),
        provider=provider,
        dispatcher=Dispatcher(),
        budget=RunBudget(3, 6, 90, 20000, 3000),
        system_prompt="Test",
        max_output_tokens=3000,
        per_call_deadline=25,
    )
    assert provider.calls == 3
    assert outcome.result is not None
    assert outcome.result.missing_information == ["Shipment records"]
    assert outcome.error_code is None


def test_spent_lookups_still_leave_a_call_for_the_answer():
    """Parallel calls can exhaust the lookup budget with a model turn to spare."""

    class Dispatcher:
        granted_evidence = frozenset()

        def __init__(self):
            self.executed = []

        def execute(self, call, **kwargs):
            self.executed.append(call.id)
            return "{}", StepRecord(ordinal=0, step_type=RunStepType.tool_call)

    class Provider:
        name = "test"
        calls = 0

        def complete(self, *, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                assert tools
                return ProviderResult(
                    text=None,
                    tool_calls=[ToolCall(f"c{i}", "get_order_context", {}) for i in range(4)],
                    usage=Usage(1, 1),
                    model="test",
                )
            # Every requested call must be answered, executed or not, or an
            # OpenAI-format API rejects the conversation.
            answered = {m.tool_call_id for m in messages if m.role == "tool"}
            assert answered == {"c0", "c1", "c2", "c3"}
            assert not tools
            return ProviderResult(
                text=json.dumps({"summary": "Partial.", "missing_information": ["Lookups"]}),
                usage=Usage(1, 1),
                model="test",
            )

    provider, dispatcher = Provider(), Dispatcher()
    outcome = run_investigation(
        ctx=RunContext(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "Test", "Investigate", date(2026, 9, 25)
        ),
        provider=provider,
        dispatcher=dispatcher,
        budget=RunBudget(3, 3, 90, 20000, 3000),
        system_prompt="Test",
        max_output_tokens=3000,
        per_call_deadline=25,
    )
    assert dispatcher.executed == ["c0", "c1", "c2"]
    assert provider.calls == 2
    assert outcome.result is not None
    # An answer formed without every lookup it asked for is useful and incomplete.
    assert outcome.incomplete_reason == "tool_limit"
    skipped = [s for s in outcome.steps if s.error_code == "run.tool_limit"]
    assert len(skipped) == 1 and outcome.tool_calls == 3


def test_tool_calls_on_the_final_turn_are_not_dispatched():
    class Dispatcher:
        granted_evidence = frozenset()

        def execute(self, call, **kwargs):
            raise AssertionError("a tool that was not offered must not run")

    class Provider:
        name = "test"
        calls = 0

        def complete(self, *, messages, tools, **kwargs):
            self.calls += 1
            return ProviderResult(
                text=None,
                tool_calls=[ToolCall("late", "get_order_context", {})],
                usage=Usage(1, 1),
                model="test",
            )

    outcome = run_investigation(
        ctx=RunContext(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "Test", "Investigate", date(2026, 9, 25)
        ),
        provider=Provider(),
        dispatcher=Dispatcher(),
        budget=RunBudget(1, 6, 90, 20000, 3000),
        system_prompt="Test",
        max_output_tokens=3000,
        per_call_deadline=25,
    )
    assert outcome.result is None
    assert outcome.incomplete_reason == "turn_limit"


def test_synthesis_check_ignores_only_the_lookup_bound():
    from opspilot.domain.enums import IncompleteReason

    budget = RunBudget(3, 2, 90, 20000, 3000, turns_used=1, tool_calls_used=2)
    assert budget.check() is IncompleteReason.tool_limit
    assert budget.check(synthesis=True) is None
    budget.turns_used = 3
    assert budget.check(synthesis=True) is IncompleteReason.turn_limit
    budget.turns_used, budget.output_tokens_used = 1, 3000
    assert budget.check(synthesis=True) is IncompleteReason.budget_exhausted
