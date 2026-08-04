# Phase 1 — Auth & Access Logging: Implementation Spec

Status: Ready to build · Targets requirements **R5** (see who accessed) and **R10**
(two-password security, both changeable). Rate limiting **N2** (app-level half).

This is a literal build spec: file-by-file, with model columns, function signatures,
route behavior, and the error-prone snippets written out. Follow the **Build order**
checklist at the bottom top-to-bottom. Don't introduce new architecture — every
decision you need has already been made in the "Decisions" section.

---

## What Phase 1 delivers

1. A `app_settings` table holding the hashed **user** and **admin** passwords — the
   only place passwords live.
2. A `/login` page: one password field decides the role (user vs admin); users also
   give a display name so the admin can see who accessed (R5).
3. Stateless signed-cookie sessions (`{user_id, role, name}`) plus a long-lived
   `device_id` cookie that links repeat logins from the same device to one identity.
4. `require_user` / `require_admin` FastAPI dependencies protecting routes.
5. An `access_log` row written on every login (R5).
6. An admin **change-passwords** page that can rotate either/both passwords (R10).
7. App-level rate limiting on `/login` (N2).
8. A minimal admin landing + access-log table to prove R5 end-to-end.

Out of scope (later phases / follow-ups): the chat UI (Phase 3), the full review
dashboard (Phase 5), Alembic migrations, CSRF tokens, edge/WAF rate limiting,
session revocation on password change. These are listed under **Follow-ups**.

---

## Decisions (do not re-litigate)

- **Identity model (A+F hybrid):** identity is a *labeled session*, not a verified
  account — the shared password can't identify a person. Two layers:
  - **(A)** the user types a **display name** for human-readable attribution (R5/R7).
  - **(F)** the browser carries a long-lived **`device_id`** cookie. On login we
    **find-or-create** a `User` keyed by `(device_id, role)`, so the same phone reuses
    one identity across logins instead of spawning a new `User` every time.

  Each login still writes its own `access_log` row (so "who accessed and when" stays
  per-login), while the `User` row is stable per device. Chats (Phase 3) and
  access-log rows reference `user.id`. A new device or cleared cookies = a new
  identity — a documented limitation, acceptable for the MVP.
- **Role detection:** there is ONE password field. On submit, check the admin hash
  first, then the user hash. Admin password wins ties. Name is only meaningful for
  users; for admin it's forced to `"admin"`.
- **Sessions:** stateless, via `itsdangerous.URLSafeTimedSerializer` (already a
  dependency). No server-side session store. Expiry enforced by `max_age`.
- **Cookies (two):**
  - `session` — signed session, `HttpOnly`, `SameSite=Lax`, `Secure` in non-dev,
    `max_age = settings.session_max_age_seconds` (12h).
  - `device_id` — random hex, set if absent, **long-lived**
    (`device_id_max_age_seconds`, ~1 year), `HttpOnly`, `SameSite=Lax`, `Secure` in
    non-dev. Identifies a *device*, not a person.
- **Schema creation:** use `app.database.init_db()` at startup for dev (it creates
  tables + the pgvector extension). Alembic is a Follow-up, not Phase 1.
- **Rate limiting:** app-level via `slowapi` on `POST /login`. The edge/WAF half of
  N2 is deploy-time config, not code.
- **DB:** the app already targets Postgres+pgvector (Chunk uses `Vector`/`TSVECTOR`),
  so run Phase 1 against the dev Postgres (`docker compose up -d db`), not SQLite.

---

## Config additions — `app/config.py`

Add these fields to `Settings` (keep existing ones):

```python
    # --- Sessions / auth ---
    session_cookie_name: str = "session"
    session_max_age_seconds: int = 60 * 60 * 12  # 12h
    device_cookie_name: str = "device_id"
    device_id_max_age_seconds: int = 60 * 60 * 24 * 365  # ~1 year
    login_rate_limit: str = "10/minute"
```

`secret_key`, `app_env`, `initial_user_password`, `initial_admin_password` already
exist and are used below.

---

## New models

Create three model files, then register them in `app/models/__init__.py`.

### `app/models/app_settings.py`
Singleton row (id is always `1`).

| column | type | notes |
|--------|------|-------|
| id | Integer PK | always 1 |
| user_password_hash | String(255) | bcrypt hash |
| admin_password_hash | String(255) | bcrypt hash |
| updated_at | DateTime | `default`+`onupdate` = `datetime.utcnow` |

Also add a helper in this module:
```python
def get_app_settings(db) -> "AppSettings":
    """Fetch the singleton settings row (id=1). Assumes seed.py has run."""
    return db.get(AppSettings, 1)
```

### `app/models/user.py`
A login principal.

| column | type | notes |
|--------|------|-------|
| id | String(32) PK | `default=lambda: uuid.uuid4().hex` |
| device_id | String(32), indexed | the `device_id` cookie value this identity belongs to |
| display_name | String(120), nullable | user's entered name; `"admin"` for admins |
| role | String(16) | `"user"` or `"admin"` |
| created_at | DateTime | `default=datetime.utcnow` |
| last_seen_at | DateTime | `default` + `onupdate` = `datetime.utcnow`; bumped each login |

Add a unique constraint on `(device_id, role)` so find-or-create is unambiguous:
```python
from sqlalchemy import UniqueConstraint
__table_args__ = (UniqueConstraint("device_id", "role", name="uq_user_device_role"),)
```

(Chats will FK to `user.id` in Phase 3 — don't add that relationship yet.)

### `app/models/access_log.py`
One row per login (R5).

| column | type | notes |
|--------|------|-------|
| id | Integer PK | |
| user_id | FK→users.id, nullable | `ondelete="SET NULL"` |
| event | String(32) | `"login"` for now |
| ip | String(64), nullable | `request.client.host` |
| user_agent | String(512), nullable | from header |
| created_at | DateTime, indexed | `default=datetime.utcnow` |

### `app/models/__init__.py`
Append imports so they register on `Base.metadata`:
```python
from app.models.app_settings import AppSettings, get_app_settings
from app.models.user import User
from app.models.access_log import AccessLog
```
Add them to `__all__`.

Follow the column/typing style already in `app/models/document.py` (use
`Mapped[...]` + `mapped_column(...)`).

---

## `app/core/security.py` — replace the two stubs

Keep `pwd_context`, `hash_password`, `verify_password`. **Delete**
`create_session_token` and `change_password`. Add the serializer-based session
helpers:

```python
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="session")

def make_session(payload: dict) -> str:
    """Sign a session payload into an opaque cookie string."""
    return _serializer.dumps(payload)

def read_session(token: str, max_age: int) -> dict | None:
    """Verify + decode a session cookie. Returns None if invalid or expired."""
    try:
        return _serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
```

---

## `app/core/limiter.py` — new (shared limiter)

`slowapi` needs the same `Limiter` instance in `main.py` (state + handler) and in
the route module (decorator), so it lives in its own module:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
```

---

## `app/dependencies.py` — new (auth dependencies)

```python
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
        raise HTTPException(status_code=401)  # handler redirects to /login
    return p


def require_admin(request: Request) -> Principal:
    p = require_user(request)
    if p.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    return p
```

Routes consume these with `principal: Principal = Depends(require_admin)`.

---

## Routes

Use `Jinja2Templates(directory="app/templates")`. Create one instance in `main.py`
and import it, OR create a tiny `app/templating.py` exporting `templates =
Jinja2Templates(...)` and import it in both route modules. (Pick the second — avoids
a circular import with `main`.)

### `app/api/routes/auth.py`

`router = APIRouter(tags=["auth"])`. Endpoints:

| method | path | auth | behavior |
|--------|------|------|----------|
| GET | `/login` | none | Render `login.html`. Accept optional `?error=1` to show a message. If already logged in, redirect (admin→`/admin`, user→`/`). |
| POST | `/login` | none, **rate-limited** | See logic below. |
| POST | `/logout` | none | Delete the session cookie, redirect `303 → /login`. |

`POST /login` logic (form fields `password` required, `name` optional):
```
row = get_app_settings(db)
if security.verify_password(password, row.admin_password_hash):
    role, name = "admin", "admin"
elif security.verify_password(password, row.user_password_hash):
    role, name = "user", (name.strip() or "Anonymous")
else:
    re-render login.html with an error, status 401

# (F) device identity: reuse the browser's device_id cookie, or mint a new one.
device_id = request.cookies.get(settings.device_cookie_name) or uuid.uuid4().hex

# find-or-create the User for THIS device + role (one identity per device per role)
user = (db.query(User)
          .filter(User.device_id == device_id, User.role == role)
          .one_or_none())
if user is None:
    user = User(device_id=device_id, role=role, display_name=name)
    db.add(user)
else:
    user.display_name = name                 # allow updating the name
    user.last_seen_at = datetime.utcnow()
db.flush()  # ensure user.id is populated

# One access_log row PER login, even when the User is reused (R5 = per-login events).
db.add(AccessLog(user_id=user.id, event="login",
                 ip=request.client.host,
                 user_agent=request.headers.get("user-agent")))
db.commit()

token = security.make_session({"uid": user.id, "role": role, "name": name})
target = "/admin" if role == "admin" else "/"
resp = RedirectResponse(target, status_code=303)
resp.set_cookie(settings.session_cookie_name, token,
                max_age=settings.session_max_age_seconds,
                httponly=True, samesite="lax",
                secure=(settings.app_env != "development"))
# set/refresh the long-lived device cookie
resp.set_cookie(settings.device_cookie_name, device_id,
                max_age=settings.device_id_max_age_seconds,
                httponly=True, samesite="lax",
                secure=(settings.app_env != "development"))
return resp
```
Imports for this handler: `import uuid` and `from datetime import datetime`.

Decorate the POST with the limiter (the function **must** take `request: Request`):
```python
from app.core.limiter import limiter

@router.post("/login")
@limiter.limit(settings.login_rate_limit)
async def login(request: Request, db: Session = Depends(get_db),
                password: str = Form(...), name: str = Form("")):
    ...
```

Get `db` via `Depends(get_db)` (already defined in `app/database.py`). Form parsing
needs `python-multipart` (already in requirements).

### `app/api/routes/admin.py`

`router = APIRouter(prefix="/admin", tags=["admin"])`, all routes
`Depends(require_admin)`.

| method | path | behavior |
|--------|------|----------|
| GET | `/admin` | Render `admin/dashboard.html` — links to security + access log. |
| GET | `/admin/security` | Render `admin/security.html` (the change-password form). |
| POST | `/admin/security` | Rotate passwords (logic below). |
| GET | `/admin/access-log` | List recent logins for R5 (logic below). |

`POST /admin/security` (fields: `current_admin_password` required,
`new_user_password` optional, `new_admin_password` optional):
```
row = get_app_settings(db)
if not security.verify_password(current_admin_password, row.admin_password_hash):
    re-render with error "current admin password incorrect", status 400
if new_user_password:  row.user_password_hash  = security.hash_password(new_user_password)
if new_admin_password: row.admin_password_hash = security.hash_password(new_admin_password)
db.commit()
re-render with a success message
```

`GET /admin/access-log`:
```
rows = (db.query(AccessLog, User)
          .outerjoin(User, User.id == AccessLog.user_id)
          .order_by(AccessLog.created_at.desc())
          .limit(200).all())
# pass to template: name, role, event, ip, user_agent, created_at
```

---

## Templates (`app/templates/`)

Mobile-first (this is scanned from a phone). Use Tailwind via CDN for now
(`<script src="https://cdn.tailwindcss.com"></script>`) — swap to a build step later.
Keep them minimal; no JS needed beyond the form submit.

- **`base.html`** — skeleton with `<meta name="viewport" content="width=device-width,
  initial-scale=1">`, the Tailwind CDN, a `{% block content %}{% endblock %}`, and a
  centered max-width container. All other templates `{% extends "base.html" %}`.
- **`login.html`** — a `<form method="post" action="/login">` with:
  - text input `name="name"` labeled "Your name" (hint: "for the lab log").
  - password input `name="password"` (required).
  - submit button "Enter".
  - show an error banner when `error` is set.
- **`admin/dashboard.html`** — greeting + links to `/admin/security` and
  `/admin/access-log` and a `POST /logout` button.
- **`admin/security.html`** — a `<form method="post" action="/admin/security">`:
  - password `current_admin_password` (required).
  - password `new_user_password` (optional).
  - password `new_admin_password` (optional).
  - submit "Update passwords"; show success/error message.
- **`admin/access_log.html`** — a table: Name · Role · Event · Time · IP · User-agent,
  iterating the rows.
- **`home.html`** — placeholder authed landing for users ("Chat coming in Phase 3").
  Add `GET /` in `auth.py` (or a tiny `routes/home.py`) that requires a user and
  renders this; if not authed, the 401 handler sends them to `/login`.

Templates receive `request` in context (required by Jinja2Templates):
`templates.TemplateResponse("login.html", {"request": request, "error": ...})`.

---

## `app/main.py` — rewrite

Wire everything. Concrete target:

```python
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import auth, admin
from app.core.limiter import limiter
from app.database import init_db

app = FastAPI(title="Lab Machine Assistant")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Rate limiting (N2, app-level)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Auth: turn 401 into a redirect to the login page for browser routes.
@app.exception_handler(StarletteHTTPException)
async def on_http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        return RedirectResponse("/login", status_code=303)
    return PlainTextResponse(exc.detail or "", status_code=exc.status_code)

@app.on_event("startup")
def _startup() -> None:
    init_db()  # dev convenience; Alembic is a Follow-up

app.include_router(auth.router)
app.include_router(admin.router)

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
```

(If `@app.on_event` is deprecated in the installed FastAPI, use a lifespan handler —
same `init_db()` call.)

---

## `scripts/seed.py` — implement

```python
from app.config import settings
from app.core.security import hash_password
from app.database import SessionLocal, init_db
from app.models.app_settings import AppSettings


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.get(AppSettings, 1):
            print("AppSettings already seeded — nothing to do.")
            return
        db.add(AppSettings(
            id=1,
            user_password_hash=hash_password(settings.initial_user_password),
            admin_password_hash=hash_password(settings.initial_admin_password),
        ))
        db.commit()
        print("Seeded user + admin passwords.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

---

## Tests — `tests/test_auth.py`

**Required (no DB, run anywhere):** session + password units.
- `make_session` → `read_session` round-trips the payload.
- `read_session` returns `None` for a tampered token.
- `read_session` returns `None` when `max_age` has passed (use `max_age=-1` or
  monkeypatch time).
- `hash_password`/`verify_password`: correct password verifies, wrong one doesn't.

**Optional integration (needs dev Postgres), mark with a skip if `DATABASE_URL`
unreachable** — use `fastapi.testclient.TestClient`:
- seed an `AppSettings` row, POST `/login` with the user password → 303 + a `session`
  cookie set; an `AccessLog` row exists.
- POST `/login` with a wrong password → 401, no cookie.
- GET `/admin/security` without a cookie → redirected to `/login`.
- GET `/admin/security` with a user (non-admin) cookie → 403.
- POST `/admin/security` with a correct current admin password changes the user
  password (verify by logging in with the new one).
- POST `/login` twice from the **same** `TestClient` (it persists the `device_id`
  cookie) → both succeed and resolve to the **same** `user.id`, with **two**
  `AccessLog` rows. A fresh `TestClient` → a different `user.id`.

---

## Acceptance criteria

- **R10-AC1/2:** user logs in with the shared password; admin logs in with the
  separate password; the two are independent.
- **R10-AC3:** from `/admin/security`, the admin can change the user password, the
  admin password, or both.
- **R10-AC4:** passwords are only ever stored bcrypt-hashed; the cookie is signed.
- **R5-AC1:** every login writes an `access_log` row (identity, time, IP, UA).
- **R5-AC2 (basic):** `/admin/access-log` lists those rows.
- **Identity continuity (A+F):** logging in twice from the same browser reuses one
  `User` row (same `user.id`, `last_seen_at` bumped), while each login still adds a
  distinct `access_log` row.
- **N2:** more than `login_rate_limit` POSTs to `/login` from one IP get HTTP 429.

---

## Manual smoke test

```bash
docker compose up -d db
uv pip install -r requirements.txt          # or pip
python scripts/seed.py                       # creates tables + password row
uvicorn app.main:app --reload
```
Then in a browser:
1. Visit `/admin/security` → you're bounced to `/login` (401→redirect works).
2. Log in with `INITIAL_USER_PASSWORD` + a name → lands on `/` (home placeholder).
3. Log in with `INITIAL_ADMIN_PASSWORD` → lands on `/admin`.
4. `/admin/access-log` shows both logins with names/role/IP/time.
5. Change the user password on `/admin/security`; log out; old user password fails,
   new one works.
6. Log in again as a user in the **same** browser → `/admin/access-log` gains a
   second row, but it maps to the **same** `User` (name/last-seen updated, no
   duplicate). A different browser/incognito → a new `User`.
7. Hammer `/login` >10×/min → 429.

---

## Build order checklist

- [ ] 1. Add config fields (`session_*`, `login_rate_limit`).
- [ ] 2. Models: `app_settings.py`, `user.py` (with `device_id` + `(device_id, role)`
       unique constraint), `access_log.py`; register in `__init__`.
- [ ] 3. `security.py`: drop stubs, add `make_session` / `read_session`.
- [ ] 4. `core/limiter.py`.
- [ ] 5. `dependencies.py` (`Principal`, `get_principal`, `require_user`, `require_admin`).
- [ ] 6. `app/templating.py` (`templates = Jinja2Templates(...)`).
- [ ] 7. Templates: `base`, `login`, `home`, `admin/dashboard`, `admin/security`, `admin/access_log`.
- [ ] 8. Routes: `auth.py` (login/logout + `GET /` home; login does device-id
       find-or-create and sets BOTH the `session` and `device_id` cookies), `admin.py`.
- [ ] 9. Rewrite `main.py` (routers, limiter, 401-redirect handler, startup init_db).
- [ ] 10. Implement `scripts/seed.py`.
- [ ] 11. `tests/test_auth.py` (required units; optional integration).
- [ ] 12. Run the manual smoke test; confirm all acceptance criteria.

---

## Follow-ups (create as Phase 1.5 / later, not now)

- Alembic migrations to replace `init_db()` for prod.
- CSRF tokens on the POST forms (cookie auth + `SameSite=Lax` covers the common case).
- Edge/WAF rate limiting (the other half of N2) — deploy config.
- Session revocation when a password changes (stateless cookies stay valid until
  expiry; document or move to server-side sessions if required).
- `X-Forwarded-For` handling so `ip` is correct behind a proxy/load balancer.
- Optional account-lockout / backoff after repeated failures.
