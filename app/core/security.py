"""Auth primitives: password hashing and stateless session cookies.

Two-password model (REQUIREMENTS R10): a shared *user* password and a separate
*admin* password, both hashed in the `app_settings` table and both changeable by
the admin. Sessions are signed with itsdangerous and carried in an HttpOnly cookie.
"""
import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="session")


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def make_session(payload: dict) -> str:
    """Sign a session payload into an opaque cookie string."""
    return _serializer.dumps(payload)


def read_session(token: str, max_age: int) -> dict | None:
    """Verify + decode a session cookie. Returns None if invalid or expired."""
    try:
        return _serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
