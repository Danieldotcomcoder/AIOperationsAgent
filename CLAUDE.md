# OpsPilot — instructions for AI coding assistants

Read [docs/HANDOFF.md](docs/HANDOFF.md) first (current state, next task), then
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The original planning documents are in
[docs/plan/](docs/plan/) and remain the product contract.

## What this is

A multi-tenant operations agent: it investigates delayed orders using three
allowlisted read tools plus policy retrieval, returns typed evidence-linked findings,
and proposes a follow-up task that a supervisor must approve before a durable worker
executes it idempotently.

The point of the project is the engineering around the model — tenancy, bounded
autonomy, grounding, human control, durability, measurement — not the model output.
Changes that weaken any of those to make a demo smoother are the wrong change.

## Stack

React 18 + TypeScript + Vite · FastAPI + Pydantic v2 + SQLAlchemy 2 (sync) + Alembic ·
PostgreSQL 16 + pgvector · Celery + Redis · Docker Compose + Caddy · uv, ruff, mypy.

## Non-negotiable rules

1. **`organization_id` is server-derived.** From the membership row for the verified
   JWT subject. Never from a body, query, header alone, or tool argument. Every tenant
   read goes through `TenantScopedRepository`.
2. **A cross-tenant read is 404, not 403.**
3. **`domain/` is pure.** It may import `core/` and nothing else. No DB, no IO, no
   framework.
4. **Services own transactions.** One request or one task is one unit of work. A state
   change and its outbox event share a transaction.
5. **Three read tools, one approved write.** Widening the allowlist requires an ADR.
6. **The model cannot execute anything.** It proposes; the backend persists; the
   execute worker acts after approval.
7. **Citations are validated in code** against `evidence_grants` for that run.
8. **At-least-once delivery with idempotent effects.** Never write "exactly once".
9. **Retries count toward the run bounds** (3 turns, 6 tool calls, 90 s).
10. **Bump `PROMPT_VERSION`** in `agent/prompts.py` with any prompt change.
11. **Log metadata, never content.** No prompts, document text, notes, or secrets.
12. **Never claim tests pass without running them.** Report actual output.

## Layout

```
apps/api/src/opspilot/   core → domain → models → db → services/{agent,retrieval,ops} → api
apps/web/src/            api client, app shell, feature screens
packages/contracts/      shared TS types generated from the OpenAPI schema
data/seed/               deterministic generator (fixed seed, frozen clock)
evals/                   80 labelled cases, 40 dev / 40 held out
infra/, docs/            deployment and design
```

Dependencies point downward only. A module never imports from a layer above it.

## Working style

- Inspect existing code before proposing a rewrite. The scaffold encodes decisions;
  read the ADR before changing one.
- `NotImplementedError("... — Week N")` marks scheduled work. `grep -rn "Week 3"` gives
  that week's task list.
- Make the smallest coherent change. Report changed files, verification actually run,
  and remaining risk.
- Do not add a dependency without saying why the existing ones are insufficient. No
  agent framework, no reranker, no vector index without a measurement.
- Update `docs/HANDOFF.md` at the end of a session.

## Commands

```bash
cp .env.example .env
docker compose up -d postgres redis
cd apps/api && uv sync
uv run alembic upgrade head
uv run uvicorn opspilot.main:app --reload
uv run ruff check . && uv run mypy src
pnpm install && pnpm dev
```
