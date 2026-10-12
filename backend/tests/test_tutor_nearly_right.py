"""
tests/test_tutor_nearly_right.py
--------------------------------
A nearly-right answer keeps the part that is right, in the student's words.

Seen live (session 5c6672fa): "size of matrix is (size of knapsack)x(no of
items)" (one short of (W+1)x(n+1)) got a flat "That is not quite right." The
verdict rule cuts every reason clause below Hint 3 because a reason usually
states the answer; a reason made of the student's own words does not.

Fake model outputs only. No model, no network.
"""

import pytest

from agents.tutor.output_guard import (
    NOT_QUITE_VERDICT,
    PATH_REPLY,
    RULE_VERDICT_REASON,
    TutorTurn,
    apply_guards,
)
from agents.tutor.prompts import reply_instruction

_Q = "solve a knapsack problem visually with a numerical"
_COURSE = (
    "Build a table with n + 1 rows for the items and W + 1 columns for the capacities. "
    "The first row and the first column are zero."
)
_ATTEMPT = "size of matrix is (size of knapsack)x(no of items)"


def _guard(text, reply=_ATTEMPT):
    return apply_guards(
        text,
        TutorTurn(path=PATH_REPLY, hint_level=0, question=_Q, course_text=_COURSE, student_reply=reply),
    )


def test_a_reason_in_their_own_words_is_kept():
    text = (
        "Close: the size does depend on the knapsack size and the number of items. "
        "What is still missing from each dimension?"
    )
    assert _guard(text) == (text, [])


def test_a_reason_that_states_the_answer_is_still_cut():
    out, tripped = _guard(
        "That is not quite right, because the table has n + 1 rows and W + 1 columns. "
        "What is still missing from each dimension?"
    )
    assert out == "That is not quite right. What is still missing from each dimension?"
    assert RULE_VERDICT_REASON in tripped


def test_partly_right_that_negates_their_words_is_still_not_quite():
    out, tripped = _guard(
        "You are partly right, but the size is not (size of knapsack)x(no of items). What should it be?"
    )
    assert out == NOT_QUITE_VERDICT + " What should it be?"
    assert RULE_VERDICT_REASON in tripped


@pytest.mark.parametrize("non_answer", [False, True])
def test_the_reply_instruction_asks_for_their_words(non_answer):
    text = " ".join(reply_instruction(non_answer, 0).split())
    assert "say which part is right in their own words, then ask about what is still missing without stating it" in text
