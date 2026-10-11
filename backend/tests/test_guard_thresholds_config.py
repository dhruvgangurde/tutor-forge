"""
tests/test_guard_thresholds_config.py
-------------------------------------
The tutor guard and quiz check thresholds live in core/config.py (CLAUDE.md
invariant 5). They are fixed in code, not read from the environment, and every
module that uses one reads it from there.
"""

import dataclasses

import pytest

import tutoring.service as tutoring_service
from agents.assessment import validation
from agents.tutor import output_guard, query_resolution
from core.config import (
    QUESTION_CHECKS,
    TUTOR_GUARD,
    QuestionCheckThresholds,
    Settings,
    TutorGuardThresholds,
)


def test_the_calibrated_values_are_unchanged():
    assert TUTOR_GUARD == TutorGuardThresholds(
        claim_min_shared_words=3,
        claim_background_share=0.5,
        claim_min_sentences_for_background=4,
        definition_grounded_share=0.6,
        repeat_overlap=0.6,
        repeat_min_words=3,
        definition_max_term_words=5,
        graded_question_asked_share=0.8,
        graded_question_stem_share=0.6,
    )
    assert QUESTION_CHECKS == QuestionCheckThresholds(
        key_support_share=0.6,
        restates_key_ratio=0.8,
        restates_key_min_chars=10,
        min_questions_for_share=5,
    )


@pytest.mark.parametrize("thresholds", [TUTOR_GUARD, QUESTION_CHECKS])
def test_thresholds_cannot_be_changed_at_runtime(thresholds):
    name = dataclasses.fields(thresholds)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(thresholds, name, 0)


def test_thresholds_are_not_environment_settings(monkeypatch):
    names = {f.name for f in dataclasses.fields(TUTOR_GUARD)} | {
        f.name for f in dataclasses.fields(QUESTION_CHECKS)
    }
    assert not names & set(Settings.model_fields)
    monkeypatch.setenv("REPEAT_OVERLAP", "0.1")
    monkeypatch.setenv("KEY_SUPPORT_SHARE", "0.1")
    assert TutorGuardThresholds().repeat_overlap == 0.6
    assert QuestionCheckThresholds().key_support_share == 0.6


# ── Each module reads the value from core/config.py ──────────────────────────


def test_the_guard_reads_the_repeat_overlap(monkeypatch):
    previous = [{"compare", "element", "boundary", "swap"}]
    question = "How do you compare each element with the boundary?"
    assert output_guard._repeats_question(question, previous, set())
    monkeypatch.setattr(
        output_guard, "TUTOR_GUARD", dataclasses.replace(TUTOR_GUARD, repeat_overlap=1.01)
    )
    assert not output_guard._repeats_question(question, previous, set())


def test_the_definition_detector_reads_the_term_length(monkeypatch):
    question = "what is a red black balanced binary search tree?"
    assert query_resolution.definition_term(question) is None
    monkeypatch.setattr(
        query_resolution,
        "TUTOR_GUARD",
        dataclasses.replace(TUTOR_GUARD, definition_max_term_words=6),
    )
    assert query_resolution.definition_term(question) == "red black balanced binary search tree"


def test_the_graded_question_match_reads_its_shares(monkeypatch):
    asked = "what is the pivot used for in partition"
    stem = "Which element is the pivot in the partition step of quick sort?"
    assert not tutoring_service._is_graded_question(asked, "pivot used", stem)
    monkeypatch.setattr(
        tutoring_service,
        "TUTOR_GUARD",
        dataclasses.replace(
            TUTOR_GUARD, graded_question_asked_share=0.0, graded_question_stem_share=0.0
        ),
    )
    assert tutoring_service._is_graded_question(asked, "pivot used", stem)


def test_the_question_checks_read_the_key_support_share(monkeypatch):
    evidence = ["The pivot is the last element of the array."]
    q = {
        "question_type": "mcq",
        "stem": "Which element is the pivot?",
        "options": ["The first one", "A random index", "The median value", "Any element"],
        "correct_answer": "A",
        "bloom_level": "remember",
    }
    assert validation.REASON_UNSUPPORTED_KEY in validation.question_problems(q, evidence)
    monkeypatch.setattr(
        validation, "QUESTION_CHECKS", dataclasses.replace(QUESTION_CHECKS, key_support_share=0.0)
    )
    assert validation.question_problems(q, evidence) == []


def test_the_bloom_share_reads_the_minimum_quiz_length(monkeypatch):
    blooms = ["remember"] * 4
    assert validation.higher_order_needed(blooms, 0.5) == 0
    monkeypatch.setattr(
        validation, "QUESTION_CHECKS", dataclasses.replace(QUESTION_CHECKS, min_questions_for_share=4)
    )
    assert validation.higher_order_needed(blooms, 0.5) == 2
