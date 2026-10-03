"""Report every place a tenant table is queried without the tenant-scoped repository.

A grep, not a proof. It catches the ordinary mistake — someone writes
``select(Order)`` in a service because it is shorter — which is exactly the mistake
that produces a cross-tenant leak. It cannot catch a deliberate bypass, and it is not
a substitute for the security review in Week 6.

    python scripts/audit_tenancy.py            # report
    python scripts/audit_tenancy.py --strict   # exit 1 on any finding (CI)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

API_SRC = Path(__file__).resolve().parents[1] / "apps" / "api" / "src" / "opspilot"

ALLOWED_FUNCTIONS = {
    ("db/repository.py", "query"): "the tenant scoping mechanism",
    (
        "db/identity.py",
        "active_memberships",
    ): "bootstrap from verified subject before tenant exists",
    ("db/identity.py", "dev_membership"): "local issuer bootstrap from seeded email",
    ("db/worker_context.py", "run_organization"): "worker ID-to-tenant discovery only",
    ("ops/api.py", "lookup_task"): "service-token-only lookup by unguessable idempotency key",
    ("services/runs.py", "stranded_run_ids"): "internal sweep returns only queued IDs",
    (
        "services/retention.py",
        "expired_trace_ids",
    ): "internal age sweep; per-row deletion re-scopes",
    (
        "services/retention.py",
        "prune_audit_batch",
    ): "bounded internal age sweep; deletes by tenant and ID",
    ("workers/tasks.py", "dispatch_outbox"): "internal due-event ID sweep",
    ("workers/tasks.py", "execute_action"): "worker ID-to-tenant discovery only",
    ("workers/tasks.py", "reconcile_pending"): "internal abandoned-action ID sweep",
    ("workers/tasks.py", "expire_stale_proposals"): "internal expiry ID sweep",
    ("workers/tasks.py", "fail_abandoned_runs"): "internal abandoned-run ID sweep",
    ("workers/tasks.py", "index_document_version"): "worker ID-to-tenant discovery only",
}

TENANT_MODELS = {
    "Customer",
    "Product",
    "Order",
    "OrderLine",
    "Shipment",
    "ShipmentEvent",
    "Document",
    "DocumentVersion",
    "Chunk",
    "AgentRun",
    "RunStep",
    "EvidenceGrant",
    "ProposedAction",
    "Approval",
    "OutboxEvent",
    "OperationTask",
    "AuditEvent",
    "Feedback",
    "OpsTask",
    "Membership",
}


def find_unscoped(path: Path, relative: str = "") -> list[tuple[int, str]]:
    """Flag model and column projections plus direct Session.get bypasses.

    Exceptions belong to individual reviewed functions, never an entire service.
    This is still a heuristic, not proof against aliases or raw SQL.
    """
    findings: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in {"select", "get"} or not node.args:
            continue
        if name == "get" and isinstance(func, ast.Attribute):
            receiver = ast.unparse(func.value)
            if receiver not in {"session", "self.session", "self._session"}:
                continue  # repository.get is the correct access path
        ancestor = parents.get(node)
        while ancestor is not None and not isinstance(
            ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            ancestor = parents.get(ancestor)
        function = ancestor.name if ancestor is not None else ""
        if (relative, function) in ALLOWED_FUNCTIONS:
            continue
        for argument in node.args:
            while isinstance(argument, ast.Attribute):
                argument = argument.value
            model = argument.id if isinstance(argument, ast.Name) else ""
            if model in TENANT_MODELS:
                findings.append((node.lineno, f"{name}({model}) without TenantScopedRepository"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    total = 0
    for path in sorted(API_SRC.rglob("*.py")):
        rel = path.relative_to(API_SRC).as_posix()
        for line, message in find_unscoped(path, rel):
            print(f"{rel}:{line}: {message}")
            total += 1

    if total == 0:
        print("tenancy audit: no unscoped tenant queries found")
        return 0
    print(f"\ntenancy audit: {total} finding(s)")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
