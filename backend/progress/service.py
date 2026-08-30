"""
progress/service.py
--------------------
Read-side queries for the student progress view.

Everything here is scoped to one student and reads released grades only:
FinalGrade -> GradeRecommendation -> Submission -> Assessment -> Course. A
GradeRecommendation without a FinalGrade contributes no score anywhere in this
module — it is counted as "awaiting grade" and nothing more.

Per-concept mastery is read from the materialized student_concept_mastery table
(written by progress/mastery.py on finalize); it is never recomputed here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Assessment,
    Chapter,
    Concept,
    Course,
    FinalGrade,
    GradeRecommendation,
    StudentConceptMastery,
    Submission,
    TutoringMessage,
    TutoringSession,
)

_UNTAGGED_NOTE = (
    "No concept-level breakdown is available for this course yet. These "
    "assessments were generated before questions were linked to concepts."
)


async def list_course_progress(
    student_id: uuid.UUID,
    db: AsyncSession,
) -> list[dict]:
    """
    One row per course the student has submitted to, newest activity first.

    Counts graded and awaiting-grade submissions separately so the UI can say
    "2 graded, 1 awaiting" rather than implying an ungraded submission scored 0.
    """
    result = await db.execute(
        select(
            Course.id,
            Course.name,
            func.count(func.distinct(Submission.id)).label("submissions"),
            func.count(func.distinct(FinalGrade.id)).label("graded"),
            func.coalesce(func.sum(FinalGrade.final_score), 0.0).label("earned"),
            func.coalesce(
                func.sum(
                    func.coalesce(GradeRecommendation.max_score, 0.0)
                ).filter(FinalGrade.id.isnot(None)),
                0.0,
            ).label("possible"),
            func.max(FinalGrade.finalized_at).label("last_graded_at"),
        )
        .select_from(Submission)
        .join(Assessment, Submission.assessment_id == Assessment.id)
        .join(Course, Assessment.course_id == Course.id)
        .outerjoin(
            GradeRecommendation, GradeRecommendation.submission_id == Submission.id
        )
        .outerjoin(FinalGrade, FinalGrade.recommendation_id == GradeRecommendation.id)
        .where(Submission.student_id == student_id)
        .group_by(Course.id, Course.name)
        .order_by(func.max(FinalGrade.finalized_at).desc().nullslast(), Course.name)
    )

    rows = []
    for course_id, name, submissions, graded, earned, possible, last_graded in result.all():
        rows.append(
            {
                "course_id": course_id,
                "course_name": name,
                "assessments_graded": int(graded or 0),
                "assessments_awaiting_grade": int(submissions or 0) - int(graded or 0),
                "earned_points": float(earned or 0.0),
                "possible_points": float(possible or 0.0),
                "last_graded_at": last_graded,
            }
        )
    return rows


async def get_course_progress(
    student_id: uuid.UUID,
    course_id: uuid.UUID,
    db: AsyncSession,
) -> dict | None:
    """
    Full progress detail for one course, or None if the course does not exist.

    A student with no submissions in an existing course gets an empty-but-valid
    payload rather than a 404 — "you have not started this yet" is a real state
    the page can render.
    """
    course = (
        await db.execute(select(Course).where(Course.id == course_id))
    ).scalar_one_or_none()
    if not course:
        return None

    # ── Released grades, newest first ─────────────────────────────────────────
    result_rows = (
        await db.execute(
            select(
                Submission.id,
                Assessment.title,
                FinalGrade.final_score,
                GradeRecommendation.max_score,
                FinalGrade.action,
                FinalGrade.finalized_at,
                Submission.submitted_at,
            )
            .select_from(FinalGrade)
            .join(
                GradeRecommendation,
                FinalGrade.recommendation_id == GradeRecommendation.id,
            )
            .join(Submission, GradeRecommendation.submission_id == Submission.id)
            .join(Assessment, Submission.assessment_id == Assessment.id)
            .where(
                Submission.student_id == student_id,
                Assessment.course_id == course_id,
            )
            .order_by(FinalGrade.finalized_at.desc())
        )
    ).all()

    results = [
        {
            "submission_id": sid,
            "assessment_title": title,
            "final_score": float(final_score),
            "max_score": float(max_score),
            "action": action,
            "finalized_at": finalized_at,
            "submitted_at": submitted_at,
        }
        for sid, title, final_score, max_score, action, finalized_at, submitted_at in result_rows
    ]

    # ── Per-concept mastery, scoped to this course's concepts ─────────────────
    mastery_rows = (
        await db.execute(
            select(
                Concept.id,
                Concept.name,
                Chapter.title,
                StudentConceptMastery.attempts,
                StudentConceptMastery.earned_points,
                StudentConceptMastery.possible_points,
                StudentConceptMastery.mastery,
            )
            .select_from(StudentConceptMastery)
            .join(Concept, StudentConceptMastery.concept_id == Concept.id)
            .join(Chapter, Concept.chapter_id == Chapter.id)
            .where(
                StudentConceptMastery.student_id == student_id,
                Chapter.course_id == course_id,
            )
            .order_by(Chapter.order_index, Concept.order_index)
        )
    ).all()

    concepts = [
        {
            "concept_id": cid,
            "concept_name": cname,
            "chapter_title": chapter_title,
            "attempts": int(attempts),
            "earned_points": float(earned),
            "possible_points": float(possible),
            "mastery": float(mastery),
        }
        for cid, cname, chapter_title, attempts, earned, possible, mastery in mastery_rows
    ]

    # ── Tutoring engagement ───────────────────────────────────────────────────
    session_count = (
        await db.execute(
            select(func.count())
            .select_from(TutoringSession)
            .where(
                TutoringSession.student_id == student_id,
                TutoringSession.course_id == course_id,
            )
        )
    ).scalar() or 0

    message_count = (
        await db.execute(
            select(func.count())
            .select_from(TutoringMessage)
            .join(TutoringSession, TutoringMessage.session_id == TutoringSession.id)
            .where(
                TutoringSession.student_id == student_id,
                TutoringSession.course_id == course_id,
                TutoringMessage.role == "student",
            )
        )
    ).scalar() or 0

    return {
        "course_id": course.id,
        "course_name": course.name,
        "earned_points": sum(r["final_score"] for r in results),
        "possible_points": sum(r["max_score"] for r in results),
        "concepts": concepts,
        "results": results,
        "tutoring_sessions": int(session_count),
        "tutoring_messages": int(message_count),
        # Explain an empty concept list rather than letting the page look broken.
        "untagged_note": _UNTAGGED_NOTE if results and not concepts else None,
    }
