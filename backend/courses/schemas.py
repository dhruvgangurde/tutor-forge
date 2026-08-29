"""courses/schemas.py — Pydantic v2 schemas for courses endpoints."""

import uuid
from datetime import datetime
from pydantic import BaseModel


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
    chapter_count: int
    concept_count: int
