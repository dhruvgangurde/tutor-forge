"""
tests/test_health.py
--------------------
Tests for the liveness/readiness probes (audit finding F9).

Liveness is exercised through the app; readiness logic is unit-tested via
check_readiness() so no real Postgres/Chroma is required.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from health.router import check_readiness
from main import app


class _FakeResult:
    pass


class _FakeDB:
    """Minimal async DB stand-in: execute() succeeds, or raises if fail=True."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def execute(self, *_args, **_kwargs):
        if self._fail:
            raise RuntimeError("db unreachable")
        return _FakeResult()


def test_health_liveness_ok():
    # Plain TestClient (no context manager) so the app lifespan does not run —
    # liveness must be dependency-free.
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "tutorforge"


@pytest.mark.asyncio
async def test_readiness_all_ok():
    chroma = MagicMock()
    ok, checks = await check_readiness(_FakeDB(), chroma)
    assert ok is True
    assert checks == {"database": "ok", "chroma": "ok"}
    chroma.heartbeat.assert_called_once()


@pytest.mark.asyncio
async def test_readiness_database_down():
    ok, checks = await check_readiness(_FakeDB(fail=True), MagicMock())
    assert ok is False
    assert checks["database"] == "error"
    assert checks["chroma"] == "ok"


@pytest.mark.asyncio
async def test_readiness_chroma_down():
    chroma = MagicMock()
    chroma.heartbeat.side_effect = RuntimeError("chroma down")
    ok, checks = await check_readiness(_FakeDB(), chroma)
    assert ok is False
    assert checks["chroma"] == "error"


@pytest.mark.asyncio
async def test_readiness_chroma_unavailable():
    ok, checks = await check_readiness(_FakeDB(), None)
    assert ok is False
    assert checks["chroma"] == "unavailable"
