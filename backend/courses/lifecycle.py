"""
courses/lifecycle.py
---------------------
Archive, restore, and hard-delete for courses.

Why this is not a plain DELETE
------------------------------
Every foreign key from ``courses`` downward is ON DELETE CASCADE:

    courses -> assessments -> submissions -> grade_recommendations
            -> final_grades -> grade_audit_records
    courses -> chapters -> concepts -> student_concept_mastery

So ``DELETE FROM courses`` silently destroys released grades and their audit
records. ``FinalGrade`` is the only teacher-approved grade in the system and
``GradeAuditRecord`` exists specifically to be the audit trail. A teacher
tidying their course list must not be able to erase a student's marked work.

Measured on live data before this module existed, deleting one course would
have destroyed 2 FinalGrades and 2 GradeAuditRecords.

The model
---------
  archive   default, always safe, reversible. The course disappears from every
            student-facing surface — no new tutoring sessions, no taking or
            submitting assessments, no new generation — while every historical
            row stays exactly where it is.
  restore   the inverse. Archiving is not a one-way door.
  hard      only when the course has zero submissions, i.e. there is no student
            work to destroy. Otherwise refused with the counts that block it.

Archiving deliberately does NOT mutate assessment rows. Hiding is done by
filtering on the course's archived state at read time, so restore is exactly
one column write and cannot leave assessments in a half-published state.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Assessment,
    Chapter,
    Concept,
    Course,
    FinalGrade,
    GradeAuditRecord,
    GradeRecommendation,
    StudentConceptMastery,
    Submission,
    TutoringMessage,
    TutoringSession,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeletionImpact:
    """What a hard delete of one course would destroy."""
    assessments: int
    submissions: int
    recommendations: int
    final_grades: int
    audit_records: int
    tutoring_sessions: int
    tutoring_messages: int
    concept_mastery_rows: int

    @property
    def blocks_hard_delete(self) -> bool:
        """
        Student work present. Submissions are the trigger, not grades alone: an
        ungraded submission is still a student's answers, and deleting it would
        destroy work they cannot resubmit.
        """
        return self.submissions > 0

    def as_dict(self) -> dict:
        return {
            "assessments": self.assessments,
            "submissions": self.submissions,
            "recommendations": self.recommendations,
            "final_grades": self.final_grades,
            "audit_records": self.audit_records,
            "tutoring_sessions": self.tutoring_sessions,
            "tutoring_messages": self.tutoring_messages,
            "concept_mastery_rows": self.concept_mastery_rows,
        }

    def describe(self) -> str:
        """Human-readable reason, for the 409 a teacher actually reads."""
        parts = [_plural(self.submissions, "student submission")]
        if self.final_grades:
            parts.append(_plural(self.final_grades, "released grade"))
        if self.audit_records:
            parts.append(_plural(self.audit_records, "grade audit record"))
        if self.concept_mastery_rows:
            parts.append(_plural(self.concept_mastery_rows, "concept-mastery record"))
        return ", ".join(parts)


def _plural(n: int, noun: str) -> str:
    """'1 released grade', '3 released grades' -- never 'grade(s)'."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


async def deletion_impact(course_id: uuid.UUID, db: AsyncSession) -> DeletionImpact:
    """Count everything a hard delete would cascade through."""

    async def _count(stmt) -> int:
        return (await db.execute(stmt)).scalar() or 0

    course_assessments = select(Assessment.id).where(Assessment.course_id == course_id)
    course_submissions = (
        select(Submission.id)
        .join(Assessment, Submission.assessment_id == Assessment.id)
        .where(Assessment.course_id == course_id)
    )
    course_recs = (
        select(GradeRecommendation.id)
        .join(Submission, GradeRecommendation.submission_id == Submission.id)
        .join(Assessment, Submission.assessment_id == Assessment.id)
        .where(Assessment.course_id == course_id)
    )
    course_finals = (
        select(FinalGrade.id)
        .join(GradeRecommendation, FinalGrade.recommendation_id == GradeRecommendation.id)
        .join(Submission, GradeRecommendation.submission_id == Submission.id)
        .join(Assessment, Submission.assessment_id == Assessment.id)
        .where(Assessment.course_id == course_id)
    )
    course_sessions = select(TutoringSession.id).where(
        TutoringSession.course_id == course_id
    )

    return DeletionImpact(
        assessments=await _count(
            select(func.count()).select_from(course_assessments.subquery())
        ),
        submissions=await _count(
            select(func.count()).select_from(course_submissions.subquery())
        ),
        recommendations=await _count(
            select(func.count()).select_from(course_recs.subquery())
        ),
        final_grades=await _count(
            select(func.count()).select_from(course_finals.subquery())
        ),
        audit_records=await _count(
            select(func.count())
            .select_from(GradeAuditRecord)
            .where(GradeAuditRecord.final_grade_id.in_(course_finals))
        ),
        tutoring_sessions=await _count(
            select(func.count()).select_from(course_sessions.subquery())
        ),
        tutoring_messages=await _count(
            select(func.count())
            .select_from(TutoringMessage)
            .where(TutoringMessage.session_id.in_(course_sessions))
        ),
        concept_mastery_rows=await _count(
            select(func.count())
            .select_from(StudentConceptMastery)
            .where(
                StudentConceptMastery.concept_id.in_(
                    select(Concept.id)
                    .join(Chapter, Concept.chapter_id == Chapter.id)
                    .where(Chapter.course_id == course_id)
                )
            )
        ),
    )


async def archive_course(course: Course, db: AsyncSession) -> Course:
    """
    Hide the course from all new activity, preserving every historical row.

    Idempotent: archiving an archived course leaves the original timestamp, so
    the record of *when* it was archived is not overwritten by a double-click.
    """
    if course.archived_at is None:
        course.archived_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(
            "Course archived: course_id=%s name=%r — history preserved.",
            course.id,
            course.name,
        )
    return course


async def restore_course(course: Course, db: AsyncSession) -> Course:
    """Undo an archive. One column write, because archiving mutated nothing else."""
    if course.archived_at is not None:
        course.archived_at = None
        await db.commit()
        logger.info("Course restored: course_id=%s name=%r", course.id, course.name)
    return course


async def hard_delete_course(
    course: Course,
    db: AsyncSession,
    retrieval_service=None,
) -> None:
    """
    Permanently remove a course and its derived data.

    The caller MUST have checked deletion_impact().blocks_hard_delete first —
    this function does not re-check, so that the router owns the HTTP semantics
    and this stays a plain operation.

    The Chroma collection goes too; leaving it behind would orphan the
    embeddings on disk with no row pointing at them. A Chroma failure is logged
    and does not abort the DB delete: a stale collection is recoverable, a
    half-deleted course is not.
    """
    course_id, name = course.id, course.name

    if retrieval_service is not None:
        try:
            retrieval_service.delete_course_collection(course_id)
        except Exception:  # noqa: BLE001 - never block the delete on Chroma
            logger.exception(
                "Could not delete Chroma collection for course %s; the database "
                "rows are still being removed and the collection is now orphaned.",
                course_id,
            )

    # Core DELETE, not db.delete(course): the ORM tries to NULL child FKs before
    # removing the parent, which fails on assessments.course_id (NOT NULL).
    # Every FK here is ON DELETE CASCADE, so letting the database do the cascade
    # is both correct and the only thing that works.
    await db.execute(sa_delete(Course).where(Course.id == course_id))
    await db.commit()
    logger.warning(
        "Course HARD DELETED: course_id=%s name=%r (had no student submissions).",
        course_id,
        name,
    )
