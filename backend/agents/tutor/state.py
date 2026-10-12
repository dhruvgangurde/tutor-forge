"""
agents/tutor/state.py — LangGraph state for the Socratic tutor agent.
"""

from typing import Any, NotRequired, TypedDict
from uuid import UUID


class TutorState(TypedDict):
    session_id: UUID
    course_id: UUID
    student_id: UUID
    question: str
    prior_question: str | None                 # the open learning question, if any
    tutor_last_message: NotRequired[str | None]  # the tutor's last non-refusal turn
    resolved_question: str | None              # what retrieval actually ran on
    retrieval_query_source: str                # "message" | "prior_turn" (the open question)
    turn_kind: NotRequired[str]                # "new_question" | "follow_up" | "reply"
    is_non_answer: NotRequired[bool]           # "no", "I don't know": ask a simpler step
    definition_allowed: NotRequired[bool]      # pure "what is X?", not from graded work
    history: NotRequired[list[dict]]           # the turns before this one, oldest first:
                                               # {role, content, hint_level, is_refusal}
    pedagogy_skip_requested: bool              # FR-03.3: asked to be handed the answer
    retrieval_result: dict[str, Any] | None   # serialized RetrievalResult
    is_grounded: bool
    hint_level: int                            # 0 = opening turn, 4 = full explanation
    response: str
    citations: list[dict]                      # list of Citation dicts
    pedagogy_trace: dict[str, Any]             # Langfuse trace metadata
