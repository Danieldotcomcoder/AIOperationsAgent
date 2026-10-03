# OpsPilot — Data Model

One PostgreSQL 16 database, one `public` schema, `pgvector` extension. Every
tenant-owned table carries `organization_id` as the first column after the primary
key. All primary keys are UUIDv7-style time-ordered UUIDs generated in the
application (`core/ids.py`) so that inserts stay index-friendly and IDs are safe to
expose.

Conventions applied to every table:

- `id UUID PRIMARY KEY`
- `created_at`, `updated_at` as `TIMESTAMPTZ NOT NULL DEFAULT now()`
- Money as `NUMERIC(12,2)`; never float.
- Enumerations as native PostgreSQL enums, defined once in `domain/enums.py`.
- Tenant tables additionally declare `UNIQUE (organization_id, id)` so that children
  can reference the parent with a **composite foreign key** `(organization_id,
  parent_id)`. This is what makes a cross-tenant association fail in the database.
- Soft deletes only where an audit trail requires it (`documents.deleted_at`).
  Everything else is a hard delete with `ON DELETE RESTRICT`.

## Group A — Identity and tenancy

**organizations** — `slug UNIQUE`, `name`, `timezone`, `settings JSONB`.

**memberships** — `organization_id`, `subject` (OIDC `sub`), `email`, `display_name`,
`role` (`analyst` | `supervisor`), `status` (`active` | `revoked`), `revoked_at`.
`UNIQUE (subject, organization_id)`; index on `(subject) WHERE status = 'active'`.
This table is the *only* source of organization and role. A JWT never carries them.

> A subject may hold memberships in several organizations. The active organization is
> chosen at login and re-resolved on every request; it is never taken from a header
> without re-verification against this table.

## Group B — Operational records (the facts the agent reads)

**customers** — `organization_id`, `external_ref`, `name`, `email`, `phone`,
`address_json`. `UNIQUE (organization_id, external_ref)`.

**products** — `organization_id`, `sku`, `name`, `category`, `unit_price`.
`UNIQUE (organization_id, sku)`.

**orders** — `organization_id`, `order_number`, `customer_id`, `status`
(`open` | `partially_shipped` | `shipped` | `delivered` | `cancelled`), `placed_at`,
`promised_date`, `total_amount`, `notes`, `record_version INT NOT NULL DEFAULT 1`.
`UNIQUE (organization_id, order_number)` — **not** unique globally; order numbers
collide across organizations on purpose.

`record_version` increments on every material change and is what an approval binds
to. It is maintained by SQLAlchemy's `version_id_col`, so a concurrent update raises
`StaleDataError` rather than silently winning.

**order_lines** — `organization_id`, `order_id`, `product_id`, `quantity`,
`quantity_shipped`, `unit_price`. Composite FK to `(organization_id, order_id)`.

**shipments** — `organization_id`, `order_id`, `carrier`, `tracking_reference`
(nullable — "missing tracking" is a seeded exception class), `status`, `shipped_at`,
`estimated_delivery`, `delivered_at`.

**shipment_events** — `organization_id`, `shipment_id`, `event_type`
(`dispatched` | `in_transit` | `delayed` | `exception` | `delivered` | `note`),
`occurred_at`, `source_event_id` (carrier-side ID, unique per org), `payload JSONB`,
`note TEXT`. **`note` is untrusted text** and is a designated prompt-injection
surface in the evaluation set. Index `(organization_id, shipment_id, occurred_at)`.

Evidence IDs exposed to the model are of the form `evt:<uuid>`, `ord:<uuid>`,
`shp:<uuid>`, `pol:<document_version_id>#<chunk_ordinal>`. `domain/evidence.py` owns
parsing and formatting; nothing else constructs these strings.

## Group C — Knowledge (policies and retrieval)

**documents** — `organization_id`, `title`, `source_filename`, `content_type`
(`text/markdown` | `text/plain`), `status` (`pending` | `indexing` | `active` |
`failed` | `deleted`), `deleted_at`, `uploaded_by`.

**document_versions** — `organization_id`, `document_id`, `version_number`,
`content_hash` (sha256 of normalized bytes, `UNIQUE (organization_id, content_hash)`),
`raw_text TEXT`, `effective_from DATE NOT NULL`, `effective_to DATE NULL`,
`is_active BOOL`, `indexed_at`, `embedding_model`, `embedding_dim`.

*Currently effective* means `effective_from <= as_of AND (effective_to IS NULL OR
effective_to > as_of) AND is_active`. Superseded versions stay in the table — the
evaluation set contains conflicting historical policies and the correct behaviour is
to cite the effective one and, where relevant, say that an older one differs.

**chunks** — `organization_id`, `document_version_id`, `ordinal`, `heading_path`
(e.g. `Delayed shipments > Carrier delay > Escalation`), `text`, `token_count`,
`embedding vector(1536)`. Retrieval filters by organization and effective version
**before** ranking; no post-filtering of a global ANN result. Exact search
(`<=>` with no index) is correct at this corpus size; add `ivfflat`/`hnsw` only when
measured latency justifies it, and record the decision in an ADR.

## Group D — Agent execution

**agent_runs** — `organization_id`, `initiated_by` (membership id), `request_text`,
`target_order_id` (nullable), `status` (see STATE-MACHINES.md), `incomplete_reason`,
`prompt_version`, `model_name`, `provider`, `tokens_in`, `tokens_out`, `cost_usd`,
`turn_count`, `tool_call_count`, `retry_count`, `started_at`, `finished_at`,
`duration_ms`, `error_code`, `result_json JSONB` (the validated structured output),
`trace_expires_at`, `trace_pruned_at`, `cancel_requested_at`, `budget_reservation JSONB`
(original quota/spend counter receipt for idempotent settlement).

**run_steps** — `organization_id`, `run_id`, `ordinal`, `step_type`
(`model_call` | `tool_call` | `validation`), `tool_name`, `arguments_json`,
`result_digest`, `result_row_count`, `latency_ms`, `tokens_in`, `tokens_out`,
`error_code`. Stores digests and metadata, not raw document bodies.

**evidence_grants** — `organization_id`, `run_id`, `evidence_id`. The exact set of
evidence IDs handed to the model during this run. Final-answer citation validation is
a set-membership check against this table, which is why an invented citation cannot
pass.

## Group E — Approval and durable execution

**proposed_actions** — `organization_id`, `run_id`, `action_type`
(`create_followup_task` only, for now), `payload_json JSONB` **immutable**,
`payload_hash` (sha256 of the canonical JSON serialization; `domain/hashing.py`),
`source_record_type`, `source_record_id`, `source_record_version`, `status`
(see STATE-MACHINES.md), `expires_at`, `created_at`.
`UNIQUE (organization_id, run_id, payload_hash)` prevents duplicate proposals from a
retried run.

**approvals** — `organization_id`, `proposed_action_id`, `decision`
(`approved` | `rejected`), `approver_membership_id`, `approver_role_at_decision`,
`payload_hash_at_decision`, `source_version_at_decision`, `decided_at`, `comment`.
Append-only; a re-proposal creates a new action, never a second approval row on the
old one.

**outbox_events** — `id`, `organization_id`, `aggregate_type`, `aggregate_id`,
`event_type`, `payload_json`, `created_at`, `published_at NULL`, `attempts`,
`next_attempt_at`, `last_error`. Claimed by the dispatcher with
`FOR UPDATE SKIP LOCKED`. Index on `(published_at, next_attempt_at)` for the sweep.

**operation_tasks** — the downstream effect ledger: `organization_id`,
`proposed_action_id`, `idempotency_key UNIQUE`, `downstream_task_id`, `status`
(`pending` | `succeeded` | `failed` | `unknown`), `attempts`, `last_attempt_at`,
`response_json`, `reconciled_at`.

The idempotency key is deterministic:
`sha256(organization_id | proposed_action_id | payload_hash)`. It is therefore stable
across retries and across worker restarts, which is what makes at-least-once delivery
safe.

**ops_tasks** — the simulated ERP's own table, written only by `opspilot.ops`.
`organization_id`, `idempotency_key UNIQUE`, `order_id`, `assignee_id`, `due_at`,
`description`, `status`. A duplicate key returns the existing row's ID with `200`
rather than creating a second task.

## Group F — Audit and feedback

**audit_events** — `organization_id`, `actor_type` (`user` | `system` | `worker`),
`actor_id`, `event_type`, `object_type`, `object_id`, `occurred_at`, `metadata JSONB`,
`request_id`. Append-only; retained 90 days. Every authorization denial, approval
decision, document deletion and execution outcome writes one row.

**feedback** — `organization_id`, `run_id`, `membership_id`, `usefulness`
(`useful` | `partly` | `not_useful`), `correction_text`, `failure_class`,
`created_at`.

## Retention

| Data | Retention | Mechanism |
| --- | --- | --- |
| Run trace content (requests/results, steps, grants, feedback text) | 14 days; active workflows excluded | `beat` job `retention.prune_traces`, 100 runs/batch |
| Audit events | 90 days | `beat` job `retention.prune_audit`, 500 events/batch |
| Documents | until deleted by a user | `DELETE /documents/{id}` cascades to versions/chunks |

Deletion of a document must remove its chunks from future retrieval in the same
transaction. A run that already cited a deleted passage keeps its stored citation but
the resolver returns 404. Both retention jobs run hourly. Run accounting and immutable
proposal/approval/operational records remain; feedback is blocked after trace pruning.
`document_versions.index_requested_by` stores the membership requesting initial or
retry indexing, with a composite tenant FK. The worker rechecks active supervisor
authority before embedding.

## Migration policy

Alembic, one head, no branch merges. Every migration is reversible or explicitly
documents why it is not. Expand/contract for anything a running previous image reads:
add nullable, backfill, switch reads, drop in a later release. Never rename a column
in a single release.
