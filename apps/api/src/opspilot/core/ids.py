"""Identifier generation.

UUIDv7: time-ordered, so primary-key inserts stay at the right edge of the B-tree
instead of scattering, and rows sort by creation without a second column. Random tail
keeps them unguessable, which matters because ids are exposed in URLs.
"""

from __future__ import annotations

import os
import time
import uuid


def new_id() -> uuid.UUID:
    """A UUIDv7 (RFC 9562): 48-bit millisecond timestamp + 74 bits of randomness."""
    unix_ms = int(time.time() * 1000)
    rand = os.urandom(10)
    b = bytearray(16)
    b[0:6] = unix_ms.to_bytes(6, "big")
    b[6:16] = rand
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(b))


def timestamp_ms(value: uuid.UUID) -> int:
    """Creation time embedded in a UUIDv7, in milliseconds since the epoch."""
    return int.from_bytes(value.bytes[0:6], "big")
