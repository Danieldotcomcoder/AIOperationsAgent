"""The verified identity of a caller.

A ``Principal`` can only be constructed from a verified token plus a live membership
row. It is the single carrier of ``organization_id`` and ``role`` through the
application — no other value is trusted for either (ADR 0002).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from opspilot.domain.enums import Role


@dataclass(frozen=True, slots=True)
class Claims:
    """Normalized token claims. Identical shape from Auth0 and from the dev issuer."""

    subject: str
    email: str
    issuer: str
    audience: str
    expires_at: int
    #: Requested organization. A *hint* only — it is resolved against memberships and
    #: rejected if the subject has no active membership there.
    organization_hint: str | None = None


@dataclass(frozen=True, slots=True)
class Principal:
    membership_id: uuid.UUID
    organization_id: uuid.UUID
    subject: str
    email: str
    display_name: str
    role: Role

    @property
    def is_supervisor(self) -> bool:
        return self.role is Role.supervisor

    def audit_actor(self) -> dict[str, str]:
        return {"actor_id": str(self.membership_id), "actor_role": self.role.value}
