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
