# ADR 0014 — Durable cancellation, bounded retention, and usage receipts

Status: Accepted · 2026-09-24

## Context

Holding the investigation claim transaction open blocked concurrent cancellation.
Retention jobs were placeholders, and budget refunds needed to remain correct across
duplicate delivery, configuration changes, and UTC period boundaries.

## Decision

Commit the run claim before provider work. Persist cancellation requests separately
from terminal status; check cancellation, membership, and the agent flag between
calls and tools. Lock and refresh the run and membership before persisting results.
An in-flight provider call finishes or times out, and its known usage is recorded.
Close abandoned claims after the run deadline plus 120 seconds without retrying an
unknown paid call. Never resurrect a terminal run.

Reserve quota and spend atomically in Redis. Persist the original counter keys and
reservation amount as a receipt; settlement is idempotent and never recreates expired
period counters. Unknown usage retains its reservation conservatively.

Prune expired trace content in bounded batches while preserving aggregate accounting
and immutable operational records. Unresolved proposals retain their evidence. Block
new feedback after pruning. Audit-event retention is an independent bounded sweep.
Policy indexing records and rechecks the requesting supervisor before embedding.

## Consequences

Cancellation is cooperative, not a provider transport abort. A crash can overcount
spend, which is safer than silently refunding unknown usage. Trace retention is not
an account-erasure policy: proposals, approvals, and downstream records remain.
Two additive, reversible migrations introduce lifecycle fields and indexing authority.
Restart API, workers, and beat together after migration; mixed-version behavior has
not been validated. Revisit for provider cancellation APIs, durable usage ledgers,
or a separately specified operational-record erasure policy.
