"""
db/backfill_concept_tags.py
----------------------------
Best-effort concept tagging for questions written before tagging existed.

Run from the backend/ directory:
    python -m db.backfill_concept_tags            # report only, writes nothing
    python -m db.backfill_concept_tags --apply    # write the confident matches

Why this is a separate script and not part of migration 0003
------------------------------------------------------------
The matching is approximate, and a migration should not silently write
approximate data into a column whose whole value is that it is trustworthy.
Keeping it separate means the schema change is reversible and boring, and the
data change is explicit, re-runnable, and reviewable in dry-run first.

Why it is stricter than generation-time tagging
-----------------------------------------------
Generation-time tagging sees the question stem AND the topic the assessment was
generated from. This sees a stem and nothing else, so it runs at
BACKFILL_MIN_SCORE / BACKFILL_MIN_MARGIN, which are deliberately higher than the
generation thresholds (progress/concept_matching.py). When it cannot tell, it
leaves concept_id NULL: that question keeps counting toward course-level
progress and simply claims no concept mastery. A wrong historical tag would
undercut exactly the thing real tagging is for.

Idempotent: only ever considers questions where concept_id IS NULL, so re-running
after adding concepts to a course picks up newly-matchable questions and touches
nothing else.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from core.database import AsyncSessionFactory
from db.models import Assessment, Chapter, Concept, Question
from progress.concept_matching import (
    BACKFILL_MIN_MARGIN,
    BACKFILL_MIN_SCORE,
    match_concept,
)


async def backfill(apply: bool) -> dict:
    """Tag untagged questions. Returns counters; writes only when apply=True."""
    stats = {"examined": 0, "tagged": 0, "skipped_no_match": 0, "courses": 0}

    async with AsyncSessionFactory() as db:
        # Group the work by course: candidate concepts are per-course, so this
        # loads each course's concept list once rather than per question.
        course_ids = (
            await db.execute(select(Assessment.course_id).distinct())
        ).scalars().all()

        for course_id in course_ids:
            candidates = [
                (row[0], row[1], row[2])
                for row in (
                    await db.execute(
                        select(Concept.id, Concept.name, Concept.keywords)
                        .join(Chapter, Concept.chapter_id == Chapter.id)
                        .where(Chapter.course_id == course_id)
                    )
                ).all()
            ]
            if not candidates:
                continue
            stats["courses"] += 1

            questions = (
                await db.execute(
                    select(Question)
                    .join(Assessment, Question.assessment_id == Assessment.id)
                    .where(
                        Assessment.course_id == course_id,
                        Question.concept_id.is_(None),
                    )
                )
            ).scalars().all()

            for question in questions:
                stats["examined"] += 1
                match = match_concept(
                    question.stem,
                    candidates,
                    min_score=BACKFILL_MIN_SCORE,
                    min_margin=BACKFILL_MIN_MARGIN,
                )
                if match is None:
                    stats["skipped_no_match"] += 1
                    continue
                stats["tagged"] += 1
                print(
                    f"  match score={match.score:.2f} "
                    f"(runner-up {match.runner_up_score:.2f}) "
                    f"terms={sorted(match.matched_terms)} :: {question.stem[:70]!r}"
                )
                if apply:
                    question.concept_id = match.concept_id

        if apply:
            await db.commit()

    return stats


async def main() -> None:
    apply = "--apply" in sys.argv
    print(
        f"Concept tag backfill ({'APPLY' if apply else 'DRY RUN'}) — "
        f"min_score={BACKFILL_MIN_SCORE} min_margin={BACKFILL_MIN_MARGIN}"
    )
    stats = await backfill(apply)
    print(
        f"\ncourses with concepts: {stats['courses']}\n"
        f"untagged questions examined: {stats['examined']}\n"
        f"confidently tagged: {stats['tagged']}\n"
        f"left NULL (no confident match): {stats['skipped_no_match']}"
    )
    if not apply and stats["tagged"]:
        print("\nNothing was written. Re-run with --apply to persist these matches.")


if __name__ == "__main__":
    asyncio.run(main())
