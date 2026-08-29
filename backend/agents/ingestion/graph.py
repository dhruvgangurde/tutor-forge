"""
agents/ingestion/graph.py
--------------------------
Builds and returns the compiled LangGraph ingestion graph.

Node execution sequence:
    validate → parse → build_hierarchy → create_course_collection
             → chunk_and_embed → persist_to_db

create_course_collection runs before chunk_and_embed to guarantee the
ChromaDB collection exists before add_chunks() is called.

Error handling: any node can return state with status="failed".
The conditional edges route to END on failure so the graph terminates
without executing subsequent nodes.
"""

from functools import partial
from typing import Literal

from langgraph.graph import END, StateGraph

from agents.ingestion.nodes import (
    build_hierarchy_node,
    chunk_and_embed_node,
    create_course_collection_node,
    parse_content_node,
    persist_to_db_node,
    validate_files_node,
)
from agents.ingestion.state import IngestionState


def _failed_or_continue(state: IngestionState) -> Literal["continue", "end"]:
    """Route to END if the last node marked status=failed."""
    return "end" if state.get("status") == "failed" else "continue"


def build_ingestion_graph(
    retrieval_service,
    gemini_pro,
    db,
) -> "CompiledGraph":
    """
    Build and compile the ingestion graph with injected service dependencies.

    Args:
        retrieval_service: RetrievalService singleton from app.state
        gemini_pro: GeminiProClient singleton from app.state
        db: AsyncSession for the current background task invocation
    """
    builder = StateGraph(IngestionState)

    # Register nodes (services injected via functools.partial)
    builder.add_node("validate", validate_files_node)
    builder.add_node("parse", parse_content_node)
    builder.add_node(
        "build_hierarchy",
        partial(build_hierarchy_node, gemini_pro=gemini_pro),
    )
    builder.add_node(
        "create_course_collection",
        partial(create_course_collection_node, retrieval_service=retrieval_service),
    )
    builder.add_node(
        "chunk_and_embed",
        partial(chunk_and_embed_node, retrieval_service=retrieval_service),
    )
    builder.add_node(
        "persist_to_db",
        partial(persist_to_db_node, db=db),
    )

    # Entry point
    builder.set_entry_point("validate")

    # Edges with failure short-circuit
    builder.add_conditional_edges(
        "validate",
        _failed_or_continue,
        {"continue": "parse", "end": END},
    )
    builder.add_conditional_edges(
        "parse",
        _failed_or_continue,
        {"continue": "build_hierarchy", "end": END},
    )
    builder.add_conditional_edges(
        "build_hierarchy",
        _failed_or_continue,
        # create_course_collection BEFORE chunk_and_embed
        {"continue": "create_course_collection", "end": END},
    )
    builder.add_conditional_edges(
        "create_course_collection",
        _failed_or_continue,
        {"continue": "chunk_and_embed", "end": END},
    )
    builder.add_conditional_edges(
        "chunk_and_embed",
        _failed_or_continue,
        {"continue": "persist_to_db", "end": END},
    )
    builder.add_edge("persist_to_db", END)

    return builder.compile()
