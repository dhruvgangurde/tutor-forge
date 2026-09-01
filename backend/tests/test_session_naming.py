"""
tests/test_session_naming.py
-----------------------------
Session list display metadata, and the student-facing assessment card fields.

The student's session list showed "Session 4bc8fb2b" with a "Level N" badge.
Neither identifies anything: the UUID prefix is arbitrary, and the level is the
hint-ladder depth of the most recent question, which moves whenever a hint is
requested and says nothing about the subject. These cover the replacement —
a title derived from the session's first student question.

Also here: the published-assessment card gained a date, because two assessments
generated on one course with the same title were indistinguishable to a student.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import hash_password
from db.models import (
    Assessment,
    Course,
    Question,
    TutoringMessage,
    TutoringSession,
    User,
)
from main import app
from tutoring.service import (
    SESSION_TITLE_MAX_CHARS,
    derive_session_title,
    list_sessions,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def override_get_db_session():
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
    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def student_token(client):
    resp = await client.post(
        "/auth/login", json={"email": "student@demo.com", "password": "password123"}
    )
    return resp.json()["access_token"]


async def _seed_course(name: str = "Earth Science") -> uuid.UUID:
    async with _Session() as db:
        owner = (
            await db.execute(select(User).where(User.email == "teacher@demo.com"))
        ).scalar_one()
        course = Course(name=name, owner_id=owner.id, status="ready")
        db.add(course)
        await db.commit()
        return course.id


async def _student_id() -> uuid.UUID:
    async with _Session() as db:
        user = (
            await db.execute(select(User).where(User.email == "student@demo.com"))
        ).scalar_one()
        return user.id


async def _seed_session(course_id: uuid.UUID, *, messages=()) -> uuid.UUID:
    """A session with (role, content) messages, timestamped in order."""
    async with _Session() as db:
        student = (
            await db.execute(select(User).where(User.email == "student@demo.com"))
        ).scalar_one()
        session = TutoringSession(
            student_id=student.id, course_id=course_id, current_hint_level=0
        )
        db.add(session)
        await db.flush()
        base = datetime.now(timezone.utc)
        for i, (role, content) in enumerate(messages):
            db.add(
                TutoringMessage(
                    session_id=session.id,
                    role=role,
                    content=content,
                    hint_level=0,
                    created_at=base + timedelta(seconds=i),
                )
            )
        await db.commit()
        return session.id


# ── derive_session_title: pure ────────────────────────────────────────────────


def test_a_short_question_is_used_verbatim():
    assert derive_session_title("How does photosynthesis work?") == (
        "How does photosynthesis work?"
    )


def test_no_question_yet_yields_no_title():
    # A session created but never used. The UI labels this rather than
    # inventing a title for it.
    assert derive_session_title(None) is None


def test_a_blank_question_yields_no_title():
    assert derive_session_title("   \n\t ") is None


def test_a_long_question_is_truncated_with_an_ellipsis():
    question = (
        "Could you explain in detail how the light-dependent reactions of "
        "photosynthesis differ from the Calvin cycle in terms of inputs?"
    )
    title = derive_session_title(question)
    assert title is not None
    assert title.endswith("…")
    assert len(title) <= SESSION_TITLE_MAX_CHARS + 1


def test_truncation_lands_on_a_word_boundary():
    question = (
        "What is the difference between mitosis and meiosis in eukaryotic "
        "cells during the cell cycle overall"
    )
    title = derive_session_title(question)
    assert title is not None
    body = title.rstrip("…")
    assert question.startswith(body), "truncation must be a prefix of the question"
    assert question[len(body)] == " ", f"cut mid-word: {title!r}"


def test_a_single_enormous_token_is_still_cut():
    # No word boundary to prefer; returning the whole string would blow out the
    # card. This is why the boundary search has a floor.
    title = derive_session_title("A" * 300)
    assert title is not None
    assert len(title) <= SESSION_TITLE_MAX_CHARS + 1


def test_a_multiline_question_becomes_one_line():
    title = derive_session_title("Explain this:\n\n  - part one\n  - part two")
    assert title is not None
    assert "\n" not in title
    assert "  " not in title


def test_trailing_punctuation_is_trimmed_before_the_ellipsis():
    question = (
        "Explain the role of chlorophyll in absorbing light energy, and then, "
        "separately, describe the Calvin cycle."
    )
    title = derive_session_title(question)
    assert title is not None
    assert not title.rstrip("…").endswith(","), title


# ── list_sessions: DB-facing ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions_titles_from_the_first_student_message():
    course_id = await _seed_course()
    await _seed_session(
        course_id,
        messages=[
            ("student", "What causes the seasons on Earth?"),
            ("tutor", "What do you already know about axial tilt?"),
            ("student", "A later, different question about tides."),
        ],
    )
    async with _Session() as db:
        items = await list_sessions(await _student_id(), db)

    assert len(items) == 1
    assert items[0].title == "What causes the seasons on Earth?", (
        "the FIRST question names the session, not the most recent one"
    )
    assert items[0].message_count == 3
    assert items[0].course_name == "Earth Science"


@pytest.mark.asyncio
async def test_a_session_with_no_messages_has_no_title():
    course_id = await _seed_course()
    session_id = await _seed_session(course_id)
    async with _Session() as db:
        items = await list_sessions(await _student_id(), db)

    assert items[0].session.id == session_id
    assert items[0].title is None
    assert items[0].message_count == 0
    # No messages, so the session's own creation time is the only activity.
    assert items[0].last_activity_at == items[0].session.created_at


@pytest.mark.asyncio
async def test_a_tutor_only_session_still_has_no_title():
    # Defensive: titles come from student turns only. A tutor message must
    # never become the name of the session.
    course_id = await _seed_course()
    await _seed_session(
        course_id,
        messages=[("tutor", "I can only help with this course's material.")],
    )
    async with _Session() as db:
        items = await list_sessions(await _student_id(), db)
    assert items[0].title is None
    assert items[0].message_count == 1


@pytest.mark.asyncio
async def test_each_session_gets_its_own_title():
    # The bug this guards: first-questions are fetched for all sessions in one
    # query, so a grouping mistake would give every card the same name.
    course_id = await _seed_course()
    first = await _seed_session(
        course_id, messages=[("student", "Question about photosynthesis")]
    )
    second = await _seed_session(
        course_id, messages=[("student", "Question about cellular respiration")]
    )
    async with _Session() as db:
        by_id = {i.session.id: i for i in await list_sessions(await _student_id(), db)}

    assert by_id[first].title == "Question about photosynthesis"
    assert by_id[second].title == "Question about cellular respiration"


@pytest.mark.asyncio
async def test_list_sessions_is_empty_for_a_student_with_none():
    async with _Session() as db:
        assert await list_sessions(await _student_id(), db) == []


@pytest.mark.asyncio
async def test_another_students_sessions_are_not_listed():
    course_id = await _seed_course()
    await _seed_session(course_id, messages=[("student", "Mine, not yours")])
    async with _Session() as db:
        assert await list_sessions(uuid.uuid4(), db) == []


@pytest.mark.asyncio
async def test_endpoint_returns_the_title_and_course_name(client, student_token):
    course_id = await _seed_course()
    session_id = await _seed_session(
        course_id, messages=[("student", "Why is the sky blue?")]
    )

    resp = await client.get(
        "/tutor/sessions", headers={"Authorization": f"Bearer {student_token}"}
    )
    assert resp.status_code == 200, resp.text
    rows = [s for s in resp.json() if s["id"] == str(session_id)]
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Why is the sky blue?"
    assert row["course_name"] == "Earth Science"
    assert row["message_count"] == 1
    assert row["last_activity_at"] is not None
    # Still returned for the detail view, just no longer a list-level label.
    assert row["current_hint_level"] == 0


@pytest.mark.asyncio
async def test_the_endpoint_no_longer_needs_the_uuid_to_name_a_session(
    client, student_token
):
    # The point of the whole change: a card can be rendered without falling
    # back to the id.
    course_id = await _seed_course()
    await _seed_session(course_id, messages=[("student", "Explain plate tectonics")])
    resp = await client.get(
        "/tutor/sessions", headers={"Authorization": f"Bearer {student_token}"}
    )
    assert all(s["title"] for s in resp.json())


# ── Published assessment cards ────────────────────────────────────────────────


async def _seed_published_assessment(
    course_id: uuid.UUID, title: str, *, points: float = 1.0, questions: int = 2
) -> uuid.UUID:
    async with _Session() as db:
        owner = (
            await db.execute(select(User).where(User.email == "teacher@demo.com"))
        ).scalar_one()
        assessment = Assessment(
            course_id=course_id,
            created_by=owner.id,
            title=title,
            status="published",
            published_at=datetime.now(timezone.utc),
        )
        db.add(assessment)
        await db.flush()
        for i in range(questions):
            db.add(
                Question(
                    assessment_id=assessment.id,
                    question_type="short_answer",
                    stem=f"Question {i}",
                    answer_key="{}",
                    max_points=points,
                    order_index=i,
                )
            )
        await db.commit()
        return assessment.id


@pytest.mark.asyncio
async def test_published_cards_carry_a_date_and_points(client, student_token):
    course_id = await _seed_course()
    await _seed_published_assessment(course_id, "Week 1 Quiz", points=2.5, questions=2)

    resp = await client.get(
        "/assessments/published",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200, resp.text
    card = resp.json()[0]
    assert card["published_at"] is not None
    assert card["question_count"] == 2
    assert card["total_points"] == 5.0


@pytest.mark.asyncio
async def test_two_same_titled_assessments_are_distinguishable(client, student_token):
    # The reported defect: identical cards with nothing to tell them apart.
    course_id = await _seed_course()
    first = await _seed_published_assessment(course_id, "Quiz", questions=2)
    second = await _seed_published_assessment(course_id, "Quiz", questions=5)

    resp = await client.get(
        "/assessments/published",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    cards = {c["id"]: c for c in resp.json()}
    assert cards[str(first)]["title"] == cards[str(second)]["title"] == "Quiz"
    # Something on the card must now differ, or the student is still guessing.
    distinguishing = {"published_at", "question_count", "total_points"}
    assert any(
        cards[str(first)][field] != cards[str(second)][field]
        for field in distinguishing
    ), "same-titled cards still render identically"
