"""R5 — The "answer" with NO LLM.

Build the index from every PDF in rag_lab/data/, then print the top-k chunks for a
question. This printed output is EXACTLY the context an LLM would receive. If a
human can answer the question from these chunks, the RAG system works.

Usage: python -m rag_lab.retrieve "how do I reset machine X?"
"""
import glob
import sys

from rag_lab.chunk import chunk
from rag_lab.load import load_pdf
from rag_lab.search import InMemoryIndex


def build_index() -> InMemoryIndex:
    index = InMemoryIndex()
    for path in glob.glob("rag_lab/data/*.pdf"):
        pages = load_pdf(path)
        index.add(chunk(pages, document_id=path))
    return index


def main() -> None:
    question = " ".join(sys.argv[1:]) or "how do I reset the machine?"
    index = build_index()
    for hit in index.search(question, top_k=5):
        c = hit.chunk
        print(f"[{hit.score:.3f}] {c.document_id} p.{c.page}\n{c.text}\n")


if __name__ == "__main__":
    main()
