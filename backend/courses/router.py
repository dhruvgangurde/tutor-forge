"""
courses/router.py
-----------------
REST endpoints for course management and ingestion.

Routes:
  POST   /courses/upload              — upload files, create course + ingestion job
  GET    /courses                     — list courses owned by current teacher
  GET    /courses/{course_id}         — get course status/detail
  GET    /courses/{course_id}/structure — get full chapter/concept hierarchy
  GET    /courses/available           — list ready courses available for tutoring (student only)
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
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
from courses.schemas import CourseDetail, CourseStructure, CourseSummary, CourseUploadResponse
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
        CourseSummary(id=c.id, name=c.name, status=c.status, created_at=c.created_at)
        for c in result.scalars().all()
    ]


@router.get("/available", response_model=list[CourseSummary])
async def list_available_courses(
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> list[CourseSummary]:
    """List all ready (fully ingested) courses available for tutoring sessions."""
    result = await db.execute(
        select(Course).where(Course.status == "ready").order_by(Course.created_at.desc())
    )
    return [
        CourseSummary(id=c.id, name=c.name, status=c.status, created_at=c.created_at)
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
    structure = await get_course_structure(course_id, db)
    if structure is None:
        raise HTTPException(status_code=404, detail="Course not found.")
    return structure
