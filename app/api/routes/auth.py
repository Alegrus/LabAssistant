"""Auth routes: login and logout. The authed landing page ("/") lives in chat.py."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.core import security
from app.core.limiter import limiter
from app.database import get_db
from app.dependencies import get_principal
from app.models.access_log import AccessLog
from app.models.app_settings import get_app_settings
from app.models.user import User
from app.templating import templates

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request):
    p = get_principal(request)
    if p is not None:
        return RedirectResponse("/admin" if p.role == "admin" else "/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": False})


@router.post("/login")
@limiter.limit(settings.login_rate_limit)
async def login(
    request: Request,
    db=Depends(get_db),
    password: str = Form(...),
    name: str = Form(""),
):
    row = get_app_settings(db)
    if row is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": True},
            status_code=500,
        )

    if security.verify_password(password, row.admin_password_hash):
        role, display_name = "admin", "admin"
    elif security.verify_password(password, row.user_password_hash):
        role, display_name = "user", (name.strip() or "Anonymous")
    else:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": True},
            status_code=401,
        )

    # (F) device identity: reuse existing device_id cookie or mint a new one
    device_id = request.cookies.get(settings.device_cookie_name) or uuid.uuid4().hex

    user = (
        db.query(User)
        .filter(User.device_id == device_id, User.role == role)
        .one_or_none()
    )
    if user is None:
        user = User(device_id=device_id, role=role, display_name=display_name)
        db.add(user)
    else:
        user.display_name = display_name
        user.last_seen_at = datetime.utcnow()
    db.flush()

    db.add(
        AccessLog(
            user_id=user.id,
            event="login",
            display_name=display_name,  # snapshot the name used at THIS login
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    )
    db.commit()

    token = security.make_session({"uid": user.id, "role": role, "name": display_name})
    target = "/admin" if role == "admin" else "/"
    resp = RedirectResponse(target, status_code=303)
    secure = settings.app_env != "development"
    resp.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    resp.set_cookie(
        settings.device_cookie_name,
        device_id,
        max_age=settings.device_id_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(settings.session_cookie_name)
    return resp
