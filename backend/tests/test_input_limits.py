"""
tests/test_input_limits.py
--------------------------
Maximum input sizes (audit 2026-10-06 #5; limits live in core/limits.py).

For every bounded field: a value exactly at the limit is accepted, and one
character over is a 422 naming the limit -- never a 500. Before this, an
over-long course name reached the VARCHAR(255) column and failed as a 500, and
tutor questions and answers had no ceiling at all.

Same pattern as test_enrollment.py: SQLite in-memory DB, rows seeded directly.
"""

import json
import uuid

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
from core.limits import (
    MAX_ANSWER_TEXT_CHARS,
    MAX_ASSESSMENT_TITLE_CHARS,
    MAX_COURSE_NAME_CHARS,
    MAX_EMAIL_CHARS,
    MAX_SUBMISSION_ANSWERS,
    MAX_TUTOR_QUESTION_CHARS,
)
from core.security import hash_password
from db.models import Assessment, Course, Enrollment, Question, User
from main import app
from tests.test_tutoring import (
    _make_mock_gemini_pro,
    _make_mock_langfuse,
    _make_mock_retrieval,
)

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
        db.add_all(
            [
                User(email="teacher@demo.com", hashed_password=hash_password("password123"), role="teacher"),
                User(email="student@demo.com", hashed_password=hash_password("password123"), role="student"),
            ]
        )
        await db.commit()
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    app.dependency_overrides[get_db_session] = _override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _token(client, email: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "password123"})
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _assert_too_long(resp, limit: int) -> None:
    """A clean 422 that names the limit, with the oversized value not echoed."""
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"] == "validation_error"
    err = body["errors"][0]
    assert err["type"] in ("string_too_long", "too_long", "value_error")
    if err["type"] != "value_error":  # EmailStr reports its own length error
        assert err["ctx"]["max_length"] == limit
    assert "input" not in err


def _email(length: int) -> str:
    """A syntactically valid address of exactly ``length`` characters."""
    local = "a" * 64  # the longest local part RFC 5321 allows
    remaining = length - len(local) - 1 - len(".com")
    labels = []
    while remaining > 0:
        size = min(63, remaining)  # DNS labels are at most 63 characters
        labels.append("b" * size)
        remaining -= size + 1
    address = f"{local}@{'.'.join(labels)}.com"
    assert len(address) == length
    return address


@pytest.fixture
async def course() -> dict:
    """
    A ready course owned by teacher@demo.com, student@demo.com enrolled, with a
    published assessment of MAX_SUBMISSION_ANSWERS short-answer questions.
    """
    async with _Session() as db:
        teacher = (await db.execute(select(User).where(User.email == "teacher@demo.com"))).scalar_one()
        student = (await db.execute(select(User).where(User.email == "student@demo.com"))).scalar_one()
        c = Course(name="Limits 101", owner_id=teacher.id, status="ready")
        db.add(c)
        await db.flush()
        a = Assessment(
            course_id=c.id, created_by=teacher.id, title="Long Quiz",
            status="published", config=json.dumps({"topic": "limits"}),
        )
        db.add(a)
        await db.flush()
        questions = [
            Question(
                assessment_id=a.id, question_type="short_answer", stem=f"Explain point {i}.",
                answer_key=json.dumps({"model_answer": "anything"}),
                max_points=1.0, order_index=i,
            )
            for i in range(MAX_SUBMISSION_ANSWERS)
        ]
        db.add_all(questions)
        db.add(Enrollment(course_id=c.id, student_id=student.id))
        await db.commit()
        return {
            "course_id": c.id,
            "assessment_id": a.id,
            "question_ids": [q.id for q in questions],
        }


# ── Tutor question ────────────────────────────────────────────────────────────

async def _session(client, token, course) -> str:
    resp = await client.post(
        "/tutor/sessions", json={"course_id": str(course["course_id"])}, headers=_auth(token)
    )
    return resp.json()["session_id"]


async def test_tutor_question_at_the_limit_is_answered(client, course):
    token = await _token(client, "student@demo.com")
    session_id = await _session(client, token, course)
    app.dependency_overrides[get_retrieval_service] = lambda: _make_mock_retrieval()
    app.dependency_overrides[get_gemini_pro] = lambda: _make_mock_gemini_pro()
    app.dependency_overrides[get_langfuse_client] = lambda: _make_mock_langfuse()

    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "q" * MAX_TUTOR_QUESTION_CHARS},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text


async def test_tutor_question_one_over_the_limit_is_422(client, course):
    token = await _token(client, "student@demo.com")
    session_id = await _session(client, token, course)
    resp = await client.post(
        f"/tutor/sessions/{session_id}/chat",
        json={"question": "q" * (MAX_TUTOR_QUESTION_CHARS + 1)},
        headers=_auth(token),
    )
    _assert_too_long(resp, MAX_TUTOR_QUESTION_CHARS)


# ── Submission answers ────────────────────────────────────────────────────────

def _answers(course, count: int, text: str = "an answer") -> dict:
    ids = course["question_ids"]
    return {
        "responses": [
            {"question_id": str(ids[i % len(ids)]), "answer_text": text} for i in range(count)
        ]
    }


async def test_short_answer_at_the_limit_is_accepted(client, course):
    token = await _token(client, "student@demo.com")
    resp = await client.post(
        f"/assessments/{course['assessment_id']}/submit",
        json=_answers(course, 1, "x" * MAX_ANSWER_TEXT_CHARS),
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text


async def test_short_answer_one_over_the_limit_is_422(client, course):
    token = await _token(client, "student@demo.com")
    resp = await client.post(
        f"/assessments/{course['assessment_id']}/submit",
        json=_answers(course, 1, "x" * (MAX_ANSWER_TEXT_CHARS + 1)),
        headers=_auth(token),
    )
    _assert_too_long(resp, MAX_ANSWER_TEXT_CHARS)


async def test_submission_with_the_maximum_number_of_answers_is_accepted(client, course):
    token = await _token(client, "student@demo.com")
    resp = await client.post(
        f"/assessments/{course['assessment_id']}/submit",
        json=_answers(course, MAX_SUBMISSION_ANSWERS),
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text


async def test_submission_with_one_answer_too_many_is_422(client, course):
    token = await _token(client, "student@demo.com")
    resp = await client.post(
        f"/assessments/{course['assessment_id']}/submit",
        json=_answers(course, MAX_SUBMISSION_ANSWERS + 1),
        headers=_auth(token),
    )
    _assert_too_long(resp, MAX_SUBMISSION_ANSWERS)


# ── Course name ───────────────────────────────────────────────────────────────

async def _upload(client, token, name: str):
    return await client.post(
        "/courses/upload",
        data={"name": name},
        files={"files": ("notes.txt", b"course content", "text/plain")},
        headers=_auth(token),
    )


async def test_course_name_at_the_column_limit_is_accepted(client):
    token = await _token(client, "teacher@demo.com")
    resp = await _upload(client, token, "n" * MAX_COURSE_NAME_CHARS)
    assert resp.status_code == 200, resp.text


async def test_course_name_one_over_the_column_limit_is_422_not_500(client):
    token = await _token(client, "teacher@demo.com")
    resp = await _upload(client, token, "n" * (MAX_COURSE_NAME_CHARS + 1))
    _assert_too_long(resp, MAX_COURSE_NAME_CHARS)


# ── Email (registration and enrollment) ───────────────────────────────────────

async def test_email_at_the_limit_registers_and_can_be_enrolled(client, course):
    address = _email(MAX_EMAIL_CHARS)
    reg = await client.post("/auth/register", json={"email": address, "password": "password123"})
    assert reg.status_code in (200, 201), reg.text

    teacher = await _token(client, "teacher@demo.com")
    resp = await client.post(
        f"/courses/{course['course_id']}/enrollments", json={"email": address}, headers=_auth(teacher)
    )
    assert resp.status_code == 201, resp.text


async def test_email_one_over_the_limit_is_422_on_register(client):
    resp = await client.post(
        "/auth/register", json={"email": _email(MAX_EMAIL_CHARS + 1), "password": "password123"}
    )
    _assert_too_long(resp, MAX_EMAIL_CHARS)


async def test_email_one_over_the_limit_is_422_on_enroll(client, course):
    teacher = await _token(client, "teacher@demo.com")
    resp = await client.post(
        f"/courses/{course['course_id']}/enrollments",
        json={"email": _email(MAX_EMAIL_CHARS + 1)},
        headers=_auth(teacher),
    )
    _assert_too_long(resp, MAX_EMAIL_CHARS)


# ── Assessment title ──────────────────────────────────────────────────────────

def _generate(course, title: str) -> dict:
    return {"course_id": str(course["course_id"]), "title": title, "topic": "joins", "count": 3}


async def test_assessment_title_at_the_limit_is_accepted(client, course):
    teacher = await _token(client, "teacher@demo.com")
    resp = await client.post(
        "/assessments/generate", json=_generate(course, "t" * MAX_ASSESSMENT_TITLE_CHARS),
        headers=_auth(teacher),
    )
    assert resp.status_code == 202, resp.text


async def test_assessment_title_one_over_the_limit_is_422(client, course):
    teacher = await _token(client, "teacher@demo.com")
    resp = await client.post(
        "/assessments/generate", json=_generate(course, "t" * (MAX_ASSESSMENT_TITLE_CHARS + 1)),
        headers=_auth(teacher),
    )
    _assert_too_long(resp, MAX_ASSESSMENT_TITLE_CHARS)
