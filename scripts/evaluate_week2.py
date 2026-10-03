"""Collect bounded live investigations and database facts for human review.

From apps/api: uv run python ../../scripts/evaluate_week2.py --count 10
Uses the configured provider, normal admission budgets and durable run lifecycle.
No approvals or ERP writes are performed. Run against the local synthetic database.
"""

from __future__ import annotations

# ruff: noqa: T201 -- command-line progress, never credentials or model content
import argparse
import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import asdict
from pathlib import Path

from opspilot.auth.principal import Principal
from opspilot.core.clock import business_date, utcnow
from opspilot.core.config import get_settings
from opspilot.db.identity import dev_membership
from opspilot.db.repository import TenantScopedRepository
from opspilot.db.session import session_scope
from opspilot.models import Organization
from opspilot.services.orders import get_order_by_number
from opspilot.services.runs import create_run, get_run, get_run_steps, process_run

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--start", type=int, choices=range(10), default=0)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.app_env.is_local or settings.llm_provider == "fake":
        raise SystemExit("Requires a local environment and a real configured chat provider.")
    dataset_path = ROOT / "evals/cases/seed_scenarios.json"
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    if args.start + args.count > 10:
        raise SystemExit("Select within the first ten development cases.")
    cases = dataset["cases"][args.start : args.start + args.count]
    output = ROOT / "evals/reports" / ("week2_" + uuid.uuid4().hex + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(  # noqa: S603 -- fixed local git command, no user arguments
        [shutil.which("git") or "git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = {
        "started_at": utcnow().isoformat(),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "dataset_as_of": dataset["generated_from"]["as_of"],
        "evaluation_as_of": business_date(settings).isoformat(),
        "commit": commit.stdout.strip() if commit.returncode == 0 else "uncommitted",
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "embedding_provider": settings.embedding_provider,
        "gate_status": "human_review_required",
        "start_index": args.start,
        "cases": [],
        "note": "Facts are current database snapshots, not model-authored labels, read on "
        "the same business date the agent used. No automatic quality verdict.",
    }
    for case in cases:
        with session_scope() as session:
            member = dev_membership(session, "ana.analyst@northwind.example.com")
            if member is None:
                raise SystemExit("Seed the local development memberships first.")
            org = session.get(Organization, member.organization_id)
            if org is None or org.slug != case["organization"]:
                raise SystemExit("Dataset organization does not match the local principal.")
            principal = Principal(
                membership_id=member.id,
                organization_id=member.organization_id,
                subject=member.subject,
                email=member.email,
                display_name=member.display_name,
                role=member.role,
            )
            repo = TenantScopedRepository(session, member.organization_id)
            facts = get_order_by_number(
                session,
                repo,
                case["order_number"],
                as_of=business_date(settings),
            )
            if facts is None:
                raise SystemExit("Seeded order is missing; no provider call was made.")
            created = create_run(
                session,
                settings,
                principal,
                request_text=f"Investigate order {case['order_number']}. Explain the facts, "
                "what remains uncertain, and any appropriate follow-up. "
                "Retrieve applicable policies.",
                order_number=case["order_number"],
            )
        # This command performs the normal worker entrypoint synchronously. The
        # queued duplicate is harmless because the durable claim is idempotent.
        process_run(settings, created.id)
        with session_scope() as session:
            run = get_run(session, principal, created.id)
            steps = get_run_steps(session, principal, created.id)
            row = {
                "order_number": case["order_number"],
                "run_id": str(run.id),
                "database_facts": asdict(facts),
                "status": run.status.value,
                "error_code": run.error_code,
                "incomplete_reason": run.incomplete_reason,
                "result": run.result_json,
                "prompt_version": run.prompt_version,
                "model": run.model_name,
                "tokens_in": run.tokens_in,
                "tokens_out": run.tokens_out,
                "cost_usd": str(run.cost_usd),
                "duration_ms": run.duration_ms,
                "turns": run.turn_count,
                "tools": run.tool_call_count,
                "steps": [
                    {
                        "type": step.step_type.value,
                        "tool": step.tool_name,
                        "error_code": step.error_code,
                    }
                    for step in steps
                ],
                "human_review": {
                    "reviewed_by": None,
                    "facts_correct": None,
                    "fabricated_order_reference": None,
                    "notes": None,
                },
            }
        report["cases"].append(row)
        output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(
            f"{case['order_number']}: {row['status']}; error={row['error_code']}; "
            f"incomplete={row['incomplete_reason']}",
            flush=True,
        )
        if row["result"] is None:
            print("Stopping at the first unusable result; diagnose before spending more quota.")
            break
    print(f"Review evidence: {output}")
    return (
        0
        if len(report["cases"]) == args.count
        and all(row["result"] is not None for row in report["cases"])
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
