"""
progress/mastery.py
--------------------
Turn one finalized grade into per-concept mastery rows.

Called from grading/service.finalize_grade(), inside the same transaction that
writes FinalGrade + GradeAuditRecord. Mastery is derived only from grades a
teacher has actually released; a GradeRecommendation on its own never moves it.

Attribution
-----------
The teacher finalizes ONE number for the whole submission, but mastery is
per-concept, so the score has to be split back out across questions. The AI's
per-question breakdown in ``GradeRecommendation.rationale`` is the only record
of how the total was arrived at, so that is what the split uses — scaled so the
attributed points sum to the teacher's final score rather than the AI's.

    scale = final_score / recommended_score      (recommended_score > 0)

When the AI recommended 0 but the teacher awarded points, there is no per-question
signal to scale, so the award is spread across questions in proportion to their
max_points. That is a fallback, not a model of what the teacher meant, and it is
the only case where attribution is not grounded in the per-question breakdown.

Questions with concept_id=None contribute nothing here. That is the whole point
of leaving an unconfident tag NULL: an untagged question still counts toward
course-level progress, it simply does not claim mastery of a concept nobody
verified it covers.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    GradeRecommendation,
    Question,
    StudentConceptMastery,
    Submission,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuestionPoints:
    """Points attributed to one question of a finalized submission."""
    question_id: uuid.UUID
    earned: float
    possible: float


def attribute_points(
    rationale: dict,
    final_score: float,
    recommended_score: float,
) -> list[QuestionPoints]:
    """
    Split a finalized score back across the questions it came from.

    Pure — no DB, no ORM — so the scaling rules are directly testable.
    Returns [] when the rationale carries no per-question breakdown.
    """
    entries = rationale.get("questions") or []
    rows: list[tuple[uuid.UUID, float, float]] = []

    for entry in entries:
        raw_id = entry.get("question_id")
        if not raw_id:
            continue
        try:
            qid = uuid.UUID(str(raw_id))
        except (ValueError, TypeError, AttributeError):
            continue
        criteria = entry.get("criteria") or []
        earned = sum(float(c.get("score", 0.0)) for c in criteria)
        possible = sum(float(c.get("max_points", 0.0)) for c in criteria)
        rows.append((qid, earned, possible))

    if not rows:
        return []

    if recommended_score > 0:
        scale = final_score / recommended_score
        return [QuestionPoints(qid, earned * scale, possible) for qid, earned, possible in rows]

    # AI recommended 0, teacher awarded something: no per-question signal to
    # scale, so spread by weight. Documented fallback, not an inference.
    total_possible = sum(possible for _, _, possible in rows)
    if total_possible <= 0:
        return [QuestionPoints(qid, 0.0, possible) for qid, _, possible in rows]
    return [
        QuestionPoints(qid, final_score * (possible / total_possible), possible)
        for qid, _, possible in rows
    ]


async def apply_finalized_grade_to_mastery(
    submission_id: uuid.UUID,
    final_score: float,
    db: AsyncSession,
) -> int:
    """
    Fold one finalized submission into the student's per-concept mastery.

    Returns the number of concepts touched. Adds to the caller's transaction and
    does NOT commit — finalize_grade() owns the commit so the FinalGrade, its
    audit record and this update land together or not at all.

    Never raises on missing data: a submission whose recommendation has no
    per-question breakdown, or whose questions are all untagged, simply touches
    nothing. Grade finalization must not fail because mastery could not be
    computed.
    """
    rec = (
        await db.execute(
            select(GradeRecommendation).where(
                GradeRecommendation.submission_id == submission_id
            )
        )
    ).scalar_one_or_none()
    if not rec or not rec.rationale:
        return 0

    submission = (
        await db.execute(select(Submission).where(Submission.id == submission_id))
    ).scalar_one_or_none()
    if not submission:
        return 0

    try:
        rationale = json.loads(rec.rationale)
    except json.JSONDecodeError:
        logger.warning(
            "Could not parse rationale for recommendation %s; skipping mastery update.",
            rec.id,
        )
        return 0

    attributed = attribute_points(rationale, final_score, rec.recommended_score)
    if not attributed:
        return 0

    # Resolve question -> concept. Untagged questions drop out here.
    question_ids = [a.question_id for a in attributed]
    questions = (
        await db.execute(select(Question).where(Question.id.in_(question_ids)))
    ).scalars().all()
    concept_by_question = {
        q.id: q.concept_id for q in questions if q.concept_id is not None
    }
    if not concept_by_question:
        return 0

    # Aggregate this submission's contribution per concept before touching rows,
    # so a submission with two questions on one concept is a single update.
    per_concept: dict[uuid.UUID, list[float]] = {}
    for a in attributed:
        concept_id = concept_by_question.get(a.question_id)
        if concept_id is None:
            continue
        bucket = per_concept.setdefault(concept_id, [0.0, 0.0, 0.0])
        bucket[0] += a.earned
        bucket[1] += a.possible
        bucket[2] += 1

    if not per_concept:
        return 0

    existing = (
        await db.execute(
            select(StudentConceptMastery).where(
                StudentConceptMastery.student_id == submission.student_id,
                StudentConceptMastery.concept_id.in_(list(per_concept)),
            )
        )
    ).scalars().all()
    by_concept = {row.concept_id: row for row in existing}

    now = datetime.now(timezone.utc)
    for concept_id, (earned, possible, attempts) in per_concept.items():
        row = by_concept.get(concept_id)
        if row is None:
            row = StudentConceptMastery(
                student_id=submission.student_id,
                concept_id=concept_id,
                attempts=0,
                earned_points=0.0,
                possible_points=0.0,
                mastery=0.0,
            )
            db.add(row)
        row.attempts += int(attempts)
        row.earned_points += earned
        row.possible_points += possible
        row.mastery = (
            row.earned_points / row.possible_points if row.possible_points > 0 else 0.0
        )
        row.last_graded_at = now

    logger.info(
        "Mastery updated for student %s from submission %s: %d concept(s).",
        submission.student_id,
        submission_id,
        len(per_concept),
    )
    return len(per_concept)
