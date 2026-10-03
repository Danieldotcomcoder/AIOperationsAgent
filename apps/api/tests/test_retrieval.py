import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from opspilot.domain.enums import DocumentStatus
from opspilot.models import Organization
from opspilot.models.knowledge import EMBEDDING_DIM, Chunk, Document, DocumentVersion
from opspilot.retrieval.search import citation_for, search_passages

TODAY = date(2026, 9, 23)
QUERY = [1.0] + [0.0] * (EMBEDDING_DIM - 1)


def organization(db: Session) -> uuid.UUID:
    row = Organization(slug=f"retrieval-test-{uuid.uuid4()}", name="Retrieval test")
    db.add(row)
    db.flush()
    return row.id


def policy(
    db: Session,
    org: uuid.UUID,
    *,
    active: bool = True,
    start: date = TODAY,
    end: date | None = None,
    deleted: bool = False,
    tokens: int = 10,
    similarity: float = 1.0,
) -> tuple[Document, DocumentVersion]:
    doc = Document(
        organization_id=org,
        title="Carrier escalation",
        source_filename="policy.md",
        content_type="text/markdown",
        status=DocumentStatus.active,
        uploaded_by=uuid.uuid4(),
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(doc)
    db.flush()
    version = DocumentVersion(
        organization_id=org,
        document_id=doc.id,
        version_number=1,
        content_hash=uuid.uuid4().hex,
        raw_text="Escalate after two days.",
        effective_from=start,
        effective_to=end,
        is_active=active,
    )
    db.add(version)
    db.flush()
    db.add(
        Chunk(
            organization_id=org,
            document_version_id=version.id,
            ordinal=0,
            heading_path="Carrier > Delay",
            text=version.raw_text,
            token_count=tokens,
            embedding=[similarity, 1.0 - similarity] + [0.0] * (EMBEDDING_DIM - 2),
        )
    )
    db.flush()
    return doc, version


def search(db: Session, org: uuid.UUID, **bounds: int):
    return search_passages(
        db,
        organization_id=org,
        query_embedding=QUERY,
        as_of=TODAY,
        **({"candidates": 8, "top_k": 5, "token_budget": 100} | bounds),
    )


def test_filters_before_candidate_limit(db: Session) -> None:
    org, other = organization(db), organization(db)
    policy(db, other)
    policy(db, org, active=False)
    policy(db, org, start=TODAY + timedelta(days=1))
    policy(db, org, end=TODAY)
    policy(db, org, deleted=True)
    deleted_doc, _ = policy(db, org)
    deleted_doc.status = DocumentStatus.deleted
    _, expected = policy(db, org, similarity=0.5)
    result = search(db, org, candidates=1, top_k=1)
    assert [p.citation_id for p in result] == [citation_for(expected.id, 0)]
    assert result[0].heading_path == "Carrier > Delay"
    assert result[0].document_title == "Carrier escalation"
    assert result[0].effective_from == TODAY
    assert result[0].distance > 0


def test_deletion_visible_to_next_search(db: Session) -> None:
    org = organization(db)
    doc, _ = policy(db, org)
    assert len(search(db, org)) == 1
    doc.deleted_at = datetime.now(UTC)
    db.flush()
    assert search(db, org) == []


def test_budget_skips_oversized_chunks_and_caps_results(db: Session) -> None:
    org = organization(db)
    policy(db, org, tokens=101)
    _, expected = policy(db, org, tokens=10, similarity=0.9)
    policy(db, org, tokens=11, similarity=0.8)
    policy(db, org, tokens=10, similarity=0.7)
    result = search(db, org, token_budget=20)
    assert len(result) == 2
    assert sum(p.token_count for p in result) == 20
    assert result[0].citation_id == citation_for(expected.id, 0)
    assert len(search(db, org, top_k=1)) == 1
    assert search(db, org, token_budget=9) == []


def test_empty_corpus(db: Session) -> None:
    assert search(db, organization(db)) == []


@pytest.mark.parametrize(
    "vector",
    [[], [0.0] * EMBEDDING_DIM, [float("nan")] * EMBEDDING_DIM, [float("inf")] * EMBEDDING_DIM],
)
def test_invalid_vectors_fail_before_database_access(vector: list[float]) -> None:
    with Session() as session, pytest.raises(ValueError, match="vector"):
        search_passages(
            session,
            organization_id=uuid.uuid4(),
            query_embedding=vector,
            as_of=TODAY,
            candidates=8,
            top_k=5,
            token_budget=100,
        )


@pytest.mark.parametrize(
    "bounds", [{"candidates": 0}, {"top_k": 0}, {"top_k": 9}, {"token_budget": 0}]
)
def test_invalid_bounds_fail_before_database_access(bounds: dict[str, int]) -> None:
    with Session() as session, pytest.raises(ValueError, match="bounds"):
        search(session, uuid.uuid4(), **bounds)
