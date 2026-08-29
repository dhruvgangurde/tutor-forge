"""
tests/test_db_integrity.py
--------------------------
Tests for the F7 + F20 integrity hardening.

F7 is verified behaviorally against the DB constraint (SQLite create_all applies
the same UniqueConstraint declared on the model). F20 is verified by asserting
the FK index set is present on the ORM metadata — the same names the Alembic
0002 migration creates on Postgres.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.security import hash_password
from db.models import Assessment, Course, Submission, User

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = async_sessionmaker(_engine, expire_on_commit=False)


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _seed(s) -> tuple[User, User, Assessment]:
    teacher = User(email=f"t-{uuid.uuid4()}@x.com", hashed_password=hash_password("pw"), role="teacher")
    student = User(email=f"s-{uuid.uuid4()}@x.com", hashed_password=hash_password("pw"), role="student")
    s.add_all([teacher, student])
    await s.flush()
    course = Course(name="C", owner_id=teacher.id, status="ready")
    s.add(course)
    await s.flush()
    assessment = Assessment(
        course_id=course.id, created_by=teacher.id, title="Quiz", status="published",
    )
    s.add(assessment)
    await s.flush()
    return teacher, student, assessment


# ── F7: submission uniqueness ─────────────────────────────────────────────────

async def test_duplicate_submission_rejected(session):
    _, student, assessment = await _seed(session)
    session.add(Submission(assessment_id=assessment.id, student_id=student.id))
    await session.commit()

    session.add(Submission(assessment_id=assessment.id, student_id=student.id))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_same_student_different_assessment_allowed(session):
    teacher, student, assessment_a = await _seed(session)
    assessment_b = Assessment(
        course_id=assessment_a.course_id, created_by=teacher.id,
        title="Quiz B", status="published",
    )
    session.add(assessment_b)
    await session.flush()

    session.add(Submission(assessment_id=assessment_a.id, student_id=student.id))
    session.add(Submission(assessment_id=assessment_b.id, student_id=student.id))
    await session.commit()  # must not raise


async def test_different_student_same_assessment_allowed(session):
    teacher, student_a, assessment = await _seed(session)
    student_b = User(email=f"s2-{uuid.uuid4()}@x.com", hashed_password=hash_password("pw"), role="student")
    session.add(student_b)
    await session.flush()

    session.add(Submission(assessment_id=assessment.id, student_id=student_a.id))
    session.add(Submission(assessment_id=assessment.id, student_id=student_b.id))
    await session.commit()  # must not raise


# ── F20: foreign-key indexes present on metadata ──────────────────────────────

def test_fk_indexes_declared():
    present = {
        ix.name for table in Base.metadata.tables.values() for ix in table.indexes
    }
    expected = {
        "ix_courses_owner_id",
        "ix_chapters_course_id",
        "ix_concepts_chapter_id",
        "ix_ingestion_jobs_course_id",
        "ix_tutoring_sessions_student_id",
        "ix_tutoring_sessions_course_id",
        "ix_tutoring_messages_session_id",
        "ix_assessments_course_id",
        "ix_assessments_created_by",
        "ix_questions_assessment_id",
        "ix_rubric_criteria_question_id",
        "ix_submissions_student_id",
        "ix_submission_responses_submission_id",
        "ix_submission_responses_question_id",
        "ix_final_grades_teacher_id",
    }
    missing = expected - present
    assert not missing, f"missing FK indexes: {sorted(missing)}"


def test_submission_unique_constraint_declared():
    names = {c.name for c in Submission.__table__.constraints if c.name}
    assert "uq_submissions_assessment_student" in names
