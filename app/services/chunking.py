"""Structure-aware, token-bounded chunking.

Why not fixed-size character windows: they cut sentences in half and ignore the
embedding model's real limit (tokens, not characters). Here we:

  1. Split text on structure first — paragraphs, then sentences — so chunk
     boundaries fall at natural breaks.
  2. Greedily pack those units up to a token budget (`chunk_tokens`), measured with
     the *embedding model's own tokenizer* so chunks never overflow its context.
  3. Carry a token overlap between consecutive chunks so an instruction split across
     a boundary still appears whole in one chunk.

Every chunk keeps its source `page` and a document-wide `ordinal`, which later
power citations (R9) and stable ordering. The tokenizer is loaded lazily/cached so
importing this module doesn't require transformers until chunking runs.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

from app.config import settings

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
# Split after sentence-ending punctuation followed by whitespace.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class TextChunk:
    text: str
    page: int | None
    ordinal: int
    token_count: int


@lru_cache(maxsize=1)
def _tokenizer():
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(settings.embedding_model)


def _count_tokens(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False))


def _split_units(text: str) -> list[str]:
    """Break text into the smallest natural units (sentences within paragraphs)."""
    units: list[str] = []
    for para in _PARAGRAPH_SPLIT.split(text):
        para = para.strip()
        if not para:
            continue
        for sent in _SENTENCE_SPLIT.split(para):
            sent = sent.strip()
            if sent:
                units.append(sent)
    return units


def _enforce_token_limit(units: list[str], max_tokens: int) -> list[tuple[str, int]]:
    """Attach token counts; hard-split any single unit longer than max_tokens."""
    tok = _tokenizer()
    sized: list[tuple[str, int]] = []
    for unit in units:
        ids = tok.encode(unit, add_special_tokens=False)
        if len(ids) <= max_tokens:
            sized.append((unit, len(ids)))
        else:
            # A monster "sentence" (e.g. a table dumped as text): split on tokens.
            for i in range(0, len(ids), max_tokens):
                piece_ids = ids[i : i + max_tokens]
                sized.append((tok.decode(piece_ids), len(piece_ids)))
    return sized


def _overlap_tail(
    units: list[tuple[str, int]], overlap_tokens: int
) -> list[tuple[str, int]]:
    """Trailing whole units of the just-emitted chunk that fit in `overlap_tokens`.

    Carries only units that fit within the overlap budget (never forces one in), so
    the carried overlap can't push the next chunk over `chunk_tokens`. Returns []
    when overlap is disabled or the last unit alone exceeds the budget — overlap is
    best-effort. Progress is guaranteed by the caller's `has_new` flag, not by this.
    """
    if overlap_tokens <= 0:
        return []
    tail: list[tuple[str, int]] = []
    total = 0
    for unit in reversed(units):
        if total + unit[1] > overlap_tokens:
            break
        tail.insert(0, unit)
        total += unit[1]
    return tail


def chunk_page(
    text: str,
    page: int | None,
    start_ordinal: int,
    chunk_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[TextChunk]:
    """Chunk one page's text. Ordinals continue from `start_ordinal`."""
    # `is None` (not `or`): an explicit 0 overlap must not fall back to the default.
    chunk_tokens = settings.chunk_tokens if chunk_tokens is None else chunk_tokens
    overlap_tokens = (
        settings.chunk_overlap_tokens if overlap_tokens is None else overlap_tokens
    )

    sized = _enforce_token_limit(_split_units(text), chunk_tokens)
    if not sized:
        return []

    chunks: list[TextChunk] = []
    ordinal = start_ordinal
    current: list[tuple[str, int]] = []
    current_tokens = 0
    has_new = False  # guards against emitting overlap-only chunks / infinite loops

    def emit() -> None:
        nonlocal ordinal
        chunks.append(
            TextChunk(
                text=" ".join(u[0] for u in current),
                page=page,
                ordinal=ordinal,
                token_count=current_tokens,
            )
        )
        ordinal += 1

    i = 0
    while i < len(sized):
        unit, n = sized[i]
        if current and has_new and current_tokens + n > chunk_tokens:
            emit()
            current = _overlap_tail(current, overlap_tokens)
            current_tokens = sum(u[1] for u in current)
            has_new = False
        else:
            current.append((unit, n))
            current_tokens += n
            has_new = True
            i += 1

    if current and has_new:
        emit()

    return chunks
