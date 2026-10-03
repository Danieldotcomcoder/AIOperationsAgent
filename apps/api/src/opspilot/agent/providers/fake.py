"""Deterministic provider for development, CI and every deterministic evaluation case.

Not a stub. It walks the same loop a real provider drives — request tools, read the
results, produce a structured answer — so the orchestrator, the dispatcher, the
evidence grants and the validation path are all genuinely exercised with no network
and no spend.

It reads the evidence ids out of the tool results it was given, which means its
citations are *real* by default. That matters: a fake that invented ids would make the
validation path pass trivially and prove nothing.

Scenarios let a test ask for a specific failure. They are the point of this class:

    FakeProvider()                                  # well-behaved run
    FakeProvider(scenario="unsupported_citation")   # cites an id never granted
    FakeProvider(scenario="malformed_output")       # violates the response schema
    FakeProvider(scenario="unavailable")            # raises ProviderUnavailable
    FakeProvider(scenario="tool_loop")              # never stops calling tools
    FakeProvider(scenario="no_evidence")            # abstains, states what is missing
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from opspilot.agent.providers.base import Message, ProviderResult, ToolCall, Usage
from opspilot.core.errors import ProviderUnavailable

#: Matches the evidence ids the tools embed in their payloads.
_EVIDENCE = re.compile(r"\b(?:ord|shp|evt|tsk|pol):[0-9a-fA-F-]{36}(?:#\d+)?\b")

#: Deterministic id that no run will ever have been granted.
_UNGRANTED = "ord:00000000-0000-7000-8000-000000000000"


class FakeProvider:
    """Synchronous, deterministic, offline."""

    name = "fake"

    def __init__(self, scenario: str = "healthy", model: str = "fake-1") -> None:
        self.scenario = scenario
        self.model = model
        self.calls = 0

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _tool_results(messages: list[Message]) -> list[str]:
        return [m.content for m in messages if m.role == "tool"]

    @staticmethod
    def _called(messages: list[Message], tool: str) -> bool:
        return any(m.role == "tool" and m.name == tool for m in messages)

    @staticmethod
    def _current_date(messages: list[Message]) -> str:
        """The business date the server put in the system prompt, as a real model reads it."""
        for m in messages:
            if m.role == "system":
                match = re.search(r"Current date: (\d{4}-\d{2}-\d{2})", m.content)
                if match:
                    return match.group(1)
        return "2026-09-12"

    @staticmethod
    def _order_number(messages: list[Message]) -> str | None:
        """Pull an order number out of the user's request, as a real model would."""
        for m in messages:
            if m.role == "user":
                match = re.search(r"\b([A-Z]{2}-\d{3,6})\b", m.content)
                if match:
                    return match.group(1)
        return None

    def _evidence(self, messages: list[Message]) -> list[str]:
        seen: list[str] = []
        for content in self._tool_results(messages):
            for ref in _EVIDENCE.findall(content):
                if ref not in seen:
                    seen.append(ref)
        return seen

    # -- the provider interface -------------------------------------------
    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        deadline_s: float,
        max_output_tokens: int,
    ) -> ProviderResult:
        self.calls += 1
        usage = Usage(input_tokens=400 + 120 * len(messages), output_tokens=180)

        if self.scenario == "unavailable":
            raise ProviderUnavailable("fake provider configured to be unavailable")

        # Turn 1: look at the order. Turn 2: look for the policy. Then answer.
        order_number = self._order_number(messages)
        if order_number and not self._called(messages, "get_order_context"):
            return self._tool_result_for("get_order_context", {"order_number": order_number}, usage)
        if not order_number and not self._called(messages, "list_overdue_orders"):
            return self._tool_result_for(
                "list_overdue_orders",
                {"as_of_date": self._current_date(messages), "limit": 10},
                usage,
            )
        if not self._called(messages, "search_policies") or self.scenario == "tool_loop":
            # tool_loop never advances past this branch, which is how the turn and
            # tool-call bounds get exercised.
            return self._tool_result_for(
                "search_policies",
                {"query": "delayed shipment escalation policy"},
                usage,
            )

        return self._final(messages, usage)

    def _tool_result_for(self, name: str, args: dict[str, Any], usage: Usage) -> ProviderResult:
        return ProviderResult(
            text=None,
            tool_calls=[ToolCall(id=f"call_{self.calls}_{name}", name=name, arguments=args)],
            usage=usage,
            finish_reason="tool_calls",
            latency_ms=12,
            model=self.model,
        )

    def _final(self, messages: list[Message], usage: Usage) -> ProviderResult:
        if self.scenario == "malformed_output":
            # Missing `summary`, and `findings` is the wrong shape. The orchestrator
            # must reject this rather than coax it into something usable.
            return ProviderResult(
                text=json.dumps({"findings": "the order is late"}),
                usage=usage,
                finish_reason="stop",
                latency_ms=9,
                model=self.model,
            )

        evidence = self._evidence(messages)
        order_refs = [e for e in evidence if e.startswith("ord:")]
        event_refs = [e for e in evidence if e.startswith("evt:")]
        shipment_refs = [e for e in evidence if e.startswith("shp:")]
        policy_refs = [e for e in evidence if e.startswith("pol:")]

        if self.scenario == "no_evidence" or not evidence:
            payload: dict[str, Any] = {
                "summary": (
                    "I could not establish why this order is delayed. The records "
                    "available to me do not contain a dispatch or carrier event."
                ),
                "findings": [],
                "policy_citations": [],
                "missing_information": [
                    "A carrier event trail for this order",
                    "The applicable delayed-shipment policy, which is not indexed",
                ],
                "proposed_action": None,
            }
            return self._json(payload, usage)

        findings: list[dict[str, Any]] = []
        if order_refs:
            findings.append(
                {
                    "statement": "The order is past its promised date and has not been delivered.",
                    "kind": "observed_fact",
                    "evidence_ids": order_refs[:1],
                }
            )
        if event_refs:
            findings.append(
                {
                    "statement": (
                        "The carrier recorded events for this shipment "
                        "but no delivery confirmation."
                    ),
                    "kind": "observed_fact",
                    "evidence_ids": event_refs[:3],
                }
            )
            findings.append(
                {
                    "statement": (
                        "The delay most likely originates with the carrier "
                        "rather than with dispatch."
                    ),
                    "kind": "inference",
                    "evidence_ids": (shipment_refs or event_refs)[:2],
                }
            )

        if self.scenario == "unsupported_citation":
            findings.append(
                {
                    "statement": "A related order for the same customer is also delayed.",
                    "kind": "observed_fact",
                    # Never granted to any run. The validator must strip this.
                    "evidence_ids": [_UNGRANTED],
                }
            )

        payload = {
            "summary": (
                "The order is overdue. The shipment left the depot but the carrier has "
                "not confirmed delivery, so the delay sits with the carrier."
            ),
            "findings": findings,
            "policy_citations": [
                {
                    "citation_id": ref,
                    "relevance": "Retrieved policy reference (development fixture).",
                }
                for ref in policy_refs[:1]
            ],
            "missing_information": [
                "The applicable delayed-shipment policy could not be retrieved.",
            ]
            if not policy_refs
            else [],
            "proposed_action": {
                "action_type": "create_followup_task",
                "description": "Contact the carrier for a delivery ETA and update the customer.",
                "due_in_days": 2,
                "rationale": "The shipment is in transit past its estimated delivery date.",
            },
        }
        return self._json(payload, usage)

    def _json(self, payload: dict[str, Any], usage: Usage) -> ProviderResult:
        return ProviderResult(
            text=json.dumps(payload),
            usage=usage,
            finish_reason="stop",
            latency_ms=11,
            model=self.model,
        )


def ungranted_evidence_id() -> str:
    """The id the ``unsupported_citation`` scenario emits, for assertions."""
    return _UNGRANTED


__all__ = ["FakeProvider", "ungranted_evidence_id", "uuid"]
