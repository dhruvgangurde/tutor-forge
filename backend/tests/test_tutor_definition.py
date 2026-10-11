# The tutoring fixtures are imported by name from tests/test_tutoring.py and
# requested as test parameters, which ruff reads as redefinitions.
# ruff: noqa: F811
"""
tests/test_tutor_definition.py
------------------------------
A pure "what is X?" question opens with one grounded definition sentence and
then a question, unless it is a question from graded work in the course.

Fake model outputs only. No model, no network.
"""

import json

import pytest
from sqlalchemy import select

from agents.tutor.output_guard import (
    PATH_HINT,
    PATH_NEW_QUESTION,
    PATH_REPLY,
    RULE_DEFINITION_EXTRA,
    RULE_STATES_ANSWER,
    TutorTurn,
    apply_guards,
)
from agents.tutor.prompts import DEFINITION_OPENING, HINT_LADDER, system_instruction_for_level
from agents.tutor.query_resolution import definition_term
from db.models import Assessment, Course, Question, User
from tests.test_tutor_output_regressions import _run_state, _state
from tests.test_tutoring import (  # noqa: F401  (fixtures used by name)
    _chat,
    _clear_tutor_mocks,
    _hint,
    _install_tutor_mocks,
    _make_query_scoring_retrieval,
    _new_session,
    _TestSessionFactory,
    client,
    ready_course_id,
    setup_db,
    student_token,
)
from tutoring.service import _is_graded_question

# ── Which questions count ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "question, term",
    [
        ("What is potential difference?", "potential difference"),
        ("what is the pivot in quick sort?", "pivot in quick sort"),
        ("what’s a pivot", "pivot"),
        ("define recursion", "recursion"),
        ("Can you define merge sort?", "merge sort"),
        ("what does pivot mean?", "pivot"),
        ("what is meant by partition?", "partition"),
        ("what is the definition of a stable sort?", "stable sort"),
    ],
)
def test_pure_what_is_questions_name_their_term(question, term):
    assert definition_term(question) == term


@pytest.mark.parametrize(
    "question",
    [
        "what is the time complexity of merge sort?",     # a fact about it, not what it is
        "what is the difference between quick sort and merge sort?",
        "what is the worst case of quick sort?",
        "what is the role of the pivot?",
        "what are the steps of partition?",
        "what is the value of i when the loop ends?",
        "what is the output of partition([3,1,2])?",
        "what is 2+2?",
        "what is it?",                                     # no term of its own
        "how does partition work?",
        "is the pivot the largest element?",
        "what is the pivot used for in quick sort and how is it chosen each time?",  # too long
    ],
)
def test_other_questions_are_not_definition_questions(question):
    assert definition_term(question) is None


# ── The instruction ───────────────────────────────────────────────────────────


def test_the_definition_opening_asks_for_one_sentence_then_a_question():
    assert system_instruction_for_level(0, definition=True) == DEFINITION_OPENING
    assert "exactly one sentence" in DEFINITION_OPENING
    assert "exactly one question" in DEFINITION_OPENING
    assert "do not state the definition" not in DEFINITION_OPENING
    assert "ONLY on the provided course context" in DEFINITION_OPENING  # grounding unchanged


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_only_the_opening_rung_has_a_definition_form(level):
    assert system_instruction_for_level(level, definition=True) == HINT_LADDER[level]


def test_the_ordinary_opening_still_withholds_the_definition():
    assert system_instruction_for_level(0) == HINT_LADDER[0]
    assert "do not state the definition" in HINT_LADDER[0]


# ── The guard ─────────────────────────────────────────────────────────────────

_PD = "What is potential difference?"
_COURSE = (
    "Potential difference is the work done per unit charge in moving a charge between two points. "
    "It is measured in volts. Work is needed to move a charge against the force of attraction or "
    "repulsion between charges."
)
_DEFINITION = "Potential difference is the work done per unit charge to move a charge between two points."
_FOLLOW_UP = "What do you think has to happen for a charge to move?"


def _guard(text, *, allowed=True, path=PATH_NEW_QUESTION, level=0, reply=None):
    return apply_guards(
        text,
        TutorTurn(
            path=path,
            hint_level=level,
            question=_PD,
            course_text=_COURSE,
            student_reply=reply,
            definition_allowed=allowed,
        ),
    )


def test_one_grounded_definition_sentence_is_kept():
    text = _DEFINITION + " " + _FOLLOW_UP
    assert _guard(text) == (text, [])


def test_without_the_flag_the_definition_is_still_removed():
    out, tripped = _guard(_DEFINITION + " " + _FOLLOW_UP, allowed=False)
    assert out == _FOLLOW_UP
    assert RULE_STATES_ANSWER in tripped


def test_only_one_sentence_is_spared():
    out, tripped = _guard(_DEFINITION + " It is measured in volts. " + _FOLLOW_UP)
    assert out == _DEFINITION + " " + _FOLLOW_UP
    assert RULE_DEFINITION_EXTRA in tripped


def test_a_second_definition_is_not_spared():
    second = "Potential difference is the energy given to each unit of charge between two points."
    out, _ = _guard(_DEFINITION + " " + second + " " + _FOLLOW_UP)
    assert out == _DEFINITION + " " + _FOLLOW_UP


def test_a_definition_not_from_the_course_is_not_spared():
    ungrounded = "Potential difference is the pressure that pushes electrons like water in a pipe."
    out, tripped = _guard(ungrounded + " " + _FOLLOW_UP)
    assert out == _FOLLOW_UP
    assert RULE_STATES_ANSWER in tripped


def test_a_definition_that_comes_later_is_not_spared():
    out, _ = _guard("Let's start with the idea itself. " + _DEFINITION + " " + _FOLLOW_UP)
    assert _DEFINITION not in out


@pytest.mark.parametrize(
    "path, level, reply",
    [
        (PATH_NEW_QUESTION, 1, None),
        (PATH_HINT, 1, None),
        (PATH_HINT, 2, None),
        (PATH_REPLY, 0, "is it about batteries?"),
    ],
)
def test_the_flag_counts_only_on_the_opening_turn(path, level, reply):
    out, tripped = _guard(_DEFINITION + " " + _FOLLOW_UP, path=path, level=level, reply=reply)
    assert _DEFINITION not in out
    assert tripped


# A question that names more than the term: the definition names only "pivot".
_PIVOT_Q = "what is the pivot in quick sort?"
_PIVOT_COURSE = (
    "Quick sort picks the last element of the array as the pivot. "
    "The array is partitioned around the pivot so smaller elements come before it."
)
_PIVOT_DEFINITION = "The pivot is the element the array is partitioned around."
_PIVOT_FOLLOW_UP = "Where do you think the smaller elements end up?"


def _pivot_guard(text, *, allowed, path=PATH_NEW_QUESTION, level=0, reply=None):
    return apply_guards(
        text,
        TutorTurn(
            path=path,
            hint_level=level,
            question=_PIVOT_Q,
            course_text=_PIVOT_COURSE,
            student_reply=reply,
            definition_allowed=allowed,
        ),
    )


@pytest.mark.parametrize(
    "allowed, path, level",
    [
        (False, PATH_NEW_QUESTION, 0),  # graded quiz question: the service did not allow it
        (True, PATH_HINT, 1),            # a hint never gives the definition
        (True, PATH_HINT, 2),
    ],
)
def test_a_definition_is_removed_when_not_allowed_even_if_the_question_names_more(allowed, path, level):
    out, tripped = _pivot_guard(_PIVOT_DEFINITION + " " + _PIVOT_FOLLOW_UP, allowed=allowed, path=path, level=level)
    assert out == _PIVOT_FOLLOW_UP
    assert RULE_STATES_ANSWER in tripped


def test_a_definition_not_from_the_course_is_removed_even_if_the_question_names_more():
    ungrounded = "The pivot is a referee who decides which team each player joins."
    out, tripped = _pivot_guard(ungrounded + " " + _PIVOT_FOLLOW_UP, allowed=True)
    assert out == _PIVOT_FOLLOW_UP
    assert RULE_STATES_ANSWER in tripped


def test_an_allowed_grounded_definition_naming_only_the_term_is_kept():
    text = _PIVOT_DEFINITION + " " + _PIVOT_FOLLOW_UP
    assert _pivot_guard(text, allowed=True) == (text, [])


def test_confirming_their_own_definition_is_not_giving_it_away():
    _out, tripped = _pivot_guard(
        "Yes, the pivot is the element the array is partitioned around. " + _PIVOT_FOLLOW_UP,
        allowed=False, path=PATH_REPLY, level=1, reply="the element the array is partitioned around",
    )
    assert RULE_STATES_ANSWER not in tripped


# ── The node ──────────────────────────────────────────────────────────────────


def _definition_state(**kwargs):
    state = _state(course_chunks=[_COURSE], **kwargs)
    state["definition_allowed"] = True
    return state


def test_node_uses_the_definition_opening_and_keeps_the_sentence():
    from unittest.mock import MagicMock

    from agents.tutor.nodes import generate_guiding_question_node
    from retrieval.service import RetrievalService

    gemini = MagicMock()
    gemini.generate.return_value = _DEFINITION + " " + _FOLLOW_UP
    retrieval = RetrievalService.__new__(RetrievalService)
    out = generate_guiding_question_node(_definition_state(), retrieval, gemini)["response"]

    assert out == _DEFINITION + " " + _FOLLOW_UP
    system = gemini.generate.call_args.kwargs["system_instruction"]
    assert system.startswith(DEFINITION_OPENING)


def test_node_ignores_the_flag_on_a_hint():
    out = _run_state(_DEFINITION + " " + _FOLLOW_UP, _definition_state(hint_level=0), hint=True)
    assert _DEFINITION not in out


def test_node_ignores_the_flag_on_a_reply():
    out = _run_state(
        _DEFINITION + " " + _FOLLOW_UP, _definition_state(reply="is it about batteries?", hint_level=0)
    )
    assert _DEFINITION not in out


# ── Graded work ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "question, stem, graded",
    [
        ("what is a pivot?", "What is a pivot?", True),                          # the same question
        ("what is the pivot in quick sort?", "What is the pivot in quick sort?", True),
        ("what is the pivot in quick sort?", "Which of the following best describes the pivot?", True),
        ("define recursion", "What does recursion mean?", True),
        ("what is a pivot?", "Trace partition on [3, 1, 2] and give the final array.", False),
        ("what is a pivot?", "What is the time complexity of quick sort?", False),
        ("what is recursion?", "Which of the following best describes the pivot?", False),
    ],
)
def test_graded_question_match(question, stem, graded):
    assert _is_graded_question(question, definition_term(question), stem) is graded


_PIVOT_Q = "what is the pivot in quick sort?"
_SCORES = {_PIVOT_Q: 0.70, "how does partition work?": 0.70, "no": 0.35}


async def _add_quiz(course_id, stem, status="published"):
    async with _TestSessionFactory() as db:
        teacher = (await db.execute(select(User).where(User.email == "teacher@demo.com"))).scalar_one()
        course = await db.get(Course, course_id)
        a = Assessment(
            course_id=course.id, created_by=teacher.id, title="Sorting quiz",
            status=status, config=json.dumps({"topic": "sorting"}),
        )
        db.add(a)
        await db.flush()
        db.add(Question(
            assessment_id=a.id, question_type="short_answer", stem=stem,
            answer_key=json.dumps({"model_answer": "x"}), max_points=1.0, order_index=0,
        ))
        await db.commit()


async def _systems(client, token, course_id, messages):
    retrieval = _make_query_scoring_retrieval(_SCORES)
    gemini = _install_tutor_mocks(retrieval)
    try:
        sid = await _new_session(client, token, course_id)
        for message in messages:
            resp = await _chat(client, token, sid, message)
            assert resp.status_code == 200
    finally:
        _clear_tutor_mocks()
    return [c.kwargs["system_instruction"] for c in gemini.generate.call_args_list]


@pytest.mark.asyncio
async def test_api_definition_question_gets_the_definition_opening(client, student_token, ready_course_id):
    systems = await _systems(client, student_token, ready_course_id, [_PIVOT_Q])
    assert systems[0].startswith(DEFINITION_OPENING)


@pytest.mark.asyncio
async def test_api_published_quiz_question_keeps_the_ordinary_opening(client, student_token, ready_course_id):
    await _add_quiz(ready_course_id, "Which of the following best describes the pivot?")
    systems = await _systems(client, student_token, ready_course_id, [_PIVOT_Q])
    assert systems[0].startswith(HINT_LADDER[0][:60])
    assert not systems[0].startswith(DEFINITION_OPENING)


@pytest.mark.asyncio
async def test_api_draft_quiz_question_does_not_count(client, student_token, ready_course_id):
    await _add_quiz(ready_course_id, "Which of the following best describes the pivot?", status="draft")
    systems = await _systems(client, student_token, ready_course_id, [_PIVOT_Q])
    assert systems[0].startswith(DEFINITION_OPENING)


@pytest.mark.asyncio
async def test_api_other_questions_and_replies_keep_the_ladder(client, student_token, ready_course_id):
    systems = await _systems(client, student_token, ready_course_id, ["how does partition work?", "no"])
    assert all(not s.startswith(DEFINITION_OPENING) for s in systems)
