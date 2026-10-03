"""Reproducible evaluation commands.

    pnpm eval verify                       # frozen case file intact?
    pnpm eval run                          # dev split, scripted model (no spend)
    pnpm eval run --split held_out         # the held-out estimate
    pnpm eval run --live --repeat 3        # configured provider, release-candidate style
    pnpm eval run --only N01 T04 --keep    # selected cases; keep the database to inspect
    pnpm eval build --force                # rebuild v1 (refused once frozen without --force)

Each run uses a fresh, isolated database (see environment.py) and writes
``evals/reports/eval_<split>_<id>.json`` and ``.md``. Model-dependent cases spend
provider quota only with ``--live``.
"""

# ruff: noqa: T201, S603 -- a command-line tool reporting progress; fixed local git calls

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "evals" / "reports"
POLICY = ROOT / "data" / "policies" / "delayed-shipments.md"


def _commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    head = subprocess.run(
        [git, "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if head.returncode != 0:
        return "uncommitted"
    dirty = subprocess.run(
        [git, "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build(args: argparse.Namespace) -> int:
    from evals.harness import cases
    from evals.harness.environment import isolated

    if cases.CASES_FILE.exists() and not args.force:
        print(f"{cases.CASES_FILE} is frozen. Pass --force only to replace v1 deliberately.")
        return 1
    with isolated(live=False) as env:
        from opspilot.db.session import session_scope

        scenarios = json.loads((env.directory / "seed-scenarios.json").read_text(encoding="utf-8"))
        with session_scope() as session:
            built = cases.build(session, scenarios["cases"])
        path = cases.write(
            built,
            frozen_on=datetime.now(UTC).date().isoformat(),
            dataset=scenarios["generated_from"],
        )
    print(f"wrote {path.relative_to(ROOT)}: {len(built)} cases")
    return 0


def verify(_: argparse.Namespace) -> int:
    from evals.harness import cases

    document = cases.load()
    total = len(document["cases"])
    splits = {
        split: sum(1 for case in document["cases"] if case["split"] == split)
        for split in ("dev", "held_out")
    }
    print(f"{total} cases intact; {splits} ; frozen {document['frozen_on']}")
    return 0


def _upload_policy(ctx: Any, organization: str) -> None:
    """Index the synthetic starter policy with the evaluated embedding model."""
    from sqlalchemy import select

    from opspilot.models.knowledge import DocumentVersion
    from opspilot.services.documents import index_version

    files = {"file": ("delayed-shipments.md", POLICY.read_bytes(), "text/markdown")}
    data = {"title": "Delayed shipments", "effective_from": "2026-01-01"}
    response = ctx.call(
        "POST", "/documents", as_=(organization, "supervisor"), files=files, data=data
    )
    response.raise_for_status()
    with ctx.session() as session:
        version_id = session.scalars(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == uuid.UUID(response.json()["id"])
            )
        ).one()
    with ctx.session() as session:
        index_version(session, ctx.orgs[organization], version_id, ctx.settings)


def run(args: argparse.Namespace) -> int:
    from evals.harness import cases
    from evals.harness.environment import SEED_DAY, isolated

    document = cases.load()
    selected = [
        case
        for case in document["cases"]
        if (args.split == "all" or case["split"] == args.split)
        and (not args.only or case["case_id"] in args.only)
    ]
    if not selected:
        print("No cases match the selection.")
        return 1
    run_id = uuid.uuid4().hex[:8]
    started = _now()
    deterministic_rows: list[dict[str, Any]] = []
    model_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []

    with isolated(live=args.live, keep=args.keep) as env:
        from evals.harness.deterministic import RUNNERS, Context
        from evals.harness.model_cases import grade, run_agent, run_baseline
        from opspilot.agent.prompts import PROMPT_VERSION
        from opspilot.core.config import get_settings
        from opspilot.retrieval.embeddings import embedding_identity

        settings = get_settings()
        excluded = {
            case["request"]["order_number"] for case in document["cases"] if case["kind"] == "model"
        }
        ctx = Context(env.base_url, excluded)
        for organization in ("northwind-supply", "harbor-wholesale"):
            _upload_policy(ctx, organization)

        for case in (c for c in selected if c["kind"] == "deterministic"):
            began = time.monotonic()
            row: dict[str, Any] = {
                "case_id": case["case_id"],
                "category": case["category"],
                "description": case["description"],
                "expected": case["expected"]["outcome"],
            }
            try:
                row["details"] = RUNNERS[case["case_id"]](ctx)
                row["passed"] = True
            except AssertionError as failure:
                row.update(passed=False, failure=str(failure))
            except Exception as error:
                row.update(
                    passed=False,
                    failure=f"{type(error).__name__}: {error}",
                    trace=traceback.format_exc(limit=4),
                )
            row["duration_ms"] = int((time.monotonic() - began) * 1000)
            deterministic_rows.append(row)
            print(
                f"{case['case_id']} {'PASS' if row['passed'] else 'FAIL'} {case['description']}"
                + ("" if row["passed"] else f" — {row['failure']}")
            )

        model_cases = [c for c in selected if c["kind"] == "model"]
        for case in model_cases:
            for attempt in range(args.repeat):
                agent = run_agent(ctx, case)
                agent["checks"] = grade(case, agent, ctx)
                model_rows.append(
                    {
                        "case_id": case["case_id"],
                        "category": case["category"],
                        "split": case["split"],
                        "repeat": attempt + 1,
                        "expected_outcome": case["expected"]["outcome"],
                        "agent": agent,
                    }
                )
                failed = [name for name, ok in agent["checks"].items() if not ok]
                print(
                    f"{case['case_id']}#{attempt + 1} {agent.get('status')} "
                    + (f"failed: {', '.join(failed)}" if failed else "all code checks passed")
                )
            answer = run_baseline(ctx, case)
            answer["checks"] = grade(case, answer, ctx)
            baseline_rows.append({"case_id": case["case_id"], **answer})

        meta = {
            "run_id": run_id,
            "started_at": started,
            "finished_at": _now(),
            "split": args.split,
            "repeat": args.repeat,
            "only": args.only or None,
            "cases_file": cases.CASES_FILE.relative_to(ROOT).as_posix(),
            "cases_version": document["version"],
            "frozen_on": document["frozen_on"],
            "split_sha256": document["split_sha256"],
            "commit": _commit(),
            "provider": settings.llm_provider,
            "model": "fake-1" if settings.llm_provider == "fake" else settings.llm_model,
            "prompt_version": PROMPT_VERSION,
            "embedding_mode": embedding_identity(settings),
            "business_date": SEED_DAY,
            "environment": str(env.directory.relative_to(ROOT)),
        }

    from evals.harness.report import markdown, summarize

    summary = summarize(meta, deterministic_rows, model_rows, baseline_rows)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = REPORTS / f"eval_{args.split}_{run_id}"
    full = {
        **summary,
        "deterministic_cases": deterministic_rows,
        "model_runs": model_rows,
        "baseline_answers": baseline_rows,
    }
    stem.with_suffix(".json").write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")
    stem.with_suffix(".md").write_text(markdown(summary), encoding="utf-8")
    print(f"\nreport: {stem.with_suffix('.md').relative_to(ROOT)}")
    failed_gates = [gate["gate"] for gate in summary["gates"] if gate["status"] == "FAIL"]
    if failed_gates:
        print(f"FAILED gates: {', '.join(failed_gates)}")
    return 1 if failed_gates else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.harness",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build", help="derive and freeze the 80 cases")
    build_parser.add_argument("--force", action="store_true")
    sub.add_parser("verify", help="check the frozen case file")
    run_parser = sub.add_parser("run", help="evaluate in an isolated environment")
    run_parser.add_argument("--split", choices=["dev", "held_out", "all"], default="dev")
    run_parser.add_argument(
        "--live", action="store_true", help="use the provider configured in .env"
    )
    run_parser.add_argument("--repeat", type=int, default=1, choices=range(1, 6), metavar="1-5")
    run_parser.add_argument("--only", nargs="*", help="case ids to run")
    run_parser.add_argument("--keep", action="store_true", help="keep the isolated database")
    args = parser.parse_args()
    return {"build": build, "verify": verify, "run": run}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
