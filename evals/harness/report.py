"""Evaluation report: JSON for machines, Markdown for people.

Deterministic and model-dependent cases keep separate denominators. Repeats count as
runs, never as cases. A gate that needs a person is reported as pending — whatever
the code checks say — and a scripted model's cost and latency are labelled as not
meaningful rather than reported as passing.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

GATE_COST_USD = 0.02
GATE_P95_SECONDS = 30.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.95 * len(ordered))) - 1]


def _rate(passed: int, total: int) -> str:
    return f"{passed}/{total} ({passed / total:.0%})" if total else "0/0"


def summarize(
    meta: dict[str, Any],
    deterministic: list[dict[str, Any]],
    model: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> dict[str, Any]:
    scripted = meta["provider"] == "fake"
    det: dict[str, dict[str, Any]] = {}
    for row in deterministic:
        bucket = det.setdefault(row["category"], {"passed": 0, "total": 0, "failed": []})
        bucket["total"] += 1
        bucket["passed"] += int(row["passed"])
        if not row["passed"]:
            bucket["failed"].append(row["case_id"])

    per_check: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    agent_runs = [row["agent"] for row in model]
    for row in model:
        for name, ok in row["agent"]["checks"].items():
            cell = per_check[row["category"]][name]
            cell[0] += int(ok)
            cell[1] += 1
    base_check: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in baseline:
        for name, ok in row["checks"].items():
            base_check[name][0] += int(ok)
            base_check[name][1] += 1
    agent_check: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in model:
        for name, ok in row["agent"]["checks"].items():
            agent_check[name][0] += int(ok)
            agent_check[name][1] += 1

    by_case: dict[str, list[dict[str, bool]]] = defaultdict(list)
    for row in model:
        by_case[row["case_id"]].append(row["agent"]["checks"])
    unstable = sorted(
        case for case, runs in by_case.items() if len({tuple(sorted(r.items())) for r in runs}) > 1
    )

    cited = sum(
        int(((run.get("result") or {}).get("validation") or {}).get("cited") or 0)
        for run in agent_runs
    )
    unsupported = sum(
        len(((run.get("result") or {}).get("validation") or {}).get("unsupported") or [])
        for run in agent_runs
    )
    abstain_rows = [row for row in model if row["expected_outcome"] in {"abstain", "not_found"}]
    abstained = sum(
        int(row["agent"]["checks"].get("states_missing", False)) for row in abstain_rows
    )
    costs = [
        float(run.get("cost_usd") or 0)
        for run in agent_runs
        if run.get("result") is not None or run.get("status")
    ]
    durations = [
        float(run.get("duration_ms") or 0)
        for run in agent_runs
        if run.get("duration_ms") is not None
    ]
    mean_cost = sum(costs) / len(costs) if costs else 0.0
    tenancy_model_ok = all(row["agent"]["checks"].get("own_tenant_evidence", True) for row in model)

    def det_status(category: str) -> str:
        bucket = det.get(category)
        if not bucket:
            return "not run"
        return "pass" if bucket["passed"] == bucket["total"] else "FAIL"

    gates = [
        {
            "gate": "Authorization",
            "threshold": "every tenant/role case passes",
            "measured": _rate(
                det.get("tenant_role", {}).get("passed", 0),
                det.get("tenant_role", {}).get("total", 0),
            )
            + (
                "; model runs read only own-tenant evidence"
                if tenancy_model_ok
                else "; a model run read foreign evidence"
            ),
            "status": det_status("tenant_role") if tenancy_model_ok else "FAIL",
        },
        {
            "gate": "Approval integrity",
            "threshold": "all stale/expired/changed/unapproved writes refused",
            "measured": _rate(
                det.get("approval_stale", {}).get("passed", 0),
                det.get("approval_stale", {}).get("total", 0),
            ),
            "status": det_status("approval_stale"),
        },
        {
            "gate": "Idempotency",
            "threshold": "at most one downstream task per approval",
            "measured": _rate(
                det.get("duplicates_crashes", {}).get("passed", 0),
                det.get("duplicates_crashes", {}).get("total", 0),
            ),
            "status": det_status("duplicates_crashes"),
        },
        {
            "gate": "Investigation success",
            "threshold": "≥ 85% of answerable normal and ambiguity cases meet the human rubric",
            "measured": "code preconditions only; human rubric not graded",
            "status": "pending human review",
        },
        {
            "gate": "Citation support",
            "threshold": "≥ 95% of audited factual claims supported",
            "measured": (
                f"automated proxy: {cited - unsupported}/{cited} cited ids "
                "were granted to their run"
                if cited
                else "no citations"
            ),
            "status": "pending human audit",
        },
        {
            "gate": "Missing evidence",
            "threshold": "≥ 90% of abstention cases marked unresolved",
            "measured": _rate(abstained, len(abstain_rows)) + " state what is missing (code check)",
            "status": "not meaningful (scripted model)"
            if scripted
            else ("pass" if abstain_rows and abstained / len(abstain_rows) >= 0.9 else "FAIL")
            + " — confirm wording by review",
        },
        {
            "gate": "Cost",
            "threshold": f"mean < ${GATE_COST_USD} per investigation; report p95",
            "measured": f"mean ${mean_cost:.6f}, p95 ${_p95(costs):.6f} (estimates)",
            "status": "not meaningful (scripted model)"
            if scripted
            else ("pass" if costs and sum(costs) / len(costs) < GATE_COST_USD else "FAIL"),
        },
        {
            "gate": "Latency",
            "threshold": f"p95 < {GATE_P95_SECONDS:.0f} s at three simultaneous users",
            "measured": f"p95 {_p95(durations) / 1000:.1f} s, sequential runs only",
            "status": "not meaningful (scripted model)"
            if scripted
            else "partial: concurrency not measured",
        },
        {
            "gate": "Operations",
            "threshold": "restore, rollback, limits, log rotation, disable-write switch",
            "measured": "disable-write switch covered by A09; the rest are deployment drills",
            "status": "not measured here",
        },
    ]
    return {
        "meta": meta,
        "deterministic": det,
        "model": {
            category: {
                name: {"passed": cell[0], "applicable": cell[1]} for name, cell in checks.items()
            }
            for category, checks in per_check.items()
        },
        "agent_overall": {
            name: {"passed": cell[0], "applicable": cell[1]} for name, cell in agent_check.items()
        },
        "baseline_overall": {
            name: {"passed": cell[0], "applicable": cell[1]} for name, cell in base_check.items()
        },
        "agent_runs": len(agent_runs),
        "unique_model_cases": len(by_case),
        "unstable_cases": unstable,
        "incomplete_reasons": dict(
            Counter(
                run.get("incomplete_reason") for run in agent_runs if run.get("incomplete_reason")
            )
        ),
        "error_codes": dict(
            Counter(run.get("error_code") for run in agent_runs if run.get("error_code"))
        ),
        "unknown_tool_attempts": sum(
            int(run.get("unknown_tool_attempts") or 0) for run in agent_runs
        ),
        "cost": {"mean_usd": sum(costs) / len(costs) if costs else 0.0, "p95_usd": _p95(costs)},
        "latency": {
            "agent_p95_ms": _p95(durations),
            "baseline_p95_ms": _p95([float(row["duration_ms"]) for row in baseline]),
        },
        "human_review_pending": len(by_case),
        "gates": gates,
    }


def markdown(summary: dict[str, Any]) -> str:
    meta = summary["meta"]
    lines = [
        f"# Evaluation report — {meta['split']} split",
        "",
        f"- Run: {meta['started_at']} → {meta['finished_at']} ({meta['run_id']})",
        f"- Cases: `{meta['cases_file']}` {meta['cases_version']}, frozen {meta['frozen_on']}; "
        f"commit `{meta['commit']}`",
        f"- Model: {meta['provider']} · {meta['model']} · prompt {meta['prompt_version']} · "
        f"embeddings {meta['embedding_mode']}",
        f"- Business date {meta['business_date']}; repeats per model case: {meta['repeat']}",
        "- Isolated database, dropped afterwards. Synthetic data; simulated ERP.",
    ]
    if meta["provider"] == "fake":
        lines += [
            "",
            "> **Scripted model.** These model-dependent results verify the harness and the",
            "> workflow, not answer quality. Run with `--live` for a quality measurement.",
        ]
    lines += [
        "",
        "## Release gates",
        "",
        "| Gate | Threshold | Measured | Status |",
        "| --- | --- | --- | --- |",
    ]
    for gate in summary["gates"]:
        lines.append(
            f"| {gate['gate']} | {gate['threshold']} | {gate['measured']} | {gate['status']} |"
        )

    lines += [
        "",
        "## Deterministic cases",
        "",
        "| Category | Passed | Failed cases |",
        "| --- | --- | --- |",
    ]
    for category, bucket in summary["deterministic"].items():
        lines.append(
            f"| {category} | {_rate(bucket['passed'], bucket['total'])} | "
            f"{', '.join(bucket['failed']) or '—'} |"
        )

    lines += [
        "",
        "## Model-dependent cases (code checks)",
        "",
        f"{summary['unique_model_cases']} unique cases, {summary['agent_runs']} runs. "
        f"Human rubric pending for all {summary['human_review_pending']}.",
        "",
        "| Check | Agent | Baseline |",
        "| --- | --- | --- |",
    ]
    for name in sorted(set(summary["agent_overall"]) | set(summary["baseline_overall"])):
        agent = summary["agent_overall"].get(name)
        base = summary["baseline_overall"].get(name)
        lines.append(
            f"| {name} | {_rate(agent['passed'], agent['applicable']) if agent else '—'} | "
            f"{_rate(base['passed'], base['applicable']) if base else '—'} |"
        )
    lines += [
        "",
        f"- Unstable across repeats: {', '.join(summary['unstable_cases']) or 'none'}",
        f"- Incomplete reasons: {summary['incomplete_reasons'] or 'none'}",
        f"- Error codes: {summary['error_codes'] or 'none'}",
        "- Attempted calls to tools outside the allowlist (all refused): "
        f"{summary['unknown_tool_attempts']}",
        f"- Mean cost ${summary['cost']['mean_usd']:.6f}, "
        f"p95 ${summary['cost']['p95_usd']:.6f} (estimates)",
        f"- p95 latency: agent {summary['latency']['agent_p95_ms'] / 1000:.2f} s, "
        f"baseline {summary['latency']['baseline_p95_ms'] / 1000:.3f} s",
        "",
        "The baseline is overdue-order SQL, a template explanation and a fixed policy link.",
        "It cannot abstain, so it fails `states_missing` by design; where it ties the agent",
        "on code checks, only human usefulness grading can justify the agent's cost.",
        "",
        "## Per-category code checks (agent)",
        "",
    ]
    for category, checks in summary["model"].items():
        cells = ", ".join(
            f"{name} {_rate(c['passed'], c['applicable'])}" for name, c in sorted(checks.items())
        )
        lines.append(f"- **{category}**: {cells}")
    return "\n".join(lines) + "\n"
