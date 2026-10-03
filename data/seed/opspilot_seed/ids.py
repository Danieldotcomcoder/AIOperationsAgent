"""Deterministic identifiers for seeded rows.

Production ids are UUIDv7 (time-ordered, unguessable). Seeded ids must instead be
*reproducible*: an evaluation case pins `evt:0192...` and that reference has to point
at the same shipment event after the database is dropped and regenerated, on another
machine, next month.

So seeds use UUIDv5 over a fixed namespace and a structured key. Same key, same id,
forever — and the key is human-readable in a debugger, which matters when a case fails
and you need to know which row it meant.
"""

from __future__ import annotations

import uuid

#: Fixed namespace. Changing it invalidates every stored evaluation reference, so it
#: is a breaking change requiring the labelled cases to be regenerated.
NAMESPACE = uuid.UUID("6f1b9e2c-0d3a-5f47-9c8e-1a2b3c4d5e6f")


def seeded_id(*parts: str | int) -> uuid.UUID:
    """A stable id from a structured key, e.g. ``seeded_id("northwind", "order", 42)``."""
    return uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts))
