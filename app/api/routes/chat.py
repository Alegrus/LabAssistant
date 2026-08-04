"""Chat routes (Phase 3): grounded, cited, multi-chat conversation (R1,R2,R8,R9).

Each turn runs rag.answer() — retrieval → grounding prompt → LLM → citations — and
persists both the user question and the assistant reply. Citations are stored on the
assistant message and rendered as links to the source manual, deep-linked to the page.
"""
import json
import logging
import shutil
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.dependencies import Principal, require_user
from app.models.access_log import AccessLog
from app.models.chat import Chat
from app.models.document import Document
from app.models.message import Message
from app.services import rag, vision
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_UPLOAD_DIR = Path("storage/uploads")


def _query_log(
    principal: Principal,
    request: Request,
    outcome: str,
    latency_ms: int,
    detail: str | None = None,
) -> AccessLog:
    """A per-turn activity row for the access log: what happened + how long it took."""
    return AccessLog(
        user_id=principal.user_id,
        event="query",
        display_name=principal.display_name,
        outcome=outcome,
        latency_ms=latency_ms,
        detail=detail,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def _error_detail(exc: httpx.HTTPError) -> str:
    """Concise, human-readable reason for a failed LLM turn (for the access log)."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        msg = ""
        try:
            msg = exc.response.json().get("error", {}).get("message", "") or ""
        except Exception:
            pass
        return (f"HTTP {code}: {msg}" if msg else f"HTTP {code}")[:200]
    if isinstance(exc, httpx.TimeoutException):
        return "Request timed out"
    return exc.__class__.__name__[:200]


def _owned_chat(db: Session, principal: Principal, chat_id: int) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _user_chats(db: Session, principal: Principal) -> list[Chat]:
    return (
        db.query(Chat)
        .filter(Chat.user_id == principal.user_id)
        .order_by(Chat.updated_at.desc())
        .all()
    )


def _pending_message(chat: Chat) -> Message | None:
    """The user turn awaiting confirmation of its photo interpretation, if any."""
    return next((m for m in chat.messages if m.pending), None)


def _answer_and_persist(
    db: Session,
    request: Request,
    principal: Principal,
    chat: Chat,
    question: str,
    history: list[dict],
    image_context: str | None = None,
) -> RedirectResponse:
    """Run rag.answer, persist the assistant reply + access-log row, and redirect.

    On an LLM failure, rolls back (so no half-saved turn / a pending turn stays pending),
    records the error for telemetry, and redirects with a retry banner. Shared by the
    plain-text turn and the post-confirmation image turn.
    """
    started = time.monotonic()
    try:
        result = rag.answer(db, question, history=history, image_context=image_context)
    except httpx.HTTPError as exc:
        db.rollback()
        latency_ms = int((time.monotonic() - started) * 1000)
        db.add(_query_log(principal, request, "error", latency_ms, _error_detail(exc)))
        db.commit()
        logger.warning("LLM call failed for chat %s: %s", chat.id, exc)
        draft = urllib.parse.quote(question)
        return RedirectResponse(f"/chat/{chat.id}?error=llm&draft={draft}", status_code=303)

    latency_ms = int((time.monotonic() - started) * 1000)
    db.add(
        Message(
            chat_id=chat.id,
            role="assistant",
            content=result.content,
            not_found=result.not_found,
            citations=[
                {"markers": c.markers, "document_id": c.document_id,
                 "filename": c.filename, "pages": c.pages}
                for c in result.citations
            ] or None,
        )
    )
    db.add(
        _query_log(
            principal, request,
            "not_found" if result.not_found else "answered",
            latency_ms,
        )
    )
    chat.updated_at = datetime.utcnow()  # bump so the hub orders by recency
    db.commit()
    return RedirectResponse(f"/chat/{chat.id}", status_code=303)


@router.get("/")
def chat_home(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
):
    return templates.TemplateResponse(
        request, "chat/list.html", {"principal": principal, "chats": _user_chats(db, principal)}
    )


@router.post("/chat/new")
def new_chat(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
):
    chat = Chat(user_id=principal.user_id, title="New chat")
    db.add(chat)
    db.commit()
    return RedirectResponse(f"/chat/{chat.id}", status_code=303)


@router.get("/chat/{chat_id}")
def view_chat(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
    error: str | None = None,
    draft: str | None = None,
):
    chat = _owned_chat(db, principal, chat_id)
    return templates.TemplateResponse(
        request,
        "chat/detail.html",
        {
            "principal": principal,
            "chat": chat,
            "messages": chat.messages,
            "chats": _user_chats(db, principal),
            "error": error,
            "draft": draft or "",
            "pending_msg": _pending_message(chat),
            "max_image_mb": settings.max_image_mb,
        },
    )


@router.post("/chat/{chat_id}/message")
def post_message(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
    content: str = Form(...),
    image: UploadFile | None = File(None),
):
    chat = _owned_chat(db, principal, chat_id)
    question = content.strip()
    if not question:
        return RedirectResponse(f"/chat/{chat_id}", status_code=303)

    # One turn at a time: don't accept a new message while a photo interpretation is
    # still awaiting confirmation.
    if _pending_message(chat) is not None:
        return RedirectResponse(f"/chat/{chat_id}", status_code=303)

    if image is not None and (image.filename or "").strip():
        return _handle_image_message(db, request, principal, chat, question, image)

    # Prior turns become conversation history (captured BEFORE adding this question,
    # since rag.answer() appends the current question itself).
    history = [{"role": m.role, "content": m.content} for m in chat.messages]
    db.add(Message(chat_id=chat.id, role="user", content=question))
    if chat.title == "New chat":
        chat.title = question[:60]
    return _answer_and_persist(db, request, principal, chat, question, history)


def _handle_image_message(
    db: Session,
    request: Request,
    principal: Principal,
    chat: Chat,
    question: str,
    image: UploadFile,
) -> RedirectResponse:
    """Save the photo, run question-conditioned vision extraction, and park the turn as
    'pending' so the user can confirm the interpretation before we answer."""
    if not (image.content_type or "").startswith("image/"):
        return RedirectResponse(f"/chat/{chat.id}?error=image_type", status_code=303)
    if (image.size or 0) > settings.max_image_mb * 1024 * 1024:
        return RedirectResponse(f"/chat/{chat.id}?error=image_size", status_code=303)

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(image.filename).suffix.lower() or ".img"
    stored = _UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    image.file.seek(0)
    with stored.open("wb") as out:
        shutil.copyfileobj(image.file, out)

    started = time.monotonic()
    try:
        observations = vision.extract_observations(str(stored), question)
    except httpx.HTTPError as exc:
        stored.unlink(missing_ok=True)
        latency_ms = int((time.monotonic() - started) * 1000)
        db.add(_query_log(principal, request, "error", latency_ms, _error_detail(exc)))
        db.commit()
        logger.warning("Vision call failed for chat %s: %s", chat.id, exc)
        draft = urllib.parse.quote(question)
        return RedirectResponse(f"/chat/{chat.id}?error=llm&draft={draft}", status_code=303)

    db.add(
        Message(
            chat_id=chat.id,
            role="user",
            content=question,
            image_path=str(stored),
            image_observations=observations,
            pending=True,
        )
    )
    if chat.title == "New chat":
        chat.title = question[:60]
    chat.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/chat/{chat.id}", status_code=303)


@router.post("/chat/{chat_id}/stream")
def stream_message(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
    content: str = Form(""),
    confirm: str = Form(""),
    observations: str = Form(""),
):
    """Stream a turn as newline-delimited JSON so tokens appear as they're generated.

    Emits {"type":"token"} objects, then exactly one {"type":"done"} (with citations) or
    {"type":"error"}. Handles both a plain text question and confirmation of a pending
    photo turn; the non-streaming POST routes remain as a no-JS fallback.
    """
    chat = _owned_chat(db, principal, chat_id)  # 404s before streaming starts
    is_confirm = bool(confirm)
    if not is_confirm and not content.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    def generate():
        # The response body is produced after the request's session would be closed, so
        # this generator owns its own session for the whole turn.
        sdb = SessionLocal()
        started = time.monotonic()
        try:
            schat = sdb.get(Chat, chat_id)
            pending = _pending_message(schat) if is_confirm else None
            if is_confirm and pending is None:
                yield json.dumps({"type": "error", "message": "Nothing to confirm."}) + "\n"
                return

            if is_confirm:
                image_context = observations.strip() or pending.image_observations
                question = pending.content
                history = [
                    {"role": m.role, "content": m.content}
                    for m in schat.messages
                    if m.id != pending.id
                ]
            else:
                image_context = None
                question = content.strip()
                history = [{"role": m.role, "content": m.content} for m in schat.messages]
                sdb.add(Message(chat_id=schat.id, role="user", content=question))
                if schat.title == "New chat":
                    schat.title = question[:60]

            result = None
            for kind, payload in rag.answer_stream(
                sdb, question, history=history, image_context=image_context
            ):
                if kind == "token":
                    yield json.dumps({"type": "token", "text": payload}) + "\n"
                elif kind == "status":
                    yield json.dumps({"type": "status", "text": payload}) + "\n"
                else:
                    result = payload

            citations = [
                {"markers": c.markers, "document_id": c.document_id,
                 "filename": c.filename, "pages": c.pages}
                for c in result.citations
            ]
            if is_confirm:
                pending.image_observations = image_context
                pending.pending = False  # only cleared once an answer actually exists
            sdb.add(
                Message(
                    chat_id=schat.id,
                    role="assistant",
                    content=result.content,
                    not_found=result.not_found,
                    citations=citations or None,
                )
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            sdb.add(
                _query_log(
                    principal, request,
                    "not_found" if result.not_found else "answered",
                    latency_ms,
                )
            )
            schat.updated_at = datetime.utcnow()
            sdb.commit()
            yield json.dumps(
                {
                    "type": "done",
                    "citations": citations,
                    "not_found": result.not_found,
                    "title": schat.title,
                }
            ) + "\n"
        except httpx.HTTPError as exc:
            sdb.rollback()
            latency_ms = int((time.monotonic() - started) * 1000)
            sdb.add(_query_log(principal, request, "error", latency_ms, _error_detail(exc)))
            sdb.commit()
            logger.warning("Streamed LLM call failed for chat %s: %s", chat_id, exc)
            yield json.dumps(
                {
                    "type": "error",
                    "message": "The assistant is temporarily unavailable "
                               "(the model was rate-limited, timed out, or is unreachable).",
                }
            ) + "\n"
        finally:
            sdb.close()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/{chat_id}/confirm")
def confirm_message(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
    observations: str = Form(...),
):
    """Confirm (possibly edited) photo observations, then answer from the manuals."""
    chat = _owned_chat(db, principal, chat_id)
    pending = _pending_message(chat)
    if pending is None:
        return RedirectResponse(f"/chat/{chat_id}", status_code=303)

    pending.image_observations = observations.strip() or pending.image_observations
    pending.pending = False
    history = [
        {"role": m.role, "content": m.content}
        for m in chat.messages
        if m.id != pending.id
    ]
    return _answer_and_persist(
        db, request, principal, chat, pending.content, history,
        image_context=pending.image_observations,
    )


@router.post("/chat/{chat_id}/cancel")
def cancel_message(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
):
    """Discard a pending photo turn (deletes the message + its stored image)."""
    chat = _owned_chat(db, principal, chat_id)
    pending = _pending_message(chat)
    if pending is not None:
        path = pending.image_path
        db.delete(pending)
        db.commit()
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
    return RedirectResponse(f"/chat/{chat_id}", status_code=303)


@router.get("/chat/{chat_id}/image/{message_id}")
def serve_chat_image(
    chat_id: int,
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
):
    """Serve a chat's uploaded photo to its owner (or an admin reviewing transcripts)."""
    chat = db.get(Chat, chat_id)
    if chat is None or (chat.user_id != principal.user_id and principal.role != "admin"):
        raise HTTPException(status_code=404, detail="Not found")
    msg = db.get(Message, message_id)
    if (
        msg is None
        or msg.chat_id != chat.id
        or not msg.image_path
        or not Path(msg.image_path).exists()
    ):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(msg.image_path)


@router.get("/documents/{document_id}/file")
def serve_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_user),
):
    """Serve a source PDF for citations (any logged-in user). Inline so the browser
    PDF viewer opens and #page=N deep-links work."""
    doc = db.get(Document, document_id)
    if doc is None or not Path(doc.path).exists():
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(
        doc.path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )
