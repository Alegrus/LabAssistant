"""RAG orchestration: retrieve -> assemble grounded prompt -> generate -> cite.

The grounding guarantee (REQUIREMENTS R2) lives here: the model is given ONLY the
retrieved chunks and is instructed to answer from them alone, citing each source by
number. If retrieval returns nothing, we short-circuit to the "not found" message
and never call the LLM — so an empty knowledge base can't be answered from weights.

Citation integrity (N1): sources are numbered from the retrieved set, and only the
[n] markers the model actually used are resolved to clickable documents. The model
cannot invent a citation to a document that wasn't retrieved.
"""
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.chunk import Chunk
from app.models.document import Document
from app.services import llm, vectorstore
from app.services.vectorstore import Candidate

CONDENSE_SYSTEM_PROMPT = (
    "You rewrite a follow-up question into a standalone search query. Given the "
    "conversation so far and the user's latest message, produce a single self-contained "
    "query that carries the needed context (e.g. the machine or procedure being "
    "discussed), suitable for searching a manual index. Output ONLY the rewritten "
    "query, no preamble. If the latest message is already self-contained, return it "
    "unchanged."
)

GROUNDING_SYSTEM_PROMPT = (
    "You are a lab equipment assistant. Answer the user's question using ONLY the "
    "numbered sources below. Do not use any outside or prior knowledge. If the "
    "sources do not contain the answer, reply with exactly: \"{not_found}\". "
    "Give clear, step-by-step instructions when relevant. Cite the source for every "
    "claim using its bracketed number, e.g. [1] or [2][3]. Never cite a number that "
    "is not in the list."
).format(not_found=settings.rag_not_found_message)


@dataclass
class Citation:
    markers: list[int]    # the [n] markers the model used for THIS document
    document_id: int
    filename: str
    pages: list[int]      # pages of the cited chunk(s) for deep-linking (R9)


@dataclass
class Answer:
    content: str
    citations: list[Citation]
    not_found: bool
    used_chunk_ids: list[int]  # auditability / telemetry (N5)


def retrieve(db: Session, query: str, top_k: int | None = None) -> list[Candidate]:
    """Thin pass-through to hybrid retrieval; the LLM is not involved here."""
    return vectorstore.hybrid_search(db, query, top_k=top_k)


def _build_source_block(candidates: list[Candidate]) -> str:
    lines = []
    for n, c in enumerate(candidates, start=1):
        loc = f", page {c.page}" if c.page is not None else ""
        lines.append(f"[{n}] (document #{c.document_id}{loc})\n{c.text}")
    return "\n\n".join(lines)


def _resolve_citations(
    db: Session, content: str, candidates: list[Candidate]
) -> list[Citation]:
    """Map the [n] markers the model used back to real documents (N1)."""
    import re

    used = {int(m) for m in re.findall(r"\[(\d+)\]", content)}
    used &= set(range(1, len(candidates) + 1))  # ignore out-of-range markers
    if not used:
        return []

    # Group cited chunks by document, collecting the markers and pages that map to
    # each one. Several markers ([4], [6]) can point at chunks from the SAME manual;
    # they must collapse into a single citation/link, not one row per marker.
    markers_by_doc: dict[int, set[int]] = {}
    pages_by_doc: dict[int, set[int]] = {}
    for n in used:
        c = candidates[n - 1]
        markers_by_doc.setdefault(c.document_id, set()).add(n)
        pages_by_doc.setdefault(c.document_id, set())
        if c.page is not None:
            pages_by_doc[c.document_id].add(c.page)

    filenames = dict(
        db.query(Document.id, Document.filename)
        .filter(Document.id.in_(markers_by_doc.keys()))
        .all()
    )

    # One citation per document, ordered by the first marker that referenced it.
    citations = []
    for doc_id in sorted(markers_by_doc, key=lambda d: min(markers_by_doc[d])):
        citations.append(
            Citation(
                markers=sorted(markers_by_doc[doc_id]),
                document_id=doc_id,
                filename=filenames.get(doc_id, ""),
                pages=sorted(pages_by_doc.get(doc_id, set())),
            )
        )
    return citations


def _is_not_found(content: str) -> bool:
    """Robustly detect the not-found sentinel.

    The model is told to reply with the exact sentinel, but small drift (case,
    wrapping quotes, a trailing period, stray whitespace) shouldn't defeat the N5
    telemetry. Normalize both sides before comparing; stays strict enough to avoid
    false positives on real answers.
    """
    def _norm(s: str) -> str:
        return s.strip().strip("\"'").rstrip(".!").strip().casefold()

    return _norm(content) == _norm(settings.rag_not_found_message)


def _expand_context(db: Session, retrieved: list[Candidate]) -> list[Candidate]:
    """Stitch each retrieved chunk together with its neighbors (same document, adjacent
    ordinals) so a multi-step procedure isn't cut off at the chunk boundary. Windows
    that overlap within a document are merged into one contiguous passage; a gap in
    ordinals starts a new passage. Result is what the LLM sees and cites, ordered by
    the relevance of the chunk that seeded each passage.
    """
    before, after = settings.context_expand_before, settings.context_expand_after
    if not retrieved or (before <= 0 and after <= 0):
        return retrieved

    # Per document: every ordinal we want, and the retrieved "anchors" (rank + page).
    want: dict[int, set[int]] = {}
    anchors: dict[int, list[tuple[int, int, int | None]]] = defaultdict(list)
    for rank, c in enumerate(retrieved):
        want.setdefault(c.document_id, set()).update(
            range(c.ordinal - before, c.ordinal + after + 1)
        )
        anchors[c.document_id].append((rank, c.ordinal, c.page))

    rows = (
        db.query(Chunk)
        .filter(Chunk.document_id.in_(want.keys()))
        .order_by(Chunk.document_id, Chunk.ordinal)
        .all()
    )
    by_doc: dict[int, list[Chunk]] = defaultdict(list)
    for ch in rows:
        if ch.ordinal in want[ch.document_id]:
            by_doc[ch.document_id].append(ch)

    scored: list[tuple[int, Candidate]] = []
    for doc_id, chunks in by_doc.items():
        # Split the wanted chunks into contiguous runs (chunks already ordinal-sorted).
        runs: list[list[Chunk]] = []
        for ch in chunks:
            if runs and ch.ordinal == runs[-1][-1].ordinal + 1:
                runs[-1].append(ch)
            else:
                runs.append([ch])
        for run in runs:
            run_ordinals = {ch.ordinal for ch in run}
            hits = [a for a in anchors[doc_id] if a[1] in run_ordinals]
            best_rank = min((a[0] for a in hits), default=10**9)
            # Deep-link to where the relevant content starts, not the padded front.
            anchor_page = min(
                (a[2] for a in hits if a[2] is not None), default=run[0].page
            )
            scored.append(
                (
                    best_rank,
                    Candidate(
                        chunk_id=run[0].id,
                        document_id=doc_id,
                        page=anchor_page,
                        text="\n".join(ch.text for ch in run),
                    ),
                )
            )

    scored.sort(key=lambda t: t[0])  # most-relevant passage first

    # Keep within a rough context budget (est. ~4 chars/token) and the passage cap.
    passages: list[Candidate] = []
    used_tokens = 0
    for _, cand in scored[: settings.context_max_passages]:
        est = max(1, len(cand.text) // 4)
        if passages and used_tokens + est > settings.rag_max_context_tokens:
            break
        passages.append(cand)
        used_tokens += est
    return passages


def _condense_query(query: str, history: list[dict] | None) -> str:
    """Rewrite a context-dependent follow-up ("what next?") into a standalone search
    query using recent history, so retrieval works on turns after the first.

    Best-effort: if there's no history or the rewrite call fails, fall back to the
    raw query rather than failing the turn.
    """
    if not history:
        return query

    # Only the last few turns matter for resolving references; keep it cheap.
    recent = history[-6:]
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
    messages = [
        {"role": "system", "content": CONDENSE_SYSTEM_PROMPT},
        {"role": "user", "content": f"Conversation:\n{convo}\n\nLatest message: {query}\n\nStandalone query:"},
    ]
    try:
        rewritten = llm.chat(messages).strip()
    except httpx.HTTPError:
        return query
    # Guard against a chatty model: fall back if it returned nothing or a paragraph.
    if not rewritten or len(rewritten) > 300:
        return query
    return rewritten


def _prepare(
    db: Session, query: str, history: list[dict] | None, image_context: str | None
) -> tuple[list[Candidate], list[Candidate] | None, list[dict] | None]:
    """Shared front half of a turn: retrieve → expand → build the grounded prompt.

    Returns (candidates, passages, messages). When retrieval is empty, passages/messages
    are None and the caller must short-circuit to the not-found reply WITHOUT calling the
    LLM (R2) — an empty knowledge base must never be answered from model weights.
    """
    if image_context:
        # The observed screen state is already standalone; fold it into the query.
        search_query = f"{query}\n{image_context}"
    else:
        search_query = _condense_query(query, history)

    candidates = retrieve(db, search_query)
    if not candidates:
        return [], None, None

    passages = _expand_context(db, candidates)

    user_block = ""
    if image_context:
        user_block += f"Observed on the user's screen (from their photo):\n{image_context}\n\n"
    user_block += f"Sources:\n{_build_source_block(passages)}\n\nQuestion: {query}"

    messages = [{"role": "system", "content": GROUNDING_SYSTEM_PROMPT}]
    messages += history or []
    messages.append({"role": "user", "content": user_block})
    return candidates, passages, messages


def answer_stream(
    db: Session,
    query: str,
    history: list[dict] | None = None,
    image_context: str | None = None,
) -> Iterator[tuple[str, object]]:
    """Streaming variant of `answer()`.

    Yields ("token", text) as the model produces text, then exactly one ("done", Answer)
    carrying the assembled content and resolved citations. Citations can only be resolved
    once the full text exists (the [n] markers must be read back), which is why they
    arrive at the end rather than inline.
    """
    # Retrieval + prompt prefill take several seconds on local hardware; emit progress so
    # the user sees the turn working instead of a blank bubble.
    yield ("status", "Searching the manuals…")
    candidates, passages, messages = _prepare(db, query, history, image_context)

    if not candidates:
        yield ("token", settings.rag_not_found_message)
        yield ("done", Answer(settings.rag_not_found_message, [], True, []))
        return

    yield ("status", f"Reading {len(passages)} passage(s) from the manuals…")

    parts: list[str] = []
    for delta in llm.stream_chat(messages):
        parts.append(delta)
        yield ("token", delta)

    content = "".join(parts)
    not_found = _is_not_found(content)
    citations = [] if not_found else _resolve_citations(db, content, passages)
    yield (
        "done",
        Answer(content, citations, not_found, [c.chunk_id for c in candidates]),
    )


def answer(
    db: Session,
    query: str,
    history: list[dict] | None = None,
    image_context: str | None = None,
) -> Answer:
    """Full RAG turn. `history` is prior [{role, content}] messages for this chat.

    `image_context` is the vision model's reading of an attached photo (Phase 4). When
    present it augments retrieval and is given to the grounding model as observed state,
    so the answer addresses what's actually on the user's screen — while still coming
    only from the manuals.
    """
    candidates, passages, messages = _prepare(db, query, history, image_context)

    if not candidates:
        # No grounding -> never ask the LLM (R2). Surface for telemetry (N5).
        return Answer(settings.rag_not_found_message, [], not_found=True, used_chunk_ids=[])

    content = llm.chat(messages)
    not_found = _is_not_found(content)

    citations = [] if not_found else _resolve_citations(db, content, passages)
    return Answer(
        content=content,
        citations=citations,
        not_found=not_found,
        used_chunk_ids=[c.chunk_id for c in candidates],  # the actual retrieval hits
    )
