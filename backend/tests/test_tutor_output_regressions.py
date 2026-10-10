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

import uuid
from dataclasses import asdict
from unittest.mock import MagicMock

from agents.tutor.nodes import advance_hint_node, generate_guiding_question_node
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


def _state(*, hint_level=0, reply=None, non_answer=False):
    chunks = [
        Chunk(text=t, source_file="physics.pdf", page_or_slide=40 + i, course_id=uuid.UUID(int=0))
        for i, t in enumerate(_COURSE_CHUNKS)
    ]
    state = {
        "session_id": uuid.uuid4(),
        "question": reply if reply is not None else _PD,
        "prior_question": _PD if reply is not None else None,
        "tutor_last_message": "What do you think potential difference measures?" if reply else None,
        "resolved_question": _PD,
        "retrieval_query_source": "prior_turn" if reply is not None else "message",
        "turn_kind": "reply" if reply is not None else "new_question",
        "is_non_answer": non_answer,
        "hint_level": hint_level,
        "retrieval_result": {
            "query": _PD,
            "chunks": [asdict(c) for c in chunks],
            "confidence_scores": [0.9, 0.85],
        },
        "pedagogy_trace": {},
    }
    return state


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
