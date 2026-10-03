# Approval and simulated ERP execution

This workflow runs with `LLM_PROVIDER=fake`; OpenRouter credentials are optional and
can be added later. All operations are against synthetic local data and our simulated ERP.

## Start the workflow

From the root: `docker compose up -d postgres redis`.
Run each following command in its own terminal from `apps/api`:

```powershell
uv run uvicorn opspilot.main:app --host 127.0.0.1 --port 8000
uv run celery -A opspilot.workers.app:celery_app worker -Q investigate --pool=solo --concurrency=1
uv run celery -A opspilot.workers.app:celery_app worker -Q execute,outbox --pool=solo --concurrency=1
uv run celery -A opspilot.workers.app:celery_app beat --schedule ../../.data/celerybeat-schedule
```

Alternatively, one development worker can listen on `-Q investigate,execute,outbox`.
The scheduler is still needed. Run only one beat process. Start the UI with `pnpm dev`
from the repository root. If loopback name resolution stalls, use
`OPS_API_BASE_URL=http://127.0.0.1:8000/ops` in the root `.env` and restart the worker.

1. Investigate an order as an analyst or supervisor.
2. Sign in as `sam.supervisor@northwind.example.com` and open Approvals.
3. Review the full stored payload. Approve exact payload or reject it.
4. Approval first displays `approved`; the outbox scheduler publishes within about
   ten seconds, then execution should progress to `succeeded` with a task ID.
5. Analysts can inspect the queue but cannot decide. Old, altered or source-stale
   proposals require a new investigation. Rejection never produces an execution event.

The hash shown belongs to the exact stored payload. Earlier development proposals
used inconsistent timestamp serialization and fail this check; generate a fresh run.
No existing payloads or decisions are silently rehashed.

## Recovery behavior

The worker stores its claim before calling the ERP. It checks the idempotency key
before any write. If the task exists, it records success. If lookup returns a successful
null response, it rechecks approval and may send. Lookup errors or ambiguous write
responses leave `reconciliation_required`; they never imply no task was created.

The recovery sweep runs every 120 seconds and republishes approved/executing/uncertain
actions at least 60 seconds old. Consumers lock and re-read each action, so duplicates
are harmless. Outbox publication failures retain the event with bounded exponential
backoff (up to 256 seconds). Decisions do not depend on Redis being available at the
moment the supervisor clicks; the committed outbox is the source of truth.

Each outbox sweep selects at most 50 due events by default. Direct task calls can
set `batch_size` from 1 through 100. Each selected event is locked and checked again
before publication, so another dispatcher can safely skip events already claimed.

Expiry binds the decision time. A timely approval may execute after its expiry date,
provided current membership, payload hash and source version still validate. Source
and approver rows are locked through the write to prevent a concurrent change slipping
between validation and the HTTP call. Changing a payload requires a fresh proposal.

`FEATURE_WRITES_ENABLED=false` blocks approval and new downstream task creation.
Existing outcomes can still be reconciled. Settings are loaded at process start, so
restart API/workers after changing this flag. This is not a live distributed switch.

## Verification

From `apps/api`, with PostgreSQL available:

```powershell
uv run pytest --integration -q
```

The suite covers outbox retry deadlines and the backoff cap, dispatch batch limits,
the recovery cutoff for every action status, and broker failure followed by a later
sweep. These use PostgreSQL with controlled broker calls and time; they do not test
beat scheduling or OS-level worker restarts.

With the API and a worker listening on investigate/execute also running in fake mode:

```powershell
uv run python ../../scripts/probe_approvals.py
```

The probe creates two synthetic tasks and retains their history. It manually dispatches
the outbox, so beat is not required for this command. Do not run beat concurrently with
this failure-injection probe: it deliberately controls when execution is dispatched.
The probe races two approvals, delivers duplicate tasks, and simulates losing the local
commit after the ERP committed. It does not actually kill the worker OS process.

### Real worker termination and scheduled recovery

With local PostgreSQL and Redis running, from `apps/api`:

```powershell
uv run python ../../scripts/probe_worker_recovery.py
```

No existing API, worker or beat process is needed. The drill starts its own API on
`127.0.0.1:18080` (override with `--port`), workers and scheduler. The configured local
PostgreSQL account must be allowed to create databases and extensions. It uses a
fresh synthetic database and unique Redis prefixes for queues, results and budget
counters. Fake chat and embeddings are forced for its child processes; `.env` is
unchanged. Generated seed cases stay in the drill's artifact directory.

It verifies ordinary scheduled outbox delivery, then kills an actual Celery worker
before the ERP write and after the ERP commit but before local acknowledgement.
Each restart must receive `action.reconcile` through a live beat/Redis/worker path,
finish with one downstream task, and tolerate three additional execute deliveries.
The post-commit case must recover the original task ID.

The scheduler polls every two seconds for this drill. After each kill, the synthetic
action timestamp is aged 61 seconds to cross the unchanged production recovery
cutoff. This tests the scheduled task path, not production scheduling latency.
Verified on Windows with the `solo` pool; Linux prefork worker loss, Redis visibility
timeout redelivery and whole-host failure remain separate exercises.

The finalizer stops the owned processes, drops its temporary database and deletes
only its Redis prefix. Logs and `report.json` remain under `.data/recovery_<uuid>/`.
If the parent itself is forcibly killed, its finalizer cannot run; use the recorded
database/prefix and process logs to identify that invocation's resources.

## Stop when finished

Stop the API, both workers, beat and frontend with Ctrl+C. Then from the root:

```powershell
docker compose stop postgres redis
docker compose ps --status running
```

Keep the data volumes. Retention jobs and Linux deployment recovery remain future work.
