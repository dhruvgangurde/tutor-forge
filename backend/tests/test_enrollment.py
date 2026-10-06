"""
tests/test_enrollment.py
------------------------
Course enrollment: only students a teacher has assigned can reach a course.

Before enrollment existed, any self-registered student could list every ready
course, open tutoring sessions on it, and list, take and submit its assessments
-- and tutor citations return course text verbatim. These tests pin down:

  - an enrolled student reaches all four student surfaces;
  - an unenrolled student is refused on all four: the list endpoints simply
    omit the course, and the id-addressed endpoints answer 404 -- the same
    404 as an id that does not exist, matching the codebase's convention for
    course-scoped denials (_require_owned_course);
  - a student removed from a course loses tutor access on their existing
    session (403: the session is theirs), but keeps their history;
  - the owning teacher can list, add and remove students;
  - another teacher gets the ownership 404 from every roster route, and a
    student gets the 403 role check.

Same pattern as test_grading.py / test_course_lifecycle.py: SQLite in-memory
DB, rows seeded directly.
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
from db.models import Assessment, Course, Enrollment, Question, User
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
                User(email="outsider@demo.com", hashed_password=hash_password("password123"), role="student"),
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
    """student@demo.com -- enrolled in the seeded course."""
    return await _token(client, "student@demo.com")


@pytest.fixture
async def outsider_token(client):
    """outsider@demo.com -- a registered student who is NOT enrolled."""
    return await _token(client, "outsider@demo.com")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _user_id(email: str) -> uuid.UUID:
    async with _Session() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one().id


@pytest.fixture
async def course() -> dict[str, uuid.UUID]:
    """
    A ready course owned by teacher@demo.com with one published single-question
    assessment. student@demo.com is enrolled; outsider@demo.com is not.
    """
    async with _Session() as db:
        teacher = (await db.execute(select(User).where(User.email == "teacher@demo.com"))).scalar_one()
        student = (await db.execute(select(User).where(User.email == "student@demo.com"))).scalar_one()
        c = Course(name="SQL Joins", owner_id=teacher.id, status="ready")
        db.add(c)
        await db.flush()
        a = Assessment(
            course_id=c.id, created_by=teacher.id, title="Joins Quiz",
            status="published", config=json.dumps({"topic": "joins"}),
        )
        db.add(a)
        await db.flush()
        q = Question(
            assessment_id=a.id, question_type="mcq", stem="Which join keeps every left row?",
            options=json.dumps(["INNER", "LEFT", "CROSS", "FULL"]),
            answer_key=json.dumps({"correct_answer": "B"}),
            max_points=1.0, order_index=0,
        )
        db.add(q)
        db.add(Enrollment(course_id=c.id, student_id=student.id))
        await db.commit()
        return {"course_id": c.id, "assessment_id": a.id, "question_id": q.id}


def _submission(course: dict) -> dict:
    return {"responses": [{"question_id": str(course["question_id"]), "answer_choice": "B"}]}


# ── Enrolled student: all four surfaces work ──────────────────────────────────

async def test_enrolled_student_sees_the_course_in_available(client, student_token, course):
    resp = await client.get("/courses/available", headers=_auth(student_token))
    assert resp.status_code == 200
    assert [c["id"] for c in resp.json()] == [str(course["course_id"])]


async def test_enrolled_student_can_start_a_tutoring_session(client, student_token, course):
    resp = await client.post(
        "/tutor/sessions", json={"course_id": str(course["course_id"])}, headers=_auth(student_token)
    )
    assert resp.status_code == 201
    assert resp.json()["course_id"] == str(course["course_id"])


async def test_enrolled_student_sees_published_assessments(client, student_token, course):
    resp = await client.get("/assessments/published", headers=_auth(student_token))
    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()] == [str(course["assessment_id"])]


async def test_enrolled_student_can_take_and_submit(client, student_token, course):
    take = await client.get(f"/assessments/{course['assessment_id']}/take", headers=_auth(student_token))
    assert take.status_code == 200
    assert take.json()["questions"][0]["id"] == str(course["question_id"])

    submit = await client.post(
        f"/assessments/{course['assessment_id']}/submit", json=_submission(course),
        headers=_auth(student_token),
    )
    assert submit.status_code == 201


# ── Unenrolled student: refused on all four ───────────────────────────────────

async def test_unenrolled_student_does_not_see_the_course_in_available(client, outsider_token, course):
    resp = await client.get("/courses/available", headers=_auth(outsider_token))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_unenrolled_student_cannot_start_a_tutoring_session(client, outsider_token, course):
    resp = await client.post(
        "/tutor/sessions", json={"course_id": str(course["course_id"])}, headers=_auth(outsider_token)
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Course not found."


async def test_unenrolled_session_refusal_matches_a_nonexistent_course(client, outsider_token, course):
    real = await client.post(
        "/tutor/sessions", json={"course_id": str(course["course_id"])}, headers=_auth(outsider_token)
    )
    fake = await client.post(
        "/tutor/sessions", json={"course_id": str(uuid.uuid4())}, headers=_auth(outsider_token)
    )
    # Indistinguishable, so the endpoint cannot be used to probe which ids exist.
    assert (real.status_code, real.json()) == (fake.status_code, fake.json())


async def test_unenrolled_student_does_not_see_published_assessments(client, outsider_token, course):
    resp = await client.get("/assessments/published", headers=_auth(outsider_token))
    assert resp.status_code == 200
    assert resp.json() == []
    # Naming the course explicitly does not get around the filter.
    resp = await client.get(
        "/assessments/published", params={"course_id": str(course["course_id"])},
        headers=_auth(outsider_token),
    )
    assert resp.json() == []


async def test_unenrolled_student_cannot_take_an_assessment(client, outsider_token, course):
    resp = await client.get(f"/assessments/{course['assessment_id']}/take", headers=_auth(outsider_token))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Assessment not found or not published."
    assert "Which join" not in resp.text


async def test_unenrolled_take_refusal_matches_a_nonexistent_assessment(client, outsider_token, course):
    real = await client.get(f"/assessments/{course['assessment_id']}/take", headers=_auth(outsider_token))
    fake = await client.get(f"/assessments/{uuid.uuid4()}/take", headers=_auth(outsider_token))
    assert (real.status_code, real.json()) == (fake.status_code, fake.json())


async def test_unenrolled_student_cannot_submit(client, outsider_token, course):
    resp = await client.post(
        f"/assessments/{course['assessment_id']}/submit", json=_submission(course),
        headers=_auth(outsider_token),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Assessment not found or not published."


# ── Removal revokes access but keeps the student's history ────────────────────

async def test_removed_student_loses_tutor_access_on_an_existing_session(
    client, teacher_token, student_token, course
):
    created = await client.post(
        "/tutor/sessions", json={"course_id": str(course["course_id"])}, headers=_auth(student_token)
    )
    session_id = created.json()["session_id"]
    student_id = await _user_id("student@demo.com")
    removed = await client.delete(
        f"/courses/{course['course_id']}/enrollments/{student_id}", headers=_auth(teacher_token)
    )
    assert removed.status_code == 200

    for path in (f"/tutor/sessions/{session_id}/chat", f"/tutor/sessions/{session_id}/hint"):
        body = {"question": "What does a LEFT JOIN return?"} if path.endswith("chat") else None
        resp = await client.post(path, json=body, headers=_auth(student_token))
        # The session is the student's own, so 403 rather than an existence-hiding 404.
        assert resp.status_code == 403
        assert resp.json()["detail"] == "You are not enrolled in this course."

    # Their own history stays readable.
    history = await client.get(f"/tutor/sessions/{session_id}/messages", headers=_auth(student_token))
    assert history.status_code == 200


async def test_removed_student_keeps_their_submissions(client, teacher_token, student_token, course):
    await client.post(
        f"/assessments/{course['assessment_id']}/submit", json=_submission(course),
        headers=_auth(student_token),
    )
    student_id = await _user_id("student@demo.com")
    await client.delete(
        f"/courses/{course['course_id']}/enrollments/{student_id}", headers=_auth(teacher_token)
    )
    assert (await client.get("/courses/available", headers=_auth(student_token))).json() == []
    mine = await client.get("/assessments/my-submissions", headers=_auth(student_token))
    assert [s["assessment_id"] for s in mine.json()] == [str(course["assessment_id"])]


# ── Teacher manages the roster of their own course ────────────────────────────

async def test_teacher_lists_the_roster(client, teacher_token, course):
    resp = await client.get(f"/courses/{course['course_id']}/enrollments", headers=_auth(teacher_token))
    assert resp.status_code == 200
    roster = resp.json()
    assert [r["email"] for r in roster] == ["student@demo.com"]
    assert set(roster[0]) == {"student_id", "email", "enrolled_at"}


async def test_teacher_enrolls_a_student_who_then_gains_access(
    client, teacher_token, outsider_token, course
):
    resp = await client.post(
        f"/courses/{course['course_id']}/enrollments",
        json={"email": "outsider@demo.com"}, headers=_auth(teacher_token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "outsider@demo.com"
    assert body["student_id"] == str(await _user_id("outsider@demo.com"))

    roster = await client.get(f"/courses/{course['course_id']}/enrollments", headers=_auth(teacher_token))
    assert {r["email"] for r in roster.json()} == {"student@demo.com", "outsider@demo.com"}

    available = await client.get("/courses/available", headers=_auth(outsider_token))
    assert [c["id"] for c in available.json()] == [str(course["course_id"])]
    take = await client.get(f"/assessments/{course['assessment_id']}/take", headers=_auth(outsider_token))
    assert take.status_code == 200


async def test_enroll_matches_email_case_insensitively(client, teacher_token, course):
    resp = await client.post(
        f"/courses/{course['course_id']}/enrollments",
        json={"email": "Outsider@Demo.com"}, headers=_auth(teacher_token),
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "outsider@demo.com"


async def test_enrolling_twice_is_a_conflict(client, teacher_token, course):
    resp = await client.post(
        f"/courses/{course['course_id']}/enrollments",
        json={"email": "student@demo.com"}, headers=_auth(teacher_token),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "That student is already enrolled in this course."


@pytest.mark.parametrize("email", ["nobody@demo.com", "other@demo.com"])
async def test_enrolling_an_unknown_or_non_student_email_is_404(client, teacher_token, course, email):
    # A teacher's email gets the same answer as an unregistered one, so the
    # roster form cannot be used to learn which addresses belong to teachers.
    resp = await client.post(
        f"/courses/{course['course_id']}/enrollments", json={"email": email}, headers=_auth(teacher_token)
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No student account with that email."


async def test_enroll_rejects_a_malformed_email(client, teacher_token, course):
    resp = await client.post(
        f"/courses/{course['course_id']}/enrollments", json={"email": "not-an-email"},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 422


async def test_teacher_removes_a_student_who_then_loses_access(
    client, teacher_token, student_token, course
):
    student_id = await _user_id("student@demo.com")
    resp = await client.delete(
        f"/courses/{course['course_id']}/enrollments/{student_id}", headers=_auth(teacher_token)
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "removed"

    roster = await client.get(f"/courses/{course['course_id']}/enrollments", headers=_auth(teacher_token))
    assert roster.json() == []
    assert (await client.get("/courses/available", headers=_auth(student_token))).json() == []
    take = await client.get(f"/assessments/{course['assessment_id']}/take", headers=_auth(student_token))
    assert take.status_code == 404

    again = await client.delete(
        f"/courses/{course['course_id']}/enrollments/{student_id}", headers=_auth(teacher_token)
    )
    assert again.status_code == 404
    assert again.json()["detail"] == "That student is not enrolled in this course."


# ── Another teacher: ownership check on every roster route ────────────────────

async def test_other_teacher_cannot_manage_the_roster(client, other_teacher_token, course):
    cid = course["course_id"]
    student_id = await _user_id("student@demo.com")
    responses = [
        await client.get(f"/courses/{cid}/enrollments", headers=_auth(other_teacher_token)),
        await client.post(
            f"/courses/{cid}/enrollments", json={"email": "outsider@demo.com"},
            headers=_auth(other_teacher_token),
        ),
        await client.delete(f"/courses/{cid}/enrollments/{student_id}", headers=_auth(other_teacher_token)),
    ]
    # _require_owned_course answers 404 so a foreign course id confirms nothing.
    for resp in responses:
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Course not found."
    assert "student@demo.com" not in responses[0].text

    # Nothing changed.
    async with _Session() as db:
        rows = (await db.execute(select(Enrollment).where(Enrollment.course_id == cid))).scalars().all()
    assert [r.student_id for r in rows] == [student_id]


# ── Students cannot reach the roster routes at all ────────────────────────────

async def test_student_cannot_manage_enrollment(client, student_token, course):
    cid = course["course_id"]
    outsider_id = await _user_id("outsider@demo.com")
    responses = [
        await client.get(f"/courses/{cid}/enrollments", headers=_auth(student_token)),
        await client.post(
            f"/courses/{cid}/enrollments", json={"email": "outsider@demo.com"},
            headers=_auth(student_token),
        ),
        await client.delete(f"/courses/{cid}/enrollments/{outsider_id}", headers=_auth(student_token)),
    ]
    for resp in responses:
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Teacher role required."

    async with _Session() as db:
        rows = (await db.execute(select(Enrollment).where(Enrollment.course_id == cid))).scalars().all()
    assert len(rows) == 1  # only the seeded enrollment
