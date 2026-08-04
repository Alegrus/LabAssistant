"""Local embedding model (sentence-transformers).

Behind a tiny interface (NFR4) so the model/provider is swappable. The model is
loaded lazily and cached, so importing this module is cheap and doesn't require
torch to be installed until embeddings are actually requested.

Asymmetry matters for retrieval: bge-*-en-v1.5 wants a short instruction prepended
to QUERIES only, never to passages. We L2-normalize all vectors so cosine distance
in pgvector behaves like a dot product.
"""
from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model)


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embed document chunks for storage. Returns one vector per input."""
    if not texts:
        return []
    vecs = _model().encode(
        texts,
        batch_size=settings.embedding_batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vecs.tolist()


def embed_query(query: str) -> list[float]:
    """Embed a search query (with the retrieval instruction, if configured)."""
    text = settings.embedding_query_instruction + query
    vec = _model().encode(
        [text], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
    )[0]
    return vec.tolist()


def warmup() -> None:
    """Load the model and run one tiny inference so the first real query is fast."""
    embed_query("warmup")
