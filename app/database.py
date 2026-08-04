"""SQLAlchemy engine/session and DB initialization.

Postgres + pgvector. `init_db()` enables the `vector` extension and creates
tables/indexes. In production prefer Alembic migrations; `init_db()` is convenient
for local dev and tests.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session, future=True
)

Base = declarative_base()


def init_db() -> None:
    """Enable pgvector and create all tables + indexes."""
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, always closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
