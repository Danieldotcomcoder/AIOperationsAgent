"""Knowledge screen: policy upload, versioning, indexing status, deletion.

Markdown and plain text only, 2 MB maximum. No PDF, no OCR, no HTML — every one of
those is a parser, and a parser on untrusted input is a much larger attack surface
than this project needs.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import BaseModel

from opspilot.auth.dependencies import CurrentPrincipal, DbSession, RequireSupervisor
from opspilot.core.clock import business_date
from opspilot.core.config import get_settings
from opspilot.core.errors import RequestInvalid, ResourceNotFound
from opspilot.db.repository import TenantScopedRepository
from opspilot.domain.enums import DocumentStatus
from opspilot.models.knowledge import Chunk, Document, DocumentVersion
from opspilot.services import documents as service

router = APIRouter()

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT_TYPES = frozenset({"text/markdown", "text/plain"})


class DocumentView(BaseModel):
    id: uuid.UUID
    title: str
    status: DocumentStatus
    active_version: int | None
    effective_from: date | None
    chunk_count: int
    updated_at: datetime
    latest_version: int | None = None
    latest_effective_from: date | None = None
    embedding_mode: str | None = None


class PolicyPassageView(BaseModel):
    """One cited passage. ``text`` is untrusted document content, rendered as text."""

    title: str
    version: int
    heading_path: str
    text: str
    effective_from: date
    effective_to: date | None


@router.get("")
def list_documents(principal: CurrentPrincipal, session: DbSession) -> list[DocumentView]:
    repo = TenantScopedRepository(session, principal.organization_id)
    rows = session.scalars(
        repo.query(Document)
        .where(Document.deleted_at.is_(None), Document.status != DocumentStatus.deleted)
        .order_by(Document.updated_at.desc())
        .limit(100)
    ).all()
    as_of = business_date(get_settings())
    return [DocumentView(**service.document_view(session, repo, row, as_of=as_of)) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_document(
    principal: RequireSupervisor,
    session: DbSession,
    file: UploadFile = File(...),
    title: str = Form(...),
    effective_from: date = Form(...),
    document_id: uuid.UUID | None = Form(None),
) -> DocumentView:
    """Validate multipart metadata, then read at most the limit plus one byte.

    Hash the normalized content, create or reuse a version, then enqueue indexing.

    An identical ``content_hash`` is a no-op that returns the existing version — that
    is what makes a retried upload safe.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES or (
        file.size is not None and file.size > MAX_UPLOAD_BYTES
    ):
        raise RequestInvalid("Upload a Markdown or plain text file up to 2 MB")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    document = service.upload(
        session,
        principal,
        get_settings(),
        title=title,
        filename=file.filename or "",
        content_type=file.content_type or "",
        content=content,
        effective_from=effective_from,
        document_id=document_id,
    )
    return DocumentView(
        **service.document_view(
            session,
            TenantScopedRepository(session, principal.organization_id),
            document,
            as_of=business_date(get_settings()),
        )
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID, principal: RequireSupervisor, session: DbSession
) -> None:
    """Removes the document's chunks in the same transaction that marks it deleted.

    A deleted policy must be invisible to the very next retrieval. Tenant- and
    deletion-visibility tests are a Week 3 gate.
    """
    service.delete_document(session, principal, document_id)


@router.post("/{document_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_index(
    document_id: uuid.UUID, principal: RequireSupervisor, session: DbSession
) -> dict[str, str]:
    repo = TenantScopedRepository(session, principal.organization_id)
    document = service.visible_document(repo, document_id, lock=True)
    version = session.scalar(
        repo.query(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.indexed_at.is_(None),
        )
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    if version:
        document.status = DocumentStatus.pending
        version.index_requested_by = principal.membership_id
        service.enqueue_after_commit(session, version.id)
    return {"status": document.status}


@router.get("/versions/{version_id}/chunks/{ordinal}")
def read_citation(
    version_id: uuid.UUID, ordinal: int, principal: CurrentPrincipal, session: DbSession
) -> PolicyPassageView:
    repo = TenantScopedRepository(session, principal.organization_id)
    version = repo.get_or_404(DocumentVersion, version_id)
    document = service.visible_document(repo, version.document_id)
    chunk = repo.find_by(Chunk, document_version_id=version.id, ordinal=ordinal)
    if chunk is None:
        raise ResourceNotFound("Passage not found")
    return PolicyPassageView(
        title=document.title,
        version=version.version_number,
        heading_path=chunk.heading_path,
        text=chunk.text,
        effective_from=version.effective_from,
        effective_to=version.effective_to,
    )
