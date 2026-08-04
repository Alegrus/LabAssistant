# RAG Build-and-Learn Plan (retrieval-first, no LLM)

Status: Draft v1 · Last updated 2026-06-21

Goal: build the **retrieval half of RAG** from primitives and *understand every
piece*, before any LLM is involved. By the end you'll type a question and get back
the exact manual chunks an LLM *would* be handed — so you can judge RAG quality on
its own. Adding the LLM afterwards is a small final step.

---

## Mental model: what RAG actually is

RAG = **Retrieval-Augmented Generation**. Two halves:

```
                 ┌─────────────── INDEXING (offline, once per doc) ───────────────┐
   manual.pdf →  load → split into chunks → embed each chunk → store vectors
                 └─────────────────────────────────────────────────────────────────┘

                 ┌─────────────── RETRIEVAL (per question) ───────────────┐
   question  →   embed question → find nearest chunks → return top-k       │  ← YOU BUILD THIS
                 └─────────────────────────────────────────────────────────┘
                                                                  │
                 ┌─────────────── GENERATION (per question) ─────┴────────┐
                 stuff chunks into a prompt → LLM writes a grounded answer  │  ← DEFERRED
                 └─────────────────────────────────────────────────────────┘
```

**The whole "no answers from weights/internet" guarantee lives in retrieval.** The
LLM only ever sees the chunks you give it. If retrieval surfaces the right page, the
answer is grounded; if it surfaces junk, no LLM can fix it. That's *why* building
retrieval first is the right call — it's the part that determines quality, and the
part you can evaluate with zero LLM cost.

**Two ideas to internalize:**
1. **Chunking** — documents are split into small passages because you retrieve and
   rank *passages*, not whole PDFs. Chunk size is a real tuning knob.
2. **Embeddings** — a model maps text → a vector (a list of ~384–1024 numbers) such
   that *similar meaning → nearby vectors*. "Reset the centrifuge" and "how do I
   restart the spinner" land close together even with no shared words. Retrieval =
   find the chunk vectors closest to the question vector (cosine similarity).

---

## Guiding principle: primitives before frameworks

You *could* do all of this in ~15 lines of LangChain or LlamaIndex. **Don't — not
yet.** Those frameworks hide exactly the mechanics you're trying to learn (chunking,
embedding, similarity, top-k). Build it once with `sentence-transformers` + `numpy`
so you can see the vectors and the scores. Once you understand it, a framework
becomes a convenience instead of a black box. We graduate to pgvector only when you
need persistence.

**Tooling for the spike:** `sentence-transformers` (embeddings), `pymupdf` (PDF
parse with page numbers), `numpy` (similarity math). That's it.

A throwaway sandbox folder, **`rag_lab/`**, is scaffolded for you (separate from the
real app). You'll write code there, learn, then graduate the working pieces into
`app/services/`. See `rag_lab/README.md`.

---

## The stages

Each stage: **Build** (what to write) · **Learn** (the concept) · **Checkpoint** (how
you know it works / an experiment to run).

### R0 — Concept warm-up (no code, ~1 hr)
- **Learn:** read the mental model above. Be able to answer, out loud:
  - Why do we chunk instead of embedding the whole PDF?
  - What does an embedding represent, and what is cosine similarity?
  - Why does retrieval quality cap the whole system's quality?
- **Checkpoint:** you can explain RAG to someone in 3 sentences without saying "AI".

### R1 — Load one document (½ day)
- **Build:** `load_pdf(path) -> list[Page]` where `Page = (page_number, text)`.
  Use `pymupdf` (`fitz`). Print the text of a few pages.
- **Learn:** parsing is lossy. You'll see headers/footers, broken tables, hyphenated
  line breaks, columns out of order. *Garbage in → garbage retrieval.*
- **Checkpoint:** open the PDF next to your output. Can you find where the text got
  mangled? Note the worst offenders — they'll hurt retrieval later.

### R2 — Chunking (½–1 day)
- **Build:** `chunk(pages, size, overlap) -> list[Chunk]` where each `Chunk` keeps
  its `text`, source `page`, and `document_id`. Start with fixed-size character (or
  token) windows with overlap (e.g. 500 / 80).
- **Learn:** the size/overlap trade-off — too big = the right sentence gets diluted
  by irrelevant text (poor precision); too small = an instruction gets cut in half
  and loses context (poor recall). Overlap reduces boundary cuts. (Stretch concept:
  *recursive* splitting on paragraph/sentence boundaries, and later *semantic*
  chunking.)
- **Checkpoint:** chunk the same doc at 200, 500, 1000 chars. Eyeball 5 chunks of
  each. Which size keeps whole instructions intact without burying them?

### R3 — Embeddings (½ day)
- **Build:** `embed(texts) -> np.ndarray` using `sentence-transformers`
  (`BAAI/bge-small-en-v1.5`, 384-dim). Embed your chunks; print the shape.
- **Learn:** a vector *is* the meaning. Build intuition: embed three short sentences
  (two about the same task, one unrelated) and compute pairwise cosine similarity by
  hand with numpy. Watch the two related ones score higher.
- **Checkpoint:** `cos(reset_phrasing_A, reset_phrasing_B) > cos(reset, unrelated)`.
  If that inequality holds, you *get* embeddings.

### R4 — Vector search, from scratch (½–1 day)
- **Build:** keep chunk vectors in a numpy matrix. `search(query, top_k)`:
  embed the query → cosine-similarity against all chunk vectors → return the top-k
  chunks with their scores, page, and document.
- **Learn:** this brute-force loop *is* what a vector database does — just without
  the indexing tricks (ANN) that make it fast at millions of vectors. You now know
  what pgvector/Qdrant are doing under the hood and *why* ANN exists.
- **Checkpoint:** ask a question you know the manual answers. Is the correct chunk in
  the top-k? Where does it rank (#1? #5?)? Rank position is your raw quality signal.

### R5 — The "answer" with no LLM (½ day)
- **Build:** a CLI: `python -m rag_lab.retrieve "how do I reset machine X?"` that
  prints the top-k chunks with `document · page · score · text`.
- **Learn:** *this output is literally the context the LLM would receive.* If a human
  reading these chunks could answer the question, the LLM will too. You are now
  evaluating RAG with no model, no cost, no latency.
- **Checkpoint:** run 5 real questions. For each, could *you* answer correctly from
  only the printed chunks? Note the failures — they drive R6.

### R6 — Evaluate & tune (1–2 days, the most important stage)
- **Build:** a tiny eval set — a JSON/CSV of `{question, expected_doc, expected_page}`
  (10–20 rows). Write `evaluate()` that runs each question and computes:
  - **hit@k** — fraction where the right chunk is anywhere in the top-k.
  - **MRR** (mean reciprocal rank) — rewards ranking the right chunk higher.
- **Learn:** RAG is an *information-retrieval* problem and you must measure it, not
  vibe it. Now tune with numbers, changing one variable at a time:
  - chunk size / overlap (R2), `top_k`, embedding model.
  - **Stretch:** a cross-encoder **re-ranker** (retrieve 30 → re-rank → keep 5);
    **metadata filtering** (restrict to one machine's manuals — this is your future
    fix for the single-QR disambiguation problem); query rewriting.
- **Checkpoint:** you can state "hit@5 went from 0.6 → 0.85 when I dropped chunk size
  to 400 and added a re-ranker." That sentence means you understand RAG.

### R7 — Graduate into the app (½–1 day)
- **Build:** move the proven pieces into the real interfaces (already stubbed):
  - `app/services/embeddings.py` ← your `embed()`
  - `app/services/vectorstore.py` ← swap the numpy matrix for **pgvector** (now you
    want persistence; the interface is identical, so retrieval code doesn't change)
  - `app/services/ingestion.py` ← `load_pdf` + `chunk` + embed + upsert
  - `app/services/rag.py::retrieve()` ← your `search()`
- **Learn:** why the numpy→pgvector swap is painless — because retrieval was always
  behind a `search()` interface (NFR4, portability, in action).
- **Checkpoint:** `rag.retrieve("...")` returns the same top chunks as your sandbox,
  but now from Postgres and persisted across restarts.

### → Later: add the LLM (the small final step)
Only now wire `rag.answer()`: take the retrieved chunks, drop them into the
strict-grounding prompt (already written in `app/services/rag.py`), and call
`app/services/llm.py`. The hard, quality-defining work is already done.

---

## Suggested pace
About **1–1.5 weeks** unhurried, treating R6 as the bulk of the value. If you only
have a few days, do R1→R5 to get a working retriever, then timebox R6.

## A few rules to keep it honest
- **Change one variable at a time** in R6, or you won't know what helped.
- **Keep the eval set fixed** while tuning; expand it only between tuning rounds.
- **Always carry `document_id` + `page` on every chunk** from R2 onward — that
  metadata is what later powers clickable citations (R9) and machine filtering.
- **Don't reach for a framework** until you've felt the primitives.

## Resources (optional)
- sentence-transformers docs — "Semantic Search" and "Cross-Encoders" pages.
- pgvector README — index types (IVFFlat/HNSW) and distance operators.
- Search terms once you've built it: "RAG chunking strategies", "reranking RAG",
  "hit@k / MRR retrieval evaluation".
