"""The 35 deterministic cases: tenancy and roles, approval integrity, recovery.

Each case sets up its own state in the isolated environment, acts through the real
HTTP API or the real worker entry points, and checks the outcome the system contract
requires. None depends on a model: investigations here always use the scripted
provider and word-hash embeddings, whatever the evaluated configuration is, so these
results are identical across providers and spend nothing.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx

USERS = {
    ("northwind-supply", "analyst"): "ana.analyst@northwind.example.com",
    ("northwind-supply", "supervisor"): "sam.supervisor@northwind.example.com",
    ("harbor-wholesale", "analyst"): "hal.analyst@harbor.example.com",
    ("harbor-wholesale", "supervisor"): "hana.supervisor@harbor.example.com",
}
NW, HB = "northwind-supply", "harbor-wholesale"


@dataclass(frozen=True)
class Spec:
    case_id: str
    category: str
    organization: str
    role: str
    expected: str
    description: str


class CaseFailedError(AssertionError):
    """A contract the case checks did not hold."""


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CaseFailedError(message)


class Context:
    """Shared access to the isolated stack for deterministic cases."""

    def __init__(self, base_url: str, excluded_orders: set[str]) -> None:
        from opspilot.core.config import get_settings

        self.http = httpx.Client(base_url=base_url, timeout=30)
        self.settings = get_settings()
        # Deterministic cases never depend on (or pay for) the evaluated model.
        self.scripted = self.settings.model_copy(
            update={"llm_provider": "fake", "embedding_provider": "fake"}
        )
        self._tokens: dict[str, str] = {}
        self.orgs = self._organizations()
        self.scratch = {slug: self._scratch_order(slug, excluded_orders) for slug in (NW, HB)}

    # -- plumbing ------------------------------------------------------------------
    def token(self, organization: str, role: str) -> str:
        email = USERS[(organization, role)]
        if email not in self._tokens:
            response = self.http.post("/auth/dev/token", json={"email": email})
            response.raise_for_status()
            self._tokens[email] = response.json()["access_token"]
        return self._tokens[email]

    def call(
        self,
        method: str,
        path: str,
        *,
        as_: tuple[str, str] | None = None,
        token: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        bearer = token or (self.token(*as_) if as_ else None)
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        return self.http.request(method, path, headers=headers, **kwargs)

    @contextmanager
    def session(self) -> Iterator[Any]:
        from opspilot.db.session import session_scope

        with session_scope() as session:
            yield session

    def _organizations(self) -> dict[str, uuid.UUID]:
        from sqlalchemy import select

        from opspilot.models import Organization

        with self.session() as session:
            return {org.slug: org.id for org in session.scalars(select(Organization))}

    def _scratch_order(self, slug: str, excluded: set[str]) -> str:
        """An overdue order with a carrier trail that no model case uses."""
        from evals.harness.cases import SEED_DAY
        from opspilot.db.repository import TenantScopedRepository
        from opspilot.services.orders import get_order_by_number, list_orders

        with self.session() as session:
            repo = TenantScopedRepository(session, self.orgs[slug])
            for row in list_orders(session, repo, as_of=SEED_DAY, overdue_only=True, limit=100):
                if row.order_number in excluded:
                    continue
                detail = get_order_by_number(session, repo, row.order_number, as_of=SEED_DAY)
                if detail is not None and detail.events:
                    return row.order_number
        raise RuntimeError(f"no scratch order available in {slug}")

    # -- workflow helpers -----------------------------------------------------------
    def investigate(
        self, organization: str, order_number: str | None = None, *, process: bool = True
    ) -> dict[str, Any]:
        from opspilot.services.runs import process_run

        number = order_number or self.scratch[organization]
        response = self.call(
            "POST",
            "/runs",
            as_=(organization, "analyst"),
            json={
                "request_text": f"Investigate order {number}. Why is it delayed?",
                "order_number": number,
            },
        )
        expect(response.status_code == 202, f"run admission returned {response.status_code}")
        run_id = response.json()["id"]
        if process:
            process_run(self.scripted, uuid.UUID(run_id))
        return self.call("GET", f"/runs/{run_id}", as_=(organization, "analyst")).json()

    def proposal(self, organization: str = NW) -> dict[str, Any]:
        run = self.investigate(organization)
        expect(run.get("proposal_id") is not None, "the scripted investigation proposed nothing")
        action = self.call(
            "GET", f"/actions/{run['proposal_id']}", as_=(organization, "supervisor")
        )
        expect(action.status_code == 200, "the supervisor could not read the proposal")
        return dict(action.json())

    def decide(
        self,
        action: dict[str, Any],
        *,
        approve: bool,
        organization: str = NW,
        role: str = "supervisor",
        payload_hash: str | None = None,
    ) -> httpx.Response:
        return self.call(
            "POST",
            f"/actions/{action['id']}/{'approve' if approve else 'reject'}",
            as_=(organization, role),
            json={"payload_hash": payload_hash or action["payload_hash"]},
        )

    def action_status(self, action: dict[str, Any], organization: str = NW) -> str:
        return str(
            self.call("GET", f"/actions/{action['id']}", as_=(organization, "supervisor")).json()[
                "status"
            ]
        )

    def execute(self, action: dict[str, Any], organization: str = NW, settings: Any = None) -> None:
        from opspilot.services.execution import process_action

        process_action(settings or self.settings, self.orgs[organization], uuid.UUID(action["id"]))

    def erp_tasks(self, action: dict[str, Any], organization: str = NW) -> int:
        """Downstream tasks carrying this action's deterministic idempotency key."""
        from sqlalchemy import func, select

        from opspilot.domain.hashing import idempotency_key
        from opspilot.models import OpsTask

        key = idempotency_key(
            self.orgs[organization], uuid.UUID(action["id"]), action["payload_hash"]
        )
        with self.session() as session:
            return int(
                session.scalar(
                    select(func.count()).select_from(OpsTask).where(OpsTask.idempotency_key == key)
                )
                or 0
            )

    def outbox_events(self, action: dict[str, Any]) -> int:
        from sqlalchemy import func, select

        from opspilot.models import OutboxEvent

        with self.session() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == uuid.UUID(action["id"]))
                )
                or 0
            )

    def touch_order(self, action: dict[str, Any], organization: str = NW) -> None:
        """Change the bound order, moving its record version (ADR 0004)."""
        from opspilot.db.repository import TenantScopedRepository
        from opspilot.models import Order

        with self.session() as session:
            repo = TenantScopedRepository(session, self.orgs[organization])
            order = repo.get_or_404(Order, uuid.UUID(action["source_record_id"]))
            order.notes = f"evaluation change {uuid.uuid4().hex[:6]}"

    @contextmanager
    def provider(self, scenario: str, delay_s: float = 0.0) -> Iterator[None]:
        """Route worker-side investigations to a scripted failure scenario."""
        from opspilot.agent.providers.fake import FakeProvider
        from opspilot.services import runs

        class Slow(FakeProvider):
            def complete(self, **kwargs: Any) -> Any:
                time.sleep(delay_s)
                return super().complete(**kwargs)

        original = runs.build_provider
        runs.build_provider = lambda settings, **_: Slow(scenario=scenario)  # type: ignore[assignment]
        try:
            yield
        finally:
            runs.build_provider = original


# ---------------------------------------------------------------- tenant and role
def t01(ctx: Context) -> dict[str, Any]:
    status = ctx.call("GET", "/orders").status_code
    expect(status == 401, f"unauthenticated read returned {status}")
    return {"status": status}


def t02(ctx: Context) -> dict[str, Any]:
    token = ctx.token(NW, "analyst")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    status = ctx.call("GET", "/me", token=tampered).status_code
    expect(status == 401, f"tampered token returned {status}")
    return {"status": status}


def t03(ctx: Context) -> dict[str, Any]:
    response = ctx.call(
        "GET", "/me", as_=(NW, "analyst"), headers={"X-Organization": str(ctx.orgs[HB])}
    )
    expect(
        response.status_code == 403,
        f"selecting another organization returned {response.status_code}",
    )
    return {"status": response.status_code}


def _order_id(ctx: Context, organization: str, number: str) -> str:
    rows = ctx.call("GET", f"/orders?q={number}&limit=5", as_=(organization, "analyst")).json()
    match = [row for row in rows if row["order_number"] == number]
    expect(len(match) == 1, f"{number} not found exactly once in {organization}")
    return str(match[0]["id"])


def t04(ctx: Context) -> dict[str, Any]:
    harbor_order = _order_id(ctx, HB, "SO-1042")
    response = ctx.call("GET", f"/orders/{harbor_order}", as_=(NW, "analyst"))
    expect(response.status_code == 404, f"cross-tenant order read returned {response.status_code}")
    expect(
        response.json().get("error_code") == "resource.not_found", "the refusal confirmed existence"
    )
    return {"status": response.status_code}


def t05(ctx: Context) -> dict[str, Any]:
    northwind, harbor = _order_id(ctx, NW, "SO-1042"), _order_id(ctx, HB, "SO-1042")
    expect(northwind != harbor, "SO-1042 resolved to the same row in both organizations")
    own = ctx.call("GET", f"/orders/{northwind}", as_=(NW, "analyst"))
    expect(own.status_code == 200, "own-tenant order read failed")
    return {"northwind": northwind, "harbor": harbor}


def t06(ctx: Context) -> dict[str, Any]:
    run = ctx.investigate(HB, process=False)
    status = ctx.call("GET", f"/runs/{run['id']}", as_=(NW, "analyst")).status_code
    expect(status == 404, f"cross-tenant run read returned {status}")
    return {"status": status}


def t07(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal(HB)
    status = ctx.call("GET", f"/actions/{action['id']}", as_=(NW, "supervisor")).status_code
    expect(status == 404, f"cross-tenant proposal read returned {status}")
    return {"status": status}


def t08(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal(NW)
    response = ctx.decide(action, approve=True, organization=HB)
    expect(response.status_code == 404, f"cross-tenant approval returned {response.status_code}")
    expect(ctx.action_status(action) == "pending", "a cross-tenant approval changed the proposal")
    return {"status": response.status_code}


def t09(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal(NW)
    response = ctx.decide(action, approve=True, role="analyst")
    expect(response.status_code == 403, f"analyst approval returned {response.status_code}")
    expect(ctx.action_status(action) == "pending", "an analyst approval changed the proposal")
    expect(ctx.outbox_events(action) == 0, "an analyst approval queued execution")
    return {"status": response.status_code}


def _indexed_policy(ctx: Context, organization: str) -> str:
    from sqlalchemy import select

    from opspilot.models.knowledge import DocumentVersion
    from opspilot.services.documents import index_version

    files = {
        "file": (
            "evaluation.md",
            b"# Evaluation policy\nEscalate delays after two days.",
            "text/markdown",
        )
    }
    data = {"title": f"Evaluation policy {uuid.uuid4().hex[:6]}", "effective_from": "2026-01-01"}
    response = ctx.call(
        "POST", "/documents", as_=(organization, "supervisor"), files=files, data=data
    )
    expect(response.status_code == 201, f"policy upload returned {response.status_code}")
    with ctx.session() as session:
        version = session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == ctx.orgs[organization],
                DocumentVersion.document_id == uuid.UUID(response.json()["id"]),
            )
        ).one()
        version_id = version.id
    with ctx.session() as session:
        index_version(session, ctx.orgs[organization], version_id, ctx.scripted)
    return str(version_id)


def t10(ctx: Context) -> dict[str, Any]:
    version = _indexed_policy(ctx, HB)
    own = ctx.call(
        "GET", f"/documents/versions/{version}/chunks/0", as_=(HB, "analyst")
    ).status_code
    other = ctx.call(
        "GET", f"/documents/versions/{version}/chunks/0", as_=(NW, "analyst")
    ).status_code
    expect(own == 200, f"own-tenant passage read returned {own}")
    expect(other == 404, f"cross-tenant passage read returned {other}")
    return {"own": own, "other": other}


def t11(ctx: Context) -> dict[str, Any]:
    harbor_rows = ctx.call("GET", "/orders?limit=1", as_=(HB, "analyst")).json()
    customer = harbor_rows[0]["customer_name"]
    rows = ctx.call("GET", f"/orders?q={customer}&limit=50", as_=(NW, "analyst")).json()
    expect(rows == [], f"searching for {customer!r} returned another organization's orders")
    return {"searched": customer}


def t12(ctx: Context) -> dict[str, Any]:
    before = ctx.call("GET", "/metrics/summary?days=7", as_=(HB, "analyst")).json()["run_count"]
    ctx.investigate(NW, process=False)
    after = ctx.call("GET", "/metrics/summary?days=7", as_=(HB, "analyst")).json()["run_count"]
    expect(before == after, "another organization's run appeared in these metrics")
    return {"before": before, "after": after}


def t13(ctx: Context) -> dict[str, Any]:
    status = ctx.http.post("/auth/dev/token", json={"email": "nobody@example.com"}).status_code
    expect(status == 404, f"token for an unknown email returned {status}")
    return {"status": status}


def t14(ctx: Context) -> dict[str, Any]:
    run = ctx.investigate(HB, process=False)
    status = ctx.call("POST", f"/runs/{run['id']}/cancel", as_=(NW, "supervisor")).status_code
    expect(status == 404, f"cross-tenant cancel returned {status}")
    after = ctx.call("GET", f"/runs/{run['id']}", as_=(HB, "analyst")).json()["status"]
    expect(after == "queued", "a cross-tenant cancel changed the run")
    return {"status": status}


def t15(ctx: Context) -> dict[str, Any]:
    from sqlalchemy import select

    from opspilot.models import EvidenceGrant

    run = ctx.investigate(NW, "SO-1042")
    own = "ord:" + _order_id(ctx, NW, "SO-1042")
    foreign = "ord:" + _order_id(ctx, HB, "SO-1042")
    with ctx.session() as session:
        granted = set(
            session.scalars(
                select(EvidenceGrant.evidence_id).where(
                    EvidenceGrant.run_id == uuid.UUID(run["id"])
                )
            )
        )
    expect(own in granted, "the agent's tool did not read this organization's SO-1042")
    expect(foreign not in granted, "the agent's tool read another organization's SO-1042")
    return {"granted": len(granted)}


# ------------------------------------------------------------- approval and staleness
def a01(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    response = ctx.decide(action, approve=True, payload_hash="0" * 64)
    expect(
        response.status_code == 409 and response.json()["error_code"] == "approval.stale",
        f"mismatched hash returned {response.status_code}",
    )
    expect(
        ctx.action_status(action) == "pending" and ctx.outbox_events(action) == 0,
        "a stale hash changed state",
    )
    return {"status": response.status_code}


def a02(ctx: Context) -> dict[str, Any]:
    from opspilot.core.clock import utcnow
    from opspilot.models import ProposedAction

    action = ctx.proposal()
    with ctx.session() as session:
        row = session.get(ProposedAction, uuid.UUID(action["id"]))
        assert row is not None
        row.expires_at = utcnow() - timedelta(minutes=1)
    response = ctx.decide(action, approve=True)
    expect(
        response.status_code == 409 and response.json()["error_code"] == "approval.expired",
        f"expired approval returned {response.status_code}",
    )
    ctx.execute(action)
    expect(ctx.erp_tasks(action) == 0, "an expired proposal produced a task")
    return {"status": response.status_code}


def a03(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    ctx.touch_order(action)
    fresh = ctx.call("GET", f"/actions/{action['id']}", as_=(NW, "supervisor")).json()
    expect(fresh["source_changed"], "the screen did not flag the changed order")
    response = ctx.decide(action, approve=True)
    expect(
        response.status_code == 409 and response.json()["error_code"] == "approval.stale",
        f"stale source approval returned {response.status_code}",
    )
    return {"status": response.status_code}


def a04(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    first = ctx.decide(action, approve=True).status_code
    second = ctx.decide(action, approve=True)
    expect(first == 200, f"first approval returned {first}")
    expect(second.status_code == 409, f"second approval returned {second.status_code}")
    expect(ctx.outbox_events(action) == 1, "a repeated approval queued a second execution")
    return {"first": first, "second": second.status_code}


def a05(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    response = ctx.decide(action, approve=False)
    expect(
        response.status_code == 200 and response.json()["status"] == "rejected", "rejection failed"
    )
    ctx.execute(action)
    expect(
        ctx.outbox_events(action) == 0 and ctx.erp_tasks(action) == 0, "a rejection produced work"
    )
    return {"status": ctx.action_status(action)}


def a06(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    expect(ctx.decide(action, approve=True).status_code == 200, "approval failed")
    ctx.touch_order(action)
    ctx.execute(action)
    status = ctx.action_status(action)
    expect(ctx.erp_tasks(action) == 0, "a source change after approval still produced a task")
    expect(status in {"expired", "failed"}, f"invalidated execution ended as {status}")
    return {"status": status}


def a07(ctx: Context) -> dict[str, Any]:
    from opspilot.models import ProposedAction

    action = ctx.proposal()
    with ctx.session() as session:
        row = session.get(ProposedAction, uuid.UUID(action["id"]))
        assert row is not None
        row.payload_json = {**row.payload_json, "description": "Issue a full refund"}
    fresh = ctx.call("GET", f"/actions/{action['id']}", as_=(NW, "supervisor")).json()
    expect(not fresh["payload_valid"], "a tampered stored payload still verified")
    response = ctx.decide(action, approve=True)
    expect(
        response.status_code == 409, f"approving a tampered payload returned {response.status_code}"
    )
    return {"status": response.status_code}


def a08(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    expect(ctx.decide(action, approve=True).status_code == 200, "approval failed")
    ctx.execute(action)
    final = ctx.call("GET", f"/actions/{action['id']}", as_=(NW, "supervisor")).json()
    expect(
        final["status"] == "succeeded" and final["downstream_task_id"],
        f"execution ended as {final['status']}",
    )
    expect(ctx.erp_tasks(action) == 1, "approval did not produce exactly one task")
    return {"task": final["downstream_task_id"]}


def a09(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    expect(ctx.decide(action, approve=True).status_code == 200, "approval failed")
    ctx.execute(action, settings=ctx.settings.model_copy(update={"feature_writes_enabled": False}))
    held = ctx.action_status(action)
    expect(ctx.erp_tasks(action) == 0, "the disable-write switch did not stop the write")
    ctx.execute(action)
    expect(ctx.erp_tasks(action) == 1, "the held write did not complete once writes resumed")
    return {"while_disabled": held, "after": ctx.action_status(action)}


def a10(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    response = ctx.decide(action, approve=False, role="analyst")
    expect(response.status_code == 403, f"analyst rejection returned {response.status_code}")
    expect(ctx.action_status(action) == "pending", "an analyst rejection changed the proposal")
    return {"status": response.status_code}


# ---------------------------------------------------- duplicates, timeouts, crashes
def d01(ctx: Context) -> dict[str, Any]:
    from opspilot.services.runs import process_run

    run = ctx.investigate(NW)
    again = process_run(ctx.scripted, uuid.UUID(run["id"]))
    after = ctx.call("GET", f"/runs/{run['id']}", as_=(NW, "analyst")).json()
    expect(again is None, "a duplicate delivery ran the investigation again")
    expect(len(after["steps"]) == len(run["steps"]), "a duplicate delivery added steps")
    return {"steps": len(run["steps"])}


def _scenario_run(
    ctx: Context, scenario: str, *, delay_s: float = 0.0, settings: Any = None
) -> dict[str, Any]:
    from opspilot.services.runs import process_run

    run = ctx.investigate(NW, process=False)
    with ctx.provider(scenario, delay_s):
        process_run(settings or ctx.scripted, uuid.UUID(run["id"]))
    return dict(ctx.call("GET", f"/runs/{run['id']}", as_=(NW, "analyst")).json())


def d02(ctx: Context) -> dict[str, Any]:
    run = _scenario_run(ctx, "unavailable")
    expect(
        run["status"] == "failed" and run["incomplete_reason"] == "provider_unavailable",
        f"ended as {run['status']}/{run['incomplete_reason']}",
    )
    expect(run["turn_count"] <= ctx.scripted.run_max_turns, "retries exceeded the turn bound")
    return {"turns": run["turn_count"], "retries": run["retry_count"]}


def d03(ctx: Context) -> dict[str, Any]:
    run = _scenario_run(ctx, "tool_loop")
    expect(
        run["incomplete_reason"] in {"turn_limit", "tool_limit"},
        f"ended with {run['incomplete_reason']}",
    )
    expect(run["turn_count"] <= ctx.scripted.run_max_turns, "the turn bound was exceeded")
    expect(
        run["tool_call_count"] <= ctx.scripted.run_max_tool_calls, "the lookup bound was exceeded"
    )
    return {"turns": run["turn_count"], "tools": run["tool_call_count"]}


def d04(ctx: Context) -> dict[str, Any]:
    run = _scenario_run(ctx, "malformed_output")
    expect(
        run["status"] == "failed" and run["error_code"] == "provider.invalid_output",
        f"ended as {run['status']}/{run['error_code']}",
    )
    expect(
        run["result"] is None and run["proposal_id"] is None, "a malformed answer produced output"
    )
    return {"error_code": run["error_code"]}


def d05(ctx: Context) -> dict[str, Any]:
    tight = ctx.scripted.model_copy(update={"run_deadline_seconds": 0.3})
    started = time.monotonic()
    run = _scenario_run(ctx, "healthy", delay_s=0.5, settings=tight)
    elapsed = time.monotonic() - started
    expect(run["incomplete_reason"] == "deadline", f"ended with {run['incomplete_reason']}")
    expect(elapsed < 10, f"a 0.3 s deadline took {elapsed:.1f} s to stop")
    return {"elapsed_s": round(elapsed, 2)}


def d06(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    expect(ctx.decide(action, approve=True).status_code == 200, "approval failed")
    for _ in range(3):
        ctx.execute(action)
    expect(ctx.erp_tasks(action) == 1, f"three deliveries produced {ctx.erp_tasks(action)} tasks")
    return {"tasks": 1}


def _approved_and_claimed(ctx: Context) -> dict[str, Any]:
    """Approve, then persist the execution claim — the state a crashed worker leaves."""
    from opspilot.services.execution import prepare

    action = ctx.proposal()
    expect(ctx.decide(action, approve=True).status_code == 200, "approval failed")
    with ctx.session() as session:
        expect(
            prepare(session, ctx.settings, ctx.orgs[NW], uuid.UUID(action["id"])), "claim refused"
        )
    return action


def d07(ctx: Context) -> dict[str, Any]:
    from opspilot.domain.hashing import idempotency_key
    from opspilot.ops.client import OperationsClient
    from opspilot.ops.contracts import CreateTaskRequest

    action = _approved_and_claimed(ctx)
    payload = action["payload"]
    key = idempotency_key(ctx.orgs[NW], uuid.UUID(action["id"]), action["payload_hash"])
    # The ERP commits, then the worker "dies" before recording it locally.
    created = OperationsClient(ctx.settings).create(
        key,
        CreateTaskRequest(
            organization_id=ctx.orgs[NW],
            order_id=uuid.UUID(payload["order_id"]),
            assignee_id=uuid.UUID(payload["assignee_id"]),
            due_at=payload["due_at"],
            description=payload["description"],
        ),
    )
    ctx.execute(action)
    final = ctx.call("GET", f"/actions/{action['id']}", as_=(NW, "supervisor")).json()
    expect(final["status"] == "succeeded", f"recovery ended as {final['status']}")
    expect(final["downstream_task_id"] == created.task_id, "recovery recorded a different task")
    expect(ctx.erp_tasks(action) == 1, "recovery created a second task")
    return {"task": created.task_id}


def d08(ctx: Context) -> dict[str, Any]:
    action = _approved_and_claimed(ctx)
    expect(ctx.action_status(action) == "executing", "the claim was not durable")
    ctx.execute(action)
    expect(ctx.action_status(action) == "succeeded", "recovery after a pre-write crash failed")
    expect(ctx.erp_tasks(action) == 1, "recovery did not produce exactly one task")
    return {"tasks": 1}


def d09(ctx: Context) -> dict[str, Any]:
    from opspilot.services.runs import process_run

    run = ctx.investigate(NW, process=False)
    cancelled = ctx.call("POST", f"/runs/{run['id']}/cancel", as_=(NW, "analyst")).json()
    expect(cancelled["status"] == "cancelled", f"cancel left the run {cancelled['status']}")
    expect(process_run(ctx.scripted, uuid.UUID(run["id"])) is None, "a cancelled run was processed")
    after = ctx.call("GET", f"/runs/{run['id']}", as_=(NW, "analyst")).json()
    expect(after["steps"] == [] and after["status"] == "cancelled", "a cancelled run did work")
    return {"status": after["status"]}


def d10(ctx: Context) -> dict[str, Any]:
    action = ctx.proposal()
    expect(ctx.decide(action, approve=True).status_code == 200, "approval failed")
    unreachable = ctx.settings.model_copy(
        update={"ops_api_base_url": "http://127.0.0.1:9/ops", "ops_api_timeout_seconds": 1.0}
    )
    ctx.execute(action, settings=unreachable)
    uncertain = ctx.action_status(action)
    expect(
        uncertain == "reconciliation_required", f"an unreachable ERP left the action {uncertain}"
    )
    expect(ctx.erp_tasks(action) == 0, "a task appeared while the ERP was unreachable")
    ctx.execute(action)
    expect(
        ctx.action_status(action) == "succeeded" and ctx.erp_tasks(action) == 1,
        "reconciliation did not converge on one task",
    )
    return {"while_down": uncertain}


TR, AS, DC = "tenant_role", "approval_stale", "duplicates_crashes"
CHECKS: list[tuple[Spec, Callable[[Context], dict[str, Any]]]] = [
    (Spec("T01", TR, NW, "anonymous", "401", "Unauthenticated order read is refused."), t01),
    (Spec("T02", TR, NW, "analyst", "401", "A tampered token is refused without detail."), t02),
    (
        Spec("T03", TR, NW, "analyst", "403", "Another organization cannot be selected by header."),
        t03,
    ),
    (
        Spec(
            "T04",
            TR,
            NW,
            "analyst",
            "404",
            "Another organization's order id reads as missing, not forbidden.",
        ),
        t04,
    ),
    (
        Spec(
            "T05",
            TR,
            NW,
            "analyst",
            "distinct rows",
            "Colliding order numbers resolve per organization.",
        ),
        t05,
    ),
    (
        Spec(
            "T06",
            TR,
            NW,
            "analyst",
            "404",
            "Another organization's investigation reads as missing.",
        ),
        t06,
    ),
    (
        Spec(
            "T07", TR, NW, "supervisor", "404", "Another organization's proposal reads as missing."
        ),
        t07,
    ),
    (
        Spec(
            "T08",
            TR,
            HB,
            "supervisor",
            "404, unchanged",
            "A supervisor cannot approve another organization's proposal.",
        ),
        t08,
    ),
    (
        Spec(
            "T09",
            TR,
            NW,
            "analyst",
            "403, unchanged",
            "An analyst cannot approve, and nothing is queued.",
        ),
        t09,
    ),
    (
        Spec(
            "T10",
            TR,
            NW,
            "analyst",
            "404",
            "Another organization's policy passage reads as missing.",
        ),
        t10,
    ),
    (
        Spec(
            "T11",
            TR,
            NW,
            "analyst",
            "no rows",
            "Search never matches another organization's customers.",
        ),
        t11,
    ),
    (
        Spec(
            "T12",
            TR,
            HB,
            "analyst",
            "unchanged",
            "Metrics never count another organization's runs.",
        ),
        t12,
    ),
    (
        Spec(
            "T13",
            TR,
            NW,
            "anonymous",
            "404",
            "The dev issuer will not mint tokens for unknown people.",
        ),
        t13,
    ),
    (
        Spec(
            "T14",
            TR,
            NW,
            "supervisor",
            "404, unchanged",
            "Another organization's run cannot be cancelled.",
        ),
        t14,
    ),
    (
        Spec(
            "T15",
            TR,
            NW,
            "analyst",
            "own row only",
            "The agent's tools read only the caller's SO-1042.",
        ),
        t15,
    ),
    (
        Spec(
            "A01",
            AS,
            NW,
            "supervisor",
            "409 approval.stale",
            "A payload hash that differs from storage is refused.",
        ),
        a01,
    ),
    (
        Spec(
            "A02",
            AS,
            NW,
            "supervisor",
            "409 approval.expired",
            "An expired proposal cannot be approved or executed.",
        ),
        a02,
    ),
    (
        Spec(
            "A03",
            AS,
            NW,
            "supervisor",
            "409 approval.stale",
            "A changed source order invalidates approval.",
        ),
        a03,
    ),
    (
        Spec(
            "A04",
            AS,
            NW,
            "supervisor",
            "409, one event",
            "A decided proposal cannot be decided again.",
        ),
        a04,
    ),
    (
        Spec(
            "A05",
            AS,
            NW,
            "supervisor",
            "no work",
            "Rejection produces no outbox event and no task.",
        ),
        a05,
    ),
    (
        Spec(
            "A06",
            AS,
            NW,
            "supervisor",
            "no task",
            "A source change after approval stops execution.",
        ),
        a06,
    ),
    (
        Spec("A07", AS, NW, "supervisor", "409", "A tampered stored payload fails verification."),
        a07,
    ),
    (
        Spec(
            "A08",
            AS,
            NW,
            "supervisor",
            "one task",
            "Approval produces exactly one downstream task.",
        ),
        a08,
    ),
    (
        Spec(
            "A09",
            AS,
            NW,
            "supervisor",
            "held, then one task",
            "The disable-write switch holds approved work.",
        ),
        a09,
    ),
    (Spec("A10", AS, NW, "analyst", "403, unchanged", "An analyst cannot reject either."), a10),
    (
        Spec("D01", DC, NW, "worker", "no-op", "A duplicate investigation delivery does nothing."),
        d01,
    ),
    (
        Spec(
            "D02",
            DC,
            NW,
            "worker",
            "bounded failure",
            "An unavailable provider ends in a bounded incomplete run.",
        ),
        d02,
    ),
    (
        Spec(
            "D03",
            DC,
            NW,
            "worker",
            "bounded stop",
            "A model that never stops calling tools is stopped.",
        ),
        d03,
    ),
    (
        Spec(
            "D04",
            DC,
            NW,
            "worker",
            "rejected",
            "Output violating the schema is rejected, not rescued.",
        ),
        d04,
    ),
    (Spec("D05", DC, NW, "worker", "deadline", "The wall-clock deadline stops a slow run."), d05),
    (Spec("D06", DC, NW, "worker", "one task", "Three execute deliveries leave one task."), d06),
    (
        Spec(
            "D07",
            DC,
            NW,
            "worker",
            "same task",
            "A crash after the ERP write recovers the same task.",
        ),
        d07,
    ),
    (
        Spec(
            "D08",
            DC,
            NW,
            "worker",
            "one task",
            "A crash before the ERP write recovers with one task.",
        ),
        d08,
    ),
    (Spec("D09", DC, NW, "analyst", "no work", "A cancelled queued run is never processed."), d09),
    (
        Spec(
            "D10",
            DC,
            NW,
            "worker",
            "uncertain, then one task",
            "An unreachable ERP reconciles without duplicates.",
        ),
        d10,
    ),
]
DETERMINISTIC = [spec for spec, _ in CHECKS]
RUNNERS = {spec.case_id: runner for spec, runner in CHECKS}
