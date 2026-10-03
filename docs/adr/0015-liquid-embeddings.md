# ADR 0015 — Free Liquid embeddings in the existing vector store

Status: Accepted · 2026-09-25

## Context

The user selected `liquid/lfm-2.5-embedding-350m:free` through OpenRouter.
Its native output has 1024 dimensions and its input context is 512 tokens. The
application currently stores 1536-dimensional vectors and uses exact cosine search.

## Decision

Use the existing OpenAI-compatible SDK against OpenRouter's embeddings endpoint with
the existing OpenRouter credential, no SDK retries, bounded timeout, and zero-price
routing. Accept only this explicit embedding model. Preserve Redis admission checks
with zero-dollar reservations that do not consume investigation quotas.

Validate every returned index, native dimension, finite component, and nonzero vector
before adding 512 zeros. Zero padding preserves norms and dot products, hence cosine
distance. Store a distinct `:pad1536-v1` embedding identity; all retrieval continues
to filter by model identity before ranking. No migration or old-vector conversion is
needed. Existing fake/OpenAI versions do not become Liquid-compatible automatically.

The byte-based chunker must use at most 500 bytes per passage, leaving headroom within
the model context. Reject larger query inputs before the network call; do not silently
truncate text, average segments, or claim that an incompatible vector is comparable.

## Consequences and verification

Mocked tests cover transport, free routing, shape/index/NaN/zero rejection and padding.
The first live probe on 2026-09-25 was rejected with OpenRouter's daily free quota
exhausted, so successful live Liquid indexing/retrieval is still unverified.
Revisit if larger contexts, other models, or an approximate vector index are needed.

Sources: [model card](https://openrouter.ai/liquid/lfm-2.5-embedding-350m:free),
[embedding API](https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings).
