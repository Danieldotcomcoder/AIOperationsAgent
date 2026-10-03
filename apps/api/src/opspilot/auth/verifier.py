"""Token verification (ADR 0008).

Two implementations behind one interface. Everything downstream — membership
resolution, role checks, tenant scoping, audit — is identical in both modes, so the
local development path exercises the real authorization code rather than bypassing it.

STATUS: interface and Auth0 skeleton are defined; JWKS fetch/cache and the dev issuer
key handling are the first task of Week 1 (see docs/HANDOFF.md).
"""

from __future__ import annotations

from typing import Protocol

import jwt
from jwt import PyJWKClient

from opspilot.auth.principal import Claims
from opspilot.core.config import AppEnv, Settings
from opspilot.core.errors import NotAuthenticated

#: RS256 only. ``none`` and the HS* family are rejected outright — accepting a
#: symmetric algorithm against a public key is the classic JWT forgery.
ALLOWED_ALGORITHMS = ["RS256"]


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Claims: ...


class Auth0Verifier:
    """Validates signature, issuer, audience, expiry and algorithm against JWKS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._issuer = settings.auth0_issuer
        self._audience = settings.auth0_audience
        # PyJWKClient caches keys and refetches on an unknown kid, which is what makes
        # key rotation a non-event.
        self._jwks = PyJWKClient(
            f"https://{settings.auth0_domain}/.well-known/jwks.json",
            cache_keys=True,
            lifespan=3600,
        )

    def verify(self, token: str) -> Claims:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            # Never echo the library's message to the client; log it, return a generic
            # failure. Verification detail is a probing oracle.
            raise NotAuthenticated("token verification failed") from exc

        return Claims(
            subject=payload["sub"],
            email=payload.get("email", ""),
            issuer=payload["iss"],
            audience=self._audience,
            expires_at=int(payload["exp"]),
            organization_hint=payload.get("org_id"),
        )


class DevIssuerVerifier:
    """Local development only.

    Verifies tokens minted by ``POST /auth/dev/token`` with a locally generated RSA
    keypair. Refuses to construct outside development/test, and the route that mints
    tokens is not registered outside those environments either — two gates, because
    one is how this kind of thing reaches production.
    """

    ISSUER = "https://dev.opspilot.local/"
    AUDIENCE = "opspilot-api"

    def __init__(self, settings: Settings) -> None:
        if not settings.app_env.is_local:
            raise RuntimeError(
                f"DevIssuerVerifier refuses to run with APP_ENV={settings.app_env}. "
                "Set AUTH_MODE=auth0."
            )
        self._settings = settings
        self._public_key = _load_dev_public_key(settings)

    def verify(self, token: str) -> Claims:
        try:
            payload = jwt.decode(
                token,
                self._public_key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self.AUDIENCE,
                issuer=self.ISSUER,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise NotAuthenticated("token verification failed") from exc
        return Claims(
            subject=payload["sub"],
            email=payload.get("email", ""),
            issuer=payload["iss"],
            audience=self.AUDIENCE,
            expires_at=int(payload["exp"]),
            organization_hint=payload.get("org_id"),
        )


def _load_dev_public_key(settings: Settings) -> bytes:
    """Public half of the locally generated dev keypair. See ``auth/dev_keys.py``."""
    from opspilot.auth.dev_keys import public_key_pem

    return public_key_pem(settings)


def build_verifier(settings: Settings) -> TokenVerifier:
    if settings.auth_mode == "auth0":
        return Auth0Verifier(settings)
    if settings.app_env is AppEnv.production:  # defence in depth; config also refuses
        raise RuntimeError("AUTH_MODE=dev is not permitted in production")
    return DevIssuerVerifier(settings)
