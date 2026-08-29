"""tutoring/schemas.py — Pydantic v2 schemas for tutoring endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel


# ── Request schemas ───────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    """Request to create a new tutoring session."""

    course_id: uuid.UUID


class ChatRequest(BaseModel):
    """Request to send a question to the tutor."""

    question: str


# ── Response schemas ──────────────────────────────────────────────────────────


class Citation(BaseModel):
    """A cited passage from the course material."""

    chunk_text: str
    source_file: str
    page_or_slide: int | None
    confidence: float


class SessionCreated(BaseModel):
    """Response from session creation."""

    session_id: uuid.UUID
    course_id: uuid.UUID


class SessionSummary(BaseModel):
    """Lightweight summary of a tutoring session."""

    id: uuid.UUID
    course_id: uuid.UUID
    current_hint_level: int
    created_at: datetime


class TutoringMessageOut(BaseModel):
    """A message in a tutoring session (student or tutor)."""

    id: uuid.UUID
    role: str  # "student" | "tutor"
    content: str
    citations: list[Citation]
    hint_level: int
    is_refusal: bool
    created_at: datetime


class TutorResponse(BaseModel):
    """Response from a student question (chat endpoint)."""

    response: str
    citations: list[Citation]
    is_grounded: bool
    hint_level: int
    session_id: uuid.UUID


class HintResponse(BaseModel):
    """Response from a hint request endpoint."""

    response: str
    citations: list[Citation]
    hint_level: int
    session_id: uuid.UUID
