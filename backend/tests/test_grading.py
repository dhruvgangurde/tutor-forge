"""
tests/test_grading.py
---------------------
Endpoint-level authorization tests for the grading router (grading/router.py).

The router used to be the only one in the app with no role dependency: all five
endpoints took get_current_user, so a student reached the handlers and was
turned away only by course ownership -- GET /grading/queue answered a student
with 200 []. Every endpoint now declares Depends(require_teacher). These tests
pin that down from both sides:

  - a student gets 403 "Teacher role required." from all five, even on their
    own submission -- the role check, not the ownership check, rejects them;
  - the owning teacher still gets normal success from all five;
  - a teacher who does not own the course still hits the ownership guard
    (now a 404 identical to a missing submission, audit #4c), so the role
    dependency was added in front of that guard, not instead of it.

Same pattern as test_course_lifecycle.py: SQLite in-memory DB, rows seeded
directly. The grading graph itself is stubbed by conftest (stub_background_jobs).
"""

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import hash_password
from db.models import Assessment, Course, FinalGrade, GradeRecommendation, Question, Submission, User
from main import app

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
                User(email="other@demo.com", hashed_password=hash_password("password123"), role="teacher"),
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


async def _token(client, email: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "password123"})
    return resp.json()["access_token"]


@pytest.fixture
async def teacher_token(client):
    return await _token(client, "teacher@demo.com")


@pytest.fixture
async def other_teacher_token(client):
    return await _token(client, "other@demo.com")


@pytest.fixture
async def student_token(client):
    return await _token(client, "student@demo.com")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def submissions() -> dict[str, uuid.UUID]:
    """
    One course owned by teacher@demo.com with three published assessments, each
    answered once by student@demo.com (a student may submit an assessment only
    once, hence three):

      ungraded  -- no recommendation yet; what POST /grade accepts
      approve   -- pending_review recommendation, consumed by the approve test
      override  -- pending_review recommendation, consumed by the override test
    """
    async with _Session() as db:
        teacher = (await db.execute(select(User).where(User.email == "teacher@demo.com"))).scalar_one()
        student = (await db.execute(select(User).where(User.email == "student@demo.com"))).scalar_one()
        course = Course(name="Test Course", owner_id=teacher.id, status="ready")
        db.add(course)
        await db.flush()

        ids: dict[str, uuid.UUID] = {}
        for key in ("ungraded", "approve", "override"):
            a = Assessment(
                course_id=course.id, created_by=teacher.id, title=f"Quiz {key}",
                status="published", config=json.dumps({"topic": "joins"}),
            )
            db.add(a)
            await db.flush()
            db.add(Question(
                assessment_id=a.id, question_type="mcq", stem="Q?",
                options=json.dumps(["one", "two", "three", "four"]),
                answer_key=json.dumps({"correct_answer": "A"}),
                max_points=4.0, order_index=0,
            ))
            sub = Submission(
                assessment_id=a.id, student_id=student.id,
                status="pending_grading" if key == "ungraded" else "graded",
            )
            db.add(sub)
            await db.flush()
            if key != "ungraded":
                db.add(GradeRecommendation(
                    submission_id=sub.id, status="pending_review",
                    recommended_score=3.0, max_score=4.0,
                ))
            ids[key] = sub.id
        await db.commit()
        return ids


# (method, path template, json body) for each of the five grading endpoints.
# Bodies are valid, so a rejection can only come from authorization.
_ENDPOINTS = {
    "queue": ("GET", "/grading/queue", None),
    "detail": ("GET", "/grading/{sub}", None),
    "grade": ("POST", "/grading/{sub}/grade", None),
    "approve": ("POST", "/grading/{sub}/approve", {"note": "looks right"}),
    "override": ("POST", "/grading/{sub}/override", {"final_score": 2.0, "reason": "partial credit"}),
}


# ── Students are rejected by role, on every endpoint ──────────────────────────

@pytest.mark.parametrize("endpoint", list(_ENDPOINTS))
async def test_student_gets_role_403_from_every_grading_endpoint(
    client, student_token, submissions, endpoint
):
    method, path, body = _ENDPOINTS[endpoint]
    # The student's OWN submission: an ownership-only guard would still say
    # "You do not own the course", which is exactly the old behaviour.
    url = path.format(sub=submissions["approve"])
    resp = await client.request(method, url, json=body, headers=_auth(student_token))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Teacher role required."


async def test_student_role_rejection_changes_nothing(client, student_token, submissions):
    for endpoint in ("grade", "approve", "override"):
        method, path, body = _ENDPOINTS[endpoint]
        sub = submissions["ungraded" if endpoint == "grade" else endpoint]
        await client.request(method, path.format(sub=sub), json=body, headers=_auth(student_token))
    async with _Session() as db:
        assert (await db.execute(select(FinalGrade))).scalars().all() == []
        recs = (await db.execute(select(GradeRecommendation))).scalars().all()
        assert {r.status for r in recs} == {"pending_review"}


# ── The owning teacher still gets normal behaviour ────────────────────────────

async def test_teacher_queue_lists_pending_recommendations(client, teacher_token, submissions):
    resp = await client.get("/grading/queue", headers=_auth(teacher_token))
    assert resp.status_code == 200
    listed = {row["submission_id"] for row in resp.json()}
    assert listed == {str(submissions["approve"]), str(submissions["override"])}


async def test_teacher_can_read_a_recommendation(client, teacher_token, submissions):
    resp = await client.get(f"/grading/{submissions['approve']}", headers=_auth(teacher_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["submission_id"] == str(submissions["approve"])
    assert body["recommended_score"] == 3.0
    assert body["status"] == "pending_review"


async def test_teacher_can_trigger_grading(client, teacher_token, submissions):
    resp = await client.post(f"/grading/{submissions['ungraded']}/grade", headers=_auth(teacher_token))
    # 202, not 200: grading is enqueued and runs in the background by design.
    assert resp.status_code == 202
    assert resp.json()["status"] == "grading_in_progress"


async def test_teacher_can_approve(client, teacher_token, submissions):
    resp = await client.post(
        f"/grading/{submissions['approve']}/approve",
        json={"note": "looks right"}, headers=_auth(teacher_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "approved"
    assert body["final_score"] == 3.0


async def test_teacher_can_override(client, teacher_token, submissions):
    resp = await client.post(
        f"/grading/{submissions['override']}/override",
        json={"final_score": 2.0, "reason": "partial credit"}, headers=_auth(teacher_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "overridden"
    assert body["final_score"] == 2.0


# ── Ownership still applies to teachers ───────────────────────────────────────

async def test_other_teacher_queue_is_empty(client, other_teacher_token, submissions):
    resp = await client.get("/grading/queue", headers=_auth(other_teacher_token))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.parametrize("endpoint", ["detail", "grade", "approve", "override"])
async def test_other_teacher_gets_ownership_403(client, other_teacher_token, submissions, endpoint):
    # Name kept from before audit #4c; the ownership refusal is now a 404.
    method, path, body = _ENDPOINTS[endpoint]
    sub = submissions["ungraded" if endpoint == "grade" else "approve"]
    resp = await client.request(method, path.format(sub=sub), json=body, headers=_auth(other_teacher_token))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Submission not found."


@pytest.mark.parametrize("endpoint", ["detail", "grade", "approve", "override"])
async def test_other_teachers_submission_is_indistinguishable_from_a_missing_one(
    client, other_teacher_token, submissions, endpoint
):
    method, path, body = _ENDPOINTS[endpoint]
    real = submissions["ungraded" if endpoint == "grade" else "approve"]
    theirs = await client.request(method, path.format(sub=real), json=body, headers=_auth(other_teacher_token))
    missing = await client.request(
        method, path.format(sub=uuid.uuid4()), json=body, headers=_auth(other_teacher_token)
    )
    assert (theirs.status_code, theirs.json()["detail"]) == (missing.status_code, missing.json()["detail"])


@pytest.mark.parametrize("endpoint", ["detail", "grade", "approve", "override"])
async def test_student_still_gets_the_role_403_not_a_404(client, student_token, endpoint):
    # Role first: a student is told the route is for teachers, whatever the id.
    method, path, body = _ENDPOINTS[endpoint]
    resp = await client.request(method, path.format(sub=uuid.uuid4()), json=body, headers=_auth(student_token))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Teacher role required."


async def test_other_teacher_cannot_change_anything(client, other_teacher_token, submissions):
    for endpoint in ("approve", "override"):
        method, path, body = _ENDPOINTS[endpoint]
        await client.request(
            method, path.format(sub=submissions[endpoint]), json=body, headers=_auth(other_teacher_token)
        )
    async with _Session() as db:
        assert (await db.execute(select(FinalGrade))).scalars().all() == []
