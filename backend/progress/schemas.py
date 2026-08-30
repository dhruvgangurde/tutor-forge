"""
progress/schemas.py
--------------------
Pydantic v2 schemas for the student progress API.

Every field here is rendered by the progress UI. Nothing speculative: there is
no percentile, no trend line, no predicted grade, because nothing displays them.

Language note: these carry *released* grades only — a score reaches this API
only after a teacher approved or overrode the AI recommendation
(grading/service.finalize_grade). Pending recommendations are deliberately
invisible here; showing them would route around the human-in-the-loop gate.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── Course-level summary (list view) ──────────────────────────────────────────

class CourseProgressSummary(BaseModel):
    """One row of the student's progress list — one course they have work in."""
    course_id: uuid.UUID
    course_name: str
    assessments_graded: int = Field(
        description="Submissions with a released grade. Ungraded ones are counted separately."
    )
    assessments_awaiting_grade: int = Field(
        description="Submitted but not yet released by a teacher; no score is shown for these."
    )
    earned_points: float
    possible_points: float
    last_graded_at: datetime | None = None


# ── Course detail ─────────────────────────────────────────────────────────────

class ConceptMastery(BaseModel):
    """
    Accumulated performance on one concept.

    Only concepts the student has actually been graded on appear. A concept with
    no tagged, graded question is omitted rather than shown at zero — a zero
    would read as "you failed this", when it means "nothing measured it yet".
    """
    concept_id: uuid.UUID
    concept_name: str
    chapter_title: str
    attempts: int
    earned_points: float
    possible_points: float
    mastery: float = Field(ge=0.0, description="earned/possible, 0..1")


class AssessmentResult(BaseModel):
    """One released grade in the student's history."""
    submission_id: uuid.UUID
    assessment_title: str
    final_score: float
    max_score: float
    action: str = Field(description='"approved" or "overridden" by the teacher.')
    finalized_at: datetime
    submitted_at: datetime | None = None


class CourseProgressDetail(BaseModel):
    """
    Everything the progress page shows for one course.

    ``untagged_note`` is populated when the student has released grades in this
    course but no concept mastery to show, which happens when the questions
    predate concept tagging. Without it the page would look broken rather than
    explained.
    """
    course_id: uuid.UUID
    course_name: str
    earned_points: float
    possible_points: float
    concepts: list[ConceptMastery] = Field(default_factory=list)
    results: list[AssessmentResult] = Field(default_factory=list)
    tutoring_sessions: int = 0
    tutoring_messages: int = 0
    untagged_note: str | None = None
