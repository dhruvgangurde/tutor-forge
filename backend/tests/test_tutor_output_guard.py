"""
tests/test_tutor_output_guard.py
---------------------------------
Unit tests for agents/tutor/output_guard.py: one test per rule per path, the
cases where a rule must NOT trip, the fixed fallback, and what gets logged.

Fake model outputs only. No model, no network.
"""

import logging

import pytest

from agents.tutor.output_guard import (
    PATH_HINT,
    PATH_NEW_QUESTION,
    PATH_REPLY,
    RULE_CLAIM_QUESTION,
    RULE_EXAMPLE_REFERENCE,
    RULE_FALLBACK,
    RULE_FRAGMENT,
    RULE_INVENTED_REFERENCE,
    RULE_NON_ANSWER_STATEMENT,
    RULE_ONE_QUESTION,
    RULE_SOURCE_REFERENCE,
    RULE_STATES_ANSWER,
    RULE_UNUSED_NUMBER,
    RULE_VERDICT_AFTER_NON_ANSWER,
    TutorTurn,
    apply_guards,
    guard_tutor_reply,
)
from agents.tutor.prompts import GUARD_FALLBACK_NON_ANSWER, GUARD_FALLBACK_QUESTION

_PD = "What is potential difference?"
_COURSE = (
    "Potential difference is the work done per unit charge in moving a charge "
    "between two points. It is measured in volts.\n\n"
    "Example 7: A charge of 2 C gains 35.3 J of energy between two points, so the "
    "potential difference is 17.65 V. The circuit is shown in figure 2.14 on page 41."
)


def _turn(path=PATH_NEW_QUESTION, level=0, reply=None, non_answer=False):
    return TutorTurn(
        path=path,
        hint_level=level,
        question=_PD,
        course_text=_COURSE,
        student_reply=reply,
        is_non_answer=non_answer,
    )


def _guard(text, **kwargs):
    return apply_guards(text, _turn(**kwargs))


# ── 1. Invented student statements ────────────────────────────────────────────


@pytest.mark.parametrize("path, level", [(PATH_NEW_QUESTION, 0), (PATH_HINT, 1), (PATH_HINT, 3)])
def test_back_reference_with_no_reply_is_removed(path, level):
    out, tripped = _guard(
        "You mentioned that charge matters here. What do you think moves a charge?",
        path=path,
        level=level,
    )
    assert out == "What do you think moves a charge?"
    assert RULE_INVENTED_REFERENCE in tripped


def test_back_reference_after_a_non_answer_is_removed():
    out, tripped = _guard(
        "As you said, energy is involved. What does it take to push something uphill?",
        path=PATH_REPLY,
        reply="I don't know",
        non_answer=True,
    )
    assert out == "What does it take to push something uphill?"
    assert RULE_INVENTED_REFERENCE in tripped


def test_back_reference_to_something_the_reply_did_not_say_is_removed():
    out, tripped = _guard(
        "You mentioned that charges repel each other. What happens when you push two together?",
        path=PATH_REPLY,
        reply="it is about batteries",
    )
    assert out == "What happens when you push two together?"
    assert RULE_INVENTED_REFERENCE in tripped


def test_reference_to_an_earlier_answer_is_removed_even_with_a_reply():
    out, tripped = _guard(
        "Your earlier answer was about energy. How does energy relate to charge?",
        path=PATH_REPLY,
        reply="energy",
    )
    assert out == "How does energy relate to charge?"
    assert RULE_INVENTED_REFERENCE in tripped


def test_example_attributed_to_the_student_is_removed():
    out, tripped = _guard(
        "The example you provided uses a battery. What does the battery do to a charge?",
        path=PATH_NEW_QUESTION,
    )
    assert out == "What does the battery do to a charge?"
    assert RULE_INVENTED_REFERENCE in tripped


def test_back_reference_that_matches_their_reply_is_kept():
    text = (
        "You mentioned that they have the same charge. "
        "What happens when like charges are pushed together?"
    )
    out, tripped = _guard(text, path=PATH_REPLY, reply="because they have the same charge?")
    assert out == text
    assert tripped == []


@pytest.mark.parametrize("path", [PATH_NEW_QUESTION, PATH_HINT])
def test_you_asked_is_not_an_invented_statement(path):
    text = "You asked about potential difference. What do you think a difference in potential could mean?"
    out, tripped = _guard(text, path=path, level=0 if path == PATH_NEW_QUESTION else 1)
    assert out == text
    assert tripped == []


def test_your_answer_is_fine_when_there_is_a_reply():
    text = "Your answer names the right idea. What would make one point different from another?"
    out, tripped = _guard(text, path=PATH_REPLY, reply="something about energy")
    assert out == text
    assert tripped == []


# ── 2. Stating the answer (hint levels 0-2) ───────────────────────────────────


@pytest.mark.parametrize(
    "path, level",
    [(PATH_NEW_QUESTION, 0), (PATH_HINT, 1), (PATH_HINT, 2)],
)
def test_definition_is_removed_below_hint_3(path, level):
    out, tripped = _guard(
        "Potential difference is the work done per unit charge. "
        "What has to happen to move a charge?",
        path=path,
        level=level,
    )
    assert out == "What has to happen to move a charge?"
    assert RULE_STATES_ANSWER in tripped


def test_definition_is_removed_from_a_reply_below_hint_3():
    out, tripped = _guard(
        "Not exactly: potential difference is measured in volts and compares two points. "
        "What do you think is being compared?",
        path=PATH_REPLY,
        level=1,
        reply="is it about batteries?",
    )
    assert out == "What do you think is being compared?"
    assert RULE_STATES_ANSWER in tripped


def test_definition_as_a_lead_in_to_a_question_is_cut():
    out, tripped = _guard(
        "Since potential difference is the work done per unit charge, "
        "what would doubling the charge do?"
    )
    assert out == "What would doubling the charge do?"
    assert RULE_STATES_ANSWER in tripped


def test_course_wording_without_a_defining_verb_is_still_caught():
    out, tripped = _guard(
        "Moving a charge between two points takes work done per unit charge, which gives "
        "the potential difference. What do you think that work depends on?"
    )
    assert out == "What do you think that work depends on?"
    assert RULE_STATES_ANSWER in tripped


def test_correct_restatement_of_their_own_words_is_kept():
    text = (
        "Yes, potential difference is the work done per unit charge. "
        "How would that help you compare two points in a circuit?"
    )
    out, tripped = _guard(text, path=PATH_REPLY, level=1, reply="it is the work done per unit charge")
    assert out == text
    assert tripped == []


@pytest.mark.parametrize(
    "path, level, reply",
    [
        (PATH_HINT, 3, None),
        (PATH_HINT, 4, None),
        (PATH_REPLY, 3, "is it the energy?"),
        (PATH_REPLY, 4, "ok"),
    ],
)
def test_hint_3_full_explanation_and_replies_after_them_keep_answers_and_numbers(path, level, reply):
    text = (
        "Potential difference is the work done per unit charge, so 35.3 J on 2 C gives 17.65 V. "
        "Can you see why dividing the energy by the charge works?"
    )
    out, tripped = _guard(text, path=path, level=level, reply=reply)
    assert out == text
    assert tripped == []


# ── 3. A verdict after a non-answer ───────────────────────────────────────────


@pytest.mark.parametrize("reply", ["no", "I don't know", "not sure"])
def test_verdict_after_a_non_answer_is_removed(reply):
    out, tripped = _guard(
        "Good try! What does it take to push something uphill?",
        path=PATH_REPLY,
        reply=reply,
        non_answer=True,
    )
    assert out == "What does it take to push something uphill?"
    assert RULE_VERDICT_AFTER_NON_ANSWER in tripped


def test_verdict_opening_a_question_is_cut_and_the_question_kept():
    out, tripped = _guard(
        "Not quite, but what does it take to lift a box?",
        path=PATH_REPLY,
        reply="no",
        non_answer=True,
    )
    assert out == "What does it take to lift a box?"
    assert RULE_VERDICT_AFTER_NON_ANSWER in tripped


def test_only_verdicts_left_falls_back_to_the_non_answer_question():
    out, tripped = _guard(
        "You're close. That's a good start.", path=PATH_REPLY, reply="idk", non_answer=True
    )
    assert out == GUARD_FALLBACK_NON_ANSWER
    assert tripped[-1] == RULE_FALLBACK


def test_praise_for_a_real_attempt_is_kept():
    text = "Good thinking. What happens when like charges get closer together?"
    out, tripped = _guard(text, path=PATH_REPLY, reply="they repel each other")
    assert out == text
    assert tripped == []


def test_kind_acknowledgement_of_a_non_answer_is_kept():
    text = "That's fine. What do you already know about charges?"
    out, tripped = _guard(text, path=PATH_REPLY, reply="no", non_answer=True)
    assert out == text
    assert tripped == []


# ── 4. Worked-example leakage ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, level, reply",
    [(PATH_NEW_QUESTION, 0, None), (PATH_HINT, 3, None), (PATH_REPLY, 1, "is it about energy?")],
)
def test_example_figure_and_page_references_are_removed(path, level, reply):
    out, tripped = _guard(
        "Look at figure 2.14 on page 41 to see the circuit. What is being compared?",
        path=path,
        level=level,
        reply=reply,
    )
    assert out == "What is being compared?"
    assert RULE_EXAMPLE_REFERENCE in tripped


def test_full_explanation_loses_only_the_reference_qualifier():
    out, tripped = _guard(
        "As shown in Example 7, a charge of 2 C that gains 35.3 J sits across 17.65 V.",
        path=PATH_HINT,
        level=4,
    )
    assert out == "A charge of 2 C that gains 35.3 J sits across 17.65 V."
    assert tripped == [RULE_EXAMPLE_REFERENCE]


@pytest.mark.parametrize(
    "path, level, reply",
    [(PATH_NEW_QUESTION, 0, None), (PATH_HINT, 2, None), (PATH_REPLY, 1, "energy?")],
)
def test_course_numbers_they_did_not_use_are_removed_below_hint_3(path, level, reply):
    out, tripped = _guard(
        "Imagine a charge gaining 35.3 J. What would you need to know to compare two points?",
        path=path,
        level=level,
        reply=reply,
    )
    assert out == "What would you need to know to compare two points?"
    assert RULE_UNUSED_NUMBER in tripped


def test_numbers_they_used_are_kept():
    text = "Let's see where 17.65 V could come from. What two quantities would you divide?"
    out, tripped = _guard(text, path=PATH_REPLY, level=1, reply="is it 17.65 V?")
    assert out == text
    assert tripped == []


def test_numbers_not_from_the_course_are_kept():
    text = "Picture moving 1000 tiny charges one by one. What changes as they move?"
    out, tripped = _guard(text)
    assert out == text
    assert tripped == []


# ── Never empty; normal replies untouched ─────────────────────────────────────


def test_removing_everything_falls_back_to_a_safe_question():
    out, tripped = _guard("Potential difference is the work done per unit charge.")
    assert out == GUARD_FALLBACK_QUESTION
    assert tripped == [RULE_STATES_ANSWER, RULE_FALLBACK]


def test_empty_model_output_falls_back():
    out, tripped = _guard("   ")
    assert out == GUARD_FALLBACK_QUESTION
    assert tripped == [RULE_FALLBACK]


def test_low_rung_left_with_no_question_falls_back():
    out, _ = _guard(
        "Potential difference is the work done per unit charge. Think about batteries.",
        path=PATH_HINT,
        level=1,
    )
    assert out == GUARD_FALLBACK_QUESTION


@pytest.mark.parametrize(
    "path, level, reply",
    [
        (PATH_NEW_QUESTION, 0, None),
        (PATH_HINT, 2, None),
        (PATH_REPLY, 0, "because they push on each other?"),
    ],
)
def test_a_normal_reply_is_left_unchanged(path, level, reply):
    text = (
        "Think about what it takes to push two like charges closer together.\n\n"
        "- Picture holding them there.\n"
        "- Then picture letting go.\n\n"
        "What do you think happens when you let go?"
    )
    out, tripped = _guard(text, path=path, level=level, reply=reply)
    assert out == text
    assert tripped == []


def test_a_reply_without_a_question_is_not_replaced_when_nothing_tripped():
    text = "This course doesn't cover that part."
    assert _guard(text) == (text, [])


def test_list_layout_survives_a_removal():
    out, _ = _guard(
        "- Potential difference is the work done per unit charge.\n- What moves the charge?"
    )
    assert out == "- What moves the charge?"


def test_abbreviations_and_decimals_do_not_split_sentences():
    out, tripped = _guard("Compare Fig. 3 with what you expect. What do you notice?", level=4)
    assert out == "What do you notice?"
    assert tripped == [RULE_EXAMPLE_REFERENCE]


# ── Logging ───────────────────────────────────────────────────────────────────


def test_guard_logs_only_rule_path_and_level(caplog):
    student_reply = "my secret reply about batteries"
    with caplog.at_level(logging.INFO, logger="agents.tutor.output_guard"):
        guard_tutor_reply(
            "Potential difference is the work done per unit charge. What moves a charge?",
            TutorTurn(
                path=PATH_REPLY,
                hint_level=1,
                question=_PD,
                course_text=_COURSE,
                student_reply=student_reply,
            ),
        )
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["[TUTOR-GUARD] rule=states_answer path=reply hint_level=1"]
    joined = " ".join(messages)
    for leaked in ("work done", "moves a charge", "batteries", "potential difference", "17.65"):
        assert leaked not in joined.lower()


# ── Prompts carry the same rules ──────────────────────────────────────────────


class TestGuardPromptRules:
    def test_ask_do_not_tell_below_hint_3_only(self):
        from agents.tutor.prompts import HINT_LADDER, reply_instruction

        for level in (0, 1, 2):
            assert "Ask, do not tell" in HINT_LADDER[level], level
            assert "Ask, do not tell" in reply_instruction(False, level), level
            assert "Ask, do not tell" in reply_instruction(True, level), level
        for level in (3, 4):
            assert "Ask, do not tell" not in HINT_LADDER[level], level
            assert "Ask, do not tell" not in reply_instruction(False, level), level

    def test_every_instruction_forbids_invented_statements_and_example_numbers(self):
        from agents.tutor.prompts import HINT_LADDER, reply_instruction

        for text in [*HINT_LADDER.values(), reply_instruction(False), reply_instruction(True, 4)]:
            assert "Refer only to what they actually wrote" in text
            assert '"you mentioned"' in text
            assert "never mention an example" in text

    def test_non_answer_step_forbids_a_verdict(self):
        from agents.tutor.prompts import reply_instruction

        assert "Give no verdict" in reply_instruction(True)
        assert "Give no verdict" not in reply_instruction(False)


# ── 6. One question only ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, level, reply",
    [
        (PATH_NEW_QUESTION, 0, None),
        (PATH_HINT, 3, None),
        (PATH_HINT, 4, None),
        (PATH_REPLY, 1, "something about energy"),
    ],
)
def test_only_the_first_question_is_kept(path, level, reply):
    out, tripped = _guard(
        "Think about pushing two like charges together. What does that take? "
        "Where does the effort go? Why might that matter?",
        path=path,
        level=level,
        reply=reply,
    )
    assert out == "Think about pushing two like charges together. What does that take?"
    assert tripped == [RULE_ONE_QUESTION]


def test_statements_after_the_first_question_are_kept():
    out, _ = _guard("What does that take? Picture holding them close. Where does the effort go?")
    assert out == "What does that take? Picture holding them close."


def test_questions_in_a_list_are_trimmed_to_one():
    out, _ = _guard("Try these:\n\n- What does that take?\n- Where does the effort go?")
    assert out == "Try these:\n\n- What does that take?"


def test_a_question_removed_by_an_earlier_rule_does_not_count():
    out, tripped = _guard(
        "Since potential difference is the work done per unit charge, what is it per coulomb? "
        "What has to happen to move a charge? Where does the energy go?",
    )
    assert out == "What is it per coulomb?"
    assert tripped == [RULE_STATES_ANSWER, RULE_ONE_QUESTION]


def test_one_question_never_empties_a_reply():
    for text in ("What is work?", "Why? How? When?", "Work is force times distance."):
        out, _ = _guard(text, level=4)
        assert out.strip()


def test_every_instruction_asks_for_one_question():
    from agents.tutor.prompts import HINT_LADDER, reply_instruction

    for text in [*HINT_LADDER.values(), reply_instruction(False), reply_instruction(True, 4)]:
        assert "Ask at most one question" in text


# ── Repairing joins left by removals ──────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "Think about moving a charge., it takes effort to do that. What does it take?",
            "Think about moving a charge. It takes effort to do that. What does it take?",
        ),
        ("Like charges repel,. What happens next?", "Like charges repel. What happens next?"),
        ("Like charges repel, , so what happens next?", "Like charges repel, so what happens next?"),
        (", like charges repel each other. What happens next?", "Like charges repel each other. What happens next?"),
        ("Think about it ; what happens next?", "Think about it; what happens next?"),
    ],
)
def test_broken_joins_become_well_formed_sentences(raw, expected):
    out, tripped = _guard(raw, level=3)
    assert out == expected
    assert tripped == []


@pytest.mark.parametrize(
    "raw",
    [
        "Think about moving a charge., which is why it matters. What does it take?",
        "Think about moving a charge., and so on. What does it take?",
        "Think about moving a charge., then. What does it take?",
    ],
)
def test_fragments_that_cannot_be_repaired_are_dropped(raw):
    out, tripped = _guard(raw, level=3)
    assert out == "Think about moving a charge. What does it take?"
    assert tripped == [RULE_FRAGMENT]


@pytest.mark.parametrize(
    "text",
    [
        "Some quantities, e.g., voltage, compare two points. What else compares two points?",
        "Lists, tables, etc., can help. What would you put in a table?",
        "Note: like charges repel. What happens next?",
    ],
)
def test_ordinary_punctuation_is_not_treated_as_debris(text):
    assert _guard(text, level=3) == (text, [])


def test_a_variable_opening_an_orphan_keeps_its_case():
    out, _ = _guard("Look at the equation., x is the unknown here. What is x?", level=3)
    assert out == "Look at the equation. x is the unknown here. What is x?"


# ── After a non-answer below Hint 3: ask, state nothing about the topic ───────

_PARAPHRASE = (
    "Potential difference is related to the work required to move charges against the "
    "force of attraction or repulsion between them."
)


@pytest.mark.parametrize("level", [0, 1, 2])
def test_topic_statement_after_a_non_answer_is_removed(level):
    out, tripped = _guard(
        _PARAPHRASE + " What do you think makes it hard to push two charges together?",
        path=PATH_REPLY,
        level=level,
        reply="no",
        non_answer=True,
    )
    assert out == "What do you think makes it hard to push two charges together?"
    assert RULE_NON_ANSWER_STATEMENT in tripped


def test_course_wording_without_the_topic_is_removed_after_a_non_answer():
    out, tripped = _guard(
        "The work done per unit charge in moving a charge matters here. What does it take to lift a box?",
        path=PATH_REPLY,
        reply="I don't know",
        non_answer=True,
    )
    assert out == "What does it take to lift a box?"
    assert tripped == [RULE_NON_ANSWER_STATEMENT]


def test_topic_lead_in_to_a_question_is_cut_after_a_non_answer():
    out, tripped = _guard(
        "Since potential difference is related to work, what does it take to lift a box?",
        path=PATH_REPLY,
        reply="no",
        non_answer=True,
    )
    assert out == "What does it take to lift a box?"
    assert RULE_NON_ANSWER_STATEMENT in tripped


def test_only_topic_statements_after_a_non_answer_fall_back():
    out, tripped = _guard(_PARAPHRASE, path=PATH_REPLY, reply="no", non_answer=True)
    assert out == GUARD_FALLBACK_NON_ANSWER
    assert tripped == [RULE_NON_ANSWER_STATEMENT, RULE_FALLBACK]


def test_scaffolding_without_the_topic_is_kept_after_a_non_answer():
    text = "That's fine. Think about pushing a heavy box up a hill. What makes it hard?"
    assert _guard(text, path=PATH_REPLY, reply="no", non_answer=True) == (text, [])


def test_same_statement_after_a_real_attempt_keeps_the_current_rules():
    text = _PARAPHRASE + " What do you think that work is done against?"
    assert _guard(text, path=PATH_REPLY, reply="is it about energy?") == (text, [])


@pytest.mark.parametrize("level", [3, 4])
def test_hint_3_and_full_explanation_keep_topic_statements_after_a_non_answer(level):
    text = _PARAPHRASE + " Can you see why that takes work?"
    assert _guard(text, path=PATH_REPLY, level=level, reply="no", non_answer=True) == (text, [])


# ── Narrating sources they have never seen ────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # A: qualifiers removed, the rest of the sentence kept.
        ("According to the passage, like charges repel. Why?", "Like charges repel. Why?"),
        ("Based on the text, like charges repel. Why?", "Like charges repel. Why?"),
        ("As per the section, like charges repel. Why?", "Like charges repel. Why?"),
        ("Like charges repel, from the reading. Why?", "Like charges repel. Why?"),
        ("Like charges repel, as described in the material. Why?", "Like charges repel. Why?"),
        ("Like charges repel, as explained in this excerpt. Why?", "Like charges repel. Why?"),
        ("Like charges repel in the passage. Why?", "Like charges repel. Why?"),
        (
            "How would you explain it, based on the description of moving charges between points?",
            "How would you explain it?",
        ),
        (
            "Based on the description of moving charges, what do you think is needed?",
            "What do you think is needed?",
        ),
        ("What does work mean in this context?", "What does work mean?"),
        ("Work matters in this context.", "Work matters."),
        # B: the source as the subject; the claim is kept.
        ("The passage says that like charges repel. Why?", "Like charges repel. Why?"),
        ("Charges move. The text states that work is needed. Why?", "Charges move. Work is needed. Why?"),
        ("This reading suggests like charges repel. Why?", "Like charges repel. Why?"),
    ],
)
def test_source_narration_is_rewritten(raw, expected):
    from core.context_phrasing import strip_source_narration

    assert strip_source_narration(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "What does the text of the poem suggest about the speaker?",
        "Have you finished the reading for this week?",
        "Which section of a circuit carries the most current?",
        "In this context, what does work mean?",
        "What does charge mean in this context of circuits?",
        "This section explains how charges move.",
        "Look at the textbook.",
    ],
)
def test_ordinary_uses_are_not_narration(text):
    from core.context_phrasing import strip_source_narration

    assert strip_source_narration(text) == text


@pytest.mark.parametrize(
    "path, level, reply, non_answer",
    [
        (PATH_NEW_QUESTION, 0, None, False),
        (PATH_HINT, 4, None, False),
        (PATH_REPLY, 1, "is it energy?", False),
        (PATH_REPLY, 0, "no", True),
    ],
)
@pytest.mark.parametrize(
    "sentence",
    [
        "Think about what the passage describes.",
        "Look again at this passage.",
        "Recall the excerpt on charges.",
        "Think about the description of moving charges.",
        "Remember the definition given earlier.",
        "Use the explanation given before.",
        "Look at the provided text again.",
    ],
)
def test_sentence_pointing_at_an_unseen_source_is_dropped(path, level, reply, non_answer, sentence):
    out, tripped = _guard(
        sentence + " What makes it hard to push two charges together?",
        path=path,
        level=level,
        reply=reply,
        non_answer=non_answer,
    )
    assert out == "What makes it hard to push two charges together?"
    assert RULE_SOURCE_REFERENCE in tripped


@pytest.mark.parametrize(
    "text",
    [
        "Think about the text of the poem. What does the speaker want?",
        "Your reading for this week covers charges. What did you notice in the reading?",
        "Think about the material a wire is made of. Why might that matter?",
        "Look at the next section of the circuit. What do you notice?",
    ],
)
def test_text_reading_section_and_material_are_not_dropped_on_sight(text):
    assert _guard(text, level=3) == (text, [])


def test_every_instruction_forbids_pointing_at_sources():
    from agents.tutor.prompts import HINT_LADDER, reply_instruction

    for text in [*HINT_LADDER.values(), reply_instruction(False), reply_instruction(True, 4)]:
        for phrase in ('"the passage"', '"the text says"', '"the description of"', '"the definition given in"'):
            assert phrase in text
        assert 'do not end a question with "in this context"' in text


# ── "the description" without "of" ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("What do you think it means based on the description?", "What do you think it means?"),
        ("Based on the description, what do you think is needed?", "What do you think is needed?"),
        ("What would you change, according to the description given?", "What would you change?"),
        ("What is moving in the description above?", "What is moving?"),
        ("Charges move from the description. Why?", "Charges move. Why?"),
        ("What is compared, using the description provided?", "What is compared?"),
    ],
)
def test_bare_description_qualifier_is_removed(raw, expected):
    from core.context_phrasing import strip_source_narration

    assert strip_source_narration(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Can you write a description of a series circuit?",
        "What would a good description of a series circuit include?",
        "Your description is close. What would you add to your description?",
        "Think about the description and the diagram together. What do you notice?",
        "Give a short description in your own words. What did you include?",
    ],
)
def test_ordinary_description_is_left_alone(text):
    from core.context_phrasing import strip_source_narration

    assert strip_source_narration(text) == text
    assert _guard(text, level=3) == (text, [])


# ── The course and its lectures, by name ──────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Inside, opening and closing a question: removed, the question kept.
        (
            "Do you remember the key idea from the course material about resistors in series?",
            "Do you remember the key idea about resistors in series?",
        ),
        ("From the lecture notes, what do you remember about resistors?", "What do you remember about resistors?"),
        ("What do you remember about resistors, from our course material?", "What do you remember about resistors?"),
        ("What do you remember, according to the lecture?", "What do you remember?"),
        ("Based on our course, what moves a charge?", "What moves a charge?"),
        ("As per the course content, charges move. Why?", "Charges move. Why?"),
        ("Charges move, as described in the lecture slides. Why?", "Charges move. Why?"),
        ("Charges move in the course notes. Why?", "Charges move. Why?"),
        # As the subject: the claim is kept.
        ("Our course explains that like charges repel. Why?", "Like charges repel. Why?"),
        ("The lecture slides say like charges repel. Why?", "Like charges repel. Why?"),
        ("The passages show that work is needed. Why?", "Work is needed. Why?"),
        ("As the lecture notes explain, charges move. Why?", "Charges move. Why?"),
    ],
)
def test_course_and_lecture_narration_is_removed(raw, expected):
    from core.context_phrasing import strip_source_narration

    assert strip_source_narration(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Of course, charges move. Why?",
        "The river changes course from season to season. Why?",
        "That isn't covered in the course.",
        "This course doesn't cover that part.",
        "Later in the course, you'll meet capacitors. What do you expect?",
        "The course explains how charges move. Why?",
        "Look at your course notes. What do you see?",
    ],
)
def test_ordinary_mentions_of_the_course_are_left_alone(text):
    from core.context_phrasing import strip_source_narration

    assert strip_source_narration(text) == text


def test_every_instruction_forbids_naming_the_course_material():
    from agents.tutor.prompts import HINT_LADDER, reply_instruction

    for text in [*HINT_LADDER.values(), reply_instruction(False), reply_instruction(True, 4)]:
        for phrase in ('"from the course material"', '"in our course"', '"the lecture notes say"'):
            assert phrase in text


# ── A yes/no question that carries the answer ─────────────────────────────────

#: The course text the item 3 prototype table was measured against.
_CLAIM_COURSE = (
    "Potential difference is the work done per unit charge in moving a charge between two "
    "points. It is measured in volts. Work is required to move a charge against the force of "
    "attraction or repulsion between charges."
)


def _claim_guard(text, *, first_turn=False, path=None, level=0, reply=None, non_answer=None):
    if first_turn:
        path, reply, non_answer = PATH_NEW_QUESTION, None, False
    elif path is None:
        path, reply, non_answer = PATH_REPLY, "no", True
    return apply_guards(
        text,
        TutorTurn(
            path=path,
            hint_level=level,
            question=_PD,
            course_text=_CLAIM_COURSE,
            student_reply=reply,
            is_non_answer=bool(non_answer),
        ),
    )


@pytest.mark.parametrize(
    "question",
    [
        "Do you think potential difference is related to the work needed to move charges?",
        "Does potential difference measure the work needed per charge?",
        "Is potential difference the work done per unit charge?",
        "Are potential differences measured between two points?",
        "Can you remember that potential difference is the work done per charge?",
        "Do you know that potential difference is the work done per charge?",
        "Did you recall that potential difference compares two points?",
        "Would you agree that potential difference is measured in volts?",
        "So, do you believe potential difference depends on the work done?",
    ],
)
def test_each_yes_no_form_with_a_claim_is_dropped_after_a_non_answer(question):
    out, tripped = _claim_guard(question + " What does it take to lift a box?")
    assert out == "What does it take to lift a box?"
    assert RULE_CLAIM_QUESTION in tripped


def test_live_claim_question_after_no_falls_back():
    out, tripped = _claim_guard(
        "Do you think potential difference is related to the work or energy required to move "
        "charges between two points?"
    )
    assert out == GUARD_FALLBACK_NON_ANSWER
    assert tripped == [RULE_CLAIM_QUESTION, RULE_FALLBACK]


#: The prototype table from the item 3 proposal: (question, trips after "no", trips on a first turn).
_CLAIM_TABLE = [
    (
        (
            "Do you think potential difference is related to the work or energy required to "
            "move charges between two points?"
        ),
        True,
        False,
    ),
    ("Is potential difference the work done per unit charge?", True, True),
    ("Does potential difference measure the work needed per charge?", True, True),
    ("Do you think potential difference is the work done per unit charge?", True, True),
    ("Do you think potential difference has something to do with energy?", True, False),
    ("Do you know what potential difference measures?", False, False),
    ("Can you describe potential difference in your own words?", False, False),
    ("Have you ever pushed two magnets together?", False, False),
    ("Do you think it takes effort to push two like charges together?", False, False),
    ("What do you think potential difference measures?", False, False),
]


@pytest.mark.parametrize("question, after_no, _first", _CLAIM_TABLE)
def test_claim_table_after_a_non_answer(question, after_no, _first):
    out, tripped = _claim_guard(question)
    if after_no:
        assert out == GUARD_FALLBACK_NON_ANSWER
        assert RULE_CLAIM_QUESTION in tripped
    else:
        assert out == question
        assert tripped == []


@pytest.mark.parametrize("question, _after_no, first_turn", _CLAIM_TABLE)
def test_claim_table_on_a_first_turn(question, _after_no, first_turn):
    out, tripped = _claim_guard(question, first_turn=True)
    if first_turn:
        assert out == GUARD_FALLBACK_QUESTION
        assert RULE_CLAIM_QUESTION in tripped
    else:
        assert out == question
        assert tripped == []


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_hints_keep_claim_questions(level):
    question = "Do you think potential difference is the work done per unit charge?"
    assert _claim_guard(question, path=PATH_HINT, level=level, non_answer=False) == (question, [])


@pytest.mark.parametrize(
    "path, level, reply, non_answer",
    [
        (PATH_REPLY, 0, "is it energy?", False),   # a real attempt
        (PATH_REPLY, 2, "maybe work?", False),
        (PATH_REPLY, 3, "no", True),               # Hint 3 after "no"
        (PATH_REPLY, 4, "no", True),               # full explanation after "no"
    ],
)
def test_claim_questions_are_kept_outside_the_rule(path, level, reply, non_answer):
    question = "Do you think potential difference is the work done per unit charge?"
    out, tripped = _claim_guard(question, path=path, level=level, reply=reply, non_answer=non_answer)
    assert (out, tripped) == (question, [])


def test_claim_rule_logs_only_rule_path_and_level(caplog):
    with caplog.at_level(logging.INFO, logger="agents.tutor.output_guard"):
        guard_tutor_reply(
            "Is potential difference the work done per unit charge? What does it take to lift a box?",
            TutorTurn(path=PATH_NEW_QUESTION, hint_level=0, question=_PD, course_text=_CLAIM_COURSE),
        )
    assert [r.getMessage() for r in caplog.records] == [
        "[TUTOR-GUARD] rule=claim_question path=new_question hint_level=0"
    ]
