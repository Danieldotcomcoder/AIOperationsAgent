import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from opspilot.agent.providers.base import ProviderResult, ToolCall, Usage
from opspilot.agent.schemas import InvestigationResult, ProposedActionDraft
from opspilot.auth.principal import Principal
from opspilot.core.clock import utcnow
from opspilot.core.config import get_settings
from opspilot.db.repository import TenantScopedRepository
from opspilot.db.session import session_scope
from opspilot.domain.enums import MembershipStatus, Role, RunStatus
from opspilot.models import (
    AgentRun,
    AuditEvent,
    EvidenceGrant,
    Membership,
    Order,
    Organization,
    ProposedAction,
    RunStep,
)
from opspilot.services import runs


@pytest.fixture
def committed_run(request):
    if not request.config.getoption("--integration"):
        pytest.skip("requires --integration and PostgreSQL")
    # Each concurrency test owns a new identity/run; no mutation of seeded users.
    with session_scope() as session:
        org = session.scalar(select(Organization).where(Organization.slug == "northwind-supply"))
        repo = TenantScopedRepository(session, org.id)
        member = repo.add(
            Membership(
                subject="cancel-test:" + uuid.uuid4().hex,
                email="test@example.com",
                display_name="Cancellation test",
                role=Role.analyst,
            )
        )
        session.flush()
        order = session.scalar(repo.query(Order).limit(1))
        run = repo.add(
            AgentRun(
                initiated_by=member.id,
                target_order_id=order.id,
                request_text="Cancellation concurrency test",
                status=RunStatus.queued,
                provider="fake",
                model_name="fake-1",
                prompt_version="test",
                trace_expires_at=utcnow() + timedelta(days=14),
            )
        )
        session.flush()
        data = SimpleNamespace(
            org=org.id,
            id=run.id,
            member=member.id,
            principal=Principal(
                member.id, org.id, member.subject, member.email, member.display_name, member.role
            ),
        )
    try:
        yield data
    finally:
        with session_scope() as session:
            for model in (RunStep, EvidenceGrant, ProposedAction):
                session.execute(
                    delete(model).where(model.organization_id == data.org, model.run_id == data.id)
                )
            session.execute(
                delete(AuditEvent).where(
                    AuditEvent.organization_id == data.org, AuditEvent.object_id == data.id
                )
            )
            session.execute(
                delete(AgentRun).where(AgentRun.organization_id == data.org, AgentRun.id == data.id)
            )
            session.execute(
                delete(Membership).where(
                    Membership.organization_id == data.org, Membership.id == data.member
                )
            )


@pytest.mark.parametrize("interrupt", ["cancel", "revoke"])
@pytest.mark.parametrize("reply_kind", ["proposal", "tools"])
def test_active_call_can_be_interrupted_without_proposal_or_more_tools(
    committed_run, monkeypatch, interrupt, reply_kind
):
    item = committed_run
    entered, resume = Event(), Event()

    class BlockingProvider:
        name = "fake"
        model = "fake-1"
        calls = 0

        def complete(self, **kwargs):
            self.calls += 1
            entered.set()
            assert resume.wait(10), "test must release the provider"
            if reply_kind == "tools":
                return ProviderResult(
                    text=None,
                    tool_calls=[ToolCall("1", "get_order_context", {"order_number": "SO-1042"})],
                    usage=Usage(123, 45),
                    model=self.model,
                )
            return ProviderResult(
                text=InvestigationResult(
                    summary="Finished",
                    proposed_action=ProposedActionDraft(
                        description="Do not persist this",
                        due_in_days=2,
                        rationale="Cancellation won",
                    ),
                ).model_dump_json(),
                usage=Usage(123, 45),
                model=self.model,
            )

    provider = BlockingProvider()
    monkeypatch.setattr(runs, "build_provider", lambda _: provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            runs.process_run, get_settings().model_copy(update={"llm_provider": "fake"}), item.id
        )
        try:
            assert entered.wait(10)
            with session_scope() as session:
                repo = TenantScopedRepository(session, item.org)
                assert repo.get(AgentRun, item.id).status == RunStatus.investigating
            # Duplicate delivery must return while the first worker is in its call.
            assert pool.submit(runs.process_run, get_settings(), item.id).result(timeout=3) is None

            def interrupt_run():
                with session_scope() as session:
                    if interrupt == "cancel":
                        result = runs.cancel_run(session, item.principal, item.id)
                        assert result.status == RunStatus.investigating
                    else:
                        TenantScopedRepository(session, item.org).get(
                            Membership, item.member
                        ).status = MembershipStatus.revoked

            pool.submit(interrupt_run).result(timeout=3)  # proves no provider-length row lock
        finally:
            resume.set()
        outcome = future.result(timeout=10)
    assert outcome.input_tokens == 123 and outcome.output_tokens == 45
    assert provider.calls == 1 and outcome.tool_calls == 0
    with session_scope() as session:
        repo = TenantScopedRepository(session, item.org)
        run = repo.get(AgentRun, item.id)
        assert run.status == (RunStatus.cancelled if interrupt == "cancel" else RunStatus.failed)
        assert run.result_json is None
        assert run.tokens_in == 123 and run.tokens_out == 45
        assert not repo.list_by(ProposedAction, run_id=item.id)


def test_cancel_before_worker_claim_never_calls_provider(committed_run, monkeypatch):
    item = committed_run
    monkeypatch.setattr(
        runs, "build_provider", lambda _: pytest.fail("cancelled run cannot call model")
    )
    with session_scope() as session:
        runs.cancel_run(session, item.principal, item.id)
    assert runs.process_run(get_settings(), item.id) is None


def test_cancellation_wins_final_persistence_race(committed_run, monkeypatch):
    from opspilot.agent.orchestrator import RunOutcome

    item = committed_run

    def finish(**kwargs):
        with session_scope() as session:
            runs.cancel_run(session, item.principal, item.id)
        return RunOutcome(
            result=InvestigationResult(
                summary="Too late",
                proposed_action=ProposedActionDraft(
                    description="Do not persist", due_in_days=1, rationale="Cancelled"
                ),
            ),
            input_tokens=11,
            model_name="fake-1",
        )

    monkeypatch.setattr(runs, "run_investigation", finish)
    runs.process_run(get_settings(), item.id)
    with session_scope() as session:
        repo = TenantScopedRepository(session, item.org)
        assert repo.get(AgentRun, item.id).status == RunStatus.cancelled
        assert not repo.list_by(ProposedAction, run_id=item.id)


def test_abandoned_claim_closes_without_respending(committed_run):
    from opspilot.workers.tasks import fail_abandoned_runs

    item = committed_run
    with session_scope() as session:
        run = TenantScopedRepository(session, item.org).get(AgentRun, item.id)
        run.status = RunStatus.investigating
        run.started_at = utcnow() - timedelta(minutes=10)
        run.cancel_requested_at = utcnow()
    assert fail_abandoned_runs.run() >= 1
    with session_scope() as session:
        run = TenantScopedRepository(session, item.org).get(AgentRun, item.id)
        assert run.status == RunStatus.cancelled and run.error_code == "worker.lost"
    assert runs.process_run(get_settings(), item.id) is None
