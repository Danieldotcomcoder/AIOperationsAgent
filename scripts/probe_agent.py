"""Drive the orchestrator through every scenario the fake provider can produce.

Offline and deterministic: no API key, no spend, no Celery. It exercises the loop, the
tool dispatcher, the evidence grants, the bounds and the citation-validation policy
directly, which makes it the fastest way to tell whether the agent still behaves after
a change.

    uv run python ../../scripts/probe_agent.py      # from apps/api
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from opspilot.agent.orchestrator import RunContext, ToolDispatcher, run_investigation  # noqa: E402
from opspilot.agent.prompts import build_system_prompt  # noqa: E402
from opspilot.agent.providers.fake import FakeProvider  # noqa: E402
from opspilot.core.config import get_settings  # noqa: E402
from opspilot.core.ids import new_id  # noqa: E402
from opspilot.domain.budget import RunBudget  # noqa: E402
from opspilot.models import Membership, Order, Organization  # noqa: E402

AS_OF = __import__("datetime").date(2026, 9, 12)

SCENARIOS = [
    ("healthy", "a clean run over a real order"),
    ("unsupported_citation", "cites an id it was never granted"),
    ("malformed_output", "returns output that violates the schema"),
    ("unavailable", "provider is down"),
    ("tool_loop", "never stops calling tools"),
    ("no_evidence", "abstains and says what is missing"),
]


def main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)

    with Session(engine) as session:
        org = session.scalars(
            select(Organization).where(Organization.slug == "northwind-supply")
        ).one()
        membership = session.scalars(
            select(Membership).where(Membership.organization_id == org.id).limit(1)
        ).one()
        # An overdue order that actually has a carrier event trail — otherwise the
        # run has only the order row to cite and the probe demonstrates very little.
        from opspilot.models import Shipment, ShipmentEvent

        order = session.scalars(
            select(Order)
            .join(Shipment, Shipment.order_id == Order.id)
            .join(ShipmentEvent, ShipmentEvent.shipment_id == Shipment.id)
            .where(Order.organization_id == org.id, Order.promised_date < AS_OF)
            .order_by(Order.promised_date)
            .limit(1)
        ).first()
        if order is None:  # pragma: no cover - only if the dataset is not seeded
            raise SystemExit("no overdue order with events found — run the seed generator")

        print(f"organization : {org.name}")
        print(f"order        : {order.order_number} (promised {order.promised_date})\n")
        header = f"{'scenario':22} {'status':10} {'turns':>5} {'tools':>5} {'cited':>5} {'bad':>4} {'kept':>5}  notes"
        print(header)
        print("-" * len(header))

        failures = 0
        for scenario, description in SCENARIOS:
            ctx = RunContext(
                run_id=new_id(),
                organization_id=org.id,
                membership_id=membership.id,
                organization_name=org.name,
                request_text=f"Investigate order {order.order_number}. Why is it delayed?",
                as_of=AS_OF,
            )
            budget = RunBudget(
                max_turns=settings.run_max_turns,
                max_tool_calls=settings.run_max_tool_calls,
                deadline_seconds=settings.run_deadline_seconds,
                max_input_tokens=settings.run_max_input_tokens,
                max_output_tokens=settings.run_max_output_tokens,
            )
            outcome = run_investigation(
                ctx=ctx,
                provider=FakeProvider(scenario=scenario),
                dispatcher=ToolDispatcher(ctx, session),
                budget=budget,
                system_prompt=build_system_prompt(
                    organization_name=org.name, as_of=AS_OF.isoformat()
                ),
                max_output_tokens=settings.run_max_output_tokens,
                per_call_deadline=settings.provider_call_deadline_seconds,
            )

            v = outcome.validation
            if outcome.error_code:
                status = "error"
            elif outcome.incomplete_reason:
                status = outcome.incomplete_reason.value
            elif outcome.result:
                status = "complete"
            else:
                status = "no result"
            kept = len(outcome.result.findings) if outcome.result else 0

            note = _expectation(scenario, outcome, status)
            if note.startswith("UNEXPECTED"):
                failures += 1
            print(
                f"{scenario:22} {status:10} {outcome.turns:>5} {outcome.tool_calls:>5} "
                f"{v.cited:>5} {len(v.unsupported):>4} {kept:>5}  {note}"
            )

        # The healthy run is worth printing in full: it is the thing being built.
        print("\n--- healthy run, in full -------------------------------------------")
        ctx = RunContext(
            run_id=new_id(),
            organization_id=org.id,
            membership_id=membership.id,
            organization_name=org.name,
            request_text=f"Investigate order {order.order_number}. Why is it delayed?",
            as_of=AS_OF,
        )
        dispatcher = ToolDispatcher(ctx, session)
        outcome = run_investigation(
            ctx=ctx,
            provider=FakeProvider(),
            dispatcher=dispatcher,
            budget=RunBudget(
                max_turns=settings.run_max_turns,
                max_tool_calls=settings.run_max_tool_calls,
                deadline_seconds=settings.run_deadline_seconds,
                max_input_tokens=settings.run_max_input_tokens,
                max_output_tokens=settings.run_max_output_tokens,
            ),
            system_prompt=build_system_prompt(organization_name=org.name, as_of=AS_OF.isoformat()),
            max_output_tokens=settings.run_max_output_tokens,
            per_call_deadline=settings.provider_call_deadline_seconds,
        )
        result = outcome.result
        assert result is not None
        print(f"summary: {result.summary}\n")
        for f in result.findings:
            print(f"  [{f.kind:13}] {f.statement}")
            print(f"                  evidence: {', '.join(f.evidence_ids)}")
        print(f"\nmissing information: {result.missing_information}")
        if result.proposed_action:
            print(f"proposed action    : {result.proposed_action.description}")
            print(f"                     due in {result.proposed_action.due_in_days} days")
        print(f"\nevidence granted   : {len(dispatcher.granted_evidence)} ids")
        print(f"steps recorded     : {len(outcome.steps)}")
        for s in outcome.steps:
            print(
                f"  {s.ordinal}: {s.step_type.value:11} {s.tool_name or '':22} "
                f"{s.latency_ms:>4}ms rows={s.result_row_count}"
            )

        print(f"\n{'FAILURES: ' + str(failures) if failures else 'all scenarios behaved as expected'}")
        return 1 if failures else 0


def _expectation(scenario: str, outcome: object, status: str) -> str:
    """What each scenario must do. Anything else is a regression."""
    v = outcome.validation  # type: ignore[attr-defined]
    result = outcome.result  # type: ignore[attr-defined]
    if scenario == "healthy":
        ok = status == "complete" and result is not None and v.unsupported == []
        return "answers with supported citations" if ok else "UNEXPECTED: should complete cleanly"
    if scenario == "unsupported_citation":
        ok = status == "insufficient_evidence" and len(v.unsupported) == 1 and v.findings_removed == 1
        return (
            "stripped 1 claim, counted before stripping"
            if ok
            else "UNEXPECTED: should strip and mark incomplete"
        )
    if scenario == "malformed_output":
        ok = outcome.error_code == "provider.invalid_output" and result is None  # type: ignore[attr-defined]
        return "rejected, no result" if ok else "UNEXPECTED: should reject the output"
    if scenario == "unavailable":
        ok = status == "provider_unavailable" and result is None
        return "bounded retry then gave up" if ok else "UNEXPECTED: should stop as unavailable"
    if scenario == "tool_loop":
        ok = status in ("turn_limit", "tool_limit") and result is None
        return f"stopped at the bound ({status})" if ok else "UNEXPECTED: should hit a bound"
    if scenario == "no_evidence":
        ok = result is not None and result.findings == [] and result.missing_information
        return "abstained and said what is missing" if ok else "UNEXPECTED: should abstain"
    return ""


if __name__ == "__main__":
    sys.exit(main())
