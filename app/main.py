"""FastAPI application entrypoint."""
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import admin, auth, chat, documents
from app.config import settings
from app.core.limiter import limiter
from app.core.seed import ensure_app_settings
from app.database import SessionLocal, init_db

app = FastAPI(title="Lab Machine Assistant")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(StarletteHTTPException)
async def on_http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        return RedirectResponse("/login", status_code=303)
    return PlainTextResponse(exc.detail or str(exc.status_code), status_code=exc.status_code)


def _warm_models() -> None:
    """Preload the RAG models so the first query is fast. Runs in a background thread;
    a failure just leaves them to lazy-load on first use (non-fatal)."""
    from app.services import embeddings, reranker

    for name, fn in (("embeddings", embeddings.warmup), ("reranker", reranker.warmup)):
        started = time.monotonic()
        try:
            fn()
            print(f"[warmup] {name} ready in {time.monotonic() - started:.1f}s")
        except Exception as exc:  # noqa: BLE001 - warmup must never crash boot
            print(f"[warmup] {name} failed, will lazy-load on first use: {exc}")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Dev convenience: auto-seed the default passwords so a fresh DB is usable
    # immediately. In non-dev environments seeding stays an explicit, human step
    # (scripts/seed.py) so we never silently create default credentials in prod.
    if settings.app_env == "development":
        db = SessionLocal()
        try:
            if ensure_app_settings(db):
                print("[startup] seeded default user/admin passwords (dev).")
        finally:
            db.close()

    # Load models off the request path so the first user query doesn't pay for it.
    # Background thread keeps boot + /health instant; lru_cache serializes any query
    # that races the warmup, so the model is never loaded twice.
    if settings.warm_models_on_startup:
        threading.Thread(target=_warm_models, name="model-warmup", daemon=True).start()


app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(documents.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
