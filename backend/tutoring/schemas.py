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
    """
    One row of a student's session list.

    ``title`` is derived from the session's first student message, not stored:
    a raw UUID prefix ("Session 4bc8fb2b") is not something a student can
    recognise their own work by. It is None for a session with no questions
    yet, which the UI labels rather than inventing a title for.

    ``current_hint_level`` is still returned because the session detail view
    uses it, but it is not a list-level identifier: it describes the ladder
    depth of the most recent question only.
    """

    id: uuid.UUID
    course_id: uuid.UUID
    course_name: str
    title: str | None
    message_count: int
    last_activity_at: datetime
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
