"""Message model — one turn in a chat. Assistant turns carry citations (R9).

`citations` stores the resolved citation list as JSON so the transcript can be
re-rendered without re-running RAG. `not_found` flags the grounded fallback (N5).
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    not_found: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Phase 4 (vision): a user message may carry a photo. `image_observations` is the
    # vision model's question-conditioned reading of it; `pending` marks a turn that is
    # awaiting the user's confirmation of that reading before we answer.
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    image_observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat = relationship("Chat", back_populates="messages")
