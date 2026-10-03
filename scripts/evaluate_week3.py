"""Score twenty human-labeled questions against the current semantic policy index.

From apps/api: uv run python ../../scripts/evaluate_week3.py --labels path/to/labels.json
No document upload, label generation, approvals, or model-based grading is performed.
"""

from __future__ import annotations

# ruff: noqa: T201 -- local CLI progress only
import argparse
import hashlib
import json
import uuid
from pathlib import Path

from opspilot.core.clock import utcnow
from opspilot.core.config import get_settings
from opspilot.core.errors import ProviderUnavailable
from opspilot.db.identity import dev_membership
from opspilot.db.repository import TenantScopedRepository
from opspilot.db.session import session_scope
from opspilot.models import DocumentVersion
from opspilot.retrieval.embeddings import embedding_identity
from opspilot.retrieval.search import effective_versions_stmt
from opspilot.services.tools import search_policies

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()
    raw = args.labels.read_bytes()
    labels = json.loads(raw)
    cases = labels.get("cases", [])
    if (
        not labels.get("reviewed_by")
        or len(cases) != 20
        or len({case["id"] for case in cases}) != 20
        or any(
            not isinstance(case.get("expected_quote"), str)
            or not case["expected_quote"].strip()
            or not case.get("expected_document_title")
            for case in cases
        )
    ):
        raise SystemExit(
            "Requires 20 unique human-reviewed labels with expected_quote "
            "and expected_document_title. No provider calls made."
        )
    settings = get_settings()
    if not settings.app_env.is_local or settings.embedding_provider == "fake":
        raise SystemExit("Requires local configuration with real semantic embeddings.")
    identity = embedding_identity(settings)
    report = {
        "started_at": utcnow().isoformat(),
        "labels_sha256": hashlib.sha256(raw).hexdigest(),
        "reviewed_by": labels["reviewed_by"],
        "embedding_identity": identity,
        "top_k": settings.retrieval_top_k,
        "token_budget": settings.retrieval_token_budget,
        "status": "incomplete",
        "cases": [],
    }
    if settings.retrieval_top_k != 5:
        raise SystemExit("Week 3 requires RETRIEVAL_TOP_K=5.")
    output = ROOT / "evals/reports" / ("week3_" + uuid.uuid4().hex + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with session_scope() as session:
        member = dev_membership(session, "ana.analyst@northwind.example.com")
        if member is None:
            raise SystemExit("Seed the local development membership first.")
        repo = TenantScopedRepository(session, member.organization_id)
        eligible = effective_versions_stmt(repo, utcnow().date()).where(
            DocumentVersion.embedding_model == identity,
            DocumentVersion.embedding_dim == settings.embedding_dim,
        )
        if session.scalar(eligible.limit(1)) is None:
            raise SystemExit("No compatible effective policies: index reviewed policies first.")
        for case in cases:
            try:
                result = search_policies(
                    session,
                    repo,
                    query=case["query"],
                    as_of=utcnow().date(),
                    top_k=5,
                )
                passages = result.payload["passages"]
                hit = any(
                    passage["document_title"] == case["expected_document_title"]
                    and case["expected_quote"] in passage["text"]
                    for passage in passages
                )
                row = {**case, "hit_at_5": hit, "passages": passages}
            except ProviderUnavailable:
                row = {**case, "hit_at_5": None, "error": "provider.unavailable"}
            report["cases"].append(row)
            output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            print(f"{case['id']}: hit_at_5={row['hit_at_5']}", flush=True)
            if row["hit_at_5"] is None:
                print("Stopped: provider unavailable. Keep this attempt in the evaluation history.")
                break
    complete = len(report["cases"]) == 20 and all(
        row["hit_at_5"] is not None for row in report["cases"]
    )
    report["hits"] = sum(row["hit_at_5"] is True for row in report["cases"])
    report["status"] = (
        "passed_retrieval_threshold"
        if complete and report["hits"] >= 18
        else "failed_retrieval_threshold"
        if complete
        else "incomplete"
    )
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Retrieval evidence: {output}")
    return 0 if report["status"] == "passed_retrieval_threshold" else 1


if __name__ == "__main__":
    raise SystemExit(main())
