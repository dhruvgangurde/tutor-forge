"""
tests/test_embedding_identity.py
--------------------------------
One configured embedding size for indexing and querying, recorded on each new
course collection and checked before use. In-memory Chroma and fake embedding
clients only: no network.
"""

import logging
import uuid

import chromadb
import pytest

from core.config import Settings
from retrieval.models import Chunk
from retrieval.service import EmbeddingMismatch, RetrievalService


class _Embedder:
    """An embedding client that says which model and size it is."""

    def __init__(self, model="gemini-embedding-001", dimensions=768, size=None):
        self.embedding_model_name = model
        self.embedding_dimensions = dimensions
        self.size = size or dimensions or 768
        self.query_calls = 0

    def embed_documents(self, texts):
        return [[1.0] + [0.0] * (self.size - 1) for _ in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return [1.0] + [0.0] * (self.size - 1)


def _chunks(course_id, n=3):
    return [
        Chunk(text=f"chunk {i}", source_file="a.pdf", page_or_slide=1, course_id=course_id, chunk_id=f"c{i}")
        for i in range(n)
    ]


@pytest.fixture
def chroma():
    return chromadb.EphemeralClient()


def _indexed(chroma, embedder):
    service = RetrievalService(chroma_client=chroma, gemini_client=embedder)
    course_id = uuid.uuid4()
    service.create_course_collection(course_id)
    service.add_chunks(course_id, _chunks(course_id))
    return course_id


# ── Configuration ─────────────────────────────────────────────────────────────


def test_defaults_are_gemini_embedding_001_at_768():
    assert Settings.model_fields["embedding_model"].default == "gemini-embedding-001"
    assert Settings.model_fields["embedding_dimensions"].default == 768


def test_gemini_sends_the_configured_size_for_chunks_queries_and_single_texts(monkeypatch):
    import google.generativeai as genai

    from core.config import settings
    from main import GeminiProClient

    calls = []

    def fake_embed_content(**kwargs):
        calls.append(kwargs)
        content = kwargs["content"]
        n = len(content) if isinstance(content, list) else 1
        vectors = [[0.0] * 768 for _ in range(n)]
        return {"embedding": vectors if isinstance(content, list) else vectors[0]}

    monkeypatch.setattr(genai, "embed_content", fake_embed_content)
    monkeypatch.setattr(settings, "embedding_dimensions", 768)
    client = GeminiProClient(api_key="test-key", model="gemini-flash-lite-latest")

    client.embed_documents(["a", "b"])
    client.embed_query("q")
    client.embed("single")

    assert [c["output_dimensionality"] for c in calls] == [768, 768, 768]
    assert [c["task_type"] for c in calls] == ["retrieval_document", "retrieval_query", "retrieval_document"]
    assert client.embedding_dimensions == 768


# ── New collections record their embedding ────────────────────────────────────


def test_new_collection_records_provider_model_and_size(chroma):
    from core.config import settings

    course_id = _indexed(chroma, _Embedder())
    meta = chroma.get_collection(f"course_{str(course_id).replace('-', '_')}").metadata
    assert meta["embedding_provider"] == settings.llm_provider
    assert meta["embedding_model"] == "gemini-embedding-001"
    assert meta["embedding_dim"] == 768
    assert meta["hnsw:space"] == "cosine"


def test_mock_provider_records_and_matches_its_own_identity(chroma):
    from core.mock_gemini import MockGeminiProClient

    client = MockGeminiProClient()
    service = RetrievalService(chroma_client=chroma, gemini_client=client)
    course_id = uuid.uuid4()
    service.create_course_collection(course_id)
    service.add_chunks(course_id, _chunks(course_id))
    assert len(service.retrieve(course_id, "chunk", top_k=2).chunks) == 2


# ── Mismatches fail clearly ───────────────────────────────────────────────────


def test_query_with_another_model_is_refused_before_any_embedding_call(chroma):
    course_id = _indexed(chroma, _Embedder(model="text-embedding-004"))
    other = _Embedder(model="gemini-embedding-001")
    service = RetrievalService(chroma_client=chroma, gemini_client=other)

    with pytest.raises(EmbeddingMismatch, match="text-embedding-004.*gemini-embedding-001"):
        service.retrieve(course_id, "what is potential difference?")
    assert other.query_calls == 0


def test_query_of_another_size_is_refused_with_both_sizes_named(chroma):
    course_id = _indexed(chroma, _Embedder(dimensions=768))
    service = RetrievalService(
        chroma_client=chroma, gemini_client=_Embedder(dimensions=768, size=3072)
    )

    with pytest.raises(EmbeddingMismatch, match="768-dimension.*produced 3072"):
        service.retrieve(course_id, "what is potential difference?")


def test_adding_chunks_with_another_model_is_refused_and_nothing_is_written(chroma):
    course_id = _indexed(chroma, _Embedder(model="text-embedding-004"))
    service = RetrievalService(chroma_client=chroma, gemini_client=_Embedder(model="gemini-embedding-001"))

    with pytest.raises(EmbeddingMismatch):
        service.add_chunks(course_id, _chunks(course_id, n=5))
    collection = chroma.get_collection(f"course_{str(course_id).replace('-', '_')}")
    assert collection.count() == 3


def test_unknown_size_is_checked_against_a_stored_vector(chroma):
    # Ollama does not know its size up front; the stored vectors decide.
    course_id = _indexed(chroma, _Embedder(model="nomic-embed-text", dimensions=None, size=768))
    service = RetrievalService(
        chroma_client=chroma,
        gemini_client=_Embedder(model="nomic-embed-text", dimensions=None, size=1024),
    )
    with pytest.raises(EmbeddingMismatch, match="768-dimension.*produced 1024"):
        service.retrieve(course_id, "anything")


# ── Legacy collections (no metadata) ──────────────────────────────────────────


def _legacy_collection(chroma, size=768):
    course_id = uuid.uuid4()
    collection = chroma.get_or_create_collection(
        name=f"course_{str(course_id).replace('-', '_')}",
        metadata={"course_id": str(course_id), "hnsw:space": "cosine"},
    )
    collection.upsert(
        ids=["l0", "l1"],
        documents=["old chunk", "another old chunk"],
        embeddings=[[1.0] + [0.0] * (size - 1)] * 2,
        metadatas=[{"source_file": "a.pdf", "page_or_slide": 1, "course_id": str(course_id)}] * 2,
    )
    return course_id


def test_legacy_collection_of_the_same_size_is_used_and_logged_once(chroma, caplog):
    course_id = _legacy_collection(chroma, 768)
    service = RetrievalService(chroma_client=chroma, gemini_client=_Embedder())

    with caplog.at_level(logging.WARNING, logger="retrieval.service"):
        first = service.retrieve(course_id, "q1", top_k=2)
        second = service.retrieve(course_id, "q2", top_k=2)

    assert len(first.chunks) == 2 and len(second.chunks) == 2
    legacy = [r for r in caplog.records if "EMBEDDING-LEGACY" in r.getMessage()]
    assert len(legacy) == 1


def test_legacy_collection_of_another_size_fails_clearly_not_inside_chroma(chroma):
    course_id = _legacy_collection(chroma, 768)
    service = RetrievalService(
        chroma_client=chroma, gemini_client=_Embedder(dimensions=3072, size=3072)
    )
    with pytest.raises(EmbeddingMismatch, match="768-dimension.*produced 3072"):
        service.retrieve(course_id, "q")


def test_empty_legacy_collection_is_not_refused(chroma):
    course_id = uuid.uuid4()
    chroma.get_or_create_collection(
        name=f"course_{str(course_id).replace('-', '_')}",
        metadata={"course_id": str(course_id), "hnsw:space": "cosine"},
    )
    service = RetrievalService(chroma_client=chroma, gemini_client=_Embedder())
    assert service.retrieve(course_id, "q").chunks == []


# ── Logs carry counts and scores, never text ──────────────────────────────────


def test_retrieve_logs_no_query_or_chunk_text(chroma, caplog):
    secret_query = "my private question about zebra striping"
    secret_chunk = "confidential course passage on quokka habitats"
    service = RetrievalService(chroma_client=chroma, gemini_client=_Embedder())
    course_id = uuid.uuid4()
    service.create_course_collection(course_id)
    service.add_chunks(
        course_id,
        [Chunk(text=secret_chunk, source_file="notes.pdf", page_or_slide=2, course_id=course_id, chunk_id="s0")],
    )

    with caplog.at_level(logging.DEBUG, logger="retrieval.service"):
        result = service.retrieve(course_id, secret_query, top_k=1)

    assert len(result.chunks) == 1
    text = " ".join(r.getMessage() for r in caplog.records).lower()
    for fragment in ("zebra", "private question", "quokka", "confidential"):
        assert fragment not in text
    assert any("[RETRIEVE-COMPLETE]" in r.getMessage() and "chunks=1" in r.getMessage() for r in caplog.records)
