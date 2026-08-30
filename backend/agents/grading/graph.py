"""
agents/grading/graph.py
------------------------
Builds and compiles the Grading Agent LangGraph.

Node execution sequence:

    load_submission
          ↓
    retrieve_evidence
          ↓
    check_groundedness
          ↓
    grade_responses ──[failure or no responses]──→ END
          ↓ (ok)
    persist_recommendation
          ↓
         END

Routing rules:
  - load_submission:     if status == "failed" → END (DB error, missing submission)
  - grade_responses:     always continues to persist (partial grades are still written)
  - persist_recommendation: always → END

IMPORTANT: build_grading_graph() accepts a db session scoped to the BackgroundTask.
           It must not be shared across requests.
"""

from functools import partial
from typing import Literal

from langgraph.graph import END, StateGraph

from agents.grading.nodes import (
    check_evidence_groundedness_node,
    grade_responses_node,
    load_submission_node,
    persist_recommendation_node,
    retrieve_evidence_node,
)
from agents.grading.state import GradingState


# ── Routing functions ─────────────────────────────────────────────────────────

def _after_load_router(state: GradingState) -> Literal["retrieve", "end"]:
    """
    If load_submission_node failed (missing submission, no responses),
    skip all subsequent nodes and go directly to END.
    The caller (service._run_grading_graph) will mark the Submission as failed.
    """
    return "end" if state.get("status") == "failed" else "retrieve"


def _after_grade_router(state: GradingState) -> Literal["persist", "end"]:
    """
    If grade_responses_node set status="failed" (should not happen — it uses
    partial grading), skip persist to avoid writing a corrupt recommendation.
    In normal operation this always routes to "persist".
    """
    return "end" if state.get("status") == "failed" else "persist"


# ── Graph factory ─────────────────────────────────────────────────────────────

def build_grading_graph(
    retrieval_service,
    gemini_pro,
    db,
    langfuse,
):
    """
    Build and compile the grading graph with injected dependencies.

    Args:
        retrieval_service:  RetrievalService singleton from app.state
        gemini_pro:         GeminiProClient singleton — provides generate_deterministic()
        db:                 AsyncSession scoped to the BackgroundTask — do NOT reuse
        langfuse:           Langfuse singleton from app.state
    """
    builder = StateGraph(GradingState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node(
        "load_submission",
        partial(load_submission_node, db=db),
    )
    builder.add_node(
        "retrieve_evidence",
        partial(retrieve_evidence_node, retrieval_service=retrieval_service),
    )
    builder.add_node(
        "check_groundedness",
        partial(check_evidence_groundedness_node, retrieval_service=retrieval_service),
    )
    builder.add_node(
        "grade_responses",
        partial(grade_responses_node, gemini_pro=gemini_pro),
    )
    builder.add_node(
        "persist_recommendation",
        partial(persist_recommendation_node, db=db, langfuse=langfuse),
    )

    # ── Entry point ───────────────────────────────────────────────────────────
    builder.set_entry_point("load_submission")

    # ── Edges ─────────────────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "load_submission",
        _after_load_router,
        {"retrieve": "retrieve_evidence", "end": END},
    )

    builder.add_edge("retrieve_evidence", "check_groundedness")
    builder.add_edge("check_groundedness", "grade_responses")

    builder.add_conditional_edges(
        "grade_responses",
        _after_grade_router,
        {"persist": "persist_recommendation", "end": END},
    )

    builder.add_edge("persist_recommendation", END)

    return builder.compile()
