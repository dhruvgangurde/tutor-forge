"""
assessments/schemas.py
-----------------------
Pydantic v2 schemas for the assessment API.

Validation is the first gate — invalid requests are rejected before
any DB write or LLM call is made.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Constants used in validators ──────────────────────────────────────────────

_VALID_BLOOM = frozenset({"remember", "understand", "apply", "analyze", "evaluate", "create"})
_VALID_DIFFICULTY = frozenset({"easy", "medium", "hard", "mixed"})
_VALID_QTYPES = frozenset({"mcq", "short_answer", "numeric"})

_MIN_QUESTIONS = 1
_MAX_QUESTIONS = 30


# ── Request: generate assessment ──────────────────────────────────────────────

class GenerateRequest(BaseModel):
    """
    Teacher request to generate a new grounded assessment.
    All fields are validated before the LLM is called.
    """

    course_id: uuid.UUID

    title: Annotated[str, Field(min_length=1, max_length=200)]

    topic: Annotated[str, Field(min_length=3, max_length=300,
                                description="The topic or concept to focus questions on.")]

    difficulty: str = Field(
        default="mixed",
        description="One of: easy | medium | hard | mixed",
    )

    count: int = Field(
        default=10,
        ge=_MIN_QUESTIONS,
        le=_MAX_QUESTIONS,
        description=f"Total number of questions ({_MIN_QUESTIONS}-{_MAX_QUESTIONS}).",
    )

    bloom_mix: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Bloom level counts, e.g. {'remember': 2, 'understand': 3}. "
            "If empty, the agent uses sensible defaults."
        ),
    )

    type_mix: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Question type counts, e.g. {'mcq': 6, 'short_answer': 3, 'numeric': 1}. "
            "If empty, the agent uses sensible defaults."
        ),
    )

    # ── Field validators ──────────────────────────────────────────────────────

    @field_validator("topic")
    @classmethod
    def topic_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("topic must not be blank or whitespace only.")
        return stripped

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be blank or whitespace only.")
        return stripped

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _VALID_DIFFICULTY:
            raise ValueError(
                f"difficulty must be one of {sorted(_VALID_DIFFICULTY)}, got '{v}'."
            )
        return v

    @field_validator("bloom_mix")
    @classmethod
    def validate_bloom_mix(cls, v: dict[str, int]) -> dict[str, int]:
        if not v:
            return v  # empty means "use defaults"
        for key, cnt in v.items():
            if key not in _VALID_BLOOM:
                raise ValueError(
                    f"Invalid Bloom level '{key}'. "
                    f"Valid levels: {sorted(_VALID_BLOOM)}."
                )
            if not isinstance(cnt, int) or cnt < 0:
                raise ValueError(
                    f"Bloom count for '{key}' must be a non-negative integer, got {cnt!r}."
                )
        return v

    @field_validator("type_mix")
    @classmethod
    def validate_type_mix(cls, v: dict[str, int]) -> dict[str, int]:
        if not v:
            return v  # empty means "use defaults"
        for key, cnt in v.items():
            if key not in _VALID_QTYPES:
                raise ValueError(
                    f"Invalid question type '{key}'. "
                    f"Valid types: {sorted(_VALID_QTYPES)}."
                )
            if not isinstance(cnt, int) or cnt < 0:
                raise ValueError(
                    f"Question type count for '{key}' must be a non-negative integer, got {cnt!r}."
                )
        return v

    @model_validator(mode="after")
    def bloom_total_matches_count(self) -> "GenerateRequest":
        """If bloom_mix is provided, its total must equal count."""
        if self.bloom_mix:
            total = sum(self.bloom_mix.values())
            if total != self.count:
                raise ValueError(
                    f"bloom_mix totals {total} questions but count={self.count}. "
                    "They must match, or omit bloom_mix to use defaults."
                )
        return self

    @model_validator(mode="after")
    def type_total_matches_count(self) -> "GenerateRequest":
        """If type_mix is provided, its total must equal count."""
        if self.type_mix:
            total = sum(self.type_mix.values())
            if total != self.count:
                raise ValueError(
                    f"type_mix totals {total} questions but count={self.count}. "
                    "They must match, or omit type_mix to use defaults."
                )
        return self


# ── Response: rubric criterion ────────────────────────────────────────────────

class RubricCriterionDetail(BaseModel):
    id: uuid.UUID
    description: str
    max_points: float


# ── Response: question detail (used in draft/preview) ────────────────────────

class QuestionDetail(BaseModel):
    id: uuid.UUID
    question_type: str
    stem: str
    options: list[str] | None
    bloom_level: str | None
    difficulty: str | None
    max_points: float
    rubric_criteria: list[RubricCriterionDetail]


# ── Response: assessment summary (list view — no question content) ──────────────────

class AssessmentSummary(BaseModel):
    """Lightweight summary returned in list endpoints."""
    id: uuid.UUID
    title: str
    status: str
    course_id: uuid.UUID
    question_count: int
    generation_error: str | None = None  # set when status=='failed'
    created_at: datetime


# ── Response: assessment draft (detail view) ──────────────────────────────────

class AssessmentDraft(BaseModel):
    """Full draft returned for teacher preview."""
    id: uuid.UUID
    title: str
    status: str
    course_id: uuid.UUID
    question_count: int
    questions: list[QuestionDetail]
    generation_error: str | None = None  # user-friendly error; set when status=='failed'
    published_at: datetime | None = None
    created_at: datetime


# ── Request/response: rubric criterion update ─────────────────────────────────

class RubricCriterionUpdate(BaseModel):
    description: Annotated[str, Field(min_length=1)]
    max_points: Annotated[float, Field(gt=0)]


# ── Request: draft question edit ──────────────────────────────────────────────

class QuestionUpdate(BaseModel):
    """
    Teacher edit to one generated question, before publishing.

    Every field is optional: the form sends only what changed, so an untouched
    rubric is not round-tripped and cannot be corrupted by a partial payload.

    Draft-status only. Editing a published assessment would change the paper
    underneath students who have already answered it.
    """
    stem: Annotated[str, Field(min_length=1, max_length=4000)] | None = None
    options: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None,
        description=(
            "MCQ options as PLAIN text, no 'A.'/'B.' prefix — the letter comes "
            "from position, and a stored prefix renders twice."
        ),
    )
    correct_answer: str | None = Field(
        default=None,
        description="MCQ: a single letter A-D. Numeric/short answer: the answer text.",
    )
    worked_solution: str | None = Field(default=None, max_length=4000)
    max_points: Annotated[float, Field(gt=0)] | None = None
    rubric_criteria: list[RubricCriterionUpdate] | None = None

    @model_validator(mode="after")
    def at_least_one_change(self) -> "QuestionUpdate":
        if all(
            getattr(self, f) is None
            for f in (
                "stem",
                "options",
                "correct_answer",
                "worked_solution",
                "max_points",
                "rubric_criteria",
            )
        ):
            raise ValueError("Provide at least one field to update.")
        return self

    @field_validator("options")
    @classmethod
    def four_options(cls, v: list[str] | None) -> list[str] | None:
        # The UI renders exactly A-D and the grader matches a single letter, so
        # any other count would produce an unanswerable question.
        if v is not None and len(v) != 4:
            raise ValueError("An MCQ must have exactly 4 options.")
        return v


class QuestionUpdateAck(BaseModel):
    """Result of a draft edit, including what it did to the concept tag."""
    question_id: uuid.UUID
    assessment_id: uuid.UUID
    updated_fields: list[str]
    concept_tag: str = Field(
        description=(
            "'unchanged', 'revalidated' (content changed, still matches the same "
            "concept), or 'cleared' (content changed and no longer matches)."
        )
    )


# ── Request: student submission ───────────────────────────────────────────────

class SubmissionResponseItem(BaseModel):
    """
    One answer within a student submission.

    Both answer fields may be null: that is an *explicit skip*. A student who
    leaves a question blank still submits a row for it, which is persisted with
    both columns NULL and scored 0 by the grading agent with a "not answered"
    rationale. Rejecting the item here would fail the whole submission request
    over a single untouched question.
    """
    question_id: uuid.UUID
    answer_text: str | None = Field(
        default=None,
        description=(
            "Free-text answer for short_answer or numeric questions. "
            "Null (or blank) means the question was left unanswered."
        ),
    )
    answer_choice: str | None = Field(
        default=None,
        description=(
            "Single letter A-D for MCQ questions. "
            "Null means the question was left unanswered."
        ),
    )

    @field_validator("answer_text")
    @classmethod
    def blank_text_is_unanswered(cls, v: str | None) -> str | None:
        """Normalise a whitespace-only answer to NULL so 'skipped' is one shape."""
        if v is not None and not v.strip():
            return None
        return v

    @field_validator("answer_choice")
    @classmethod
    def valid_choice(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip().upper()
            if not v:
                return None  # blank choice == unanswered
            if v not in {"A", "B", "C", "D"}:
                raise ValueError(
                    f"answer_choice must be one of A, B, C, D - got '{v}'."
                )
        return v


class SubmitRequest(BaseModel):
    responses: list[SubmissionResponseItem] = Field(min_length=1)


# ── Response: submission acknowledgement ──────────────────────────────────────

class SubmissionAck(BaseModel):
    submission_id: uuid.UUID
    status: str    # always "pending_grading"
    message: str


# ── Response: published assessment (student view - no rubric) ─────────────────

class PublishedAssessmentSummary(BaseModel):
    """
    Assessment summary for students (published only, no questions or rubric).

    ``published_at`` is carried alongside ``created_at`` because a teacher can
    generate two assessments with the same title on the same course, and the
    student list then shows two cards a student cannot tell apart. The date the
    paper became available is the one that means something to them; created_at
    is when generation ran, which they never saw.
    """
    id: uuid.UUID
    title: str
    course_id: uuid.UUID
    course_name: str  # populated by router
    question_count: int
    total_points: float
    published_at: datetime | None
    created_at: datetime


# ── Response: assessment for a student to take ────────────────────────────────
#
# These schemas are the security boundary for the take endpoint. Answer keys are
# excluded STRUCTURALLY -- the fields simply do not exist on the models, so a
# response_model=StudentAssessmentDetail route cannot serialise an answer even
# if a future mapper hands one over. That is the same reason QuestionDetail
# omits answer_key for the teacher preview, and it is deliberately not a
# hand-written dict filter, which would silently start leaking the first time
# someone adds a field to the ORM model.


class StudentRubricCriterion(BaseModel):
    """
    One rubric line as shown to a student taking the assessment.

    Criterion descriptions ARE exposed: they describe what the answer is being
    judged on ("Explains energy absorption"), which is exactly the kind of
    transparency a rubric exists to provide, and they are what the teacher
    already shows on a marked script. The correct answer itself lives in
    Question.answer_key, which no schema in this file carries.
    """
    id: uuid.UUID
    description: str
    max_points: float


class StudentQuestion(BaseModel):
    """
    One question as shown to a student taking the assessment.

    Deliberately narrower than QuestionDetail: no answer_key (never modelled
    anywhere), and no bloom_level/difficulty either. Those two are authoring
    metadata, are not needed to answer, and telling a student mid-assessment
    that a question is "hard" is a nudge nobody asked for.
    """
    id: uuid.UUID
    question_type: str
    stem: str
    options: list[str] | None
    max_points: float
    rubric_criteria: list[StudentRubricCriterion]


class StudentAssessmentDetail(BaseModel):
    """
    Full question content for a published assessment, student view.

    Distinct from AssessmentDraft: no generation_error (an internal diagnostic
    that can carry pipeline detail) and no answer content anywhere.
    """
    id: uuid.UUID
    title: str
    status: str
    course_id: uuid.UUID
    question_count: int
    questions: list[StudentQuestion]
    published_at: datetime | None = None


# ── Response: student submission summary ──────────────────────────────────────

class StudentSubmissionSummary(BaseModel):
    """Lightweight summary of a student's submission."""
    submission_id: uuid.UUID
    assessment_id: uuid.UUID
    assessment_title: str
    course_id: uuid.UUID
    course_name: str
    submitted_at: datetime
    status: str  # "pending_grading" | "graded"
    final_score: float | None = None  # only populated if graded
    max_score: float | None = None


# ── Response: student submission detail ───────────────────────────────────────

class StudentSubmissionResponse(BaseModel):
    """One response within a submission detail view."""
    question_id: uuid.UUID
    question_text: str
    question_type: str
    student_answer: str | None  # free-text or numeric
    student_choice: str | None  # MCQ choice (A-D)
    max_points: float


class StudentSubmissionDetail(BaseModel):
    """Full submission detail for student review (includes responses, grading if available)."""
    submission_id: uuid.UUID
    assessment_id: uuid.UUID
    assessment_title: str
    course_id: uuid.UUID
    course_name: str
    submitted_at: datetime
    status: str  # "pending_grading" | "graded"
    responses: list[StudentSubmissionResponse]

    # Only populated if status == "graded"
    final_score: float | None = None
    max_score: float | None = None
    graded_at: datetime | None = None
    feedback: str | None = None  # teacher-provided feedback
