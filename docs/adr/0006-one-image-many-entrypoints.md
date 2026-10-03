# ADR 0006 — API and workers ship as one image with different entrypoints

**Status:** Accepted — 2026-09-12

## Context

The deployment target is a single small VPS running Compose. Separate images for API,
investigate worker, execute worker and beat would mean four builds, four pulls, and
four opportunities for version skew between the code that enqueues a message and the
code that consumes it.

## Decision

Build one image, `opspilot-api`, containing the whole Python application. The Compose
services differ only in `command`:

| Service | Command |
| --- | --- |
| `api` | `uvicorn opspilot.main:app` |
| `worker-investigate` | `celery -A opspilot.workers.app worker -Q investigate -c 2` |
| `worker-execute` | `celery -A opspilot.workers.app worker -Q execute -c 1` |
| `beat` | `celery -A opspilot.workers.app beat` |

Migrations run once, from a one-shot `migrate` service, before the others start.

## Consequences

- A deploy moves every process to the same commit simultaneously. Message contracts
  cannot skew across a release, which removes the need for message versioning at this
  scale.
- The image carries web dependencies into the workers. A few MB; irrelevant here.
- Queue separation still gives the isolation that matters: a slow investigation
  cannot starve approved-action execution, and `worker-execute` stays at concurrency
  1 so the write path is serialized per host.

## Revisit when

Processes need to scale or deploy independently, or the investigate worker acquires
heavy dependencies (a local model, OCR) that the API should not carry.
