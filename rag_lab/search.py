"""R4 — A vector index and top-k search, from scratch.

This brute-force cosine loop IS what a vector database does — minus the ANN
indexing that makes it fast at millions of vectors. Building it by hand is how you
learn what pgvector/Qdrant do under the hood (and why ANN exists).

In R7 you swap this class for a pgvector-backed one with the SAME interface, so
retrieval code doesn't change (NFR4, portability).
"""
from dataclasses import dataclass

import numpy as np

from rag_lab.chunk import Chunk
from rag_lab.embed import embed, cosine



@dataclass
class Hit:
    chunk: Chunk
    score: float


class InMemoryIndex:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.vectors: np.ndarray | None = None  # (n, dim)

    def add(self, chunks: list[Chunk]) -> None:
        """Embed each chunk's TEXT and APPEND to the index (build_index calls this
        once per document, so it must accumulate, not overwrite)."""
        if not chunks:
            return
        new_vectors = embed([c.text for c in chunks])
        self.chunks.extend(chunks)
        self.vectors = (
            new_vectors
            if self.vectors is None
            else np.vstack([self.vectors, new_vectors])
        )

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        """Embed the query, cosine-rank all chunks, return the top_k best."""
        qvec = embed([query], is_query=True)[0]  # (dim,) — pull the single row out
        hits = [
            Hit(chunk, cosine(qvec, vec))
            for chunk, vec in zip(self.chunks, self.vectors)
        ]
        # reverse=True -> highest similarity first; honour the caller's top_k
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]
