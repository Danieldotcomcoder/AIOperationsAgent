"""Pre-tenant membership lookup, restricted to verified subjects or the local issuer.

These are the bootstrap exceptions to tenant scoping: no tenant context exists until
the caller's own active memberships have been resolved. Never use for business data.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.domain.enums import MembershipStatus
from opspilot.models import Membership


def active_memberships(session: Session, subject: str) -> Sequence[Membership]:
    return session.scalars(
        select(Membership).where(
            Membership.subject == subject,
            Membership.status == MembershipStatus.active,
        )
    ).all()


def dev_membership(session: Session, email: str) -> Membership | None:
    return session.scalars(
        select(Membership)
        .where(
            Membership.email == email,
            Membership.status == MembershipStatus.active,
        )
        .order_by(Membership.id)
        .limit(1)
    ).first()
