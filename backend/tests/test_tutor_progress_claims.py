"""
tests/test_tutor_progress_claims.py
-----------------------------------
A hint never claims progress the student has not made.

Seen live (session 5c6672fa): "Now that you have the table size set up, think
about how you start filling it in. What values do you place in the very first
row...?" right after the student got the size wrong, and "Now that you are
reading the matrix dimensions into your array, how do you think you should
call the function...?" when they had written no code. The claim is kept only
when the student's own messages carry it; otherwise the clause goes and the
question stays.

Fake model outputs only. No model, no network.
"""

import logging
from unittest.mock import MagicMock

import pytest

from agents.tutor.nodes import advance_hint_node
from agents.tutor.output_guard import (
    PATH_HINT,
    PATH_NEW_QUESTION,
    PATH_REPLY,
    RULE_PROGRESS_CLAIM,
    TutorTurn,
    apply_guards,
)
from retrieval.service import RetrievalService
from tests.test_tutor_output_regressions import _state

_Q = "solve a knapsack problem visually with a numerical"
_COURSE = (
    "Build a table with n + 1 rows for the items and W + 1 columns for the capacities. "
    "The first row and the first column are zero."
)
_STUDENT = ("knapsack can either contain an item or not, no fractions", "matrix",
            "size of matrix is (size of knapsack)x(no of items)")
_LIVE_HINT_1 = (
    "Now that you have the table size set up, think about how you start filling it in. What values do you "
    "place in the very first row and the very first column before you start considering the actual items?"
)
_LIVE_HINT_CODE = (
    "Now that you are reading the matrix dimensions into your array, how do you think you should call the "
    "function responsible for computing the minimum multiplications?"
)


def _guard(text, *, path=PATH_HINT, level=1, reply=None, students=_STUDENT):
    return apply_guards(
        text,
        TutorTurn(path=path, hint_level=level, question=_Q, course_text=_COURSE,
                  student_reply=reply, student_messages=students),
    )


def test_the_live_hint_loses_its_claim_and_keeps_its_question():
    out, tripped = _guard(_LIVE_HINT_1)
    assert out == (
        "What values do you place in the very first row and the very first column before you start "
        "considering the actual items?"
    )
    assert RULE_PROGRESS_CLAIM in tripped


def test_a_claim_leading_a_question_is_cut_and_the_question_stays():
    out, tripped = _guard(_LIVE_HINT_CODE, students=("teach me how to code in c++ for the matrix multiplication code",))
    assert out == "How do you think you should call the function responsible for computing the minimum multiplications?"
    assert RULE_PROGRESS_CLAIM in tripped


def test_a_claim_the_student_actually_made_is_kept():
    text = "Now that you have set up the table with rows for items, what goes in the first row?"
    out, tripped = _guard(text, students=("i set up a table with rows for items and columns for capacity",))
    assert (out, tripped) == (text, [])


def test_a_claim_in_their_reply_is_kept_on_the_reply_path():
    text = "Since you have chosen the last element as the pivot, where does it end up?"
    out, tripped = _guard(text, path=PATH_REPLY, level=0, reply="i chose the last element as the pivot",
                          students=())
    assert (out, tripped) == (text, [])


@pytest.mark.parametrize("path", [PATH_NEW_QUESTION, PATH_HINT, PATH_REPLY])
def test_an_unsupported_claim_in_a_statement_drops_the_statement(path):
    text = "You have now built the whole table. What is in the last cell?"
    out, tripped = _guard(text, path=path, level=0, reply="no idea" if path == PATH_REPLY else None, students=())
    assert out == "What is in the last cell?"
    assert RULE_PROGRESS_CLAIM in tripped


@pytest.mark.parametrize("text", [
    "Think about how you start filling it in. What values go in the first row?",
    "What do you think happens when you have the table ready?",  # a question about a future state
    "You have two choices for each item. Which one keeps the total weight within W?",  # not a progress claim
])
def test_ordinary_hints_are_left_alone(text):
    assert _guard(text) == (text, [])


def test_the_node_passes_the_students_messages_from_the_history(caplog):
    history = [
        {"role": "student", "content": _Q, "hint_level": 0, "is_refusal": False},
        {"role": "tutor", "content": "What structure do you create first?", "hint_level": 0, "is_refusal": False},
        {"role": "student", "content": _STUDENT[2], "hint_level": 0, "is_refusal": False},
        {"role": "tutor", "content": "That is not quite right. What should the size be?", "hint_level": 0,
         "is_refusal": False},
    ]
    state = {**_state(hint_level=0, question=_Q, course_chunks=[_COURSE]), "history": history}
    gemini = MagicMock()
    gemini.generate.return_value = _LIVE_HINT_1
    with caplog.at_level(logging.INFO, logger="agents.tutor"):
        out = advance_hint_node(state, RetrievalService.__new__(RetrievalService), gemini)
    assert out["response"].startswith("What values do you place in the very first row")
    messages = [r.getMessage() for r in caplog.records if r.name.startswith("agents.tutor")]
    assert "[TUTOR-GUARD] rule=progress_claim path=hint hint_level=1" in messages
    assert all("table size" not in m for m in messages)  # the claim itself is never logged
