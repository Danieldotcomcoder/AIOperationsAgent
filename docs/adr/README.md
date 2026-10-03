# Architecture Decision Records

One file per decision, numbered, never edited after acceptance — a reversal is a new
ADR that supersedes the old one. Keep them short: context, decision, consequences,
and the signal that would make us revisit.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-single-postgres-for-everything.md) | One Postgres (business + vectors + run state + outbox) | Accepted |
| [0002](0002-server-derived-tenant-context.md) | Tenant context derived server-side, never model- or client-supplied | Accepted |
| [0003](0003-transactional-outbox.md) | Transactional outbox with idempotent downstream effects | Accepted |
| [0004](0004-proposal-approval-binding.md) | Approval binds to payload hash + source version + expiry | Accepted |
| [0005](0005-provider-adapter.md) | Thin provider adapter, two real providers + a deterministic fake | Accepted |
| [0006](0006-one-image-many-entrypoints.md) | API and workers ship as one image | Accepted |
| [0007](0007-exact-vector-search-first.md) | Exact vector search before any index or hybrid retrieval | Accepted |
| [0008](0008-pluggable-oidc-verifier.md) | Pluggable OIDC verifier with a hard-gated dev issuer | Accepted |
| [0009](0009-synchronous-io.md) | Synchronous database and worker IO | Accepted |
| [0010](0010-unsupported-citations.md) | Strip unsupported citations and preserve pre-strip accounting | Accepted |
| [0011](0011-openrouter.md) | OpenRouter with free-only routing by default | Accepted |
| [0012](0012-policy-ingestion.md) | Bounded policy ingestion and effective revision intervals | Accepted |
| [0013](0013-durable-approval-execution.md) | Durable execution claim and reconciliation before retry | Accepted |
| [0014](0014-run-lifecycle-retention.md) | Durable cancellation, bounded retention, and usage receipts | Accepted |
| [0015](0015-liquid-embeddings.md) | Free Liquid embeddings with cosine-preserving storage padding | Accepted |
