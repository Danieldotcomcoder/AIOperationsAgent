"""Week 7 surfaces: metrics summary, run history, order activity and system info."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from opspilot.auth.dependencies import get_principal
from opspilot.core.config import get_settings
from opspilot.db.session import db_session
from opspilot.domain.enums import Role, RunStatus
from opspilot.main import create_app
from opspilot.models import AgentRun, Membership, Organization
from opspilot.services import metrics


@pytest.fixture
def client(db, case):
    app = create_app()
    app.dependency_overrides[db_session] = lambda: db
    app.dependency_overrides[get_principal] = lambda: case.principal
    with TestClient(app) as test_client:
        yield test_client


def _run(**values) -> AgentRun:
    defaults = {
        "request_text": "metrics test",
        "prompt_version": "test",
        "provider": "fake",
        "model_name": "fake-1",
    }
    return AgentRun(**{**defaults, **values})


def test_percentile_is_nearest_rank() -> None:
    assert metrics.percentile([], 95) == 0
    assert metrics.percentile([7.0], 50) == 7.0
    assert metrics.percentile([float(v) for v in range(1, 21)], 95) == 19.0
    assert metrics.percentile([3.0, 1.0, 2.0], 100) == 3.0


def test_metrics_count_only_the_callers_organization(db, case) -> None:
    before = metrics.summary(db, case.principal, days=7)

    other = Organization(slug=f"metrics-{uuid.uuid4()}", name="Other tenant")
    db.add(other)
    db.flush()
    stranger = Membership(
        organization_id=other.id,
        subject=f"metrics|{uuid.uuid4()}",
        email="other@example.com",
        display_name="Other",
        role=Role.analyst,
    )
    db.add(stranger)
    db.flush()
    db.add(
        _run(
            organization_id=other.id,
            initiated_by=stranger.id,
            status=RunStatus.failed,
            error_code="provider.invalid_output",
        )
    )
    db.flush()
    assert metrics.summary(db, case.principal, days=7) == before

    case.repo.add(
        _run(
            initiated_by=case.member.id,
            status=RunStatus.completed,
            cost_usd=Decimal("0.010000"),
            duration_ms=1500,
            result_json={"validation": {"cited": 4, "unsupported": ["ord:x"]}},
        )
    )
    db.flush()
    after = metrics.summary(db, case.principal, days=7)
    assert after["run_count"] == before["run_count"] + 1
    assert after["completed"] == before["completed"] + 1
    assert after["citations_cited"] == before["citations_cited"] + 4
    assert after["citations_unsupported"] == before["citations_unsupported"] + 1
    assert after["runs_with_unsupported_citations"] == before["runs_with_unsupported_citations"] + 1
    with pytest.raises(ValueError):
        metrics.summary(db, case.principal, days=0)


def test_metrics_route_bounds_its_window(client) -> None:
    assert client.get("/metrics/summary?days=0").status_code == 422
    assert client.get("/metrics/summary?days=91").status_code == 422
    body = client.get("/metrics/summary?days=30").json()
    assert body["days"] == 30 and body["run_count"] >= 1
    assert {"p95_queue_wait_ms", "approval_rate", "tool_outcomes", "by_model"} <= body.keys()


def test_run_history_carries_order_proposal_and_feedback(client, case) -> None:
    rows = client.get("/runs?status_filter=needs_review&limit=100").json()
    row = next(item for item in rows if item["id"] == str(case.run.id))
    assert row["order_number"] == case.order.order_number
    assert row["proposal_status"] == "pending" and row["initiated_by_me"]
    assert all(item["status"] == "needs_review" for item in rows)

    response = client.post(
        f"/runs/{case.run.id}/feedback",
        json={"usefulness": "partly", "failure_class": "missing_policy"},
    )
    assert response.status_code == 201
    detail = client.get(f"/runs/{case.run.id}").json()
    assert detail["proposal_id"] == str(case.action.id)
    assert [(f["usefulness"], f["mine"]) for f in detail["feedback"]] == [("partly", True)]
    listed = next(
        item for item in client.get("/runs?limit=100").json() if item["id"] == str(case.run.id)
    )
    assert listed["feedback_ratings"] == ["partly"]


def test_feedback_waits_for_a_finished_investigation(client, case, db) -> None:
    case.run.status = RunStatus.investigating
    db.flush()
    response = client.post(f"/runs/{case.run.id}/feedback", json={"usefulness": "useful"})
    assert response.status_code == 409


def test_order_inbox_filters_and_activity(client, case) -> None:
    number = case.order.order_number
    found = client.get(f"/orders?q={number}&limit=100").json()
    assert [row["order_number"] for row in found] == [number]
    assert client.get("/orders?q=%25&limit=5").json() == []  # % is literal, not a wildcard
    flagged = client.get("/orders?flagged_only=true&limit=100").json()
    assert flagged and all(row["has_open_exception"] for row in flagged)
    counts = client.get("/orders/counts").json()
    assert counts["total"] >= counts["overdue"] >= 0
    detail = client.get(f"/orders/{case.order.id}").json()
    assert str(case.run.id) in {run["id"] for run in detail["investigations"]}
    assert isinstance(detail["followup_tasks"], list)


def test_system_info_names_configuration_but_no_secrets(client) -> None:
    body = client.get("/system").json()
    assert body["scripted_model"] is True and body["llm_provider"] == "fake"
    settings = get_settings()
    assert body["bounds"] == {
        "max_turns": settings.run_max_turns,
        "max_tool_calls": settings.run_max_tool_calls,
        "deadline_seconds": settings.run_deadline_seconds,
    }
    flat = str(body).lower()
    assert not any(word in flat for word in ("api_key", "secret", "token", "http"))
