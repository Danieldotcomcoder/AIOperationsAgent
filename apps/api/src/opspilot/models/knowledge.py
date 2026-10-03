"""Policy documents, versions and chunks.

Superseded versions are kept, not deleted: the evaluation set contains conflicting
historical policies, and the correct behaviour is to cite the effective one and say
where an older one differs. "Effective" is a predicate on dates, evaluated in SQL
*before* ranking, never as a post-filter over a global similarity search.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from opspilot.domain.enums import DocumentStatus
from opspilot.models.base import Base, TenantMixin, TimestampMixin, tenant_fk, tenant_unique
from opspilot.models.enum_types import DOCUMENT_STATUS

#: Storage width, matching Settings.embedding_dim. Liquid's native 1024 dimensions
#: are zero-padded to this width, preserving cosine distance. Model identities still
#: isolate embedding spaces: equal storage dimensions do not make models comparable.
EMBEDDING_DIM = 1536


class Document(Base, TenantMixin, TimestampMixin):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        DOCUMENT_STATUS, nullable=False, default=DocumentStatus.pending
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Soft delete, because an audit trail must still explain a past citation.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        tenant_unique(),
        Index("ix_documents_org_status", "organization_id", "status"),
    )


class DocumentVersion(Base, TenantMixin, TimestampMixin):
    __tablename__ = "document_versions"

    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    #: sha256 of normalized content. Makes re-upload and re-index idempotent.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_requested_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_dim: Mapped[int | None] = mapped_column()

    __table_args__ = (
        tenant_unique(),
        tenant_fk("document_id", "documents", ondelete="CASCADE"),
        tenant_fk("index_requested_by", "memberships"),
        UniqueConstraint("organization_id", "content_hash"),
        UniqueConstraint("organization_id", "document_id", "version_number"),
        Index("ix_doc_versions_effective", "organization_id", "is_active", "effective_from"),
    )


class Chunk(Base, TenantMixin, TimestampMixin):
    __tablename__ = "chunks"

    document_version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Position within the version. Half of a citation id: pol:<version_id>#<ordinal>.
    ordinal: Mapped[int] = mapped_column(nullable=False)
    #: e.g. "Delayed shipments > Carrier delay > Escalation". Shown with the citation
    #: so a reviewer can find the passage in the source document in seconds.
    heading_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(nullable=False, default=0)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    __table_args__ = (
        tenant_unique(),
        tenant_fk("document_version_id", "document_versions", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "document_version_id", "ordinal"),
        # No vector index yet — exact search is correct at this corpus size (ADR 0007).
        # When one is added it must be created CONCURRENTLY in its own migration.
        Index("ix_chunks_org_version", "organization_id", "document_version_id"),
    )
