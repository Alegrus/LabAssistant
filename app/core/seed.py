"""Idempotent, race-safe seeding of the singleton AppSettings row (R10).

Shared by `scripts/seed.py` (explicit, any environment) and the app startup hook
(development only — see app/main.py). Never overwrites an existing row, so it is
safe to call on every boot.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_password
from app.models.app_settings import AppSettings


def ensure_app_settings(db: Session) -> bool:
    """Insert AppSettings(id=1) with the initial passwords if it's missing.

    Returns True if it created the row, False if it already existed. Race-safe:
    if another worker inserts it first, the IntegrityError is swallowed.
    """
    if db.get(AppSettings, 1) is not None:
        return False
    db.add(
        AppSettings(
            id=1,
            user_password_hash=hash_password(settings.initial_user_password),
            admin_password_hash=hash_password(settings.initial_admin_password),
        )
    )
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()  # a concurrent worker seeded it first — that's fine
        return False
