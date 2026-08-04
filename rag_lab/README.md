# rag_lab — RAG learning sandbox

A throwaway playground for building the retrieval half of RAG **from primitives**
(`sentence-transformers` + `numpy` + `pymupdf`). Follow
[../docs/RAG_LEARNING_PLAN.md](../docs/RAG_LEARNING_PLAN.md) stage by stage.

This is **not** the production app. Once a piece works and you understand it, you
graduate it into `app/services/` (stage R7).

## Setup
```bash
pip install sentence-transformers pymupdf numpy   # or: pip install -r ../requirements.txt
```
Drop a sample manual PDF into `rag_lab/data/`.

## Files (fill these in — they're intentionally stubs)
| File | Stage | What it does |
|------|-------|--------------|
| `load.py`     | R1 | PDF → list of (page_number, text) |
| `chunk.py`    | R2 | pages → overlapping chunks (carry page + doc id) |
| `embed.py`    | R3 | texts → vectors (local model) |
| `search.py`   | R4 | in-memory index + cosine top-k search |
| `retrieve.py` | R5 | CLI: question → printed top-k chunks (NO LLM) |
| `evaluate.py` | R6 | hit@k / MRR against `eval_set.json` |

## Run (once implemented)
```bash
python -m rag_lab.retrieve "how do I reset machine X?"
python -m rag_lab.evaluate
```

## The point
`retrieve.py`'s output is exactly the context an LLM would receive. If you can answer
the question from those chunks, the RAG system works — before any LLM exists.
