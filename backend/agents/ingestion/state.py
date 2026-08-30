"""
agents/ingestion/state.py
-------------------------
LangGraph state for the course ingestion agent.
"""

from typing import Any, TypedDict
from uuid import UUID


class IngestionState(TypedDict):
    job_id: UUID
    course_id: UUID
    files: list[dict[str, Any]]   # [{"filename": str, "content": bytes, "mime_type": str}]
    parsed_content: list[dict]    # output of parse_content_node
    hierarchy: dict               # course→chapters→concepts JSON from Gemini
    chunks: list[dict]            # flattened chunks ready for embedding
    status: str                   # "pending" | "running" | "complete" | "failed"
    error: str | None
    trace_id: str | None          # Langfuse trace id; None if tracing failed
