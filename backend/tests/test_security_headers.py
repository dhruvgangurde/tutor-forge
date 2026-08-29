"""
tests/test_security_headers.py
------------------------------
Tests for the security-headers middleware (F18). Exercised on an isolated app so
no auth/DB is involved.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.security_headers import SecurityHeadersMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/x")
    async def x():
        return {"ok": True}

    return TestClient(app)


@pytest.fixture(autouse=True)
def enable_headers(monkeypatch):
    monkeypatch.setattr(settings, "security_headers_enabled", True)


def test_static_headers_present():
    resp = _client().get("/x")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


def test_hsts_only_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    assert "Strict-Transport-Security" not in _client().get("/x").headers

    monkeypatch.setattr(settings, "environment", "production")
    resp = _client().get("/x")
    assert "max-age=" in resp.headers["Strict-Transport-Security"]


def test_disabled_flag_adds_no_headers(monkeypatch):
    monkeypatch.setattr(settings, "security_headers_enabled", False)
    resp = _client().get("/x")
    assert "X-Content-Type-Options" not in resp.headers
