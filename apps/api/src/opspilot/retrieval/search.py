"""Organization-scoped policy retrieval (ADR 0007).

The ordering of operations is the security property, not an optimization:

    1. filter by organization_id
    2. filter to versions effective on the as-of date
    3. rank by vector distance
    4. take top-k within the token budget

Filtering *after* ranking would mean a passage from another tenant could influence
which passages are returned at all. The filter goes in the WHERE clause.

Used by the policy tool after upload and indexing (ADR 0012).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select
from sqlalchemy.orm import Session

from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import DocumentStatus
from opspilot.domain.evidence import EvidenceKind, format_ref
from opspilot.models.knowledge import EMBEDDING_DIM, Chunk, Document, DocumentVersion


@dataclass(frozen=True, slots=True)
class Passage:
    citation_id: str
    heading_path: str
    text: str
    distance: float
    token_count: int
    document_title: str
    effective_from: date


def effective_versions_stmt(repo: TenantScopedRepository, as_of: date) -> Select[tuple[uuid.UUID]]:
    """Versions this organization may retrieve, as of a date.

    A version is eligible after indexing; its date interval determines when it applies.
    Historical versions cannot appear outside their interval, but remain resolvable
    by citation id until deletion, so an old run's citation remains explainable.
    """
    visible_documents = (
        repo.query(Document)
        .with_only_columns(Document.id)
        .where(
            Document.deleted_at.is_(None),
            Document.status != DocumentStatus.deleted,
        )
    )
    return (
        repo.query(DocumentVersion)
        .with_only_columns(DocumentVersion.id)
        .where(
            DocumentVersion.document_id.in_(visible_documents),
            DocumentVersion.is_active.is_(True),
            DocumentVersion.effective_from <= as_of,
            (DocumentVersion.effective_to.is_(None)) | (DocumentVersion.effective_to > as_of),
        )
    )


def search_passages(
    session: Session,
    *,
    organization_id: uuid.UUID,
    query_embedding: list[float],
    as_of: date,
    candidates: int,
    top_k: int,
    token_budget: int,
    embedding_model: str | None = None,
) -> list[Passage]:
    """Exact nearest-neighbour search over the filtered set.

    ``Chunk.embedding.cosine_distance(query_embedding)`` with no vector index: correct
    by construction at this corpus size, so a retrieval miss is an embedding or
    chunking problem and nothing else (ADR 0007).

    Returns at most ``top_k`` passages whose cumulative ``token_count`` fits
    ``token_budget`` — the budget is a hard stop, not a target.
    """
    if candidates < 1 or top_k < 1 or top_k > candidates or token_budget < 1:
        raise ValueError("Require positive bounds and top_k <= candidates")
    if (
        len(query_embedding) != EMBEDDING_DIM
        or not all(math.isfinite(value) for value in query_embedding)
        or not any(query_embedding)
    ):
        raise ValueError("Query must be a finite, nonzero vector of the configured dimension")

    repo = TenantScopedRepository(session, organization_id)
    versions = effective_versions_stmt(repo, as_of)
    if embedding_model is not None:
        versions = versions.where(
            DocumentVersion.embedding_model == embedding_model,
            DocumentVersion.embedding_dim == EMBEDDING_DIM,
        )
    distance = Chunk.embedding.cosine_distance(query_embedding).label("distance")
    stmt = (
        repo.query(Chunk)
        .add_columns(distance)
        .where(Chunk.document_version_id.in_(versions))
        .order_by(distance, Chunk.document_version_id, Chunk.ordinal)
        .limit(candidates)
    )
    passages: list[Passage] = []
    remaining = token_budget
    for chunk, score in session.execute(stmt):
        # Never truncate a passage while retaining its citation to the complete chunk.
        if chunk.token_count <= 0 or chunk.token_count > remaining:
            continue
        version = repo.get_or_404(DocumentVersion, chunk.document_version_id)
        document = repo.get_or_404(Document, version.document_id)
        passages.append(
            Passage(
                citation_id=citation_for(version.id, chunk.ordinal),
                heading_path=chunk.heading_path,
                text=chunk.text,
                distance=float(score),
                token_count=chunk.token_count,
                document_title=document.title,
                effective_from=version.effective_from,
            )
        )
        remaining -= chunk.token_count
        if len(passages) == top_k:
            break
    return passages


def citation_for(document_version_id: uuid.UUID, ordinal: int) -> str:
    return format_ref(EvidenceKind.policy, document_version_id, ordinal)
