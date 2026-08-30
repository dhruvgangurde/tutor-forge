"""
grading/schemas.py
-------------------
Pydantic v2 schemas for the Grading API.

Schema hierarchy
----------------
  Request:
    ApproveRequest   — teacher approves at the recommended score
    OverrideRequest  — teacher sets a custom score + reason

  Response (list):
    GradingQueueItem  — lightweight summary for the teacher queue

  Response (detail):
    EvidenceCitation  — one cited passage from the course corpus
    CriterionGrade    — score + feedback for one rubric criterion
    QuestionGrade     — one question and the criteria belonging to it
    GradingDetail     — full recommendation, grouped by question

  Ack:
    GradingAck        — immediate 202 response after trigger_grading
    FinalGradeResponse — returned by approve/override endpoints

Language note: per PROJECT-BRIEF §5.6, all response schemas use the words
"recommended_score" and "suggested_feedback" — never "final_grade" or
"official_grade" in the recommendation layer.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, model_validator


# ── Evidence ──────────────────────────────────────────────────────────────────

class EvidenceCitation(BaseModel):
    """One verbatim passage from the course corpus used to justify a score."""
    text: str
    source_file: str
    page_or_slide: int | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0


# ── Per-criterion grading result ──────────────────────────────────────────────

class CriterionGrade(BaseModel):
    """Recommended score and feedback for a single rubric criterion."""
    criterion_id: uuid.UUID | None = None    # None for MCQ/numeric pseudo-criterion
    description: str
    score: Annotated[float, Field(ge=0.0)]
    max_points: Annotated[float, Field(gt=0.0)]
    feedback: str                            # always labelled as "Suggested Feedback"
    citations: list[EvidenceCitation] = Field(default_factory=list)
    requires_review: bool = Field(
        default=False,
        description=(
            "True when the groundedness gate could not evaluate this criterion "
            "against course evidence. The score is 0 because nothing was judged, "
            "NOT because the answer was wrong - the teacher must decide."
        ),
    )


# ── Per-question grouping ─────────────────────────────────────────────────────

class QuestionGrade(BaseModel):
    """
    One question of the assessment and the criteria scored against it.

    GradingDetail used to expose a single flat ``criteria`` list, which threw
    away the grouping the stored rationale already has
    (``rationale["questions"][*]["criteria"]``). On a multi-question assessment
    that left a reviewing teacher unable to tell which criterion belonged to
    which question.

    ``stem`` is hydrated from the questions table at read time rather than read
    out of the rationale, because the grading agent does not store it (see
    agents/grading/nodes.py, where each entry carries only question_id,
    question_type and criteria). Hydrating instead of changing what the agent
    writes means recommendations produced before this change group correctly
    too. It is None if the question has since been deleted.
    """
    question_id: uuid.UUID | None = None
    question_type: str = ""
    stem: str | None = None
    criteria: list[CriterionGrade] = Field(default_factory=list)


# ── Queue item (list view) ────────────────────────────────────────────────────

class GradingQueueItem(BaseModel):
    """
    Lightweight summary for the teacher review queue.
    Does not include per-criterion detail — use GradingDetail for that.
    """
    recommendation_id: uuid.UUID
    submission_id: uuid.UUID
    student_email: str | None = None
    assessment_title: str | None = None
    recommended_score: float
    max_score: float
    status: str           # always "pending_review" in the queue
    submitted_at: datetime | None = None


# ── Full grading detail (teacher review view) ─────────────────────────────────

class GradingDetail(BaseModel):
    """
    Full grading recommendation for teacher review.

    Scores and suggested feedback are grouped by question; ``evidence_citations``
    stays flat as the union of everything cited across the submission.
    """
    recommendation_id: uuid.UUID
    submission_id: uuid.UUID
    student_email: str | None = None
    assessment_title: str | None = None
    recommended_score: float
    max_score: float
    status: str
    questions: list[QuestionGrade] = Field(default_factory=list)
    evidence_citations: list[EvidenceCitation] = Field(default_factory=list)
    created_at: datetime


# ── Request: teacher approve ──────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    """
    Teacher explicitly approves the AI-recommended score.
    No score field — the recommended_score is used as-is.
    An optional note can be attached for the audit record.
    """
    note: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional teacher note for the audit record.",
    )


# ── Request: teacher override ─────────────────────────────────────────────────

class OverrideRequest(BaseModel):
    """
    Teacher overrides the AI recommendation with a custom score.
    A reason is required for audit transparency.
    """
    final_score: Annotated[float, Field(ge=0.0)]
    reason: Annotated[str, Field(min_length=5, max_length=2000)]

    @model_validator(mode="after")
    def reason_not_blank(self) -> "OverrideRequest":
        if not self.reason.strip():
            raise ValueError("reason must not be blank.")
        return self


# ── Response: trigger ack ─────────────────────────────────────────────────────

class GradingAck(BaseModel):
    """Immediate 202 response confirming grading has been enqueued."""
    submission_id: uuid.UUID
    status: str = "grading_in_progress"
    message: str


# ── Response: finalized grade ─────────────────────────────────────────────────

class FinalGradeResponse(BaseModel):
    """
    Returned by approve and override endpoints after instructor action.

    Note: This represents the instructor decision record — not a grade
    released to the student. Display language must always say "Finalized by
    instructor" rather than "Official grade released".
    """
    final_grade_id: uuid.UUID
    final_score: float
    action: str             # "approved" | "overridden"
    finalized_at: datetime
