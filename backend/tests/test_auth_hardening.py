"""
tests/test_auth_hardening.py
----------------------------
Tests for password policy (F17) and constant-time login (F31).

Password policy is unit-tested at the schema level; constant-time login is
verified by asserting a bcrypt verify runs even for an unknown email, using a
fake DB session (no real database needed).
"""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import auth.service as auth_service
from auth.schemas import RegisterRequest
from auth.service import authenticate_user
from core.security import hash_password
from db.models import User


# ── F17: password policy ──────────────────────────────────────────────────────

def test_rejects_short_password():
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="short7!")  # 7 chars < 8


def test_rejects_password_over_72_bytes():
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="x" * 73)


def test_rejects_multibyte_password_over_72_bytes():
    # 40 two-byte chars = 80 bytes though only 40 characters.
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="é" * 40)


def test_accepts_valid_password():
    req = RegisterRequest(email="a@b.com", password="goodpassword")
    assert req.password == "goodpassword"


def test_accepts_password_at_72_byte_boundary():
    req = RegisterRequest(email="a@b.com", password="x" * 72)
    assert len(req.password) == 72


# ── F31: constant-time login ──────────────────────────────────────────────────

class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDB:
    def __init__(self, user):
        self._user = user

    async def execute(self, *_args, **_kwargs):
        return _Result(self._user)


@pytest.mark.asyncio
async def test_unknown_email_still_runs_a_verify(monkeypatch):
    spy = MagicMock(return_value=False)
    monkeypatch.setattr(auth_service, "verify_password", spy)

    result = await authenticate_user("nobody@x.com", "whatever", _FakeDB(None))

    assert result is None
    spy.assert_called_once()  # dummy verify ran — no timing oracle


@pytest.mark.asyncio
async def test_correct_password_authenticates():
    user = User(email="u@x.com", hashed_password=hash_password("secret123"), role="student")
    result = await authenticate_user("u@x.com", "secret123", _FakeDB(user))
    assert result is user


@pytest.mark.asyncio
async def test_wrong_password_rejected():
    user = User(email="u@x.com", hashed_password=hash_password("secret123"), role="student")
    result = await authenticate_user("u@x.com", "wrongpass", _FakeDB(user))
    assert result is None
