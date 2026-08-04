"""Auth unit tests (no DB required) and optional integration tests.

Unit tests run anywhere. Integration tests need a reachable DATABASE_URL and are
skipped automatically when Postgres is unavailable.
"""
import time

import pytest

from app.core.security import hash_password, make_session, read_session, verify_password


# ---------------------------------------------------------------------------
# Unit tests — no DB, no network
# ---------------------------------------------------------------------------

def test_make_read_session_round_trip():
    payload = {"uid": "abc123", "role": "user", "name": "Alex"}
    token = make_session(payload)
    result = read_session(token, max_age=60)
    assert result == payload


def test_read_session_tampered_token_returns_none():
    token = make_session({"uid": "x", "role": "user", "name": "X"})
    tampered = token[:-4] + "xxxx"
    assert read_session(tampered, max_age=60) is None


def test_read_session_expired_returns_none():
    token = make_session({"uid": "x", "role": "user", "name": "X"})
    # max_age=-1 means "expired 1 second ago" — always fails
    assert read_session(token, max_age=-1) is None


def test_hash_and_verify_correct_password():
    h = hash_password("correct-horse")
    assert verify_password("correct-horse", h) is True


def test_verify_wrong_password_returns_false():
    h = hash_password("correct-horse")
    assert verify_password("wrong-battery", h) is False


# ---------------------------------------------------------------------------
# Integration tests — require Postgres; skipped if DATABASE_URL unreachable
# ---------------------------------------------------------------------------

def _postgres_available() -> bool:
    try:
        from app.database import engine
        with engine.connect():
            return True
    except Exception:
        return False


skip_no_db = pytest.mark.skipif(
    not _postgres_available(), reason="Postgres not reachable"
)


@pytest.fixture
def seeded_client():
    """TestClient with a seeded AppSettings row and clean tables.

    Function-scoped so each test gets an isolated DB: tests that mutate the
    password (e.g. change_user_password) can't leak state into later tests.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.core.limiter import limiter
    from app.database import SessionLocal, engine, init_db
    from app.main import app
    from app.models.access_log import AccessLog
    from app.models.app_settings import AppSettings
    from app.models.user import User

    # The login rate limit is per-IP and its state persists across tests (same
    # test-client IP), so a full suite run would spuriously 429. Disable it here;
    # rate limiting isn't what these tests exercise.
    limiter.enabled = False

    init_db()
    db = SessionLocal()
    # Clean slate for these tests
    db.execute(text("DELETE FROM access_log"))
    db.execute(text("DELETE FROM users"))
    db.execute(text("DELETE FROM app_settings"))
    db.commit()

    db.add(
        AppSettings(
            id=1,
            user_password_hash=hash_password("test-user-pw"),
            admin_password_hash=hash_password("test-admin-pw"),
        )
    )
    db.commit()
    db.close()

    client = TestClient(app, raise_server_exceptions=True)
    yield client, SessionLocal

    # Teardown
    db2 = SessionLocal()
    db2.execute(text("DELETE FROM access_log"))
    db2.execute(text("DELETE FROM users"))
    db2.execute(text("DELETE FROM app_settings"))
    db2.commit()
    db2.close()


@skip_no_db
def test_login_user_sets_session_cookie_and_logs(seeded_client):
    client, SessionLocal = seeded_client
    resp = client.post("/login", data={"password": "test-user-pw", "name": "Alice"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "session" in resp.cookies

    db = SessionLocal()
    from app.models.access_log import AccessLog
    logs = db.query(AccessLog).all()
    assert len(logs) >= 1
    db.close()


@skip_no_db
def test_login_wrong_password_no_cookie(seeded_client):
    client, _ = seeded_client
    resp = client.post("/login", data={"password": "wrong", "name": "X"}, follow_redirects=False)
    assert resp.status_code == 401
    assert "session" not in resp.cookies


@skip_no_db
def test_admin_security_unauthenticated_redirects_to_login(seeded_client):
    client, _ = seeded_client
    # Fresh client with no cookies
    from fastapi.testclient import TestClient
    from app.main import app
    bare = TestClient(app, raise_server_exceptions=True)
    resp = bare.get("/admin/security", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@skip_no_db
def test_admin_security_user_role_forbidden(seeded_client):
    client, _ = seeded_client
    # Log in as user first
    from fastapi.testclient import TestClient
    from app.main import app
    user_client = TestClient(app, raise_server_exceptions=True)
    user_client.post("/login", data={"password": "test-user-pw", "name": "Bob"})
    resp = user_client.get("/admin/security", follow_redirects=False)
    assert resp.status_code == 403


@skip_no_db
def test_change_user_password(seeded_client):
    client, SessionLocal = seeded_client
    # Log in as admin
    from fastapi.testclient import TestClient
    from app.main import app
    admin_client = TestClient(app, raise_server_exceptions=True)
    admin_client.post("/login", data={"password": "test-admin-pw", "name": ""})

    resp = admin_client.post(
        "/admin/security",
        data={
            "current_admin_password": "test-admin-pw",
            "new_user_password": "new-user-pw",
            "new_admin_password": "",
        },
    )
    assert resp.status_code == 200

    # Old password should now fail
    from fastapi.testclient import TestClient as TC
    c2 = TC(app, raise_server_exceptions=True)
    r = c2.post("/login", data={"password": "test-user-pw", "name": "X"}, follow_redirects=False)
    assert r.status_code == 401

    # New password should work
    c3 = TC(app, raise_server_exceptions=True)
    r2 = c3.post("/login", data={"password": "new-user-pw", "name": "X"}, follow_redirects=False)
    assert r2.status_code == 303


@skip_no_db
def test_device_id_continuity(seeded_client):
    """Two logins from same TestClient (same device_id cookie) → one User, two AccessLog rows."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.access_log import AccessLog
    from app.models.user import User

    client = TestClient(app, raise_server_exceptions=True)
    client.post("/login", data={"password": "test-user-pw", "name": "Carol"}, follow_redirects=False)
    client.post("/login", data={"password": "test-user-pw", "name": "Carol"}, follow_redirects=False)

    db = seeded_client[1]()
    users = db.query(User).filter(User.role == "user").all()
    # All logins from this client share one device_id → should collapse to one User
    device_ids = {u.device_id for u in users}
    # At least one device maps to this client; Carol's device_id should appear once
    carol_users = [u for u in users if u.display_name == "Carol"]
    assert len(carol_users) == 1

    logs = db.query(AccessLog).filter(AccessLog.user_id == carol_users[0].id).all()
    assert len(logs) >= 2
    db.close()


@skip_no_db
def test_fresh_client_gets_new_user(seeded_client):
    """A fresh TestClient (no device_id cookie) creates a separate User."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.user import User

    c1 = TestClient(app, raise_server_exceptions=True)
    c2 = TestClient(app, raise_server_exceptions=True)
    c1.post("/login", data={"password": "test-user-pw", "name": "Dave"}, follow_redirects=False)
    c2.post("/login", data={"password": "test-user-pw", "name": "Dave"}, follow_redirects=False)

    db = seeded_client[1]()
    dave_users = db.query(User).filter(User.display_name == "Dave").all()
    # Two different clients → two different device_ids → two User rows
    assert len(dave_users) >= 2
    assert len({u.device_id for u in dave_users}) >= 2
    db.close()


@skip_no_db
def test_access_log_snapshots_name_per_login(seeded_client):
    """Same device, two different names → each AccessLog row keeps its own name.

    Regression: previously the log read the (shared, mutable) User.display_name, so
    the earlier login's name was retroactively changed to the latest one.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.access_log import AccessLog

    c = TestClient(app, raise_server_exceptions=True)  # one client = one device_id
    c.post("/login", data={"password": "test-user-pw", "name": "Alice"}, follow_redirects=False)
    c.post("/login", data={"password": "test-user-pw", "name": "Bob"}, follow_redirects=False)

    db = seeded_client[1]()
    names = [row.display_name for row in db.query(AccessLog).order_by(AccessLog.id).all()]
    db.close()
    # Both logins collapse to one User, but the log preserves each name-of-the-moment.
    assert names == ["Alice", "Bob"]
