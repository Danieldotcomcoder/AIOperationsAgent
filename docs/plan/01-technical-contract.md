# 01 — Technical Contract and Data

## Repository

Use one repository with apps/web, apps/api, packages/contracts, data/seed, evals, infra, and docs. Put architecture decisions and the current milestone in docs so AI coding tools can read the same project context. Pin dependencies and commit lockfiles.

## Seed data

Create a deterministic Python generator with a fixed random seed and a frozen clock.

- Two fictional organizations with separate memberships and deliberately overlapping human-readable order numbers.
- 50 customers, 200 products, 300 orders, and approximately 600 shipment events across both organizations.
- 20 Markdown policies, including versioned and conflicting historical policies; clearly define current effective dates.
- 30 seeded exception cases: late dispatch, carrier delay, incomplete shipment, delivered-but-open order, missing tracking, and insufficient evidence.
- Synthetic names and addresses only. No employer records, credentials, or customer documents.

Truth comes from the seed database and hand-reviewed policy rules. Generate expected answers from those facts, then review them manually. Do not let an LLM invent ground truth.

## Data model

Every tenant-owned table includes organization_id. Add indexes and constraints that keep relationships within one organization.

| Table group | Core fields or purpose |
| --- | --- |
| organizations, memberships | OIDC subject, organization, role: analyst or supervisor |
| customers, orders, order_lines | Order number, promised date, status, customer, record version |
| shipments, shipment_events | Tracking reference, timestamp, event type, source ID |
| documents, document_versions, chunks | Content hash, effective date, active version, embedding, citation locator |
| agent_runs, run_steps | Status, initiating user, prompt/model versions, usage, tool results, error codes |
| proposed_actions, approvals | Immutable payload, payload hash, source record version, approver, expiry |
| operation_tasks, outbox_events | Unique execution key, downstream task ID, durable dispatch and reconciliation |
| audit_events, feedback | Actor, event type, timestamp, object reference; usefulness and correction |

## Agent tool contract

The model chooses among three read tools. Organization identity comes from validated server context and is never a model-controlled parameter.

- get_order_context(order_number): parameterized SQL reads returning order, shipments, event IDs, and existing tasks.
- list_overdue_orders(as_of_date, limit): bounded, parameterized SQL query; maximum 50 rows.
- search_policies(query, order_context): retrieve authorized, currently effective policy passages with stable citation IDs.

The model's final structured output includes summary, findings with evidence IDs, policy citations, missing information, and an optional proposed action. The backend persists proposals. The model cannot invoke execution.

**Approved write:** create_followup_task(order_id, assignee_id, due_at, description, idempotency_key). This is an internal operations API you implement; label it as a simulated ERP boundary in the demo. Only the execution worker can call it after validating approval.

## Run lifecycle

Persist state: queued, investigating, needs_review, completed, failed, or cancelled. Keep proposed-action state separate: pending, approved, rejected, expired, executing, succeeded, failed, or reconciliation_required.

A run lasts at most 90 seconds excluding human review. Cap each provider call at 25 seconds and the entire investigation at three model turns and six read-tool calls. Tool/schema retries count toward those limits. Return a useful incomplete status when a limit is reached.

Do not hold a Celery worker open while waiting for approval. Persist the proposal, end the job, and enqueue execution only after approval.

## Approval and execution invariants

1. The server resolves the approver's current membership and supervisor role.
2. Approval binds to the immutable action payload hash, organization, and current source record version.
3. Expire approval after 24 hours. Any payload edit or material source change requires a new proposal and approval.
4. In one transaction, atomically mark the action approved and insert an outbox event. A dispatcher publishes durable outbox events to the queue.
5. The execution worker rechecks authorization, approval, expiration, and source version.
6. The internal operations API enforces a unique idempotency key and returns the same task ID for duplicate deliveries.
7. If execution succeeds but the response is lost, reconcile by that key before retrying. Never assume a network timeout means no action occurred.

This is at-least-once delivery with idempotent effects, not a claim of exactly-once queue delivery. Celery documents the need for idempotent tasks and the behavior of retries and acknowledgments. [Celery task documentation](https://docs.celeryq.dev/en/stable/userguide/tasks.html)

## Retrieval implementation

Upload Markdown/plain text only, maximum 2 MB per document. Store raw text in Postgres for this scope. Chunk by headings, starting around 500 tokens with 75-token overlap. Store heading paths and stable version IDs.

Filter by organization and effective version before ranking. Start with exact vector similarity, retrieve eight passages, and provide the best five within a context budget. Inspect failed retrievals before changing chunking. Add Postgres keyword search and rank fusion only if measured lexical misses justify it.

Citations must resolve to passages supplied to that run. Validate citation IDs in code; assess whether the passage actually supports the claim during evaluation. Stale and deleted documents must disappear from future retrievals.

Missing evidence means an explicit unresolved finding. Do not use the model's self-reported confidence as an authorization decision.

## Authorization and input handling

Validate access on every API, tool, document, approval, feedback, and run-history read. Include organization and document version in cache keys. Recheck membership in background work; never trust a queued organization ID alone.

Treat document text, shipment notes, and user requests as untrusted input. The system prompt should state that they cannot change permissions. Actual enforcement lives in server code: typed arguments, allowlisted tools, bounded results, no arbitrary URLs or shell execution, and no unrestricted SQL.

Render output without executing HTML. Keep provider keys server-side. Log operational metadata rather than full sensitive prompts. Retain pilot traces for 14 days and audit events for 90 days, with explicit deletion jobs.

## API outline

- POST /runs and GET /runs/{id}
- GET /orders and GET /orders/{id}
- POST /documents and DELETE /documents/{id}
- GET /actions and POST /actions/{id}/approve or /reject
- POST /runs/{id}/feedback
- GET /metrics/summary, /health/live, and /health/ready

All business routes require authorization. Use polling initially; streaming can follow if it improves the experience.

## Deferred work

After a successful pilot: one real read-only integration; scanned PDF extraction; hybrid retrieval if needed; a constrained analytics tool; richer tracing. Choose one next capability based on user feedback. Multi-agent coordination, fine-tuning, Kubernetes, and a general-purpose autonomous agent are outside this plan.