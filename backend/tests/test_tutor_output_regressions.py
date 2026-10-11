"""
tests/test_tutor_output_regressions.py
---------------------------------------
Regression tests for tutor replies seen in a live replay on Ollama qwen2.5:7b.

Each test feeds the model's actual faulty output (a fake; no model is called)
through the real tutor node and asserts on what the student would be shown.
They go through ``generate_guiding_question_node`` / ``advance_hint_node`` only,
so they exercise whatever post-processing the node applies, not a helper in
isolation.
"""

import logging
import re
import uuid
from dataclasses import asdict
from unittest.mock import MagicMock

import pytest

from agents.tutor.nodes import advance_hint_node, generate_guiding_question_node
from agents.tutor.prompts import GUARD_FALLBACK_NON_ANSWER, GUARD_FALLBACK_QUESTION
from core.context_phrasing import strip_context_references
from retrieval.models import Chunk
from retrieval.service import RetrievalService

_PD = "What is potential difference?"

_COURSE_CHUNKS = [
    (
        "Potential difference is the work done per unit charge in moving a charge "
        "between two points. It is measured in volts."
    ),
    (
        "Example 7: A charge of 2 C gains 35.3 J of energy between two points, so the "
        "potential difference is 17.65 V. The circuit is shown in figure 2.14 on page 41."
    ),
]


def _state(*, hint_level=0, reply=None, non_answer=False, question=_PD, course_chunks=None):
    texts = course_chunks if course_chunks is not None else _COURSE_CHUNKS
    chunks = [
        Chunk(text=t, source_file="physics.pdf", page_or_slide=40 + i, course_id=uuid.UUID(int=0))
        for i, t in enumerate(texts)
    ]
    state = {
        "session_id": uuid.uuid4(),
        "question": reply if reply is not None else question,
        "prior_question": question if reply is not None else None,
        "tutor_last_message": "What do you think potential difference measures?" if reply else None,
        "resolved_question": question,
        "retrieval_query_source": "prior_turn" if reply is not None else "message",
        "turn_kind": "reply" if reply is not None else "new_question",
        "is_non_answer": non_answer,
        "hint_level": hint_level,
        "retrieval_result": {
            "query": question,
            "chunks": [asdict(c) for c in chunks],
            "confidence_scores": [0.9 - 0.05 * i for i in range(len(chunks))],
        },
        "pedagogy_trace": {},
    }
    return state


def _run_state(model_output, state, *, hint=False):
    gemini = MagicMock()
    gemini.generate.return_value = model_output
    retrieval = RetrievalService.__new__(RetrievalService)  # real context and citation builders
    node = advance_hint_node if hint else generate_guiding_question_node
    return node(state, retrieval, gemini)["response"]


def _run(model_output, *, hint=False, **state_kwargs):
    gemini = MagicMock()
    gemini.generate.return_value = model_output
    retrieval = RetrievalService.__new__(RetrievalService)  # real context and citation builders
    node = advance_hint_node if hint else generate_guiding_question_node
    return node(_state(**state_kwargs), retrieval, gemini)["response"]


def test_first_turn_does_not_invent_something_the_student_said():
    out = _run(
        "You mentioned a formula for this earlier. "
        "What do you think happens to a charge as it moves between two points?"
    )
    assert "mentioned" not in out.lower()
    assert out == "What do you think happens to a charge as it moves between two points?"


def test_level_0_does_not_state_the_definition():
    out = _run(
        "Potential difference is the work done per unit charge between two points. "
        "What do you think has to happen to move a charge from one point to another?"
    )
    assert "work done per unit charge" not in out
    assert out == "What do you think has to happen to move a charge from one point to another?"


def test_no_verdict_after_a_non_answer():
    out = _run(
        "You didn't quite answer the question, but you're on the right track. "
        "What does it take to push something uphill?",
        reply="no",
        non_answer=True,
    )
    assert "right track" not in out
    assert "quite" not in out
    assert out == "What does it take to push something uphill?"


def test_worked_example_details_do_not_leak_into_a_hint():
    out = _run(
        "In Example 7, the potential difference came out to 17.65 V, as shown in figure 2.14. "
        "What two quantities would you compare to find it?",
        hint=True,
        hint_level=0,  # advance_hint_node escalates to Hint 1
    )
    assert "Example 7" not in out
    assert "17.65" not in out
    assert "2.14" not in out
    assert out == "What two quantities would you compare to find it?"


def test_raw_latex_is_turned_into_plain_text():
    out = _run(
        r"Think about the relationship ( V = \frac{W}{Q} ). What does W stand for here?",
        hint_level=3,
    )
    assert "\\" not in out
    assert "frac" not in out
    assert out == "Think about the relationship V = W/Q. What does W stand for here?"


def test_only_the_first_of_several_questions_is_kept():
    out = _run(
        "What is work? How is it related to charge? Can you connect the two?",
        reply="something to do with energy",
        hint_level=1,
    )
    assert out == "What is work?"


# ── Round 1c: a second live replay (qwen2.5:7b, hint level 0) ─────────────────

#: Shown to the student, verbatim, after they answered "no".
_LIVE_AFTER_NO = (
    "It seems like you're not sure about the concept of potential difference., potential "
    "difference is related to the work required to move charges against the force of "
    "attraction or repulsion between them. Can you try to describe in your own words what "
    "potential difference means in this context?"
)
_LIVE_HEAD = "It seems like you're not sure about the concept of potential difference."


def test_context_qualifier_opening_a_later_sentence_leaves_no_broken_join():
    raw = (
        _LIVE_HEAD + " According to the context provided, potential difference is related "
        "to the work required to move charges."
    )
    assert strip_context_references(raw) == (
        _LIVE_HEAD + " Potential difference is related to the work required to move charges."
    )


def test_live_garbled_join_is_repaired_into_sentences():
    out = _run(_LIVE_AFTER_NO, hint_level=3)  # Hint 3: only the join repair applies
    assert ".," not in out
    assert ", potential" not in out
    assert "potential difference. Potential difference is related to the work" in out


@pytest.mark.parametrize("reply", ["no", "I don't know"])
def test_no_course_statement_after_a_non_answer_at_level_0(reply):
    out = _run(_LIVE_AFTER_NO, reply=reply, non_answer=True)
    assert "related to the work" not in out
    assert "not sure about the concept" not in out
    assert out.startswith("Can you try to describe in your own words what potential difference means")


def test_live_reply_after_no_ends_as_one_clean_question():
    out = _run(_LIVE_AFTER_NO, reply="no", non_answer=True)
    assert out == "Can you try to describe in your own words what potential difference means?"


@pytest.mark.parametrize(
    "state, expected",
    [
        ({}, GUARD_FALLBACK_QUESTION),
        ({"reply": "no", "non_answer": True}, GUARD_FALLBACK_NON_ANSWER),
    ],
)
def test_question_about_the_passage_is_dropped(state, expected):
    out = _run(
        "Can you recall the definition given in the passage about voltage or potential difference?",
        **state,
    )
    assert out == expected


def test_trailing_description_of_clause_is_cut():
    out = _run(
        "How would you explain what it takes to move a charge, based on the description of "
        "overcoming the force of attraction or repulsion between charges?"
    )
    assert out == "How would you explain what it takes to move a charge?"


def test_tutor_logs_carry_no_reply_text(caplog):
    reply = (
        "The student is thinking about the context here. Potential difference is the work "
        "done per unit charge. What moves a charge?"
    )
    state = _state()
    with caplog.at_level(logging.DEBUG, logger="agents.tutor"):
        _run_state(reply, state)

    messages = [r.getMessage() for r in caplog.records if r.name.startswith("agents.tutor")]
    assert "[TUTOR-GUARD] rule=third_person_learner path=new_question hint_level=0" in messages
    assert "[TUTOR-GUARD] rule=unhandled_context_mention path=new_question hint_level=0" in messages
    for message in messages:
        assert re.fullmatch(r"\[TUTOR-GUARD\] rule=\w+ path=\w+ hint_level=\d+", message), message
        assert str(state["session_id"]) not in message
    joined = " ".join(messages).lower()
    for fragment in ("student is thinking", "context here", "work done", "moves a charge"):
        assert fragment not in joined


# ── Round 1d: a third live replay ─────────────────────────────────────────────


def test_closing_based_on_the_description_is_removed():
    out = _run("What do you think potential difference means based on the description?")
    assert out == "What do you think potential difference means?"


def test_course_material_qualifier_inside_a_question_is_removed():
    out = _run("Do you remember the key idea from the course material about resistors in series?")
    assert out == "Do you remember the key idea about resistors in series?"


def test_yes_no_question_restating_the_course_after_no_is_dropped():
    out = _run(
        "Do you think potential difference is related to the work or energy required to move "
        "charges between two points?",
        reply="no",
        non_answer=True,
    )
    assert out == GUARD_FALLBACK_NON_ANSWER


def test_hint_with_a_yes_no_claim_question_is_unchanged():
    question = "Do you think potential difference is the work done per unit charge?"
    assert _run(question, hint=True, hint_level=0) == question  # escalates to Hint 1


# ── Round 1e: answer leaks seen live at hint levels 0-2 ───────────────────────

_PD_LIVE_COURSE = [
    (
        "Potential difference is the work done per unit charge in moving a charge between two "
        "points. Work is required to move a charge against the force of attraction or repulsion "
        "between charges."
    ),
]


def test_pronoun_restatement_of_the_course_after_a_wrong_attempt_is_removed():
    out = _run(
        "That's not quite right. The potential difference is not the product of current and "
        "resistance. It's the work or energy per unit charge utilized when charges are moved to "
        "overcome the force of attraction or repulsion. Can you try to define it based on that idea?",
        reply="is it current times resistance?",
        hint_level=1,
        course_chunks=_PD_LIVE_COURSE,
    )
    assert out == (
        "That's not quite right. The potential difference is not the product of current and "
        "resistance. Can you try to define it?"
    )


_SERIES_Q = "formula for equivalent resistance of resistors in series"
_SERIES_COURSE = [
    (
        "For resistors in series, the equivalent resistance is the sum of the individual "
        "resistances: R_T = R1 + R2 + R3. The same current flows through each resistor."
    ),
]


def test_formula_in_a_hint_below_hint_3_is_removed():
    # The live hint, verbatim up to "Take the next step:"; its elided ending is
    # completed with a question so the test can check that it survives.
    out = _run(
        "So, if you have two resistors R1 and R2 in series, the equivalent resistance R_T is: "
        "R_T = R1 + R2 Take the next step: what happens if you add a third resistor?",
        hint=True,
        hint_level=1,  # advance_hint_node escalates to Hint 2
        question=_SERIES_Q,
        course_chunks=_SERIES_COURSE,
    )
    assert out == "Take the next step: what happens if you add a third resistor?"


# ── Round 1f: a real attempt keeps its verdict when the fallback fires ───────


def test_fallback_after_a_real_attempt_keeps_the_verdict():
    out = _run(
        "That's not quite right. The maximum element is not simply the first one. "
        "It's found by comparing every element with the current maximum and keeping the larger one.",
        reply="is it the largest element?",
        hint_level=1,
        question="how do you find the maximum element in an array?",
        course_chunks=[
            (
                "To find the maximum element in an array, compare every element with the current "
                "maximum and keep the larger one. Start with the first element as the current maximum."
            )
        ],
    )
    assert out == (
        "That's not quite right. The maximum element is not simply the first one. "
        + GUARD_FALLBACK_QUESTION
    )
