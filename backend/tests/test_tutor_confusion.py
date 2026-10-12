# The tutoring fixtures are imported by name from tests/test_tutoring.py and
# requested as test parameters, which ruff reads as redefinitions.
# ruff: noqa: F811
"""
tests/test_tutor_confusion.py
-----------------------------
"no" or "I don't get it" after a full explanation gets the explanation again
from another angle, not a smaller quiz question.

Seen live (session 5c6672fa): the full explanation of dynamic programming
ended "Does this explanation help?"; the student said "no"; the tutor asked
"What kind of problems ... are we trying to solve?". The non-answer step
("break THE QUESTION into a smaller first step") was written for "I don't
know" to a tutor question at the low rungs; after an explanation it is the
wrong move.

Fake model outputs only. No model, no network.
"""

from unittest.mock import MagicMock

import pytest

from agents.tutor.nodes import generate_guiding_question_node
from agents.tutor.prompts import EXPLAIN_FROM_LEVEL, reply_instruction
from agents.tutor.query_resolution import is_confusion
from retrieval.service import RetrievalService
from tests.test_tutor_output_regressions import _state
from tests.test_tutoring import (  # noqa: F401  (fixtures used by name)
    _chat,
    _clear_tutor_mocks,
    _hint,
    _install_tutor_mocks,
    _make_query_scoring_retrieval,
    _new_session,
    client,
    ready_course_id,
    setup_db,
    student_token,
)

_SMALLER_STEP = "smaller, simpler first step"
_AGAIN = "Explain it again from a different angle"


@pytest.mark.parametrize("text", [
    "no", "I don't know", "i dont get it", "I don't understand this", "still confused",
    "that didn't help", "explain again", "can you explain it simpler", "you lost me", "too complicated",
])
def test_confused_replies(text):
    assert is_confusion(text)


@pytest.mark.parametrize("text", ["the first row is zero", "is it the largest element?", "matrix", "because i > j"])
def test_attempts_are_not_confusion(text):
    assert not is_confusion(text)


def test_the_instruction_for_a_confused_reply_after_an_explanation():
    again = reply_instruction(True, 4, explain_again=True)
    assert _AGAIN in again and 'labelled "Example"' in again
    assert _SMALLER_STEP not in again
    assert "ONLY on the provided course context" in again   # grounding unchanged
    assert "Ask at most one question" in again               # output rules unchanged
    assert _AGAIN not in reply_instruction(True, 4)          # off by default
    assert _SMALLER_STEP in reply_instruction(True, 1)


def _system(state):
    gemini = MagicMock()
    gemini.generate.return_value = "Think of it this way. Example: two items. Which one fits first?"
    generate_guiding_question_node(state, RetrievalService.__new__(RetrievalService), gemini)
    return gemini.generate.call_args.kwargs["system_instruction"]


@pytest.mark.parametrize("reply", ["no", "i dont get it", "that didn't help"])
def test_after_a_full_explanation_a_confused_reply_gets_the_explanation_again(reply):
    state = {**_state(reply=reply, non_answer=True, hint_level=4), "tutor_last_level": 4}
    system = _system(state)
    assert _AGAIN in system and _SMALLER_STEP not in system


def test_at_the_near_answer_rung_too():
    state = {**_state(reply="no", non_answer=True, hint_level=EXPLAIN_FROM_LEVEL), "tutor_last_level": 3}
    assert _AGAIN in _system(state)


def test_below_hint_3_a_non_answer_still_gets_a_smaller_step():
    state = {**_state(reply="no", non_answer=True, hint_level=1), "tutor_last_level": 1}
    system = _system(state)
    assert _SMALLER_STEP in system and _AGAIN not in system


def test_an_attempt_after_an_explanation_is_judged_as_before():
    state = {**_state(reply="the first row is all zeros", hint_level=4), "tutor_last_level": 4}
    system = _system(state)
    assert _AGAIN not in system and "Say a reply is correct only when" in system


# ── The service ───────────────────────────────────────────────────────────────

_Q = "how does the partition function keep track of the smaller elements?"
_EXPLANATION = "The index i starts at low - 1 and moves up on each smaller element. Does this help?"
_RETRY = "Think of it this way: the index marks a boundary. Example: for [3, 1, 2]... Which side is 1 on?"


@pytest.mark.asyncio
async def test_api_no_after_the_full_explanation(client, student_token, ready_course_id):
    retrieval = _make_query_scoring_retrieval({_Q: 0.70, "no": 0.35})
    gemini = _install_tutor_mocks(retrieval)
    ask = "What do you think happens next?"
    gemini.generate.side_effect = [ask, ask, ask, ask, _EXPLANATION, _RETRY]
    try:
        sid = await _new_session(client, student_token, ready_course_id)
        assert (await _chat(client, student_token, sid, _Q)).status_code == 200
        for _ in range(4):
            assert (await _hint(client, student_token, sid)).status_code == 200
        reply = await _chat(client, student_token, sid, "no")
    finally:
        _clear_tutor_mocks()

    assert reply.status_code == 200
    data = reply.json()
    assert data["is_grounded"] is True and data["hint_level"] == 4
    assert data["response"].startswith("Think of it this way")  # the example survives at level 4
    system = gemini.generate.call_args.kwargs["system_instruction"]
    assert _AGAIN in system and _SMALLER_STEP not in system
    assert "<<UNTRUSTED THEIR REPLY>>\nno\n" in gemini.generate.call_args.args[0]
