"""
agents/tutor/graph.py - Socratic tutor LangGraph graph.

Conditional edges after check_groundedness_node:

  chat mode (a message the student typed) routes three ways:
    not grounded                      -> refuse_node               (FR-02.7)
    grounded + pedagogy-skip request  -> decline_direct_answer_node (FR-03.3)
    grounded                          -> generate_guiding_question

  hint mode (the hint button) routes two ways: the pedagogy-skip branch is
  intentionally not wired, because clicking the hint button IS engaging with
  the ladder. See _hint_router.

Neither refusal path makes an LLM call.
"""

from functools import partial
from typing import Literal

from langgraph.graph import END, StateGraph

from agents.tutor.nodes import (
    advance_hint_node,
    check_groundedness_node,
    decline_direct_answer_node,
    emit_pedagogy_trace_node,
    generate_guiding_question_node,
    refuse_node,
    retrieve_context_node,
)
from agents.tutor.state import TutorState


def _chat_router(state: TutorState) -> Literal["grounded", "refused", "declined"]:
    """
    Three-way routing for a typed chat turn.

    Order matters: groundedness is checked FIRST, so a student asking for the
    answer to something genuinely outside the corpus still gets the out-of-corpus
    refusal rather than a decline implying the topic is covered.
    """
    if not state.get("is_grounded"):
        return "refused"
    if state.get("pedagogy_skip_requested"):
        return "declined"
    return "grounded"


def _hint_router(state: TutorState) -> Literal["grounded", "refused"]:
    """
    Two-way routing for a hint-button request.

    The pedagogy-skip branch is deliberately absent here. Clicking "hint" IS
    using the ladder, so it must never be declined -- even though the question
    carried over from the last student message may itself read as a request for
    the answer ("give me the answer" -> hint click). Declining there would make
    the button that exists to help a student dead-end on the very message
    telling them to use it.
    """
    return "grounded" if state.get("is_grounded") else "refused"


def build_tutor_graph(
    retrieval_service,
    gemini_pro,
    langfuse,
    mode: str = "chat",  # "chat" | "hint"
):
    """
    Build and compile the tutor graph.

    mode="hint" replaces generate_guiding_question_node with advance_hint_node
    so hint requests share the same groundedness gate.
    """
    builder = StateGraph(TutorState)

    builder.add_node("retrieve_context", partial(retrieve_context_node, retrieval_service=retrieval_service))
    builder.add_node("check_groundedness", partial(check_groundedness_node, retrieval_service=retrieval_service))
    builder.add_node("refuse", refuse_node)
    builder.add_node(
        "decline_direct_answer",
        partial(decline_direct_answer_node, retrieval_service=retrieval_service),
    )

    if mode == "hint":
        builder.add_node(
            "generate_response",
            partial(advance_hint_node, retrieval_service=retrieval_service, gemini_pro=gemini_pro),
        )
    else:
        builder.add_node(
            "generate_response",
            partial(generate_guiding_question_node, retrieval_service=retrieval_service, gemini_pro=gemini_pro),
        )

    builder.add_node("emit_trace", partial(emit_pedagogy_trace_node, langfuse=langfuse))

    builder.set_entry_point("retrieve_context")
    builder.add_edge("retrieve_context", "check_groundedness")
    if mode == "hint":
        builder.add_conditional_edges(
            "check_groundedness",
            _hint_router,
            {"grounded": "generate_response", "refused": "refuse"},
        )
    else:
        builder.add_conditional_edges(
            "check_groundedness",
            _chat_router,
            {
                "grounded": "generate_response",
                "refused": "refuse",
                "declined": "decline_direct_answer",
            },
        )
    builder.add_edge("generate_response", "emit_trace")
    builder.add_edge("refuse", "emit_trace")
    builder.add_edge("decline_direct_answer", "emit_trace")
    builder.add_edge("emit_trace", END)

    return builder.compile()
