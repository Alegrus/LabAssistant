"""Admin document management (Phase 2, R6): batch upload → ingest, list, delete.

Uploads are processed ONE FILE AT A TIME and streamed straight to disk, so peak
memory is bounded by the largest single file, not the batch total. Ingestion runs
synchronously (decision N4) — each file ends up ready/failed and is visible on the
list. Delete removes the DB row (chunks cascade) and the file on disk.
"""
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import Principal, require_admin
from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.services import ingestion
from app.templating import templates

router = APIRouter(prefix="/admin/documents", tags=["documents"])

_STORAGE_DIR = Path("storage/documents")


def _render_list(request, db, principal, error=None, status_code=200):
    docs = db.query(Document).order_by(Document.created_at.desc()).all()
    counts = dict(
        db.query(Chunk.document_id, func.count(Chunk.id))
        .group_by(Chunk.document_id)
        .all()
    )
    rows = [(d, counts.get(d.id, 0)) for d in docs]

    # A batch upload redirects back with a ?ingested&failed&skipped summary.
    qp = request.query_params
    summary = None
    if any(k in qp for k in ("ingested", "failed", "skipped")):
        summary = {
            "ingested": qp.get("ingested", "0"),
            "failed": qp.get("failed", "0"),
            "skipped": qp.get("skipped", "0"),
        }

    return templates.TemplateResponse(
        request,
        "admin/documents.html",
        {
            "principal": principal,
            "rows": rows,
            "error": error,
            "summary": summary,
            "max_mb": settings.max_upload_mb,
            "batch_mb": settings.max_batch_mb,
            "warn_mb": settings.upload_warn_mb,
        },
        status_code=status_code,
    )


@router.get("")
def list_documents(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    return _render_list(request, db, principal)


@router.post("")
async def upload_documents(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
    files: list[UploadFile] = File(...),
    title: str = Form(""),
):
    valid = [f for f in files if f.filename]
    if not valid:
        return _render_list(request, db, principal, "No files selected.", 400)

    # Batch-size guard (disk + request-time). Uses the multipart part sizes; no read.
    total_bytes = sum((f.size or 0) for f in valid)
    if total_bytes > settings.max_batch_mb * 1024 * 1024:
        return _render_list(
            request,
            db,
            principal,
            f"This batch is {total_bytes / 1048576:.0f} MB, over the "
            f"{settings.max_batch_mb} MB limit. Upload fewer files at once.",
            400,
        )

    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    max_file_bytes = settings.max_upload_mb * 1024 * 1024
    single = len(valid) == 1
    ingested = failed = skipped = 0

    for f in valid:
        name = f.filename
        if not name.lower().endswith(".pdf") or (f.size or 0) > max_file_bytes:
            skipped += 1  # wrong type or over the per-file limit
            continue

        # Stream to disk (small buffer) — never loads the whole file into memory.
        stored_path = _STORAGE_DIR / f"{uuid.uuid4().hex}.pdf"
        f.file.seek(0)
        with stored_path.open("wb") as out:
            shutil.copyfileobj(f.file, out)

        # Custom display name applies only to a single-file upload; batches use filenames.
        display_name = title.strip() if (single and title.strip()) else name
        document = Document(
            filename=display_name,
            path=str(stored_path),
            content_type=f.content_type,
            status=DocumentStatus.queued,
            uploaded_by=principal.display_name,
        )
        db.add(document)
        db.commit()

        try:
            ingestion.ingest_document(db, document)
            ingested += 1
        except Exception:
            failed += 1  # status=failed + error already recorded on the row

    query = urlencode({"ingested": ingested, "failed": failed, "skipped": skipped})
    return RedirectResponse(f"/admin/documents?{query}", status_code=303)


@router.post("/{document_id}/delete")
def delete_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    path = document.path
    ingestion.delete_document(db, document)  # removes row + chunks (cascade)
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass  # file already gone; the DB is the source of truth

    return RedirectResponse("/admin/documents", status_code=303)
