"""
tests/test_rate_limit.py
------------------------
Tests for rate limiting (F5). The limiter and middleware are exercised in
isolation on a tiny app (real routes at the classified paths), so no real
auth/DB is involved and the main suite stays unaffected. Rate limiting is
re-enabled locally here (the session fixture disables it globally).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.ratelimit import FixedWindowRateLimiter, RateLimitMiddleware, classify
from core.security import create_access_token


# ── Unit: FixedWindowRateLimiter ──────────────────────────────────────────────

def test_limiter_allows_up_to_limit_then_denies():
    clock = {"t": 100.0}
    limiter = FixedWindowRateLimiter(clock=lambda: clock["t"])
    results = [limiter.check("k", limit=3, window=60) for _ in range(4)]
    assert [allowed for allowed, _ in results] == [True, True, True, False]
    _, retry_after = results[-1]
    assert retry_after > 0


def test_limiter_resets_after_window():
    clock = {"t": 0.0}
    limiter = FixedWindowRateLimiter(clock=lambda: clock["t"])
    assert limiter.check("k", 1, 60)[0] is True
    assert limiter.check("k", 1, 60)[0] is False   # limit hit
    clock["t"] = 61.0                                # window elapsed
    assert limiter.check("k", 1, 60)[0] is True     # fresh window


def test_limiter_keys_are_independent():
    limiter = FixedWindowRateLimiter(clock=lambda: 0.0)
    assert limiter.check("a", 1, 60)[0] is True
    assert limiter.check("a", 1, 60)[0] is False
    assert limiter.check("b", 1, 60)[0] is True     # separate bucket


# ── Classification ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,method,expected", [
    ("/auth/login", "POST", "auth"),
    ("/auth/register", "POST", "auth"),
    ("/auth/login", "GET", None),
    ("/assessments/generate", "POST", "ai"),
    ("/courses/upload", "POST", "ai"),
    ("/grading/abc/grade", "POST", "ai"),
    ("/tutor/sessions/abc/chat", "POST", "ai"),
    ("/tutor/sessions/abc/hint", "POST", "ai"),
    ("/assessments/course/abc", "GET", None),
    ("/health", "GET", None),
])
def test_classify(path, method, expected):
    assert classify(path, method) == expected


# ── Middleware integration (isolated app) ─────────────────────────────────────

def _app(limiter: FixedWindowRateLimiter) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.post("/auth/login")
    async def login():
        return {"ok": True}

    @app.post("/assessments/generate")
    async def generate():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


@pytest.fixture
def limiting_on(monkeypatch):
    """Enable limiting with small, deterministic limits for this test."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_max", 3)
    monkeypatch.setattr(settings, "rate_limit_auth_window_seconds", 60)
    monkeypatch.setattr(settings, "rate_limit_ai_max", 2)
    monkeypatch.setattr(settings, "rate_limit_ai_window_seconds", 60)


def test_auth_endpoint_blocks_after_limit(limiting_on):
    client = TestClient(_app(FixedWindowRateLimiter()))
    for _ in range(3):
        assert client.post("/auth/login").status_code == 200
    blocked = client.post("/auth/login")
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) > 0


def test_unlimited_paths_are_never_blocked(limiting_on):
    client = TestClient(_app(FixedWindowRateLimiter()))
    for _ in range(10):
        assert client.get("/health").status_code == 200  # not a limited class


def test_ai_endpoint_is_user_aware(limiting_on):
    client = TestClient(_app(FixedWindowRateLimiter()))
    token_a = create_access_token({"sub": "user-a", "email": "a@x.com", "role": "teacher"})
    token_b = create_access_token({"sub": "user-b", "email": "b@x.com", "role": "teacher"})
    ha = {"Authorization": f"Bearer {token_a}"}
    hb = {"Authorization": f"Bearer {token_b}"}

    # user A exhausts their AI budget (limit 2)
    assert client.post("/assessments/generate", headers=ha).status_code == 200
    assert client.post("/assessments/generate", headers=ha).status_code == 200
    assert client.post("/assessments/generate", headers=ha).status_code == 429
    # user B is unaffected — independent per-user bucket
    assert client.post("/assessments/generate", headers=hb).status_code == 200


def test_disabled_flag_bypasses_limiter(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_auth_max", 1)
    client = TestClient(_app(FixedWindowRateLimiter()))
    for _ in range(5):
        assert client.post("/auth/login").status_code == 200  # never limited
