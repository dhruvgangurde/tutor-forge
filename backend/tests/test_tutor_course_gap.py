# The tutoring fixtures are imported by name from tests/test_tutoring.py and
# requested as test parameters, which ruff reads as redefinitions.
# ruff: noqa: F811
"""
tests/test_tutor_course_gap.py
------------------------------
A reply that only says "this course doesn't cover that part" when the gate has
just found the topic in the course.

Seen live (session 5c6672fa): as a whole reply to "0/1 knapsack" while pages
11-13 teach it; glued to the fallback question one turn later; and as the
entire full explanation of a grounded C++ question, which also used up the
last rung. The node now asks once more, saying the passages do cover it, and
otherwise shows a fixed message counted as a refusal; a hint that ends there
does not move the ladder; the guard never keeps such a sentence as an opening.

Fake model outputs only. No model, no network.
"""

from unittest.mock import MagicMock

import pytest

from agents.tutor.nodes import advance_hint_node, generate_guiding_question_node
from agents.tutor.output_guard import (
    PATH_REPLY,
    RULE_FALLBACK_KEPT_TEXT,
    TutorTurn,
    apply_guards,
    is_course_gap_reply,
)
from agents.tutor.prompts import COURSE_GAP_MESSAGE, COURSE_GAP_RETRY, GUARD_FALLBACK_QUESTION
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

#: The three live replies, verbatim.
_LIVE_GAPS = [
    "This course doesn't cover that part. Which of the available topics would you like to explore instead?",
    (
        "This course doesn't cover that part. Let's work through this together. What do you already know "
        "about this topic, in your own words?"
    ),
    "This course doesn't cover that part.",
]
_GOOD = "The program reads the dimensions into an array, then calls matrixChain. What does that call return?"


#: Shown live on 2026-10-12 as ordinary tutor turns, with citations, at the
#: 0.62 bar (adjacent-06 and adjacent-07 in groundedness_calibration.json).
_LIVE_MISSED_GAPS = [
    "Provided, there is no mention or explanation of the travelling salesman problem.",
    "I must inform you that this course does not cover how binary search works.",
]


@pytest.mark.parametrize("text", _LIVE_GAPS[0:1] + _LIVE_GAPS[2:3] + _LIVE_MISSED_GAPS + [
    "The course material does not cover this.",
    "That isn't covered in this course. What would you like to look at instead?",
    "There is no mention or explanation of the travelling salesman problem.",
    "That is not explained in the provided material.",
])
def test_a_reply_that_only_says_the_course_does_not_cover_it(text):
    assert is_course_gap_reply(text)


@pytest.mark.parametrize("text", [
    _GOOD,
    (
        "The course doesn't cover recursion in general, but it does show the base case: it stops when "
        "low >= high. What happens to a range of one element?"
    ),
    (
        "There is no mention of negative edge weights in this step, so Dijkstra's greedy choice holds "
        "here. Which vertex does it pick next?"
    ),
    "Unfortunately the pivot does not end at index 0. Where does one partition leave it?",
    "",
    None,
])
def test_an_explanation_is_not_a_gap_reply(text):
    assert not is_course_gap_reply(text)


def test_the_live_glued_reply_is_a_gap_reply_too():
    assert not is_course_gap_reply(_LIVE_GAPS[1])  # two non-gap sentences: the guard, not the node, made it
    # ...but a gap sentence is never kept as the opening ahead of the fallback.
    out, tripped = apply_guards(
        "This course doesn't cover that part. Potential difference is the work done per unit charge.",
        TutorTurn(
            path=PATH_REPLY, hint_level=0, question="What is potential difference?",
            course_text="Potential difference is the work done per unit charge between two points.",
            student_reply="is it about batteries?",
        ),
    )
    assert out == GUARD_FALLBACK_QUESTION
    assert "doesn't cover" not in out
    assert RULE_FALLBACK_KEPT_TEXT not in tripped


# ── The node ──────────────────────────────────────────────────────────────────


def _run(outputs, state, *, hint=False):
    gemini = MagicMock()
    gemini.generate.side_effect = list(outputs)
    node = advance_hint_node if hint else generate_guiding_question_node
    out = node(state, RetrievalService.__new__(RetrievalService), gemini)
    return out, gemini


def test_a_gap_reply_is_asked_again_with_the_passages_vouched_for():
    out, gemini = _run([_LIVE_GAPS[0], _GOOD], _state(hint_level=3))
    assert gemini.generate.call_count == 2
    assert COURSE_GAP_RETRY in gemini.generate.call_args_list[1].kwargs["system_instruction"]
    assert COURSE_GAP_RETRY not in gemini.generate.call_args_list[0].kwargs["system_instruction"]
    assert out["response"] == _GOOD
    assert not out["pedagogy_trace"].get("refused")


def test_a_second_gap_reply_becomes_the_fixed_message_and_a_refusal():
    out, gemini = _run([_LIVE_GAPS[0], _LIVE_GAPS[2]], _state(reply="0/1 knapsack", hint_level=0))
    assert gemini.generate.call_count == 2  # once more, never a loop
    assert out["response"] == COURSE_GAP_MESSAGE
    assert out["pedagogy_trace"]["refused"] is True
    assert out["pedagogy_trace"]["refusal_kind"] == "course_gap"
    assert out["citations"]  # the topic is grounded: the sources still show


def test_a_full_explanation_that_is_only_a_gap_does_not_move_the_ladder():
    out, _ = _run([_LIVE_GAPS[2], _LIVE_GAPS[2]], _state(hint_level=3), hint=True)  # 3 -> 4 attempted
    assert out["response"] == COURSE_GAP_MESSAGE
    assert out["hint_level"] == 3
    assert out["pedagogy_trace"]["hint_level"] == 3


def test_a_hint_that_recovers_on_the_retry_moves_the_ladder():
    out, _ = _run([_LIVE_GAPS[2], _GOOD], _state(hint_level=3), hint=True)
    assert out["hint_level"] == 4
    assert out["response"] == _GOOD


def test_a_normal_reply_makes_one_call():
    out, gemini = _run([_GOOD], _state(hint_level=3))
    assert gemini.generate.call_count == 1
    assert out["response"] == _GOOD


# ── The service ───────────────────────────────────────────────────────────────

_Q = "how does the partition function keep track of the smaller elements?"
_ASK = "What do you think happens next?"


@pytest.mark.asyncio
async def test_api_hint_gap_keeps_the_level_and_the_next_hint_can_still_reach_the_top(
    client, student_token, ready_course_id
):
    retrieval = _make_query_scoring_retrieval({_Q: 0.70})
    gemini = _install_tutor_mocks(retrieval)
    gemini.generate.side_effect = [_ASK, _ASK, _ASK, _ASK, _LIVE_GAPS[2], _LIVE_GAPS[2], _GOOD]
    try:
        sid = await _new_session(client, student_token, ready_course_id)
        assert (await _chat(client, student_token, sid, _Q)).status_code == 200
        for _ in range(3):
            assert (await _hint(client, student_token, sid)).status_code == 200
        gap = await _hint(client, student_token, sid)
        recovered = await _hint(client, student_token, sid)
        messages = await client.get(f"/tutor/sessions/{sid}/messages",
                                    headers={"Authorization": f"Bearer {student_token}"})
    finally:
        _clear_tutor_mocks()

    assert gap.status_code == 200
    assert gap.json()["response"] == COURSE_GAP_MESSAGE
    assert gap.json()["hint_level"] == 3
    assert recovered.json()["hint_level"] == 4 and recovered.json()["response"] == _GOOD
    gap_msg = messages.json()[-2]
    assert gap_msg["is_refusal"] is True and gap_msg["hint_level"] == 3


@pytest.mark.asyncio
async def test_api_chat_gap_is_persisted_as_a_refusal(client, student_token, ready_course_id):
    retrieval = _make_query_scoring_retrieval({_Q: 0.70})
    gemini = _install_tutor_mocks(retrieval)
    gemini.generate.side_effect = [_ASK, _LIVE_GAPS[0], _LIVE_GAPS[2]]
    try:
        sid = await _new_session(client, student_token, ready_course_id)
        assert (await _chat(client, student_token, sid, _Q)).status_code == 200
        reply = await _chat(client, student_token, sid, "0/1 knapsack")
        messages = await client.get(f"/tutor/sessions/{sid}/messages",
                                    headers={"Authorization": f"Bearer {student_token}"})
    finally:
        _clear_tutor_mocks()

    assert reply.json()["response"] == COURSE_GAP_MESSAGE
    assert reply.json()["is_grounded"] is True
    assert messages.json()[-1]["is_refusal"] is True


@pytest.mark.asyncio
async def test_a_new_question_answered_with_the_gap_message_is_not_the_open_question(
    client, student_token, ready_course_id
):
    """
    The gate passed, so the question became the session's open question even
    though the tutor ended up saying the course does not cover it; the next
    hint then worked on a topic the student had just been told was not there.
    Like an out-of-corpus refusal, it now leaves nothing open.
    """
    import uuid

    from sqlalchemy import select

    from agents.tutor.prompts import NO_OPEN_QUESTION_FOR_HINT
    from db.models import TutoringSession
    from tests.test_tutoring import _TestSessionFactory

    retrieval = _make_query_scoring_retrieval({_Q: 0.70})
    gemini = _install_tutor_mocks(retrieval)
    gemini.generate.side_effect = [_LIVE_GAPS[2], _LIVE_GAPS[2], _ASK, _ASK]
    try:
        sid = await _new_session(client, student_token, ready_course_id)
        reply = await _chat(client, student_token, sid, _Q)
        hint = await _hint(client, student_token, sid)
    finally:
        _clear_tutor_mocks()

    assert reply.json()["response"] == COURSE_GAP_MESSAGE
    async with _TestSessionFactory() as db:
        session = (
            await db.execute(select(TutoringSession).where(TutoringSession.id == uuid.UUID(sid)))
        ).scalar_one()
    assert session.anchor_message_id is None
    assert hint.status_code == 400
    assert hint.json() == {"detail": NO_OPEN_QUESTION_FOR_HINT}


# ── A gap sentence the model glued to the rest of its reply ───────────────────


@pytest.mark.parametrize("path, level", [("new_question", 0), ("hint", 2), ("reply", 0), ("hint", 4)])
def test_an_empty_gap_sentence_glued_to_a_reply_is_removed(path, level):
    # The live turn after "0/1 knapsack": the model wrote it like this itself.
    out, tripped = apply_guards(
        _LIVE_GAPS[1],
        TutorTurn(path=path, hint_level=level, question="what is 0/1 knapsack?",
                  course_text="The 0/1 knapsack problem takes each item whole or not at all.",
                  student_reply="0/1 knapsack" if path == "reply" else None),
    )
    assert "doesn't cover" not in out
    assert out.endswith("What do you already know about this topic, in your own words?")
    assert "course_gap" in tripped


def test_a_gap_sentence_that_names_what_is_missing_stays():
    text = ("The course doesn't cover the C++ template syntax. The program calls matrixChain(p, n + 1). "
            "What does that call return?")
    out, tripped = apply_guards(
        text, TutorTurn(path="hint", hint_level=4, question="matrix chain code",
                        course_text="It calls matrixChain(p, n + 1) and prints cost[1][n]."),
    )
    assert out == text and "course_gap" not in tripped


# ── The model copying the out-of-corpus refusal ───────────────────────────────
# Seen live (Gemini Test 2, 2026-10-13): after a refused question, "Explain
# linear probing versus chaining in hash tables." passed the gate and the model
# answered with the stock refusal word for word, copied from the conversation
# window. It was stored as an ordinary turn with 4 citations and became the
# open question, so the next hint ran on it and ended in the course-gap message.

_OFF = "What is the travelling salesman problem?"
_HASHING = "How do hash tables resolve collisions?"
_PROBING = "Explain linear probing versus chaining in hash tables."


def test_a_reply_that_copies_the_stock_refusal_is_a_refusal():
    from agents.tutor.prompts import OUT_OF_CORPUS_REFUSAL

    out, gemini = _run([OUT_OF_CORPUS_REFUSAL], _state())
    assert out["response"] == OUT_OF_CORPUS_REFUSAL
    assert out["citations"] == []
    assert out["pedagogy_trace"]["refused"] is True
    assert out["pedagogy_trace"]["refusal_kind"] == "out_of_corpus"
    assert gemini.generate.call_count == 1  # no retry: the model has declined the topic


def test_a_hint_that_copies_the_stock_refusal_keeps_the_level():
    from agents.tutor.prompts import OUT_OF_CORPUS_REFUSAL

    out, _ = _run([OUT_OF_CORPUS_REFUSAL], _state(hint_level=1), hint=True)
    assert out["response"] == OUT_OF_CORPUS_REFUSAL
    assert out["citations"] == []
    assert out["hint_level"] == 1


@pytest.mark.asyncio
async def test_api_a_copied_refusal_is_stored_as_one_and_opens_nothing(
    client, student_token, ready_course_id
):
    import uuid

    from sqlalchemy import select

    from agents.tutor.prompts import NO_OPEN_QUESTION_FOR_HINT, OUT_OF_CORPUS_REFUSAL
    from db.models import TutoringSession
    from tests.test_tutoring import _TestSessionFactory

    retrieval = _make_query_scoring_retrieval({_OFF: 0.30, _HASHING: 0.70, _PROBING: 0.70})
    gemini = _install_tutor_mocks(retrieval)
    gemini.generate.side_effect = [_ASK, OUT_OF_CORPUS_REFUSAL, _ASK, _ASK]
    headers = {"Authorization": f"Bearer {student_token}"}
    try:
        sid = await _new_session(client, student_token, ready_course_id)
        assert (await _chat(client, student_token, sid, _OFF)).status_code == 200
        assert (await _chat(client, student_token, sid, _HASHING)).status_code == 200
        reply = await _chat(client, student_token, sid, _PROBING)
        hint = await _hint(client, student_token, sid)
        messages = await client.get(f"/tutor/sessions/{sid}/messages", headers=headers)
    finally:
        _clear_tutor_mocks()

    assert reply.json()["response"] == OUT_OF_CORPUS_REFUSAL
    copied = [m for m in messages.json() if m["role"] == "tutor"][-1]
    assert copied["is_refusal"] is True
    assert copied["citations"] == []
    async with _TestSessionFactory() as db:
        session = (
            await db.execute(select(TutoringSession).where(TutoringSession.id == uuid.UUID(sid)))
        ).scalar_one()
    assert session.anchor_message_id is None
    assert hint.status_code == 400
    assert hint.json() == {"detail": NO_OPEN_QUESTION_FOR_HINT}
