"""Document ingestion: parse -> chunk -> embed -> persist.

Runs synchronously inside the admin upload request (decision N4): the admin sees the
final `ready`/`failed` status when the call returns. Status + error are recorded so
a garbled manual is visible rather than silently missing from answers.

Deletion is handled by deleting the Document row — `ON DELETE CASCADE` removes its
chunks (and their vectors), so it stops appearing in answers/citations (R6).
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.services import embeddings
from app.services.chunking import chunk_page


@dataclass
class _Page:
    page_number: int
    text: str


def _load_pdf(path: str) -> list[_Page]:
    """Extract text per page, preserving 1-based page numbers for citations."""
    import pymupdf

    with pymupdf.open(path) as doc:
        return [_Page(page_number=i + 1, text=page.get_text()) for i, page in enumerate(doc)]


def ingest_document(db: Session, document: Document) -> Document:
    """Parse, chunk, embed and index a document. Sets status to ready/failed.

    Raises on failure (after recording the error) so the caller can surface it.
    """
    document.status = DocumentStatus.processing
    db.commit()

    try:
        pages = _load_pdf(document.path)

        text_chunks = []
        ordinal = 0
        for page in pages:
            page_chunks = chunk_page(page.text, page.page_number, ordinal)
            ordinal += len(page_chunks)
            text_chunks.extend(page_chunks)

        if not text_chunks:
            raise ValueError("No extractable text (scanned/empty PDF — OCR needed?)")

        vectors = embeddings.embed_passages([c.text for c in text_chunks])

        db.add_all(
            Chunk(
                document_id=document.id,
                ordinal=tc.ordinal,
                page=tc.page,
                text=tc.text,
                token_count=tc.token_count,
                embedding=vec,
            )
            for tc, vec in zip(text_chunks, vectors)
        )

        document.status = DocumentStatus.ready
        document.error = None
        db.commit()
        return document

    except Exception as exc:
        db.rollback()
        document.status = DocumentStatus.failed
        document.error = str(exc)
        db.commit()
        raise


def delete_document(db: Session, document: Document) -> None:
    """Remove a document and (via cascade) all its chunks/embeddings."""
    db.delete(document)
    db.commit()
