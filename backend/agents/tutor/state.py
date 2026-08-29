"""
agents/tutor/state.py — LangGraph state for the Socratic tutor agent.
"""

from typing import Any, TypedDict
from uuid import UUID


class TutorState(TypedDict):
    session_id: UUID
    course_id: UUID
    student_id: UUID
    question: str
    prior_question: str | None                 # last substantive student turn, if any
    resolved_question: str | None              # what retrieval actually ran on
    retrieval_query_source: str                # "message" | "prior_turn"
    pedagogy_skip_requested: bool              # FR-03.3: asked to be handed the answer
    retrieval_result: dict[str, Any] | None   # serialized RetrievalResult
    is_grounded: bool
    hint_level: int                            # 0 = opening turn, 4 = full explanation
    response: str
    citations: list[dict]                      # list of Citation dicts
    pedagogy_trace: dict[str, Any]             # Langfuse trace metadata
