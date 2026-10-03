"""Spend and quota control.

Reserve before the call, reconcile after. Doing it the other way round — charge what
was actually used — means a burst of concurrent runs can each pass the check and
collectively blow the budget, because none of them sees the others' spend until they
finish.

All counters are Redis integers in micro-dollars. Integers, not floats: ``INCRBYFLOAT``
accumulates representation error over thousands of small increments, and a budget that
drifts is a budget nobody trusts.

Fail closed. If Redis is unreachable we do not know what has been spent, and "we do not
know" must not mean "go ahead".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import redis

from opspilot.core.config import Settings
from opspilot.core.errors import QuotaExceeded
from opspilot.core.logging import get_logger

log = get_logger(__name__)

MICRO = Decimal("1000000")

#: Assumed cost of a run at reservation time, before real usage is known. Deliberately
#: generous: under-reserving lets concurrent runs slip past the ceiling together.
RESERVATION_USD = Decimal("0.05")

#: Counters outlive their window so a reconciliation arriving late still lands.
_DAY_TTL = 60 * 60 * 48
_MONTH_TTL = 60 * 60 * 24 * 40

# Admission and settlement are each a single atomic operation. The receipt captures
# the original UTC windows, so a midnight/month boundary cannot refund a new budget.
_RESERVE = """
local amount, daily, monthly, count, quota = tonumber(ARGV[1]), tonumber(ARGV[2]),
    tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5])
if tonumber(redis.call('GET', KEYS[1]) or '0') + amount > daily then return 1 end
if tonumber(redis.call('GET', KEYS[2]) or '0') + amount > monthly then return 2 end
if count > 0 and tonumber(redis.call('GET', KEYS[3]) or '0') + count > quota then return 3 end
redis.call('INCRBY', KEYS[1], amount)
redis.call('EXPIRE', KEYS[1], ARGV[6])
redis.call('INCRBY', KEYS[2], amount)
redis.call('EXPIRE', KEYS[2], ARGV[7])
redis.call('INCRBY', KEYS[3], count)
redis.call('EXPIRE', KEYS[3], ARGV[6])
redis.call('SET', KEYS[4], 'reserved', 'EX', ARGV[7])
return 0
"""
_SETTLE = """
if redis.call('GET', KEYS[4]) ~= 'reserved' then return 0 end
for i = 1, 2 do
    if redis.call('EXISTS', KEYS[i]) == 1 then redis.call('INCRBY', KEYS[i], ARGV[1]) end
end
if tonumber(ARGV[2]) > 0 and redis.call('EXISTS', KEYS[3]) == 1 then
    redis.call('DECRBY', KEYS[3], ARGV[2])
end
redis.call('SET', KEYS[4], 'settled', 'KEEPTTL')
return 1
"""


@dataclass(frozen=True, slots=True)
class BudgetKeys:
    day: str
    month: str
    user_runs: str

    @staticmethod
    def for_now(organization_id: UUID, membership_id: UUID, now: datetime) -> BudgetKeys:
        day = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        return BudgetKeys(
            day=f"budget:day:{day}",
            month=f"budget:month:{month}",
            # Per-user run counts are scoped by organization as well, so one tenant
            # cannot exhaust another's allowance through a shared subject.
            user_runs=f"quota:runs:{organization_id}:{membership_id}:{day}",
        )


class BudgetService:
    def __init__(
        self,
        settings: Settings,
        client: redis.Redis | None = None,
        *,
        reservation_usd: Decimal | None = None,
        count_run: bool = True,
    ) -> None:
        self._settings = settings
        self._redis = client or redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        self._count_run = int(count_run)
        self.receipt: dict[str, Any] | None = None
        from opspilot.agent.pricing import configured_cost

        self._reservation = (
            reservation_usd
            if reservation_usd is not None
            else max(
                RESERVATION_USD,
                configured_cost(
                    settings,
                    settings.llm_model,
                    settings.run_max_input_tokens * settings.run_max_turns,
                    settings.run_max_output_tokens * settings.run_max_turns,
                ),
            )
        )

    def reserve(self, organization_id: UUID, membership_id: UUID) -> dict[str, Any]:
        """Claim headroom for one run, or raise ``QuotaExceeded``.

        Lua checks and increments together; rejected admissions change no counters.
        """
        keys = BudgetKeys.for_now(organization_id, membership_id, datetime.now(UTC))
        amount = int(self._reservation * MICRO)
        receipt: dict[str, Any] = {
            "keys": [
                keys.day,
                keys.month,
                keys.user_runs,
                keys.month + ":reservation:" + uuid4().hex,
            ],
            "amount": amount,
            "count": self._count_run,
        }
        try:
            result = cast(
                int,
                self._redis.eval(
                    _RESERVE,
                    4,
                    *receipt["keys"],
                    str(amount),
                    str(int(Decimal(str(self._settings.budget_daily_usd)) * MICRO)),
                    str(int(Decimal(str(self._settings.budget_monthly_usd)) * MICRO)),
                    str(self._count_run),
                    str(self._settings.budget_per_user_daily_runs),
                    str(_DAY_TTL),
                    str(_MONTH_TTL),
                ),
            )
        except redis.RedisError as exc:
            # We cannot know what has been spent, so we do not spend more.
            log.error("budget.unavailable", error=type(exc).__name__)
            raise QuotaExceeded("spend controls are unavailable") from exc

        if result != 0:
            raise QuotaExceeded(
                {
                    1: "daily spend limit reached",
                    2: "monthly spend limit reached",
                    3: "daily run quota reached for this user",
                }.get(result, "spend rejected")
            )
        self.receipt = receipt
        return receipt

    def reconcile(
        self,
        organization_id: UUID,
        membership_id: UUID,
        actual_usd: Decimal,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        """Replace the reservation with what the run actually cost.

        Best effort: a failure here over-counts spend, which fails safe. Never let it
        break a run that has already completed successfully.
        """
        receipt = receipt or self.receipt
        if receipt is None:
            # Legacy runs have no stable window/amount receipt. Retain their reserve
            # rather than guessing and undercounting spend after config/time changes.
            return
        self._settle(receipt, int(actual_usd * MICRO), release_quota=False)

    def _settle(self, receipt: dict[str, Any], actual: int, *, release_quota: bool) -> None:
        try:
            self._redis.eval(
                _SETTLE,
                4,
                *receipt["keys"],
                str(actual - receipt["amount"]),
                str(receipt["count"] if release_quota else 0),
            )
        except redis.RedisError as exc:  # pragma: no cover - best effort
            log.warning("budget.reconcile_failed", error=type(exc).__name__)

    def release(
        self, organization_id: UUID, membership_id: UUID, *, receipt: dict[str, Any] | None = None
    ) -> None:
        """Return an unused reservation, e.g. when the run never started."""
        receipt = receipt or self.receipt
        if receipt is not None:
            self._settle(receipt, 0, release_quota=True)

    def snapshot(self) -> dict[str, float]:
        """Current spend, for the metrics screen. Zeros when Redis is unreachable."""
        keys = BudgetKeys.for_now(UUID(int=0), UUID(int=0), datetime.now(UTC))
        try:
            day, month = cast(list[bytes | None], self._redis.mget(keys.day, keys.month))
        except redis.RedisError:  # pragma: no cover - display only
            return {"day_usd": 0.0, "month_usd": 0.0}
        return {
            "day_usd": float(Decimal(int(day or 0)) / MICRO),
            "month_usd": float(Decimal(int(month or 0)) / MICRO),
        }
