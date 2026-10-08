"""
tests/test_citation_excerpt.py
------------------------------
Tutor citations return a short excerpt, not the raw chunk (audit 2026-10-06 #1).

Retrieved chunks are ~400 characters of course text. Returned whole on every
tutor answer, they let a student page through the course material verbatim.
The API now returns at most MAX_CITATION_EXCERPT_CHARS per citation, cut on a
word boundary with an ellipsis, while the document name and page are kept and
the model still receives the full chunk.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import (
    get_db_session,
    get_gemini_pro,
    get_langfuse_client,
    get_retrieval_service,
)
from core.limits import MAX_CITATION_EXCERPT_CHARS
from core.security import hash_password
from db.models import Course, Enrollment, User
from main import app
from retrieval.models import Citation as RetrievedCitation
from tests.test_tutoring import (
    _make_mock_gemini_pro,
    _make_mock_langfuse,
    _make_mock_retrieval,
)
from tutoring.schemas import Citation, excerpt

ELLIPSIS = "…"

# ~430 characters of prose, like a real retrieved chunk.
LONG_CHUNK = (
    "Merge sort divides the array into two halves, recursively sorts each half, "
    "and then merges the two sorted halves into a single sorted array. The merge "
    "step walks both halves with two pointers, always taking the smaller head "
    "element, so it runs in linear time. Because the recursion depth is "
    "logarithmic, the total running time is O(n log n) in every case, and the "
    "algorithm is stable, which matters when sorting records by several keys."
)


# ── The excerpt function ──────────────────────────────────────────────────────

def test_short_text_is_returned_unchanged():
    assert excerpt("A short passage.") == "A short passage."


def test_text_exactly_at_the_cap_is_unchanged():
    text = "x" * MAX_CITATION_EXCERPT_CHARS
    assert excerpt(text) == text


def test_long_text_is_cut_on_a_word_boundary_with_an_ellipsis():
    out = excerpt(LONG_CHUNK)
    assert len(out) <= MAX_CITATION_EXCERPT_CHARS
    assert out.endswith(ELLIPSIS)
    body = out[: -len(ELLIPSIS)]
    assert LONG_CHUNK.startswith(body)
    # The cut lands between words, not inside one.
    assert LONG_CHUNK[len(body)] in " .,"


def test_a_single_unbroken_word_is_still_capped():
    out = excerpt("y" * 1000)
    assert len(out) == MAX_CITATION_EXCERPT_CHARS
    assert out.endswith(ELLIPSIS)


def test_citation_schema_keeps_document_and_page():
    c = Citation(chunk_text=LONG_CHUNK, source_file="sorting.pdf", page_or_slide=7, confidence=0.82)
    assert len(c.chunk_text) <= MAX_CITATION_EXCERPT_CHARS
    assert (c.source_file, c.page_or_slide, c.confidence) == ("sorting.pdf", 7, 0.82)


# ── Every tutor endpoint ──────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_db():
    async with _Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture(autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as db:
        teacher = User(email="teacher@demo.com", hashed_password=hash_password("password123"), role="teacher")
        student = User(email="student@demo.com", hashed_password=hash_password("password123"), role="student")
        db.add_all([teacher, student])
        await db.flush()
        course = Course(name="Algorithms", owner_id=teacher.id, status="ready")
        db.add(course)
        await db.flush()
        db.add(Enrollment(course_id=course.id, student_id=student.id))
        await db.commit()
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    retrieval = _make_mock_retrieval()
    # Two long chunks, as retrieval really returns them.
    retrieval.build_citation_bundle.return_value = [
        RetrievedCitation(chunk_text=LONG_CHUNK, source_file="sorting.pdf", page_or_slide=7, confidence=0.82),
        RetrievedCitation(chunk_text=LONG_CHUNK[::-1], source_file="notes.txt", page_or_slide=None, confidence=0.71),
    ]
    retrieval.build_context_window.return_value = f"[Source: sorting.pdf, p.7]\n{LONG_CHUNK}"
    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_retrieval_service] = lambda: retrieval
    gemini = _make_mock_gemini_pro()
    app.dependency_overrides[get_gemini_pro] = lambda: gemini
    app.dependency_overrides[get_langfuse_client] = lambda: _make_mock_langfuse()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.gemini = gemini
        yield ac
    app.dependency_overrides.clear()


async def _student_session(client) -> tuple[dict, str]:
    login = await client.post("/auth/login", json={"email": "student@demo.com", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    async with _Session() as db:
        course_id = (await db.execute(select(Course.id))).scalar_one()
    created = await client.post("/tutor/sessions", json={"course_id": str(course_id)}, headers=headers)
    return headers, created.json()["session_id"]


def _assert_capped(citations: list[dict]) -> None:
    assert citations, "expected citations to check"
    for c in citations:
        assert len(c["chunk_text"]) <= MAX_CITATION_EXCERPT_CHARS
        assert c["chunk_text"].endswith(ELLIPSIS)
        assert c["source_file"] in ("sorting.pdf", "notes.txt")
    assert LONG_CHUNK not in str(citations)


async def test_chat_citations_are_capped(client):
    headers, session_id = await _student_session(client)
    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat", json={"question": "How does merge sort work?"}, headers=headers
    )
    assert resp.status_code == 200
    _assert_capped(resp.json()["citations"])
    assert resp.json()["citations"][0]["page_or_slide"] == 7
    assert LONG_CHUNK not in resp.text


async def test_hint_citations_are_capped(client):
    headers, session_id = await _student_session(client)
    await client.post(
        f"/tutor/sessions/{session_id}/chat", json={"question": "How does merge sort work?"}, headers=headers
    )
    resp = await client.post(f"/tutor/sessions/{session_id}/hint", headers=headers)
    assert resp.status_code == 200
    _assert_capped(resp.json()["citations"])
    assert LONG_CHUNK not in resp.text


async def test_message_history_citations_are_capped(client):
    headers, session_id = await _student_session(client)
    await client.post(
        f"/tutor/sessions/{session_id}/chat", json={"question": "How does merge sort work?"}, headers=headers
    )
    resp = await client.get(f"/tutor/sessions/{session_id}/messages", headers=headers)
    assert resp.status_code == 200
    tutor_turns = [m for m in resp.json() if m["role"] == "tutor"]
    _assert_capped(tutor_turns[0]["citations"])
    assert LONG_CHUNK not in resp.text


async def test_the_model_still_receives_the_full_chunk(client):
    headers, session_id = await _student_session(client)
    await client.post(
        f"/tutor/sessions/{session_id}/chat", json={"question": "How does merge sort work?"}, headers=headers
    )
    # The prompt is built from the full context window, not from the excerpts.
    prompts = " ".join(str(call) for call in client.gemini.generate.call_args_list)
    assert LONG_CHUNK in prompts
