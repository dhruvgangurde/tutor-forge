"""
courses/router.py
-----------------
REST endpoints for course management and ingestion.

Routes:
  POST   /courses/upload              — upload files, create course + ingestion job
  GET    /courses                     — list courses owned by current teacher
  GET    /courses/{course_id}         — get course status/detail
  GET    /courses/{course_id}/structure — get full chapter/concept hierarchy
  GET    /courses/available           — list ready courses the student is enrolled in (student only)
  GET    /courses/{course_id}/enrollments              — course roster (owner only)
  POST   /courses/{course_id}/enrollments              — enroll a student by email (owner only)
  DELETE /courses/{course_id}/enrollments/{student_id} — remove a student (owner only)
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.service import require_teacher, require_student
from core.config import settings
from core.dependencies import (
    get_db_session,
    get_gemini_pro,
    get_langfuse_client,
    get_retrieval_service,
)
from courses.enrollment import (
    AlreadyEnrolledError,
    NotEnrolledError,
    StudentNotFoundError,
    enroll_student,
    enrolled_course_ids,
    list_enrollments,
    remove_enrollment,
)
from courses.schemas import (
    CourseDetail,
    CourseStructure,
    CourseSummary,
    CourseUploadResponse,
    EnrollmentOut,
    EnrollRequest,
)
from courses.lifecycle import (
    archive_course,
    deletion_impact,
    hard_delete_course,
    restore_course,
)
from courses.service import create_course, get_course_structure
from db.models import Course, User

router = APIRouter(prefix="/courses", tags=["courses"])

_ALLOWED_EXTENSIONS = {".pdf", ".pptx", ".ppt", ".txt"}
_UPLOAD_CHUNK = 1024 * 1024  # 1 MB read granularity


async def read_upload_capped(upload: UploadFile, max_bytes: int) -> bytes:
    """
    Read an UploadFile in bounded chunks, aborting with HTTP 413 once the cap is
    exceeded (F15). Memory use is bounded to ~max_bytes instead of the previous
    unbounded ``await upload.read()`` that buffered the whole file first.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File '{upload.filename}' exceeds the maximum upload size of "
                    f"{max_bytes} bytes."
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/upload", response_model=CourseUploadResponse)
async def upload_course(
    name: str = Form(...),
    files: list[UploadFile] = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
    retrieval_service=Depends(get_retrieval_service),
    gemini_pro=Depends(get_gemini_pro),
    langfuse=Depends(get_langfuse_client),
) -> CourseUploadResponse:
    """Upload course files; creates ingestion job as a background task."""
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files: at most {settings.max_upload_files} per upload.",
        )

    file_data = []
    for uf in files:
        ext = "." + (uf.filename or "").rsplit(".", 1)[-1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {uf.filename}")
        content = await read_upload_capped(uf, settings.max_upload_bytes)
        file_data.append({
            "filename": uf.filename,
            "content": content,
            "mime_type": uf.content_type or "application/octet-stream",
        })

    course_id, job_id = await create_course(
        name=name,
        owner=teacher,
        files=file_data,
        db=db,
        background_tasks=background_tasks,
        retrieval_service=retrieval_service,
        gemini_pro=gemini_pro,
        langfuse=langfuse,
    )

    return CourseUploadResponse(
        course_id=course_id,
        job_id=job_id,
        status="pending",
        message="Ingestion started. Poll GET /courses/{course_id} for status.",
    )


@router.get("", response_model=list[CourseSummary])
async def list_courses(
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> list[CourseSummary]:
    result = await db.execute(
        select(Course).where(Course.owner_id == teacher.id).order_by(Course.created_at.desc())
    )
    return [
        # The teacher sees archived courses, clearly marked — archiving is for
        # tidying their own list, not for hiding a course from themselves.
        CourseSummary(
            id=c.id,
            name=c.name,
            status=c.status,
            created_at=c.created_at,
            archived_at=c.archived_at,
            is_archived=c.archived_at is not None,
        )
        for c in result.scalars().all()
    ]


@router.get("/available", response_model=list[CourseSummary])
async def list_available_courses(
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> list[CourseSummary]:
    """List the ready (fully ingested) courses this student is enrolled in."""
    result = await db.execute(
        select(Course)
        .where(
            Course.status == "ready",
            Course.archived_at.is_(None),
            Course.id.in_(enrolled_course_ids(student.id)),
        )
        .order_by(Course.created_at.desc())
    )
    return [
        CourseSummary(
            id=c.id,
            name=c.name,
            status=c.status,
            created_at=c.created_at,
            archived_at=None,
            is_archived=False,
        )
        for c in result.scalars().all()
    ]


@router.get("/{course_id}", response_model=CourseDetail)
async def get_course(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> CourseDetail:
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course or course.owner_id != teacher.id:
        raise HTTPException(status_code=404, detail="Course not found.")
    return CourseDetail(
        id=course.id,
        name=course.name,
        status=course.status,
        created_at=course.created_at,
        chapter_count=0,
        concept_count=0,
    )


@router.get("/{course_id}/structure", response_model=CourseStructure)
async def get_structure(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> CourseStructure:
    await _require_owned_course(course_id, teacher, db)
    structure = await get_course_structure(course_id, db)
    if structure is None:
        raise HTTPException(status_code=404, detail="Course not found.")
    return structure


# ── Course lifecycle: archive / restore / delete ──────────────────────────────


async def _require_owned_course(
    course_id: uuid.UUID, teacher: User, db: AsyncSession
) -> Course:
    """Load a course and verify the caller owns it. 404 hides existence."""
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course or course.owner_id != teacher.id:
        raise HTTPException(status_code=404, detail="Course not found.")
    return course


@router.delete("/{course_id}", response_model=dict)
async def delete_course(
    course_id: uuid.UUID,
    hard: bool = False,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
    retrieval_service=Depends(get_retrieval_service),
) -> dict:
    """
    Archive a course, or permanently delete one that has no student work.

    Default (``hard=false``) archives: the course vanishes from every
    student-facing surface but every historical row survives. This is always
    safe and always reversible via POST /courses/{id}/restore.

    ``hard=true`` permanently deletes, and is refused with 409 when the course
    has any student submissions. Every FK from courses downward is ON DELETE
    CASCADE, so a hard delete would otherwise destroy released grades and their
    audit records — a student's marked work is an education record, not the
    teacher's to erase by tidying a list.
    """
    course = await _require_owned_course(course_id, teacher, db)

    if not hard:
        await archive_course(course, db)
        return {
            "course_id": str(course.id),
            "action": "archived",
            "message": (
                "Course archived. Students can no longer start new work on it; "
                "all existing submissions, grades and history are preserved. "
                "Restore it at any time."
            ),
        }

    impact = await deletion_impact(course_id, db)
    if impact.blocks_hard_delete:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot permanently delete this course: it has {impact.describe()}. "
                "Deleting it would destroy student work that has already been "
                "marked. Archive it instead."
            ),
        )

    await hard_delete_course(course, db, retrieval_service=retrieval_service)
    return {
        "course_id": str(course_id),
        "action": "deleted",
        "message": "Course permanently deleted. It had no student submissions.",
    }


@router.post("/{course_id}/restore", response_model=dict)
async def restore_archived_course(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> dict:
    """Un-archive a course, making it available to students again."""
    course = await _require_owned_course(course_id, teacher, db)
    await restore_course(course, db)
    return {
        "course_id": str(course.id),
        "action": "restored",
        "message": "Course restored. Students can start new work on it again.",
    }


@router.get("/{course_id}/deletion-impact", response_model=dict)
async def get_deletion_impact(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> dict:
    """
    What a permanent delete would destroy — so the UI can warn before asking.

    A teacher cannot otherwise tell a disposable mis-upload from a course with
    two students' released grades behind it.
    """
    await _require_owned_course(course_id, teacher, db)
    impact = await deletion_impact(course_id, db)
    return {
        "course_id": str(course_id),
        "can_hard_delete": not impact.blocks_hard_delete,
        "impact": impact.as_dict(),
        "blocking_reason": impact.describe() if impact.blocks_hard_delete else None,
    }


# ── Enrollment (roster management, owner only) ────────────────────────────────


@router.get("/{course_id}/enrollments", response_model=list[EnrollmentOut])
async def get_enrollments(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> list[EnrollmentOut]:
    """Students currently enrolled in this course."""
    await _require_owned_course(course_id, teacher, db)
    return [
        EnrollmentOut(student_id=user.id, email=user.email, enrolled_at=enrollment.enrolled_at)
        for enrollment, user in await list_enrollments(course_id, db)
    ]


@router.post(
    "/{course_id}/enrollments",
    response_model=EnrollmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_enrollment(
    course_id: uuid.UUID,
    body: EnrollRequest,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> EnrollmentOut:
    """Enroll an existing student account in this course, by email."""
    await _require_owned_course(course_id, teacher, db)
    try:
        enrollment, student = await enroll_student(course_id, body.email, db)
    except StudentNotFoundError:
        raise HTTPException(status_code=404, detail="No student account with that email.")
    except AlreadyEnrolledError:
        raise HTTPException(status_code=409, detail="That student is already enrolled in this course.")
    return EnrollmentOut(
        student_id=student.id, email=student.email, enrolled_at=enrollment.enrolled_at
    )


@router.delete("/{course_id}/enrollments/{student_id}", response_model=dict)
async def delete_enrollment(
    course_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    teacher: User = Depends(require_teacher),
) -> dict:
    """
    Remove a student from this course. They lose access immediately; their
    sessions, submissions and grades are kept.
    """
    await _require_owned_course(course_id, teacher, db)
    try:
        await remove_enrollment(course_id, student_id, db)
    except NotEnrolledError:
        raise HTTPException(status_code=404, detail="That student is not enrolled in this course.")
    return {
        "course_id": str(course_id),
        "student_id": str(student_id),
        "action": "removed",
        "message": "Student removed. Their existing work and grades are kept.",
    }
