"""
agents/assessment/graph.py
---------------------------
Builds and compiles the assessment generation LangGraph.

Node execution sequence:
    retrieve_concepts → check_groundedness
                              |
              ┌───────────────┴──────────────┐
           grounded                      not grounded
              │                               │
      generate_questions                 refuse_node → END
              │
    ┌─────────┴──────────┐
  ok (questions > 0)  failed or empty
        │                    │
  persist_assessment         END
        │
       END

Error routing rules:
  - retrieve_concepts sets status="failed" on ChromaDB error → generation_router → END
  - check_groundedness routes to refuse when is_grounded is False → refuse → END
  - generate_questions sets status="failed" on JSON/LLM error → generation_router → END
  - generate_questions produces 0 questions → generation_router → END (avoids empty persist)
  - persist_assessment always routes to END (success or failure handled internally)

IMPORTANT: build_assessment_graph() accepts a db session that is scoped to the
BackgroundTask — it must not be shared across requests. The session is bound
to persist_assessment_node via functools.partial and must remain open for the
duration of the graph run.
"""

from functools import partial
from typing import Literal

from langgraph.graph import END, StateGraph

from agents.assessment.nodes import (
    check_groundedness_node,
    generate_questions_node,
    persist_assessment_node,
    refuse_node,
    retrieve_concepts_node,
)
from agents.assessment.state import AssessmentState


# ── Routing functions ─────────────────────────────────────────────────────────

def _groundedness_router(state: AssessmentState) -> Literal["grounded", "refused"]:
    """Route to generate_questions if grounded, refuse otherwise."""
    return "grounded" if state.get("is_grounded") else "refused"


def _generation_router(state: AssessmentState) -> Literal["persist", "end"]:
    """
    Route after generate_questions_node.

    Routes to END (skipping persist) when:
      - status == "failed"  (LLM error, JSON parse error, or ChromaDB error)
      - questions list is empty (LLM returned 0 valid questions)
    """
    if state.get("status") == "failed":
        return "end"
    if not state.get("questions"):
        return "end"
    return "persist"


# ── Graph factory ─────────────────────────────────────────────────────────────

def build_assessment_graph(
    retrieval_service,
    gemini_flash,
    db,
    langfuse,
    assessment_id,
):
    """
    Build and compile the assessment generation graph with injected dependencies.

    Args:
        retrieval_service: RetrievalService singleton from app.state
        gemini_flash:      GeminiFlashClient singleton from app.state
        db:                AsyncSession scoped to the BackgroundTask — do NOT reuse
        langfuse:          Langfuse singleton from app.state
        assessment_id:     UUID of the placeholder Assessment row created by
                           service.create_assessment() — persist node will UPDATE it
    """
    builder = StateGraph(AssessmentState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node(
        "retrieve_concepts",
        partial(retrieve_concepts_node, retrieval_service=retrieval_service),
    )
    builder.add_node(
        "check_groundedness",
        partial(check_groundedness_node, retrieval_service=retrieval_service),
    )
    builder.add_node("refuse", refuse_node)
    builder.add_node(
        "generate_questions",
        partial(
            generate_questions_node,
            retrieval_service=retrieval_service,
            gemini_flash=gemini_flash,
        ),
    )
    builder.add_node(
        "persist_assessment",
        partial(persist_assessment_node, db=db, langfuse=langfuse),
    )

    # ── Entry point ───────────────────────────────────────────────────────────
    builder.set_entry_point("retrieve_concepts")

    # ── Edges ─────────────────────────────────────────────────────────────────
    builder.add_edge("retrieve_concepts", "check_groundedness")

    builder.add_conditional_edges(
        "check_groundedness",
        _groundedness_router,
        {"grounded": "generate_questions", "refused": "refuse"},
    )

    builder.add_edge("refuse", END)

    builder.add_conditional_edges(
        "generate_questions",
        _generation_router,
        {"persist": "persist_assessment", "end": END},
    )

    builder.add_edge("persist_assessment", END)

    return builder.compile()
