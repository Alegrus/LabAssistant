"""Seed initial config: hashed user + admin passwords (R10).

Run once after first deploy. Rotate both passwords via the admin UI afterwards.
Reads INITIAL_USER_PASSWORD / INITIAL_ADMIN_PASSWORD from the environment.
"""
from app.core.seed import ensure_app_settings
from app.database import SessionLocal, init_db


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if ensure_app_settings(db):
            print("Seeded user + admin passwords.")
        else:
            print("AppSettings already seeded — nothing to do.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
