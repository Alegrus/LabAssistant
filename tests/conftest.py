"""Pytest bootstrap: redirect ALL tests to a separate test database.

This runs before any `app.*` import, so `app.config.settings` (and therefore
`app.database.engine`) bind to a dedicated `*_test` database instead of the dev
one. That's why the integration tests can freely DELETE/seed rows without ever
wiping your development data.

We create the test database if it doesn't exist by connecting to the `postgres`
maintenance database first. `init_db()` (called by the fixtures) then creates the
schema + pgvector extension inside it.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Base connection info, in precedence order: an explicit DATABASE_URL in the
# environment, then the one in .env (which is where the real dev/deploy value lives —
# pydantic reads it at app import, but this file runs before that and only sees
# os.environ), then the local-Docker default.
_DEFAULT = "postgresql+psycopg://postgres:postgres@localhost:5432/labassistant"


def _from_dotenv() -> str | None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


_base_url = make_url(os.environ.get("DATABASE_URL") or _from_dotenv() or _DEFAULT)

# Derive a sibling test database name (labassistant -> labassistant_test).
_test_db = (_base_url.database or "labassistant") + "_test"
_test_url = _base_url.set(database=_test_db)

# Create the test database if it's missing (must connect to a different DB to do so).
_admin = create_engine(
    _base_url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True
)
with _admin.connect() as conn:
    exists = conn.execute(
        text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": _test_db}
    ).scalar()
    if not exists:
        conn.execute(text(f'CREATE DATABASE "{_test_db}"'))
_admin.dispose()

# Point the whole app at the test DB BEFORE app.config is ever imported.
os.environ["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
