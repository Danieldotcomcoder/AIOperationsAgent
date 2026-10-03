# ADR 0001 — One Postgres for business records, vectors, run state and outbox

**Status:** Accepted — 2026-09-12

## Context

The system needs relational business data, vector similarity search, durable run
state, and a reliable outbox. The obvious "correct" architecture would use a managed
vector database and a separate operational store. The budget is USD 20–35/month total
on one 2 vCPU / 4 GB VPS, and the corpus is ~20 documents.

## Decision

Use a single PostgreSQL 16 instance with the `pgvector` extension for everything.

## Consequences

- The outbox pattern works, because the domain write and the event insert are in the
  same transaction on the same database. With a separate store this guarantee is lost
  and the whole reliability story becomes hand-waving.
- Retrieval filters (`organization_id`, effective version) are ordinary SQL predicates
  applied *before* ranking, rather than metadata filters applied after an ANN search.
- One backup, one restore drill, one connection pool, one place to check consistency.
- Vector search competes with OLTP for the same CPU. Acceptable at 3 concurrent users
  and a few thousand chunks; not acceptable at scale.

## Revisit when

Chunk count exceeds roughly 10^5, p95 retrieval latency exceeds 200 ms with an index
in place, or embedding refresh starts blocking transactional work.
