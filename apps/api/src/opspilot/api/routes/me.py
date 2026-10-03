"""Who am I, and what may I do.

The SPA needs this before it can render anything: which organization it is acting in,
and whether to show approval controls at all. It returns exactly what the server
resolved — the client never derives its own role from a token claim, because a claim
the client can read is a claim the client can misread.

Hiding a control is a usability decision. The server still enforces the role on every
write; this endpoint does not grant anything.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from opspilot.auth.dependencies import CurrentPrincipal
from opspilot.core.clock import business_date
from opspilot.core.config import get_settings
from opspilot.domain.enums import Role

router = APIRouter()


class MeResponse(BaseModel):
    membership_id: uuid.UUID
    organization_id: uuid.UUID
    subject: str
    email: str
    display_name: str
    role: Role
    #: Convenience for the UI only. Enforcement lives in the approval routes.
    can_approve: bool
    #: The day "overdue" and "effective" are evaluated on — the same day the agent
    #: sees. The UI displays it rather than computing its own.
    business_date: date


@router.get("/me")
def me(principal: CurrentPrincipal) -> MeResponse:
    return MeResponse(
        membership_id=principal.membership_id,
        organization_id=principal.organization_id,
        subject=principal.subject,
        email=principal.email,
        display_name=principal.display_name,
        role=principal.role,
        can_approve=principal.is_supervisor,
        business_date=business_date(get_settings()),
    )
