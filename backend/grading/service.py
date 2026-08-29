"""
grading/service.py
-------------------
Service layer for the Grading Agent.

Responsibilities:
  1. trigger_grading()        — validate, dispatch BackgroundTask
  2. _run_grading_graph()     — async wrapper; catches exceptions; marks Submission
  3. get_grading_detail()     — load GradeRecommendation + parsed rationale for API
  4. list_grading_queue()     — teacher queue of submissions needing review
  5. finalize_grade()         — write FinalGrade + GradeAuditRecord (instructor action)

Architecture constraints:
  - The BackgroundTask owns its own DB session (never shares with the HTTP request).
  - GradeRecommendation is the agent's output; FinalGrade is instructor-only.
  - No grade is released to the student until finalize_grade() is called.
  - Temperature = 0 is enforced inside GeminiProClient.generate_deterministic().
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Assessment,
    FinalGrade,
    GradeAuditRecord,
    GradeRecommendation,
    Submission,
    SubmissionResponse,
    User,
)

logger = logging.getLogger(__name__)

_MAX_ERROR_LEN = 400


def _sanitise_error(exc: Exception) -> str:
    """Bounded, user-friendly error string — never exposes raw tracebacks."""
    msg = str(exc)
    if not msg or msg.lower() in {"none", "<no description>"}:
        msg = "An unexpected error occurred during grading."
    if len(msg) > _MAX_ERROR_LEN:
        msg = msg[:_MAX_ERROR_LEN].rstrip() + "…"
    return msg


# ── 1. trigger_grading ────────────────────────────────────────────────────────

async def trigger_grading(
    submission_id: uuid.UUID,
    db: AsyncSession,
    app_state,
    background_tasks,
) -> uuid.UUID:
    """
    Validate the submission and enqueue a background grading task.

    Returns the submission_id (caller uses this as the polling key).

    Raises ValueError on any validation failure (converted to 400 by the router).
    """
    # ── Validate submission exists and belongs to a published assessment ───────
    result = await db.execute(
        select(Submission)
        .options(selectinload(Submission.assessment))
        .where(Submission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise ValueError(f"Submission {submission_id} not found.")

    if submission.status == "graded":
        raise ValueError(
            "This submission has already been graded. "
            "Use the override endpoint to change the grade."
        )

    if submission.assessment.status != "published":
        raise ValueError("Cannot grade a submission for an unpublished assessment.")

    # ── Validate no duplicate GradeRecommendation ─────────────────────────────
    existing = await db.execute(
        select(GradeRecommendation).where(
            GradeRecommendation.submission_id == submission_id
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(
            "A grading recommendation already exists for this submission. "
            "Use the override endpoint to change the grade."
        )

    # ── Resolve course_id (needed by retrieval inside the graph) ──────────────
    course_id = submission.assessment.course_id

    # ── Schedule background grading ────────────────────────────────────────────
    background_tasks.add_task(
        _run_grading_graph,
        submission_id=submission_id,
        course_id=course_id,
        app_state=app_state,
    )

    logger.info(
        "Grading task enqueued for submission %s (course %s)",
        submission_id,
        course_id,
    )
    return submission_id


# ── 2. _run_grading_graph ─────────────────────────────────────────────────────

async def _run_grading_graph(
    submission_id: uuid.UUID,
    course_id: uuid.UUID,
    app_state,
) -> None:
    """
    Run the grading LangGraph inside a BackgroundTask.

    Owns its own DB session — never shares with the HTTP request session.
    On any exception: marks Submission.status = "pending_grading" (resets to
    retry-able state) so the teacher can re-trigger via the grade endpoint.
    """
    from agents.grading.graph import build_grading_graph
    from core.database import AsyncSessionFactory

    async with AsyncSessionFactory() as db:
        try:
            graph = build_grading_graph(
                retrieval_service=app_state.retrieval_service,
                gemini_pro=app_state.gemini_pro,
                db=db,
                langfuse=app_state.langfuse,
            )

            initial_state = {
                "submission_id": submission_id,
                "course_id": course_id,
                "responses": [],
                "evidence_per_question": [],
                "criterion_results": [],
                "total_score": 0.0,
                "max_score": 0.0,
                "recommendation_rationale": {},
                "recommendation_id": None,
                "trace_id": None,
                "status": "running",
                "error": None,
            }

            final_state = await graph.ainvoke(initial_state)

            if final_state.get("status") == "failed":
                logger.error(
                    "Grading graph finished with status=failed for submission %s: %s",
                    submission_id,
                    final_state.get("error"),
                )
                # Reset to pending_grading so teacher can re-trigger
                await _reset_submission_to_pending(db, submission_id)

        except Exception as exc:
            logger.exception(
                "Grading graph raised an unhandled exception for submission %s: %s",
                submission_id,
                exc,
            )
            await _reset_submission_to_pending(db, submission_id)


async def _reset_submission_to_pending(
    db: AsyncSession,
    submission_id: uuid.UUID,
) -> None:
    """Reset Submission status to 'pending_grading' so grading can be retried."""
    try:
        result = await db.execute(
            select(Submission).where(Submission.id == submission_id)
        )
        submission = result.scalar_one_or_none()
        if submission:
            submission.status = "pending_grading"
            await db.commit()
    except Exception:
        logger.exception(
            "Could not reset submission %s to pending_grading after grading failure.",
            submission_id,
        )


# ── 3. get_grading_detail ─────────────────────────────────────────────────────

async def get_grading_detail(
    submission_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """
    Load and parse the GradeRecommendation for a submission.

    Returns a plain dict for the router to serialise; raises ValueError
    if no recommendation exists yet.

    The rationale field (stored as JSON) is parsed and returned as a dict
    so the API can serve structured per-criterion data.
    """
    result = await db.execute(
        select(GradeRecommendation)
        .options(
            selectinload(GradeRecommendation.submission).selectinload(Submission.student),
            selectinload(GradeRecommendation.submission)
            .selectinload(Submission.assessment),
        )
        .where(GradeRecommendation.submission_id == submission_id)
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise ValueError(
            f"No grading recommendation found for submission {submission_id}. "
            "Grading may still be in progress."
        )

    rationale: dict = {}
    if rec.rationale:
        try:
            rationale = json.loads(rec.rationale)
        except json.JSONDecodeError:
            logger.warning("Could not parse rationale JSON for recommendation %s", rec.id)

    citations: list = []
    if rec.evidence_citations:
        try:
            citations = json.loads(rec.evidence_citations)
        except json.JSONDecodeError:
            pass

    return {
        "recommendation_id": rec.id,
        "submission_id": submission_id,
        "student_email": rec.submission.student.email if rec.submission and rec.submission.student else None,
        "assessment_title": rec.submission.assessment.title if rec.submission and rec.submission.assessment else None,
        "recommended_score": rec.recommended_score,
        "max_score": rec.max_score,
        "status": rec.status,
        "rationale": rationale,
        "evidence_citations": citations,
        "created_at": rec.created_at,
    }


# ── 4. list_grading_queue ─────────────────────────────────────────────────────

async def list_grading_queue(
    teacher_id: uuid.UUID,
    db: AsyncSession,
) -> list[dict]:
    """
    Return all GradeRecommendations with status='pending_review' for courses
    owned by this teacher.

    Joins: GradeRecommendation → Submission → Assessment → Course (owner check)
    """
    from db.models import Assessment, Course

    result = await db.execute(
        select(GradeRecommendation)
        .join(Submission, GradeRecommendation.submission_id == Submission.id)
        .join(Assessment, Submission.assessment_id == Assessment.id)
        .join(Course, Assessment.course_id == Course.id)
        .options(
            selectinload(GradeRecommendation.submission).selectinload(Submission.student),
            selectinload(GradeRecommendation.submission)
            .selectinload(Submission.assessment),
        )
        .where(
            GradeRecommendation.status == "pending_review",
            Course.owner_id == teacher_id,
        )
        .order_by(GradeRecommendation.created_at.asc())
    )
    recommendations = result.scalars().all()

    return [
        {
            "recommendation_id": rec.id,
            "submission_id": rec.submission_id,
            "student_email": rec.submission.student.email
            if rec.submission and rec.submission.student
            else None,
            "assessment_title": rec.submission.assessment.title
            if rec.submission and rec.submission.assessment
            else None,
            "recommended_score": rec.recommended_score,
            "max_score": rec.max_score,
            "status": rec.status,
            "submitted_at": rec.submission.submitted_at if rec.submission else None,
        }
        for rec in recommendations
    ]


# ── 5. finalize_grade ─────────────────────────────────────────────────────────

async def finalize_grade(
    submission_id: uuid.UUID,
    teacher_id: uuid.UUID,
    action: str,          # "approved" | "overridden"
    final_score: float,
    teacher_note: str | None,
    db: AsyncSession,
) -> dict:
    """
    Write FinalGrade + GradeAuditRecord in a single atomic transaction.

    This is the ONLY path to a released grade — enforcing the
    'instructor approval required before grade release' constraint.

    Raises ValueError on:
      - No pending recommendation for this submission
      - Recommendation already finalized
      - final_score > max_score
    """
    result = await db.execute(
        select(GradeRecommendation)
        .options(selectinload(GradeRecommendation.final_grade))
        .where(GradeRecommendation.submission_id == submission_id)
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise ValueError(
            f"No grading recommendation found for submission {submission_id}."
        )
    if rec.final_grade:
        raise ValueError("This submission has already been finalized.")

    if final_score < 0:
        raise ValueError("final_score cannot be negative.")
    if final_score > rec.max_score:
        raise ValueError(
            f"final_score ({final_score}) cannot exceed max_score ({rec.max_score})."
        )

    # ── INSERT FinalGrade ─────────────────────────────────────────────────────
    final_grade = FinalGrade(
        recommendation_id=rec.id,
        teacher_id=teacher_id,
        final_score=final_score,
        action=action,
        teacher_note=teacher_note,
        finalized_at=datetime.now(timezone.utc),
    )
    db.add(final_grade)
    await db.flush()  # get final_grade.id

    # ── INSERT GradeAuditRecord ───────────────────────────────────────────────
    audit = GradeAuditRecord(
        final_grade_id=final_grade.id,
        ai_recommended_score=rec.recommended_score,
        teacher_final_score=final_score,
        action_taken=action,
        reason=teacher_note,
        recorded_at=datetime.now(timezone.utc),
    )
    db.add(audit)

    # ── UPDATE GradeRecommendation.status ──────────────────────────────────────
    rec.status = action  # "approved" | "overridden"

    await db.commit()

    logger.info(
        "Grade finalized: submission=%s action=%s final_score=%.2f teacher=%s",
        submission_id,
        action,
        final_score,
        teacher_id,
    )

    return {
        "final_grade_id": final_grade.id,
        "final_score": final_score,
        "action": action,
        "finalized_at": final_grade.finalized_at,
    }
