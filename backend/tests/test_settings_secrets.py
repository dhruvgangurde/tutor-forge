"""
tests/test_settings_secrets.py
------------------------------
Settings held secrets in plain fields that its repr showed, so anything that
formats the settings object printed them: a monkeypatch of a missing setting
put the database password and every API key into a test failure, and an
f-string with !r, a log line or a debugger would do the same.
"""

import pytest

from core.config import Settings

_SECRETS = {
    "database_url": "postgresql+asyncpg://user:db-pass-123@host/db",
    "gemini_api_key": "gemini-key-123",
    "langfuse_secret_key": "langfuse-secret-123",
    "jwt_secret_key": "jwt-secret-123",
}


def _shown(text: str) -> list[str]:
    return [v for v in ("db-pass-123", "gemini-key-123", "langfuse-secret-123", "jwt-secret-123") if v in text]


def test_settings_repr_never_shows_a_secret():
    s = Settings(_env_file=None, **_SECRETS)
    assert _shown(repr(s) + str(s)) == []


def test_patching_a_missing_setting_never_shows_a_secret(monkeypatch):
    # The path that leaked: monkeypatch's AttributeError formats repr(target).
    s = Settings(_env_file=None, **_SECRETS)
    with pytest.raises(AttributeError) as exc:
        monkeypatch.setattr(s, "no_such_setting", 1)
    assert _shown(str(exc.value)) == []
