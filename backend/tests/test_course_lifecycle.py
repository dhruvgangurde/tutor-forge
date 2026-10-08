"""
tests/test_course_lifecycle.py
-------------------------------
Course archive / restore / delete, and draft question editing.

The load-bearing rule these exist to protect: a hard delete cascades
courses -> assessments -> submissions -> grade_recommendations -> final_grades
-> grade_audit_records, and courses -> chapters -> concepts ->
student_concept_mastery. A FinalGrade is the only teacher-approved grade record
in the system. A teacher tidying their course list must not be able to erase a
student's marked work, so deletion is refused whenever student work exists and
archiving is offered instead.
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
from db.models import (
    Assessment,
    Chapter,
    Concept,
    Course,
    Enrollment,
    FinalGrade,
    GradeRecommendation,
    Question,
    RubricCriterion,
    Submission,
    User,
)
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


async def _seed_course(*, owner_email="teacher@demo.com", name="Test Course") -> uuid.UUID:
    async with _Session() as db:
        owner = (await db.execute(select(User).where(User.email == owner_email))).scalar_one()
        course = Course(name=name, owner_id=owner.id, status="ready")
        db.add(course)
        await db.flush()
        # Course access requires an enrollment; the seeded student is in this class.
        student = (await db.execute(select(User).where(User.email == "student@demo.com"))).scalar_one()
        db.add(Enrollment(course_id=course.id, student_id=student.id))
        await db.commit()
        return course.id


async def _seed_published_assessment(course_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    async with _Session() as db:
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
        a = Assessment(
            course_id=course_id, created_by=course.owner_id, title="Quiz",
            status="published", config=json.dumps({"topic": "binary search"}),
        )
        db.add(a)
        await db.flush()
        q = Question(
            assessment_id=a.id, question_type="mcq", stem="Q?",
            options=json.dumps(["one", "two", "three", "four"]),
            answer_key=json.dumps({"correct_answer": "A"}),
            max_points=1.0, order_index=0,
        )
        db.add(q)
        await db.commit()
        return a.id, q.id


async def _seed_graded_submission(course_id: uuid.UUID) -> None:
    """A submission with a released FinalGrade — the record deletion must not destroy."""
    async with _Session() as db:
        student = (await db.execute(select(User).where(User.email == "student@demo.com"))).scalar_one()
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
        a = (await db.execute(select(Assessment).where(Assessment.course_id == course_id))).scalars().first()
        sub = Submission(assessment_id=a.id, student_id=student.id, status="graded")
        db.add(sub)
        await db.flush()
        rec = GradeRecommendation(
            submission_id=sub.id, status="approved", recommended_score=1.0, max_score=1.0,
        )
        db.add(rec)
        await db.flush()
        db.add(FinalGrade(recommendation_id=rec.id, teacher_id=course.owner_id, final_score=1.0, action="approved"))
        await db.commit()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Archive ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_archives_by_default(client, teacher_token):
    course_id = await _seed_course()
    resp = await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))
    assert resp.status_code == 200
    assert resp.json()["action"] == "archived"

    async with _Session() as db:
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
    assert course.archived_at is not None, "the row must survive; only the flag changes"


@pytest.mark.asyncio
async def test_archiving_preserves_every_historical_row(client, teacher_token):
    course_id = await _seed_course()
    await _seed_published_assessment(course_id)
    await _seed_graded_submission(course_id)

    await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))

    async with _Session() as db:
        assert (await db.execute(select(Submission))).scalars().all()
        assert (await db.execute(select(FinalGrade))).scalars().all()
        assert (await db.execute(select(Assessment))).scalars().all()


@pytest.mark.asyncio
async def test_archive_is_idempotent_and_keeps_the_original_timestamp(client, teacher_token):
    course_id = await _seed_course()
    await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))
    async with _Session() as db:
        first = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one().archived_at

    await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))
    async with _Session() as db:
        second = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one().archived_at
    assert first == second, "a double-click must not overwrite when it was archived"


@pytest.mark.asyncio
async def test_restore_undoes_an_archive(client, teacher_token):
    course_id = await _seed_course()
    await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))
    resp = await client.post(f"/courses/{course_id}/restore", headers=_auth(teacher_token))
    assert resp.status_code == 200
    assert resp.json()["action"] == "restored"

    async with _Session() as db:
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
    assert course.archived_at is None


# ── Hard delete ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hard_delete_allowed_when_there_is_no_student_work(client, teacher_token):
    course_id = await _seed_course()
    await _seed_published_assessment(course_id)  # assessments alone do not block

    resp = await client.delete(f"/courses/{course_id}?hard=true", headers=_auth(teacher_token))
    assert resp.status_code == 200
    assert resp.json()["action"] == "deleted"

    async with _Session() as db:
        assert (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_hard_delete_refused_when_a_released_grade_exists(client, teacher_token):
    course_id = await _seed_course()
    await _seed_published_assessment(course_id)
    await _seed_graded_submission(course_id)

    resp = await client.delete(f"/courses/{course_id}?hard=true", headers=_auth(teacher_token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "submission" in detail.lower()
    assert "released grade" in detail.lower()
    assert "archive" in detail.lower(), "the refusal must name the safe alternative"

    # Nothing destroyed by the refused attempt.
    async with _Session() as db:
        assert (await db.execute(select(FinalGrade))).scalars().all()
        assert (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_an_ungraded_submission_also_blocks(client, teacher_token):
    # Student work that nobody has marked yet is still work they cannot resubmit.
    course_id = await _seed_course()
    _aid, _qid = await _seed_published_assessment(course_id)
    async with _Session() as db:
        student = (await db.execute(select(User).where(User.email == "student@demo.com"))).scalar_one()
        a = (await db.execute(select(Assessment))).scalars().first()
        db.add(Submission(assessment_id=a.id, student_id=student.id, status="pending_grading"))
        await db.commit()

    resp = await client.delete(f"/courses/{course_id}?hard=true", headers=_auth(teacher_token))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_deletion_impact_reports_the_counts_before_asking(client, teacher_token):
    course_id = await _seed_course()
    await _seed_published_assessment(course_id)
    await _seed_graded_submission(course_id)

    resp = await client.get(f"/courses/{course_id}/deletion-impact", headers=_auth(teacher_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_hard_delete"] is False
    assert data["impact"]["submissions"] == 1
    assert data["impact"]["final_grades"] == 1
    assert data["blocking_reason"]


@pytest.mark.asyncio
async def test_lifecycle_is_owner_scoped(client, other_teacher_token):
    course_id = await _seed_course()
    for call in (
        client.delete(f"/courses/{course_id}", headers=_auth(other_teacher_token)),
        client.post(f"/courses/{course_id}/restore", headers=_auth(other_teacher_token)),
        client.get(f"/courses/{course_id}/deletion-impact", headers=_auth(other_teacher_token)),
    ):
        assert (await call).status_code == 404


@pytest.mark.asyncio
async def test_students_cannot_archive_a_course(client, student_token):
    course_id = await _seed_course()
    resp = await client.delete(f"/courses/{course_id}", headers=_auth(student_token))
    assert resp.status_code == 403


# ── Archiving actually hides the course from students ─────────────────────────


@pytest.mark.asyncio
async def test_archived_course_disappears_from_student_surfaces(
    client, teacher_token, student_token
):
    course_id = await _seed_course()
    assessment_id, _qid = await _seed_published_assessment(course_id)

    # Visible before archiving.
    assert len((await client.get("/assessments/published", headers=_auth(student_token))).json()) == 1
    assert (await client.get(f"/assessments/{assessment_id}/take", headers=_auth(student_token))).status_code == 200

    await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))

    assert (await client.get("/assessments/published", headers=_auth(student_token))).json() == []
    assert (await client.get(f"/assessments/{assessment_id}/take", headers=_auth(student_token))).status_code == 404
    assert (
        await client.get("/courses/available", headers=_auth(student_token))
    ).json() == []


@pytest.mark.asyncio
async def test_restore_makes_the_course_available_again(client, teacher_token, student_token):
    course_id = await _seed_course()
    assessment_id, _qid = await _seed_published_assessment(course_id)
    await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))
    await client.post(f"/courses/{course_id}/restore", headers=_auth(teacher_token))

    # Restore is one column write because archiving never mutated assessments.
    assert (await client.get(f"/assessments/{assessment_id}/take", headers=_auth(student_token))).status_code == 200
    assert len((await client.get("/assessments/published", headers=_auth(student_token))).json()) == 1


@pytest.mark.asyncio
async def test_teacher_still_sees_their_archived_course_marked(client, teacher_token):
    course_id = await _seed_course()
    await client.delete(f"/courses/{course_id}", headers=_auth(teacher_token))

    rows = (await client.get("/courses", headers=_auth(teacher_token))).json()
    assert len(rows) == 1, "archiving tidies the list, it does not hide it from the owner"
    assert rows[0]["is_archived"] is True
    assert rows[0]["archived_at"] is not None


# ── Draft question editing ────────────────────────────────────────────────────


async def _seed_draft_with_questions(course_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A draft holding one MCQ and one short-answer question, both concept-tagged."""
    async with _Session() as db:
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
        chapter = Chapter(course_id=course_id, title="Searching", order_index=0)
        db.add(chapter)
        await db.flush()
        concept = Concept(chapter_id=chapter.id, name="Binary Search", order_index=0)
        db.add(concept)
        await db.flush()

        a = Assessment(
            course_id=course_id, created_by=course.owner_id, title="Draft Quiz",
            status="draft", config=json.dumps({"topic": "binary search"}),
        )
        db.add(a)
        await db.flush()
        mcq = Question(
            assessment_id=a.id, question_type="mcq", stem="Original stem?",
            options=json.dumps(["one", "two", "three", "four"]),
            answer_key=json.dumps({"correct_answer": "A", "worked_solution": "because"}),
            max_points=1.0, order_index=0, concept_id=concept.id,
        )
        sa = Question(
            assessment_id=a.id, question_type="short_answer", stem="Explain it.",
            answer_key=json.dumps({"correct_answer": "x"}),
            max_points=2.0, order_index=1,
        )
        db.add_all([mcq, sa])
        await db.flush()
        db.add(RubricCriterion(question_id=sa.id, description="Original criterion", max_points=2.0, order_index=0))
        await db.commit()
        return a.id, mcq.id, sa.id


@pytest.mark.asyncio
async def test_editing_a_stem_persists(client, teacher_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)

    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"stem": "A completely rewritten question about sorting?"},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200, resp.text
    assert "stem" in resp.json()["updated_fields"]

    async with _Session() as db:
        q = (await db.execute(select(Question).where(Question.id == qid))).scalar_one()
    assert q.stem == "A completely rewritten question about sorting?"


@pytest.mark.asyncio
async def test_editing_strips_a_pasted_letter_prefix_and_context_phrase(client, teacher_token):
    # A teacher pasting from the draft could reintroduce either Batch-1 defect,
    # so the edit path runs the same normalisation the generator does.
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)

    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={
            "stem": "When did humans emerge according to the context?",
            "options": ["A. first", "B. second", "C. third", "D. fourth"],
        },
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200, resp.text

    async with _Session() as db:
        q = (await db.execute(select(Question).where(Question.id == qid))).scalar_one()
    assert q.stem == "When did humans emerge?"
    assert json.loads(q.options) == ["first", "second", "third", "fourth"]


@pytest.mark.asyncio
async def test_editing_content_clears_a_now_stale_concept_tag(client, teacher_token):
    # The tag was written against the generated wording. A rewrite about a
    # different subject must not keep pointing at the old concept.
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)

    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"stem": "What is the capital city of Peru?"},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200
    assert resp.json()["concept_tag"] == "cleared"

    async with _Session() as db:
        q = (await db.execute(select(Question).where(Question.id == qid))).scalar_one()
    assert q.concept_id is None


@pytest.mark.asyncio
async def test_a_non_content_edit_leaves_the_tag_untouched(client, teacher_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)
    async with _Session() as db:
        before = (await db.execute(select(Question).where(Question.id == qid))).scalar_one().concept_id

    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"max_points": 3.0},
        headers=_auth(teacher_token),
    )
    assert resp.json()["concept_tag"] == "unchanged"

    async with _Session() as db:
        q = (await db.execute(select(Question).where(Question.id == qid))).scalar_one()
    assert q.concept_id == before
    assert q.max_points == 3.0


@pytest.mark.asyncio
async def test_editing_rubric_replaces_criteria_and_recomputes_points(client, teacher_token):
    course_id = await _seed_course()
    aid, _mcq, sa_id = await _seed_draft_with_questions(course_id)

    resp = await client.patch(
        f"/assessments/{aid}/questions/{sa_id}",
        json={
            "rubric_criteria": [
                {"description": "Names the mechanism", "max_points": 2.0},
                {"description": "Gives an example", "max_points": 1.0},
            ]
        },
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200, resp.text

    async with _Session() as db:
        q = (await db.execute(select(Question).where(Question.id == sa_id))).scalar_one()
        criteria = (
            await db.execute(select(RubricCriterion).where(RubricCriterion.question_id == sa_id))
        ).scalars().all()
    assert len(criteria) == 2, "old criteria are replaced, not appended to"
    assert q.max_points == 3.0, "points follow the rubric total when not set explicitly"


@pytest.mark.asyncio
async def test_published_assessments_cannot_be_edited(client, teacher_token):
    # The whole point of draft-only: a published paper must not change under
    # students who have already answered it.
    course_id = await _seed_course()
    aid, qid = await _seed_published_assessment(course_id)

    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"stem": "Changed after publishing"},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 409
    assert "draft" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_editing_is_owner_scoped(client, other_teacher_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"stem": "Not yours"},
        headers=_auth(other_teacher_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_students_cannot_edit_a_draft(client, student_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"stem": "Nope"},
        headers=_auth(student_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_empty_payload_is_rejected(client, teacher_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}", json={}, headers=_auth(teacher_token)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_teacher_draft_shows_the_answer_key(client, teacher_token):
    # A teacher has to see what the grader will accept before publishing: a
    # generated key can be wrong, and grading matches it exactly.
    course_id = await _seed_course()
    aid, mcq_id, sa_id = await _seed_draft_with_questions(course_id)
    resp = await client.get(f"/assessments/{aid}", headers=_auth(teacher_token))
    assert resp.status_code == 200
    by_id = {q["id"]: q for q in resp.json()["questions"]}
    assert by_id[str(mcq_id)]["correct_answer"] == "A"
    assert by_id[str(mcq_id)]["worked_solution"] == "because"
    assert by_id[str(sa_id)]["correct_answer"] == "x"


@pytest.mark.asyncio
async def test_correcting_the_mcq_key_persists_and_the_draft_shows_it(client, teacher_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"correct_answer": "c"},  # normalised to the letter the grader matches
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated_fields"] == ["correct_answer"]

    draft = await client.get(f"/assessments/{aid}", headers=_auth(teacher_token))
    q = next(x for x in draft.json()["questions"] if x["id"] == str(qid))
    assert q["correct_answer"] == "C"
    # The rest of the key survives a correct-answer edit.
    assert q["worked_solution"] == "because"


@pytest.mark.asyncio
async def test_unparseable_answer_key_shows_as_no_key_rather_than_failing(client, teacher_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)
    async with _Session() as db:
        q = (await db.execute(select(Question).where(Question.id == qid))).scalar_one()
        q.answer_key = "not json"
        await db.commit()
    resp = await client.get(f"/assessments/{aid}", headers=_auth(teacher_token))
    assert resp.status_code == 200
    q = next(x for x in resp.json()["questions"] if x["id"] == str(qid))
    assert q["correct_answer"] is None


async def _seed_numeric_question(assessment_id: uuid.UUID, key: str = "20") -> uuid.UUID:
    async with _Session() as db:
        q = Question(
            assessment_id=assessment_id, question_type="numeric", stem="How many?",
            answer_key=json.dumps({"correct_answer": key}), max_points=1.0, order_index=2,
        )
        db.add(q)
        await db.commit()
        return q.id


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["7", "3,27,38,43", "[3, 27, 38, 43]"])
async def test_numeric_key_accepts_a_number_or_a_list(client, teacher_token, key):
    course_id = await _seed_course()
    aid, _mcq, _sa = await _seed_draft_with_questions(course_id)
    qid = await _seed_numeric_question(aid)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}", json={"correct_answer": key},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_numeric_key_that_is_not_a_number_is_rejected_with_a_clear_message(client, teacher_token):
    # The grader can only compare numbers; anything else would score every
    # student 0. Rejected at save time, where the teacher can fix it.
    course_id = await _seed_course()
    aid, _mcq, _sa = await _seed_draft_with_questions(course_id)
    qid = await _seed_numeric_question(aid)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}", json={"correct_answer": "about twenty"},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 400
    assert "number or a comma-separated list of numbers" in resp.json()["detail"]
    async with _Session() as db:
        q = (await db.execute(select(Question).where(Question.id == qid))).scalar_one()
    assert json.loads(q.answer_key)["correct_answer"] == "20"  # unchanged


@pytest.mark.asyncio
async def test_mcq_correct_answer_must_be_a_letter(client, teacher_token):
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"correct_answer": "the second one"},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_options_rejected_on_a_non_mcq(client, teacher_token):
    course_id = await _seed_course()
    aid, _mcq, sa_id = await _seed_draft_with_questions(course_id)
    resp = await client.patch(
        f"/assessments/{aid}/questions/{sa_id}",
        json={"options": ["a", "b", "c", "d"]},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_edit_then_publish_reflects_the_edit(client, teacher_token, student_token):
    # The teacher must be publishing what they see.
    course_id = await _seed_course()
    aid, qid, _sa = await _seed_draft_with_questions(course_id)

    await client.patch(
        f"/assessments/{aid}/questions/{qid}",
        json={"stem": "Edited before publishing?"},
        headers=_auth(teacher_token),
    )
    assert (await client.patch(f"/assessments/{aid}/publish", headers=_auth(teacher_token))).status_code == 200

    take = await client.get(f"/assessments/{aid}/take", headers=_auth(student_token))
    stems = [q["stem"] for q in take.json()["questions"]]
    assert "Edited before publishing?" in stems


def test_blocking_reason_is_pluralised_properly():
    # Copy fix (frontend audit #10): "1 student submission", never "submission(s)".
    from courses.lifecycle import DeletionImpact

    zero = dict.fromkeys(
        ["assessments", "submissions", "recommendations", "final_grades", "audit_records",
         "tutoring_sessions", "tutoring_messages", "concept_mastery_rows"],
        0,
    )
    one = DeletionImpact(**{**zero, "submissions": 1, "final_grades": 1})
    many = DeletionImpact(**{**zero, "submissions": 3, "final_grades": 2, "audit_records": 2})
    assert one.describe() == "1 student submission, 1 released grade"
    assert many.describe() == "3 student submissions, 2 released grades, 2 grade audit records"
    assert "(s)" not in one.describe() + many.describe()
