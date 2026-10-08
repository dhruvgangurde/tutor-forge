"""
tests/test_grading_finalized.py
-------------------------------
GET /grading/finalized: a teacher's released-grade history (frontend audit #13).

The Grading page only showed the pending queue; once a grade was approved or
overridden it vanished. This read-only list is ownership-scoped like the queue:
a teacher sees every final grade in their own courses and nothing else.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import hash_password
from db.models import Assessment, Course, FinalGrade, GradeRecommendation, Submission, User
from main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = async_sessionmaker(_engine, expire_on_commit=False)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


async def _override_db():
    async with _Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _seed_grade(db, *, owner, student, course_name, title, final, finalized_at, pending=False):
    course = Course(name=course_name, owner_id=owner.id, status="ready")
    db.add(course)
    await db.flush()
    a = Assessment(course_id=course.id, created_by=owner.id, title=title,
                   status="published", config=json.dumps({"topic": "t"}))
    db.add(a)
    await db.flush()
    sub = Submission(assessment_id=a.id, student_id=student.id, status="graded")
    db.add(sub)
    await db.flush()
    rec = GradeRecommendation(submission_id=sub.id, recommended_score=4.0, max_score=6.0,
                              status="pending_review" if pending else "approved")
    db.add(rec)
    await db.flush()
    if not pending:
        db.add(FinalGrade(recommendation_id=rec.id, teacher_id=owner.id, final_score=final,
                          action="approved", finalized_at=finalized_at))


@pytest.fixture(autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as db:
        teacher = User(email="teacher@demo.com", hashed_password=hash_password("password123"), role="teacher")
        other = User(email="other@demo.com", hashed_password=hash_password("password123"), role="teacher")
        student = User(email="student@demo.com", hashed_password=hash_password("password123"), role="student")
        db.add_all([teacher, other, student])
        await db.flush()
        await _seed_grade(db, owner=teacher, student=student, course_name="Mine A", title="Older quiz",
                          final=3.0, finalized_at=T0)
        await _seed_grade(db, owner=teacher, student=student, course_name="Mine B", title="Newer quiz",
                          final=5.5, finalized_at=T0 + timedelta(days=1))
        await _seed_grade(db, owner=teacher, student=student, course_name="Mine C", title="Still pending",
                          final=0, finalized_at=T0, pending=True)
        await _seed_grade(db, owner=other, student=student, course_name="Theirs", title="Other teacher quiz",
                          final=6.0, finalized_at=T0 + timedelta(days=2))
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


async def _headers(client, email: str) -> dict:
    resp = await client.post("/auth/login", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_teacher_sees_own_released_grades_newest_first(client):
    resp = await client.get("/grading/finalized", headers=await _headers(client, "teacher@demo.com"))
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["assessment_title"] for r in rows] == ["Newer quiz", "Older quiz"]
    newest = rows[0]
    assert newest["student_email"] == "student@demo.com"
    assert newest["course_name"] == "Mine B"
    assert (newest["final_score"], newest["max_score"]) == (5.5, 6.0)
    assert newest["released"] is True
    assert newest["action"] == "approved"


async def test_pending_recommendations_are_not_listed(client):
    resp = await client.get("/grading/finalized", headers=await _headers(client, "teacher@demo.com"))
    assert "Still pending" not in [r["assessment_title"] for r in resp.json()]


async def test_other_teachers_grades_never_appear(client):
    mine = await client.get("/grading/finalized", headers=await _headers(client, "teacher@demo.com"))
    assert "Other teacher quiz" not in resp_titles(mine)
    theirs = await client.get("/grading/finalized", headers=await _headers(client, "other@demo.com"))
    assert resp_titles(theirs) == ["Other teacher quiz"]


async def test_limit_caps_the_list(client):
    resp = await client.get("/grading/finalized?limit=1", headers=await _headers(client, "teacher@demo.com"))
    assert resp_titles(resp) == ["Newer quiz"]
    too_big = await client.get("/grading/finalized?limit=1000", headers=await _headers(client, "teacher@demo.com"))
    assert too_big.status_code == 422


async def test_student_gets_403(client):
    resp = await client.get("/grading/finalized", headers=await _headers(client, "student@demo.com"))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Teacher role required."


async def test_requires_authentication(client):
    assert (await client.get("/grading/finalized")).status_code == 401


async def test_route_is_not_swallowed_by_the_submission_id_route(client):
    # /grading/{submission_id} is declared after; "finalized" must not be parsed as a UUID.
    resp = await client.get("/grading/finalized", headers=await _headers(client, "teacher@demo.com"))
    assert resp.status_code == 200
    async with _Session() as db:
        assert len((await db.execute(select(FinalGrade))).scalars().all()) == 3


def resp_titles(resp) -> list[str]:
    return [r["assessment_title"] for r in resp.json()]
