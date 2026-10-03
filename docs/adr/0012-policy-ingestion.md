# ADR 0012 — Bounded development policy ingestion

Accepted 2026-09-23 as the first usable Week 3 implementation.

Keep the existing schema, exact pgvector search and three-tool allowlist. Serialize
uploads per organization for content-hash deduplication; lock the document during
indexing and deletion. Only publish indexing after commit. Explicit retry recovers
failed publication/indexing; an automatic recovery sweep remains future work.

A successfully indexed revision defines an effective interval ending when its next
revision starts. Indexed historical/scheduled versions retain `is_active=true` as an
eligibility flag, with date bounds deciding which one is effective. Pending revisions
cannot supersede an indexed one. This refines the scaffold's single-active-version
description: future policies must not hide the policy currently in force.

Use deterministic UTF-8 byte windows with heading paths and overlap as a conservative,
fully offline token bound. This deliberately produces smaller passages than ADR 0007's
~500 model tokens. Evaluate misses before changing chunking; no semantic recall gate
is claimed. Limit each policy to 256 chunks as well as 2 MB.

The `fake-hash-v1` embedding space is an explicitly labelled development fixture using
hashed word counts. Optional `text-embedding-3-small` calls reserve budget and validate
vector shape. Queries filter by embedding model identity and dimension before ranking.
No mixed-space retrieval, silent hosted fallback, new vector index, or reranker.

Deletion removes raw text and vectors and disables every version while retaining
document/version metadata and existing run audit records. Historical passage links
then return 404. The existing unique per-tenant content hash prevents re-upload of
identical deleted content; restoring deleted content is not implemented.

Remaining acceptance work: real-model validation and the 20 human-labelled retrieval
questions. Bulk re-indexing across embedding-provider changes is also not implemented.
