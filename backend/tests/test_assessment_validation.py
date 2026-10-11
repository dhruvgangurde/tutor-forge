"""
tests/test_assessment_validation.py
-----------------------------------
Generated questions are checked deterministically; a failing one is
regenerated once and dropped if it still fails.

Seen live (Gemini, a quick sort course): "What is the simplest choice for the
pivot?" (first, middle and last are all simple), a distractor "If low == high"
beside the key "If low >= high", and two of three questions easy recall.

Fake model outputs only. No model, no network.
"""

import json
import logging
import uuid
from unittest.mock import MagicMock

import pytest

from agents.assessment.nodes import generate_questions_node
from agents.assessment.validation import (
    REASON_DISTRACTOR_RESTATES_KEY,
    REASON_MALFORMED_MCQ,
    REASON_TEXT,
    REASON_UNSUPPORTED_KEY,
    REASON_VAGUE_SUPERLATIVE,
    evidence_sentences,
    higher_order_needed,
    question_problems,
)
from core.config import settings
from core.gemini_calls import background_work, in_background

_COURSE = (
    "Quick sort picks the last element, arr[high], as the pivot. "
    "The partition places every element less than or equal to the pivot before it. "
    "The recursion stops when low >= high, because a range of one element is already sorted. "
    "int partition(int arr[], int low, int high) { int pivot = arr[high]; int i = low - 1; }"
)
_EVIDENCE = evidence_sentences([_COURSE])


def _mcq(stem, options, key="A", bloom="remember"):
    return {
        "question_type": "mcq", "stem": stem, "options": options, "correct_answer": key,
        "bloom_level": bloom, "difficulty": "easy", "worked_solution": "", "rubric_criteria": [],
    }


_GOOD = _mcq(
    "Which element does quick sort use as the pivot in this course?",
    ["The last element", "The first element", "The middle element", "A random element"],
)

# ── The checks ────────────────────────────────────────────────────────────────


def test_live_simplest_pivot_stem_is_a_vague_superlative():
    q = _mcq("What is the simplest choice for the pivot?", _GOOD["options"])
    assert REASON_VAGUE_SUPERLATIVE in question_problems(q, _EVIDENCE)


@pytest.mark.parametrize(
    "stem",
    [
        "Which pivot choice is simplest in terms of the number of comparisons?",  # criterion stated
        "What is the best case of quick sort?",                                   # a technical term
        "Which element does quick sort use as the pivot?",
    ],
)
def test_stems_without_a_vague_superlative_pass(stem):
    assert REASON_VAGUE_SUPERLATIVE not in question_problems(_mcq(stem, _GOOD["options"]), _EVIDENCE)


def test_live_base_case_distractor_restates_the_key():
    q = _mcq(
        "When does the recursion stop?",
        ["If low >= high", "If low == high", "If high == 0", "If the array is empty"],
    )
    assert REASON_DISTRACTOR_RESTATES_KEY in question_problems(q, _EVIDENCE)


@pytest.mark.parametrize(
    "options, key",
    [
        (["The last element", "The first element", "The middle element", "A random element"], "A"),
        (["Increment i, then swap", "Swap, then increment i", "Swap only", "Increment only"], "A"),
        (["O(n)", "O(n^2)", "O(log n)", "O(1)"], "A"),  # too short for a ratio
    ],
)
def test_distinct_distractors_pass(options, key):
    q = _mcq("Which is it?", options, key=key, bloom="apply")
    assert question_problems(q, _EVIDENCE) == []


def test_a_recall_key_must_be_stated_in_the_passages():
    q = _mcq("Which element is the pivot?", ["The median of three", "The first one", "The middle one", "A random one"])
    assert question_problems(q, _EVIDENCE) == [REASON_UNSUPPORTED_KEY]
    assert question_problems(_GOOD, _EVIDENCE) == []


def test_numbers_in_a_recall_key_must_appear_exactly():
    q = {"question_type": "numeric", "stem": "Where does i start?", "correct_answer": "low - 2",
         "bloom_level": "remember"}
    assert question_problems(q, _EVIDENCE) == [REASON_UNSUPPORTED_KEY]
    assert question_problems({**q, "correct_answer": "low - 1"}, _EVIDENCE) == []


def test_a_higher_order_key_is_worked_out_so_it_need_not_appear():
    q = {"question_type": "numeric", "stem": "After partitioning [3, 1, 2], where is the pivot?",
         "correct_answer": "7", "bloom_level": "apply"}
    assert question_problems(q, _EVIDENCE) == []


@pytest.mark.parametrize(
    "options, key",
    [(["a", "b", "c"], "A"), (["a", "b", "c", "d"], "E"), (None, "A"), (["a", "b", "c", "d"], "")],
)
def test_malformed_mcq(options, key):
    assert REASON_MALFORMED_MCQ in question_problems(_mcq("Which?", options, key=key), _EVIDENCE)


@pytest.mark.parametrize(
    "blooms, share, needed",
    [
        (["remember"] * 4, 0.4, 0),                                  # under 5: no share
        (["remember"] * 5, 0.4, 2),
        (["remember", "understand", "remember", "apply", "remember"], 0.4, 1),
        (["apply", "analyze", "remember", "remember", "remember"], 0.4, 0),
        (["remember"] * 10, 0.4, 4),
        (["remember"] * 5, 0.6, 3),
        (["evaluate", "create", "remember", "remember", "remember"], 0.4, 0),
    ],
)
def test_higher_order_needed(blooms, share, needed):
    assert higher_order_needed(blooms, share) == needed


# ── The node: regenerate once, then drop ──────────────────────────────────────


def _state(config=None):
    return {
        "course_id": uuid.UUID(int=0),
        "teacher_id": uuid.UUID(int=1),
        "config": config or {"topic": "quick sort", "difficulty": "mixed", "count": 3},
        "retrieved_concepts": [],
        "retrieval_cache": {
            "query": "quick sort",
            "chunks": [{
                "text": _COURSE, "source_file": "lab.pdf", "page_or_slide": 1,
                "course_id": str(uuid.UUID(int=0)), "chunk_id": "",
            }],
            "confidence_scores": [0.9],
        },
        "is_grounded": True,
        "questions": [], "bloom_tags": [], "distractors": [], "rubric_criteria": [], "answer_key": [],
        "assessment_id": uuid.UUID(int=2), "trace_id": None, "status": "running", "error": None,
    }


def _retrieval():
    svc = MagicMock()
    svc.build_context_window.return_value = _COURSE
    return svc


def _short(stem, bloom="understand"):
    return {"question_type": "short_answer", "stem": stem, "options": None,
            "correct_answer": "Key points: the pivot.", "bloom_level": bloom, "difficulty": "medium",
            "worked_solution": "", "rubric_criteria": [{"description": "x", "max_points": 1.0}]}


_VAGUE = _mcq("What is the simplest choice for the pivot?", _GOOD["options"])


class _Flash:
    """
    A fake Flash client that answers by prompt: the batch, then each
    regeneration in turn. The existing distractor-improvement call gets "{}",
    which leaves the options as they were.
    """

    def __init__(self, batch, regens=()):
        self.batch = batch
        self.regens = list(regens)
        self.prompts: list[str] = []
        self.regen_prompts: list[str] = []
        self.background: list[bool] = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if prompt.startswith("Improve the three incorrect MCQ options"):
            return "{}"
        self.background.append(in_background())
        if prompt.startswith("Write ONE assessment question"):
            self.regen_prompts.append(prompt)
            reply = self.regens.pop(0)
        else:
            reply = self.batch
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply) if isinstance(reply, dict) else reply


def _run(batch, regens=(), config=None):
    flash = _Flash(batch, regens)
    result = generate_questions_node(_state(config), retrieval_service=_retrieval(), gemini_flash=flash)
    return result, flash


def test_a_failing_question_is_regenerated_once_and_replaced():
    replacement = _mcq("Which element is used as the pivot?", _GOOD["options"])
    result, flash = _run({"questions": [_short("Explain partition."), _VAGUE]}, [{"question": replacement}])

    assert [q["stem"] for q in result["questions"]] == ["Explain partition.", replacement["stem"]]
    assert len(flash.regen_prompts) == 1
    regen_prompt = flash.regen_prompts[0]
    assert REASON_TEXT[REASON_VAGUE_SUPERLATIVE] in regen_prompt
    assert _VAGUE["stem"] not in regen_prompt          # the rejected question is never sent back
    assert "Explain partition." in regen_prompt         # the others, so it asks something new
    assert "<<UNTRUSTED EXISTING QUESTIONS>>" in regen_prompt


def test_a_question_that_fails_twice_is_dropped():
    result, flash = _run({"questions": [_short("Explain partition."), _VAGUE]}, [{"question": _VAGUE}])
    assert result["status"] != "failed"
    assert [q["stem"] for q in result["questions"]] == ["Explain partition."]
    assert len(result["answer_key"]) == len(result["bloom_tags"]) == 1
    assert len(flash.regen_prompts) == 1               # once, not in a loop


@pytest.mark.parametrize(
    "regen",
    [
        RuntimeError("quota"),                         # the call itself fails
        "not json",
        json.dumps({"question": _short("A different type")}),  # wrong question type
        json.dumps({"nothing": 1}),
    ],
)
def test_an_unusable_regeneration_drops_the_slot(regen):
    result, _ = _run({"questions": [_short("Explain partition."), _VAGUE]}, [regen])
    assert [q["stem"] for q in result["questions"]] == ["Explain partition."]


def test_every_question_failing_returns_the_plain_message():
    result, _ = _run({"questions": [_VAGUE]}, [{"question": _VAGUE}])
    assert result["status"] == "failed"
    assert result["error"] == (
        "I couldn't write good questions from this material this time. "
        "Please try again, or choose a different topic."
    )
    assert result["questions"] == []


def test_the_all_failed_message_is_a_client_safe_domain_error():
    from core.exceptions import NO_USABLE_QUESTIONS_MESSAGE, DomainError, NoUsableQuestions

    err = NoUsableQuestions()
    assert isinstance(err, DomainError)
    assert str(err) == NO_USABLE_QUESTIONS_MESSAGE
    result, _ = _run({"questions": [_VAGUE]}, [{"question": _VAGUE}])
    assert result["error"] == NO_USABLE_QUESTIONS_MESSAGE
    assert _VAGUE["stem"] not in result["error"]


def test_passing_questions_make_no_extra_call():
    result, flash = _run({"questions": [_short("Explain partition."), _GOOD]})
    assert len(result["questions"]) == 2
    assert flash.regen_prompts == []


def test_regeneration_runs_inside_the_background_budget():
    flash = _Flash(
        {"questions": [_short("Explain partition."), _VAGUE]},
        [{"question": _mcq("Which element is used as the pivot?", _GOOD["options"])}],
    )
    with background_work():  # as assessments/service.py runs the graph
        generate_questions_node(_state(), retrieval_service=_retrieval(), gemini_flash=flash)
    assert len(flash.regen_prompts) == 1
    assert flash.background == [True, True]  # the batch and the regeneration


# ── The apply-or-higher share ─────────────────────────────────────────────────


def _recall_quiz(n=5):
    return [_short(f"Recall question {i}.", bloom="remember") for i in range(n)]


def test_prompt_states_the_share_only_for_5_plus_without_a_teacher_mix():
    _, flash = _run({"questions": [_GOOD]}, config={"topic": "t", "count": 5})
    assert "At least 40% of the questions must be at the apply or analyze level" in flash.prompts[0]

    _, flash = _run({"questions": [_GOOD]}, config={"topic": "t", "count": 4})
    assert "apply or analyze level" not in flash.prompts[0]

    _, flash = _run({"questions": [_GOOD]}, config={"topic": "t", "count": 5, "bloom_mix": {"remember": 5}})
    assert "apply or analyze level" not in flash.prompts[0]


def test_a_recall_heavy_quiz_has_recall_questions_regenerated_at_apply_and_analyze():
    applied = _short("Trace partition on [3, 1, 2].", bloom="apply")
    analyzed = _short("Compare two pivot positions.", bloom="analyze")
    result, flash = _run({"questions": _recall_quiz()}, [{"question": applied}, {"question": analyzed}],
                         config={"topic": "t", "count": 5})

    assert [q["bloom_level"] for q in result["questions"]].count("remember") == 3
    assert {"apply", "analyze"} <= set(result["bloom_tags"])
    prompts = flash.regen_prompts
    assert '- Bloom level: "apply"' in prompts[0] and '- Bloom level: "analyze"' in prompts[1]
    assert all(REASON_TEXT["not_higher_order"] in p for p in prompts)


def test_a_replacement_still_at_recall_level_keeps_the_original():
    still_recall = _short("Another recall question.", bloom="remember")
    result, _ = _run({"questions": _recall_quiz()}, [{"question": still_recall}] * 2,
                     config={"topic": "t", "count": 5})
    assert [q["stem"] for q in result["questions"]] == [f"Recall question {i}." for i in range(5)]


def _validation_log(caplog):
    lines = [r.getMessage() for r in caplog.records if "[ASSESSMENT-VALIDATION]" in r.getMessage()]
    assert len(lines) == 1
    return lines[0]


def test_the_log_counts_originals_kept_because_the_replacement_failed(caplog):
    still_recall = _short("Another recall question.", bloom="remember")
    applied = _short("Trace partition on [3, 1, 2].", bloom="apply")
    with caplog.at_level(logging.INFO, logger="agents.assessment"):
        _run({"questions": _recall_quiz()}, [{"question": still_recall}, {"question": applied}],
             config={"topic": "t", "count": 5})
    line = _validation_log(caplog)
    assert "higher_order_needed=2 higher_order_added=1 higher_order_kept_original=1" in line
    assert "Recall question" not in line and "Another recall" not in line and "Trace" not in line


def test_the_log_counts_no_kept_original_when_every_replacement_lands(caplog):
    applied = _short("Trace partition on [3, 1, 2].", bloom="apply")
    analyzed = _short("Compare two pivot positions.", bloom="analyze")
    with caplog.at_level(logging.INFO, logger="agents.assessment"):
        _run({"questions": _recall_quiz()}, [{"question": applied}, {"question": analyzed}],
             config={"topic": "t", "count": 5})
    assert "higher_order_added=2 higher_order_kept_original=0" in _validation_log(caplog)


def test_a_teachers_bloom_mix_is_never_overridden():
    result, flash = _run({"questions": _recall_quiz()},
                         config={"topic": "t", "count": 5, "bloom_mix": {"remember": 5}})
    assert len(result["questions"]) == 5
    assert flash.regen_prompts == []


def test_the_share_comes_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "assessment_min_higher_order_share", 0.6)
    applied = _short("Trace partition on [3, 1, 2].", bloom="apply")
    _, flash = _run({"questions": _recall_quiz()}, [{"question": applied}] * 3,
                    config={"topic": "t", "count": 5})
    assert "At least 60%" in flash.prompts[0]
    assert len(flash.regen_prompts) == 3
