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

import logging
import uuid
from typing import TYPE_CHECKING

import chromadb

from core.config import settings
from retrieval.models import Chunk, Citation, RetrievalResult

if TYPE_CHECKING:
    from main import GeminiProClient

_COLLECTION_PREFIX = "course_"

_log = logging.getLogger(__name__)


def _collection_name(course_id: uuid.UUID) -> str:
    """Deterministic Chroma collection name for a course."""
    return f"{_COLLECTION_PREFIX}{str(course_id).replace('-', '_')}"


class EmbeddingMismatch(RuntimeError):
    """
    A course collection was embedded with a different provider, model or vector
    size than the one now configured.

    Vectors from different embedding models are not comparable even when their
    sizes match, so querying such a collection would return meaningless matches
    or, when the sizes differ, a Chroma error. This says what differs instead.
    The fix is to upload the course again under the current configuration.
    """


#: Legacy collections (no embedding metadata) already warned about, so the log
#: says it once per collection per process rather than on every question.
_warned_legacy: set[str] = set()


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
                # Which embedding made these vectors, so a later query with a
                # different one fails clearly instead of matching nonsense.
                **self._embedding_identity(),
            },
        )

    # ── Embedding identity ────────────────────────────────────────────────────

    def _embedding_identity(self) -> dict[str, str | int]:
        """
        Provider, embedding model and vector size of the configured embedding
        client, as collection metadata. Empty when the client does not say
        (a test double), which is then treated like a legacy collection.
        """
        model = getattr(self._gemini, "embedding_model_name", None)
        if not isinstance(model, str) or not model:
            return {}
        identity: dict[str, str | int] = {
            "embedding_provider": settings.llm_provider,
            "embedding_model": model,
        }
        dimensions = getattr(self._gemini, "embedding_dimensions", None)
        if isinstance(dimensions, int) and dimensions > 0:
            identity["embedding_dim"] = dimensions
        return identity

    def _check_embedding_identity(self, collection, name: str) -> dict:
        """
        Fail with EmbeddingMismatch when the collection was embedded by another
        provider or model. Returns the collection's metadata.
        """
        meta = collection.metadata or {}
        current = self._embedding_identity()
        if "embedding_model" in meta and current:
            stored = (meta.get("embedding_provider"), meta.get("embedding_model"))
            active = (current["embedding_provider"], current["embedding_model"])
            if stored != active:
                raise EmbeddingMismatch(
                    f"Collection {name} was embedded with {stored[0]}/{stored[1]} but the "
                    f"configured embedding is {active[0]}/{active[1]}. Upload the course "
                    "again to re-index it."
                )
        return meta

    @staticmethod
    def _stored_vector_size(collection) -> int | None:
        """The size of one stored vector, or None if the collection is empty."""
        peek = collection.get(limit=1, include=["embeddings"])
        vectors = peek.get("embeddings")
        if vectors is None or len(vectors) == 0:
            return None
        return len(vectors[0])

    def _check_vector_size(self, collection, meta: dict, name: str, size: int) -> None:
        """
        Fail with EmbeddingMismatch when ``size`` differs from the collection's
        vectors. Legacy collections (no embedding metadata) are checked against
        one stored vector and logged, never refused for that alone.
        """
        expected = meta.get("embedding_dim")
        if not isinstance(expected, int) or expected <= 0:
            expected = self._stored_vector_size(collection)
            if "embedding_model" not in meta and name not in _warned_legacy:
                _warned_legacy.add(name)
                _log.warning(
                    "[EMBEDDING-LEGACY] collection=%s has no embedding metadata; checked "
                    "the size of one stored vector only (%s). Upload the course again to "
                    "record its embedding.",
                    name,
                    expected,
                )
        if expected is not None and size != expected:
            raise EmbeddingMismatch(
                f"Collection {name} holds {expected}-dimension vectors but the configured "
                f"embedding produced {size}. Check EMBEDDING_DIMENSIONS, or upload the "
                "course again to re-index it."
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
        meta = self._check_embedding_identity(collection, collection_name)

        documents = [c.text for c in chunks]
        logger.info(
            "[ADD-CHUNKS-EMBEDDING] course_id=%s chunks=%d embedding_model=%s",
            course_id,
            len(documents),
            settings.embedding_model,
        )

        # One request per batch, not per chunk: a 165-chunk upload used to
        # make 165 calls and trip the provider's per-minute limit.
        embeddings = self._gemini.embed_documents(documents)
        if embeddings:
            logger.info(
                "[ADD-CHUNKS-EMBEDDING-COMPLETE] course_id=%s chunks=%d embedding_dim=%d",
                course_id,
                len(embeddings),
                len(embeddings[0]),
            )
            self._check_vector_size(collection, meta, collection_name, len(embeddings[0]))

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

        # Counts and scores only: the query is what the student typed, and the
        # chunks are course text, so neither belongs in the log.
        logger.info(
            "[RETRIEVE-START] course_id=%s collection_name=%s query_chars=%d top_k=%d",
            course_id,
            collection_name,
            len(query),
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

        # Checked before the query is embedded, so a mismatch costs no API call.
        meta = self._check_embedding_identity(collection, collection_name)

        # Embed query
        query_embedding = self._gemini.embed_query(query)
        self._check_vector_size(collection, meta, collection_name, len(query_embedding))
        logger.info(
            "[RETRIEVE-QUERY-EMBEDDED] course_id=%s embedding_dim=%d",
            course_id,
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
            "[RETRIEVE-RESPONSE] course_id=%s results=%d",
            course_id,
            len(docs),
        )

        for idx, (doc, meta, dist) in enumerate(zip(docs, metas, distances), 1):
            confidence = max(0.0, 1.0 - dist)
            source_file = meta.get("source_file", "")
            page_or_slide = meta.get("page_or_slide")

            logger.info(
                "[RETRIEVE-RESULT-%d] rank=%d distance=%.4f confidence=%.4f",
                idx,
                idx,
                dist,
                confidence,
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
            "[RETRIEVE-COMPLETE] course_id=%s top_score=%.4f chunks=%d",
            course_id,
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
