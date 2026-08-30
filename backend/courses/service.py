"""
courses/service.py
------------------
Business logic for course creation and ingestion triggering.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from courses.schemas import ChapterWithConcepts, ConceptSummary, CourseStructure, CourseSummary
from db.models import Chapter, Concept, Course, IngestionJob, User

if TYPE_CHECKING:
    from retrieval.service import RetrievalService
    from main import GeminiProClient
    from langfuse import Langfuse


async def create_course(
    name: str,
    owner: User,
    files: list[dict],
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    retrieval_service: "RetrievalService",
    gemini_pro: "GeminiProClient",
    langfuse: "Langfuse" = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """
    Create a Course + IngestionJob and enqueue the ingestion graph as a BackgroundTask.
    Returns (course_id, job_id).
    """
    course = Course(name=name, owner_id=owner.id, status="pending")
    db.add(course)
    await db.flush()

    job = IngestionJob(course_id=course.id, status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(course)
    await db.refresh(job)

    background_tasks.add_task(
        _run_ingestion,
        course_id=course.id,
        job_id=job.id,
        files=files,
        retrieval_service=retrieval_service,
        gemini_pro=gemini_pro,
        langfuse=langfuse,
    )

    return course.id, job.id


async def _run_ingestion(
    course_id: uuid.UUID,
    job_id: uuid.UUID,
    files: list[dict],
    retrieval_service: "RetrievalService",
    gemini_pro: "GeminiProClient",
    langfuse: "Langfuse" = None,
) -> None:
    """BackgroundTask target: build and invoke the ingestion graph."""
    from agents.ingestion.graph import build_ingestion_graph
    from agents.ingestion.state import IngestionState
    from core.database import AsyncSessionFactory

    async with AsyncSessionFactory() as db:
        # Update job status to running
        result = await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        job = result.scalar_one_or_none()
        if job:
            job.status = "running"
            await db.commit()

        try:
            graph = build_ingestion_graph(
                retrieval_service=retrieval_service,
                gemini_pro=gemini_pro,
                db=db,
                langfuse=langfuse,
            )
            initial_state: IngestionState = {
                "job_id": job_id,
                "course_id": course_id,
                "files": files,
                "parsed_content": [],
                "hierarchy": {},
                "chunks": [],
                "status": "running",
                "error": None,
            }
            final_state = await graph.ainvoke(initial_state)

            # Nodes can fail gracefully (return status="failed") without
            # raising — the conditional edges route straight to END in that
            # case, so ainvoke() returns normally. We must check the
            # returned state explicitly to catch that case.
            if final_state.get("status") == "failed":
                await _mark_course_and_job_failed(
                    db=db,
                    course_id=course_id,
                    job_id=job_id,
                    error_message=final_state.get("error") or "Ingestion failed.",
                )
        except Exception as exc:
            # Mark failed on unexpected errors. Uses a fresh session because
            # `db` may be left in a bad state by whatever raised.
            async with AsyncSessionFactory() as err_db:
                await _mark_course_and_job_failed(
                    db=err_db,
                    course_id=course_id,
                    job_id=job_id,
                    error_message=str(exc),
                )


async def _mark_course_and_job_failed(
    db: AsyncSession,
    course_id: uuid.UUID,
    job_id: uuid.UUID,
    error_message: str,
) -> None:
    """Mark both the Course and its IngestionJob as failed, in one transaction."""
    job_result = await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    job = job_result.scalar_one_or_none()
    if job:
        job.status = "failed"
        job.error_message = error_message

    course_result = await db.execute(select(Course).where(Course.id == course_id))
    course = course_result.scalar_one_or_none()
    if course:
        course.status = "failed"

    await db.commit()


async def get_course_structure(
    course_id: uuid.UUID,
    db: AsyncSession,
) -> CourseStructure | None:
    """Return the full chapter/concept hierarchy for a course."""
    result = await db.execute(
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.chapters).selectinload(Chapter.concepts)
        )
    )
    course = result.scalar_one_or_none()
    if not course:
        return None

    chapters = [
        ChapterWithConcepts(
            id=ch.id,
            title=ch.title,
            order_index=ch.order_index,
            concepts=[
                ConceptSummary(
                    id=c.id,
                    name=c.name,
                    description=c.description,
                    difficulty=c.difficulty,
                    order_index=c.order_index,
                )
                for c in sorted(ch.concepts, key=lambda x: x.order_index)
            ],
        )
        for ch in sorted(course.chapters, key=lambda x: x.order_index)
    ]

    return CourseStructure(
        course_id=course.id,
        name=course.name,
        chapters=chapters,
    )
