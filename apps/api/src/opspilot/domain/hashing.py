"""Canonical serialization and payload hashing (ADR 0004).

A supervisor approves an exact artifact. That only means something if "exact" is
computable, so a payload has exactly one byte representation:

* object keys sorted;
* no insignificant whitespace;
* datetimes normalized to UTC, second precision, ``Z`` suffix;
* decimals as their plain string form, never a float;
* ``None`` preserved (a missing key and a null key are different payloads).

Change any rule here and every stored ``payload_hash`` becomes unverifiable. If it
must change, it is a migration that re-hashes and a new ADR, not an edit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime in a hashable payload")
        return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, float):
        raise TypeError("float is not permitted in a hashable payload; use Decimal")
    return value


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        _normalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def payload_hash(payload: dict[str, Any]) -> str:
    """sha256 of the canonical form, hex encoded. Stable across processes and hosts."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def content_hash(raw: str) -> str:
    """Document content hash. Normalizes line endings and trailing whitespace so that
    a re-upload differing only in CRLF is recognized as the same version."""
    normalized = "\n".join(line.rstrip() for line in raw.replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def idempotency_key(organization_id: UUID, proposed_action_id: UUID, payload_hash_hex: str) -> str:
    """Deterministic downstream key (ADR 0003).

    Derivable from stored state alone, so a retry after a crash — in a different
    process, on a different day — produces the identical key and the downstream
    returns the original task instead of creating a second one.
    """
    material = f"{organization_id}|{proposed_action_id}|{payload_hash_hex}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
