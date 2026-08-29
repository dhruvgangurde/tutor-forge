"""
tests/test_assessment_unit.py
------------------------------
Pure unit tests for the assessment subsystem that require NO database connection
and NO FastAPI app startup.

These test:
  - ORM model structure (columns, constraints, defaults)
  - Pydantic schema validators
  - assessments/service._sanitise_error helper
  - agents/assessment/nodes pure functions

Isolation strategy: imports are done inline inside each test to avoid the
module-level engine creation in core/database.py (which requires asyncpg and
a live connection string). This keeps these tests fast and dependency-free.
"""

import json
import uuid

import pytest


# ── ORM model integrity ───────────────────────────────────────────────────────

class TestAssessmentModelIntegrity:
    """
    Structural checks on the Assessment ORM model.
    No DB connection required — we inspect SQLAlchemy table metadata only.
    """

    def _get_assessment_table(self):
        """Import Assessment without triggering core.database's engine creation."""
        # db.models imports core.database, which imports core.config (settings) and
        # then builds the engine. We therefore need asyncpg installed OR we skip.
        # On the CI/dev machine where only basic deps are present, use pytest.importorskip.
        asyncpg = pytest.importorskip("asyncpg", reason="asyncpg not installed; skipping ORM tests")
        from db.models import Assessment
        return Assessment.__table__

    def test_generation_error_column_exists(self):
        table = self._get_assessment_table()
        assert "generation_error" in {c.key for c in table.columns}

    def test_published_at_column_exists(self):
        table = self._get_assessment_table()
        assert "published_at" in {c.key for c in table.columns}

    def test_status_check_constraint_exists(self):
        table = self._get_assessment_table()
        from sqlalchemy import CheckConstraint
        names = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_assessments_status" in names

    def test_status_constraint_covers_all_values(self):
        table = self._get_assessment_table()
        from sqlalchemy import CheckConstraint
        for c in table.constraints:
            if isinstance(c, CheckConstraint) and c.name == "ck_assessments_status":
                expr = str(c.sqltext)
                for val in ("generating", "draft", "published", "failed"):
                    assert val in expr, f"'{val}' missing from constraint: {expr}"
                return
        raise AssertionError("ck_assessments_status not found")

    def test_status_default_is_generating(self):
        table = self._get_assessment_table()
        col = table.c["status"]
        assert col.default.arg == "generating"


# ── Schema validator unit tests ───────────────────────────────────────────────

class TestGenerateRequestValidation:
    """Pydantic v2 validator tests — no imports that touch the DB."""

    def _make(self, **kwargs):
        from assessments.schemas import GenerateRequest
        return GenerateRequest(**kwargs)

    def _raises(self, **kwargs):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._make(**kwargs)

    def test_valid_minimal_request(self):
        req = self._make(
            course_id=uuid.uuid4(),
            title="Test",
            topic="Photosynthesis",
        )
        assert req.difficulty == "mixed"
        assert req.count == 10

    def test_topic_blank_rejected(self):
        self._raises(course_id=uuid.uuid4(), title="T", topic="   ")

    def test_topic_strip_applied(self):
        req = self._make(course_id=uuid.uuid4(), title="T", topic="  Plants  ")
        assert req.topic == "Plants"

    def test_title_blank_rejected(self):
        self._raises(course_id=uuid.uuid4(), title="   ", topic="Plants")

    def test_invalid_difficulty_rejected(self):
        self._raises(course_id=uuid.uuid4(), title="T", topic="T", difficulty="extreme")

    def test_difficulty_case_insensitive(self):
        req = self._make(course_id=uuid.uuid4(), title="T", topic="Ecology", difficulty="HARD")
        assert req.difficulty == "hard"

    def test_count_below_min_rejected(self):
        self._raises(course_id=uuid.uuid4(), title="T", topic="T", count=0)

    def test_count_above_max_rejected(self):
        self._raises(course_id=uuid.uuid4(), title="T", topic="T", count=31)

    def test_invalid_bloom_level_rejected(self):
        self._raises(
            course_id=uuid.uuid4(), title="T", topic="T",
            bloom_mix={"synthesize": 2}, count=2,
        )

    def test_bloom_total_mismatch_rejected(self):
        self._raises(
            course_id=uuid.uuid4(), title="T", topic="T",
            bloom_mix={"remember": 3}, count=10,
        )

    def test_bloom_total_matches_passes(self):
        req = self._make(
            course_id=uuid.uuid4(), title="T", topic="Photosynthesis",
            bloom_mix={"remember": 2, "understand": 3}, count=5,
        )
        assert req.bloom_mix == {"remember": 2, "understand": 3}

    def test_type_mix_total_mismatch_rejected(self):
        self._raises(
            course_id=uuid.uuid4(), title="T", topic="T",
            type_mix={"mcq": 5}, count=10,
        )

    def test_invalid_question_type_rejected(self):
        self._raises(
            course_id=uuid.uuid4(), title="T", topic="T",
            type_mix={"essay": 5}, count=5,
        )

    def test_empty_bloom_mix_uses_defaults(self):
        req = self._make(course_id=uuid.uuid4(), title="T", topic="Ecology", bloom_mix={})
        assert req.bloom_mix == {}  # empty is allowed (agent uses defaults)


class TestSubmissionResponseItemValidation:
    """Typed submission item validation."""

    def _make(self, **kwargs):
        from assessments.schemas import SubmissionResponseItem
        return SubmissionResponseItem(**kwargs)

    def _raises(self, **kwargs):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._make(**kwargs)

    def test_valid_mcq_choice(self):
        item = self._make(question_id=uuid.uuid4(), answer_choice="A")
        assert item.answer_choice == "A"

    def test_choice_lowercased_and_normalised(self):
        item = self._make(question_id=uuid.uuid4(), answer_choice=" b ")
        assert item.answer_choice == "B"

    def test_valid_text_answer(self):
        item = self._make(question_id=uuid.uuid4(), answer_text="The answer is 42.")
        assert item.answer_text == "The answer is 42."

    def test_no_answer_accepted_as_explicit_skip(self):
        # A skipped question must not fail the whole submission — it is stored
        # as a row with both answer columns NULL and graded 0.
        item = self._make(question_id=uuid.uuid4())
        assert item.answer_text is None
        assert item.answer_choice is None

    def test_blank_text_normalised_to_none(self):
        item = self._make(question_id=uuid.uuid4(), answer_text="   ")
        assert item.answer_text is None

    def test_blank_choice_normalised_to_none(self):
        item = self._make(question_id=uuid.uuid4(), answer_choice="  ")
        assert item.answer_choice is None

    def test_invalid_choice_rejected(self):
        self._raises(question_id=uuid.uuid4(), answer_choice="E")

    def test_choice_f_rejected(self):
        self._raises(question_id=uuid.uuid4(), answer_choice="F")


# ── _sanitise_error unit tests ────────────────────────────────────────────────

class TestSanitiseError:
    """Pure unit tests for the _sanitise_error helper in service.py."""

    def _fn(self):
        from assessments.service import _sanitise_error
        return _sanitise_error

    def test_normal_message_passes_through(self):
        fn = self._fn()
        assert fn(Exception("Topic not found.")) == "Topic not found."

    def test_long_message_is_truncated(self):
        from assessments.service import _MAX_ERROR_LEN
        fn = self._fn()
        result = fn(Exception("x" * 1000))
        assert len(result) <= _MAX_ERROR_LEN + 1
        assert result.endswith("…")

    def test_empty_message_replaced_with_default(self):
        fn = self._fn()
        result = fn(Exception(""))
        assert "unexpected error" in result.lower()

    def test_none_str_replaced(self):
        fn = self._fn()
        result = fn(Exception("none"))
        assert "unexpected error" in result.lower()

    def test_no_description_replaced(self):
        fn = self._fn()
        result = fn(Exception("<no description>"))
        assert "unexpected error" in result.lower()

    def test_exactly_max_length_not_truncated(self):
        from assessments.service import _MAX_ERROR_LEN
        fn = self._fn()
        msg = "a" * _MAX_ERROR_LEN
        result = fn(Exception(msg))
        # Exactly at limit — should NOT append ellipsis
        assert not result.endswith("…")
        assert len(result) == _MAX_ERROR_LEN


# ── Node pure function tests ──────────────────────────────────────────────────

class TestNodePureFunctions:
    """Tests for stateless helper functions in nodes.py."""

    def test_strip_json_fences_with_json_prefix(self):
        from agents.assessment.nodes import _strip_json_fences
        raw = "```json\n{\"key\": 1}\n```"
        assert _strip_json_fences(raw) == '{"key": 1}'

    def test_strip_json_fences_plain_backticks(self):
        from agents.assessment.nodes import _strip_json_fences
        raw = "```\n{\"key\": 1}\n```"
        assert _strip_json_fences(raw) == '{"key": 1}'

    def test_strip_json_fences_no_fences(self):
        from agents.assessment.nodes import _strip_json_fences
        raw = '{"key": 1}'
        assert _strip_json_fences(raw) == '{"key": 1}'

    def test_normalize_bloom_valid(self):
        from agents.assessment.nodes import _normalize_bloom
        for level in ("remember", "understand", "apply", "analyze", "evaluate", "create"):
            assert _normalize_bloom(level) == level

    def test_normalize_bloom_uppercase(self):
        from agents.assessment.nodes import _normalize_bloom
        assert _normalize_bloom("APPLY") == "apply"

    def test_normalize_bloom_invalid_falls_back(self):
        from agents.assessment.nodes import _normalize_bloom
        assert _normalize_bloom("synthesize") == "understand"
        assert _normalize_bloom("") == "understand"

    def test_normalize_difficulty_valid(self):
        from agents.assessment.nodes import _normalize_difficulty
        for d in ("easy", "medium", "hard"):
            assert _normalize_difficulty(d) == d

    def test_normalize_difficulty_uppercase(self):
        from agents.assessment.nodes import _normalize_difficulty
        assert _normalize_difficulty("HARD") == "hard"

    def test_normalize_difficulty_invalid_falls_back(self):
        from agents.assessment.nodes import _normalize_difficulty
        assert _normalize_difficulty("extreme") == "medium"
        assert _normalize_difficulty("") == "medium"

    def test_refuse_node_sets_failed(self):
        from agents.assessment.nodes import refuse_node
        state = {
            "course_id": uuid.UUID(int=0),
            "teacher_id": uuid.UUID(int=1),
            "config": {"topic": "Plants"},
            "retrieved_concepts": [],
            "retrieval_cache": {},
            "is_grounded": False,
            "questions": [],
            "bloom_tags": [],
            "distractors": [],
            "rubric_criteria": [],
            "answer_key": [],
            "assessment_id": uuid.UUID(int=2),
            "trace_id": None,
            "status": "running",
            "error": None,
        }
        result = refuse_node(state)
        assert result["status"] == "failed"
        assert result["error"] is not None
        assert result["questions"] == []
        assert result["bloom_tags"] == []
