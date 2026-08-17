"""The startup guard that refuses a default SECRET_KEY outside development.

SECRET_KEY signs the session cookie carrying the authenticated role, so running with
the shipped placeholder would let anyone mint an admin session. These tests need no
database or network: the guard runs before any other startup work.
"""
import pytest

from app.config import settings
from app.main import _DEFAULT_SECRET_KEY, _startup


@pytest.fixture
def restore_settings():
    saved = (settings.app_env, settings.secret_key)
    yield
    settings.app_env, settings.secret_key = saved


@pytest.mark.parametrize("env", ["production", "staging"])
def test_refuses_default_secret_outside_development(restore_settings, env):
    settings.app_env = env
    settings.secret_key = _DEFAULT_SECRET_KEY

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _startup()


def test_development_may_use_the_default_secret(restore_settings):
    """Local dev must stay zero-config; the guard only applies to real deployments."""
    settings.app_env = "development"
    settings.secret_key = _DEFAULT_SECRET_KEY

    # Reaching the database means the guard let it through (that call is what fails
    # here, not the guard). Any non-RuntimeError outcome is a pass.
    try:
        _startup()
    except RuntimeError as exc:  # pragma: no cover - only on regression
        pytest.fail(f"guard wrongly blocked development startup: {exc}")
    except Exception:
        pass  # DB/network unavailable in this environment - irrelevant to the guard


def test_configured_secret_passes_the_guard(restore_settings):
    settings.app_env = "production"
    settings.secret_key = "a-properly-random-secret-value"

    try:
        _startup()
    except RuntimeError as exc:  # pragma: no cover - only on regression
        pytest.fail(f"guard blocked a configured secret: {exc}")
    except Exception:
        pass  # as above: anything past the guard is out of scope here
