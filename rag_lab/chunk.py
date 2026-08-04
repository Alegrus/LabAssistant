"""R2 — Split pages into overlapping chunks.

Learn the size/overlap trade-off: too big dilutes the relevant sentence (poor
precision); too small severs instructions (poor recall); overlap reduces boundary
cuts. Experiment: chunk the same doc at 200 / 500 / 1000 and eyeball the results.

IMPORTANT: every chunk keeps its source `page` and `document_id` — that metadata
later powers clickable citations (R9) and per-machine filtering.
"""
from dataclasses import dataclass

from rag_lab.load import Page


@dataclass
class Chunk:
    document_id: str
    page: int
    text: str


def chunk(pages: list[Page], document_id: str, size: int = 500, overlap: int = 80) -> list[Chunk]:
    """Sliding window over each page's text. TODO: implement (R2).

    Start simple: fixed-size character windows with `overlap` characters shared
    between neighbours. Stretch later: split on paragraph/sentence boundaries.
    """

    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    chunks = []
    stride = size - overlap  # how far the window moves each step

    for page in pages:
        text = page.text
        if not text.strip():
            continue  # skip blank pages — they'd embed to noise

        start = 0
        while start < len(text):
            piece = text[start : start + size]
            chunks.append(Chunk(document_id=document_id, page=page.page_number, text=piece))
            if start + size >= len(text):
                break  # this chunk reached the end; don't emit a trailing duplicate
            start += stride

    return chunks

