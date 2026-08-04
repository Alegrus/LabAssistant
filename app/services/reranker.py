"""Cross-encoder reranker.

A bi-encoder (the embedding model) scores query and passage independently, which is
fast but approximate. A cross-encoder reads the (query, passage) PAIR together and
scores true relevance — far more accurate, but too slow to run over the whole
corpus. So we use it as a second stage: retrieve a broad candidate pool cheaply,
then rerank just those candidates here. This is the single biggest quality lever in
the pipeline.
"""
from dataclasses import dataclass
from functools import lru_cache

from app.config import settings


@dataclass
class Rerankable:
    """Anything with text we can rerank; the score is written back onto it."""

    text: str
    rerank_score: float = 0.0


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.reranker_model)


def rerank(query: str, candidates: list, top_k: int) -> list:
    """Score (query, candidate.text) pairs and return the top_k, best first.

    Mutates each candidate's `rerank_score`. Candidates only need a `.text`
    attribute and a settable `rerank_score`.
    """
    if not candidates:
        return []

    pairs = [(query, c.text) for c in candidates]
    scores = _model().predict(pairs)

    for cand, score in zip(candidates, scores):
        cand.rerank_score = float(score)

    ranked = sorted(candidates, key=lambda c: c.rerank_score, reverse=True)
    return ranked[:top_k]


def warmup() -> None:
    """Load the cross-encoder and score a realistically sized batch.

    A single short pair isn't enough: the GPU (MPS) compiles kernels per input shape, so
    warming with one tiny pair still left the first real 30-candidate batch paying ~5s of
    compilation. Score a full-size batch of passage-length text instead.
    """
    filler = "warmup passage text for kernel compilation. " * 20
    _model().predict([("warmup query", filler)] * settings.rerank_candidates)
