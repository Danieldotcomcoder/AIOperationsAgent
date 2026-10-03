# ADR 0003 — Transactional outbox with idempotent downstream effects

**Status:** Accepted — 2026-09-12

## Context

Approving an action must reliably cause exactly one downstream task. Two naive
options both fail: publishing to Redis inside the DB transaction can publish a message
for a transaction that later rolls back; publishing after commit can lose the message
if the process dies in between. Celery documents at-least-once semantics and requires
idempotent tasks.

## Decision

- The approval transaction writes the status change **and** an `outbox_events` row.
- A dispatcher claims unpublished rows with `FOR UPDATE SKIP LOCKED`, publishes to
  Celery, and marks them published. A periodic sweep re-publishes anything stale.
- The downstream effect is keyed by a deterministic idempotency key,
  `sha256(organization_id | proposed_action_id | payload_hash)`, enforced `UNIQUE` by
  the operations API, which returns the existing task id for a duplicate.
- An uncertain outcome moves the action to `reconciliation_required` and is resolved
  by looking the key up, never by blind retry.

## Consequences

- Delivery is **at-least-once with idempotent effects**. It is not exactly-once and
  must never be described that way in docs, README, or the resume bullet.
- Every downstream call site must have a stable key derivable without stored state.
- Adds a dispatcher process and a sweep job; on this deployment both live in `beat`
  and `worker-execute`.

## Revisit when

A second downstream integration appears that cannot supply an idempotency key — at
which point the reconciliation contract, not the outbox, is what needs redesign.
