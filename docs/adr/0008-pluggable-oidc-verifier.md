# ADR 0008 — Pluggable OIDC verifier with a hard-gated local dev issuer

**Status:** Accepted — 2026-09-12

## Context

Weeks 1–7 are local development. Requiring a live Auth0 tenant to exercise any
authenticated endpoint blocks work, makes CI depend on a third party, and tempts the
worst possible shortcut: an `if DEBUG: skip auth` branch in the request path.

## Decision

`auth/verifier.py` defines `TokenVerifier` with one method, `verify(token) -> Claims`.
Two implementations, selected by `AUTH_MODE`:

- `Auth0Verifier` — fetches and caches JWKS, validates signature, `iss`, `aud`, `exp`,
  `nbf`, and algorithm allowlist (RS256 only; `none` and HS* are rejected outright).
- `DevIssuerVerifier` — a locally generated RSA keypair signs tokens for seeded users
  via `POST /auth/dev/token`. It **raises at import time** unless
  `APP_ENV in {development, test}`, and the route is not registered otherwise.

Both return the same `Claims`. Everything downstream — membership resolution, role
checks, tenant scoping, audit — is identical in both modes. There is no branch on
`APP_ENV` anywhere in the authorization path itself.

## Consequences

- Local and CI runs are fully authenticated with real JWT verification, real
  membership lookups and real role enforcement.
- The staging/pilot switch is one environment variable plus Auth0 configuration
  values, and it changes no application logic.
- The dev issuer is a deliberate, contained risk: one module, one env gate, one route,
  and a startup assertion that fails loudly in staging or production.

## Revisit when

A second identity provider is needed, or Auth0 plan limits force a change — the
interface already absorbs that.
