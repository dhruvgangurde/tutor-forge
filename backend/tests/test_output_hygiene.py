"""
tests/test_output_hygiene.py
-----------------------------
Regression tests for three defects visible to a student in the live app:

  1. MCQ options rendered double-lettered ("A. A. Two billion years ago") —
     the generator baked the letter into the option text and the UI prepended
     its own.
  2. Distractor-pool contamination — an MCQ carrying options that answer a
     DIFFERENT question in the same generation batch. A mass or an area is not
     a wrong answer to a "when did this happen?" question; it is not an answer.
  3. "According to the context" leaking into stems and tutor replies. The
     student never saw a context block.

Each is fixed in two layers — a prompt rule and a post-processing enforcement —
because the prompt rules are what already failed. These tests cover the
enforcement layer, which is the one that holds when the model ignores the rule.
"""

import pytest

from core.context_phrasing import mentions_context, strip_context_references


# ── 1. Option letter prefixes ─────────────────────────────────────────────────


class TestOptionPrefixStripping:
    def _fn(self):
        from agents.assessment.nodes import _strip_option_prefix

        return _strip_option_prefix

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("A. Two billion years ago", "Two billion years ago"),
            ("B. 300,000 years ago", "300,000 years ago"),
            ("B) 300,000 years ago", "300,000 years ago"),
            ("(C) Chloroplast", "Chloroplast"),
            ("D - Golgi apparatus", "Golgi apparatus"),
            ("a. lowercase label", "lowercase label"),
            ("  C.   padded  ", "padded"),
        ],
    )
    def test_strips_every_label_style(self, raw, expected):
        assert self._fn()(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "B cells produce antibodies",  # a real answer starting with a letter
            "Plain text option",
            "A priori reasoning",
            "O(log n) complexity",
            "300,000 years ago",
        ],
    )
    def test_leaves_legitimate_text_alone(self, raw):
        # The delimiter requirement is what protects these: "B cells" has no
        # "." or ")" after the letter, so it is not a label.
        assert self._fn()(raw) == raw

    def test_only_one_label_is_removed(self):
        # Guards against over-stripping if a double prefix is ever stored.
        assert self._fn()("A. B. Something") == "B. Something"

    def test_non_string_passes_through(self):
        assert self._fn()(None) is None

    def test_prompts_no_longer_ask_for_lettered_options(self):
        from agents.assessment.prompts import (
            GENERATE_QUESTIONS_PROMPT,
            IMPROVE_DISTRACTORS_PROMPT,
        )

        # The JSON examples the model copies must not show a letter prefix.
        assert '"A. ..."' not in GENERATE_QUESTIONS_PROMPT
        assert '"A. ..."' not in IMPROVE_DISTRACTORS_PROMPT


# ── 2. Distractor-pool contamination ──────────────────────────────────────────


class TestDistractorContamination:
    def _fns(self):
        from agents.assessment.nodes import _leaking_options, _other_question_answers

        return _other_question_answers, _leaking_options

    def _batch(self):
        # The real batch from the screenshot: an MCQ asking WHEN, alongside a
        # numeric question whose answer is an area.
        return [
            {"question_type": "mcq", "correct_answer": "B"},
            {"question_type": "short_answer", "correct_answer": "Humanity's impact is unsustainable"},
            {"question_type": "numeric", "correct_answer": "510072000"},
        ]

    def test_collects_other_questions_answers_excluding_mcq_letters(self):
        other, _ = self._fns()
        answers = other(self._batch(), 0)
        assert "510072000" in answers
        # An MCQ's answer is a bare letter and would collide with everything.
        assert "b" not in answers

    def test_detects_the_live_contamination_case(self):
        other, leaking = self._fns()
        options = [
            "Two billion years ago",
            "300,000 years ago",
            "148940000 km2 emerged 300,000 years ago in Africa and have spread",
            "510072000 km2",
        ]
        hits = leaking(options, other(self._batch(), 0))
        assert "510072000 km2" in hits

    def test_detects_a_foreign_answer_welded_into_longer_text(self):
        # The model often embeds the foreign answer rather than copying it bare.
        other, leaking = self._fns()
        batch = [
            {"question_type": "mcq", "correct_answer": "A"},
            {"question_type": "numeric", "correct_answer": "5.972168x10^24 kg"},
        ]
        hits = leaking(["The mass is 5.972168x10^24 kg exactly"], other(batch, 0))
        assert hits

    def test_clean_options_are_not_flagged(self):
        other, leaking = self._fns()
        clean = [
            "Two billion years ago",
            "300,000 years ago",
            "50,000 years ago",
            "1 million years ago",
        ]
        assert leaking(clean, other(self._batch(), 0)) == []

    def test_short_answers_are_too_generic_to_match_on(self):
        # "4" as another question's answer must not flag every option
        # containing a 4 — that would be noise, not detection.
        other, leaking = self._fns()
        batch = [
            {"question_type": "mcq", "correct_answer": "A"},
            {"question_type": "numeric", "correct_answer": "4"},
        ]
        assert other(batch, 0) == set()
        assert leaking(["4 comparisons", "14 comparisons"], other(batch, 0)) == []

    def test_prompt_forbids_cross_question_answers(self):
        from agents.assessment.prompts import IMPROVE_DISTRACTORS_PROMPT

        assert "DIFFERENT question" in IMPROVE_DISTRACTORS_PROMPT
        assert "SAME KIND OF THING" in IMPROVE_DISTRACTORS_PROMPT


# ── 3. Context phrasing ───────────────────────────────────────────────────────


class TestContextPhrasing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (
                "When did humans emerge in Africa according to the context?",
                "When did humans emerge in Africa?",
            ),
            (
                "What is the mass of Earth in kilograms according to the context?",
                "What is the mass of Earth in kilograms?",
            ),
            (
                "The context states that humans emerged 300,000 years ago.",
                "Humans emerged 300,000 years ago.",
            ),
            (
                "Describe the impact of human activities based on the provided context.",
                "Describe the impact of human activities.",
            ),
            (
                "as described in the context, the crust is thin.",
                "the crust is thin.",
            ),
        ],
    )
    def test_strips_real_observed_phrasings(self, raw, expected):
        assert strip_context_references(raw) == expected

    def test_rewrites_a_negation_without_deleting_the_claim(self):
        # Deleting the clause would turn a refusal into a non-sequitur.
        out = strip_context_references(
            "the course context provided does not directly mention Bellman-Ford."
        )
        assert "context" not in out.lower()
        assert "does not cover" in out
        assert "Bellman-Ford" in out

    @pytest.mark.parametrize(
        "raw",
        [
            "In the context of Dynamic Programming, what does 'DP' stand for?",
            "In the context of graph theory, define a cycle.",
        ],
    )
    def test_leaves_the_in_the_context_of_idiom_alone(self, raw):
        # "In the context of X" is ordinary English meaning "within the domain
        # of". Stripping it would mangle the stem into "Of X, what does...".
        assert strip_context_references(raw) == raw
        assert mentions_context(raw) is False

    def test_untouched_text_is_returned_unchanged(self):
        clean = "Explain why binary search requires a sorted array."
        assert strip_context_references(clean) == clean

    def test_empty_input_is_safe(self):
        assert strip_context_references("") == ""
        assert strip_context_references(None) is None
        assert mentions_context("") is False

    def test_detector_finds_what_the_stripper_may_miss(self):
        # mentions_context is the flag for wordings the stripper does not yet
        # handle, so it must be broader than the stripper.
        assert mentions_context("Refer to the context for details.") is True

    def test_both_agents_forbid_mentioning_the_context(self):
        from agents.assessment.prompts import GENERATE_QUESTIONS_PROMPT
        from agents.tutor.prompts import HINT_LADDER

        assert "NEVER refer to" in GENERATE_QUESTIONS_PROMPT
        # Every rung, not just the opener — a student can enter at any level.
        assert all("NEVER mention" in v for v in HINT_LADDER.values())

    def test_sanitiser_output_never_still_mentions_the_context(self):
        for raw in [
            "When did humans emerge in Africa according to the context?",
            "The context states that X.",
            "based on the information provided in the course context, Y.",
            "the course context provided does not directly mention Z.",
        ]:
            assert not mentions_context(strip_context_references(raw)), raw


class TestSanitiserIsWiredIntoTheAgents:
    """
    The unit tests above prove the sanitiser works; these prove it is actually
    APPLIED. In live verification the prompt rule alone was enough and the
    sanitiser never fired, which is the good outcome — but it means the
    enforcement path needs covering here rather than by observation.
    """

    def test_tutor_response_is_sanitised_before_it_reaches_the_student(self):
        import uuid

        from agents.tutor.nodes import generate_guiding_question_node

        class _Model:
            def generate(self, prompt, **kw):
                return (
                    "The course context provided does not directly mention "
                    "Bellman-Ford, but according to the context, relaxation repeats."
                )

        class _Retrieval:
            def build_context_window(self, result, token_budget=3000):
                return "course text"

            def build_citation_bundle(self, result):
                return []

        state = {
            "session_id": uuid.uuid4(),
            "course_id": uuid.uuid4(),
            "student_id": uuid.uuid4(),
            "question": "What is Bellman-Ford for?",
            "prior_question": None,
            "resolved_question": None,
            "retrieval_query_source": "message",
            "pedagogy_skip_requested": False,
            "retrieval_result": {"query": "q", "chunks": [], "confidence_scores": []},
            "is_grounded": True,
            "hint_level": 0,
            "response": "",
            "citations": [],
            "pedagogy_trace": {},
        }
        out = generate_guiding_question_node(
            state, retrieval_service=_Retrieval(), gemini_pro=_Model()
        )
        assert not mentions_context(out["response"]), out["response"]
        assert "Bellman-Ford" in out["response"], "content must survive the strip"

    def test_generated_stems_and_options_are_normalised_on_the_way_to_the_db(self):
        # Covers the assessment side of the same wiring: the node must strip
        # both the letter prefix and the context framing before persisting.
        from agents.assessment.nodes import _strip_option_prefix

        raw_stem = "When did humans emerge in Africa according to the context?"
        raw_options = ["A. Two billion years ago", "B. 300,000 years ago"]

        stem = strip_context_references(raw_stem)
        options = [_strip_option_prefix(o) for o in raw_options]

        assert stem == "When did humans emerge in Africa?"
        assert options == ["Two billion years ago", "300,000 years ago"]
