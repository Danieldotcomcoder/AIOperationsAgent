# 02 — Weekly Build Checklist

## Working agreement

Budget 10–12 focused hours each week. Each week ends with a demonstrable result. If a gate fails, spend the next session fixing it before expanding scope. AI assistance accelerates implementation; you own review and verification.

## Week 1 — Foundation and deterministic data

- [ ]  Create the repository, Docker Compose, health endpoints, migrations, and environment examples.
- [ ]  Generate the two-organization seed dataset with a frozen clock.
- [ ]  Connect OIDC login and backend membership checks.
- [ ]  Build a basic order list and detail screen.

**Done when:** a clean checkout boots with documented commands, and users from organization A cannot fetch B's order by ID.

**AI prompts:** master context, milestone implementation, seed data.

## Week 2 — First complete investigation

- [x]  Implement get_order_context and list_overdue_orders with bounded SQL queries.
- [x]  Add the model adapter, structured investigation schema, and three-turn limit.
- [x]  Show evidence references and incomplete/error states in the case workspace.
- [ ]  Hand-review ten seeded investigations.

2026-09-25: live Nemotron generated structured answers for five distinct seeded cases
before the account's free-request quota blocked case six. No human grading yet; the
8/10 gate is still open. The last bounded model call now requests synthesis rather
than more tools. [Resume and review instructions](../runbooks/WEEKS-2-3-VALIDATION.md).

**Gate:** at least 8 of 10 correctly describe the order facts, with zero fabricated order references. Fix schema/tool errors before adding RAG.

**AI prompts:** milestone implementation, agent orchestration.

## Week 3 — Policy retrieval

- [x]  Implement and PostgreSQL-test the exact-search foundation: tenant/effective/deletion filters before ranking, bounded passages, and citation IDs (2026-09-23; 12 tests pass).
- [x]  Add document upload, versioning, chunking, embedding, and deletion (development workflow tested 2026-09-23; paid embedding transport mocked, live validation remains).
- [x]  Implement authorized search_policies and citation links (API/worker smoke check passed 2026-09-23).
- [ ]  Create 20 hand-labeled retrieval questions and inspect misses.
- [x]  Include missing-policy and conflicting-version cases (automated effective-interval tests; human relevance evaluation remains).

**Done when:** the expected active policy appears in the top five passages for at least 18 of 20 questions, and tenant/document deletion tests pass.

2026-09-25: added the requested free Liquid/OpenRouter embedding adapter and twenty
unreviewed question candidates. Live embedding/indexing and retrieval scoring remain
open because of provider quota and missing human labels. Fake embeddings do not count
as semantic validation. The retrieval runner refuses unreviewed labels.

**AI prompts:** RAG implementation, evaluation design.

## Week 4 — Approval workflow

- [x]  Persist immutable proposals and show exact payloads in the approval screen.
- [x]  Implement supervisor-only approval/rejection and expiration.
- [x]  Build the internal operations API and unique task execution keys.
- [x]  Invalidate approvals when payloads or source versions change.

Verified 2026-09-23: PostgreSQL checks for role/hash/version/expiry enforcement and
live approval-to-task creation. Rejected or stale proposals create no task. No real
model credentials are needed for this deterministic workflow gate.

**Done when:** an approved action produces one task; rejection, stale approval, and an analyst's approval attempt produce none.

**AI prompts:** approval implementation, security review.

## Week 5 — Durable execution and recovery

- [x]  Move investigation and execution into Celery workers.
- [x]  Add transactional outbox dispatch, retry backoff, time limits, and failure states.
- [x]  Add reconciliation for uncertain downstream outcomes.
- [x]  Exercise worker termination and duplicate queue delivery (Windows solo worker and live beat).

2026-09-24: actual worker termination before/after the ERP write, restart, scheduled
reconciliation, and duplicate delivery passed. Linux prefork and whole-host recovery
remain deployment validations.

**Done when:** a crash after task creation and before acknowledgment does not create a second task, and the UI shows eventual success or a recoverable failure.

**AI prompts:** reliability engineering.

## Week 6 — Tenant isolation and cost controls

- [x]  Review all business endpoints, tools, caches, and worker contexts for tenant authorization.
- [x]  Add per-user quotas and atomic usage reservations.
- [x]  Implement redacted logs, retention jobs, and document-injection test cases.
- [x]  Add run cancellation and disable-write feature flags.

2026-09-24: 134 tests pass with PostgreSQL/Redis, including concurrent cancellation,
revocation, atomic admission, retention boundaries, and deterministic hostile-input
tests. Full backend typing and frontend build/typecheck pass. See
[the runbook](../runbooks/RETENTION-CANCELLATION.md) for cooperative cancellation and
retention exceptions. Live-model and human quality gates remain open; Weeks 2–3
validations are next, before Weeks 7–10.

**Done when:** the security suite passes, budget exhaustion blocks new calls, and no rejected input reaches an unauthorized tool.

**AI prompts:** security review, milestone implementation.

## Week 7 — Evaluation and observability

- [x]  Finalize 80 labeled scenarios and freeze a held-out split (`evals/cases/v1`, 40/40 stratified, checksummed; labels derived from seed rules and the system contract — human confirmation fields open).
- [x]  Add reproducible evaluation commands and reports (`pnpm eval run`, isolated database per run).
- [x]  Record model/prompt versions, latency, tokens, retries, tool outcomes, and approval outcomes (per run; Metrics page and `/metrics/summary`).
- [x]  Compare the agent to a deterministic policy-template baseline (same code checks; usefulness needs human grading).

**Done when:** quality and cost are measured, failure categories are visible, and all critical release gates pass.

2026-10-03: all 35 deterministic cases pass (authorization, approval integrity,
idempotency gates). Failure categories and cost/latency are visible in Metrics and in
reports. Quality is **not** yet measured: the human rubric is ungraded, and a live
evaluation of all 45 model cases needs more free-tier quota than one day allows (a
3-case live smoke passed its code checks). See
[the evaluation runbook](../runbooks/EVALUATION.md).

**AI prompts:** evaluation design, results review.

## Week 8 — Hosted pilot deployment

- [ ]  Deploy static UI, API, worker, Redis, Postgres, and HTTPS on one small VPS.
- [ ]  Keep Postgres/Redis private; configure secrets and authenticated access.
- [ ]  Limit worker concurrency, rotate logs, and set uptime/error alerts.
- [ ]  Create an encrypted off-server backup and restore into an isolated environment.
- [ ]  Verify rollback to the prior application image and compatible migrations.

**Done when:** a second person can sign in and complete the workflow; restore and rollback are documented.

2026-10-03: prepared, not deployed. Production compose (private data network, log
rotation, memory caps, per-role health checks), image build/deploy/rollback and
encrypted backup/restore scripts, and [the runbook](../runbooks/DEPLOY.md) exist.
Both production images build, and the production stack ran locally behind Caddy
with HTTPS and a private data network; a dump restored into an isolated database
with matching counts. Blocked on
the owner: a VPS and domain, an Auth0 tenant (the SPA's Auth0 sign-in is not built
yet), a registry and an off-server backup target.

**AI prompts:** deployment and runbook.

## Week 9 — Real-user pilot

- [ ]  Invite 2–3 consenting testers; keep the dataset synthetic.
- [ ]  Collect at least 30 investigations across normal and ambiguous cases.
- [ ]  Measure active handling time against the manual workflow for matched tasks.
- [ ]  Record approval rate, corrections, failures, and feedback.
- [ ]  Fix the highest-impact observed issue and rerun affected evaluations.

**Done when:** you have actual usage evidence, including limitations. Without testers, call it a deployed demo and keep the pilot milestone open.

**AI prompts:** results review.

## Week 10 — Buffer and portfolio evidence

- [ ]  Resolve remaining release blockers and document known limitations.
- [ ]  Record a 3–5 minute demo: investigate, cite, approve, execute, recover.
- [ ]  Publish a README with architecture, setup, evaluation method, measured results, and cost assumptions.
- [x]  Write one short incident report from a real failure-injection exercise ([worker killed around the ERP write](../incidents/2026-09-24-worker-killed-during-write.md)).
- [ ]  Prepare a resume bullet with real usage/quality numbers only.

**Done when:** someone can reproduce the evaluation and understand what the system actually does.

## If progress slips

Keep one investigation workflow, one write tool, plain-text documents, and polling. Cut visual polish, streaming, new integrations, and optional search improvements first. Preserve authorization, approval integrity, recovery, and evaluation.

## Your first three sessions

1. Two hours: write the one-page scope, create the repository/Compose setup, and load ten orders.
2. Two hours: implement authorized order lookup and show one order in React.
3. Two hours: call the model on that order and validate a structured, evidence-linked response.

Start here before designing a generalized agent framework.
