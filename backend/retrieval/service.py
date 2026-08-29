"""
retrieval/service.py
--------------------
RetrievalService — the central grounding engine for all LangGraph agents.

Lifecycle:
  - Created once in FastAPI lifespan (main.py) and stored on app.state.
  - On startup, PersistentClient connects to the configured persist directory.
    All existing course collections are automatically loaded and ready for
    retrieval — no re-ingestion is required after a server restart.
  - New collections are created only during course ingestion, via
    create_course_collection().

Every agent that calls retrieve() MUST subsequently call is_grounded() and
refuse to generate an LLM response if it returns False. This is the hard
constraint that prevents hallucination (AC-02: 0 out-of-corpus leakage).
"""

import uuid
from typing import TYPE_CHECKING

import chromadb

from core.config import settings
from retrieval.models import Chunk, Citation, RetrievalResult

if TYPE_CHECKING:
    from main import GeminiProClient

_COLLECTION_PREFIX = "course_"


def _collection_name(course_id: uuid.UUID) -> str:
    """Deterministic Chroma collection name for a course."""
    return f"{_COLLECTION_PREFIX}{str(course_id).replace('-', '_')}"


class RetrievalService:
    """
    Wraps ChromaDB + Gemini embeddings with a domain-specific interface.

    All public methods are consumed by agent graphs. The service is a
    singleton: one instance per server process, shared across all requests.
    """

    def __init__(
        self,
        chroma_client: chromadb.PersistentClient,
        gemini_client: "GeminiProClient",
    ) -> None:
        self._chroma = chroma_client
        self._gemini = gemini_client

    # ── Collection management ─────────────────────────────────────────────────

    def create_course_collection(self, course_id: uuid.UUID) -> None:
        """
        Create a ChromaDB collection for a course.

        Called by create_course_collection_node in the ingestion graph,
        BEFORE chunk_and_embed_node — guaranteeing the collection exists
        when add_chunks() is called.

        Idempotent: get_or_create_collection will reuse an existing collection.

        NOTE: hnsw:space is set to "cosine" explicitly. ChromaDB defaults to
        squared-L2 when unset, which is incompatible with retrieve()'s
        `confidence = 1.0 - distance` formula (only valid for cosine distance).
        This is fixed at creation time and cannot be changed retroactively —
        collections created before this was set must be deleted and re-ingested.
        """
        name = _collection_name(course_id)
        self._chroma.get_or_create_collection(
            name=name,
            metadata={
                "course_id": str(course_id),
                "hnsw:space": "cosine",
            },
        )

    def delete_course_collection(self, course_id: uuid.UUID) -> None:
        """Delete the ChromaDB collection for a course (e.g. on course removal)."""
        name = _collection_name(course_id)
        try:
            self._chroma.delete_collection(name)
        except Exception:
            pass  # Collection may not exist; deletion is best-effort

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def add_chunks(self, course_id: uuid.UUID, chunks: list[Chunk]) -> None:
        """
        Embed chunks via Gemini (settings.embedding_model) and upsert to ChromaDB.

        IMPORTANT: The course collection MUST already exist before this is called.
        In the ingestion graph, create_course_collection_node runs first.
        """
        import logging
        from core.config import settings
        logger = logging.getLogger(__name__)

        if not chunks:
            logger.warning("[ADD-CHUNKS] No chunks to add for course_id=%s", course_id)
            return

        collection_name = _collection_name(course_id)
        logger.info(
            "[ADD-CHUNKS-START] course_id=%s collection_name=%s chunks=%d embedding_model=%s",
            course_id,
            collection_name,
            len(chunks),
            settings.embedding_model,
        )

        collection = self._chroma.get_collection(collection_name)

        documents = [c.text for c in chunks]
        logger.info(
            "[ADD-CHUNKS-EMBEDDING] course_id=%s chunks=%d embedding_model=%s",
            course_id,
            len(documents),
            settings.embedding_model,
        )

        embeddings = [self._gemini.embed(doc) for doc in documents]
        if embeddings:
            logger.info(
                "[ADD-CHUNKS-EMBEDDING-COMPLETE] course_id=%s chunks=%d embedding_dim=%d",
                course_id,
                len(embeddings),
                len(embeddings[0]),
            )

        ids = [c.chunk_id or str(uuid.uuid4()) for c in chunks]
        metadatas = [
            {
                "source_file": c.source_file,
                "page_or_slide": c.page_or_slide if c.page_or_slide is not None else -1,
                "course_id": str(c.course_id),
            }
            for c in chunks
        ]

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        logger.info(
            "[ADD-CHUNKS-COMPLETE] course_id=%s collection_name=%s chunks_added=%d",
            course_id,
            collection_name,
            len(ids),
        )

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def retrieve(
        self,
        course_id: uuid.UUID,
        query: str,
        top_k: int = 5,
    ) -> RetrievalResult:
        """
        Semantic search over the course collection.
        Returns a RetrievalResult with ranked chunks and confidence scores.

        Confidence scores are derived from Chroma's cosine distances:
            confidence = 1.0 - distance  (range 0.0–1.0, higher is better)
        """
        import logging
        logger = logging.getLogger(__name__)

        result = RetrievalResult(query=query)
        collection_name = _collection_name(course_id)

        logger.info(
            "[RETRIEVE-START] course_id=%s collection_name=%s query=%r top_k=%d",
            course_id,
            collection_name,
            query,
            top_k,
        )

        try:
            collection = self._chroma.get_collection(collection_name)
            logger.info(
                "[RETRIEVE-COLLECTION-FOUND] course_id=%s collection_name=%s",
                course_id,
                collection_name,
            )
        except Exception as e:
            logger.error(
                "[RETRIEVE-COLLECTION-NOT-FOUND] course_id=%s collection_name=%s error=%s",
                course_id,
                collection_name,
                e,
            )
            # Collection doesn't exist — return empty result; is_grounded() will be False
            return result

        # Embed query
        query_embedding = self._gemini.embed(query)
        logger.info(
            "[RETRIEVE-QUERY-EMBEDDED] course_id=%s query=%r embedding_dim=%d",
            course_id,
            query,
            len(query_embedding),
        )

        # Query Chroma
        response = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        docs = response.get("documents", [[]])[0]
        metas = response.get("metadatas", [[]])[0]
        distances = response.get("distances", [[]])[0]

        logger.info(
            "[RETRIEVE-RESPONSE] course_id=%s query=%r results=%d",
            course_id,
            query,
            len(docs),
        )

        for idx, (doc, meta, dist) in enumerate(zip(docs, metas, distances), 1):
            confidence = max(0.0, 1.0 - dist)
            source_file = meta.get("source_file", "")
            page_or_slide = meta.get("page_or_slide")

            logger.info(
                "[RETRIEVE-RESULT-%d] rank=%d distance=%.4f confidence=%.4f "
                "source=%s page=%s text_preview=%r",
                idx,
                idx,
                dist,
                confidence,
                source_file,
                page_or_slide,
                doc[:150].replace("\n", " "),
            )

            chunk = Chunk(
                text=doc,
                source_file=source_file,
                page_or_slide=page_or_slide or None,
                course_id=course_id,
            )
            result.chunks.append(chunk)
            result.confidence_scores.append(confidence)

        logger.info(
            "[RETRIEVE-COMPLETE] course_id=%s query=%r top_score=%.4f chunks=%d",
            course_id,
            query,
            result.top_score if not result.is_empty() else 0.0,
            len(result.chunks),
        )

        return result

    # ── Groundedness gate ─────────────────────────────────────────────────────

    def is_grounded(
        self,
        result: RetrievalResult,
        *,
        threshold: float,
    ) -> bool:
        """
        Return True only if the top retrieval score meets the threshold.

        This is the hard constraint enforced by every agent graph:
        - If False → agent routes to refuse_node (no LLM call made)
        - If True  → agent proceeds to generate a grounded response

        ``threshold`` is REQUIRED and keyword-only, deliberately. It used to
        default to 0.75, and check_groundedness_node in the tutor graph simply
        never passed one — so the tutor silently graded every question against
        a hardcoded Gemini-era number while the mock provider's real bar was
        0.20, wrongfully refusing on-topic questions. A default here is an
        invitation to that bug, because the correct value is provider-specific
        and only settings.active_groundedness_threshold knows it. Callers must
        state the bar they mean.
        """
        if result.is_empty():
            return False
        return result.top_score >= threshold

    # ── Citation assembly ─────────────────────────────────────────────────────

    def build_citation_bundle(self, result: RetrievalResult) -> list[Citation]:
        """
        Assemble a list of Citation objects from a RetrievalResult.
        Each Citation contains the chunk text, source file, location, and
        confidence score for display in the frontend CitationPanel.
        """
        citations = []
        for chunk, score in zip(result.chunks, result.confidence_scores):
            citations.append(Citation(
                chunk_text=chunk.text,
                source_file=chunk.source_file,
                page_or_slide=chunk.page_or_slide,
                confidence=round(score, 4),
            ))
        return citations

    def build_context_window(
        self,
        result: RetrievalResult,
        token_budget: int = 4096,
    ) -> str:
        """
        Format citation-annotated chunks into a context string for LLM input.

        Chunks are added in descending confidence order until the token budget
        is exceeded.

        TOKEN ESTIMATION RATIONALE
        --------------------------
        We use the approximation: 1 token ≈ 4 characters (English text).
        This is the same heuristic used by OpenAI's official cookbook and is
        well-established for Latin-script text.

        Why not use a real tokenizer (e.g. tiktoken, sentencepiece)?
          1. Gemini uses a proprietary SentencePiece vocabulary. Google does not
             publish a standalone tokenizer for it, so any third-party tokenizer
             would introduce calibration drift anyway.
          2. The Gemini Pro / Flash context windows are 32k–128k tokens.
             At those scales, a ±15% estimation error (worst case for the 4-char
             heuristic on mixed-language text) shifts the effective budget by
             ≤ 600 tokens — well within safe margins.
          3. The budget here is 4 096 tokens (~16 KB of text), a small fraction
             of the available context window. Even if we overestimate by 20%,
             we still stay comfortably within the model limit.
          4. Adding tiktoken or sentencepiece would be a non-trivial dependency
             for a correctness improvement that is negligible in practice.

        Decision: keep the 4-char heuristic. Revisit if we ever target a
        sub-8k context window or observe truncation errors in production logs.

        Output format per chunk:
            [Source: {file}, p.{page}]
            {text}
        """
        char_budget = token_budget * 4
        parts: list[str] = []
        used = 0

        pairs = sorted(
            zip(result.chunks, result.confidence_scores),
            key=lambda x: x[1],
            reverse=True,
        )

        for chunk, _score in pairs:
            page_ref = f", p.{chunk.page_or_slide}" if chunk.page_or_slide else ""
            block = f"[Source: {chunk.source_file}{page_ref}]\n{chunk.text}"
            if used + len(block) > char_budget:
                break
            parts.append(block)
            used += len(block)

        return "\n\n".join(parts)
