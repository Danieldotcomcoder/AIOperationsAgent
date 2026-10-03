# ADR 0009 — Synchronous SQLAlchemy and a synchronous provider adapter

**Status:** Accepted — 2026-09-12

## Context

FastAPI invites `async def` handlers with an async driver. Celery is synchronous. A
codebase that does both ends up with two session idioms, two repository layers, and
`asyncio.run()` calls inside worker tasks — plus the perennial bug where a synchronous
driver call blocks the event loop and nobody notices until latency is measured.

The workload is three concurrent users, a 90-second run budget, and one VPS. The
bottleneck is the model provider's response time, not connection concurrency.

## Decision

Synchronous everywhere: SQLAlchemy 2.0 with psycopg3, `def` route handlers (FastAPI
runs them in its threadpool), a synchronous provider adapter using `httpx`.

## Consequences

- One session idiom, one repository, one transaction pattern shared by the API and the
  workers. The orchestrator reads as straight-line code.
- Per-request concurrency is bounded by the threadpool rather than the event loop.
  Well within budget at this scale; it would not be at a thousand concurrent users.
- Streaming responses would need rework. Streaming is already out of scope for v1.
- The two `async def` functions that remain — the lifespan hook and the correlation-id
  middleware — do no blocking work, which is the rule to keep.

## Revisit when

Sustained concurrency exceeds roughly 50 simultaneous investigations, or streaming
becomes a product requirement.
