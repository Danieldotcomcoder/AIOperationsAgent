"""Local development token issuer — NOT A DEPLOYABLE FEATURE (ADR 0008).

Three independent gates stand between this module and a deployed environment:

1. ``Settings`` refuses to construct with ``AUTH_MODE=dev`` outside development/test;
2. ``api/router.py`` only imports this module when ``app_env.is_local``;
3. the import below raises if that is somehow false.

It mints a token for a *seeded* membership. It cannot invent a subject, a role, or an
organization — everything it signs must already exist in the database, so it grants no
authority that an administrator has not already granted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, EmailStr

from opspilot.auth.dependencies import DbSession
from opspilot.auth.dev_keys import private_key_pem
from opspilot.auth.verifier import DevIssuerVerifier
from opspilot.core.config import get_settings
from opspilot.core.errors import ResourceNotFound
from opspilot.db.identity import dev_membership

if not get_settings().app_env.is_local:  # pragma: no cover - refuses to load
    raise RuntimeError("dev_auth must never be importable outside development/test")

router = APIRouter()


class DevTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Must match a seeded membership. No membership, no token.
    email: EmailStr


class DevTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - scheme name, not a secret
    expires_in: int
    organization_id: str
    role: str


@router.post("/token")
def issue_dev_token(body: DevTokenRequest, session: DbSession) -> DevTokenResponse:
    """Mint a token for a seeded membership.

    It grants nothing an administrator has not already granted: the subject, role and
    organization all come from an existing ``memberships`` row. With no such row there
    is no token — and the refusal is a 404, matching how the rest of the API declines
    to confirm what exists.
    """
    settings = get_settings()
    membership = dev_membership(session, str(body.email))
    if membership is None:
        raise ResourceNotFound("no active membership for that email")

    now = datetime.now(UTC)
    ttl = settings.dev_auth_token_ttl_seconds
    token = jwt.encode(
        {
            "sub": membership.subject,
            "email": membership.email,
            "iss": DevIssuerVerifier.ISSUER,
            "aud": DevIssuerVerifier.AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
            # A hint only. The server still resolves it against memberships on every
            # request, so editing it cannot widen access.
            "org_id": str(membership.organization_id),
        },
        private_key_pem(settings),
        algorithm="RS256",
    )
    return DevTokenResponse(
        access_token=token,
        expires_in=ttl,
        organization_id=str(membership.organization_id),
        role=membership.role.value,
    )
