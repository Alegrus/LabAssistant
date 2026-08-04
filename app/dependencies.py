"""FastAPI auth dependencies."""
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.config import settings
from app.core import security


@dataclass
class Principal:
    user_id: str
    role: str
    display_name: str | None


def get_principal(request: Request) -> Principal | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    data = security.read_session(token, settings.session_max_age_seconds)
    if not data:
        return None
    return Principal(data["uid"], data["role"], data.get("name"))


def require_user(request: Request) -> Principal:
    p = get_principal(request)
    if p is None:
        raise HTTPException(status_code=401)
    return p


def require_admin(request: Request) -> Principal:
    p = require_user(request)
    if p.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    return p
