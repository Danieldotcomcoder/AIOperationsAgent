"""FastAPI authorization dependencies.

Every business route depends on ``CurrentPrincipal``. There is no route that reads
tenant data without one, and there is no way to obtain a ``Principal`` except by
presenting a verified token that matches an active membership row.

Use ``RequireSupervisor`` for approval routes. Role is checked here *and* re-checked
in the service and again in the execute worker — three times, because each of those
runs at a different moment and the answer can change between them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from opspilot.auth.principal import Claims, Principal
from opspilot.auth.verifier import TokenVerifier
from opspilot.core.errors import NotAuthenticated, NotAuthorized
from opspilot.db.identity import active_memberships
from opspilot.db.session import db_session
from opspilot.domain.enums import Role

#: ``scope="function"`` closes the session — and so commits — when the endpoint
#: returns, *before* the response is sent. The default (request) scope commits after
#: the response is on the wire, which would report 202/200 for work that a failed
#: commit then discarded. Every use must share this one object: FastAPI caches
#: dependencies per (callable, scope), so mixing scopes would open two sessions.
_SESSION = Depends(db_session, scope="function")


def get_verifier(request: Request) -> TokenVerifier:
    """Built once at startup and held on app state; JWKS caching depends on reuse."""
    verifier: TokenVerifier | None = getattr(request.app.state, "verifier", None)
    if verifier is None:  # pragma: no cover - startup wiring bug
        raise RuntimeError("token verifier is not configured")
    return verifier


def get_claims(
    verifier: Annotated[TokenVerifier, Depends(get_verifier)],
    authorization: Annotated[str | None, Header()] = None,
) -> Claims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise NotAuthenticated("missing bearer token")
    return verifier.verify(authorization.split(" ", 1)[1].strip())


def get_principal(
    claims: Annotated[Claims, Depends(get_claims)],
    session: Annotated[Session, _SESSION],
    x_organization: Annotated[str | None, Header(alias="X-Organization")] = None,
) -> Principal:
    """Resolve the caller's organization and role from the database.

    ``X-Organization`` selects *among the caller's own memberships* when a subject
    belongs to several. It is never trusted on its own: an organization the subject
    has no active membership in produces the same result as one that does not exist.
    """
    memberships = active_memberships(session, claims.subject)
    if not memberships:
        raise NotAuthorized("no active membership for this subject")

    requested = x_organization or claims.organization_hint
    if requested:
        chosen = next((m for m in memberships if str(m.organization_id) == requested), None)
        if chosen is None:
            raise NotAuthorized("no active membership in the requested organization")
    elif len(memberships) == 1:
        chosen = memberships[0]
    else:
        raise NotAuthorized("several memberships; X-Organization is required")

    return Principal(
        membership_id=chosen.id,
        organization_id=chosen.organization_id,
        subject=chosen.subject,
        email=chosen.email,
        display_name=chosen.display_name,
        role=chosen.role,
    )


def require_supervisor(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if principal.role is not Role.supervisor:
        raise NotAuthorized("this action requires the supervisor role")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
RequireSupervisor = Annotated[Principal, Depends(require_supervisor)]
DbSession = Annotated[Session, _SESSION]
