"""
tests/test_tutoring.py
-----------------------
Integration and unit tests for the tutoring endpoints and service.

Strategy:
  - Uses SQLite in-memory DB (same pattern as test_auth.py).
  - Gemini Pro and RetrievalService are mocked — no real API calls.
  - Langfuse is mocked to prevent cross-test app.state pollution.
  - Graph invocation is real (but against mocked services).
  - DB fixtures reset between tests via autouse setup_db.
"""

import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import (
    get_db_session,
    get_gemini_pro,
    get_langfuse_client,
    get_retrieval_service,
)
from core.security import hash_password
from db.models import Course, Enrollment, TutoringSession, User
from main import app

# ── Test database ─────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

_test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_TestSessionFactory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def override_get_db_session():
    async with _TestSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Mock services ─────────────────────────────────────────────────────────────


def _make_mock_retrieval(grounded: bool = True, empty: bool = False):
    """Create a mock RetrievalService."""
    from retrieval.models import Chunk, Citation, RetrievalResult

    svc = MagicMock()
    if empty:
        result = RetrievalResult(query="test")
    else:
        chunk = Chunk(
            text="The Socratic method uses questions to develop critical thinking.",
            source_file="philosophy_101.pdf",
            page_or_slide=3,
            course_id=uuid.UUID(int=0),
        )
        result = RetrievalResult(
            query="test",
            chunks=[chunk],
            confidence_scores=[0.9 if grounded else 0.3],
        )
    svc.retrieve.return_value = result
    svc.is_grounded.return_value = grounded
    svc.build_context_window.return_value = (
        "[Source: philosophy_101.pdf, p.3]\n"
        "The Socratic method uses questions to develop critical thinking."
    )
    # Return real Citation dataclass instances — matching the production contract
    # of RetrievalService.build_citation_bundle(). A MagicMock here broke
    # dataclasses.asdict() in generate_guiding_question_node (asdict requires a
    # real dataclass instance).
    svc.build_citation_bundle.return_value = [
        Citation(
            chunk_text="The Socratic method uses questions to develop critical thinking.",
            source_file="philosophy_101.pdf",
            page_or_slide=3,
            confidence=0.9 if grounded else 0.3,
        )
    ]
    return svc


def _make_mock_gemini_pro():
    """Create a mock GeminiProClient."""
    svc = MagicMock()
    svc.generate.return_value = "What do you think happens when someone questions their assumptions?"
    return svc


def _make_mock_langfuse():
    """
    Create a mock Langfuse client.

    ``spec=Langfuse`` is load-bearing: an unspecced MagicMock answers *any*
    attribute, so this mock used to satisfy the v2 ``langfuse.trace()`` call
    long after langfuse v4 removed that method — every tutor test passed while
    the endpoint 500'd in production. With the spec, calling a method the
    installed SDK does not have raises AttributeError here too.
    """
    from langfuse import Langfuse

    svc = MagicMock(spec=Langfuse)
    span = MagicMock()
    span.trace_id = uuid.uuid4().hex
    span.id = uuid.uuid4().hex[:16]
    svc.start_observation.return_value = span
    return svc


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables and seed users + course before each test."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _TestSessionFactory() as db:
        teacher = User(
            email="teacher@demo.com",
            hashed_password=hash_password("password123"),
            role="teacher",
        )
        student = User(
            email="student@demo.com",
            hashed_password=hash_password("password123"),
            role="student",
        )
        db.add_all([teacher, student])
        await db.flush()

        course = Course(
            owner_id=teacher.id,
            name="Philosophy 101",
            status="ready",
        )
        db.add(course)
        await db.flush()
        # Course access requires an enrollment; the seeded student is in this class.
        db.add(Enrollment(course_id=course.id, student_id=student.id))
        await db.commit()

    yield

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def student_token(client):
    resp = await client.post("/auth/login", json={
        "email": "student@demo.com",
        "password": "password123",
    })
    return resp.json()["access_token"]


@pytest.fixture
async def teacher_token(client):
    resp = await client.post("/auth/login", json={
        "email": "teacher@demo.com",
        "password": "password123",
    })
    return resp.json()["access_token"]


@pytest.fixture
async def ready_course_id():
    async with _TestSessionFactory() as db:
        from sqlalchemy import select
        result = await db.execute(select(Course).where(Course.name == "Philosophy 101"))
        course = result.scalar_one()
        return course.id


# ── Session creation tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session_success(client, student_token, ready_course_id):
    """Successfully create a tutoring session."""
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "session_id" in data
    assert data["course_id"] == str(ready_course_id)


@pytest.mark.asyncio
async def test_create_session_nonexistent_course(client, student_token):
    """Attempt to create session on non-existent course."""
    fake_course_id = uuid.uuid4()
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(fake_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    # 404, not the old 400: enrollment is checked first, and a course id that
    # does not exist answers exactly like one the student is not enrolled in.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_session_not_ready_course(client, student_token):
    """Attempt to create session on not-ready course."""
    async with _TestSessionFactory() as db:
        from sqlalchemy import select
        result = await db.execute(select(Course).where(Course.name == "Philosophy 101"))
        course = result.scalar_one()
        course.status = "ingesting"
        await db.commit()

    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(course.id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_session_requires_student_role(client, teacher_token, ready_course_id):
    """Teacher role should not be able to create tutoring session."""
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_session_requires_auth(client, ready_course_id):
    """Unauthenticated request should return 401."""
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
    )
    assert resp.status_code == 401


# ── Chat tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_grounded_success(client, student_token, ready_course_id):
    """Send a grounded question and get a Socratic response."""
    # Create session
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    # Mock services
    mock_retrieval = _make_mock_retrieval(grounded=True)
    mock_gemini = _make_mock_gemini_pro()
    mock_langfuse = _make_mock_langfuse()
    app.dependency_overrides[get_retrieval_service] = lambda: mock_retrieval
    app.dependency_overrides[get_gemini_pro] = lambda: mock_gemini
    app.dependency_overrides[get_langfuse_client] = lambda: mock_langfuse

    # Send chat
    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "What is the Socratic method?"},
        headers={"Authorization": f"Bearer {student_token}"},
    )

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)

    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert data["is_grounded"] is True
    assert len(data["citations"]) > 0
    assert data["hint_level"] == 0


@pytest.mark.asyncio
async def test_chat_ungrounded_refusal(client, student_token, ready_course_id):
    """Send an ungrounded question and get a refusal."""
    # Create session
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    # Mock with ungrounded retrieval
    mock_retrieval = _make_mock_retrieval(grounded=False)
    mock_gemini = _make_mock_gemini_pro()
    mock_langfuse = _make_mock_langfuse()
    app.dependency_overrides[get_retrieval_service] = lambda: mock_retrieval
    app.dependency_overrides[get_gemini_pro] = lambda: mock_gemini
    app.dependency_overrides[get_langfuse_client] = lambda: mock_langfuse

    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "Tell me about pizza"},
        headers={"Authorization": f"Bearer {student_token}"},
    )

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_grounded"] is False
    assert len(data["citations"]) == 0
    assert "I can only help with topics covered in this course" in data["response"]


@pytest.mark.asyncio
async def test_chat_wrong_session_owner(client, student_token, ready_course_id):
    """Student B should not access student A's session."""
    # Create session as student A
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    # Create another student
    async with _TestSessionFactory() as db:
        other_student = User(
            email="other@demo.com",
            hashed_password=hash_password("password123"),
            role="student",
        )
        db.add(other_student)
        await db.commit()

    # Login as student B
    resp = await client.post("/auth/login", json={
        "email": "other@demo.com",
        "password": "password123",
    })
    other_token = resp.json()["access_token"]

    # Try to chat on student A's session: 404, the same as a session that
    # does not exist (audit #4c), so the id's existence is not revealed.
    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "What is philosophy?"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Tutoring session not found."

    # ...on every session route, identical to a random id.
    other = {"Authorization": f"Bearer {other_token}"}
    for method, suffix, body in (
        ("GET", "messages", None),
        ("POST", "chat", {"question": "What is philosophy?"}),
        ("POST", "hint", None),
    ):
        theirs = await client.request(method, f"/tutor/sessions/{session_id}/{suffix}", json=body, headers=other)
        missing = await client.request(method, f"/tutor/sessions/{uuid.uuid4()}/{suffix}", json=body, headers=other)
        assert theirs.status_code == missing.status_code == 404, suffix
        assert theirs.json() == missing.json(), suffix


# ── Hint tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hint_escalation(client, student_token, ready_course_id):
    """Request hints and verify hint_level escalates across all five stages."""
    # Create session
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    # Mock services
    mock_retrieval = _make_mock_retrieval(grounded=True)
    mock_gemini = _make_mock_gemini_pro()
    mock_langfuse = _make_mock_langfuse()
    app.dependency_overrides[get_retrieval_service] = lambda: mock_retrieval
    app.dependency_overrides[get_gemini_pro] = lambda: mock_gemini
    app.dependency_overrides[get_langfuse_client] = lambda: mock_langfuse

    # Send initial question
    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "What is critical thinking?"},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.json()["hint_level"] == 0

    # Request hint 1
    resp = await client.post(
        f"/tutor/sessions/{session_id}/hint",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["hint_level"] == 1

    # Request hint 2
    resp = await client.post(
        f"/tutor/sessions/{session_id}/hint",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["hint_level"] == 2

    # Request hint 3
    resp = await client.post(
        f"/tutor/sessions/{session_id}/hint",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["hint_level"] == 3

    # Request hint 4 - the terminal Full Explanation rung (FR-03.2). The cap
    # used to be 3, which left the final stage of the ladder unreachable.
    resp = await client.post(
        f"/tutor/sessions/{session_id}/hint",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["hint_level"] == 4

    # Request hint 5 (should stay at 4)
    resp = await client.post(
        f"/tutor/sessions/{session_id}/hint",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["hint_level"] == 4

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)


@pytest.mark.asyncio
async def test_hint_before_chat_fails(client, student_token, ready_course_id):
    """Request hint without prior question should return 400."""
    # Create session
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    # Try to request hint without sending a chat first
    resp = await client.post(
        f"/tutor/sessions/{session_id}/hint",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 400
    assert "before requesting a hint" in resp.json()["detail"].lower()


# ── Message history tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_messages_after_exchange(client, student_token, ready_course_id):
    """Fetch message history after a chat exchange."""
    # Create session
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    # Mock services
    mock_retrieval = _make_mock_retrieval(grounded=True)
    mock_gemini = _make_mock_gemini_pro()
    mock_langfuse = _make_mock_langfuse()
    app.dependency_overrides[get_retrieval_service] = lambda: mock_retrieval
    app.dependency_overrides[get_gemini_pro] = lambda: mock_gemini
    app.dependency_overrides[get_langfuse_client] = lambda: mock_langfuse

    # Send a chat
    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "What is logic?"},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200

    # Fetch message history
    resp = await client.get(
        f"/tutor/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 2  # 1 student + 1 tutor
    assert messages[0]["role"] == "student"
    assert messages[0]["content"] == "What is logic?"
    assert messages[1]["role"] == "tutor"
    assert len(messages[1]["citations"]) > 0

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)


# ── List sessions tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions(client, student_token, ready_course_id):
    """List all sessions for a student."""
    # Create two sessions
    for i in range(2):
        resp = await client.post(
            "/tutor/sessions",
            json={"course_id": str(ready_course_id)},
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 201

    # List sessions
    resp = await client.get(
        "/tutor/sessions",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 2
    assert all(s["course_id"] == str(ready_course_id) for s in sessions)


# ── Authorization tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_requires_auth(client, ready_course_id):
    """Chat without auth should return 401."""
    fake_session_id = uuid.uuid4()
    resp = await client.post(
        f"/tutor/sessions/{fake_session_id}/chat",
        json={"question": "What?"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_requires_student_role(client, teacher_token, ready_course_id):
    """Teacher role cannot use chat endpoint."""
    # Create a session as a student first
    async with _TestSessionFactory() as db:
        student = User(
            email="student2@demo.com",
            hashed_password=hash_password("password123"),
            role="student",
        )
        db.add(student)
        await db.flush()
        session = TutoringSession(student_id=student.id, course_id=ready_course_id)
        db.add(session)
        await db.commit()
        session_id = session.id

    # Try to use chat as teacher
    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "What?"},
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invalid_session_id_returns_404(client, student_token):
    """Invalid session ID should return 404."""
    fake_session_id = uuid.uuid4()
    resp = await client.post(
        f"/tutor/sessions/{fake_session_id}/chat",
        json={"question": "What?"},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 404


# ── Langfuse v4 regression tests ──────────────────────────────────────────────
#
# The tutor graph's final node (emit_pedagogy_trace_node) writes a Langfuse
# trace, and BOTH graph branches — generate_response and refuse — route through
# it. When langfuse 2.x -> 4.x removed `Langfuse.trace()`, that node raised
# AttributeError on every turn, so /chat and /hint returned 500 and the
# student's message was discarded before it was persisted.
#
# It survived the whole existing suite because every tutor test handed the graph
# a bare MagicMock, which cheerfully answers `.trace()` forever. The tests below
# close that hole from both directions: one drives the real installed SDK, the
# other proves the node is now non-fatal.


@pytest.fixture
def real_langfuse():
    """
    A *real* langfuse client, wired to an in-memory OTel exporter.

    No network: spans are handed to `exporter` instead of the Langfuse API, so
    the test exercises the installed SDK's actual method surface (which is the
    point) while staying hermetic and fast.

    The public key must be unique per instantiation. LangfuseResourceManager
    caches clients in a process-wide ``_instances`` dict keyed by public key and
    ignores the ``span_exporter`` of every subsequent client sharing that key —
    so a fixed key would silently route the second test's spans into the first
    test's (already shut down) exporter, and this fixture would report an empty
    span list regardless of what the code under test did.
    """
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    lf = Langfuse(
        public_key=f"pk-lf-test-{uuid.uuid4().hex}",
        secret_key="sk-lf-test",
        host="http://localhost:1",  # never contacted; export goes to `exporter`
        span_exporter=exporter,
        tracing_enabled=True,
        flush_at=1,
        sample_rate=1.0,
    )
    try:
        yield lf, exporter
    finally:
        lf.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("grounded", [True, False], ids=["grounded", "refused"])
async def test_chat_returns_200_against_real_langfuse_sdk(
    client, student_token, ready_course_id, real_langfuse, grounded
):
    """
    POST /chat must return 200 — not 500 — against the real langfuse client.

    The tutor graph runs for real here: only retrieval and the LLM are stubbed,
    so emit_pedagogy_trace_node executes the genuine SDK call. Parametrized over
    both branches because they converge on that node.
    """
    lf, exporter = real_langfuse

    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    app.dependency_overrides[get_retrieval_service] = lambda: _make_mock_retrieval(
        grounded=grounded
    )
    app.dependency_overrides[get_gemini_pro] = lambda: _make_mock_gemini_pro()
    app.dependency_overrides[get_langfuse_client] = lambda: lf

    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "What is the Socratic method?"},
        headers={"Authorization": f"Bearer {student_token}"},
    )

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)

    assert resp.status_code == 200, resp.text
    assert resp.json()["is_grounded"] is grounded

    # The trace call genuinely succeeded — it was not swallowed by the
    # non-fatal guard. Without this the test would still pass if the SDK call
    # were wrong, which is the failure mode we are guarding against.
    lf.flush()
    span_names = [s.name for s in exporter.get_finished_spans()]
    assert "tutor_interaction" in span_names, span_names


@pytest.mark.asyncio
async def test_chat_survives_langfuse_failure(
    client, student_token, ready_course_id, caplog
):
    """
    Defense in depth: a Langfuse outage must not turn a good answer into a 500.

    Complements the test above rather than replacing it — that one proves the
    call is correct, this one proves a broken call can no longer take down the
    turn (which is how the v4 break became user-visible in the first place).
    """
    from langfuse import Langfuse

    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    broken_langfuse = MagicMock(spec=Langfuse)
    broken_langfuse.start_observation.side_effect = RuntimeError("langfuse is down")

    app.dependency_overrides[get_retrieval_service] = lambda: _make_mock_retrieval(
        grounded=True
    )
    app.dependency_overrides[get_gemini_pro] = lambda: _make_mock_gemini_pro()
    app.dependency_overrides[get_langfuse_client] = lambda: broken_langfuse

    with caplog.at_level(logging.ERROR, logger="agents.tutor.nodes"):
        resp = await client.post(
            f"/tutor/sessions/{session_id}/chat",
            json={"question": "What is the Socratic method?"},
            headers={"Authorization": f"Bearer {student_token}"},
        )

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_grounded"] is True
    assert data["response"]  # the tutor's answer still came back
    # The failure must be LOUD. A bare warning is how the v4 break stayed
    # invisible for a whole major version, and observability coverage is itself
    # an acceptance criterion — so a dead trace is logged at ERROR, with the
    # traceback, naming what was lost.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a failed trace must be logged at ERROR, not swallowed"
    assert any("Langfuse trace FAILED" in r.message for r in errors)
    assert any(r.exc_info for r in errors), "the traceback must be captured"


@pytest.mark.asyncio
async def test_chat_persists_messages_when_tracing_fails(
    client, student_token, ready_course_id
):
    """
    The student's message must survive a tracing failure.

    Persistence happens after graph.ainvoke() in send_chat_message, so the
    original crash discarded the student's question as well as the answer.
    """
    from sqlalchemy import select

    from db.models import TutoringMessage
    from langfuse import Langfuse

    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    broken_langfuse = MagicMock(spec=Langfuse)
    broken_langfuse.start_observation.side_effect = RuntimeError("langfuse is down")

    app.dependency_overrides[get_retrieval_service] = lambda: _make_mock_retrieval(
        grounded=True
    )
    app.dependency_overrides[get_gemini_pro] = lambda: _make_mock_gemini_pro()
    app.dependency_overrides[get_langfuse_client] = lambda: broken_langfuse

    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "What is the Socratic method?"},
        headers={"Authorization": f"Bearer {student_token}"},
    )

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)

    assert resp.status_code == 200, resp.text

    async with _TestSessionFactory() as db:
        rows = (
            await db.execute(
                select(TutoringMessage).where(
                    TutoringMessage.session_id == uuid.UUID(session_id)
                )
            )
        ).scalars().all()

    roles = sorted(m.role for m in rows)
    assert roles == ["student", "tutor"], roles


# ── Groundedness threshold regression tests ───────────────────────────────────
#
# check_groundedness_node used to call is_grounded() with no threshold, silently
# taking its old 0.75 default instead of settings.active_groundedness_threshold
# (0.20 under the mock provider). Every on-topic question scoring between the
# two bars was refused. Live repro from the bug audit: a real "What are tectonic
# plates?" against an ingested earth.pdf course retrieved at 0.2041 — correctly
# grounded at 0.20, wrongfully refused at 0.75.
#
# The tests above stub `is_grounded.return_value` outright, so they assert the
# mock's answer, not the gate's. These drive the REAL gate: only retrieve() is
# stubbed (that is the Chroma + embeddings boundary), and is_grounded /
# build_context_window / build_citation_bundle are the production methods.


def _make_real_gate_retrieval(top_score: float):
    """
    A RetrievalService with only `retrieve()` stubbed.

    Constructed via __new__ so no Chroma client or LLM is needed; every other
    method is the genuine implementation, which is the entire point — a mocked
    `is_grounded.return_value` cannot catch a wrong threshold.
    """
    from retrieval.models import Chunk, RetrievalResult
    from retrieval.service import RetrievalService

    svc = RetrievalService.__new__(RetrievalService)
    chunk = Chunk(
        text="The Socratic method uses questions to develop critical thinking.",
        source_file="philosophy_101.pdf",
        page_or_slide=3,
        course_id=uuid.UUID(int=0),
    )
    svc.retrieve = MagicMock(
        return_value=RetrievalResult(
            query="test", chunks=[chunk], confidence_scores=[top_score]
        )
    )
    return svc


async def _chat_with_real_gate(client, token, course_id, top_score):
    """Run one chat turn through the real graph against the real gate."""
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(course_id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    session_id = resp.json()["session_id"]

    app.dependency_overrides[get_retrieval_service] = lambda: _make_real_gate_retrieval(
        top_score
    )
    app.dependency_overrides[get_gemini_pro] = lambda: _make_mock_gemini_pro()
    app.dependency_overrides[get_langfuse_client] = lambda: _make_mock_langfuse()
    try:
        return await client.post(
            f"/tutor/sessions/{session_id}/chat",
            json={"question": "What is the Socratic method?"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.pop(get_retrieval_service, None)
        app.dependency_overrides.pop(get_gemini_pro, None)
        app.dependency_overrides.pop(get_langfuse_client, None)


def test_mock_provider_threshold_is_the_value_these_tests_assume():
    """Anchor the scores below to the configured bar, not to a magic number."""
    from core.config import settings

    assert settings.llm_provider == "mock"
    assert settings.active_groundedness_threshold == 0.20


@pytest.mark.asyncio
async def test_chat_accepts_borderline_score_between_mock_and_gemini_bars(
    client, student_token, ready_course_id
):
    """
    THE regression: 0.2041 is above the mock bar (0.20) and below the old
    hardcoded default (0.75). It must now be answered, not refused.

    This is the exact score the live repro produced against a real ingested
    course, so a pass here means that user-visible symptom is gone.
    """
    resp = await _chat_with_real_gate(client, student_token, ready_course_id, 0.2041)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_grounded"] is True, "0.2041 is above the mock bar of 0.20"
    assert "I can only help with topics covered in this course" not in data["response"]
    assert data["citations"], "a grounded answer must carry citations"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "top_score, grounded",
    [
        (0.90, True),    # comfortably grounded under any bar
        (0.2041, True),  # the live repro score — between the two bars
        (0.20, True),    # exactly at the bar: is_grounded uses >=
        (0.1999, False), # just below the bar
        (0.05, False),   # genuinely off-topic
    ],
    ids=["high", "live-repro", "at-bar", "just-below", "off-topic"],
)
async def test_chat_gate_honours_active_threshold(
    client, student_token, ready_course_id, top_score, grounded
):
    """
    The gate must track settings.active_groundedness_threshold on both sides.

    The negative cases matter as much as the positive ones: the fix must not
    have simply lowered the bar to zero — genuinely ungrounded questions still
    have to be refused (AC-02, zero leakage).
    """
    resp = await _chat_with_real_gate(client, student_token, ready_course_id, top_score)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_grounded"] is grounded
    refused = "I can only help with topics covered in this course" in data["response"]
    assert refused is not grounded


@pytest.mark.asyncio
async def test_refusal_still_makes_no_llm_call(client, student_token, ready_course_id):
    """
    AC-02: an ungrounded question must not reach the LLM at all.

    Guards the other direction of the fix — that raising the acceptance rate
    did not start leaking off-corpus questions into the model.
    """
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(ready_course_id)},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    session_id = resp.json()["session_id"]

    gemini = _make_mock_gemini_pro()
    app.dependency_overrides[get_retrieval_service] = lambda: _make_real_gate_retrieval(
        0.05
    )
    app.dependency_overrides[get_gemini_pro] = lambda: gemini
    app.dependency_overrides[get_langfuse_client] = lambda: _make_mock_langfuse()

    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "Tell me about pizza"},
        headers={"Authorization": f"Bearer {student_token}"},
    )

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_pro, None)
    app.dependency_overrides.pop(get_langfuse_client, None)

    assert resp.status_code == 200
    assert resp.json()["is_grounded"] is False
    gemini.generate.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# Bug 1 — refusal conflation: query resolution + the FR-03.3 decline branch
# Bug 2 — hint ladder escalation + the terminal Full Explanation rung
#
# Both bugs were confirmed by a live diagnostic pass against Ollama
# (nomic-embed-text, DSA course, bar 0.57). The measured scores are reproduced
# in the helpers below so the regression cases are the real ones:
#
#   "What is the time complexity of binary search and why?"   0.6616  grounded
#   "give me the answer"                                      0.5414  refused
#   "what about the worst case"                               0.4979  refused
#   control: "best recipe for sourdough bread"                0.5108  refused
#   control: "who won the 1998 football world cup"            0.3813  refused
#
# Note the on-topic follow-up scored HIGHER than an off-topic control — which is
# why the fix is query resolution, not threshold tuning.
# ══════════════════════════════════════════════════════════════════════════════

_ON_TOPIC = "What is the time complexity of binary search and why?"

#: Measured top_scores, keyed by the text actually sent to retrieve().
_MEASURED_SCORES = {
    _ON_TOPIC: 0.6616,
    "give me the answer": 0.5414,
    "just tell me": 0.4878,
    "what about the worst case": 0.4979,
    "What is the best recipe for sourdough bread?": 0.5108,
    "Who won the 1998 football world cup?": 0.3813,
}


def _make_query_scoring_retrieval(scores: dict[str, float] = None, default: float = 0.30):
    """
    A RetrievalService whose score depends on the QUERY TEXT it is handed.

    This is the whole point of these tests: a stub that returns a fixed score
    cannot tell whether query resolution actually changed which text was
    embedded. Only `retrieve()` is stubbed — `is_grounded` and
    `build_citation_bundle` are the genuine implementations, so the real
    provider threshold is exercised.
    """
    from retrieval.models import Chunk, RetrievalResult
    from retrieval.service import RetrievalService

    table = dict(_MEASURED_SCORES if scores is None else scores)
    svc = RetrievalService.__new__(RetrievalService)
    seen: list[str] = []

    def _retrieve(course_id, query, *args, **kwargs):
        seen.append(query)
        chunk = Chunk(
            text="Binary search halves the search interval on each step.",
            source_file="dsa_notes.pdf",
            page_or_slide=7,
            course_id=uuid.UUID(int=0),
        )
        return RetrievalResult(
            query=query,
            chunks=[chunk],
            confidence_scores=[table.get(query, default)],
        )

    svc.retrieve = MagicMock(side_effect=_retrieve)
    svc.queries_seen = seen
    return svc


#: Saved llm_provider values, so _clear_tutor_mocks can restore what it changed.
_PROVIDER_STACK: list[str] = []


def _install_tutor_mocks(retrieval, gemini=None, langfuse=None):
    """
    Override the three app.state-backed tutor dependencies, and evaluate the
    groundedness gate against the ollama bar for the duration of the test.

    The provider switch is load-bearing, not incidental. _MEASURED_SCORES holds
    real nomic-embed-text scores, and they only mean what they meant live when
    judged against that provider's 0.57 threshold. Under the suite's default
    mock provider the bar is 0.20, where even the off-topic controls (0.5108,
    0.3813) read as grounded — and every zero-leakage assertion below would
    pass while proving nothing.
    """
    from core.config import settings

    _PROVIDER_STACK.append(settings.llm_provider)
    settings.llm_provider = "ollama"

    gemini = gemini or _make_mock_gemini_pro()
    app.dependency_overrides[get_retrieval_service] = lambda: retrieval
    app.dependency_overrides[get_gemini_pro] = lambda: gemini
    app.dependency_overrides[get_langfuse_client] = lambda: (langfuse or _make_mock_langfuse())
    return gemini


def _clear_tutor_mocks():
    from core.config import settings

    for dep in (get_retrieval_service, get_gemini_pro, get_langfuse_client):
        app.dependency_overrides.pop(dep, None)
    if _PROVIDER_STACK:
        settings.llm_provider = _PROVIDER_STACK.pop()


async def _new_session(client, token, course_id) -> str:
    resp = await client.post(
        "/tutor/sessions",
        json={"course_id": str(course_id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


async def _chat(client, token, session_id, question):
    return await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": question},
        headers={"Authorization": f"Bearer {token}"},
    )


async def _hint(client, token, session_id):
    return await client.post(
        f"/tutor/sessions/{session_id}/hint",
        headers={"Authorization": f"Bearer {token}"},
    )


def _is_out_of_corpus(text: str) -> bool:
    from agents.tutor.prompts import OUT_OF_CORPUS_REFUSAL

    return OUT_OF_CORPUS_REFUSAL in text


def _is_pedagogy_decline(text: str) -> bool:
    from agents.tutor.prompts import DIRECT_ANSWER_DECLINE

    return DIRECT_ANSWER_DECLINE in text


# ── Unit: query resolution ────────────────────────────────────────────────────


class TestLowContentDetection:
    """The heuristic that decides whether a message can be retrieved on alone."""

    def _fn(self):
        from agents.tutor.query_resolution import is_low_content

        return is_low_content

    @pytest.mark.parametrize(
        "message",
        [
            "give me the answer",
            "just tell me",
            "why?",
            "what about the worst case",
            "I don't know",
            "can you explain more",
            "",
        ],
    )
    def test_contentless_messages_are_low_content(self, message):
        assert self._fn()(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            _ON_TOPIC,
            "What is the best recipe for sourdough bread?",
            "Who won the 1998 football world cup?",
            "How does quicksort partition an array?",
        ],
    )
    def test_topical_messages_are_not_low_content(self, message):
        # The two off-topic controls MUST land here: they have to keep being
        # scored on their own text, which is what keeps them refused.
        assert self._fn()(message) is False


class TestPedagogySkipDetection:
    """FR-03.3 classification — separate from groundedness."""

    def _fn(self):
        from agents.tutor.query_resolution import is_pedagogy_skip_request

        return is_pedagogy_skip_request

    @pytest.mark.parametrize(
        "message",
        [
            "give me the answer",
            "Give me the answer!",
            "ok just tell me the answer already",
            "what's the answer",
            "solve it for me",
            "i give up",
        ],
    )
    def test_answer_demands_detected(self, message):
        assert self._fn()(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            _ON_TOPIC,
            "what about the worst case",
            "why?",
            "I don't know",
            "Can you explain how the halving works?",
            "",
        ],
    )
    def test_ordinary_messages_not_flagged(self, message):
        # A student who is merely stuck is NOT asking to skip the pedagogy.
        assert self._fn()(message) is False


class TestResolveRetrievalQuery:
    def _fn(self):
        from agents.tutor.query_resolution import resolve_retrieval_query

        return resolve_retrieval_query

    def test_low_content_message_falls_back_to_prior_turn(self):
        query, source = self._fn()("give me the answer", _ON_TOPIC)
        assert query == _ON_TOPIC
        assert source == "prior_turn"

    def test_topical_message_is_used_as_is(self):
        off_topic = "What is the best recipe for sourdough bread?"
        query, source = self._fn()(off_topic, _ON_TOPIC)
        assert query == off_topic
        assert source == "message"

    def test_no_prior_turn_means_no_fallback(self):
        query, source = self._fn()("give me the answer", None)
        assert query == "give me the answer"
        assert source == "message"

    def test_fallback_substitutes_rather_than_concatenates(self):
        # Concatenation was measured and rejected: it lifts off-topic
        # follow-ups to ~0.65 and breaks zero-leakage.
        query, _ = self._fn()("give me the answer", _ON_TOPIC)
        assert "give me the answer" not in query


# ── Unit: the hint ladder prompts ─────────────────────────────────────────────


class TestHintLadderPrompts:
    """Bug 2 was a prompt contradiction, so the prompts get asserted directly."""

    def _ladder(self):
        from agents.tutor.prompts import HINT_LADDER

        return HINT_LADDER

    def test_every_level_has_its_own_instruction(self):
        ladder = self._ladder()
        assert set(ladder) == {0, 1, 2, 3, 4}
        assert len(set(ladder.values())) == 5, "levels must not share a prompt"

    def test_low_rungs_forbid_the_answer(self):
        ladder = self._ladder()
        for level in (0, 1):
            assert "do not give the student the answer" in ladder[level].lower()

    def test_terminal_rung_requires_the_answer(self):
        text = self._ladder()[4].lower()
        assert "full explanation" in text
        assert "state the answer plainly" in text
        # The absolute that made every rung read the same must be gone up here.
        assert "do not give the student the answer" not in text

    def test_directness_is_stated_at_every_level(self):
        for level, text in self._ladder().items():
            assert f"hint level {level}" in text.lower(), (
                f"level {level} does not state its own level"
            )

    def test_grounding_clause_survives_every_rung(self):
        # Directness escalates; permission to leave the corpus never does.
        for level, text in self._ladder().items():
            assert "ONLY on the provided course context" in text, level

    def test_level_lookup_clamps_out_of_range(self):
        from agents.tutor.prompts import HINT_LADDER, system_instruction_for_level

        assert system_instruction_for_level(-3) == HINT_LADDER[0]
        assert system_instruction_for_level(99) == HINT_LADDER[4]


# ── Integration: Bug 1, chat path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_request_after_on_topic_turn_is_not_out_of_corpus(
    client, student_token, ready_course_id
):
    """
    The headline bug: "give me the answer" mid-conversation used to be told the
    question was outside the course material.
    """
    retrieval = _make_query_scoring_retrieval()
    _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        first = await _chat(client, student_token, session_id, _ON_TOPIC)
        assert first.status_code == 200
        assert first.json()["is_grounded"] is True

        resp = await _chat(client, student_token, session_id, "give me the answer")
    finally:
        _clear_tutor_mocks()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert not _is_out_of_corpus(data["response"]), (
        "on-topic answer request must not get the out-of-corpus refusal"
    )
    assert _is_pedagogy_decline(data["response"])
    # The TOPIC is grounded, so the flag reports the topic, not the decline.
    assert data["is_grounded"] is True
    # Retrieval ran on the prior turn, not on the contentless message.
    assert retrieval.queries_seen[-1] == _ON_TOPIC


@pytest.mark.asyncio
async def test_pedagogy_decline_wording_is_distinct_and_points_at_the_ladder(
    client, student_token, ready_course_id
):
    """FR-03.3 must read differently from FR-02.7 and must not dead-end."""
    from agents.tutor.prompts import DIRECT_ANSWER_DECLINE, OUT_OF_CORPUS_REFUSAL

    assert DIRECT_ANSWER_DECLINE != OUT_OF_CORPUS_REFUSAL
    assert "hint" in DIRECT_ANSWER_DECLINE.lower()

    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        calls_before = gemini.generate.call_count
        resp = await _chat(client, student_token, session_id, "just tell me")
    finally:
        _clear_tutor_mocks()

    assert _is_pedagogy_decline(resp.json()["response"])
    # Declining must not go through the model: an LLM asked to refuse an answer
    # request is the setup most likely to leak the answer while refusing.
    assert gemini.generate.call_count == calls_before


@pytest.mark.asyncio
async def test_off_topic_follow_up_after_on_topic_turn_still_refuses(
    client, student_token, ready_course_id
):
    """
    Zero-leakage regression guard (AC-02).

    This is the case that killed naive history-concatenation: with the prior
    turn blended into the query, this scored ~0.66 and passed. Substitution must
    not reintroduce it — a follow-up with real topical content of its own is
    always scored on that content.
    """
    off_topic = "What is the best recipe for sourdough bread?"
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        calls_before = gemini.generate.call_count
        resp = await _chat(client, student_token, session_id, off_topic)
    finally:
        _clear_tutor_mocks()

    data = resp.json()
    assert data["is_grounded"] is False
    assert _is_out_of_corpus(data["response"])
    # Scored on its own text — it did not inherit the prior topic's grounding.
    assert retrieval.queries_seen[-1] == off_topic
    assert gemini.generate.call_count == calls_before


@pytest.mark.asyncio
async def test_second_off_topic_control_also_still_refuses(
    client, student_token, ready_course_id
):
    """The lower-scoring control, for good measure."""
    off_topic = "Who won the 1998 football world cup?"
    retrieval = _make_query_scoring_retrieval()
    _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        resp = await _chat(client, student_token, session_id, off_topic)
    finally:
        _clear_tutor_mocks()

    assert resp.json()["is_grounded"] is False
    assert _is_out_of_corpus(resp.json()["response"])


@pytest.mark.asyncio
async def test_stuck_follow_up_is_tutored_not_refused(
    client, student_token, ready_course_id
):
    """
    "what about the worst case" is on-topic and is NOT an answer demand, so it
    must be tutored normally. It scored 0.4979 alone — refused before the fix.
    """
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        resp = await _chat(client, student_token, session_id, "what about the worst case")
    finally:
        _clear_tutor_mocks()

    data = resp.json()
    assert data["is_grounded"] is True
    assert not _is_out_of_corpus(data["response"])
    assert not _is_pedagogy_decline(data["response"])
    assert gemini.generate.called


@pytest.mark.asyncio
async def test_answer_request_as_first_message_is_still_out_of_corpus(
    client, student_token, ready_course_id
):
    """
    With no prior turn there is nothing to fall back to, so a session that opens
    with "give me the answer" is genuinely ungrounded and must be refused as
    such. The fallback must not become an unconditional bypass.
    """
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        resp = await _chat(client, student_token, session_id, "give me the answer")
    finally:
        _clear_tutor_mocks()

    data = resp.json()
    assert data["is_grounded"] is False
    assert _is_out_of_corpus(data["response"])
    assert not gemini.generate.called


# ── Integration: Bug 1 item 3 — the hint path must not stay poisoned ──────────


@pytest.mark.asyncio
async def test_hint_after_answer_request_uses_the_substantive_question(
    client, student_token, ready_course_id
):
    """
    request_hint retrieves on the last student message. After the student types
    "give me the answer", every later hint click used to retrieve on that
    string, score 0.5414, and refuse — the ladder stayed broken for the rest of
    the session.
    """
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        await _chat(client, student_token, session_id, "give me the answer")
        resp = await _hint(client, student_token, session_id)
    finally:
        _clear_tutor_mocks()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The hint was actually generated, not refused before reaching the model.
    assert gemini.generate.called
    assert not _is_out_of_corpus(data["response"])
    # Clicking hint IS using the ladder, so it must never be declined either.
    assert not _is_pedagogy_decline(data["response"])
    assert data["hint_level"] == 1
    assert retrieval.queries_seen[-1] == _ON_TOPIC


# ── Integration: Bug 2 — escalation and the terminal rung ─────────────────────


@pytest.mark.asyncio
async def test_hint_ladder_reaches_the_full_explanation_rung(
    client, student_token, ready_course_id
):
    """Four clicks walk 1 -> 2 -> 3 -> 4, then stay at the terminal rung."""
    from agents.tutor.prompts import FULL_EXPLANATION_LEVEL

    retrieval = _make_query_scoring_retrieval()
    _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        levels = []
        for _ in range(5):
            resp = await _hint(client, student_token, session_id)
            assert resp.status_code == 200, resp.text
            levels.append(resp.json()["hint_level"])
    finally:
        _clear_tutor_mocks()

    assert levels == [1, 2, 3, 4, 4]
    assert FULL_EXPLANATION_LEVEL == 4


@pytest.mark.asyncio
async def test_each_rung_sends_a_different_and_escalating_instruction(
    client, student_token, ready_course_id
):
    """
    The measurable form of "hint 3 must be more direct than hint 1".

    Before the fix all four calls sent a byte-identical system instruction, and
    the only difference anywhere in the 1930-character prompt was one digit
    appended to the user turn. Asserting on what is SENT is the defensible
    check — the model's wording is not deterministic, but the instruction it
    receives is.
    """
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)  # level 0
        for _ in range(4):
            await _hint(client, student_token, session_id)  # levels 1..4
    finally:
        _clear_tutor_mocks()

    systems = [c.kwargs["system_instruction"] for c in gemini.generate.call_args_list]
    assert len(systems) == 5, "one call per rung (chat + 4 hints)"

    # 1. No two rungs share an instruction — the original bug in one assertion.
    assert len(set(systems)) == 5

    # 2. Each rung announces its own level, in order.
    for level, text in enumerate(systems):
        assert f"HINT LEVEL {level}" in text

    # 3. Directness escalates: the low rungs withhold, the top rung does not.
    assert "Do not give the student the answer" in systems[0]
    assert "Do not give the student the answer" in systems[1]
    assert "STOP short of" in systems[2]
    assert "do not withhold" in systems[4].lower()
    assert "State the answer plainly" in systems[4]

    # 4. Grounding is constant across the ladder.
    for text in systems:
        assert "ONLY on the provided course context" in text


@pytest.mark.asyncio
async def test_terminal_rung_instruction_is_distinct_from_level_three(
    client, student_token, ready_course_id
):
    """The Full Explanation stage must be a real stage, not a fourth hint."""
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        for _ in range(4):
            await _hint(client, student_token, session_id)
    finally:
        _clear_tutor_mocks()

    systems = [c.kwargs["system_instruction"] for c in gemini.generate.call_args_list]
    level_3, level_4 = systems[3], systems[4]

    assert level_3 != level_4
    assert "FULL EXPLANATION" in level_4
    assert "FULL EXPLANATION" not in level_3
    # Level 3 still frames itself as a hint; level 4 must not ask a question
    # in place of the answer.
    assert "near-answer" in level_3
    assert "Do not ask the student a guiding question in place of the answer" in level_4


@pytest.mark.asyncio
async def test_tutor_generation_runs_cooler_than_the_client_default(
    client, student_token, ready_course_id
):
    """
    Ladder adherence depends on the model honouring a per-level target, and the
    0.7 client default added enough sampling noise that adjacent rungs could
    come back in either order.
    """
    from agents.tutor.prompts import TUTOR_TEMPERATURE

    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
    finally:
        _clear_tutor_mocks()

    assert gemini.generate.call_args.kwargs["temperature"] == TUTOR_TEMPERATURE
    assert 0.0 < TUTOR_TEMPERATURE < 0.7


@pytest.mark.asyncio
async def test_hint_prompt_topic_is_the_resolved_question(
    client, student_token, ready_course_id
):
    """
    A guiding question generated about the literal string "give me the answer"
    is nonsense, so the prompt must state the resolved topic instead.
    """
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        await _chat(client, student_token, session_id, "give me the answer")
        await _hint(client, student_token, session_id)
    finally:
        _clear_tutor_mocks()

    prompt = gemini.generate.call_args.args[0]
    assert _ON_TOPIC in prompt
    assert "give me the answer" not in prompt


@pytest.mark.asyncio
async def test_decline_is_recorded_as_a_refusal_in_the_transcript(
    client, student_token, ready_course_id
):
    """The UI styles refusals differently; a decline is one, and is grounded."""
    retrieval = _make_query_scoring_retrieval()
    _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        await _chat(client, student_token, session_id, "give me the answer")
        messages = await client.get(
            f"/tutor/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {student_token}"},
        )
    finally:
        _clear_tutor_mocks()

    tutor_msgs = [m for m in messages.json() if m["role"] == "tutor"]
    assert len(tutor_msgs) == 2
    assert tutor_msgs[0]["is_refusal"] is False  # the ordinary guiding question
    assert tutor_msgs[1]["is_refusal"] is True  # the decline
    assert _is_pedagogy_decline(tutor_msgs[1]["content"])
    # Grounded, so the student still sees which material covers the topic.
    assert tutor_msgs[1]["citations"]


@pytest.mark.asyncio
async def test_hint_after_an_off_topic_question_refuses_rather_than_falling_back(
    client, student_token, ready_course_id
):
    """
    Found during live verification, pinned here so it stays deliberate.

    Query resolution falls back only for CONTENTLESS messages. An off-topic
    question has plenty of topical content, so it stays the active question and
    the hint button correctly reports it as out-of-corpus — the student is not
    silently given hints about a different, earlier topic they did not ask
    about. Typing a new on-topic question starts a fresh ladder, which is the
    same rule /chat already applies by resetting current_hint_level.

    The contrast with test_hint_after_answer_request_uses_the_substantive_question
    is the point: "give me the answer" has no topic and falls back; "what is the
    best recipe for sourdough bread?" has one and does not.
    """
    off_topic = "What is the best recipe for sourdough bread?"
    retrieval = _make_query_scoring_retrieval()
    gemini = _install_tutor_mocks(retrieval)
    try:
        session_id = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, session_id, _ON_TOPIC)
        await _chat(client, student_token, session_id, off_topic)
        calls_before = gemini.generate.call_count
        resp = await _hint(client, student_token, session_id)
    finally:
        _clear_tutor_mocks()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert _is_out_of_corpus(data["response"])
    assert retrieval.queries_seen[-1] == off_topic, "must not inherit the earlier topic"
    assert gemini.generate.call_count == calls_before, "AC-02: no LLM call on a refusal"
    # The ladder does not advance on a refused turn.
    assert data["hint_level"] == 0
