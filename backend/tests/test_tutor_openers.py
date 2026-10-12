"""
tests/test_tutor_openers.py
---------------------------
Requests that open with "teach me", "help me", "walk me through", "i want" or
"i need" are new questions, and bare digits are not topical words.

Seen live (session 5c6672fa): "teach me how to code in c++ for the matrix
multiplication code" and "i want diagram of the matrix for matrix
multiplication" were replies to whatever question was open, because neither
opener was known; "0/1 knapsack" counted three topical words, two of them
digits, so it was not low-content.

No model, no network.
"""

import pytest

from agents.tutor.query_resolution import (
    TURN_NEW_QUESTION,
    TURN_REPLY,
    classify_turn,
    is_low_content,
    topical_words,
)


@pytest.mark.parametrize("text", [
    "teach me how to code in c++ for the matrix multiplication code",
    "i want diagram of the matrix for matrix multiplication",
    "I need help with the knapsack table",
    "help me with the partition step",
    "walk me through the merge step",
    "i'd like an example of quick sort",
    "please teach me recursion",
])
def test_requests_are_new_questions(text):
    assert classify_turn(text) == TURN_NEW_QUESTION


@pytest.mark.parametrize("text", ["no", "the index starts at 0", "i think it is the largest element"])
def test_replies_stay_replies(text):
    assert classify_turn(text) == TURN_REPLY


@pytest.mark.parametrize("text", ["i want to know", "i want it", "help", "i need that"])
def test_a_request_with_no_topic_of_its_own_is_not_a_new_question(text):
    assert classify_turn(text) != TURN_NEW_QUESTION


def test_digits_are_not_topical_words():
    assert topical_words("0/1 knapsack") == ["knapsack"]
    assert "17" not in topical_words("17 elements remain")
    assert is_low_content("0/1 knapsack")
    assert not is_low_content("the 0/1 knapsack table fills row by row")
