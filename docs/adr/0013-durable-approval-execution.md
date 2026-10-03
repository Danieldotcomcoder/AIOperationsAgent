# ADR 0013 — Durable execution claim and reconciliation before retry

Accepted 2026-09-23. Complements ADRs 0003 and 0004 without changing canonical hashing.

Approval stores the decision, state transition, audit record and outbox event in one
transaction. Supervisors must echo the displayed hash. Stored JSON is serialized with
the existing canonical serializer before persistence; this fixes an earlier producer
bug where timestamp formatting differed between hashing and storage. Older mismatched
proposals fail closed and require a fresh investigation, not a silent rehash.

Execution commits a durable `executing` claim and operation ledger before network IO.
A separate transaction locks the action, checks the downstream key, and only writes
after definitive absence and fresh authorization. An abandoned claim is recovered by
the periodic sweep. A downstream success followed by a local rollback is resolved by
key lookup. Delivery remains at-least-once with idempotent effects.

Order and supervisor membership locks use PostgreSQL `FOR NO KEY UPDATE` through the
HTTP write: concurrent source/role edits wait, while downstream foreign-key key-share
checks can proceed. The action lock serializes duplicate deliveries. These locks last
at most the bounded lookup/write calls in ordinary operation, plus database overhead;
production lock-wait tuning remains part of reliability hardening.

Worker validation checks the approval at its recorded decision time, with current
membership and source state. Expiry cannot retroactively revoke a timely decision.
An already-created downstream task is recorded as fact even if permissions have since
changed; revocation blocks a new write, not truthful reconciliation of an old effect.

The local live probe verifies concurrent approval, duplicate messages and the lost
local-commit boundary. Actual worker termination/restart remains a separate fault test.
