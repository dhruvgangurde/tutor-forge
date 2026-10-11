# The tutoring fixtures are imported by name from tests/test_tutoring.py and
# requested as test parameters, which ruff reads as redefinitions.
# ruff: noqa: F811
"""
tests/test_tutor_open_question.py
---------------------------------
Which question a reply is judged against, after a refusal.

Seen live: once "what is the capital of France?" was refused, every later
reply was judged against that refused question and refused too, including an
on-topic attempt at the quick sort question. A refused question is never the
open question: with none open, a message is judged on its own text.

Real /tutor API on the SQLite test database; retrieval scores are fixed per
query (no embeddings, no model).
"""

import pytest

from tests.test_tutoring import (  # noqa: F401  (fixtures used by name)
    _chat,
    _clear_tutor_mocks,
    _hint,
    _install_tutor_mocks,
    _is_out_of_corpus,
    _make_query_scoring_retrieval,
    _new_session,
    client,
    ready_course_id,
    setup_db,
    student_token,
)

ON = "how does the partition function keep track of the smaller elements?"
OFF = "what is the capital of France?"
ATTEMPT = "i think i starts at 0 and moves right whenever an element is bigger than the pivot"
# The bar is the Ollama one (0.57), set by _install_tutor_mocks.
SCORES = {ON: 0.70, OFF: 0.30, ATTEMPT: 0.66, "no": 0.35}


async def _conversation(client, token, course_id, messages, scores=SCORES):
    retrieval = _make_query_scoring_retrieval(scores)
    _install_tutor_mocks(retrieval)
    try:
        sid = await _new_session(client, token, course_id)
        replies = [await _chat(client, token, sid, m) for m in messages]
    finally:
        _clear_tutor_mocks()
    return sid, replies, retrieval


@pytest.mark.asyncio
async def test_on_topic_attempt_after_a_refused_question_is_not_refused(client, student_token, ready_course_id):
    _, replies, retrieval = await _conversation(client, student_token, ready_course_id, [ON, OFF, ATTEMPT])

    assert _is_out_of_corpus(replies[1].json()["response"])     # the off-topic question is refused
    attempt = replies[2].json()
    assert attempt["is_grounded"] is True
    assert not _is_out_of_corpus(attempt["response"])
    # Judged on its own text, not against the refused question.
    assert retrieval.queries_seen[-1] == ATTEMPT


@pytest.mark.asyncio
async def test_no_in_answer_to_a_real_tutor_question_is_never_refused(client, student_token, ready_course_id):
    _, replies, retrieval = await _conversation(client, student_token, ready_course_id, [ON, "no"])

    reply = replies[1].json()
    assert reply["is_grounded"] is True
    assert not _is_out_of_corpus(reply["response"])
    assert retrieval.queries_seen[-1] == ON                      # judged against the open question


@pytest.mark.asyncio
async def test_no_after_a_refusal_is_judged_on_its_own_text(client, student_token, ready_course_id):
    # The tutor asked nothing at that point, so "no" answers no question.
    _, replies, retrieval = await _conversation(client, student_token, ready_course_id, [ON, OFF, "no"])

    assert _is_out_of_corpus(replies[2].json()["response"])
    assert retrieval.queries_seen[-1] == "no"


@pytest.mark.asyncio
async def test_a_grounded_question_after_a_refusal_opens_the_conversation_again(
    client, student_token, ready_course_id
):
    _, replies, retrieval = await _conversation(client, student_token, ready_course_id, [OFF, ON, "no"])

    assert replies[1].json()["is_grounded"] is True
    assert replies[2].json()["is_grounded"] is True
    assert retrieval.queries_seen[-1] == ON


@pytest.mark.asyncio
async def test_hint_after_a_refused_question_still_has_nothing_to_build_on(
    client, student_token, ready_course_id
):
    from agents.tutor.prompts import NO_OPEN_QUESTION_FOR_HINT

    retrieval = _make_query_scoring_retrieval(SCORES)
    _install_tutor_mocks(retrieval)
    try:
        sid = await _new_session(client, student_token, ready_course_id)
        await _chat(client, student_token, sid, ON)
        await _chat(client, student_token, sid, OFF)
        resp = await _hint(client, student_token, sid)
    finally:
        _clear_tutor_mocks()

    assert resp.status_code == 400
    assert resp.json() == {"detail": NO_OPEN_QUESTION_FOR_HINT}
