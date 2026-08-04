"""R3 — Turn text into vectors with a local embedding model.

Learn: a vector *is* the meaning; similar meaning -> nearby vectors. Build
intuition by embedding two related sentences + one unrelated, then comparing
cosine similarity (see the __main__ demo).

Hint: `from sentence_transformers import SentenceTransformer`;
`SentenceTransformer("MongoDB/mdbr-leaf-mt").encode(texts)` -> (n, 1024) array.
Load the model once (module-level), not per call.

This model is ASYMMETRIC: it carries a 'query' prompt that must be prepended to
queries only (not documents). We therefore split embedding into query vs passage,
exactly like the production pipeline (embed_query / embed_passages).
"""
import numpy as np
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('MongoDB/mdbr-leaf-mt')


def embed(texts: list[str], is_query: bool = False) -> np.ndarray:
    """Return an (len(texts), dim) float array.

    is_query=True prepends the model's query instruction; passages get none.
    normalize_embeddings=True makes every vector unit-length so a downstream
    index can use a plain dot product as cosine similarity (R4).
    """
    return model.encode(
        texts,
        prompt_name="query" if is_query else None,
        normalize_embeddings=True,
    )


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors."""
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


if __name__ == "__main__":
    # Intuition check: the two related phrasings should score higher than the
    # unrelated pair. If this inequality holds, you understand embeddings.
    v = embed([
        "How do I reset the centrifuge?",
        "What are the steps to restart the spinner?",
        "Where is the cafeteria?",
    ])
    print("related:  ", round(cosine(v[0], v[1]), 3))
    print("unrelated:", round(cosine(v[0], v[2]), 3))
