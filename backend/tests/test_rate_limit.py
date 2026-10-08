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
from starlette.requests import Request

from core.config import settings
from core.ratelimit import FixedWindowRateLimiter, RateLimitMiddleware, classify, client_key
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


# ── X-Forwarded-For is trusted only behind a configured proxy (audit #7b) ─────

def _rotating_xff_attempts(client, count: int) -> list[int]:
    """POST /auth/login ``count`` times, each claiming a new forwarded address."""
    return [
        client.post("/auth/login", headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code
        for i in range(count)
    ]


def test_forwarded_for_is_ignored_by_default(limiting_on, monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy", False)
    client = TestClient(_app(FixedWindowRateLimiter()))
    # Rotating the header used to give every request a fresh bucket; now all
    # five attempts count against the one real client address (limit 3).
    assert _rotating_xff_attempts(client, 5) == [200, 200, 200, 429, 429]


def test_client_key_uses_the_peer_address_by_default(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy", False)
    request = _request({"x-forwarded-for": "198.51.100.7"}, peer="10.0.0.5")
    assert client_key(request) == "ip:10.0.0.5"


def test_forwarded_for_is_honoured_behind_a_trusted_proxy(limiting_on, monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy", True)
    client = TestClient(_app(FixedWindowRateLimiter()))
    # Distinct real clients behind the proxy get their own buckets...
    assert _rotating_xff_attempts(client, 5) == [200] * 5
    # ...and one client is still limited.
    same = [client.post("/auth/login", headers={"X-Forwarded-For": "203.0.113.9"}).status_code for _ in range(4)]
    assert same == [200, 200, 200, 429]


def test_trusted_proxy_uses_the_entry_the_proxy_appended(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy", True)
    # The client sent a fake first hop; the proxy appended the real address.
    request = _request({"x-forwarded-for": "1.2.3.4, 198.51.100.7"}, peer="10.0.0.5")
    assert client_key(request) == "ip:198.51.100.7"


def test_trusted_proxy_without_the_header_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy", True)
    assert client_key(_request({}, peer="10.0.0.5")) == "ip:10.0.0.5"


def _request(headers: dict[str, str], peer: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 50000),
        }
    )
