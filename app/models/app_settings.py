"""AppSettings — singleton row (id=1) holding the two hashed passwords (R10)."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    admin_password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


def get_app_settings(db) -> "AppSettings":
    """Fetch the singleton settings row (id=1). Assumes seed.py has run."""
    return db.get(AppSettings, 1)
