"""
progress/tagging.py
--------------------
DB-facing helpers for attaching questions to concepts.

Split from progress/concept_matching.py so the scorer stays pure and testable
without a session, and so the assessment agent's import surface is one small
module rather than the matching internals.

Safety contract for the generation path: nothing in here may break assessment
generation. Tagging is an enrichment — a question with concept_id=None is a
perfectly valid question that simply contributes to course-level progress only.
Callers in the agent wrap these in try/except and carry on.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from progress.concept_matching import (
    BACKFILL_MIN_MARGIN,
    BACKFILL_MIN_SCORE,
    GENERATION_MIN_MARGIN,
    GENERATION_MIN_SCORE,
    match_concept,
)

logger = logging.getLogger(__name__)

#: (concept_id, name, keywords_json) — the shape match_concept() consumes.
ConceptCandidate = tuple[uuid.UUID, str, str | None]


async def load_course_concepts(
    course_id: uuid.UUID,
    db: AsyncSession,
) -> list[ConceptCandidate]:
    """
    Every concept belonging to a course, as match candidates.

    Concepts hang off chapters, not courses, so this joins through Chapter.
    Returns [] for a course with no extracted hierarchy, which makes every
    subsequent match a no-op rather than an error.
    """
    from db.models import Chapter, Concept

    result = await db.execute(
        select(Concept.id, Concept.name, Concept.keywords)
        .join(Chapter, Concept.chapter_id == Chapter.id)
        .where(Chapter.course_id == course_id)
    )
    return [(row[0], row[1], row[2]) for row in result.all()]


def retag_question_after_edit(
    stem: str,
    candidates: list[ConceptCandidate],
) -> uuid.UUID | None:
    """
    Re-match an edited question's concept, or None if no longer confident.

    Deliberately does NOT reuse the assessment's generation topic, unlike
    tag_question_at_generation. The topic describes what was *generated*, not
    what the teacher just wrote, so including it drags the match back to the
    original concept: rewriting a binary-search stem into "What is the capital
    city of Peru?" still re-matched to Binary Search purely on the topic string.
    A test caught exactly that.

    With only the stem to go on, this uses the stricter backfill thresholds —
    the same reasoning as db/backfill_concept_tags.py: less evidence, higher
    bar, and clear the tag rather than keep a doubtful one.
    """
    if not candidates:
        return None
    match = match_concept(
        stem,
        candidates,
        min_score=BACKFILL_MIN_SCORE,
        min_margin=BACKFILL_MIN_MARGIN,
    )
    return None if match is None else match.concept_id  # type: ignore[return-value]


def tag_question_at_generation(
    stem: str,
    topic: str,
    candidates: list[ConceptCandidate],
) -> uuid.UUID | None:
    """
    Concept for a freshly generated question, or None if not confident.

    Matches on the stem plus the teacher's topic string: the topic is what the
    whole assessment was generated from, so it is real evidence about which
    concept the question came from, and it is only available here — the backfill
    has nothing but the stem.
    """
    if not candidates:
        return None
    text = f"{stem} {topic}".strip()
    match = match_concept(
        text,
        candidates,
        min_score=GENERATION_MIN_SCORE,
        min_margin=GENERATION_MIN_MARGIN,
    )
    if match is None:
        return None
    logger.debug(
        "Tagged question to concept %s (score=%.2f, runner-up=%.2f, terms=%s)",
        match.concept_id,
        match.score,
        match.runner_up_score,
        sorted(match.matched_terms),
    )
    return match.concept_id  # type: ignore[return-value]
