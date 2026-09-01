"""
assessments/router.py
----------------------
REST endpoints for assessment generation, management, and student submission.

Routes (Teacher):
  POST   /assessments/generate                 — generate assessment (202)
  GET    /assessments/course/{course_id}       — list summaries for a course (owner-scoped)
  GET    /assessments/{assessment_id}          — draft detail for preview
  PATCH  /assessments/{assessment_id}/publish  — publish draft to students

Routes (Student):
  GET    /assessments/published                — list published assessments
  GET    /assessments/{assessment_id}/take     — full questions for taking it
  POST   /assessments/{assessment_id}/submit   — submit responses
  GET    /assessments/my-submissions           — list student's submissions
  GET    /assessments/submissions/{id}         — view submission detail

Authorization:
  - All endpoints require a valid JWT.
  - Teacher endpoints: require_teacher + course ownership verified.
  - Student endpoints: require_student + student ownership verified.

Error handling:
  - HTTPException is raised only in the router layer.
  - Business logic raises ValueError; core/exceptions.py converts to 400.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from assessments.schemas import (
    AssessmentDraft,
    AssessmentSummary,
    GenerateRequest,
    PublishedAssessmentSummary,
    QuestionDetail,
    RubricCriterionDetail,
    StudentAssessmentDetail,
    StudentQuestion,
    StudentRubricCriterion,
    StudentSubmissionDetail,
    StudentSubmissionResponse,
    StudentSubmissionSummary,
    SubmissionAck,
    QuestionUpdate,
    QuestionUpdateAck,
    SubmitRequest,
)
from assessments.service import (
    create_assessment,
    get_assessment_detail,
    list_assessments_for_course,
    list_published_assessments,
)
from agents.assessment.nodes import _strip_option_prefix
from auth.service import require_student, require_teacher
from core.context_phrasing import strip_context_references
from progress.tagging import load_course_concepts, retag_question_after_edit
from core.dependencies import (
    get_db_session,
    get_gemini_flash,
    get_langfuse_client,
    get_retrieval_service,
)
from db.models import (
    Assessment,
    Course,
    FinalGrade,
    GradeRecommendation,
    Question,
    RubricCriterion,
    Submission,
    SubmissionResponse,
    User,
)
from grading.service import trigger_grading

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assessments", tags=["assessments"])


# ── POST /assessments/generate ────────────────────────────────────────────────

@router.post(
    "/generate",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate a new assessment",
    description=(
        "Kick off grounded assessment generation as a background task. "
        "Returns assessment_id immediately; poll GET /assessments/{id} for status."
    ),
)
async def generate_assessment(
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
    retrieval_service=Depends(get_retrieval_service),
    gemini_flash=Depends(get_gemini_flash),
    langfuse=Depends(get_langfuse_client),
) -> dict:
    # ── Verify course ownership ───────────────────────────────────────────────
    result = await db.execute(select(Course).where(Course.id == body.course_id))
    course = result.scalar_one_or_none()
    if not course or course.owner_id != teacher.id:
        raise HTTPException(status_code=404, detail="Course not found.")
    if course.archived_at is not None:
        raise HTTPException(
            status_code=400,
            detail="This course is archived. Restore it before generating assessments.",
        )
    if course.status != "ready":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Course ingestion is not complete (status='{course.status}'). "
                "Please wait until the course is ready before generating assessments."
            ),
        )

    config = {
        "title": body.title,
        "topic": body.topic,
        "bloom_mix": body.bloom_mix,
        "type_mix": body.type_mix,
        "difficulty": body.difficulty,
        "count": body.count,
    }

    assessment_id = await create_assessment(
        config=config,
        course_id=body.course_id,
        teacher_id=teacher.id,
        db=db,
        background_tasks=background_tasks,
        retrieval_service=retrieval_service,
        gemini_flash=gemini_flash,
        langfuse=langfuse,
    )

    logger.info(
        "Assessment generation queued: assessment_id=%s course_id=%s teacher_id=%s",
        assessment_id,
        body.course_id,
        teacher.id,
    )

    return {
        "assessment_id": str(assessment_id),
        "status": "generating",
        "message": (
            "Assessment generation started. "
            "Poll GET /assessments/{assessment_id} for the draft."
        ),
    }


# ── GET /assessments/course/{course_id} ───────────────────────────────────────

@router.get(
    "/course/{course_id}",
    response_model=list[AssessmentSummary],
    summary="List assessments for a course",
)
async def list_course_assessments(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> list[AssessmentSummary]:
    """Return lightweight summaries of all assessments for a course."""
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course or course.owner_id != teacher.id:
        raise HTTPException(status_code=404, detail="Course not found.")

    assessments = await list_assessments_for_course(course_id, db)
    return [
        AssessmentSummary(
            id=a.id,
            title=a.title,
            status=a.status,
            course_id=a.course_id,
            # Questions are still not eager-loaded on the list query; the count
            # comes from a correlated COUNT subquery in the same statement.
            question_count=question_count,
            generation_error=a.generation_error,
            created_at=a.created_at,
        )
        for a, question_count in assessments
    ]


# ── GET /assessments/published ───────────────────────────────────────────────

@router.get(
    "/published",
    response_model=list[PublishedAssessmentSummary],
    summary="List published assessments (student view)",
)
async def list_published_assessments_endpoint(
    course_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> list[PublishedAssessmentSummary]:
    """
    Return published assessments available for students to take.

    Optional course_id query param filters to a single course.
    """
    assessments = await list_published_assessments(course_id, db)

    # Hydrate with course names
    course_ids = {a.course_id for a in assessments}
    courses_by_id = {}
    if course_ids:
        result = await db.execute(
            select(Course).where(Course.id.in_(course_ids))
        )
        courses_by_id = {c.id: c.name for c in result.scalars().all()}

    return [
        PublishedAssessmentSummary(
            id=a.id,
            title=a.title,
            course_id=a.course_id,
            course_name=courses_by_id.get(a.course_id, ""),
            question_count=len(a.questions or []),
            total_points=round(sum(q.max_points for q in (a.questions or [])), 2),
            published_at=a.published_at,
            created_at=a.created_at,
        )
        for a in assessments
    ]


# ── GET /assessments/my-submissions ──────────────────────────────────────────

@router.get(
    "/my-submissions",
    response_model=list[StudentSubmissionSummary],
    summary="List student's submissions",
)
async def list_student_submissions(
    course_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> list[StudentSubmissionSummary]:
    """
    Return all submissions for the requesting student.

    Optional course_id query param filters to a single course.
    Includes grading status and final score (if graded).
    """
    query = (
        select(Submission, Assessment, Course)
        .join(Assessment, Submission.assessment_id == Assessment.id)
        .join(Course, Assessment.course_id == Course.id)
        .options(
            selectinload(Submission.assessment).selectinload(Assessment.questions),
            selectinload(Submission.grade_recommendation).selectinload(GradeRecommendation.final_grade),
        )
        .where(Submission.student_id == student.id)
    )
    if course_id:
        query = query.where(Assessment.course_id == course_id)
    query = query.order_by(Submission.submitted_at.desc())

    result = await db.execute(query)
    submissions = []
    for submission, assessment, course in result:
        # Get final grade (already eager-loaded if exists)
        final_grade = submission.grade_recommendation.final_grade if submission.grade_recommendation else None

        # Calculate max_score from questions
        max_score = 0.0
        if assessment.questions:
            max_score = sum(q.max_points for q in assessment.questions)

        submissions.append(
            StudentSubmissionSummary(
                submission_id=submission.id,
                assessment_id=assessment.id,
                assessment_title=assessment.title,
                course_id=course.id,
                course_name=course.name,
                submitted_at=submission.submitted_at,
                status=submission.status,
                final_score=final_grade.final_score if final_grade else None,
                max_score=max_score if max_score > 0 else None,
            )
        )

    return submissions


# ── GET /assessments/{assessment_id}/take ─────────────────────────────────────

@router.get(
    "/{assessment_id}/take",
    response_model=StudentAssessmentDetail,
    summary="Get a published assessment's questions (student view)",
    description=(
        "Full question content for a student to answer. Published assessments "
        "only; answer keys are never included."
    ),
)
async def get_assessment_for_student(
    assessment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> StudentAssessmentDetail:
    """
    Return the questions a student needs in order to take this assessment.

    Why this exists separately from GET /{assessment_id}: that route is
    require_teacher and returns AssessmentDraft, so the take page 403'd for
    every student and fell through to its "Assessment Not Found" branch.

    Two guards, both load-bearing:
      - require_student, so the route can never serve teacher-shaped data.
      - status == "published". A draft or failed assessment must not be
        reachable by guessing an id, so anything not published is reported as
        not found rather than forbidden -- the same wording and status the
        submit handler already uses, which also avoids confirming that a draft
        with that id exists.
    """
    assessment = await get_assessment_detail(assessment_id, db)
    if not assessment or assessment.status != "published":
        raise HTTPException(
            status_code=404, detail="Assessment not found or not published."
        )
    # An archived course is not available to students; reported as not-found
    # for the same reason an unpublished assessment is.
    course = (
        await db.execute(select(Course).where(Course.id == assessment.course_id))
    ).scalar_one_or_none()
    if course is None or course.archived_at is not None:
        raise HTTPException(
            status_code=404, detail="Assessment not found or not published."
        )

    return _assessment_to_student_view(assessment)


# ── GET /assessments/{assessment_id} ──────────────────────────────────────────

@router.get(
    "/{assessment_id}",
    response_model=AssessmentDraft,
    summary="Get assessment draft for teacher preview",
)
async def get_assessment(
    assessment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> AssessmentDraft:
    """Return the full draft assessment including all questions and rubric criteria."""
    assessment = await get_assessment_detail(assessment_id, db)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    # Verify ownership via course
    result = await db.execute(select(Course).where(Course.id == assessment.course_id))
    course = result.scalar_one_or_none()
    if not course or course.owner_id != teacher.id:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    return _assessment_to_draft(assessment)


# ── PATCH /assessments/{assessment_id}/publish ────────────────────────────────

@router.patch(
    "/{assessment_id}/publish",
    response_model=dict,
    summary="Publish a draft assessment to students",
)
async def publish_assessment(
    assessment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> dict:
    """
    Transition an assessment from 'draft' to 'published'.

    Students cannot submit until the assessment is published.
    Only the course owner can publish.
    """
    assessment = await get_assessment_detail(assessment_id, db)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    result = await db.execute(select(Course).where(Course.id == assessment.course_id))
    course = result.scalar_one_or_none()
    if not course or course.owner_id != teacher.id:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    if assessment.status == "generating":
        raise HTTPException(
            status_code=409,
            detail="Assessment is still generating. Please wait and try again.",
        )
    if assessment.status == "failed":
        raise HTTPException(
            status_code=400,
            detail="Cannot publish a failed assessment. Please generate a new one.",
        )
    if assessment.status != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Only draft assessments can be published (current status: '{assessment.status}').",
        )
    if not assessment.questions:
        raise HTTPException(
            status_code=400,
            detail="Cannot publish an assessment with no questions.",
        )

    assessment.status = "published"
    assessment.published_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Assessment %s published by teacher %s", assessment_id, teacher.id)
    return {"assessment_id": str(assessment_id), "status": "published"}


# ── POST /assessments/{assessment_id}/submit ──────────────────────────────────

@router.post(
    "/{assessment_id}/submit",
    response_model=SubmissionAck,
    status_code=status.HTTP_201_CREATED,
    summary="Submit student responses for an assessment",
)
async def submit_assessment(
    assessment_id: uuid.UUID,
    body: SubmitRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> SubmissionAck:
    """
    Accept typed student responses for a published assessment.

    - Each student may submit only once per assessment (duplicate check).
    - Only published assessments accept submissions.
    - question_id must belong to this assessment.
    - On success, AI grading is enqueued automatically (see below).
    - Returns a submission_id for polling grading status.

    Grading is queued here rather than waiting for someone to call
    POST /grading/{id}/grade. Nothing in the app ever called it, so every
    submission sat at pending_grading forever. This produces a
    GradeRecommendation only -- FinalGrade is still written exclusively by
    grading.service.finalize_grade() after an explicit teacher approve or
    override, so the human-in-the-loop gate is untouched.
    """
    # ── Load and validate assessment ──────────────────────────────────────────
    result = await db.execute(
        select(Assessment).where(Assessment.id == assessment_id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment or assessment.status != "published":
        raise HTTPException(status_code=404, detail="Assessment not found or not published.")

    course = (
        await db.execute(select(Course).where(Course.id == assessment.course_id))
    ).scalar_one_or_none()
    if course is None or course.archived_at is not None:
        raise HTTPException(
            status_code=404, detail="Assessment not found or not published."
        )

    # ── Duplicate submission check ────────────────────────────────────────────
    existing = await db.execute(
        select(Submission).where(
            Submission.assessment_id == assessment_id,
            Submission.student_id == student.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="You have already submitted this assessment.",
        )

    # ── Validate question ownership ───────────────────────────────────────────
    # Fetch the set of valid question IDs for this assessment
    assessment_with_qs = await get_assessment_detail(assessment_id, db)
    valid_question_ids = {q.id for q in (assessment_with_qs.questions or [])}

    for resp_item in body.responses:
        if resp_item.question_id not in valid_question_ids:
            raise HTTPException(
                status_code=400,
                detail=f"question_id {resp_item.question_id} does not belong to this assessment.",
            )

    # ── Persist submission ────────────────────────────────────────────────────
    submission = Submission(
        assessment_id=assessment_id,
        student_id=student.id,
        status="pending_grading",
    )
    db.add(submission)
    await db.flush()  # obtain submission.id

    for resp_item in body.responses:
        sr = SubmissionResponse(
            submission_id=submission.id,
            question_id=resp_item.question_id,
            answer_text=resp_item.answer_text,
            answer_choice=resp_item.answer_choice,
        )
        db.add(sr)

    try:
        await db.commit()
    except IntegrityError:
        # F7: the uq_submissions_assessment_student constraint fired — a
        # concurrent request already created this student's submission after our
        # pre-check. Surface the same 409 as the non-racing path.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="You have already submitted this assessment.",
        )

    logger.info(
        "Submission %s created: assessment_id=%s student_id=%s responses=%d",
        submission.id,
        assessment_id,
        student.id,
        len(body.responses),
    )

    # ── Enqueue AI grading ────────────────────────────────────────────────────
    # Reuses grading.service.trigger_grading so this shares one set of guards
    # (already-graded, unpublished assessment, duplicate recommendation) with
    # the manual POST /grading/{id}/grade path -- the two must not drift.
    # trigger_grading only schedules the work; the task itself opens its own
    # AsyncSessionFactory session, exactly like the ingestion and
    # assessment-generation tasks, so the request-scoped `db` never crosses
    # into it.
    #
    # Failures here are logged and swallowed on purpose: the submission is
    # already committed and is the student's work. Losing their submission
    # because the grading queue rejected it would be a far worse outcome than a
    # recommendation a teacher can re-trigger from the grading endpoint.
    try:
        await trigger_grading(
            submission_id=submission.id,
            db=db,
            app_state=request.app.state,
            background_tasks=background_tasks,
        )
    except Exception:
        logger.exception(
            "Submission %s was saved but AI grading could not be enqueued; "
            "it stays at pending_grading and can be re-triggered via "
            "POST /grading/%s/grade.",
            submission.id,
            submission.id,
        )

    return SubmissionAck(
        submission_id=submission.id,
        status="pending_grading",
        message=(
            "Your submission was received. "
            "AI grading is in progress — your instructor must review before grades are released."
        ),
    )


# ── GET /assessments/submissions/{submission_id} ──────────────────────────────

@router.get(
    "/submissions/{submission_id}",
    response_model=StudentSubmissionDetail,
    summary="Get submission detail (student view)",
)
async def get_student_submission(
    submission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> StudentSubmissionDetail:
    """
    Return full submission detail including all responses and grading (if available).

    Only the student who created the submission can view it.
    """
    # Load submission with full relationships
    result = await db.execute(
        select(Submission)
        .options(
            selectinload(Submission.assessment).selectinload(Assessment.questions),
            selectinload(Submission.responses),
            selectinload(Submission.grade_recommendation),
        )
        .where(Submission.id == submission_id, Submission.student_id == student.id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found.")

    # Fetch course name
    course_result = await db.execute(
        select(Course).where(Course.id == submission.assessment.course_id)
    )
    course = course_result.scalar_one_or_none()
    course_name = course.name if course else ""

    # Build response objects from submission responses
    responses_by_q_id = {r.question_id: r for r in (submission.responses or [])}
    response_list: list[StudentSubmissionResponse] = []

    for question in (submission.assessment.questions or []):
        sr = responses_by_q_id.get(question.id)
        response_list.append(
            StudentSubmissionResponse(
                question_id=question.id,
                question_text=question.stem,
                question_type=question.question_type,
                student_answer=sr.answer_text if sr else None,
                student_choice=sr.answer_choice if sr else None,
                max_points=question.max_points,
            )
        )

    # Fetch final grade if graded
    final_grade = None
    if submission.status == "graded" and submission.grade_recommendation:
        fg_result = await db.execute(
            select(FinalGrade).where(
                FinalGrade.recommendation_id == submission.grade_recommendation.id
            )
        )
        final_grade = fg_result.scalar_one_or_none()

    # Calculate max_score
    max_score = sum(q.max_points for q in (submission.assessment.questions or []))

    return StudentSubmissionDetail(
        submission_id=submission.id,
        assessment_id=submission.assessment.id,
        assessment_title=submission.assessment.title,
        course_id=submission.assessment.course_id,
        course_name=course_name,
        submitted_at=submission.submitted_at,
        status=submission.status,
        responses=response_list,
        final_score=final_grade.final_score if final_grade else None,
        max_score=max_score if max_score > 0 else None,
        graded_at=final_grade.finalized_at if final_grade else None,
        feedback=final_grade.teacher_note if final_grade else None,
    )


# ── Private helper ────────────────────────────────────────────────────────────

def _assessment_to_draft(assessment: Assessment) -> AssessmentDraft:
    """Convert an Assessment ORM object to the AssessmentDraft response schema."""
    questions = assessment.questions or []
    return AssessmentDraft(
        id=assessment.id,
        title=assessment.title,
        status=assessment.status,
        course_id=assessment.course_id,
        question_count=len(questions),
        generation_error=assessment.generation_error,
        published_at=assessment.published_at,
        created_at=assessment.created_at,
        questions=[
            QuestionDetail(
                id=q.id,
                question_type=q.question_type,
                stem=q.stem,
                options=json.loads(q.options) if q.options else None,
                bloom_level=q.bloom_level,
                difficulty=q.difficulty,
                max_points=q.max_points,
                rubric_criteria=[
                    RubricCriterionDetail(
                        id=rc.id,
                        description=rc.description,
                        max_points=rc.max_points,
                    )
                    for rc in sorted(q.rubric_criteria, key=lambda x: x.order_index)
                ],
            )
            for q in sorted(questions, key=lambda x: x.order_index)
        ],
    )


def _assessment_to_student_view(assessment: Assessment) -> StudentAssessmentDetail:
    """
    Convert an Assessment ORM object to the student-facing take schema.

    Mirrors _assessment_to_draft, but builds the Student* models -- which have
    no answer-bearing fields at all. The exclusion is the schema's shape, not a
    filtering step here, so adding a field to the ORM cannot start leaking one.
    """
    questions = assessment.questions or []
    return StudentAssessmentDetail(
        id=assessment.id,
        title=assessment.title,
        status=assessment.status,
        course_id=assessment.course_id,
        question_count=len(questions),
        published_at=assessment.published_at,
        questions=[
            StudentQuestion(
                id=q.id,
                question_type=q.question_type,
                stem=q.stem,
                # Question.options holds only the MCQ choice text; the correct
                # letter lives in Question.answer_key, which is not read here.
                options=json.loads(q.options) if q.options else None,
                max_points=q.max_points,
                rubric_criteria=[
                    StudentRubricCriterion(
                        id=rc.id,
                        description=rc.description,
                        max_points=rc.max_points,
                    )
                    for rc in sorted(q.rubric_criteria, key=lambda x: x.order_index)
                ],
            )
            for q in sorted(questions, key=lambda x: x.order_index)
        ],
    )


# ── PATCH /assessments/{assessment_id}/questions/{question_id} ────────────────


@router.patch(
    "/{assessment_id}/questions/{question_id}",
    response_model=QuestionUpdateAck,
    summary="Edit a question in a draft assessment",
    description=(
        "Update a generated question before publishing. Draft status only, and "
        "owner-scoped like every other teacher route here."
    ),
)
async def update_draft_question(
    assessment_id: uuid.UUID,
    question_id: uuid.UUID,
    body: QuestionUpdate,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> QuestionUpdateAck:
    """
    Apply a teacher's edit to one draft question.

    Draft-only is the load-bearing rule: editing a published assessment would
    change the paper underneath students who have already answered it, and any
    grade already released against the old wording would silently stop matching
    what the question now says.
    """
    result = await db.execute(select(Assessment).where(Assessment.id == assessment_id))
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    course = (
        await db.execute(select(Course).where(Course.id == assessment.course_id))
    ).scalar_one_or_none()
    if not course or course.owner_id != teacher.id:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    if assessment.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=(
                "Only draft assessments can be edited; this one is "
                f"'{assessment.status}'. Published questions cannot change "
                "underneath students who have already answered them."
            ),
        )

    question = (
        await db.execute(
            select(Question)
            .options(selectinload(Question.rubric_criteria))
            .where(Question.id == question_id, Question.assessment_id == assessment_id)
        )
    ).scalar_one_or_none()
    if not question:
        raise HTTPException(
            status_code=404, detail="Question not found in this assessment."
        )

    updated: list[str] = []
    content_changed = False

    if body.stem is not None and body.stem != question.stem:
        # Same sanitiser the generator runs: a teacher pasting from the draft
        # could reintroduce the "according to the context" phrasing.
        question.stem = strip_context_references(body.stem.strip())
        updated.append("stem")
        content_changed = True

    if body.options is not None:
        if question.question_type != "mcq":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Options apply to MCQ questions only; this is "
                    f"'{question.question_type}'."
                ),
            )
        cleaned = [_strip_option_prefix(o).strip() for o in body.options]
        question.options = json.dumps(cleaned)
        updated.append("options")
        content_changed = True

    if body.correct_answer is not None or body.worked_solution is not None:
        try:
            key = json.loads(question.answer_key) if question.answer_key else {}
        except json.JSONDecodeError:
            key = {}
        if body.correct_answer is not None:
            answer = body.correct_answer.strip()
            if question.question_type == "mcq":
                answer = answer.upper()
                if answer not in {"A", "B", "C", "D"}:
                    raise HTTPException(
                        status_code=400,
                        detail="For an MCQ, correct_answer must be one of A, B, C, D.",
                    )
            key["correct_answer"] = answer
            updated.append("correct_answer")
        if body.worked_solution is not None:
            key["worked_solution"] = strip_context_references(
                body.worked_solution.strip()
            )
            updated.append("worked_solution")
        question.answer_key = json.dumps(key)

    if body.max_points is not None:
        question.max_points = body.max_points
        updated.append("max_points")

    if body.rubric_criteria is not None:
        if question.question_type != "short_answer":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Rubric criteria apply to short-answer questions only; this "
                    f"is '{question.question_type}'."
                ),
            )
        for existing in list(question.rubric_criteria):
            await db.delete(existing)
        for order, criterion in enumerate(body.rubric_criteria):
            db.add(
                RubricCriterion(
                    question_id=question.id,
                    description=criterion.description.strip(),
                    max_points=criterion.max_points,
                    order_index=order,
                )
            )
        # Points follow the rubric unless the teacher set them explicitly.
        if body.max_points is None:
            question.max_points = sum(c.max_points for c in body.rubric_criteria)
        updated.append("rubric_criteria")

    # ── Concept tag: re-validate or clear ─────────────────────────────────────
    # A tag written against the generated wording must not survive a rewrite
    # that changed what the question asks. Re-matching is cheap and reuses the
    # generation-time matcher, so a still-correct tag is kept rather than
    # thrown away.
    concept_tag = "unchanged"
    if content_changed and question.concept_id is not None:
        try:
            candidates = await load_course_concepts(assessment.course_id, db)
            rematched = retag_question_after_edit(question.stem, candidates)
            if rematched == question.concept_id:
                concept_tag = "revalidated"
            else:
                question.concept_id = rematched
                concept_tag = "revalidated" if rematched else "cleared"
        except Exception:  # noqa: BLE001 - tagging must never block an edit
            logger.exception(
                "Concept re-validation failed for question %s; clearing the tag "
                "rather than leaving a stale one.",
                question_id,
            )
            question.concept_id = None
            concept_tag = "cleared"

    await db.commit()
    logger.info(
        "Draft question %s updated by teacher %s: fields=%s concept_tag=%s",
        question_id,
        teacher.id,
        updated,
        concept_tag,
    )

    return QuestionUpdateAck(
        question_id=question_id,
        assessment_id=assessment_id,
        updated_fields=updated,
        concept_tag=concept_tag,
    )
