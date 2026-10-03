# Retention and cancellation

## Apply and verify

From `apps/api`, run `uv run alembic upgrade head`, then restart the API, workers,
and beat. This applies `20260924_lifecycle` and `20260924_index_auth`. Settings are
loaded at startup; restart processes when changing flags or retention configuration.
Run `uv run pytest --integration -q -p no:cacheprovider` against local PostgreSQL and
Redis, `uv run mypy src`, and `uv run python ../../scripts/audit_tenancy.py --strict`.
From the repository root, run `pnpm typecheck` and `pnpm build`.

## Cancel an investigation

The case workspace offers cancellation to the initiating member or a current
supervisor in the same organization. Other analysts cannot cancel a colleague's run.
Queued runs cancel immediately and release their unused reservation after commit.
For an investigating run, the API records `cancel_requested_at` without waiting for
the provider. The current call finishes or times out; subsequent calls/tools stop,
known usage is recorded, and no result or proposal is persisted. Repeated requests
are harmless. Completed and review-stage runs cannot be cancelled.

Membership revocation also stops further work. The worker commits its claim before
calling the provider and refreshes authorization again when finalizing. Every minute,
`run.fail_abandoned` closes claims older than the configured run deadline plus 120
seconds (210 seconds by default). It records `worker.lost`, or cancellation if already
requested, and retains uncertain spend. It does not blindly retry a paid call.

## Retention jobs

Beat dispatches both jobs hourly to the `outbox` queue:

- `retention.prune_traces`: at most 100 eligible runs per invocation; default 14 days.
  Clears request text, result JSON, steps, evidence grants, feedback correction text,
  and reservation receipts; stamps `trace_pruned_at`. Aggregate usage and run status
  remain. Queued/investigating runs and runs with pending, approved, executing, or
  reconciliation-required proposals are excluded until their workflow resolves.
- `retention.prune_audit`: at most 500 events per invocation; default 90 days.
  Deletes oldest eligible events under row locks.

The jobs are idempotent and skip locked rows. Large backlogs need repeated invocations;
one hourly batch is not a guarantee of immediate deletion at the expiry boundary.
Existing per-run expiry timestamps are honored; a legacy null timestamp uses creation
time plus `TRACE_RETENTION_DAYS`. Audit age uses `AUDIT_RETENTION_DAYS`.
Proposals, approvals, outbox/operation records, and simulated ERP tasks are preserved.
This is trace cleanup, not blanket business-data deletion. The UI explains pruned
traces, and the API rejects new feedback for them.

## Cost and incident controls

Atomic Redis reservations cover application daily/monthly spend and per-user run
quota. Receipts bind settlement to the original UTC periods; duplicate settlement
cannot refund twice or recreate an expired counter. Missing legacy receipts,
unavailable settlement, and unknown provider charges retain a conservative reservation.
Redis unavailability blocks admission and makes readiness fail.

`FEATURE_AGENT_ENABLED=false` stops new investigations and further agent work after
process restart. `FEATURE_WRITES_ENABLED=false` blocks approval/execution writes to
the simulated ERP; it does not disable every database mutation. Read and recovery
paths remain available. Re-enable only after the incident is understood.

Deterministic tests cover concurrent cancellation/revocation, retention boundaries,
tenant/role enforcement, hostile tool arguments and retrieved document instructions,
and concurrent budget admission. Live-model adherence, Linux prefork recovery, and
human quality gates remain separate validations.
