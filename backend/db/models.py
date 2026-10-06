"""
db/models.py
------------
All 16 SQLAlchemy ORM models for TutorForge AI.
Tables are defined in foreign-key dependency order so Alembic can
generate migrations without constraint violations.

Table creation order:
  1. users
  2. courses
  3. chapters
  4. concepts
  5. concept_prerequisites  (self-referential join)
  6. ingestion_jobs
  7. tutoring_sessions
  8. tutoring_messages
  9. assessments
 10. questions
 11. rubric_criteria
 12. submissions
 13. submission_responses
 14. grade_recommendations
 15. final_grades
 16. grade_audit_records
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 1. User ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "teacher" | "student"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # relationships
    owned_courses: Mapped[list["Course"]] = relationship("Course", back_populates="owner")
    tutoring_sessions: Mapped[list["TutoringSession"]] = relationship(
        "TutoringSession", back_populates="student"
    )
    created_assessments: Mapped[list["Assessment"]] = relationship(
        "Assessment", back_populates="created_by_user"
    )
    submissions: Mapped[list["Submission"]] = relationship(
        "Submission", back_populates="student"
    )
    finalized_grades: Mapped[list["FinalGrade"]] = relationship(
        "FinalGrade", back_populates="teacher"
    )


# ── 2. Course ─────────────────────────────────────────────────────────────────

class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (Index("ix_courses_owner_id", "owner_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # status values: "pending" | "ingesting" | "ready" | "failed"
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment=(
            "Set when a teacher archives the course. Archived courses are hidden "
            "from all NEW student activity (tutoring, taking assessments, "
            "generation) but every historical row is preserved: a released "
            "FinalGrade is an education record and must survive the teacher "
            "tidying their course list. NULL = active. Reversible via restore."
        ),
    )

    owner: Mapped["User"] = relationship("User", back_populates="owned_courses")
    chapters: Mapped[list["Chapter"]] = relationship(
        "Chapter", back_populates="course", cascade="all, delete-orphan"
    )
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(
        "IngestionJob", back_populates="course", cascade="all, delete-orphan"
    )
    tutoring_sessions: Mapped[list["TutoringSession"]] = relationship(
        "TutoringSession", back_populates="course"
    )
    assessments: Mapped[list["Assessment"]] = relationship(
        "Assessment", back_populates="course"
    )


# ── 3. Chapter ────────────────────────────────────────────────────────────────

class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (Index("ix_chapters_course_id", "course_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    course: Mapped["Course"] = relationship("Course", back_populates="chapters")
    concepts: Mapped[list["Concept"]] = relationship(
        "Concept", back_populates="chapter", cascade="all, delete-orphan"
    )


# ── 4. Concept ────────────────────────────────────────────────────────────────

class Concept(Base):
    __tablename__ = "concepts"
    __table_args__ = (Index("ix_concepts_chapter_id", "chapter_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array as text
    difficulty: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    chapter: Mapped["Chapter"] = relationship("Chapter", back_populates="concepts")
    prerequisites: Mapped[list["ConceptPrerequisite"]] = relationship(
        "ConceptPrerequisite",
        foreign_keys="ConceptPrerequisite.concept_id",
        cascade="all, delete-orphan",
    )


# ── 5. ConceptPrerequisite (self-referential join) ────────────────────────────

class ConceptPrerequisite(Base):
    __tablename__ = "concept_prerequisites"
    __table_args__ = (UniqueConstraint("concept_id", "prerequisite_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    prerequisite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )


# ── 6. IngestionJob ───────────────────────────────────────────────────────────

class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (Index("ix_ingestion_jobs_course_id", "course_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # status values: "pending" | "running" | "complete" | "failed"
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    course: Mapped["Course"] = relationship("Course", back_populates="ingestion_jobs")


# ── 7. TutoringSession ────────────────────────────────────────────────────────

class TutoringSession(Base):
    __tablename__ = "tutoring_sessions"
    __table_args__ = (
        Index("ix_tutoring_sessions_student_id", "student_id"),
        Index("ix_tutoring_sessions_course_id", "course_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    current_hint_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    student: Mapped["User"] = relationship("User", back_populates="tutoring_sessions")
    course: Mapped["Course"] = relationship("Course", back_populates="tutoring_sessions")
    messages: Mapped[list["TutoringMessage"]] = relationship(
        "TutoringMessage", back_populates="session", cascade="all, delete-orphan"
    )


# ── 8. TutoringMessage ────────────────────────────────────────────────────────

class TutoringMessage(Base):
    __tablename__ = "tutoring_messages"
    __table_args__ = (Index("ix_tutoring_messages_session_id", "session_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tutoring_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "student" | "tutor"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    hint_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_refusal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped["TutoringSession"] = relationship(
        "TutoringSession", back_populates="messages"
    )


# ── 9. Assessment ─────────────────────────────────────────────────────────────

_ASSESSMENT_STATUS_CONSTRAINT = CheckConstraint(
    "status IN ('generating', 'draft', 'published', 'failed')",
    name="ck_assessments_status",
)


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        _ASSESSMENT_STATUS_CONSTRAINT,
        Index("ix_assessments_course_id", "course_id"),
        Index("ix_assessments_created_by", "created_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="generating")
    # status values (enforced by ck_assessments_status):
    #   "generating" — background task running, not yet viewable
    #   "draft"      — generation complete, teacher must review before publishing
    #   "published"  — visible to students; submissions accepted
    #   "failed"     — generation failed; see generation_error for details
    config: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: teacher config
    generation_error: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="User-friendly error message when status='failed'. Null on success.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Set when status transitions to 'published'.",
    )

    course: Mapped["Course"] = relationship("Course", back_populates="assessments")
    created_by_user: Mapped["User"] = relationship("User", back_populates="created_assessments")
    questions: Mapped[list["Question"]] = relationship(
        "Question", back_populates="assessment", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["Submission"]] = relationship(
        "Submission", back_populates="assessment"
    )


# ── 10. Question ──────────────────────────────────────────────────────────────

class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        Index("ix_questions_assessment_id", "assessment_id"),
        Index("ix_questions_concept_id", "concept_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "mcq" | "short_answer" | "numeric"
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON: MCQ choices
    answer_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    bloom_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_points: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    concept_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "Concept this question assesses. Nullable on purpose: set at "
            "generation time when the match is confident (see "
            "progress/concept_matching.py), left NULL otherwise. An untagged "
            "question still counts toward course-level progress but claims no "
            "concept mastery. SET NULL on delete so removing a concept never "
            "deletes graded questions."
        ),
    )

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="questions")
    concept: Mapped["Concept | None"] = relationship("Concept")
    rubric_criteria: Mapped[list["RubricCriterion"]] = relationship(
        "RubricCriterion", back_populates="question", cascade="all, delete-orphan"
    )
    submission_responses: Mapped[list["SubmissionResponse"]] = relationship(
        "SubmissionResponse", back_populates="question"
    )


# ── 11. RubricCriterion ───────────────────────────────────────────────────────

class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"
    __table_args__ = (Index("ix_rubric_criteria_question_id", "question_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    max_points: Mapped[float] = mapped_column(Float, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    question: Mapped["Question"] = relationship("Question", back_populates="rubric_criteria")


# ── 12. Submission ────────────────────────────────────────────────────────────

class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        # F7: one submission per student per assessment, enforced at the DB level
        # so concurrent double-submits cannot both land (the router's pre-check
        # only closes the non-racing case).
        UniqueConstraint(
            "assessment_id", "student_id", name="uq_submissions_assessment_student"
        ),
        Index("ix_submissions_student_id", "student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_grading")
    # "pending_grading" | "graded" | "rejected"
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="submissions")
    student: Mapped["User"] = relationship("User", back_populates="submissions")
    responses: Mapped[list["SubmissionResponse"]] = relationship(
        "SubmissionResponse", back_populates="submission", cascade="all, delete-orphan"
    )
    grade_recommendation: Mapped["GradeRecommendation | None"] = relationship(
        "GradeRecommendation", back_populates="submission", uselist=False
    )


# ── 13. SubmissionResponse ────────────────────────────────────────────────────

class SubmissionResponse(Base):
    __tablename__ = "submission_responses"
    __table_args__ = (
        Index("ix_submission_responses_submission_id", "submission_id"),
        Index("ix_submission_responses_question_id", "question_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_choice: Mapped[str | None] = mapped_column(String(8), nullable=True)  # MCQ: "A"-"D"

    submission: Mapped["Submission"] = relationship("Submission", back_populates="responses")
    question: Mapped["Question"] = relationship("Question", back_populates="submission_responses")


# ── 14. GradeRecommendation ───────────────────────────────────────────────────

class GradeRecommendation(Base):
    __tablename__ = "grade_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    # "pending_review" | "approved" | "overridden" | "rejected"
    recommended_score: Mapped[float] = mapped_column(Float, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: per-criterion detail
    evidence_citations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    submission: Mapped["Submission"] = relationship(
        "Submission", back_populates="grade_recommendation"
    )
    final_grade: Mapped["FinalGrade | None"] = relationship(
        "FinalGrade", back_populates="recommendation", uselist=False
    )


# ── 15. FinalGrade ────────────────────────────────────────────────────────────

class FinalGrade(Base):
    """
    A FinalGrade record is ONLY written by grading/service.finalize_grade().
    This is the sole path to a released grade — enforcing the
    'teacher approval required before grade release' constraint.
    """
    __tablename__ = "final_grades"
    __table_args__ = (Index("ix_final_grades_teacher_id", "teacher_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grade_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    final_score: Mapped[float] = mapped_column(Float, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # "approved" | "overridden"
    teacher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    recommendation: Mapped["GradeRecommendation"] = relationship(
        "GradeRecommendation", back_populates="final_grade"
    )
    teacher: Mapped["User | None"] = relationship("User", back_populates="finalized_grades")
    audit_record: Mapped["GradeAuditRecord | None"] = relationship(
        "GradeAuditRecord", back_populates="final_grade", uselist=False
    )


# ── 16. GradeAuditRecord ──────────────────────────────────────────────────────

class GradeAuditRecord(Base):
    __tablename__ = "grade_audit_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    final_grade_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_grades.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    ai_recommended_score: Mapped[float] = mapped_column(Float, nullable=False)
    teacher_final_score: Mapped[float] = mapped_column(Float, nullable=False)
    action_taken: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    final_grade: Mapped["FinalGrade"] = relationship(
        "FinalGrade", back_populates="audit_record"
    )


# ── 17. StudentConceptMastery ─────────────────────────────────────────────────

class StudentConceptMastery(Base):
    """
    A student's accumulated performance on one concept.

    Materialized rather than computed on read: it accumulates across every
    finalized submission, so recomputing it would mean re-walking every
    GradeRecommendation rationale the student has ever had on every page load.

    Written ONLY by progress/mastery.apply_finalized_grade_to_mastery(), which
    grading/service.finalize_grade() calls inside its transaction. Nothing moves
    mastery except a grade a teacher actually released — an AI recommendation
    alone must not, or the progress view would route around the
    teacher-approval gate.
    """
    __tablename__ = "student_concept_mastery"
    __table_args__ = (
        UniqueConstraint("student_id", "concept_id", name="uq_mastery_student_concept"),
        Index("ix_student_concept_mastery_student_id", "student_id"),
        Index("ix_student_concept_mastery_concept_id", "concept_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Graded questions tagged to this concept that the student has answered.",
    )
    earned_points: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    possible_points: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mastery: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
        comment="earned_points / possible_points, 0..1. Stored so the API does not divide on read.",
    )
    last_graded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    student: Mapped["User"] = relationship("User")
    concept: Mapped["Concept"] = relationship("Concept")


# ── 18. Enrollment ────────────────────────────────────────────────────────────

class Enrollment(Base):
    """
    A student's access grant to one course.

    Every student-facing course surface -- listing available courses, starting
    or continuing a tutoring session, listing, taking and submitting assessments
    -- requires a row here. Rows are created and removed only by the course's
    owning teacher (courses/enrollment.py, behind the owner-scoped
    /courses/{course_id}/enrollments routes); self-registration grants access to
    nothing.
    """
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "student_id", name="uq_enrollments_course_student"),
        # The unique constraint already indexes course_id-first lookups; this one
        # serves "which courses is this student in", asked on every student page.
        Index("ix_enrollments_student_id", "student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    course: Mapped["Course"] = relationship("Course")
    student: Mapped["User"] = relationship("User")
