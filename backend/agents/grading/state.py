"""
agents/grading/state.py
------------------------
LangGraph state for the Grading Agent.

The graph processes exactly one Submission per run. The state is threaded
through all four nodes (load → retrieve → grade → persist) and is immutable
from the graph's perspective — each node returns a copy with updated fields.

Field naming conventions
------------------------
- *_per_question:    parallel arrays indexed by question position in the submission
- *_per_criterion:  parallel arrays indexed by criterion position within a question

Evidence is retrieved once per short-answer question and shared across all
criteria of that question (evidence does not need to be re-fetched per criterion).
"""

from typing import Any, TypedDict
from uuid import UUID


class GradingState(TypedDict):
    # ── Inputs (provided by the caller) ──────────────────────────────────────
    submission_id: UUID
    course_id: UUID         # needed by RetrievalService for corpus scoping

    # ── Loaded from DB (written by load_submission_node) ─────────────────────
    # Each element corresponds to one SubmissionResponse (one per question).
    responses: list[dict]
    # Schema per element:
    #   question_id:       str (UUID)
    #   question_type:     "mcq" | "short_answer" | "numeric"
    #   stem:              str
    #   answer_key:        dict  (parsed from JSON; keys: correct_answer, worked_solution)
    #   answer_text:       str | None
    #   answer_choice:     str | None   (MCQ only: "A"–"D")
    #   max_points:        float
    #   rubric_criteria:   list[dict]   (empty for MCQ/numeric)
    #   # each rubric criterion dict:
    #   #   criterion_id:  str (UUID)
    #   #   description:   str
    #   #   max_points:    float

    # ── Evidence retrieval (written by retrieve_evidence_node) ────────────────
    # Index i corresponds to responses[i]. Empty list for MCQ/numeric (no LLM needed).
    evidence_per_question: list[list[dict]]
    # Schema per chunk dict:
    #   text:           str
    #   source_file:    str
    #   page_or_slide:  int | None
    #   confidence:     float

    # ── Groundedness gate (written by check_evidence_groundedness_node) ───────
    # Index i corresponds to responses[i]. True means the response CAN be
    # evaluated against retrieved course evidence.
    #
    # Always True for MCQ/numeric: those are scored against the question's own
    # answer_key, which is the ground truth for them — corpus evidence is not
    # the basis of the judgement, so gating them on retrieval would refuse
    # perfectly gradeable work.
    #
    # For short answers it is RetrievalService.is_grounded() at
    # settings.active_groundedness_threshold — the same gate and the same
    # threshold the tutor and assessment agents use.
    evidence_grounded: list[bool]

    # ── Grading outputs (written by grade_responses_node) ─────────────────────
    # Outer index = question, inner = criterion within that question.
    # MCQ/numeric questions have exactly one criterion entry (the question itself).
    criterion_results: list[list[dict]]
    # Schema per criterion result dict:
    #   criterion_id:   str | None   (None for MCQ/numeric pseudo-criterion)
    #   description:    str
    #   score:          float
    #   max_points:     float
    #   feedback:       str
    #   citations:      list[dict]   (same schema as evidence chunks)
    #   requires_review: bool        (True when the gate could not ground it)

    total_score: float
    max_score: float

    # Serialised rationale stored as JSON in GradeRecommendation.rationale
    recommendation_rationale: dict[str, Any]
    # Schema:
    #   grading_method:     str  ("deterministic" | "llm_rubric" | "mixed")
    #   questions:          list[dict]  (one per response)
    #     question_id:      str
    #     question_type:    str
    #     criteria:         list[dict]  (same as criterion_results entries)

    # ── Persistence outputs (written by persist_recommendation_node) ──────────
    recommendation_id: UUID | None   # set after DB write; None before
    trace_id: str | None             # Langfuse trace id; None if tracing failed

    # ── Execution control ─────────────────────────────────────────────────────
    status: str          # "running" | "complete" | "failed"
    error: str | None    # human-readable failure reason; None on success
