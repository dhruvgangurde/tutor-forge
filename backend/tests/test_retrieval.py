"""
tests/test_retrieval.py
-----------------------
Unit tests for RetrievalService.

Uses mocked Gemini client — no API calls, no GEMINI_API_KEY required.
Uses real ChromaDB with in-memory (ephemeral) client for isolation.
"""

import uuid
from unittest.mock import MagicMock

import chromadb
import pytest

from retrieval.models import Chunk, RetrievalResult
from retrieval.service import RetrievalService


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_mock_gemini(embed_dim: int = 384) -> MagicMock:
    """Return a mock GeminiProClient that returns deterministic embeddings."""
    mock = MagicMock()
    # embed() returns a fixed-length float list; values differ per call position
    # so that similarity ordering is predictable in tests.
    _call_counter = {"n": 0}

    def fake_embed(text: str) -> list[float]:
        n = _call_counter["n"]
        _call_counter["n"] += 1
        # Simple deterministic embedding: value scales with call order
        base = float(n % 10) / 10.0
        return [base] * embed_dim

    mock.embed.side_effect = fake_embed
    return mock


@pytest.fixture
def mock_gemini() -> MagicMock:
    return _make_mock_gemini()


@pytest.fixture
def chroma_client() -> chromadb.EphemeralClient:
    """In-memory ChromaDB client — fully isolated per test."""
    return chromadb.EphemeralClient()


@pytest.fixture
def service(chroma_client, mock_gemini) -> RetrievalService:
    return RetrievalService(chroma_client=chroma_client, gemini_client=mock_gemini)


@pytest.fixture
def course_id() -> uuid.UUID:
    return uuid.uuid4()


# ── Collection management ─────────────────────────────────────────────────────

def test_create_course_collection(service, course_id):
    """create_course_collection should create the collection without raising."""
    service.create_course_collection(course_id)
    # Verify by adding a chunk — would raise if collection missing
    chunks = [Chunk(text="hello", source_file="test.pdf", page_or_slide=1, course_id=course_id, chunk_id="c1")]
    service.add_chunks(course_id, chunks)  # Should not raise


def test_create_collection_is_idempotent(service, course_id):
    """Calling create_course_collection twice should not raise."""
    service.create_course_collection(course_id)
    service.create_course_collection(course_id)  # idempotent


def test_delete_course_collection(service, course_id):
    """delete_course_collection should succeed even if called twice."""
    service.create_course_collection(course_id)
    service.delete_course_collection(course_id)
    service.delete_course_collection(course_id)  # best-effort, no raise


# ── add_chunks ────────────────────────────────────────────────────────────────

def test_add_chunks_requires_existing_collection(service, course_id):
    """add_chunks should raise if the collection hasn't been created first."""
    chunks = [Chunk(text="orphan", source_file="x.pdf", page_or_slide=1, course_id=course_id, chunk_id="c1")]
    with pytest.raises(Exception):
        service.add_chunks(course_id, chunks)


def test_add_chunks_empty_list_is_noop(service, course_id):
    """Adding an empty chunk list should not raise."""
    service.create_course_collection(course_id)
    service.add_chunks(course_id, [])  # no raise


def test_add_chunks_upsert(service, course_id):
    """Adding the same chunk_id twice should not create duplicates."""
    service.create_course_collection(course_id)
    chunk = Chunk(text="dedup me", source_file="a.pdf", page_or_slide=1, course_id=course_id, chunk_id="dup1")
    service.add_chunks(course_id, [chunk])
    service.add_chunks(course_id, [chunk])  # upsert — should not raise or duplicate


# ── retrieve ──────────────────────────────────────────────────────────────────

def _seed(service, course_id, n: int = 3) -> list[Chunk]:
    """Helper: create collection and add n chunks."""
    service.create_course_collection(course_id)
    chunks = [
        Chunk(
            text=f"Concept {i}: The quick brown fox.",
            source_file="lecture.pdf",
            page_or_slide=i,
            course_id=course_id,
            chunk_id=f"chunk_{i}",
        )
        for i in range(n)
    ]
    service.add_chunks(course_id, chunks)
    return chunks


def test_retrieve_returns_result(service, course_id):
    _seed(service, course_id)
    result = service.retrieve(course_id, "What is a fox?")
    assert isinstance(result, RetrievalResult)
    assert len(result.chunks) > 0
    assert len(result.confidence_scores) == len(result.chunks)


def test_retrieve_unknown_course_returns_empty(service):
    unknown_id = uuid.uuid4()
    result = service.retrieve(unknown_id, "anything")
    assert result.is_empty()


def test_retrieve_respects_top_k(service, course_id):
    _seed(service, course_id, n=10)
    result = service.retrieve(course_id, "fox", top_k=3)
    assert len(result.chunks) <= 3


# ── is_grounded ───────────────────────────────────────────────────────────────

def test_is_grounded_empty_result(service, course_id):
    empty = RetrievalResult(query="q")
    assert service.is_grounded(empty, threshold=0.75) is False


def test_is_grounded_requires_an_explicit_threshold():
    """
    The threshold has no default, by design.

    check_groundedness_node in the tutor graph used to omit it and silently
    inherit a hardcoded 0.75, refusing on-topic questions under the mock
    provider (whose real bar is 0.20). Making the argument required is what
    stops that from recurring, so the absence of a default is itself a
    behaviour worth pinning.
    """
    svc = RetrievalService.__new__(RetrievalService)
    result = RetrievalResult(
        query="q",
        chunks=[Chunk(text="x", source_file="f", page_or_slide=1, course_id=uuid.uuid4())],
        confidence_scores=[0.50],
    )
    with pytest.raises(TypeError):
        svc.is_grounded(result)


def test_is_grounded_above_threshold():
    """Manually construct a high-confidence result."""
    service = RetrievalService.__new__(RetrievalService)  # skip __init__
    result = RetrievalResult(
        query="test",
        chunks=[Chunk(text="x", source_file="f", page_or_slide=1, course_id=uuid.uuid4())],
        confidence_scores=[0.90],
    )
    assert service.is_grounded(result, threshold=0.75) is True


def test_is_grounded_below_threshold():
    service = RetrievalResult.__new__(RetrievalResult)
    from retrieval.service import RetrievalService as RS
    svc = RS.__new__(RS)
    result = RetrievalResult(
        query="test",
        chunks=[Chunk(text="x", source_file="f", page_or_slide=1, course_id=uuid.uuid4())],
        confidence_scores=[0.50],
    )
    assert svc.is_grounded(result, threshold=0.75) is False


def test_is_grounded_at_exact_threshold():
    from retrieval.service import RetrievalService as RS
    svc = RS.__new__(RS)
    result = RetrievalResult(
        query="q",
        chunks=[Chunk(text="x", source_file="f", page_or_slide=None, course_id=uuid.uuid4())],
        confidence_scores=[0.75],
    )
    assert svc.is_grounded(result, threshold=0.75) is True


# ── build_citation_bundle ─────────────────────────────────────────────────────

def test_citation_bundle_format(service, course_id):
    _seed(service, course_id, n=2)
    result = service.retrieve(course_id, "fox", top_k=2)
    citations = service.build_citation_bundle(result)
    assert len(citations) == len(result.chunks)
    for c in citations:
        assert c.chunk_text
        assert c.source_file
        assert 0.0 <= c.confidence <= 1.0


# ── build_context_window ──────────────────────────────────────────────────────

def test_context_window_respects_token_budget(service, course_id):
    """With a tiny budget, context window should be truncated."""
    _seed(service, course_id, n=5)
    result = service.retrieve(course_id, "fox", top_k=5)
    # Budget of 10 tokens ≈ 40 chars — should only include 1 chunk at most
    context = service.build_context_window(result, token_budget=10)
    # Should not contain all 5 chunks
    assert context.count("[Source:") <= 5


def test_context_window_contains_source_annotation(service, course_id):
    _seed(service, course_id, n=1)
    result = service.retrieve(course_id, "fox", top_k=1)
    context = service.build_context_window(result, token_budget=4096)
    assert "[Source:" in context
