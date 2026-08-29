"""
core/job_recovery.py
--------------------
Startup recovery for orphaned background jobs (F4-minimal).

A background task (assessment generation, course ingestion) runs in-process via
FastAPI BackgroundTasks. If the worker process dies mid-run, the corresponding
row is stranded in an in-flight state forever ("generating" / "pending" /
"running"). This module reaps those on startup: any in-flight row older than a
configurable staleness window is marked "failed" with an explanatory message so
the user is not left polling indefinitely.

This is deliberately NOT a distributed queue — it is a single-process safety
net. It is written as a pure function over a DB session, so the planned Phase-3
queue can reuse it as a periodic reaper without change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings

logger = logging.getLogger(__name__)

_ASSESSMENT_MSG = (
    "Generation did not complete — the worker was interrupted. Please generate "
    "the assessment again."
)
_INGESTION_MSG = (
    "Ingestion did not complete — the worker was interrupted. Please re-upload "
    "the course."
)


def _as_aware_utc(dt: datetime | None) -> datetime | None:
    """
    Normalize a timestamp to timezone-aware UTC.

    Postgres returns tz-aware datetimes; SQLite (tests) returns naive ones. Our
    timestamps are always written in UTC (db.models._now), so a naive value is
    treated as UTC. This keeps the age comparison correct on both backends.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def recover_stale_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> dict[str, int]:
    """
    Mark orphaned in-flight jobs as failed. Returns counts per job type.

    A row is orphaned when its in-flight timestamp is older than
    ``now - stale_after_seconds``. Rows with a missing timestamp are treated as
    stale (defensive). Commits once at the end; a no-op run makes no writes.
    """
    now = now or datetime.now(timezone.utc)
    window = stale_after_seconds if stale_after_seconds is not None else settings.job_stale_after_seconds
    cutoff = now - timedelta(seconds=window)

    from db.models import Assessment, Course, IngestionJob

    counts = {"assessments": 0, "ingestion_jobs": 0}

    # ── Assessments stuck in "generating" ─────────────────────────────────────
    result = await db.execute(
        select(Assessment).where(Assessment.status == "generating")
    )
    for assessment in result.scalars().all():
        ts = _as_aware_utc(assessment.created_at)
        if ts is None or ts < cutoff:
            assessment.status = "failed"
            assessment.generation_error = _ASSESSMENT_MSG
            counts["assessments"] += 1

    # ── Ingestion jobs stuck in "pending"/"running" (+ their course) ──────────
    result = await db.execute(
        select(IngestionJob).where(IngestionJob.status.in_(("pending", "running")))
    )
    stale_jobs = []
    for job in result.scalars().all():
        ts = _as_aware_utc(job.updated_at) or _as_aware_utc(job.created_at)
        if ts is None or ts < cutoff:
            job.status = "failed"
            job.error_message = _INGESTION_MSG
            counts["ingestion_jobs"] += 1
            stale_jobs.append(job)

    # Mark each stale job's course as failed too, so it leaves the "pending" UI.
    if stale_jobs:
        course_ids = {job.course_id for job in stale_jobs}
        course_result = await db.execute(
            select(Course).where(Course.id.in_(course_ids))
        )
        for course in course_result.scalars().all():
            if course.status in ("pending", "ingesting"):
                course.status = "failed"

    if counts["assessments"] or counts["ingestion_jobs"]:
        await db.commit()
        logger.warning("Recovered orphaned jobs at startup: %s", counts)

    return counts
