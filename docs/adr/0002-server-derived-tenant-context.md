# ADR 0002 — Tenant context is derived server-side and is never a parameter

**Status:** Accepted — 2026-09-12

## Context

The system is multi-tenant and an LLM chooses which tools to call with which
arguments. The single highest-severity failure mode available to this design is a
model, or a crafted document, causing a read or write scoped to the wrong
organization. A second, quieter failure mode is a background job trusting an
`organization_id` that was correct when it was queued and wrong by the time it ran.

## Decision

1. `organization_id` is resolved from the `memberships` row matching the verified
   JWT subject. It is never accepted from a request body, query parameter, custom
   header, or tool argument, and it is never present in any tool's JSON schema.
2. Tool argument models physically cannot express it — the Pydantic models have no
   such field and forbid extras, so a model that emits one gets a validation error.
3. Every tenant read goes through `TenantScopedRepository`, which applies the filter.
4. Background tasks carry IDs only and re-resolve membership and role at execution
   time; a revoked membership stops work already in flight.
5. Composite foreign keys `(organization_id, parent_id)` make a cross-tenant write
   fail in the database even if every Python guard is bypassed.

## Consequences

- Some queries are slightly more verbose than a naive `get_by_id`.
- Cross-tenant reads return `404`, never `403`, so existence is not disclosed.
- The evaluation set includes deliberately colliding order numbers across the two
  seeded organizations, so a missing filter surfaces as a failing case rather than as
  a silent leak.

## Revisit when

Never, within this design. If an operator-level cross-tenant view is ever needed it
gets its own explicitly audited code path, not a relaxation of this rule.
