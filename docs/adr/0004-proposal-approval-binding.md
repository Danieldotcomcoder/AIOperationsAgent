# ADR 0004 — Approval binds to payload hash, source version and expiry

**Status:** Accepted — 2026-09-12

## Context

A supervisor approves a specific change, seen on a specific screen, against a
specific state of the world. Between rendering and execution, the payload could be
edited, the underlying order could change, the approver's role could be revoked, or
hours could pass. "The supervisor clicked approve" is not, on its own, authority to
write.

## Decision

Approval is a claim about an exact artifact:

- `payload_json` is immutable once written; there is no update path.
- `payload_hash` is the sha256 of a canonical serialization (sorted keys, no
  insignificant whitespace, normalized numbers and timestamps) — `domain/hashing.py`.
- The approve request **must echo** the `payload_hash` the UI displayed. A mismatch
  is `409`, never a silent re-read of the current row.
- The approval also binds `source_record_version`. If the order changed, the proposal
  is invalidated and a new run must produce a new proposal.
- Proposals expire 24 hours after creation, checked at decision time and again in the
  worker. Expiry binds the *decision*, not the delivery.
- The approver's role is resolved at decision time and recorded on the approval row
  as `approver_role_at_decision` for the audit trail.

## Consequences

- There is no "edit then approve" flow. This is intentional: an edited payload is a
  different action and deserves its own review.
- The UI must render exactly the payload that will execute, and must send back the
  hash it rendered.
- Audit answers "who approved what, as what role, against which version, when" from
  one row.

## Revisit when

A supervisor workflow genuinely needs amendment. The answer is then a *counter
proposal* authored by the supervisor, which itself needs approval — not a mutable
payload.
