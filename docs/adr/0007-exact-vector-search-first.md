# ADR 0007 — Exact vector search before any index, reranker or hybrid retrieval

**Status:** Accepted — 2026-09-12

## Context

The corpus is 20 policy documents, versioned — order 10^3 chunks. The reflex is to
add an HNSW index, a reranker, BM25, and rank fusion. Each of those is a dependency,
a tuning surface, and a source of "we changed retrieval and quality moved, but we do
not know which change did it".

## Decision

- Start with exact search: `ORDER BY embedding <=> :query` with no vector index, after
  SQL filters on `organization_id` and effective version.
- Retrieve 8 candidates, supply at most 5 within the context budget.
- Chunk by heading, ~500 tokens, 75-token overlap, preserving the heading path.
- Before changing chunking, **inspect the failed retrievals** from the 20 labelled
  questions. Record what the miss actually was.
- Add Postgres keyword search and rank fusion only if measured lexical misses justify
  it; add a vector index only when measured latency justifies it. Either change is a
  new ADR with the measurement attached.

## Consequences

- Recall is exact by construction, so a retrieval failure is an embedding or chunking
  failure and nothing else. That makes week 3 debuggable.
- Latency is O(n) per query; fine at this corpus size, and measured in the harness.
- The gate ("expected active policy in the top 5 for at least 18 of 20 questions") is
  meaningful because there is no ANN approximation hiding in the result.

## Revisit when

Top-5 recall stalls below the gate with clean chunking, or retrieval p95 exceeds
200 ms.
