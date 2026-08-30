"""
tests/test_progress.py
-----------------------
Integration tests for the student progress endpoints.

Same harness as tests/test_assessments.py: SQLite in-memory, real routing and
authorization, services stubbed only where they reach outside the process.

The invariant these exist to protect: progress reflects RELEASED grades only. A
GradeRecommendation awaiting teacher review must never contribute a score here,
or the progress view becomes a way around the human-in-the-loop gate.
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
    Chapter,
    Concept,
    Course,
    FinalGrade,
    GradeRecommendation,
    Question,
    StudentConceptMastery,
    Submission,
    TutoringMessage,
    TutoringSession,
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
                User(
                    email="teacher@demo.com",
                    hashed_password=hash_password("password123"),
                    role="teacher",
                ),
                User(
                    email="student@demo.com",
                    hashed_password=hash_password("password123"),
                    role="student",
                ),
                User(
                    email="other@demo.com",
                    hashed_password=hash_password("password123"),
                    role="student",
                ),
            ]
        )
        await db.commit()
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


async def _token(client, email: str) -> str:
    resp = await client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    )
    return resp.json()["access_token"]


@pytest.fixture
async def student_token(client):
    return await _token(client, "student@demo.com")


@pytest.fixture
async def other_student_token(client):
    return await _token(client, "other@demo.com")


@pytest.fixture
async def teacher_token(client):
    return await _token(client, "teacher@demo.com")


# ── Seeding ───────────────────────────────────────────────────────────────────


async def _seed_course_with_concepts() -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A ready course with one chapter and two concepts."""
    async with _Session() as db:
        teacher = (
            await db.execute(select(User).where(User.email == "teacher@demo.com"))
        ).scalar_one()
        course = Course(name="Algorithms", owner_id=teacher.id, status="ready")
        db.add(course)
        await db.flush()
        chapter = Chapter(course_id=course.id, title="Searching", order_index=0)
        db.add(chapter)
        await db.flush()
        c1 = Concept(chapter_id=chapter.id, name="Binary Search", order_index=0)
        c2 = Concept(chapter_id=chapter.id, name="Graph Traversal", order_index=1)
        db.add_all([c1, c2])
        await db.commit()
        return course.id, [c1.id, c2.id]


async def _seed_graded_submission(
    course_id: uuid.UUID,
    student_email: str,
    *,
    concept_id: uuid.UUID | None,
    final_score: float,
    max_score: float,
    release: bool = True,
    title: str = "Quiz 1",
) -> uuid.UUID:
    """
    A submission with a recommendation, optionally released as a FinalGrade.

    ``release=False`` leaves it awaiting teacher review — the case that must
    never contribute a score to progress.
    """
    import json

    async with _Session() as db:
        teacher = (
            await db.execute(select(User).where(User.email == "teacher@demo.com"))
        ).scalar_one()
        student = (
            await db.execute(select(User).where(User.email == student_email))
        ).scalar_one()

        assessment = Assessment(
            course_id=course_id,
            created_by=teacher.id,
            title=title,
            status="published",
            config="{}",
        )
        db.add(assessment)
        await db.flush()

        question = Question(
            assessment_id=assessment.id,
            question_type="short_answer",
            stem="Explain it.",
            answer_key=json.dumps({"correct_answer": "x"}),
            max_points=max_score,
            order_index=0,
            concept_id=concept_id,
        )
        db.add(question)
        await db.flush()

        submission = Submission(
            assessment_id=assessment.id, student_id=student.id, status="graded"
        )
        db.add(submission)
        await db.flush()

        rec = GradeRecommendation(
            submission_id=submission.id,
            status="pending_review",
            recommended_score=final_score,
            max_score=max_score,
            rationale=json.dumps(
                {
                    "grading_method": "llm_rubric",
                    "questions": [
                        {
                            "question_id": str(question.id),
                            "question_type": "short_answer",
                            "criteria": [
                                {"score": final_score, "max_points": max_score}
                            ],
                        }
                    ],
                }
            ),
        )
        db.add(rec)
        await db.flush()

        if release:
            db.add(
                FinalGrade(
                    recommendation_id=rec.id,
                    teacher_id=teacher.id,
                    final_score=final_score,
                    action="approved",
                    finalized_at=datetime.now(timezone.utc),
                )
            )
            rec.status = "approved"
            if concept_id is not None:
                db.add(
                    StudentConceptMastery(
                        student_id=student.id,
                        concept_id=concept_id,
                        attempts=1,
                        earned_points=final_score,
                        possible_points=max_score,
                        mastery=final_score / max_score if max_score else 0.0,
                        last_graded_at=datetime.now(timezone.utc),
                    )
                )
        await db.commit()
        return submission.id


# ── GET /progress/me ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progress_is_empty_for_a_student_with_no_submissions(
    client, student_token
):
    resp = await client.get(
        "/progress/me", headers={"Authorization": f"Bearer {student_token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_released_grade_appears_in_the_summary(client, student_token):
    course_id, concepts = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id, "student@demo.com", concept_id=concepts[0], final_score=3.0, max_score=4.0
    )

    resp = await client.get(
        "/progress/me", headers={"Authorization": f"Bearer {student_token}"}
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["course_name"] == "Algorithms"
    assert row["assessments_graded"] == 1
    assert row["assessments_awaiting_grade"] == 0
    assert row["earned_points"] == 3.0
    assert row["possible_points"] == 4.0
    assert row["last_graded_at"] is not None


@pytest.mark.asyncio
async def test_unreleased_recommendation_contributes_no_score(client, student_token):
    """
    The load-bearing one. A submission the teacher has not acted on is counted
    as awaiting and adds nothing to earned/possible — otherwise the progress
    view would leak an un-approved AI score to the student.
    """
    course_id, concepts = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id,
        "student@demo.com",
        concept_id=concepts[0],
        final_score=4.0,
        max_score=4.0,
        release=False,
    )

    resp = await client.get(
        "/progress/me", headers={"Authorization": f"Bearer {student_token}"}
    )
    row = resp.json()[0]
    assert row["assessments_graded"] == 0
    assert row["assessments_awaiting_grade"] == 1
    assert row["earned_points"] == 0.0
    assert row["possible_points"] == 0.0
    assert row["last_graded_at"] is None


@pytest.mark.asyncio
async def test_graded_and_awaiting_are_counted_separately(client, student_token):
    course_id, concepts = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id, "student@demo.com", concept_id=concepts[0],
        final_score=2.0, max_score=2.0, title="Released",
    )
    await _seed_graded_submission(
        course_id, "student@demo.com", concept_id=concepts[1],
        final_score=5.0, max_score=5.0, release=False, title="Pending",
    )

    row = (
        await client.get(
            "/progress/me", headers={"Authorization": f"Bearer {student_token}"}
        )
    ).json()[0]
    assert row["assessments_graded"] == 1
    assert row["assessments_awaiting_grade"] == 1
    assert row["earned_points"] == 2.0  # not 7.0
    assert row["possible_points"] == 2.0


@pytest.mark.asyncio
async def test_a_student_never_sees_another_students_progress(
    client, student_token, other_student_token
):
    course_id, concepts = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id, "other@demo.com", concept_id=concepts[0], final_score=4.0, max_score=4.0
    )

    mine = await client.get(
        "/progress/me", headers={"Authorization": f"Bearer {student_token}"}
    )
    theirs = await client.get(
        "/progress/me", headers={"Authorization": f"Bearer {other_student_token}"}
    )
    assert mine.json() == []
    assert len(theirs.json()) == 1


@pytest.mark.asyncio
async def test_progress_rejects_a_teacher(client, teacher_token):
    resp = await client.get(
        "/progress/me", headers={"Authorization": f"Bearer {teacher_token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_progress_requires_auth(client):
    assert (await client.get("/progress/me")).status_code == 401


# ── GET /progress/me/courses/{course_id} ──────────────────────────────────────


@pytest.mark.asyncio
async def test_course_detail_returns_concept_mastery(client, student_token):
    course_id, concepts = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id, "student@demo.com", concept_id=concepts[0], final_score=3.0, max_score=4.0
    )

    resp = await client.get(
        f"/progress/me/courses/{course_id}",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["course_name"] == "Algorithms"
    assert len(data["concepts"]) == 1
    concept = data["concepts"][0]
    assert concept["concept_name"] == "Binary Search"
    assert concept["chapter_title"] == "Searching"
    assert concept["attempts"] == 1
    assert concept["mastery"] == pytest.approx(0.75)
    assert data["untagged_note"] is None


@pytest.mark.asyncio
async def test_course_detail_lists_released_results_only(client, student_token):
    course_id, concepts = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id, "student@demo.com", concept_id=concepts[0],
        final_score=3.0, max_score=4.0, title="Released",
    )
    await _seed_graded_submission(
        course_id, "student@demo.com", concept_id=concepts[1],
        final_score=9.0, max_score=9.0, release=False, title="Pending",
    )

    data = (
        await client.get(
            f"/progress/me/courses/{course_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
    ).json()
    titles = [r["assessment_title"] for r in data["results"]]
    assert titles == ["Released"]
    assert data["earned_points"] == 3.0
    assert data["possible_points"] == 4.0


@pytest.mark.asyncio
async def test_untagged_note_explains_an_empty_concept_list(client, student_token):
    """
    A student with released grades but no tagged questions must get an
    explanation, not a blank panel that looks broken.
    """
    course_id, _ = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id, "student@demo.com", concept_id=None, final_score=2.0, max_score=2.0
    )

    data = (
        await client.get(
            f"/progress/me/courses/{course_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
    ).json()
    assert data["concepts"] == []
    assert data["results"]
    assert "before questions were linked to concepts" in data["untagged_note"]


@pytest.mark.asyncio
async def test_course_detail_counts_tutoring_engagement(client, student_token):
    course_id, _ = await _seed_course_with_concepts()
    async with _Session() as db:
        student = (
            await db.execute(select(User).where(User.email == "student@demo.com"))
        ).scalar_one()
        session = TutoringSession(student_id=student.id, course_id=course_id)
        db.add(session)
        await db.flush()
        db.add_all(
            [
                TutoringMessage(session_id=session.id, role="student", content="q1"),
                TutoringMessage(session_id=session.id, role="tutor", content="a1"),
                TutoringMessage(session_id=session.id, role="student", content="q2"),
            ]
        )
        await db.commit()

    data = (
        await client.get(
            f"/progress/me/courses/{course_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
    ).json()
    assert data["tutoring_sessions"] == 1
    # Student questions only — the tutor's replies are not the student's work.
    assert data["tutoring_messages"] == 2


@pytest.mark.asyncio
async def test_course_detail_for_a_course_with_no_work_is_empty_not_404(
    client, student_token
):
    course_id, _ = await _seed_course_with_concepts()
    resp = await client.get(
        f"/progress/me/courses/{course_id}",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["concepts"] == []
    assert data["untagged_note"] is None  # nothing released, so nothing to explain


@pytest.mark.asyncio
async def test_course_detail_unknown_course_is_404(client, student_token):
    resp = await client.get(
        f"/progress/me/courses/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_course_detail_does_not_leak_another_students_mastery(
    client, student_token
):
    course_id, concepts = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_id, "other@demo.com", concept_id=concepts[0], final_score=4.0, max_score=4.0
    )

    data = (
        await client.get(
            f"/progress/me/courses/{course_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
    ).json()
    assert data["concepts"] == []
    assert data["results"] == []


@pytest.mark.asyncio
async def test_course_detail_rejects_a_teacher(client, teacher_token):
    course_id, _ = await _seed_course_with_concepts()
    resp = await client.get(
        f"/progress/me/courses/{course_id}",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403


# ── Mastery is written by finalize_grade ──────────────────────────────────────


@pytest.mark.asyncio
async def test_finalizing_a_grade_writes_concept_mastery(client):
    """
    End-to-end through the real finalize_grade(): approving a recommendation
    must move mastery, and only for the concept the question was tagged to.
    """
    import json

    from grading.service import finalize_grade

    course_id, concepts = await _seed_course_with_concepts()

    async with _Session() as db:
        teacher = (
            await db.execute(select(User).where(User.email == "teacher@demo.com"))
        ).scalar_one()
        student = (
            await db.execute(select(User).where(User.email == "student@demo.com"))
        ).scalar_one()
        assessment = Assessment(
            course_id=course_id, created_by=teacher.id, title="Q", status="published"
        )
        db.add(assessment)
        await db.flush()
        tagged = Question(
            assessment_id=assessment.id, question_type="short_answer", stem="a",
            max_points=4.0, order_index=0, concept_id=concepts[0],
        )
        untagged = Question(
            assessment_id=assessment.id, question_type="short_answer", stem="b",
            max_points=2.0, order_index=1, concept_id=None,
        )
        db.add_all([tagged, untagged])
        await db.flush()
        submission = Submission(
            assessment_id=assessment.id, student_id=student.id, status="graded"
        )
        db.add(submission)
        await db.flush()
        db.add(
            GradeRecommendation(
                submission_id=submission.id,
                status="pending_review",
                recommended_score=4.0,
                max_score=6.0,
                rationale=json.dumps(
                    {
                        "questions": [
                            {
                                "question_id": str(tagged.id),
                                "criteria": [{"score": 3.0, "max_points": 4.0}],
                            },
                            {
                                "question_id": str(untagged.id),
                                "criteria": [{"score": 1.0, "max_points": 2.0}],
                            },
                        ]
                    }
                ),
            )
        )
        await db.commit()
        submission_id, teacher_id, student_id = submission.id, teacher.id, student.id

    async with _Session() as db:
        await finalize_grade(
            submission_id=submission_id,
            teacher_id=teacher_id,
            action="approved",
            final_score=4.0,
            teacher_note=None,
            db=db,
        )

    async with _Session() as db:
        rows = (
            await db.execute(
                select(StudentConceptMastery).where(
                    StudentConceptMastery.student_id == student_id
                )
            )
        ).scalars().all()

    # Only the tagged question moved mastery; the untagged one contributed
    # nothing rather than being attributed to a guess.
    assert len(rows) == 1
    assert rows[0].concept_id == concepts[0]
    assert rows[0].earned_points == pytest.approx(3.0)
    assert rows[0].possible_points == pytest.approx(4.0)
    assert rows[0].mastery == pytest.approx(0.75)
    assert rows[0].attempts == 1


@pytest.mark.asyncio
async def test_mastery_accumulates_across_submissions(client):
    """A second graded submission on the same concept adds to the first."""
    import json

    from grading.service import finalize_grade

    course_id, concepts = await _seed_course_with_concepts()

    async def _one(score: float, possible: float, title: str):
        async with _Session() as db:
            teacher = (
                await db.execute(select(User).where(User.email == "teacher@demo.com"))
            ).scalar_one()
            student = (
                await db.execute(select(User).where(User.email == "student@demo.com"))
            ).scalar_one()
            assessment = Assessment(
                course_id=course_id, created_by=teacher.id, title=title, status="published"
            )
            db.add(assessment)
            await db.flush()
            q = Question(
                assessment_id=assessment.id, question_type="short_answer", stem="a",
                max_points=possible, order_index=0, concept_id=concepts[0],
            )
            db.add(q)
            await db.flush()
            sub = Submission(
                assessment_id=assessment.id, student_id=student.id, status="graded"
            )
            db.add(sub)
            await db.flush()
            db.add(
                GradeRecommendation(
                    submission_id=sub.id, status="pending_review",
                    recommended_score=score, max_score=possible,
                    rationale=json.dumps(
                        {
                            "questions": [
                                {
                                    "question_id": str(q.id),
                                    "criteria": [{"score": score, "max_points": possible}],
                                }
                            ]
                        }
                    ),
                )
            )
            await db.commit()
            sid, tid = sub.id, teacher.id
        async with _Session() as db:
            await finalize_grade(
                submission_id=sid, teacher_id=tid, action="approved",
                final_score=score, teacher_note=None, db=db,
            )

    await _one(2.0, 4.0, "First")
    await _one(4.0, 4.0, "Second")

    async with _Session() as db:
        row = (
            await db.execute(select(StudentConceptMastery))
        ).scalar_one()
    assert row.attempts == 2
    assert row.earned_points == pytest.approx(6.0)
    assert row.possible_points == pytest.approx(8.0)
    assert row.mastery == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_summary_orders_by_most_recent_activity(client, student_token):
    course_a, concepts_a = await _seed_course_with_concepts()
    course_b, concepts_b = await _seed_course_with_concepts()
    await _seed_graded_submission(
        course_a, "student@demo.com", concept_id=concepts_a[0],
        final_score=1.0, max_score=1.0, title="Older",
    )
    await _seed_graded_submission(
        course_b, "student@demo.com", concept_id=concepts_b[0],
        final_score=1.0, max_score=1.0, title="Newer",
    )
    # Nudge one course's release time back so the ordering is unambiguous.
    async with _Session() as db:
        grades = (await db.execute(select(FinalGrade))).scalars().all()
        grades[0].finalized_at = datetime.now(timezone.utc) - timedelta(days=3)
        await db.commit()

    rows = (
        await client.get(
            "/progress/me", headers={"Authorization": f"Bearer {student_token}"}
        )
    ).json()
    assert len(rows) == 2
    assert rows[0]["last_graded_at"] > rows[1]["last_graded_at"]
