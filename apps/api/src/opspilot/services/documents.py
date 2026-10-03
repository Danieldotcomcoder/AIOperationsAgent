"""Tenant-owned policy lifecycle. Callers own the surrounding transaction."""

import hashlib
import uuid
from datetime import date
from typing import Any

from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from opspilot.auth.principal import Principal
from opspilot.core.clock import utcnow
from opspilot.core.config import Settings
from opspilot.core.errors import ConflictError, NotAuthorized, RequestInvalid, ResourceNotFound
from opspilot.core.logging import get_logger
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import DocumentStatus, MembershipStatus, Role
from opspilot.models import Membership, Organization
from opspilot.models.knowledge import Chunk, Document, DocumentVersion
from opspilot.retrieval.chunking import chunk_text
from opspilot.retrieval.embeddings import embed_texts, embedding_identity

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_CHUNKS = 256
ALLOWED_CONTENT_TYPES = frozenset({"text/plain", "text/markdown"})
log = get_logger(__name__)


def visible_document(
    repo: TenantScopedRepository, document_id: uuid.UUID, *, lock: bool = False
) -> Document:
    stmt = repo.query(Document).where(
        Document.id == document_id,
        Document.deleted_at.is_(None),
        Document.status != DocumentStatus.deleted,
    )
    if lock:
        stmt = stmt.with_for_update()
    document = repo.session.scalar(stmt)
    if document is None:
        raise ResourceNotFound("Document not found")
    return document


def upload(
    session: Session,
    principal: Principal,
    settings: Settings,
    *,
    title: str,
    filename: str,
    content_type: str,
    content: bytes,
    effective_from: date,
    document_id: uuid.UUID | None = None,
) -> Document:
    title = title.strip()
    if content_type not in ALLOWED_CONTENT_TYPES or not filename.lower().endswith((".md", ".txt")):
        raise RequestInvalid("Use a Markdown (.md) or plain text (.txt) file")
    if not 1 <= len(content) <= MAX_UPLOAD_BYTES or not 1 <= len(title) <= 300:
        raise RequestInvalid("A title and a nonempty file up to 2 MB are required")
    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    except UnicodeDecodeError as exc:
        raise RequestInvalid("The file must be UTF-8 text") from exc
    if not text or "\x00" in text:
        raise RequestInvalid("The file must contain nonempty text without NUL characters")
    chunks = chunk_text(text, settings.chunk_target_tokens, settings.chunk_overlap_tokens)
    if len(chunks) > MAX_CHUNKS:
        raise RequestInvalid(f"Policy exceeds {MAX_CHUNKS} chunks; split it into smaller documents")
    digest = hashlib.sha256(text.encode()).hexdigest()
    # Serialize tenant uploads: the existing schema deduplicates content across an organization.
    session.scalar(
        select(Organization).where(Organization.id == principal.organization_id).with_for_update()
    )
    repo = TenantScopedRepository(session, principal.organization_id)
    document = visible_document(repo, document_id, lock=True) if document_id else None
    duplicate = repo.find_by(DocumentVersion, content_hash=digest)
    if duplicate:
        existing = visible_document(repo, duplicate.document_id)
        if document_id and duplicate.document_id != document_id:
            raise ConflictError("This content already belongs to another document")
        if duplicate.effective_from != effective_from:
            raise ConflictError("Identical content cannot silently change its effective date")
        if duplicate.indexed_at is None:
            existing.status = DocumentStatus.pending
            duplicate.index_requested_by = principal.membership_id
            enqueue_after_commit(session, duplicate.id)
        return existing
    previous = []
    if document:
        previous = list(
            session.scalars(
                repo.query(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version_number)
            ).all()
        )
        if previous and effective_from <= max(v.effective_from for v in previous):
            raise ConflictError("A revision must start after the previous version's effective date")
        if any(v.indexed_at is None for v in previous):
            raise ConflictError("Index the pending revision before uploading another")
    else:
        document = repo.add(
            Document(
                title=title,
                source_filename=filename[:300],
                content_type=content_type,
                uploaded_by=principal.membership_id,
            )
        )
        session.flush()
    document.status = DocumentStatus.pending
    version = repo.add(
        DocumentVersion(
            document_id=document.id,
            version_number=len(previous) + 1,
            content_hash=digest,
            raw_text=text,
            effective_from=effective_from,
            is_active=False,
            index_requested_by=principal.membership_id,
        )
    )
    session.flush()
    enqueue_after_commit(session, version.id)
    return document


def enqueue_after_commit(session: Session, version_id: uuid.UUID) -> None:
    @event.listens_for(session, "after_commit", once=True)
    def publish(_: Session) -> None:
        from kombu.exceptions import OperationalError

        from opspilot.workers.app import celery_app

        try:
            celery_app.send_task(
                "document.index", args=[str(version_id)], queue="investigate", retry=False
            )
        except OperationalError:
            # The explicit supervisor retry endpoint recovers an interrupted publish.
            log.warning("document.enqueue_failed", version_id=str(version_id))


def index_version(
    session: Session, organization_id: uuid.UUID, version_id: uuid.UUID, settings: Settings
) -> None:
    repo = TenantScopedRepository(session, organization_id)
    version = repo.get_or_404(DocumentVersion, version_id)
    document = visible_document(repo, version.document_id, lock=True)
    session.refresh(version)  # another worker may have finished while we waited for the lock
    if version.indexed_at is not None:
        return
    requester_id = version.index_requested_by or document.uploaded_by
    requester = session.scalar(
        repo.query(Membership)
        .where(Membership.id == requester_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if (
        requester is None
        or requester.status != MembershipStatus.active
        or requester.role != Role.supervisor
    ):
        raise NotAuthorized("Indexing requires a current supervisor membership")
    chunks = chunk_text(
        version.raw_text, settings.chunk_target_tokens, settings.chunk_overlap_tokens
    )
    if not chunks or len(chunks) > MAX_CHUNKS:
        raise RequestInvalid("Invalid policy chunk count")
    # One bounded call; all chunks and effective-date changes publish atomically.
    vectors = embed_texts([c.text for c in chunks], settings, organization_id)
    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        repo.add(
            Chunk(
                document_version_id=version.id,
                ordinal=ordinal,
                heading_path=chunk.heading_path,
                text=chunk.text,
                token_count=chunk.token_count,
                embedding=vector,
            )
        )
    version.indexed_at = utcnow()
    version.embedding_model = embedding_identity(settings)
    version.embedding_dim = settings.embedding_dim
    version.is_active = True
    versions = list(
        session.scalars(
            repo.query(DocumentVersion)
            .where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.indexed_at.is_not(None),
            )
            .order_by(DocumentVersion.effective_from)
        ).all()
    )
    # Include the new row with autoflush=False sessions.
    versions = sorted(
        {v.id: v for v in [*versions, version]}.values(), key=lambda v: v.effective_from
    )
    for position, row in enumerate(versions):
        row.effective_to = (
            versions[position + 1].effective_from if position + 1 < len(versions) else None
        )
    document.status = DocumentStatus.active
    document.updated_at = utcnow()
    session.flush()


def delete_document(session: Session, principal: Principal, document_id: uuid.UUID) -> None:
    repo = TenantScopedRepository(session, principal.organization_id)
    document = visible_document(repo, document_id, lock=True)
    versions = list(
        session.scalars(
            repo.query(DocumentVersion).where(DocumentVersion.document_id == document.id)
        ).all()
    )
    ids = [v.id for v in versions]
    session.execute(
        delete(Chunk).where(
            Chunk.organization_id == principal.organization_id, Chunk.document_version_id.in_(ids)
        )
    )
    for version in versions:
        version.is_active = False
        version.raw_text = ""  # delete document content as well as vectors
        # Release the per-organization content hash so the same text can be uploaded
        # again as a new document. The tombstone is derived from the original hash, so
        # an audit holding that hash can still match it to this version.
        version.content_hash = hashlib.sha256(
            f"deleted:{version.id}:{version.content_hash}".encode()
        ).hexdigest()
    document.status = DocumentStatus.deleted
    document.deleted_at = utcnow()


def document_view(
    session: Session, repo: TenantScopedRepository, document: Document, *, as_of: date
) -> dict[str, Any]:
    """``as_of`` is the business date, so "current" here is what retrieval would use."""
    versions = list(
        session.scalars(
            repo.query(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number.desc())
        ).all()
    )
    active = next(
        (
            v
            for v in versions
            if v.is_active
            and v.effective_from <= as_of
            and (v.effective_to is None or as_of < v.effective_to)
        ),
        None,
    )
    chunks = (
        list(
            session.scalars(
                repo.query(Chunk).where(
                    Chunk.document_version_id == active.id,
                )
            ).all()
        )
        if active
        else []
    )
    return {
        "id": document.id,
        "title": document.title,
        "status": document.status,
        "active_version": active.version_number if active else None,
        "effective_from": active.effective_from if active else None,
        "chunk_count": len(chunks),
        "updated_at": document.updated_at,
        "latest_version": versions[0].version_number if versions else None,
        "latest_effective_from": versions[0].effective_from if versions else None,
        "embedding_mode": active.embedding_model if active else None,
    }
