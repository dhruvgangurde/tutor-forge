"""
progress/router.py
-------------------
REST endpoints for the student progress view.

Routes (Student):
  GET  /progress/me                      — per-course progress summaries
  GET  /progress/me/courses/{course_id}  — concept mastery + released grade history

Authorization:
  - Both endpoints are require_student and scoped to the caller's own id, which
    is taken from the token and never from the path. There is no
    /progress/{student_id}: a student may only ever read their own progress, and
    not offering the parameter is simpler than validating it.

Released grades only: every score here comes from a FinalGrade, so a submission
whose AI recommendation is still awaiting teacher review shows as awaiting, not
as a score.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.service import require_student
from core.dependencies import get_db_session
from db.models import User
from progress.schemas import CourseProgressDetail, CourseProgressSummary
from progress.service import get_course_progress, list_course_progress

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/progress", tags=["progress"])


@router.get(
    "/me",
    response_model=list[CourseProgressSummary],
    summary="My progress across courses",
    description=(
        "One row per course the student has submitted to. Scores reflect "
        "teacher-released grades only."
    ),
)
async def my_progress(
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> list[CourseProgressSummary]:
    rows = await list_course_progress(student.id, db)
    return [CourseProgressSummary(**row) for row in rows]


@router.get(
    "/me/courses/{course_id}",
    response_model=CourseProgressDetail,
    summary="My progress in one course",
    description=(
        "Per-concept mastery and released grade history for one course, plus "
        "tutoring engagement."
    ),
)
async def my_course_progress(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> CourseProgressDetail:
    detail = await get_course_progress(student.id, course_id, db)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
        )
    return CourseProgressDetail(**detail)
