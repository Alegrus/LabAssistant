"""Hybrid retrieval: dense (vector) + sparse (full-text), fused with RRF.

Dense search finds semantic matches ("reset the spinner" ~ "restart centrifuge")
but can miss exact terms like part numbers or error codes. Sparse full-text search
nails those exact tokens but misses paraphrases. Running both and fusing their
rankings with **Reciprocal Rank Fusion** gets the best of each without having to
tune a weight between two incomparable score scales — RRF only uses *rank position*.

Pipeline:  dense top-N  ┐
                         ├─ RRF fuse ─ candidate pool ─ cross-encoder rerank ─ top-k
           sparse top-N ┘
Only chunks of `ready` documents are eligible.
"""
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.services import embeddings, reranker


@dataclass
class Candidate:
    chunk_id: int
    document_id: int
    page: int | None
    text: str
    ordinal: int | None = None  # position within the document (for neighbor stitching)
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float = 0.0  # set by the reranker; final ordering signal


def _dense_search(db: Session, query_vec: list[float], limit: int):
    distance = Chunk.embedding.cosine_distance(query_vec)
    stmt = (
        select(Chunk.id, Chunk.document_id, Chunk.page, Chunk.text, Chunk.ordinal)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.status == DocumentStatus.ready)
        .order_by(distance)  # ascending distance = most similar first
        .limit(limit)
    )
    return db.execute(stmt).all()


def _sparse_search(db: Session, query: str, limit: int):
    # websearch_to_tsquery accepts free user text safely (quotes, OR, -negation).
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(Chunk.tsv, tsquery)
    stmt = (
        select(Chunk.id, Chunk.document_id, Chunk.page, Chunk.text, Chunk.ordinal)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.status == DocumentStatus.ready)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(limit)
    )
    return db.execute(stmt).all()


def _reciprocal_rank_fusion(dense_rows, sparse_rows, k: int) -> list[Candidate]:
    """Fuse two ranked lists. Each list contributes 1/(k + rank) per item."""
    candidates: dict[int, Candidate] = {}
    scores: dict[int, float] = defaultdict(float)

    def _get(row) -> Candidate:
        return candidates.setdefault(
            row.id,
            Candidate(row.id, row.document_id, row.page, row.text, ordinal=row.ordinal),
        )

    for rank, row in enumerate(dense_rows):
        scores[row.id] += 1.0 / (k + rank + 1)
        _get(row).dense_rank = rank + 1
    for rank, row in enumerate(sparse_rows):
        scores[row.id] += 1.0 / (k + rank + 1)
        _get(row).sparse_rank = rank + 1

    for cid, cand in candidates.items():
        cand.rrf_score = scores[cid]

    return sorted(candidates.values(), key=lambda c: c.rrf_score, reverse=True)


def hybrid_search(db: Session, query: str, top_k: int | None = None) -> list[Candidate]:
    """Retrieve the most relevant chunks for `query`, best first."""
    top_k = top_k or settings.retrieval_top_k

    query_vec = embeddings.embed_query(query)
    dense_rows = _dense_search(db, query_vec, settings.retrieval_dense_k)
    sparse_rows = _sparse_search(db, query, settings.retrieval_sparse_k)

    fused = _reciprocal_rank_fusion(dense_rows, sparse_rows, settings.rrf_k)
    pool = fused[: settings.rerank_candidates]
    if not pool:
        return []

    reranked = reranker.rerank(query, pool, top_k)
    return [c for c in reranked if c.rerank_score >= settings.rerank_score_threshold]
