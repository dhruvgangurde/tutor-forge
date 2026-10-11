# The tutoring fixtures are imported by name from tests/test_tutoring.py and
# requested as test parameters, which ruff reads as redefinitions.
# ruff: noqa: F811
"""
tests/test_tutor_repeated_question.py
-------------------------------------
A hint must add something, not ask the tutor's last question again.

Seen live (Gemini, "what is the pivot in quick sort?"): Hint 1 was the opening
question in other words. The hint prompt never saw what the tutor had said, so
it could not avoid it. Now it does, and the guard drops a near-repeat.

Fake model outputs only. No model, no network.
"""

from unittest.mock import MagicMock

import pytest

from agents.tutor.nodes import advance_hint_node, generate_guiding_question_node
from agents.tutor.output_guard import (
    PATH_HINT,
    PATH_NEW_QUESTION,
    PATH_REPLY,
    RULE_FALLBACK,
    RULE_FALLBACK_KEPT_TEXT,
    RULE_REPEATED_QUESTION,
    TutorTurn,
    apply_guards,
)
from agents.tutor.prompts import (
    GUARD_FALLBACK_NON_ANSWER,
    GUARD_FALLBACK_QUESTION,
    HINT_AFTER_LAST_MESSAGE,
)
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

_PIVOT_Q = "what is the pivot in quick sort?"
#: The live opening question and the live Hint 1 that asked it again.
_OPENING = (
    "When sorting an array using quick sort, how does the algorithm use a chosen element to rearrange "
    "the other values around it?"
)
_LIVE_HINT_1 = (
    "When you are sorting an array using this method, how do you use a chosen element to help organize "
    "the rest of the values around it?"
)


def _guard(text, *, path=PATH_HINT, level=1, previous=_OPENING, non_answer=False, reply=None):
    return apply_guards(
        text,
        TutorTurn(
            path=path,
            hint_level=level,
            question=_PIVOT_Q,
            course_text="",
            student_reply=reply,
            is_non_answer=non_answer,
            previous_tutor_message=previous,
        ),
    )


# ── The guard ─────────────────────────────────────────────────────────────────


def test_live_hint_that_asks_the_opening_question_again_is_replaced():
    out, tripped = _guard(_LIVE_HINT_1)
    assert out == GUARD_FALLBACK_QUESTION
    assert tripped == [RULE_REPEATED_QUESTION, RULE_FALLBACK]


def test_a_hint_keeps_its_new_pointer_when_only_its_question_repeats():
    pointer = "Look at where the chosen element ends up once the pass is finished."
    out, tripped = _guard(pointer + " " + _LIVE_HINT_1)
    assert out == pointer + " " + GUARD_FALLBACK_QUESTION
    assert tripped == [RULE_REPEATED_QUESTION, RULE_FALLBACK_KEPT_TEXT]


@pytest.mark.parametrize(
    "hint",
    [
        (  # the second sentence of a live Hint 2: a new, more specific step
            "When you scan through the array with a loop variable, what action do you take with that "
            "boundary index before swapping elements?"
        ),
        "Where does the chosen element end up after the last swap?",
        "Why?",  # too short to judge
    ],
)
def test_a_new_question_is_kept(hint):
    assert _guard(hint) == (hint, [])


def test_the_topic_words_alone_do_not_make_a_repeat():
    # Both ask about the pivot in quick sort; that is the topic, not a repeat.
    previous = "What do you think the pivot in quick sort is for?"
    hint = "Which element of the array does quick sort pick as the pivot first?"
    assert _guard(hint, previous=previous) == (hint, [])


def test_a_repeat_after_no_is_dropped():
    out, tripped = _guard(_LIVE_HINT_1, path=PATH_REPLY, level=0, non_answer=True, reply="no")
    assert out == GUARD_FALLBACK_NON_ANSWER
    assert RULE_REPEATED_QUESTION in tripped


def test_a_reply_may_ask_the_question_again_to_steer_back():
    # Live: after an off-topic message the tutor restated its question. Wanted.
    text = "That is not related to the question we are working on. " + _OPENING
    assert _guard(text, path=PATH_REPLY, level=0, reply="what is the capital of France?") == (text, [])


def test_a_reply_to_an_attempt_may_ask_again():
    text = "Not quite. " + _LIVE_HINT_1
    _out, tripped = _guard(text, path=PATH_REPLY, level=1, reply="is it the largest element?")
    assert RULE_REPEATED_QUESTION not in tripped


@pytest.mark.parametrize("path, level", [(PATH_NEW_QUESTION, 0), (PATH_HINT, 4)])
def test_not_applied_to_a_new_question_or_the_full_explanation(path, level):
    _out, tripped = _guard(_LIVE_HINT_1, path=path, level=level)
    assert RULE_REPEATED_QUESTION not in tripped


def test_without_a_previous_message_nothing_is_compared():
    assert _guard(_LIVE_HINT_1, previous=None) == (_LIVE_HINT_1, [])


# ── The node ──────────────────────────────────────────────────────────────────


def _hint_call(state, output="Where does the chosen element end up after the last swap?"):
    gemini = MagicMock()
    gemini.generate.return_value = output
    retrieval = RetrievalService.__new__(RetrievalService)
    response = advance_hint_node(state, retrieval, gemini)["response"]
    call = gemini.generate.call_args
    return response, call.args[0], call.kwargs["system_instruction"]


@pytest.mark.parametrize("level", [0, 1, 2])  # escalates to Hints 1-3
def test_hint_prompt_carries_the_last_message_as_untrusted_data(level):
    state = _state(hint_level=level, question=_PIVOT_Q)
    state["tutor_last_message"] = _OPENING
    _response, prompt, system = _hint_call(state)
    assert "<<UNTRUSTED YOUR LAST MESSAGE>>\n" + _OPENING in prompt
    assert HINT_AFTER_LAST_MESSAGE in system
    assert _OPENING not in system  # never in the system instruction


def test_full_explanation_does_not_get_the_last_message():
    state = _state(hint_level=3, question=_PIVOT_Q)  # escalates to level 4
    state["tutor_last_message"] = _OPENING
    _response, prompt, system = _hint_call(state)
    assert "YOUR LAST MESSAGE" not in prompt
    assert HINT_AFTER_LAST_MESSAGE not in system


def test_node_drops_the_live_repeat():
    state = _state(hint_level=0, question=_PIVOT_Q)
    state["tutor_last_message"] = _OPENING
    response, _prompt, _system = _hint_call(state, output=_LIVE_HINT_1)
    assert response == GUARD_FALLBACK_QUESTION


def test_new_question_prompt_is_unchanged():
    state = _state(question=_PIVOT_Q)
    gemini = MagicMock()
    gemini.generate.return_value = "What do you think?"
    generate_guiding_question_node(state, RetrievalService.__new__(RetrievalService), gemini)
    assert "YOUR LAST MESSAGE" not in gemini.generate.call_args.args[0]


# ── The API ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hint_request_sends_the_tutors_last_message(client, student_token, ready_course_id):
    retrieval = _make_query_scoring_retrieval({_PIVOT_Q: 0.70})
    gemini = _install_tutor_mocks(retrieval)
    gemini.generate.side_effect = [_OPENING, _LIVE_HINT_1]
    try:
        sid = await _new_session(client, student_token, ready_course_id)
        opening = await _chat(client, student_token, sid, _PIVOT_Q)
        hint = await _hint(client, student_token, sid)
    finally:
        _clear_tutor_mocks()

    assert opening.status_code == 200 and hint.status_code == 200
    hint_prompt = gemini.generate.call_args_list[1].args[0]
    assert "<<UNTRUSTED YOUR LAST MESSAGE>>\n" + opening.json()["response"] in hint_prompt
    # The live repeat never reaches the student.
    assert hint.json()["response"] == GUARD_FALLBACK_QUESTION
