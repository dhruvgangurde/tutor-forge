"""agents/assessment/state.py — Assessment generation agent state."""

from typing import Any, TypedDict
from uuid import UUID


class AssessmentState(TypedDict):
    # ── Inputs (provided by the caller) ──────────────────────────────────────
    course_id: UUID
    teacher_id: UUID
    config: dict[str, Any]              # topic, bloom_mix, type_mix, difficulty, count, title

    # ── Intermediate state (written by nodes) ─────────────────────────────────
    retrieved_concepts: list[dict]      # human-readable chunks with confidence scores
    retrieval_cache: dict               # serialised RetrievalResult for service helper reuse
                                        # keys: query, chunks (list[dict]), confidence_scores,
                                        #        retrieval_ms
    is_grounded: bool                   # set by check_groundedness_node

    # ── Generated outputs (parallel arrays — index i corresponds to question i) ──
    questions: list[dict]               # normalised question dicts
    bloom_tags: list[str]               # Bloom level per question
    distractors: list[list[str]]        # MCQ options per question (empty for non-MCQ)
    rubric_criteria: list[list[dict]]   # rubric criteria per question (empty for MCQ/numeric)
    answer_key: list[dict]              # worked solutions per question

    # ── Persistence outputs ───────────────────────────────────────────────────
    assessment_id: UUID                 # the placeholder ID passed in; confirmed after persist
    trace_id: str | None                # Langfuse trace id (None if tracing failed or skipped)

    # ── Execution control ─────────────────────────────────────────────────────
    status: str                         # "running" | "complete" | "failed"
    error: str | None                   # human-readable failure reason; None on success
