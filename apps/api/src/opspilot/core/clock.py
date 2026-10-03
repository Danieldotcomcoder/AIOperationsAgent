"""Time is a dependency, not a global.

Every expiry, deadline and ``as_of`` comparison goes through a ``Clock``. The seed
generator and the evaluation harness run against a ``FrozenClock``, which is what
makes "is this proposal expired?" and "which policy version was effective?"
reproducible instead of dependent on when the suite happened to run.

Never call ``datetime.now()`` outside this module. Never use a naive datetime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from opspilot.core.config import Settings


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FrozenClock:
    """Deterministic clock for seeds, tests and evaluation snapshots."""

    instant: datetime

    def now(self) -> datetime:
        if self.instant.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime.")
        return self.instant

    def advance(self, **delta: float) -> None:
        self.instant = self.instant + timedelta(**delta)


_default: Clock = SystemClock()


def get_clock() -> Clock:
    return _default


def utcnow() -> datetime:
    return _default.now()


def business_date(settings: Settings) -> date:
    """The day "overdue" and "effective" mean, for every reader at once.

    The inbox, the order detail, the agent's tools and prompt, and the knowledge
    screen all ask this one function, so the analyst and the agent cannot be looking
    at different days. ``OPERATIONS_AS_OF`` pins it for a frozen dataset.
    """
    return settings.operations_as_of or utcnow().date()
