import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import redis

from opspilot.core.config import Settings, get_settings
from opspilot.core.errors import QuotaExceeded
from opspilot.services.budget import BudgetKeys, BudgetService


@pytest.fixture
def budget_redis(request, monkeypatch):
    if not request.config.getoption("--integration"):
        pytest.skip("requires --integration and local Redis")
    prefix = "budget-test:" + uuid.uuid4().hex + ":"
    original = BudgetKeys.for_now

    def keys(org, member, now):
        result = original(org, member, now)
        return BudgetKeys(prefix + result.day, prefix + result.month, prefix + result.user_runs)

    monkeypatch.setattr(BudgetKeys, "for_now", staticmethod(keys))
    with redis.Redis.from_url(get_settings().redis_url) as client:
        yield client
        keys_to_delete = list(client.scan_iter(match=prefix + "*"))
        if keys_to_delete:
            client.delete(*keys_to_delete)


@pytest.mark.parametrize("limit", ["daily", "monthly", "user"])
def test_atomic_budget_admission_under_concurrency(budget_redis, limit):
    config = Settings(
        _env_file=None,
        budget_daily_usd=0.05 if limit == "daily" else 10,
        budget_monthly_usd=0.05 if limit == "monthly" else 10,
        budget_per_user_daily_runs=1 if limit == "user" else 100,
    )
    org, member = uuid.uuid4(), uuid.uuid4()

    def reserve(_):
        try:
            return BudgetService(config, budget_redis, reservation_usd=Decimal("0.05")).reserve(
                org, member
            )
        except QuotaExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = [r for r in pool.map(reserve, range(16)) if r]
    assert len(receipts) == 1
    day, month, user, _ = receipts[0]["keys"]
    assert list(map(int, budget_redis.mget(day, month, user))) == [50000, 50000, 1]


def test_settlement_uses_original_windows_amount_and_is_idempotent(budget_redis, monkeypatch):
    org, member = uuid.uuid4(), uuid.uuid4()
    config = Settings(_env_file=None)
    guard = BudgetService(config, budget_redis, reservation_usd=Decimal("0.05"))
    receipt = guard.reserve(org, member)
    original_keys = list(receipt["keys"])
    # A new worker after midnight/config changes must use the persisted receipt.
    monkeypatch.setattr(BudgetKeys, "for_now", lambda *a: pytest.fail("must use original window"))
    worker = BudgetService(config, budget_redis, reservation_usd=Decimal("0.9"))
    for _ in range(3):
        worker.reconcile(org, member, Decimal("0.02"), receipt=receipt)
    worker.release(org, member, receipt=receipt)
    assert list(map(int, budget_redis.mget(*original_keys[:3]))) == [20000, 20000, 1]


def test_release_does_not_refund_twice_or_resurrect_expired_windows(budget_redis):
    org, member = uuid.uuid4(), uuid.uuid4()
    guard = BudgetService(Settings(_env_file=None), budget_redis)
    receipt = guard.reserve(org, member)
    budget_redis.delete(receipt["keys"][0])
    guard.release(org, member)
    guard.release(org, member)
    assert budget_redis.get(receipt["keys"][0]) is None
    assert int(budget_redis.get(receipt["keys"][1])) == 0
    assert int(budget_redis.get(receipt["keys"][2])) == 0


def test_embedding_reservation_works_at_full_run_quota(budget_redis):
    org, member = uuid.uuid4(), uuid.uuid4()
    config = Settings(_env_file=None, budget_per_user_daily_runs=1)
    BudgetService(config, budget_redis).reserve(org, member)
    guard = BudgetService(config, budget_redis, reservation_usd=Decimal("0.0001"), count_run=False)
    receipt = guard.reserve(org, member)
    assert int(budget_redis.get(receipt["keys"][2])) == 1


def test_redis_failure_blocks_spend():
    client = MagicMock()
    client.eval.side_effect = redis.ConnectionError("unavailable")
    with pytest.raises(QuotaExceeded):
        BudgetService(Settings(_env_file=None), client).reserve(uuid.uuid4(), uuid.uuid4())


def test_quota_keys_separate_tenant_and_member():
    now = datetime(2026, 9, 24, tzinfo=UTC)
    org, member = uuid.uuid4(), uuid.uuid4()
    first = BudgetKeys.for_now(org, member, now)
    second = BudgetKeys.for_now(uuid.uuid4(), member, now)
    assert first.user_runs != second.user_runs
    assert first.day == second.day  # spend cap is deliberately application-wide
