"""
retrieval/models.py
-------------------
Internal data models for the RetrievalService.
These are plain dataclasses — not ORM models.
"""

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class Chunk:
    """A single retrievable text unit with provenance metadata."""
    text: str
    source_file: str
    page_or_slide: int | None
    course_id: UUID
    chunk_id: str = ""        # Chroma document ID; set before upsert


@dataclass
class Citation:
    """A human-readable citation assembled from a retrieved Chunk."""
    chunk_text: str
    source_file: str
    page_or_slide: int | None
    confidence: float


@dataclass
class RetrievalResult:
    """Output of a single retrieval query."""
    query: str
    chunks: list[Chunk] = field(default_factory=list)
    confidence_scores: list[float] = field(default_factory=list)

    @property
    def top_score(self) -> float:
        """Return the highest confidence score, or 0.0 if no results."""
        return max(self.confidence_scores, default=0.0)

    def is_empty(self) -> bool:
        return len(self.chunks) == 0
