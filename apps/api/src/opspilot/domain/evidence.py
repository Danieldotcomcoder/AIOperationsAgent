"""Evidence identifiers — the grounding contract.

Every factual claim the model makes must carry an evidence id, and every evidence id
in a final answer must be one this run was actually shown. That check is set
membership against ``evidence_grants``, which is why a plausible-looking invented
citation cannot pass: the model has no way to guess a uuid that was handed to a
different run.

Format: ``<kind>:<identifier>`` with an optional ``#<locator>`` for a passage.

    ord:0192f3c1-...                      an order
    evt:0192f3c2-...                      a shipment event
    shp:0192f3c3-...                      a shipment
    tsk:0192f3c4-...                      an existing follow-up task
    pol:0192f3c5-...#3                    chunk 3 of a document version
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class EvidenceKind(StrEnum):
    order = "ord"
    shipment = "shp"
    event = "evt"
    task = "tsk"
    policy = "pol"


_PATTERN = re.compile(
    r"^(?P<kind>ord|shp|evt|tsk|pol):(?P<uuid>[0-9a-fA-F-]{36})(?:#(?P<locator>\d{1,4}))?$"
)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: EvidenceKind
    target_id: UUID
    locator: int | None = None

    def __str__(self) -> str:
        base = f"{self.kind.value}:{self.target_id}"
        return f"{base}#{self.locator}" if self.locator is not None else base


def format_ref(kind: EvidenceKind, target_id: UUID, locator: int | None = None) -> str:
    return str(EvidenceRef(kind, target_id, locator))


def parse_ref(raw: str) -> EvidenceRef | None:
    """Return the parsed reference, or ``None`` if the string is not a valid id.

    Returns ``None`` rather than raising: malformed citations are an expected model
    failure mode that the validator counts, not an exceptional condition.
    """
    match = _PATTERN.match(raw.strip())
    if match is None:
        return None
    locator = match.group("locator")
    try:
        target = UUID(match.group("uuid"))
    except ValueError:
        return None
    return EvidenceRef(
        kind=EvidenceKind(match.group("kind")),
        target_id=target,
        locator=int(locator) if locator is not None else None,
    )


def unsupported(cited: set[str], granted: set[str]) -> set[str]:
    """Citations that were never granted to this run: malformed ones included."""
    return {ref for ref in cited if parse_ref(ref) is None or ref not in granted}
