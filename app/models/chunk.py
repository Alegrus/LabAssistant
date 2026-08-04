"""Chunk model — a retrievable passage of a document.

Carries BOTH retrieval signals:
  * `embedding` — dense vector (pgvector) for semantic search, HNSW + cosine.
  * `tsv`       — a Postgres generated tsvector for sparse/keyword (full-text)
                  search, GIN-indexed. Maintained by the DB from `text`.
Plus `page`/`ordinal` metadata that powers clickable citations (R9) and ordering.
"""
from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.database import Base


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)  # order within doc
    page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)

    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))

    # Generated, persisted tsvector so sparse search has nothing to maintain in app
    # code. Postgres recomputes it whenever `text` changes.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    document = relationship("Document", back_populates="chunks")


# Dense index: HNSW with cosine ops (vectors are L2-normalized, so cosine ≈ dot).
Index(
    "ix_chunks_embedding_hnsw",
    Chunk.embedding,
    postgresql_using="hnsw",
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_ops={"embedding": "vector_cosine_ops"},
)

# Sparse index: GIN over the generated tsvector.
Index("ix_chunks_tsv_gin", Chunk.tsv, postgresql_using="gin")
