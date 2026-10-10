"""
tests/test_ai_busy.py
---------------------
How "the AI service is busy" reaches people: a 503 with fixed wording for API
callers, and a plain failure reason for a course whose ingestion hit it. The
provider's own text (quota names, retry delays) never reaches either.
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.exceptions import AI_BUSY_MESSAGE, AIServiceBusy, register_exception_handlers
from courses.failure import failure_reason

# Taken at import, before conftest's autouse stub_background_jobs replaces it
# for every test. This test fakes the database session, so the real
# orchestrator never opens a Postgres connection.
from courses.service import _run_ingestion as _real_run_ingestion

_PROVIDER_TEXT = (
    "Gemini Pro generate (gemini-flash-lite-latest): rate limited; the server asked to wait 20s"
)


@pytest.mark.asyncio
async def test_busy_error_is_a_503_with_plain_wording():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/busy")
    async def busy():
        raise AIServiceBusy(_PROVIDER_TEXT)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/busy")

    assert resp.status_code == 503
    assert resp.json() == {"error": "ai_busy", "detail": AI_BUSY_MESSAGE}
    assert "gemini" not in resp.text.lower()
    assert "20s" not in resp.text


def test_busy_error_text_is_never_the_providers():
    exc = AIServiceBusy(_PROVIDER_TEXT)
    assert str(exc) == AI_BUSY_MESSAGE
    assert exc.reason == _PROVIDER_TEXT


# ── Ingestion ─────────────────────────────────────────────────────────────────


def test_busy_ingestion_error_is_stored_with_its_prefix():
    from courses.service import _ingestion_error_message

    assert _ingestion_error_message(AIServiceBusy(_PROVIDER_TEXT)) == f"AI service busy: {_PROVIDER_TEXT}"
    assert _ingestion_error_message(RuntimeError("disk full")) == "disk full"


def test_busy_ingestion_reason_is_the_plain_sentence():
    assert failure_reason(f"AI service busy: {_PROVIDER_TEXT}") == AI_BUSY_MESSAGE


@pytest.mark.asyncio
async def test_ingestion_that_hits_a_busy_provider_is_marked_failed_with_the_busy_prefix(monkeypatch):
    import agents.ingestion.graph as ingestion_graph
    import core.database
    import courses.service as course_service

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.commit = AsyncMock()

    @asynccontextmanager
    async def fake_session():
        yield db

    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=AIServiceBusy(_PROVIDER_TEXT))
    marked = AsyncMock()
    monkeypatch.setattr(core.database, "AsyncSessionFactory", fake_session)
    monkeypatch.setattr(ingestion_graph, "build_ingestion_graph", lambda **kwargs: graph)
    monkeypatch.setattr(course_service, "_mark_course_and_job_failed", marked)

    await _real_run_ingestion(
        course_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        files=[],
        retrieval_service=MagicMock(),
        gemini_pro=MagicMock(),
    )

    stored = marked.await_args.kwargs["error_message"]
    assert stored.startswith("AI service busy: ")
    assert failure_reason(stored) == AI_BUSY_MESSAGE
