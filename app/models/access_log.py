"""AccessLog — one row per login event (R5)."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AccessLog(Base):
    __tablename__ = "access_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)  # "login" | "query"
    # Snapshot of the name used AT THIS login. The shared User.display_name is
    # overwritten on later logins (A+F identity), so the audit trail must record
    # the name-of-the-moment here, exactly like ip/user_agent below.
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Performance signal for "query" events (NULL for "login"):
    #   outcome    = "answered" | "not_found" | "error"
    #   latency_ms = wall-clock time the RAG turn took (retrieval + LLM)
    outcome: Mapped[str | None] = mapped_column(String(16))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Human-readable reason for an "error" outcome (e.g. "HTTP 429: rate-limited");
    # NULL for successful turns and logins.
    detail: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
