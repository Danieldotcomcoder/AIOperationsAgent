"""One business date for the inbox, the order detail, /me and the agent."""

from datetime import date

from fastapi.testclient import TestClient

from opspilot.api.routes import me as me_routes
from opspilot.api.routes import orders as order_routes
from opspilot.auth.dependencies import get_principal
from opspilot.core.clock import business_date, utcnow
from opspilot.core.config import Settings
from opspilot.db.session import db_session
from opspilot.domain.enums import RunStatus
from opspilot.main import create_app
from opspilot.services import orders as order_service
from opspilot.services import runs

SEED_DAY = date(2026, 9, 12)


def test_blank_business_date_is_today_and_a_pinned_one_wins() -> None:
    assert business_date(Settings(_env_file=None)) == utcnow().date()
    assert business_date(Settings(_env_file=None, operations_as_of="  ")) == utcnow().date()
    assert business_date(Settings(_env_file=None, operations_as_of="2026-09-12")) == SEED_DAY


def test_inbox_and_agent_evaluate_the_same_day(db, case, monkeypatch) -> None:
    pinned = Settings(_env_file=None, operations_as_of=SEED_DAY.isoformat())
    for module in (order_routes, me_routes):
        monkeypatch.setattr(module, "get_settings", lambda: pinned)
    app = create_app()
    app.dependency_overrides[db_session] = lambda: db
    app.dependency_overrides[get_principal] = lambda: case.principal
    with TestClient(app) as client:
        assert client.get("/me").json()["business_date"] == SEED_DAY.isoformat()
        shown = client.get(f"/orders/{case.order.id}").json()["days_overdue"]
    expected = order_service.get_order_detail(db, case.repo, case.order.id, as_of=SEED_DAY)
    assert shown == expected.summary.days_overdue

    # The agent is told the same day in its prompt, and its tools are evaluated on it.
    case.run.status = RunStatus.investigating
    case.run.request_text = "Which orders are overdue?"
    case.run.target_order_id = None
    db.flush()
    runs.execute_run(db, pinned, case.run.id, case.org.id)
    steps = runs.get_run_steps(db, case.principal, case.run.id)
    listed = next(step for step in steps if step.tool_name == "list_overdue_orders")
    assert listed.arguments_json == {"as_of_date": SEED_DAY.isoformat(), "limit": 10}
