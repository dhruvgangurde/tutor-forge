"""courses/schemas.py — Pydantic v2 schemas for courses endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field

from core.limits import MAX_EMAIL_CHARS


class CourseUploadResponse(BaseModel):
    course_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    message: str


class CourseSummary(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    archived_at: datetime | None = None
    is_archived: bool = False
    # Plain-language reason, set only when status is "failed".
    failure_reason: str | None = None


class DeletionBlockedDetail(BaseModel):
    """Why a hard delete was refused, itemised so the teacher can act on it."""
    detail: str
    impact: dict


class ConceptSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    difficulty: str | None
    order_index: int


class ChapterWithConcepts(BaseModel):
    id: uuid.UUID
    title: str
    order_index: int
    concepts: list[ConceptSummary]


class CourseStructure(BaseModel):
    course_id: uuid.UUID
    name: str
    chapters: list[ChapterWithConcepts]


class CourseDetail(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    # From the stored outline; None unless the course is ready (no outline yet,
    # or ingestion failed).
    chapter_count: int | None = None
    concept_count: int | None = None
    # Plain-language reason, set only when status is "failed".
    failure_reason: str | None = None


class EnrollRequest(BaseModel):
    """Teacher adds a student to their course by the student's account email."""
    email: Annotated[EmailStr, Field(max_length=MAX_EMAIL_CHARS)]


class EnrollmentOut(BaseModel):
    student_id: uuid.UUID
    email: str
    enrolled_at: datetime
