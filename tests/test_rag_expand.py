"""Integration tests for RAG context expansion (`rag._expand_context`).

These lock in the neighbor-stitching behavior that keeps multi-step procedures from
being cut off at a chunk boundary. Pure DB + logic: no embedding model, reranker, or
network — chunks are seeded with a dummy zero-vector because expansion looks chunks up
by (document_id, ordinal), never by vector similarity.

Require Postgres (the shared `*_test` DB from conftest); skipped if it's unreachable.
"""
import pytest

from app.config import settings
from app.services.rag import _expand_context
from app.services.vectorstore import Candidate

_DIM = settings.embedding_dim


def _postgres_available() -> bool:
    try:
        from app.database import engine
        with engine.connect():
            return True
    except Exception:
        return False


skip_no_db = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


def _seed_doc(db, ordinals_pages: list[tuple[int, int]]) -> int:
    """Create one `ready` document with chunks at the given (ordinal, page) pairs.

    Each chunk's text is 'step-{ordinal}' so assertions can check membership.
    """
    from app.models.chunk import Chunk
    from app.models.document import Document, DocumentStatus

    doc = Document(filename="m.pdf", path="/x/m.pdf", status=DocumentStatus.ready)
    db.add(doc)
    db.flush()
    for ordinal, page in ordinals_pages:
        db.add(
            Chunk(
                document_id=doc.id,
                ordinal=ordinal,
                page=page,
                text=f"step-{ordinal}",
                embedding=[0.0] * _DIM,
            )
        )
    db.commit()
    return doc.id


@pytest.fixture
def db_session():
    """Clean chunks/documents and pin the expansion knobs to deterministic values."""
    from sqlalchemy import text

    from app.database import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    db.execute(text("DELETE FROM chunks"))
    db.execute(text("DELETE FROM documents"))
    db.commit()

    saved = (
        settings.context_expand_before,
        settings.context_expand_after,
        settings.context_max_passages,
        settings.rag_max_context_tokens,
    )
    settings.context_expand_before = 1
    settings.context_expand_after = 2
    settings.context_max_passages = 10
    settings.rag_max_context_tokens = 10**9  # effectively no budget cap in these tests

    yield db

    (
        settings.context_expand_before,
        settings.context_expand_after,
        settings.context_max_passages,
        settings.rag_max_context_tokens,
    ) = saved
    db.execute(text("DELETE FROM chunks"))
    db.execute(text("DELETE FROM documents"))
    db.commit()
    db.close()


@skip_no_db
def test_stitches_forward_neighbors(db_session):
    """A single hit pulls its window (before=1, after=2) into one contiguous passage."""
    doc = _seed_doc(db_session, [(o, o + 1) for o in range(6)])  # ordinals 0..5
    passages = _expand_context(db_session, [Candidate(0, doc, 1, "step-0", ordinal=0)])

    assert len(passages) == 1
    body = passages[0].text
    assert "step-0" in body and "step-1" in body and "step-2" in body
    assert "step-3" not in body  # after=2 stops at ordinal 2


@skip_no_db
def test_merges_overlapping_windows(db_session):
    """Two nearby hits whose windows overlap collapse into ONE passage, not two."""
    doc = _seed_doc(db_session, [(o, o + 1) for o in range(8)])
    cands = [
        Candidate(0, doc, 3, "", ordinal=2),  # window 1..4
        Candidate(0, doc, 4, "", ordinal=3),  # window 2..5  -> merged run 1..5
    ]
    passages = _expand_context(db_session, cands)

    assert len(passages) == 1
    assert "step-1" in passages[0].text and "step-5" in passages[0].text


@skip_no_db
def test_splits_on_gap_and_orders_by_relevance(db_session):
    """Two far-apart hits produce two passages, ordered by the seeding hit's rank."""
    doc = _seed_doc(db_session, [(o, o + 1) for o in range(12)])
    cands = [
        Candidate(0, doc, 2, "", ordinal=1),   # rank 0 -> first passage
        Candidate(0, doc, 11, "", ordinal=10),  # rank 1 -> second passage
    ]
    passages = _expand_context(db_session, cands)

    assert len(passages) == 2
    assert "step-1" in passages[0].text
    assert "step-10" in passages[1].text


@skip_no_db
def test_page_anchor_is_the_retrieved_chunk(db_session):
    """The citation page anchors to the hit, not the padded-in front of the window."""
    # ordinal 3 lives on page 10; its window pulls in ordinal 2 (page 9) at the front.
    doc = _seed_doc(db_session, [(0, 7), (1, 8), (2, 9), (3, 10), (4, 11)])
    passages = _expand_context(db_session, [Candidate(0, doc, 10, "", ordinal=3)])

    assert len(passages) == 1
    assert passages[0].page == 10  # not 9 (the front of the stitched window)


@skip_no_db
def test_disabled_returns_hits_unchanged(db_session):
    """before=after=0 short-circuits: the original hits pass straight through."""
    settings.context_expand_before = 0
    settings.context_expand_after = 0
    doc = _seed_doc(db_session, [(0, 1), (1, 2)])
    cands = [Candidate(9, doc, 1, "orig", ordinal=0)]

    passages = _expand_context(db_session, cands)

    assert passages is cands
