"""
tests/test_grading_unit.py
---------------------------
Pure unit tests for the Grading Agent subsystem.
No database connection, no FastAPI app startup, no Gemini API calls.

Test groups:
  TestMCQGrading           — deterministic MCQ exact-match logic in grade_responses_node
  TestNumericGrading       — tolerance-based numeric grading
  TestUnansweredGrading    — questions the student skipped score 0, no LLM call
  TestStripJsonFences      — _strip_json_fences helper
  TestClampHelper          — _clamp helper boundary values
  TestSafeFloat            — _safe_float conversion helper
  TestGradingSchemas       — Pydantic v2 validators for ApproveRequest, OverrideRequest
  TestSanitiseGradingError — _sanitise_error in grading/service.py
  TestGradingStateShape    — GradingState TypedDict key completeness
"""

import json
import uuid

import pytest


# ── Helpers imported inline to avoid core.database engine creation ────────────

def _nodes():
    from agents.grading.nodes import (
        _strip_json_fences,
        _clamp,
        _safe_float,
        grade_responses_node,
    )
    return _strip_json_fences, _clamp, _safe_float, grade_responses_node


def _minimal_state(**overrides):
    """Build a minimal GradingState dict for unit tests."""
    base = {
        "submission_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "responses": [],
        "evidence_per_question": [],
        "criterion_results": [],
        "total_score": 0.0,
        "max_score": 0.0,
        "recommendation_rationale": {},
        "recommendation_id": None,
        "trace_id": None,
        "status": "running",
        "error": None,
    }
    base.update(overrides)
    return base


def _mcq_response(correct_answer: str, student_choice: str, max_points: float = 1.0) -> dict:
    return {
        "question_id": str(uuid.uuid4()),
        "question_type": "mcq",
        "stem": "What is photosynthesis?",
        "answer_key": {"correct_answer": correct_answer},
        "answer_text": None,
        "answer_choice": student_choice,
        "max_points": max_points,
        "rubric_criteria": [],
    }


def _numeric_response(correct_answer, student_answer, max_points: float = 2.0) -> dict:
    return {
        "question_id": str(uuid.uuid4()),
        "question_type": "numeric",
        "stem": "What is 6.022 × 10²³?",
        "answer_key": {"correct_answer": str(correct_answer)},
        "answer_text": str(student_answer),
        "answer_choice": None,
        "max_points": max_points,
        "rubric_criteria": [],
    }


# ── _strip_json_fences ────────────────────────────────────────────────────────

class TestStripJsonFences:
    def _fn(self):
        strip, *_ = _nodes()
        return strip

    def test_removes_json_fence(self):
        fn = self._fn()
        assert fn('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_removes_plain_fence(self):
        fn = self._fn()
        assert fn('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_passthrough_no_fence(self):
        fn = self._fn()
        assert fn('{"a": 1}') == '{"a": 1}'

    def test_strips_whitespace(self):
        fn = self._fn()
        assert fn('  {"a": 1}  ') == '{"a": 1}'

    def test_multiline_json_preserved(self):
        fn = self._fn()
        raw = '```json\n{\n  "key": "value"\n}\n```'
        result = fn(raw)
        assert result == '{\n  "key": "value"\n}'


# ── _clamp ─────────────────────────────────────────────────────────────────────

class TestClampHelper:
    def _fn(self):
        _, clamp, *_ = _nodes()
        return clamp

    def test_within_range_unchanged(self):
        assert self._fn()(2.5, 0.0, 5.0) == 2.5

    def test_below_minimum_clamped(self):
        assert self._fn()(-1.0, 0.0, 5.0) == 0.0

    def test_above_maximum_clamped(self):
        assert self._fn()(6.0, 0.0, 5.0) == 5.0

    def test_exactly_at_min(self):
        assert self._fn()(0.0, 0.0, 5.0) == 0.0

    def test_exactly_at_max(self):
        assert self._fn()(5.0, 0.0, 5.0) == 5.0


# ── _safe_float ───────────────────────────────────────────────────────────────

class TestSafeFloat:
    def _fn(self):
        _, _, safe_float, *_ = _nodes()
        return safe_float

    def test_int_converted(self):
        assert self._fn()(3) == 3.0

    def test_string_number_converted(self):
        assert self._fn()("3.14") == pytest.approx(3.14)

    def test_none_returns_default(self):
        assert self._fn()(None) == 0.0

    def test_non_numeric_string_returns_default(self):
        assert self._fn()("abc") == 0.0

    def test_custom_default(self):
        _, _, safe_float, *_ = _nodes()
        assert safe_float("bad", 99.0) == 99.0


# ── MCQ grading ───────────────────────────────────────────────────────────────

class TestMCQGrading:
    """
    grade_responses_node with MCQ responses does not call the LLM.
    We pass a None gemini_pro to confirm this (would raise if called).
    """

    def _grade(self, responses):
        *_, grade_fn = _nodes()
        state = _minimal_state(
            responses=responses,
            evidence_per_question=[[] for _ in responses],
        )
        return grade_fn(state, gemini_pro=None)  # gemini_pro=None: would fail if called

    def test_correct_answer_full_points(self):
        result = self._grade([_mcq_response("A", "A", max_points=2.0)])
        assert result["total_score"] == 2.0
        assert result["max_score"] == 2.0

    def test_wrong_answer_zero_points(self):
        result = self._grade([_mcq_response("A", "B")])
        assert result["total_score"] == 0.0

    def test_case_insensitive_correct(self):
        result = self._grade([_mcq_response("A", "a")])
        assert result["total_score"] == 1.0

    def test_empty_answer_zero_points(self):
        result = self._grade([_mcq_response("A", "")])
        assert result["total_score"] == 0.0

    def test_none_answer_zero_points(self):
        resp = _mcq_response("B", "A")
        resp["answer_choice"] = None
        result = self._grade([resp])
        assert result["total_score"] == 0.0

    def test_multiple_questions_sum_correctly(self):
        responses = [
            _mcq_response("A", "A", max_points=1.0),  # correct
            _mcq_response("B", "C", max_points=2.0),  # wrong
            _mcq_response("D", "D", max_points=3.0),  # correct
        ]
        result = self._grade(responses)
        assert result["total_score"] == pytest.approx(4.0)
        assert result["max_score"] == pytest.approx(6.0)

    def test_criterion_result_structure(self):
        result = self._grade([_mcq_response("A", "A")])
        assert len(result["criterion_results"]) == 1
        crit = result["criterion_results"][0][0]
        assert crit["description"] == "MCQ correctness"
        assert crit["criterion_id"] is None
        assert crit["max_points"] == 1.0

    def test_feedback_contains_correct_answer(self):
        result = self._grade([_mcq_response("C", "B")])
        feedback = result["criterion_results"][0][0]["feedback"]
        assert "C" in feedback   # correct answer mentioned

    def test_correct_feedback_mentions_correct(self):
        result = self._grade([_mcq_response("A", "A")])
        feedback = result["criterion_results"][0][0]["feedback"]
        assert "Correct" in feedback

    def test_rationale_method_is_deterministic(self):
        result = self._grade([_mcq_response("A", "A")])
        assert result["recommendation_rationale"]["grading_method"] == "deterministic_exact_match"


# ── Numeric grading ───────────────────────────────────────────────────────────

class TestNumericGrading:
    def _grade(self, responses):
        *_, grade_fn = _nodes()
        state = _minimal_state(
            responses=responses,
            evidence_per_question=[[] for _ in responses],
        )
        return grade_fn(state, gemini_pro=None)

    def test_exact_match_full_points(self):
        result = self._grade([_numeric_response(42.0, 42.0, max_points=3.0)])
        assert result["total_score"] == pytest.approx(3.0)

    def test_within_tolerance_full_points(self):
        # tolerance is ±1% of expected value → 6.022e23 * 0.01 = 6.022e21
        expected = 6.022e23
        student = expected + expected * 0.005  # within 1%
        result = self._grade([_numeric_response(expected, student)])
        assert result["total_score"] == pytest.approx(2.0)

    def test_outside_tolerance_zero_points(self):
        expected = 100.0
        student = 105.0   # 5% off — outside 1% tolerance
        result = self._grade([_numeric_response(expected, student)])
        assert result["total_score"] == 0.0

    def test_non_numeric_response_zero(self):
        resp = _numeric_response(42.0, 42.0)
        resp["answer_text"] = "about forty-two"
        result = self._grade([resp])
        assert result["total_score"] == 0.0

    def test_zero_expected_tolerance(self):
        # When expected is 0, tolerance falls back to NUMERIC_TOLERANCE constant
        result = self._grade([_numeric_response(0.0, 0.0)])
        assert result["total_score"] == pytest.approx(2.0)

    def test_criterion_description_set(self):
        result = self._grade([_numeric_response(5.0, 5.0)])
        crit = result["criterion_results"][0][0]
        assert crit["description"] == "Numeric correctness"

    def test_negative_student_answer_accepted(self):
        result = self._grade([_numeric_response(-10.0, -10.0)])
        assert result["total_score"] == pytest.approx(2.0)

    # ── List keys ("what is the final sorted array?") ─────────────────────────
    # The generator produces numeric questions whose key is an ordered list.
    # float() on such a key raised, so every student scored 0 -- including one
    # who typed the key exactly -- with feedback blaming the student's answer.

    @pytest.mark.parametrize("answer", ["3,27,38,43", "3, 27, 38, 43", "[3,27,38,43]", "3;27;38;43"])
    def test_list_key_accepts_the_same_list_in_any_notation(self, answer):
        result = self._grade([_numeric_response("3,27,38,43", answer, max_points=1.0)])
        assert result["total_score"] == pytest.approx(1.0)

    def test_list_key_is_order_sensitive(self):
        result = self._grade([_numeric_response("3,27,38,43", "3,27,43,38", max_points=1.0)])
        assert result["total_score"] == 0.0
        assert "in that order" in result["criterion_results"][0][0]["feedback"]

    def test_list_key_rejects_a_different_length(self):
        result = self._grade([_numeric_response("3,27,38,43", "3,27,38", max_points=1.0)])
        assert result["total_score"] == 0.0
        assert "Expected 4 values" in result["criterion_results"][0][0]["feedback"]

    def test_list_elements_use_the_same_tolerance(self):
        # each element within ±1% of its expected value
        result = self._grade([_numeric_response("100,200", "100.5,199", max_points=1.0)])
        assert result["total_score"] == pytest.approx(1.0)
        result = self._grade([_numeric_response("100,200", "100,210", max_points=1.0)])
        assert result["total_score"] == 0.0

    def test_unparseable_key_is_reported_as_a_key_problem_not_the_students(self):
        result = self._grade([_numeric_response("about forty", "40")])
        assert result["total_score"] == 0.0
        feedback = result["criterion_results"][0][0]["feedback"]
        assert "answer key is not a number" in feedback
        assert "non-numeric response" not in feedback

    def test_scalar_feedback_is_unchanged(self):
        result = self._grade([_numeric_response(42.0, 42.0)])
        assert result["criterion_results"][0][0]["feedback"] == (
            "Correct. Your answer 42.0 is within the accepted range."
        )


class TestParseNumericAnswer:
    def _fn(self):
        from grading.numeric import parse_numeric_answer
        return parse_numeric_answer

    @pytest.mark.parametrize("raw, expected", [
        ("20", [20.0]),
        (" -3.5 ", [-3.5]),
        ("3,27,38,43", [3.0, 27.0, 38.0, 43.0]),
        ("(1; 2)", [1.0, 2.0]),
        ("6.022e23", [6.022e23]),
    ])
    def test_parses_numbers_and_lists(self, raw, expected):
        assert self._fn()(raw) == expected

    @pytest.mark.parametrize("raw", ["", "  ", None, "abc", "3,,4", "3,", "nan", "inf", "[]", "3 4"])
    def test_rejects_anything_else(self, raw):
        assert self._fn()(raw) is None


# ── Unanswered (skipped) questions ────────────────────────────────────────────

class TestUnansweredGrading:
    """
    A skipped question is submitted explicitly and stored with both answer
    columns NULL. It must score 0 with a clear rationale — never crash, never
    be silently dropped, and never burn an LLM call.
    """

    def _grade(self, responses):
        *_, grade_fn = _nodes()
        state = _minimal_state(
            responses=responses,
            evidence_per_question=[[] for _ in responses],
        )
        # gemini_pro=None: if any branch tried to call the LLM this would raise.
        return grade_fn(state, gemini_pro=None)

    def _blank(self, question_type: str, max_points: float = 2.0, rubric=None) -> dict:
        return {
            "question_id": str(uuid.uuid4()),
            "question_type": question_type,
            "stem": "Explain the Calvin cycle.",
            "answer_key": {"correct_answer": "A"},
            "answer_text": None,
            "answer_choice": None,
            "max_points": max_points,
            "rubric_criteria": rubric or [],
        }

    def test_is_unanswered_helper(self):
        from agents.grading.nodes import _is_unanswered
        assert _is_unanswered({"answer_text": None, "answer_choice": None})
        assert _is_unanswered({"answer_text": "   ", "answer_choice": ""})
        assert not _is_unanswered({"answer_text": "42", "answer_choice": None})
        assert not _is_unanswered({"answer_text": None, "answer_choice": "B"})

    def test_blank_mcq_scores_zero_but_counts_toward_max(self):
        result = self._grade([self._blank("mcq", max_points=2.0)])
        assert result["total_score"] == 0.0
        assert result["max_score"] == pytest.approx(2.0)

    def test_blank_numeric_scores_zero(self):
        result = self._grade([self._blank("numeric", max_points=3.0)])
        assert result["total_score"] == 0.0
        assert result["max_score"] == pytest.approx(3.0)

    def test_blank_short_answer_scores_zero_without_llm(self):
        rubric = [
            {"criterion_id": str(uuid.uuid4()), "description": "Names the enzyme",
             "max_points": 2.0, "order_index": 0},
            {"criterion_id": str(uuid.uuid4()), "description": "Describes the steps",
             "max_points": 3.0, "order_index": 1},
        ]
        result = self._grade([self._blank("short_answer", max_points=5.0, rubric=rubric)])
        assert result["total_score"] == 0.0
        criteria = result["criterion_results"][0]
        # One entry per rubric criterion, so the teacher review UI shape is unchanged.
        assert len(criteria) == 2
        assert all(c["score"] == 0.0 for c in criteria)
        assert all("Not answered" in c["feedback"] for c in criteria)

    def test_rationale_explains_not_answered(self):
        result = self._grade([self._blank("mcq")])
        feedback = result["criterion_results"][0][0]["feedback"]
        assert "Not answered" in feedback
        assert result["recommendation_rationale"]["grading_method"] == "not_answered"

    def test_mixed_answered_and_skipped_sums_correctly(self):
        responses = [
            _mcq_response("A", "A", max_points=2.0),   # answered, correct
            self._blank("mcq", max_points=3.0),        # skipped
            _numeric_response(10.0, 10.0),             # answered, correct (2.0)
        ]
        result = self._grade(responses)
        assert result["total_score"] == pytest.approx(4.0)
        assert result["max_score"] == pytest.approx(7.0)
        assert len(result["criterion_results"]) == 3
        assert result["recommendation_rationale"]["grading_method"] == "mixed"

    def test_skipped_question_still_present_in_rationale(self):
        blank = self._blank("mcq")
        result = self._grade([_mcq_response("A", "A"), blank])
        question_ids = [q["question_id"] for q in result["recommendation_rationale"]["questions"]]
        assert blank["question_id"] in question_ids

    def test_evidence_retrieval_skipped_for_blank_short_answer(self):
        from agents.grading.nodes import retrieve_evidence_node

        class _BoomRetrieval:
            def retrieve(self, **kwargs):  # pragma: no cover - must not be called
                raise AssertionError("retrieval must not run for an unanswered question")

        rubric = [{"criterion_id": str(uuid.uuid4()), "description": "d",
                   "max_points": 1.0, "order_index": 0}]
        state = _minimal_state(responses=[self._blank("short_answer", rubric=rubric)])
        out = retrieve_evidence_node(state, retrieval_service=_BoomRetrieval())
        assert out["evidence_per_question"] == [[]]


# ── Grading schemas ───────────────────────────────────────────────────────────

class TestGradingSchemas:
    def test_approve_request_optional_note(self):
        from grading.schemas import ApproveRequest
        req = ApproveRequest()
        assert req.note is None

    def test_approve_request_with_note(self):
        from grading.schemas import ApproveRequest
        req = ApproveRequest(note="Looks good.")
        assert req.note == "Looks good."

    def test_override_request_requires_reason(self):
        from pydantic import ValidationError
        from grading.schemas import OverrideRequest
        with pytest.raises(ValidationError):
            OverrideRequest(final_score=5.0)  # missing reason

    def test_override_request_blank_reason_rejected(self):
        from pydantic import ValidationError
        from grading.schemas import OverrideRequest
        with pytest.raises(ValidationError):
            OverrideRequest(final_score=5.0, reason="   ")

    def test_override_request_negative_score_rejected(self):
        from pydantic import ValidationError
        from grading.schemas import OverrideRequest
        with pytest.raises(ValidationError):
            OverrideRequest(final_score=-1.0, reason="Reason here.")

    def test_override_request_valid(self):
        from grading.schemas import OverrideRequest
        req = OverrideRequest(final_score=7.5, reason="Response was partially correct.")
        assert req.final_score == 7.5

    def test_grading_ack_default_status(self):
        from grading.schemas import GradingAck
        ack = GradingAck(
            submission_id=uuid.uuid4(),
            message="Grading enqueued.",
        )
        assert ack.status == "grading_in_progress"

    def test_criterion_grade_zero_score_valid(self):
        from grading.schemas import CriterionGrade
        cg = CriterionGrade(
            description="Accuracy",
            score=0.0,
            max_points=5.0,
            feedback="No supporting evidence found.",
        )
        assert cg.score == 0.0

    def test_criterion_grade_negative_score_rejected(self):
        from pydantic import ValidationError
        from grading.schemas import CriterionGrade
        with pytest.raises(ValidationError):
            CriterionGrade(
                description="Accuracy",
                score=-1.0,
                max_points=5.0,
                feedback="Error.",
            )

    def test_evidence_citation_confidence_clamped(self):
        from pydantic import ValidationError
        from grading.schemas import EvidenceCitation
        with pytest.raises(ValidationError):
            EvidenceCitation(text="text", source_file="f.pdf", confidence=1.5)


# ── _sanitise_error (grading/service.py) ─────────────────────────────────────

class TestSanitiseGradingError:
    def _fn(self):
        from grading.service import _sanitise_error
        return _sanitise_error

    def test_normal_message(self):
        assert self._fn()(Exception("Submission not found.")) == "Submission not found."

    def test_long_message_truncated(self):
        from grading.service import _MAX_ERROR_LEN
        result = self._fn()(Exception("x" * 1000))
        assert len(result) <= _MAX_ERROR_LEN + 1
        assert result.endswith("…")

    def test_empty_message_fallback(self):
        result = self._fn()(Exception(""))
        assert "unexpected error" in result.lower()

    def test_none_message_fallback(self):
        result = self._fn()(Exception("none"))
        assert "unexpected error" in result.lower()


# ── GradingState shape ────────────────────────────────────────────────────────

class TestGradingStateShape:
    """Confirm all expected keys are declared in GradingState."""

    REQUIRED_KEYS = {
        "submission_id",
        "course_id",
        "responses",
        "evidence_per_question",
        "criterion_results",
        "total_score",
        "max_score",
        "recommendation_rationale",
        "recommendation_id",
        "trace_id",
        "status",
        "error",
        # Per-response groundedness gate decision (BUG-AUDIT Critical #2).
        "evidence_grounded",
    }

    def test_all_required_keys_present(self):
        from agents.grading.state import GradingState
        declared = set(GradingState.__annotations__.keys())
        missing = self.REQUIRED_KEYS - declared
        assert not missing, f"Keys missing from GradingState: {missing}"

    def test_no_unexpected_keys(self):
        """Guard against field name typos that would silently be ignored."""
        from agents.grading.state import GradingState
        declared = set(GradingState.__annotations__.keys())
        # Every declared key should be in our expected set — catches additions
        # that weren't documented here.
        for key in declared:
            assert key in self.REQUIRED_KEYS, (
                f"Undocumented key '{key}' found in GradingState — "
                "add it to REQUIRED_KEYS if intentional."
            )


# ── Rationale structure ───────────────────────────────────────────────────────

class TestRationaleStructure:
    """grade_responses_node must produce a correctly structured rationale dict."""

    def _grade_mcq(self):
        *_, grade_fn = _nodes()
        responses = [_mcq_response("A", "A")]
        state = _minimal_state(
            responses=responses,
            evidence_per_question=[[]],
        )
        return grade_fn(state, gemini_pro=None)

    def test_rationale_has_grading_method(self):
        result = self._grade_mcq()
        assert "grading_method" in result["recommendation_rationale"]

    def test_rationale_has_questions_list(self):
        result = self._grade_mcq()
        assert "questions" in result["recommendation_rationale"]
        assert len(result["recommendation_rationale"]["questions"]) == 1

    def test_question_entry_has_question_id(self):
        result = self._grade_mcq()
        q = result["recommendation_rationale"]["questions"][0]
        assert "question_id" in q
        assert "question_type" in q
        assert q["question_type"] == "mcq"

    def test_question_entry_has_criteria(self):
        result = self._grade_mcq()
        q = result["recommendation_rationale"]["questions"][0]
        assert "criteria" in q
        assert len(q["criteria"]) == 1


# ── Per-question grouping of the grading detail ───────────────────────────────


class TestGroupQuestionGrades:
    """
    GradingDetail used to flatten every question's criteria into one list, so a
    teacher reviewing a multi-question assessment could not tell which criterion
    belonged to which question. group_question_grades restores the grouping the
    stored rationale already had.
    """

    def _fn(self):
        from grading.service import group_question_grades

        return group_question_grades

    def _question(self, qid, stem, order_index, question_type="short_answer"):
        """A stand-in for the ORM row — only the read attributes matter."""

        class _Q:
            pass

        q = _Q()
        q.id = qid
        q.stem = stem
        q.order_index = order_index
        q.question_type = question_type
        return q

    def _rationale(self, *entries):
        return {"grading_method": "mixed", "questions": list(entries)}

    def test_criteria_stay_with_their_question(self):
        q1, q2 = uuid.uuid4(), uuid.uuid4()
        rationale = self._rationale(
            {
                "question_id": str(q1),
                "question_type": "short_answer",
                "criteria": [{"description": "a"}, {"description": "b"}],
            },
            {
                "question_id": str(q2),
                "question_type": "mcq",
                "criteria": [{"description": "c"}],
            },
        )
        out = self._fn()(
            rationale,
            {
                q1: self._question(q1, "Explain photosynthesis.", 0),
                q2: self._question(q2, "Which organelle?", 1, "mcq"),
            },
        )

        assert len(out) == 2
        assert [c["description"] for c in out[0]["criteria"]] == ["a", "b"]
        assert [c["description"] for c in out[1]["criteria"]] == ["c"]

    def test_stem_is_hydrated_from_the_questions_table(self):
        # The grading agent never stores the stem — see agents/grading/nodes.py,
        # where each entry carries only question_id / question_type / criteria.
        qid = uuid.uuid4()
        rationale = self._rationale(
            {"question_id": str(qid), "question_type": "mcq", "criteria": []}
        )
        out = self._fn()(rationale, {qid: self._question(qid, "Which organelle?", 0, "mcq")})
        assert out[0]["stem"] == "Which organelle?"
        assert out[0]["question_id"] == qid
        assert out[0]["question_type"] == "mcq"

    def test_ordered_by_question_order_index_not_rationale_order(self):
        # The rationale's own order follows submission.responses, which is
        # unordered, so the teacher could otherwise see Q3 before Q1.
        q1, q2, q3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        rationale = self._rationale(
            {"question_id": str(q3), "question_type": "mcq", "criteria": []},
            {"question_id": str(q1), "question_type": "mcq", "criteria": []},
            {"question_id": str(q2), "question_type": "mcq", "criteria": []},
        )
        out = self._fn()(
            rationale,
            {
                q1: self._question(q1, "first", 0),
                q2: self._question(q2, "second", 1),
                q3: self._question(q3, "third", 2),
            },
        )
        assert [g["stem"] for g in out] == ["first", "second", "third"]

    def test_deleted_question_keeps_its_entry_with_no_stem(self):
        # Dropping the entry would hide a graded criterion from the review
        # screen, which is worse than showing one without its wording.
        qid = uuid.uuid4()
        rationale = self._rationale(
            {
                "question_id": str(qid),
                "question_type": "numeric",
                "criteria": [{"description": "still scored"}],
            }
        )
        out = self._fn()(rationale, {})
        assert len(out) == 1
        assert out[0]["stem"] is None
        assert out[0]["question_type"] == "numeric"
        assert out[0]["criteria"][0]["description"] == "still scored"

    def test_unparseable_question_id_does_not_raise(self):
        rationale = self._rationale(
            {"question_id": "not-a-uuid", "question_type": "mcq", "criteria": []}
        )
        out = self._fn()(rationale, {})
        assert len(out) == 1
        assert out[0]["question_id"] is None

    def test_empty_and_missing_rationale_produce_no_groups(self):
        fn = self._fn()
        assert fn({}, {}) == []
        assert fn({"questions": []}, {}) == []
        assert fn({"questions": None}, {}) == []

    def test_no_internal_sort_keys_leak_into_the_output(self):
        qid = uuid.uuid4()
        rationale = self._rationale(
            {"question_id": str(qid), "question_type": "mcq", "criteria": []}
        )
        out = self._fn()(rationale, {qid: self._question(qid, "s", 0, "mcq")})
        assert set(out[0]) == {"question_id", "question_type", "stem", "criteria"}


class TestQuestionIdsIn:
    def _fn(self):
        from grading.service import question_ids_in

        return question_ids_in

    def test_collects_parseable_ids_only(self):
        q1, q2 = uuid.uuid4(), uuid.uuid4()
        rationale = {
            "questions": [
                {"question_id": str(q1)},
                {"question_id": "garbage"},
                {"question_id": None},
                {},
                {"question_id": str(q2)},
            ]
        }
        assert self._fn()(rationale) == [q1, q2]

    def test_empty_rationale_yields_nothing(self):
        assert self._fn()({}) == []


class TestQuestionGradeSchema:
    """The nested response schema the grouping is serialised into."""

    def test_defaults_allow_a_deleted_question(self):
        from grading.schemas import QuestionGrade

        qg = QuestionGrade()
        assert qg.question_id is None
        assert qg.stem is None
        assert qg.criteria == []

    def test_grading_detail_no_longer_carries_a_flat_criteria_field(self):
        from grading.schemas import GradingDetail

        assert "questions" in GradingDetail.model_fields
        assert "criteria" not in GradingDetail.model_fields


# ── Grading groundedness gate ─────────────────────────────────────────────────


class TestGradingGroundednessGate:
    """
    The grading agent had no groundedness gate at all (BUG-AUDIT Critical #2).
    The short-answer prompt's "no evidence -> award 0" line was an instruction to
    the model, not an enforcement, and it only covered EMPTY evidence. These
    tests pin the gate that replaced it as the enforcement point.
    """

    def _nodes(self):
        from agents.grading.nodes import (
            check_evidence_groundedness_node,
            grade_responses_node,
        )

        return check_evidence_groundedness_node, grade_responses_node

    def _retrieval(self, predicate):
        """A RetrievalService with only is_grounded stubbed by a predicate."""
        from retrieval.service import RetrievalService

        svc = RetrievalService.__new__(RetrievalService)
        svc.is_grounded = lambda result, *, threshold: predicate(result, threshold)
        return svc

    def _short_answer(self, rubric_points=2.0):
        return {
            "question_id": str(uuid.uuid4()),
            "question_type": "short_answer",
            "stem": "Explain binary search.",
            "answer_key": {},
            "answer_text": "It halves the interval each step.",
            "answer_choice": None,
            "max_points": rubric_points,
            "rubric_criteria": [
                {
                    "criterion_id": str(uuid.uuid4()),
                    "description": "Explains halving.",
                    "max_points": rubric_points,
                    "order_index": 0,
                }
            ],
        }

    def _state(self, responses, evidence):
        return _minimal_state(responses=responses, evidence_per_question=evidence)

    def test_mcq_and_numeric_are_always_grounded(self):
        # They are judged against the question's own answer_key, so corpus
        # evidence is not the basis; gating them would refuse gradeable work.
        check, _ = self._nodes()
        responses = [_mcq_response("A", "A"), _numeric_response(5.0, 5.0)]
        out = check(
            self._state(responses, [[], []]),
            retrieval_service=self._retrieval(lambda r, threshold: False),
        )
        assert out["evidence_grounded"] == [True, True]

    def test_short_answer_with_weak_evidence_is_not_grounded(self):
        check, _ = self._nodes()
        resp = self._short_answer()
        evidence = [[{"text": "t", "source_file": "f.pdf", "page_or_slide": 1, "confidence": 0.10}]]
        out = check(
            self._state([resp], evidence),
            retrieval_service=self._retrieval(lambda r, threshold: r.top_score >= threshold),
        )
        assert out["evidence_grounded"] == [False]

    def test_short_answer_with_strong_evidence_is_grounded(self):
        check, _ = self._nodes()
        resp = self._short_answer()
        evidence = [[{"text": "t", "source_file": "f.pdf", "page_or_slide": 1, "confidence": 0.95}]]
        out = check(
            self._state([resp], evidence),
            retrieval_service=self._retrieval(lambda r, threshold: r.top_score >= threshold),
        )
        assert out["evidence_grounded"] == [True]

    def test_blank_answer_is_not_reported_as_an_evidence_failure(self):
        # A skipped question scores 0 on its own terms. Calling that "we could
        # not ground it" would send the teacher hunting for a corpus problem.
        check, _ = self._nodes()
        resp = self._short_answer()
        resp["answer_text"] = None
        out = check(
            self._state([resp], [[]]),
            retrieval_service=self._retrieval(lambda r, threshold: False),
        )
        assert out["evidence_grounded"] == [True]

    def test_ungrounded_response_is_flagged_not_silently_zeroed(self):
        check, grade = self._nodes()
        resp = self._short_answer()
        state = self._state([resp], [[]])
        state = check(state, retrieval_service=self._retrieval(lambda r, threshold: False))
        out = grade(state, gemini_pro=None)  # None: an LLM call here would raise

        criteria = out["criterion_results"][0]
        assert len(criteria) == 1
        assert criteria[0]["score"] == 0.0
        assert criteria[0]["requires_review"] is True
        assert "review" in criteria[0]["feedback"].lower()
        # The denominator stays honest so the teacher can award the points.
        assert out["max_score"] == 2.0
        assert out["recommendation_rationale"]["grading_method"] == "ungrounded_pending_review"

    def test_ungrounded_response_makes_no_llm_call(self):
        # gemini_pro=None means any call raises. This is the whole point of the
        # gate: not grading confidently against nothing.
        check, grade = self._nodes()
        state = self._state([self._short_answer()], [[]])
        state = check(state, retrieval_service=self._retrieval(lambda r, threshold: False))
        grade(state, gemini_pro=None)  # must not raise

    def test_grounded_short_answer_still_reaches_the_model(self):
        check, grade = self._nodes()
        resp = self._short_answer()
        evidence = [[{"text": "binary search halves", "source_file": "f.pdf",
                      "page_or_slide": 1, "confidence": 0.95}]]
        state = self._state([resp], evidence)
        state = check(state, retrieval_service=self._retrieval(lambda r, threshold: True))

        criterion_id = resp["rubric_criteria"][0]["criterion_id"]
        payload = json.dumps(
            {
                "criterion_scores": [
                    {
                        "criterion_id": criterion_id,
                        "score": 2.0,
                        "max_points": 2.0,
                        "feedback": "ok",
                        "citations": [],
                    }
                ]
            }
        )
        called = {}

        class _Model:
            def generate_deterministic(self, prompt):
                called["prompt"] = prompt
                return payload

        out = grade(state, gemini_pro=_Model())
        assert called, "a grounded short answer must still be graded by the model"
        assert out["total_score"] == 2.0
        assert out["criterion_results"][0][0].get("requires_review", False) is False

    def test_missing_gate_state_defaults_to_grading_normally(self):
        # A caller that skips the gate node must grade exactly as before rather
        # than flagging everything.
        _check, grade = self._nodes()
        out = grade(self._state([_mcq_response("A", "A")], [[]]), gemini_pro=None)
        assert out["total_score"] == 1.0

    def test_gate_is_wired_into_the_graph_between_retrieval_and_grading(self):
        import inspect

        from agents.grading import graph as graph_module

        src = inspect.getsource(graph_module)
        assert "check_groundedness" in src
        assert '"retrieve_evidence", "check_groundedness"' in src
        assert '"check_groundedness", "grade_responses"' in src

    def test_gate_uses_the_shared_provider_threshold(self):
        # Same single source of truth as the tutor and assessment gates; a
        # private constant here would drift.
        import inspect

        from agents.grading import nodes as nodes_module

        src = inspect.getsource(nodes_module.check_evidence_groundedness_node)
        assert "active_groundedness_threshold" in src
        assert "is_grounded" in src
