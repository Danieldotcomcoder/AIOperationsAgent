import json
import os
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from opspilot.auth.principal import Principal
from opspilot.core.clock import utcnow
from opspilot.core.config import get_settings
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import ActionType, Role, RunStatus
from opspilot.domain.hashing import canonical_json, payload_hash
from opspilot.models import AgentRun, Membership, Order, Organization, ProposedAction

#: The root .env may select live providers. A test must never spend quota or change
#: meaning with someone's model choice. An explicitly exported variable still wins.
HERMETIC_ENV = {"LLM_PROVIDER": "fake", "EMBEDDING_PROVIDER": "fake", "OPERATIONS_AS_OF": ""}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--integration", action="store_true", help="Use migrated local PostgreSQL")


def pytest_configure(config: pytest.Config) -> None:
    # Runs before test modules are imported, and so before Settings is first cached.
    for key, value in HERMETIC_ENV.items():
        os.environ.setdefault(key, value)


@pytest.fixture
def db(request: pytest.FixtureRequest) -> Iterator[Session]:
    if not request.config.getoption("--integration"):
        pytest.skip("requires --integration and migrated local PostgreSQL")
    engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection) as session:
            yield session
        transaction.rollback()
    engine.dispose()


@pytest.fixture
def case(db):
    org = db.scalar(select(Organization).where(Organization.slug == "northwind-supply"))
    repo = TenantScopedRepository(db, org.id)
    member = db.scalar(repo.query(Membership).where(Membership.role == Role.supervisor).limit(1))
    order = db.scalar(repo.query(Order).limit(1))
    principal = Principal(
        member.id, org.id, member.subject, member.email, member.display_name, member.role
    )
    run = repo.add(
        AgentRun(
            initiated_by=member.id,
            target_order_id=order.id,
            request_text="test",
            status=RunStatus.needs_review,
            prompt_version="test",
            provider="fake",
            model_name="fake-1",
        )
    )
    db.flush()
    payload = json.loads(
        canonical_json(
            {
                "action_type": "create_followup_task",
                "order_id": order.id,
                "order_number": order.order_number,
                "assignee_id": member.id,
                "due_at": utcnow() + timedelta(days=2),
                "description": "Synthetic approval test",
            }
        )
    )
    action = repo.add(
        ProposedAction(
            run_id=run.id,
            action_type=ActionType.create_followup_task,
            payload_json=payload,
            payload_hash=payload_hash(payload),
            source_record_type="order",
            source_record_id=order.id,
            source_record_version=order.record_version,
            expires_at=utcnow() + timedelta(hours=24),
        )
    )
    db.flush()
    return SimpleNamespace(
        org=org, repo=repo, member=member, order=order, principal=principal, run=run, action=action
    )
