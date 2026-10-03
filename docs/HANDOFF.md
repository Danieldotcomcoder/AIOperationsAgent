# HANDOFF — current state and the next steps

**Last updated:** 2026-10-03 · **Session:** debug round, Week 7 complete, Week 8 prepared, full web UI

## Current state — Weeks 1–7 built and verified locally; Week 8 prepared, not deployed

**Run it:** `pnpm stack` (or `pnpm stack --fake`) starts PostgreSQL/Redis, migrates,
seeds if empty, and runs API, both workers, beat and the web app; Ctrl+C stops all.
Open http://127.0.0.1:5173 and click a seeded account. Logs: `.data/dev/*.log`.

### Debug round — defects found and fixed (each with a regression test)

1. **Commit after response.** FastAPI ≥0.118 runs request-scoped yield-dependency exit
   code after the response is sent, so `POST /runs` and approvals returned 2xx before
   the transaction committed; a failed commit was invisible to the client. `db_session`
   is now `scope="function"` everywhere (one shared `Depends`, since FastAPI caches per
   callable *and* scope). Test: `tests/test_transactions.py` fails on the old code.
2. **Two worlds of "today".** The inbox listed orders as of the frozen seed date while
   the agent's tools and prompt used the real clock (SO-1042: 4 vs 24 days overdue).
   New `OPERATIONS_AS_OF` setting and `core.clock.business_date()` drive the inbox,
   order detail, agent prompt/tools, knowledge "current version" and `/me`. Root
   `.env` and `.env.example` pin it to 2026-09-12. Blank means today.
3. **Tests depended on the developer's `.env`.** Two failed whenever OpenRouter
   embeddings were configured, because `search_policies` read global settings. The
   dispatcher now passes the run's settings; `conftest.py` pins fake providers.
4. **Liquid query overflow.** `query + order_context` could reach ~800 bytes against
   Liquid's 500-byte cap, so retrieval failed as "unavailable". The optional context
   is now omitted (visibly, `order_context_used`) when it does not fit; an oversize
   query is a correctable `tool.invalid_args` error. No truncation (ADR 0015).
5. **Tool limit discarded the answer.** At 6 lookups with a model call to spare, runs
   failed with no answer, and calls skipped mid-batch had no tool reply (OpenAI-format
   APIs reject that). Now every call gets a reply, the spare call synthesizes an
   answer marked `tool_limit`, and tool calls on a tool-free final turn never run.
6. **Anthropic final turn** passed `tools=[]` with `tool_use` history, which the API
   rejects; it now re-declares tools with `tool_choice: none` and groups parallel
   results into one user turn (mocked test; still never run against the live API).
7. **Deleted policies blocked re-upload** of identical content (misleading 404); the
   deleted version's hash is tombstoned. **Seed `--reset`** failed once ERP tasks
   existed; it now clears dependent history. **Run attribution** now records the
   provider/prompt version that actually ran. **404 bodies** no longer name tables.
8. **localhost on Windows** cost ~2.1 s per connection (IPv6 first); ERP calls,
   proxy and defaults use 127.0.0.1. CORS allows both dev origins.
9. Lint/format drift in migrations fixed. Production images: uvicorn was not on
   `PATH` (venv path), `uv` was pinned too old for the lockfile, installs fell back to
   silent re-resolution or `|| true`, and with **no `.dockerignore`** the API image
   would copy the host's `.venv` over its own and the web build stage would receive
   `.env`, the dev signing key and host `node_modules`. All fixed. Dev compose no
   longer shadows the container venv.

### Week 7 — done (quality measurement still needs people)

- `GET /metrics/summary` (tenant-scoped; outcomes, incomplete reasons, error codes,
  cost/latency/queue-wait percentiles, tool outcomes, pre-strip citation ratio,
  approval/execution outcomes, feedback, by model+prompt). Deployment-wide Redis
  spend is deliberately not exposed (it includes other tenants).
- Run history with order, requester, status, proposal state, usage, model/prompt
  and feedback; feedback recording; order activity; inbox filters/search/counts.
- `evals/cases/v1`: 80 cases, 40/40 stratified split, checksummed (reviews excluded
  from the checksum). Model-case expectations come from the seed generator's class
  rules; deterministic ones from the contract. `pnpm eval run` builds an isolated
  database per run. Deterministic baseline graded by the same code checks.
- Results: scripted model, all 80 — **deterministic 35/35 pass**. Live smoke
  (Nemotron Ultra free + Liquid, 3 cases): all code checks passed, 40–79 s per run.
  First successful live Liquid indexing and retrieval. No human grading yet.

### Web UI — complete for the workflow

Inbox (filters, search, quick investigate), order detail (lines, shipments, carrier
events as labelled untrusted text, investigations, ERP tasks), case workspace
(evidence chips link to the order or passage, live proposal state, feedback),
investigations, approvals (assignee, exact payload and hash), knowledge (defaults to
the business date), metrics (stat tiles, breakdowns, dark mode). Context strip labels
synthetic data, the scripted model and the business date. One-click seeded sign-in.
TypeScript types are generated from the API schema (`pnpm contracts:generate`;
`pnpm contracts:check` fails on drift). Verified by a headless-Edge walkthrough of the
whole workflow, both roles and a cross-tenant probe, with no console errors.

### Verified this session (actual output)

- `uv run pytest --integration -q`: **161 passed** (with the real `.env`; hermetic).
- `ruff check`, `ruff format --check`, `mypy src` (81 files): clean.
- `pnpm typecheck`, `pnpm build`, `pnpm contracts:check`: pass.
- `python scripts/verify_gates.py`: **18 passed, 0 failed**. Agent probe: all six.
- `pnpm eval run --split all`: deterministic 35/35. Browser walkthrough: no problems.
- Restore drill: a custom-format dump restored into an isolated database with
  identical migration head and row counts. `age`/`rclone` were not exercised.
- Production images built (api 765 MB, web 94 MB) and the production compose stack
  ran locally with placeholder secrets: migrations, health checks, HTTPS through
  Caddy, SPA routes, `/api` proxying, no dev-token route or docs in production,
  security headers, only Caddy published, PostgreSQL without internet egress.
  Details in docs/runbooks/DEPLOY.md. Not deployed to a server.

### Next, in order

1. **Human gates** (owner): grade the model cases in `evals/cases/v1` and the
   live reports; label the 20 retrieval questions (Weeks 2–3 runbook). Spread a live
   `pnpm eval run --live` over several days to stay inside the free daily quota.
2. **Week 8** needs owner inputs (VPS + domain, Auth0 tenant, registry, backup target);
   then build the SPA's Auth0 sign-in and follow docs/runbooks/DEPLOY.md.
3. Optional cleanup: four declared Python dependencies are unused (tiktoken, tenacity,
   orjson, faker); older scripts under `scripts/` are not lint-clean.

Test data note: this session's checks created runs, proposals, feedback and ERP tasks
in the development database (Northwind). The policy it uploaded with word-hash
embeddings was deleted again, because the root `.env` selects Liquid embeddings and
the two indexes never mix: upload `data/policies/delayed-shipments.md` once under the
configured embeddings. `uv run python ../../data/seed/generate.py --reset` (from
apps/api) clears operational history; evaluation runs never touch this database.

## Earlier continuation — live validation partially blocked by provider quota

The user saved an OpenRouter key and selected Nemotron Ultra free for chat, then
explicitly requested `liquid/lfm-2.5-embedding-350m:free` for embeddings. Root `.env`
now selects both through OpenRouter, retaining the existing key. No OpenAI key is
required. No credentials were printed, committed, or copied into reports.

Both user-named chat models lack advertised JSON response mode. Their adapters now
omit that transport parameter while preserving strict application schema/citation
validation and zero-price routing. The initial live Nemotron investigation used all
three calls on tools; the final allowed call now requests synthesis with no offered
tools. Prompt version is `2026.09.25-1`; bounds were not increased.

Live evidence: five distinct cases in the ten-case batch produced structured results,
then case six failed with provider unavailability. A subsequent Liquid probe exposed
HTTP 429: the account's daily free-model request allowance was exhausted (50 limit,
0 remaining). No more live calls were attempted. Earlier probes and the failed case
remain recorded; do not count repeated cases as unique or call outputs human-approved.
No evaluation proposal was approved or executed.

Batch evidence: `evals/reports/week2_a50ae81e5a23462eaf438b433090856f.json`.
It used fake embeddings, so it is not semantic retrieval evidence. The first failed
probe and corrected successful probe have separate reports, referenced in the runbook.
`scripts/evaluate_week2.py --start 5 --count 5` resumes the remaining five after quota
reset; a uniform post-indexing evaluation should rerun all ten and preserve history.

Liquid implementation uses the existing SDK, free-only routing, bounded timeout,
Redis admission, index/shape/finiteness validation, and zero padding from native 1024
to storage 1536. Padding preserves cosine distance; `:pad1536-v1` isolates the model
space. Existing indexed versions are not automatically compatible. Inputs over 500
UTF-8 bytes are rejected rather than truncated; byte-based chunks already fit. No
migration was needed. See ADR 0015. Successful live Liquid output remains unverified.

Added twenty draft retrieval questions (labels explicitly null) and a retrieval scorer
that refuses unreviewed labels, fake embeddings, and an empty compatible index before
making requests. Human labels, a representative reviewed policy corpus, and human
grading are still necessary; neither Weeks 2–3 quality gate is marked complete.

Verified: **143 tests passed** with fake environment overrides for deterministic tests;
mypy clean across 79 source files; Ruff clean. New tests cover final-turn synthesis,
chat transport compatibility, Liquid routing/padding, malformed vectors, and oversized
UTF-8 input. The retrieval runner rejected the draft labels without provider calls.
No frontend changes, new dependencies, migrations, or approvals were introduced.

Next: follow [Weeks 2–3 validation](runbooks/WEEKS-2-3-VALIDATION.md) after provider
quota is available. Complete real Liquid indexing/search and the remaining generations;
then the human-review gates. Weeks 7–10 remain afterward.
Project PostgreSQL/Redis containers were stopped after verification; volumes retained.

## Earlier continuation — Week 6 complete locally

Implemented cooperative cancellation with a separately committed worker claim,
fresh authorization checks, and finalization locks. The initiating member or a
current same-tenant supervisor can cancel; queued cancellation releases unused
budget, and active cancellation records known usage without persisting a result or
proposal. A scheduled abandoned-claim sweep prevents indefinitely investigating runs
without retrying unknown paid calls.

Implemented bounded hourly trace/audit retention, preserving aggregate accounting and
immutable operational records. Unresolved proposals retain evidence. Pruned runs
reject new feedback. Added atomic Redis quota/spend reservations with original-period
receipts and idempotent settlement; uncertain usage conservatively retains reserves.

Security cleanup includes tenant-aware browser query keys and session cache clearing,
recursive log redaction, metadata-only unexpected errors, validated request IDs,
bounded list inputs, Redis readiness, and indexing-requester authorization before
embedding. Expanded the tenancy auditor to projections and direct ORM gets with
function-level exceptions. Added hostile tool/document tests. Full backend type
checking now passes; the contracts package has its missing build configuration.

Validation:

- PostgreSQL/Redis integration suite: **134 passed**, one upstream Starlette/AnyIO warning.
- Full mypy: **79 source files, no issues**; Ruff checks passed.
- Frontend `pnpm typecheck` and root `pnpm build` passed.
- Both additive lifecycle/index-authority migrations applied; Alembic reports no drift.
- Strict tenancy audit: no findings (heuristic, not proof of complete isolation).
- Repeated real Windows solo-worker kill/restart and live beat drill passed normal,
  before-ERP-write, and after-ERP-write cases with harmless duplicate deliveries.
  Evidence: `.data/recovery_c89c7dde0aaa47daae6f62b6c131fe7b/report.json`.

The final runtime retry initially found Docker Desktop stopped; after restarting it
and the project containers, the isolated recovery drill passed. No live model calls
were made. The cancellation UI was built/typechecked; a browser interaction smoke
test was not performed in this slice. Linux prefork, whole-host recovery, hosted
deployment, live-provider quality, and human gates remain unvalidated.
Project PostgreSQL/Redis containers are stopped after verification; volumes retained.

Next, as requested: finish **Weeks 2–3 validations** (ten hand-reviewed investigations
and twenty labeled retrieval questions, including live provider/embedding behavior).
Weeks 7–10 evaluation, hosting, pilot, and portfolio evidence remain afterward.
See [the runbook](runbooks/RETENTION-CANCELLATION.md),
[ADR 0014](adr/0014-run-lifecycle-retention.md), and the weekly checklist.

## Earlier continuation — worker-kill/restart drill

Added `scripts/probe_worker_recovery.py`, a repeatable local drill that owns its API,
Celery workers and beat processes. It creates a fresh database, applies migrations and
seeds synthetic data, and isolates broker/results/budget keys with a unique Redis
prefix. Generated seed cases go into its private artifact directory. The finalizer
stops child processes, drops only its generated database and removes only its prefixed
Redis keys. The normal development database and root `.env` are not changed.

Verified on Windows with Celery's `solo` pool and real PostgreSQL/Redis/HTTP:

- Live beat publishes a committed approval outbox event and the worker creates one task.
- OS termination before the ERP POST leaves a durable `executing` claim and no ERP
  task. A restarted worker receives scheduled reconciliation and creates one task.
- OS termination after the ERP POST commits, but before local acknowledgement,
  leaves `executing` and one ERP task. Scheduled reconciliation recovers the exact
  same task ID. This is an actual killed process, not a simulated local rollback.
- Three extra execute deliveries after each of those cases leave one downstream task.
- The final isolated drill passed all three cases. Evidence is in
  `.data/recovery_073f32e8c9f9468bb2cdaa3b412b71d5/report.json` and adjacent process logs.
- Full regression suite: **83 passed**, one upstream Starlette/AnyIO warning.
  Backend/test/drill lint passed; the drill is formatted.

Limits: beat uses a two-second polling interval for this drill and the abandoned
fixture is explicitly aged 61 seconds; the production age predicate is unchanged.
This verifies scheduled application recovery, not Redis visibility-timeout redelivery,
Linux prefork child-loss handling, production cadence/latency, or a whole-host crash.
No production logic, dependencies, migrations, prompts or model credentials changed.

Next: Week 6 security/retention/cancellation cleanup. Linux deployment recovery,
live-provider validation, human quality gates, full-project typing and the tenancy
audit backlog remain. See the approval runbook for the repeatable drill command.

Project PostgreSQL/Redis containers stopped after testing; data volumes retained.

## Earlier continuation — recovery sweeps and retry timing

Continued the recovery-testing slice in fake mode; no credentials, provider calls,
dependencies or migrations were needed.

- Fixed `outbox.dispatch`: it previously ignored `batch_size` and selected 100 rows.
  It now honors the default of 50 or an explicit size of 1–100, rejects invalid bounds,
  and orders equal creation timestamps by event ID. Updated its lock description to
  match the existing per-event locking implementation.
- Added 22 regression cases covering invalid batch sizes, bounded dispatch, exclusion
  of future retries, exponential retry delays through the 256-second cap, exact due
  boundaries, every action status at the 60-second recovery cutoff, and retrying a
  recovery sweep after broker publication fails.
- Sweeps execute real PostgreSQL queries in rollback-isolated tests; broker calls and
  the clock are controlled. This verifies task bodies, not a running beat scheduler or
  OS-level worker termination/restart.

Verified: `uv run pytest --integration -q -p no:cacheprovider`: **83 passed**, one
upstream Starlette/AnyIO deprecation warning. Offline suite: **35 passed, 48 skipped**.
`ruff check src tests` passed; changed Python files were formatted. Docker initially
was unavailable; after starting Docker Desktop and the two project data containers,
the full integration suite passed. No API or worker processes were needed.

Next: actual worker-kill/restart and live beat recovery exercises, then Week 6
security/retention/cancellation cleanup. Live-provider validation and human quality
gates remain deferred. The full-project typing and tenancy-audit backlog remains.

Project PostgreSQL/Redis containers are stopped after verification; volumes retained.

## Earlier continuation — approval and execution

The owner deferred OpenRouter credentials. Continued entirely in fake mode; no live
model calls or new credentials were needed. Their preference to stop Docker after
testing remains in force.

Implemented:

- Approval inbox and tenant-scoped action reads. Supervisors can approve the exact
  displayed hash or reject; analysts are read-only. The UI shows payload integrity,
  source changes, expiry, downstream task IDs and uncertain/failed outcomes.
- Decision transactions lock and re-read the proposal, current membership and source
  order. Approval, status, audit record and outbox event commit together. Rejection
  creates no execution event. Expiry sweep closes old pending proposals.
- Fixed proposal timestamp serialization: new proposals persist `canonical_json`
  exactly as hashed. Older mismatched proposals fail integrity checks and require a
  new investigation; existing approvals/payloads are not silently rewritten.
- Membership now supports the tenant repository through its existing tenant column;
  `alembic check` confirms no database schema change.
- Service-token-only simulated ERP task creation and lookup. Database uniqueness
  enforces idempotency; a key reused with different content returns 409. Order and
  assignee must belong to the service-requested organization.
- Outbox dispatch with bounded backoff. Execution persists its claim before HTTP,
  then serializes deliveries on the action. It looks up the deterministic key before
  every possible write, revalidates permission/hash/source state before sending, and
  reconciles unknown outcomes. Recovery also scans abandoned executing/approved rows.
- The case workspace now reads the actual approval/execution status instead of
  permanently claiming the proposal is pending.

Verified this session:

- Full PostgreSQL suite: **61 passed**, one upstream Starlette/AnyIO deprecation warning.
- Live `scripts/probe_approvals.py`: concurrent approvals yield 200 and 409; approval
  -> outbox -> worker -> real HTTP simulated ERP succeeds; three duplicate deliveries
  leave one task. Failure injection rolls back the local acknowledgement after the
  downstream commit; reconciliation recovers the same task. This is not an OS-level
  worker-kill test. The probe leaves two synthetic tasks and their audit records.
- Existing HTTP gates: **18 passed, 0 failed**.
- Frontend production build, backend lint/format and targeted mypy across seven
  approval/execution modules passed. `alembic check`: no new upgrade operations.
- Full-project mypy and earlier tenancy-audit cleanup are still separate work.

**Stopped after testing:** project API/worker processes and PostgreSQL/Redis containers.
`docker compose ps --status running` returned no rows; volumes were preserved.

Running approvals now needs an execution/outbox worker and Celery beat, in addition
to API and investigation processing. See [approval runbook](runbooks/APPROVALS.md).
Unimplemented retention tasks were removed from the active beat schedule until their
Week 6 implementation, so normal operation does not periodically invoke stubs.

Next: real worker termination/restart fault tests, scheduled recovery/backoff testing,
and Week 6 security/retention/cancellation cleanup. Live OpenRouter validation and the
human investigation/retrieval quality gates remain deferred until the owner supplies
credentials and reviews output. No new dependencies or migrations were added.

## Earlier continuation — ingestion and OpenRouter

Implemented the next Week 3 slice end to end:

- Supervisor-only Markdown/text uploads, content deduplication, explicit document
  revisions, effective intervals, retry indexing, and tenant-scoped listing/deletion.
- Heading-aware bounded chunking, deterministic no-key development embeddings and
  optional OpenAI embeddings. Embedding provider/model identity filters prevent mixing
  incompatible vectors. Paid embedding calls reserve spend before calling the provider.
- Idempotent indexing worker: chunks and effective intervals commit atomically.
  Duplicate delivery is a no-op; failure is shown in the UI and can be retried.
- `search_policies` now returns actual passages and grants their citation IDs.
  The fake chat provider demonstrates policy citations when passages are available.
- Knowledge screen for uploads/revisions/retries/deletion; case links open authorized
  policy passages. Deleted text is no longer available through those links.
- OpenRouter adapter, key configuration, free-only model validation and zero-price
  routing constraints. Paid mode requires explicit input/output price estimates.
- Fixed missing assistant tool-call history in the OpenAI and Anthropic transports.
  Added the output JSON schema to the system prompt; version is `2026.09.23-2`.
- No new dependencies or database migrations.

Verification actually run:

- `uv run pytest --integration -q`: **35 passed**, one upstream Starlette/AnyIO
  deprecation warning. Includes document lifecycle, authorization, scheduled revisions,
  deletion, evidence grants, failed embeddings, budget accounting and mocked OpenRouter.
- `scripts/probe_documents.py`: live API -> queued indexing -> retrieval -> granted
  citation -> readable passage -> deleted passage returns 404. All passed in fake mode.
- Existing HTTP release gates: **18 passed, 0 failed**. Agent probe: all six scenarios.
- Backend lint/format and frontend production build passed. Targeted mypy over seven
  new retrieval/document/OpenRouter modules passed. Full-repository typing and the four
  pre-existing tenancy audit findings remain outside this slice.
- Initial respx tests missed the SDK's newer httpx2 transport, causing rejected
  test-key requests (no successful live generation). Tests now explicitly inject the
  supported httpx transport; final OpenRouter tests are mocked. No paid inference was
  performed. Real OpenRouter and OpenAI embedding responses remain unverified.

**Runtime is stopped:** temporary API and worker processes were stopped, as were this
project's PostgreSQL and Redis containers; `docker compose ps --status running` is empty.
Volumes were retained. **User preference: stop project Docker containers after testing.**

What the owner needs to add: their OpenRouter key in the root `.env`, reviewed policy
files/effective dates, and human review of ten investigations and twenty retrieval
questions. See [local model setup](runbooks/LOCAL-MODELS.md) and the synthetic starter
`data/policies/delayed-shipments.md`. Existing `.env` was not changed.

Still open: semantic retrieval evaluation and live-provider validation; automatic
indexing recovery/bulk re-indexing; the approval/execution workflow (Week 4).
Default fake embeddings are word-hash fixtures, not semantic embeddings. Conservative
byte-counted chunks are smaller than the original ~500-model-token target. Changing
embedding provider requires a new revision for now. Metadata is retained on deletion,
so the existing per-tenant content hash constraint prevents re-creating identical
deleted content. List displays at most 100 documents. No claim that Week 3's quality
gate has passed; see ADRs 0011 and 0012 for the implementation boundaries.

## Earlier continuation — retrieval foundation (historical)

Weeks 1–2 implementation is present and its automated checks were re-run successfully.
The Week 2 ten-case human review remains outstanding; do not call that quality gate passed.
The sections below describe the original session unless superseded here.

Implemented `apps/api/src/opspilot/retrieval/search.py`: exact pgvector cosine search,
tenant-scoped queries through `TenantScopedRepository`, active/effective version and
deleted-document filters before ranking, deterministic tie ordering, candidate/top-k
limits, and a cumulative token budget. Invalid dimensions, non-finite/zero vectors,
and invalid bounds fail before database access. Oversized passages are skipped intact.
No dependencies, migrations, prompts, or tool permissions changed.

Added rollback-isolated PostgreSQL tests in `apps/api/tests/test_retrieval.py` and
`conftest.py`. Run from `apps/api`: `uv run pytest --integration -q`.
Without `--integration`, database tests are explicitly skipped; input validation runs.

Verified this session:

- Retrieval tests: **12 passed**, including filtering before candidate limits,
  tenant isolation, effective-date boundaries, superseded/future policies, deletion
  visibility, empty corpus, token budgets, and malformed vectors/bounds.
- HTTP release gates: **18 passed, 0 failed** (`python scripts/verify_gates.py`).
- Agent probe: all **6 scenarios** behaved as expected.
- Frontend production build and `ruff check src tests`: passed.
- Full `mypy src`: failed with pre-existing typing issues across provider adapters,
  models, services and workers; it is not a passing baseline.
- Strict tenancy audit: four existing findings in `api/routes/dev_auth.py`,
  `auth/dependencies.py`, `api/routes/runs.py`, and `services/runs.py`.
  Proposal queries have explicit organization predicates; identity lookups occur
  before resolving tenant context. These need repository-convention cleanup or
  narrowly documented audit exceptions, rather than being treated as proven leaks.

Local PostgreSQL/Redis, API (127.0.0.1:8000), and the investigation worker were started
for verification. Integration fixtures roll back; the HTTP gate creates a synthetic run.
The repository still has no commits and its project files are untracked.

Next work: document upload/versioning/deletion, heading-aware chunking and embedding,
idempotent indexing, then wire `search_policies` and citation resolution to the tested
search primitive. Upload routes and the indexing worker remain stubs; the agent still
returns explicit empty policy results. Finish with the 20 human-labelled retrieval
questions; these tests do not establish semantic retrieval quality or complete Week 3.

Read this first, then [ARCHITECTURE.md](ARCHITECTURE.md). This file is the contract
between sessions: update it at the end of every working session, and keep it honest —
"scaffolded" and "working" are different words and must not be used interchangeably.

---

## 1. What this session did

Laid the foundation: repository layout, system design, data model, state machines,
eight ADRs, infrastructure, and a typed application skeleton for the API, the agent,
the workers and the SPA.

Then made it run. The stack now boots end to end: containers up, schema migrated,
API serving, and the full authentication and authorization chain working against
seeded identities. Everything marked "verified" below was executed and observed in
this session; everything else is code on disk that has not been run.

### Decisions taken (confirmed with the project owner)

| Decision | Choice | Recorded in |
| --- | --- | --- |
| Model provider | Adapter with OpenAI (default), Anthropic, and a deterministic fake | [ADR 0005](adr/0005-provider-adapter.md) |
| Local identity | Pluggable verifier: hard-gated dev issuer + Auth0 | [ADR 0008](adr/0008-pluggable-oidc-verifier.md) |
| Python tooling | uv + ruff + mypy | `apps/api/pyproject.toml` |
| Sync vs async | Synchronous SQLAlchemy throughout | [ADR 0009](adr/0009-synchronous-io.md) |

### Files written

```
docs/            ARCHITECTURE, DATA-MODEL, STATE-MACHINES, SECURITY, adr/0001-0009,
                 plan/ (the four original planning documents, renamed)
infra/           api.Dockerfile, web.Dockerfile, Caddyfile, postgres init SQL
/                docker-compose.yml, .env.example, package.json, pnpm-workspace.yaml
apps/api/        pyproject.toml, alembic.ini, migrations/env.py + script.py.mako
  core/          config, errors, logging, ids, clock
  domain/        enums, state, hashing, evidence, budget, approval      <- pure, complete
  models/        base, identity, operations, knowledge, agent, execution, audit
  db/            session, repository (TenantScopedRepository)
  auth/          principal, verifier, dependencies, dev_keys
  agent/         schemas, prompts, orchestrator, tools/definitions, providers/base
  retrieval/     search (contract only)
  api/           router, routes/{health,me,runs,orders,documents,actions,metrics,dev_auth}
  ops/           api (simulated ERP boundary)
  workers/       app (Celery config), tasks (signatures + invariants)
apps/web/        package.json, vite/ts config, index.html, main.tsx, App.tsx,
                 api/client.ts, styles.css
packages/        contracts/ (shared enums, guards, generation setup)
data/seed/       generate.py (determinism contract + safety rails)
scripts/         audit_tenancy.py, seed_dev_users.py
```

### What is genuinely finished

These are complete, not stubs, and can be relied on:

- `domain/` — every pure module: state machines, canonical hashing, idempotency key
  derivation, evidence id parsing, run budget, approval preconditions.
- `core/config.py` — including the startup validator that refuses unsafe deployments.
- `core/errors.py` — the full error taxonomy and its HTTP/code mapping.
- `models/` — the full schema as SQLAlchemy models with composite tenant FKs.
- `db/repository.py` — the tenant-scoping mechanism.
- `workers/app.py` — Celery configuration, queues, beat schedule.
- All architecture and ADR documentation.

Added and verified in Weeks 1–2:

- `data/seed/` — deterministic generator; regenerating gives an identical checksum.
- `services/orders.py`, `services/tools.py`, `services/runs.py`, `services/budget.py`.
- `agent/orchestrator.py` with the full loop, plus three provider adapters.
- `workers/tasks.py::investigate` and `run.requeue_stranded`.
- `apps/web` — sign-in, operations inbox, case workspace.

### What is still a signature, not an implementation

As of 2026-10-03, nothing: `grep -rn NotImplementedError apps/api/src` is empty.
Retention (Week 6) and `/metrics/summary` (Week 7) are implemented. What remains is
work that needs people or infrastructure — see "Next, in order" at the top.

---

## 2. Do this next, in this order

### Step 0 — make it run  ✅ DONE (verified this session)

```bash
cp .env.example .env
docker compose up -d postgres redis
cd apps/api && uv sync --extra dev
uv run alembic upgrade head
uv run python ../../scripts/seed_dev_users.py
uv run uvicorn opspilot.main:app --host 127.0.0.1 --port 8000
```

**Verified working:** 21 tables migrated, `alembic check` reports no drift, pgvector /
pg_trgm / pgcrypto installed, `GET /health/live` 200, `GET /health/ready` 200 with a
live database check, structured JSON logs with per-request correlation ids and secret
redaction, UUIDv7 primary keys.

**Verified security behaviour** (each exercised over HTTP against the running API):

| Probe | Result |
| --- | --- |
| Composite tenant FK: order in org B referencing org A's customer | rejected by PostgreSQL (`fk_customer_id_tenant_customers`) |
| Same `order_number` (SO-1042) in both organizations | accepted — the collision is intentional |
| No token → `GET /orders` | 401 `auth.unauthenticated` |
| Tampered signature → `GET /me` | 401, generic message (no verification oracle) |
| Analyst's token + another org's `X-Organization` | 403 `auth.forbidden` |
| Analyst → supervisor-only `POST /documents` | 403 `auth.forbidden` |
| Supervisor → same route | passes authz, reaches the unimplemented service (500) |
| Dev token for an unknown email | 404, not 403 |

Nine defects were found and fixed by doing this, all in the scaffold rather than the
design. The five I predicted plus four I did not:

1. `VersionedMixin` — removed; `Order` declares `version_id_col` itself. *(predicted)*
2. `Role` enum used by two tables → `models/enum_types.py` now declares every native
   enum exactly once. *(predicted)*
3. `postgresql_where=(col == value)` in `__table_args__` — a `MappedColumn` is not a
   SQL expression at class-definition time; switched to `text(...)`. *(new)*
4. `memberships.organization_id` had no foreign key at all. *(new)*
5. Alembic autogenerate emits `pgvector.sqlalchemy...VECTOR` without importing it —
   the import is now in `script.py.mako` for every future migration. *(new)*
6. `Settings` read `.env` relative to the working directory, so it silently found
   nothing when run from `apps/api`; it now resolves from the repo root. *(new)*
7. `structlog.stdlib.add_logger_name` with `PrintLoggerFactory` crashed on startup,
   and `.bind()` at module import froze the default config before
   `configure_logging` ran. Both fixed; a logger created at import now picks up the
   later configuration. *(new)*
8. Host port 5432 was already taken by a native PostgreSQL, and Docker bound the
   conflict **silently** — the container simply had no published port. Host ports moved
   to 55432/56379 and made configurable. *(new)*
9. `localhost` resolves to `::1` first on Windows and the Docker port proxy stalls
   there for ~30 s before falling back to IPv4; database URLs now use `127.0.0.1`.
   This turned every DB-touching request into an apparent hang. *(new)*

Also `email-validator` was missing (needed by `EmailStr`), and `.test` is a
special-use TLD it rejects — seed identities use `example.com` domains.

### Step 1 — Week 1, foundation and deterministic data  ✅ COMPLETE

1. **Dev issuer** — `auth/dev_keys.py` generates an RSA keypair on first use at
   `.data/dev-auth-key.pem`; `POST /auth/dev/token` mints RS256 tokens for seeded
   memberships only; `GET /me` returns the server-resolved principal.
2. **Identity seed** — `scripts/seed_dev_users.py`: two organizations, four
   memberships (analyst + supervisor each), idempotent.
3. **Dataset** — `data/seed/generate.py` plus `data/seed/opspilot_seed/`. Verified
   output:

   | | Northwind | Harbor |
   | --- | --- | --- |
   | customers / products / orders | 25 / 100 / 150 | 25 / 100 / 150 |
   | order lines | 377 | 382 |
   | shipments / events | 132 / 339 | 135 / 357 |
   | exception cases | 18 | 12 |

   30 exception cases in total, five of each class across the two organizations, written
   to `evals/cases/seed_scenarios.json` with `expected_findings` deliberately `null` —
   a human fills those in. Five prompt-injection payloads are planted as carrier notes
   on the `carrier_delay` cases.

   **Determinism verified**: regenerating produced an identical sha256 over orders,
   lines, shipments and events. Ids are UUIDv5 over stable keys, so evaluation cases
   keep pointing at the same rows across a rebuild.

4. **Orders** — `services/orders.py` defines "overdue" once, for both the UI and the
   future `list_overdue_orders` tool; `GET /orders` and `GET /orders/{id}` go through
   `TenantScopedRepository` and return evidence ids in the agent's citation format
   (`ord:`, `shp:`, `evt:`).
5. **Operations inbox screen** — `apps/web` builds clean under strict TypeScript and
   serves; dev sign-in, `/me`, and the overdue list were exercised through the Vite
   proxy into FastAPI into Postgres.

#### Week 1 gate — PASSED, and reproducible

`python scripts/verify_gates.py` drives the running API over HTTP and reports
pass/fail. Current result: **11 passed, 0 failed**.

```
Authorization      unauthenticated read refused; tampered token refused;
                   another organization cannot be selected by header
Tenant isolation   SO-1042 is a different row per organization;
                   cross-tenant fetch is 404 not 403; own-tenant fetch succeeds;
                   detail carries an evidence id and a record version
Role boundaries    supervisor can_approve true, analyst false
Operations         readiness reports the database
```

Run it after any deploy, migration, or change to the authorization path. It exercises
middleware, dependencies and error handlers rather than the functions beneath them,
which is where this class of bug actually lives.

`ruff check src` and `ruff format` are clean; `pnpm --filter @opspilot/web build`
passes under strict TypeScript.

Two more defects fixed along the way: `scalar_subquery().element` misuse in the flagged
-orders query, and Vite binding `[::1]` only on Windows so `127.0.0.1:5173` refused
connections while `localhost:5173` worked (now pinned to IPv4 in `vite.config.ts`).

### Step 2 — Week 2, first complete investigation  ✅ COMPLETE

The agent runs end to end. `POST /runs` returns 202, the worker picks the run up, and
the case workspace shows evidence-backed findings and a pending proposal.

1. **Three read tools** — `services/tools.py`. Each returns `(payload, evidence_ids)`;
   the dispatcher grants those ids before the model can cite them. Results are bounded
   (50 overdue rows, 40 events, 500-char carrier notes) because an unbounded tool
   result is a denial-of-budget vector. `search_policies` returns an explicit empty
   result until Week 3 — the honest answer while nothing is indexed, and it exercises
   the abstention path.
2. **Providers** — `fake.py` (deterministic, six scenarios), `openai_provider.py`, and
   `anthropic_provider.py`, behind `build_provider()`. The fake reads evidence ids out
   of the tool results it was handed, so its citations are real by default; a fake that
   invented ids would make the validation path pass trivially.
3. **Orchestrator** — bounds, retry accounting, step records, and the citation policy
   from [ADR 0010](adr/0010-unsupported-citations.md): strip unsupported claims, count
   before stripping, mark the run `insufficient_evidence`.
4. **Run lifecycle** — `services/runs.py`, `workers/tasks.py::investigate`. Atomic
   claim, authorization re-derived from the database, proposal persisted with a payload
   hash bound to the order's `record_version`.
5. **Budget** — `services/budget.py`. Reserve-then-reconcile in Redis, integer
   micro-dollars, fails closed when Redis is unreachable.
6. **Case workspace** — findings split into fact vs inference, evidence chips, an
   incomplete banner naming the reason, and a proposal block with no approve control
   (approval belongs to the supervisor screen, against the stored payload).

#### Verified this session

`python scripts/verify_gates.py` — **18 passed, 0 failed**, now including an
investigation section: run accepted async, reaches a terminal state, every citation
supported, every finding carries evidence, tool activity recorded, proposal pending not
executed, and another organization cannot read the run.

`uv run python ../../scripts/probe_agent.py` drives all six fake-provider scenarios
offline and asserts the expected behaviour of each:

| Scenario | Observed |
| --- | --- |
| healthy | 3 turns, 2 tool calls, 3 citations, all supported |
| unsupported_citation | cited 4, stripped 1, run marked `insufficient_evidence` |
| malformed_output | rejected, `provider.invalid_output`, no result |
| unavailable | one bounded retry, then `provider_unavailable` |
| tool_loop | stopped at `turn_limit` |
| no_evidence | abstained, listed what was missing |

**Duplicate delivery** — re-publishing `run.investigate` three times for a finished run
left proposals=1, steps=6, grants=3 and the status unchanged; the worker logged
`already_claimed` → `noop` each time. That is a Week 5 gate passing early.

Observed run: 3 turns, 2 tool calls, 2640/540 tokens, 14ms, `needs_review` with a
`pending` proposal whose payload was fully server-resolved — the model supplied only a
description and a relative due date.

#### Three bugs found by running it

1. **`shared_task` published to the wrong broker.** It binds to Celery's `current_app`,
   which in a web process that never imported `workers.app` is the default app pointing
   at `amqp://localhost`. Runs sat `queued` forever. Now published by task *name*
   through the configured app, which also keeps worker imports out of the web process.
2. **`session_scope` masked the real error.** An `after_commit` hook that raises leaves
   the session committed, and the `except` branch's `session.rollback()` then raised
   `InvalidRequestError` over the top of it — a broker outage reported as a SQLAlchemy
   state bug. Now guarded by `session.in_transaction()`.
3. **A broker blink could strand a run.** The row commits before the message is
   published, so a publish failure left a queued run nobody would process — the dual-
   write problem the outbox exists to solve. Publish failure is now logged rather than
   raised, and `run.requeue_stranded` (beat, every 60s) re-publishes anything still
   `queued` after a minute. Safe because `claim_run` makes redelivery a no-op.

**Gate still outstanding:** at least 8 of 10 *hand-reviewed* seeded investigations must
describe the order facts correctly. That needs a human reading real model output, so it
waits for a live provider key. The deterministic half of the gate — zero fabricated
order references — is enforced in code and verified.

### Step 3 onward

Weeks 3–10 are specified in [plan/02-weekly-checklist.md](plan/02-weekly-checklist.md)
with gates, and the release thresholds are in
[plan/03-evaluation-gates.md](plan/03-evaluation-gates.md). The scaffold already
encodes those weeks in `NotImplementedError` messages, so `grep -rn "Week 3" apps/api`
gives the work list for any week.

---

## 3. Open questions for the project owner

Not blocking; each becomes blocking at the week named.

1. ~~Unsupported citations — fail or strip?~~ **Decided:** strip for display, count
   before stripping for the gate. See [ADR 0010](adr/0010-unsupported-citations.md).
   Original question, for context: (Week 2) When the model cites evidence
   it was never granted, does the run fail, or is the claim removed and the run marked
   incomplete? This changes what the 95% citation-support gate measures. Currently the
   orchestrator logs and continues, which is a placeholder, not a decision.
2. **Multi-organization users.** (Week 1.) Should one person hold memberships in both
   seeded organizations? It makes the tenancy tests much sharper — the same token,
   two organizations — but requires the organization switcher in the UI earlier.
3. ~~`GET /orders` pagination style~~ — offset, capped at 100 rows. Fine at 300
   orders; revisit only if the dataset grows.
4. **Auth0 tenant** — needed by Week 8, not before. Confirm plan limits then.

---

## 4. Standing rules for anyone continuing this work

1. **Never claim something works without running it.** This file's honesty is the
   reason it is useful. "Scaffolded", "implemented", "tested" are three states.
2. **Update this file at the end of every session**, plus the relevant week's
   checklist. A stale handoff is worse than none.
3. **Keep `domain/` pure.** No DB, no IO, no framework imports. It is where the
   invariants live precisely because it can be reasoned about alone.
4. **Never bypass `TenantScopedRepository`** for a tenant table. If you think you must,
   that is an ADR, not a line of code.
5. **Never widen the tool allowlist** without an ADR. Three read tools and one
   approved write is the design, not a starting point.
6. **Never describe delivery as exactly-once.** At-least-once with idempotent effects.
7. **Bump `PROMPT_VERSION`** with any prompt change, or past evaluation results become
   unattributable.
8. **Synthetic data and the simulated ERP stay labelled** in the UI and the README.
   The plan is explicit that this is only called production experience once real people
   use the deployed workflow.
