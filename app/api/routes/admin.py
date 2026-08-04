"""Admin routes: dashboard, password management, access log, chat review (R5, R7, R10)."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import security
from app.database import get_db
from app.dependencies import require_admin, Principal
from app.models.access_log import AccessLog
from app.models.app_settings import get_app_settings
from app.models.chat import Chat
from app.models.message import Message
from app.models.user import User
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("")
def dashboard(request: Request, principal: Principal = Depends(require_admin)):
    return templates.TemplateResponse(
        request, "admin/dashboard.html", {"principal": principal}
    )


@router.get("/security")
def security_page(request: Request, principal: Principal = Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "admin/security.html",
        {"principal": principal, "success": False, "error": None},
    )


@router.post("/security")
def update_security(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
    current_admin_password: str = Form(...),
    new_user_password: str = Form(""),
    new_admin_password: str = Form(""),
):
    row = get_app_settings(db)

    if not security.verify_password(current_admin_password, row.admin_password_hash):
        return templates.TemplateResponse(
            request,
            "admin/security.html",
            {
                "principal": principal,
                "success": False,
                "error": "Current admin password is incorrect.",
            },
            status_code=400,
        )

    if new_user_password:
        row.user_password_hash = security.hash_password(new_user_password)
    if new_admin_password:
        row.admin_password_hash = security.hash_password(new_admin_password)
    db.commit()

    return templates.TemplateResponse(
        request,
        "admin/security.html",
        {
            "principal": principal,
            "success": True,
            "error": None,
        },
    )


@router.get("/chats")
def all_chats(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    """Every conversation across all users/devices (R7), most-recent first."""
    counts = dict(
        db.query(Message.chat_id, func.count(Message.id)).group_by(Message.chat_id).all()
    )
    rows = (
        db.query(Chat, User)
        .outerjoin(User, User.id == Chat.user_id)
        .order_by(Chat.updated_at.desc())
        .limit(500)
        .all()
    )
    chats = [(chat, user, counts.get(chat.id, 0)) for chat, user in rows]
    return templates.TemplateResponse(
        request, "admin/chats.html", {"principal": principal, "chats": chats}
    )


@router.get("/chats/{chat_id}")
def chat_transcript(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    """Read-only transcript of a single conversation, with the asker's identity."""
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    user = db.get(User, chat.user_id)
    return templates.TemplateResponse(
        request,
        "admin/chat_detail.html",
        {"principal": principal, "chat": chat, "user": user, "messages": chat.messages},
    )


@router.get("/access-log")
def access_log(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    rows = (
        db.query(AccessLog, User)
        .outerjoin(User, User.id == AccessLog.user_id)
        .order_by(AccessLog.created_at.desc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/access_log.html",
        {"principal": principal, "rows": rows},
    )
