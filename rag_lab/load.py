"""R1 — Load a PDF into (page_number, text) pairs.

Learn: parsing is lossy. Print a few pages and compare to the real PDF to see
where text gets mangled (tables, columns, hyphenation). Garbage in -> garbage
retrieval.

Hint: use pymupdf (`import fitz`); iterate pages; `page.get_text()`.
"""


from dataclasses import dataclass
import pymupdf


@dataclass
class Page:
    page_number: int  # 1-based, so it maps to what a human sees / citation links
    text: str


def load_pdf(path: str) -> list[Page]:
    """Return one Page per page of the PDF. TODO: implement (R1)."""
    document = pymupdf.open(path)
    pages = [x.get_text() for x in document]
    for i in range(len(pages)):
        pages[i] = Page(page_number=i + 1, text=pages[i])
    return pages


if __name__ == "__main__":
    # Quick manual check: print the first few pages.
    import sys
    pages = load_pdf(sys.argv[1])
    for p in pages[:3]:
        print(f"--- page {p.page_number} ---\n{p.text[:500]}\n")
