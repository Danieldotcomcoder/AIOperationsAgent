# Incident runbooks

One section per incident class from [the evaluation plan](../plan/03-evaluation-gates.md).
Commands assume the production stack (`C="docker compose -f infra/compose/docker-compose.prod.yml --env-file .env.production"`);
locally, use `pnpm stack` and `docker compose exec postgres psql -U opspilot -d opspilot`.
First look in every case: the **Metrics** page (failures by reason, uncertain writes,
queue wait) and **Investigations** (status, error code, model and prompt version).

## Model unavailable

Symptoms: runs end `failed` with `provider_unavailable`; Metrics shows the reason.

1. Nothing to undo: each run made at most three calls (retries count), returned an
   incomplete result, and kept its spend reservation rather than guessing.
2. Check the provider's status page and the key's quota
   (`GET https://openrouter.ai/api/v1/key` with the key; free tiers have daily caps).
3. Analysts continue manually from the order page; nothing waits on the agent.
4. Do **not** switch to an untested model to get answers back. A model change is a
   release: run `pnpm eval run --live` first and compare reports.

## Queue backlog

Symptoms: runs stay `queued`; Metrics p95 queue wait climbs.

```sql
select status, count(*), min(created_at) from agent_runs
 where status in ('queued','investigating') group by status;
```

1. Workers alive? `$C ps` and `$C logs --since 10m worker-investigate`.
2. Provider throttling makes runs slow, not stuck: check `p95 model latency`.
3. Stranded runs are republished every 60 s; runs abandoned by a dead worker close as
   `worker.lost` after deadline + 120 s. Keep concurrency within the documented limits
   instead of adding workers on the 4 GB host.

## Uncertain write (reconciliation_required)

Symptoms: an approval shows "outcome uncertain"; Metrics flags it in red.

1. Never retry blind. The sweep looks the idempotency key up in the ERP every 120 s:
   found means `succeeded` with that task id; a definite absence means a retry with
   the same key, which the ERP deduplicates.
2. If it stays uncertain, the ERP boundary is unreachable or answering errors:
   `$C logs --since 30m worker-execute api`.
3. To inspect one action:
   ```sql
   select a.status, o.idempotency_key, o.status, o.attempts, o.response_json
     from proposed_actions a join operation_tasks o on o.proposed_action_id = a.id
    where a.id = '<action id>';
   ```
4. Do not edit rows by hand. If a human must decide, leave it uncertain and record the
   decision in the audit trail through a new, reviewed proposal.

## Bad prompt release

Symptoms: quality drops after a release; feedback turns negative; evaluation fails.

1. Every run records `prompt_version`, provider and model. Filter Investigations and
   Metrics → "By model and prompt version" to confirm the change correlates.
2. Roll back the image (`sh infra/scripts/deploy.sh "$(cat .deploy/previous)"`) and the
   model settings in `.env.production` together, to the last evaluated pair.
3. Rerun `pnpm eval run --live --split dev` on the fix before releasing it again.

## Data loss

1. Stop writes: `FEATURE_WRITES_ENABLED=false`, `FEATURE_AGENT_ENABLED=false`, `up -d`.
2. Restore the newest backup into an isolated database
   (`sh infra/scripts/restore.sh …`), compare counts with what survived, then plan the
   recovery. Promote by restoring into a fresh `POSTGRES_DB` during a maintenance
   window, never over a database you have not copied.
3. Targets: ≤ 24 h of lost data, recovery ≤ 4 h. Record actuals.

## Cost spike

1. Admission stops by itself at the daily/monthly caps (`budget.exhausted`), before
   any model call. To stop sooner: `FEATURE_AGENT_ENABLED=false`, then `up -d`.
2. Find the source: Metrics → by model; Investigations sorted by cost; per-user run
   counts. Paid routes require explicit prices (`OPENROUTER_FREE_ONLY=false` plus
   both price settings), so a surprise paid route is a configuration change — check it.

## Security defect

1. Contain: disable the affected path (`FEATURE_WRITES_ENABLED=false` for anything
   touching writes; take the API down if tenancy is in doubt).
2. Preserve evidence before retention prunes it: copy `audit_events` and the relevant
   `agent_runs` rows (`pg_dump -t audit_events -t agent_runs`). Traces are pruned
   after 14 days, audit events after 90.
3. Fix, add the regression test, and rerun the full suite plus
   `pnpm eval run --split all` (tenancy, approval and idempotency cases must all pass)
   before re-enabling.
