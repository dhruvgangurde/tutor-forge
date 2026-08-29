"""
tests/test_job_recovery.py
--------------------------
Tests for startup job recovery (F4-minimal, core/job_recovery.py).

Seeds rows with explicit timestamps in an in-memory SQLite DB and drives the
reaper with a fixed `now` so staleness is deterministic. Covers the
"crash mid-job, restart, recover" path plus preservation of fresh/finished rows.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.job_recovery import recover_stale_jobs
from core.security import hash_password
from db.models import Assessment, Course, IngestionJob, User

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = async_sessionmaker(_engine, expire_on_commit=False)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(hours=1)      # older than the 1800s window
FRESH = NOW - timedelta(seconds=30)   # well within the window
WINDOW = 1800


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _seed_course(s, status: str = "pending") -> Course:
    user = User(
        email=f"t-{uuid.uuid4()}@x.com",
        hashed_password=hash_password("pw"),
        role="teacher",
    )
    s.add(user)
    await s.flush()
    course = Course(name="Course", owner_id=user.id, status=status)
    s.add(course)
    await s.flush()
    return course


async def _recover(s):
    return await recover_stale_jobs(s, now=NOW, stale_after_seconds=WINDOW)


# ── Assessments ───────────────────────────────────────────────────────────────

async def test_stale_generating_assessment_marked_failed(session):
    course = await _seed_course(session)
    a = Assessment(
        course_id=course.id, created_by=course.owner_id, title="A",
        status="generating", created_at=STALE,
    )
    session.add(a)
    await session.commit()

    counts = await _recover(session)

    await session.refresh(a)
    assert a.status == "failed"
    assert a.generation_error and "interrupted" in a.generation_error
    assert counts["assessments"] == 1


async def test_fresh_generating_assessment_preserved(session):
    course = await _seed_course(session)
    a = Assessment(
        course_id=course.id, created_by=course.owner_id, title="A",
        status="generating", created_at=FRESH,
    )
    session.add(a)
    await session.commit()

    counts = await _recover(session)

    await session.refresh(a)
    assert a.status == "generating"
    assert a.generation_error is None
    assert counts["assessments"] == 0


async def test_draft_assessment_ignored_even_if_old(session):
    course = await _seed_course(session)
    a = Assessment(
        course_id=course.id, created_by=course.owner_id, title="A",
        status="draft", created_at=STALE,
    )
    session.add(a)
    await session.commit()

    counts = await _recover(session)

    await session.refresh(a)
    assert a.status == "draft"          # not an in-flight state
    assert counts["assessments"] == 0


# ── Ingestion jobs (+ course) ─────────────────────────────────────────────────

async def test_stale_ingestion_job_and_course_marked_failed(session):
    course = await _seed_course(session, status="pending")
    job = IngestionJob(
        course_id=course.id, status="running", created_at=STALE, updated_at=STALE,
    )
    session.add(job)
    await session.commit()

    counts = await _recover(session)

    await session.refresh(job)
    await session.refresh(course)
    assert job.status == "failed"
    assert job.error_message and "interrupted" in job.error_message
    assert course.status == "failed"
    assert counts["ingestion_jobs"] == 1


async def test_fresh_ingestion_job_preserved(session):
    course = await _seed_course(session, status="pending")
    job = IngestionJob(
        course_id=course.id, status="pending", created_at=FRESH, updated_at=FRESH,
    )
    session.add(job)
    await session.commit()

    counts = await _recover(session)

    await session.refresh(job)
    await session.refresh(course)
    assert job.status == "pending"
    assert course.status == "pending"
    assert counts["ingestion_jobs"] == 0


async def test_completed_ingestion_job_ignored(session):
    course = await _seed_course(session, status="ready")
    job = IngestionJob(
        course_id=course.id, status="complete", created_at=STALE, updated_at=STALE,
    )
    session.add(job)
    await session.commit()

    counts = await _recover(session)

    await session.refresh(job)
    await session.refresh(course)
    assert job.status == "complete"
    assert course.status == "ready"     # untouched
    assert counts["ingestion_jobs"] == 0


# ── Restart scenario + idempotency ────────────────────────────────────────────

async def test_restart_recovers_only_orphaned_inflight_jobs(session):
    """Simulates a crash: a mix of stale in-flight, fresh in-flight, and done rows."""
    course = await _seed_course(session, status="pending")
    stale_a = Assessment(course_id=course.id, created_by=course.owner_id,
                         title="stale", status="generating", created_at=STALE)
    fresh_a = Assessment(course_id=course.id, created_by=course.owner_id,
                         title="fresh", status="generating", created_at=FRESH)
    stale_job = IngestionJob(course_id=course.id, status="running",
                             created_at=STALE, updated_at=STALE)
    session.add_all([stale_a, fresh_a, stale_job])
    await session.commit()

    counts = await _recover(session)

    await session.refresh(stale_a)
    await session.refresh(fresh_a)
    await session.refresh(stale_job)
    assert stale_a.status == "failed"
    assert fresh_a.status == "generating"
    assert stale_job.status == "failed"
    assert counts == {"assessments": 1, "ingestion_jobs": 1}


async def test_recovery_is_idempotent(session):
    course = await _seed_course(session)
    a = Assessment(course_id=course.id, created_by=course.owner_id, title="A",
                   status="generating", created_at=STALE)
    session.add(a)
    await session.commit()

    first = await _recover(session)
    second = await _recover(session)

    assert first["assessments"] == 1
    assert second["assessments"] == 0   # nothing left in-flight
