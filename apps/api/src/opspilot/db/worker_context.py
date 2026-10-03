"""Internal ID-to-tenant discovery. Returns no business content or entitlements.

Only worker messages use this lookup; all subsequent reads are repository-scoped.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.models import AgentRun


def run_organization(session: Session, run_id: UUID) -> UUID | None:
    return session.scalar(select(AgentRun.organization_id).where(AgentRun.id == run_id))
