"""
tests/test_progress_unit.py
----------------------------
Pure unit tests for the progress subsystem: the concept matcher, the
finalized-score attribution math, and the schema/migration surface.

No database, no app startup. The two functions under test here are deliberately
pure so the rules they encode — when a question gets tagged, and how a teacher's
single final score is split back across questions — are directly assertable.

Migration 0003 is verified the way 0002 already is (see test_db_integrity.py):
by asserting the ORM metadata carries the same names the migration creates, plus
the revision chain, rather than running Alembic against a live Postgres.
"""

import uuid

import pytest


# ── Concept matching ──────────────────────────────────────────────────────────


class TestTokenize:
    def _fn(self):
        from progress.concept_matching import tokenize

        return tokenize

    def test_drops_stopwords_and_short_tokens(self):
        assert self._fn()("What is the value of a in binary search?") == {
            "value",
            "binary",
            "search",
        }

    def test_empty_and_none_safe(self):
        assert self._fn()("") == set()
        assert self._fn()(None) == set()


class TestConceptTerms:
    def _fn(self):
        from progress.concept_matching import concept_terms

        return concept_terms

    def test_combines_name_and_keywords(self):
        import json

        terms = self._fn()("Binary Search", json.dumps(["logarithmic", "sorted array"]))
        assert {"binary", "search", "logarithmic", "sorted", "array"} <= terms

    def test_malformed_keywords_degrade_to_name_only(self):
        # A bad keyword blob must not break assessment generation.
        assert self._fn()("Binary Search", "{not json") == {"binary", "search"}
        assert self._fn()("Binary Search", None) == {"binary", "search"}

    def test_non_list_keywords_ignored(self):
        assert self._fn()("Sorting", '{"a": 1}') == {"sorting"}


class TestMatchConcept:
    """The gate that decides whether a question gets tagged at all."""

    def _fn(self):
        from progress.concept_matching import match_concept

        return match_concept

    def _candidates(self):
        import json

        return [
            (uuid.UUID(int=1), "Binary Search", json.dumps(["logarithmic", "halving"])),
            (uuid.UUID(int=2), "Graph Traversal", json.dumps(["dijkstra", "shortest path"])),
            (uuid.UUID(int=3), "Matrix Chain Multiplication", json.dumps(["dynamic programming"])),
        ]

    def test_matches_the_obvious_concept(self):
        match = self._fn()(
            "Explain the logarithmic halving behaviour of binary search.",
            self._candidates(),
            min_score=0.5,
            min_margin=0.15,
        )
        assert match is not None
        assert match.concept_id == uuid.UUID(int=1)

    def test_returns_none_when_nothing_is_close(self):
        assert (
            self._fn()(
                "What is the best recipe for sourdough bread?",
                self._candidates(),
                min_score=0.5,
                min_margin=0.15,
            )
            is None
        )

    def test_returns_none_when_two_concepts_tie(self):
        # Ambiguous is left untagged rather than assigned to whichever scored a
        # hair higher — a wrong tag is worse than a missing one.
        candidates = [
            (uuid.UUID(int=1), "Sorting", None),
            (uuid.UUID(int=2), "Sorting", None),
        ]
        assert (
            self._fn()("Explain sorting.", candidates, min_score=0.5, min_margin=0.15)
            is None
        )

    def test_margin_bar_rejects_a_near_tie(self):
        match = self._fn()(
            "Explain binary search and graph traversal together.",
            self._candidates(),
            min_score=0.4,
            min_margin=0.9,  # impossible margin
        )
        assert match is None

    def test_no_candidates_or_empty_text_is_none(self):
        fn = self._fn()
        assert fn("anything", [], min_score=0.5, min_margin=0.1) is None
        assert fn("", self._candidates(), min_score=0.5, min_margin=0.1) is None

    def test_match_reports_why(self):
        match = self._fn()(
            "Explain the logarithmic halving behaviour of binary search.",
            self._candidates(),
            min_score=0.5,
            min_margin=0.15,
        )
        assert match.score > match.runner_up_score
        assert "binary" in match.matched_terms


def test_backfill_bar_is_stricter_than_generation():
    """
    The whole point of tagging at generation time is that it is trustworthy.
    The backfill sees only a stem, so it must be held to a visibly higher bar —
    if these ever equalise, historical guesses start carrying the same weight as
    real tagging.
    """
    from progress.concept_matching import (
        BACKFILL_MIN_MARGIN,
        BACKFILL_MIN_SCORE,
        GENERATION_MIN_MARGIN,
        GENERATION_MIN_SCORE,
    )

    assert BACKFILL_MIN_SCORE > GENERATION_MIN_SCORE
    assert BACKFILL_MIN_MARGIN > GENERATION_MIN_MARGIN


class TestTagQuestionAtGeneration:
    def _fn(self):
        from progress.tagging import tag_question_at_generation

        return tag_question_at_generation

    def test_topic_contributes_signal_the_stem_lacks(self):
        import json

        candidates = [
            (uuid.UUID(int=1), "Binary Search", json.dumps(["logarithmic"])),
            (uuid.UUID(int=2), "Graph Traversal", json.dumps(["dijkstra"])),
        ]
        # A stem that alone says little; the topic is what identifies it.
        assert self._fn()("What is its complexity?", "binary search logarithmic", candidates) == (
            uuid.UUID(int=1)
        )

    def test_no_candidates_returns_none(self):
        assert self._fn()("anything", "topic", []) is None


# ── Attribution of a finalized score ──────────────────────────────────────────


class TestAttributePoints:
    def _fn(self):
        from progress.mastery import attribute_points

        return attribute_points

    def _rationale(self, *questions):
        return {"questions": list(questions)}

    def _q(self, qid, *criteria):
        return {
            "question_id": str(qid),
            "criteria": [{"score": s, "max_points": m} for s, m in criteria],
        }

    def test_unscaled_when_teacher_approved_the_recommendation(self):
        q1, q2 = uuid.uuid4(), uuid.uuid4()
        out = self._fn()(
            self._rationale(self._q(q1, (1.0, 1.0)), self._q(q2, (2.0, 3.0))),
            final_score=3.0,
            recommended_score=3.0,
        )
        assert [(p.earned, p.possible) for p in out] == [(1.0, 1.0), (2.0, 3.0)]

    def test_scaled_when_teacher_overrode_upward(self):
        # Teacher doubled the AI's total, so each question's share doubles.
        q1, q2 = uuid.uuid4(), uuid.uuid4()
        out = self._fn()(
            self._rationale(self._q(q1, (1.0, 2.0)), self._q(q2, (1.0, 2.0))),
            final_score=4.0,
            recommended_score=2.0,
        )
        assert [p.earned for p in out] == [2.0, 2.0]
        assert [p.possible for p in out] == [2.0, 2.0]

    def test_scaled_when_teacher_overrode_downward(self):
        q1 = uuid.uuid4()
        out = self._fn()(
            self._rationale(self._q(q1, (4.0, 4.0))),
            final_score=2.0,
            recommended_score=4.0,
        )
        assert out[0].earned == 2.0

    def test_zero_recommendation_spreads_by_weight(self):
        # Documented fallback: no per-question signal exists to scale, so the
        # teacher's award is split in proportion to each question's max_points.
        q1, q2 = uuid.uuid4(), uuid.uuid4()
        out = self._fn()(
            self._rationale(self._q(q1, (0.0, 1.0)), self._q(q2, (0.0, 3.0))),
            final_score=4.0,
            recommended_score=0.0,
        )
        assert [p.earned for p in out] == [1.0, 3.0]

    def test_zero_recommendation_and_zero_possible_is_safe(self):
        q1 = uuid.uuid4()
        out = self._fn()(
            self._rationale(self._q(q1)), final_score=5.0, recommended_score=0.0
        )
        assert out[0].earned == 0.0

    def test_missing_or_unparseable_question_ids_are_dropped(self):
        good = uuid.uuid4()
        rationale = self._rationale(
            self._q(good, (1.0, 1.0)),
            {"question_id": "not-a-uuid", "criteria": []},
            {"criteria": []},
        )
        out = self._fn()(rationale, final_score=1.0, recommended_score=1.0)
        assert [p.question_id for p in out] == [good]

    def test_empty_rationale_yields_nothing(self):
        assert self._fn()({}, 1.0, 1.0) == []
        assert self._fn()({"questions": []}, 1.0, 1.0) == []


# ── Schema / migration surface ────────────────────────────────────────────────


class TestProgressSchemaSurface:
    """Migration 0003 must match the ORM, the way 0002 is checked."""

    def test_question_concept_id_is_nullable_with_set_null(self):
        from db.models import Question

        col = Question.__table__.c.concept_id
        assert col.nullable is True, "an unconfident match must be storable as NULL"
        fk = next(iter(col.foreign_keys))
        assert fk.column.table.name == "concepts"
        # Deleting a concept must never delete graded questions.
        assert fk.ondelete == "SET NULL"

    def test_mastery_table_shape(self):
        from db.models import StudentConceptMastery

        cols = {c.name for c in StudentConceptMastery.__table__.c}
        assert cols == {
            "id",
            "student_id",
            "concept_id",
            "attempts",
            "earned_points",
            "possible_points",
            "mastery",
            "last_graded_at",
        }

    def test_mastery_is_unique_per_student_concept(self):
        from sqlalchemy import UniqueConstraint

        from db.models import StudentConceptMastery

        uniques = [
            c
            for c in StudentConceptMastery.__table__.constraints
            if isinstance(c, UniqueConstraint)
        ]
        assert any(
            {col.name for col in c.columns} == {"student_id", "concept_id"}
            for c in uniques
        )

    def test_indexes_declared_match_the_migration(self):
        from db.models import Question, StudentConceptMastery

        names = {ix.name for ix in Question.__table__.indexes} | {
            ix.name for ix in StudentConceptMastery.__table__.indexes
        }
        assert {
            "ix_questions_concept_id",
            "ix_student_concept_mastery_student_id",
            "ix_student_concept_mastery_concept_id",
        } <= names

    def test_migration_revision_chain(self):
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "db"
            / "migrations"
            / "versions"
            / "0003_student_progress.py"
        )
        spec = importlib.util.spec_from_file_location("mig0003", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.revision == "0003_student_progress"
        assert module.down_revision == "0002_integrity_constraints"
        assert callable(module.upgrade) and callable(module.downgrade)


@pytest.mark.parametrize(
    "mastery,expected_band",
    [(1.0, "high"), (0.8, "high"), (0.79, "mid"), (0.5, "mid"), (0.49, "low"), (0.0, "low")],
)
def test_mastery_is_a_plain_ratio(mastery, expected_band):
    """
    Guards the contract the UI bands against: mastery is earned/possible in
    0..1, not a percentage and not a letter grade.
    """
    assert 0.0 <= mastery <= 1.0
    band = "high" if mastery >= 0.8 else "mid" if mastery >= 0.5 else "low"
    assert band == expected_band
