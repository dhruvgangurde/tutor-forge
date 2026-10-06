"""
assessments/service.py
-----------------------
Business logic for assessment generation and retrieval.

Design:
  - create_assessment(): creates a "generating" placeholder, enqueues the
    LangGraph run as a BackgroundTask, returns the assessment_id immediately.
  - _run_assessment_graph(): BackgroundTask target. On success, the graph's
    persist_assessment_node UPDATEs the placeholder row with questions.
    On any failure, marks the placeholder as "failed" so the teacher is
    not left polling indefinitely.
  - List queries return summaries only (no questions) to avoid N+1 loads.
  - Detail queries eagerly load questions + rubric criteria.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from courses.enrollment import enrolled_course_ids
from db.models import Assessment, Course, Question

if TYPE_CHECKING:
    from retrieval.service import RetrievalService
    from langfuse import Langfuse

logger = logging.getLogger(__name__)

# Maximum characters stored in generation_error — avoids filling the column
# with huge stack traces or internal details that shouldn't reach the teacher.
_MAX_ERROR_LEN = 400


def _sanitise_error(exc: Exception) -> str:
    """
    Return a user-friendly, bounded error message from an exception.
    Never exposes internal paths, stack frames, or raw tracebacks.
    """
    msg = str(exc)
    if not msg or msg.lower() in {"none", "<no description>"}:
        msg = "An unexpected error occurred during assessment generation."
    if len(msg) > _MAX_ERROR_LEN:
        msg = msg[:_MAX_ERROR_LEN].rstrip() + "\u2026"  # ellipsis
    return msg


async def create_assessment(
    config: dict,
    course_id: uuid.UUID,
    teacher_id: uuid.UUID,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    retrieval_service: "RetrievalService",
    gemini_flash,
    langfuse: "Langfuse",
) -> uuid.UUID:
    """
    Create a "generating" placeholder Assessment row and enqueue the graph.

    Returns the assessment_id immediately so the teacher can poll status.
    The graph will UPDATE this row when generation completes or fails.
    """
    assessment = Assessment(
        course_id=course_id,
        created_by=teacher_id,
        title=config.get("title", "Untitled Assessment"),
        status="generating",
        config=json.dumps(config),
    )
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)

    background_tasks.add_task(
        _run_assessment_graph,
        assessment_id=assessment.id,
        course_id=course_id,
        teacher_id=teacher_id,
        config=config,
        retrieval_service=retrieval_service,
        gemini_flash=gemini_flash,
        langfuse=langfuse,
    )

    return assessment.id


async def _run_assessment_graph(
    assessment_id: uuid.UUID,
    course_id: uuid.UUID,
    teacher_id: uuid.UUID,
    config: dict,
    retrieval_service: "RetrievalService",
    gemini_flash,
    langfuse: "Langfuse",
) -> None:
    """
    BackgroundTask target — NOT meant to be awaited directly.

    Opens its own DB session (separate from the request session which
    is already closed by the time this runs). On any unhandled exception,
    marks the placeholder Assessment as "failed" so the teacher is not
    left waiting indefinitely.
    """
    from agents.assessment.graph import build_assessment_graph
    from agents.assessment.state import AssessmentState
    from core.database import AsyncSessionFactory

    async with AsyncSessionFactory() as db:
        try:
            graph = build_assessment_graph(
                retrieval_service=retrieval_service,
                gemini_flash=gemini_flash,
                db=db,
                langfuse=langfuse,
                assessment_id=assessment_id,
            )
            initial_state: AssessmentState = {
                "course_id": course_id,
                "teacher_id": teacher_id,
                "config": config,
                "retrieved_concepts": [],
                "retrieval_cache": {},
                "is_grounded": False,
                "questions": [],
                "bloom_tags": [],
                "distractors": [],
                "rubric_criteria": [],
                "answer_key": [],
                "assessment_id": assessment_id,
                "trace_id": None,
                "status": "running",
                "error": None,
            }
            final_state = await graph.ainvoke(initial_state)

            # Handle graceful failure: if graph returns with status="failed",
            # persist the error to database so frontend receives feedback
            if final_state.get("status") == "failed":
                error_msg = final_state.get("error") or "Assessment generation failed."
                try:
                    result = await db.execute(
                        select(Assessment).where(Assessment.id == assessment_id)
                    )
                    assessment = result.scalar_one_or_none()
                    if assessment:
                        assessment.status = "failed"
                        assessment.generation_error = error_msg
                        await db.commit()
                        logger.info(
                            "Assessment %s marked failed: %s",
                            assessment_id,
                            error_msg,
                        )
                except Exception:
                    logger.exception(
                        "Could not persist failure status for assessment %s",
                        assessment_id,
                    )

        except Exception as exc:
            logger.exception(
                "Assessment generation failed for assessment_id=%s: %s",
                assessment_id,
                exc,
            )
            # Mark placeholder as failed and store a user-friendly error message
            # so the teacher knows why generation did not complete.
            error_msg = _sanitise_error(exc)
            try:
                result = await db.execute(
                    select(Assessment).where(Assessment.id == assessment_id)
                )
                assessment = result.scalar_one_or_none()
                if assessment:
                    assessment.status = "failed"
                    assessment.generation_error = error_msg
                    await db.commit()
            except Exception:
                logger.exception(
                    "Could not mark assessment %s as failed after graph error.",
                    assessment_id,
                )


async def get_assessment_detail(
    assessment_id: uuid.UUID,
    db: AsyncSession,
) -> Assessment | None:
    """
    Load a single assessment with all questions and rubric criteria (eager join).
    Used for teacher draft preview.
    """
    result = await db.execute(
        select(Assessment)
        .where(Assessment.id == assessment_id)
        .options(
            selectinload(Assessment.questions).selectinload(Question.rubric_criteria)
        )
    )
    return result.scalar_one_or_none()


async def list_assessments_for_course(
    course_id: uuid.UUID,
    db: AsyncSession,
) -> list[tuple[Assessment, int]]:
    """
    Return (assessment, question_count) pairs for a course, newest first.

    Questions are NOT loaded — use get_assessment_detail() for the full draft.
    The count comes from a correlated scalar subquery so the list stays a single
    round trip: it keeps the original no-eager-loading intent (no N+1) while
    still reporting the real number of questions per assessment.
    """
    question_count = (
        select(func.count(Question.id))
        .where(Question.assessment_id == Assessment.id)
        .correlate(Assessment)
        .scalar_subquery()
    )
    result = await db.execute(
        select(Assessment, question_count.label("question_count"))
        .where(Assessment.course_id == course_id)
        .order_by(Assessment.created_at.desc())
    )
    return [(assessment, int(count or 0)) for assessment, count in result.all()]


async def list_published_assessments(
    course_id: uuid.UUID | None,
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
) -> list[Assessment]:
    """
    Return published assessments (student view) for courses the student is
    enrolled in.

    If course_id is provided, filter to that course.
    Questions are eagerly loaded for question_count calculation.
    Sorted newest first.
    """
    # Join Course to exclude archived ones. Archiving hides a course from new
    # student activity WITHOUT mutating assessment rows, so restore is a single
    # column write and can never leave assessments half-published.
    query = (
        select(Assessment)
        .options(selectinload(Assessment.questions))
        .join(Course, Assessment.course_id == Course.id)
        .where(
            Assessment.status == "published",
            Course.archived_at.is_(None),
            Course.id.in_(enrolled_course_ids(student_id)),
        )
    )
    if course_id:
        query = query.where(Assessment.course_id == course_id)
    query = query.order_by(Assessment.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())
