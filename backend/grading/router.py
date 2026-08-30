"""
grading/router.py
------------------
REST endpoints for the Grading Agent.

All endpoints require authentication. The approve/override endpoints additionally
require the caller to be the assessment's owning teacher (enforced via
_require_teacher_owns_submission).

Endpoints
---------
  POST /{submission_id}/grade        → 202 GradingAck
  GET  /queue                        → 200 list[GradingQueueItem]
  GET  /{submission_id}              → 200 GradingDetail (grouped by question)
  POST /{submission_id}/approve      → 200 FinalGradeResponse
  POST /{submission_id}/override     → 200 FinalGradeResponse
"""

import uuid
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.service import get_current_user
from core.dependencies import get_db_session, get_langfuse_client, get_retrieval_service, get_gemini_pro
from db.models import Assessment, Course, Submission, User
from grading.schemas import (
    ApproveRequest,
    FinalGradeResponse,
    GradingAck,
    GradingDetail,
    GradingQueueItem,
    OverrideRequest,
    EvidenceCitation,
    CriterionGrade,
    QuestionGrade,
)
from grading.service import (
    finalize_grade,
    get_grading_detail,
    list_grading_queue,
    trigger_grading,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/grading", tags=["grading"])


# ── Mapping helpers ───────────────────────────────────────────────────────────

def _to_citation(raw: dict) -> EvidenceCitation:
    """Map one stored citation dict onto the response schema."""
    return EvidenceCitation(
        text=raw.get("text", ""),
        source_file=raw.get("source_file", ""),
        page_or_slide=raw.get("page_or_slide"),
        confidence=float(raw.get("confidence", 0.0)),
    )


# ── Authorization helper ──────────────────────────────────────────────────────

async def _require_teacher_owns_submission(
    submission_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Submission:
    """
    Load the submission and verify that the requesting teacher owns the
    corresponding course.

    Raises 403 if the teacher does not own the course.
    Raises 404 if the submission does not exist.
    """
    result = await db.execute(
        select(Submission)
        .options(
            selectinload(Submission.assessment).selectinload(Assessment.course)
        )
        .where(Submission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found.")

    course: Course = submission.assessment.course
    if course.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own the course for this submission.",
        )
    return submission


# ── POST /{submission_id}/grade ───────────────────────────────────────────────

@router.post(
    "/{submission_id}/grade",
    response_model=GradingAck,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger AI grading for a submission",
    description=(
        "Enqueues AI-assisted grading for the specified submission. "
        "The grading runs asynchronously. Poll GET /{submission_id} for results. "
        "The resulting recommendation requires instructor review before release."
    ),
)
async def grade_submission(
    submission_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> GradingAck:
    """
    Validates the submission and enqueues a background grading task.
    Returns 202 immediately — grading runs asynchronously.
    """
    # Ownership check
    await _require_teacher_owns_submission(submission_id, current_user, db)

    try:
        await trigger_grading(
            submission_id=submission_id,
            db=db,
            app_state=request.app.state,
            background_tasks=background_tasks,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return GradingAck(
        submission_id=submission_id,
        status="grading_in_progress",
        message=(
            "Grading has been enqueued. "
            "Poll GET /grading/{submission_id} for the recommendation. "
            "All results require instructor review before they are released."
        ),
    )


# ── GET /queue ────────────────────────────────────────────────────────────────

@router.get(
    "/queue",
    response_model=list[GradingQueueItem],
    summary="Teacher review queue",
    description=(
        "Returns all grading recommendations with status 'pending_review' "
        "for assessments in courses owned by the requesting teacher."
    ),
)
async def get_grading_queue(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[GradingQueueItem]:
    try:
        items = await list_grading_queue(
            teacher_id=current_user.id,
            db=db,
        )
    except Exception:
        logger.exception("Error loading grading queue for teacher %s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load grading queue.",
        )

    return [
        GradingQueueItem(
            recommendation_id=item["recommendation_id"],
            submission_id=item["submission_id"],
            student_email=item.get("student_email"),
            assessment_title=item.get("assessment_title"),
            recommended_score=item["recommended_score"],
            max_score=item["max_score"],
            status=item["status"],
            submitted_at=item.get("submitted_at"),
        )
        for item in items
    ]


# ── GET /{submission_id} ──────────────────────────────────────────────────────

@router.get(
    "/{submission_id}",
    response_model=GradingDetail,
    summary="Grading recommendation detail",
    description=(
        "Returns the full grading recommendation for a submission, "
        "including per-criterion scores, suggested feedback, and evidence citations. "
        "The recommendation requires instructor approval before it is released."
    ),
)
async def get_grading_recommendation(
    submission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> GradingDetail:
    # Ownership check
    await _require_teacher_owns_submission(submission_id, current_user, db)

    try:
        detail = await get_grading_detail(submission_id=submission_id, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception:
        logger.exception("Error loading grading detail for submission %s", submission_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load grading recommendation.",
        )

    # ── Map the grouped question/criterion structure onto the schema ──────────
    # The grouping, stem hydration and ordering are done in
    # grading.service.group_question_grades; this loop is pure shape mapping.
    questions_out: list[QuestionGrade] = [
        QuestionGrade(
            question_id=entry.get("question_id"),
            question_type=entry.get("question_type", ""),
            stem=entry.get("stem"),
            criteria=[
                CriterionGrade(
                    criterion_id=crit.get("criterion_id"),
                    description=crit.get("description", ""),
                    score=float(crit.get("score", 0.0)),
                    max_points=float(crit.get("max_points", 1.0)),
                    feedback=crit.get("feedback", ""),
                    citations=[_to_citation(c) for c in crit.get("citations", [])],
                    requires_review=bool(crit.get("requires_review", False)),
                )
                for crit in entry.get("criteria", [])
            ],
        )
        for entry in detail.get("questions", [])
    ]

    all_citations = [_to_citation(c) for c in detail.get("evidence_citations", [])]

    return GradingDetail(
        recommendation_id=detail["recommendation_id"],
        submission_id=detail["submission_id"],
        student_email=detail.get("student_email"),
        assessment_title=detail.get("assessment_title"),
        recommended_score=detail["recommended_score"],
        max_score=detail["max_score"],
        status=detail["status"],
        questions=questions_out,
        evidence_citations=all_citations,
        created_at=detail["created_at"],
    )


# ── POST /{submission_id}/approve ─────────────────────────────────────────────

@router.post(
    "/{submission_id}/approve",
    response_model=FinalGradeResponse,
    summary="Approve AI grading recommendation",
    description=(
        "Teacher explicitly approves the AI-recommended score. "
        "The recommended score is finalized as-is. "
        "An optional note can be attached for the audit record. "
        "This action is irreversible."
    ),
)
async def approve_grade(
    submission_id: uuid.UUID,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> FinalGradeResponse:
    sub = await _require_teacher_owns_submission(submission_id, current_user, db)

    # Load the recommendation to get recommended_score
    from db.models import GradeRecommendation
    result = await db.execute(
        select(GradeRecommendation).where(
            GradeRecommendation.submission_id == submission_id
        )
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No grading recommendation found. Run POST /grade first.",
        )

    try:
        final = await finalize_grade(
            submission_id=submission_id,
            teacher_id=current_user.id,
            action="approved",
            final_score=rec.recommended_score,
            teacher_note=body.note,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return FinalGradeResponse(**final)


# ── POST /{submission_id}/override ────────────────────────────────────────────

@router.post(
    "/{submission_id}/override",
    response_model=FinalGradeResponse,
    summary="Override AI grading recommendation",
    description=(
        "Teacher overrides the AI recommendation with a custom score. "
        "A reason is required for audit transparency. "
        "This action is irreversible."
    ),
)
async def override_grade(
    submission_id: uuid.UUID,
    body: OverrideRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> FinalGradeResponse:
    await _require_teacher_owns_submission(submission_id, current_user, db)

    try:
        final = await finalize_grade(
            submission_id=submission_id,
            teacher_id=current_user.id,
            action="overridden",
            final_score=body.final_score,
            teacher_note=body.reason,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return FinalGradeResponse(**final)
