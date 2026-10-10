"""
tests/test_gemini_embeddings.py
-------------------------------
Batched embeddings and the provider-neutral embed_documents / embed_query
methods. The Gemini SDK is replaced by a recorder: no network, no real key.
"""

import uuid

import chromadb
import pytest

from retrieval.models import Chunk
from retrieval.service import RetrievalService


@pytest.fixture
def sdk_calls(monkeypatch):
    """Record every genai.embed_content call and answer with one vector per text."""
    import google.generativeai as genai

    calls: list[dict] = []

    def fake_embed_content(**kwargs):
        calls.append(kwargs)
        content = kwargs["content"]
        texts = content if isinstance(content, list) else [content]
        vectors = [[float(len(t) % 7) / 7.0, 1.0, 0.5] for t in texts]
        return {"embedding": vectors if isinstance(content, list) else vectors[0]}

    monkeypatch.setattr(genai, "embed_content", fake_embed_content)
    return calls


def _client():
    from main import GeminiProClient

    return GeminiProClient(api_key="test-key", model="gemini-flash-lite-latest")


def test_165_chunks_are_two_batched_requests(sdk_calls):
    texts = [f"chunk {i}" for i in range(165)]
    vectors = _client().embed_documents(texts)

    assert len(vectors) == 165
    assert [len(c["content"]) for c in sdk_calls] == [100, 65]
    assert all(c["task_type"] == "retrieval_document" for c in sdk_calls)
    assert sdk_calls[0]["content"][0] == "chunk 0"
    assert sdk_calls[1]["content"][-1] == "chunk 164"


def test_batch_size_comes_from_settings(sdk_calls, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embedding_batch_size", 40)
    _client().embed_documents([f"c{i}" for i in range(165)])
    assert [len(c["content"]) for c in sdk_calls] == [40, 40, 40, 40, 5]


def test_batch_size_is_capped_at_the_api_limit(sdk_calls, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "embedding_batch_size", 500)
    _client().embed_documents([f"c{i}" for i in range(165)])
    assert [len(c["content"]) for c in sdk_calls] == [100, 65]


def test_query_uses_the_query_task_type(sdk_calls):
    vector = _client().embed_query("what is potential difference?")
    assert len(vector) == 3
    assert len(sdk_calls) == 1
    assert sdk_calls[0]["task_type"] == "retrieval_query"
    assert sdk_calls[0]["content"] == ["what is potential difference?"]


def test_a_short_batch_answer_is_an_error_not_a_silent_misalignment(monkeypatch):
    import google.generativeai as genai

    monkeypatch.setattr(genai, "embed_content", lambda **kwargs: {"embedding": [[0.1, 0.2]]})
    with pytest.raises(ValueError, match="1 embeddings for 2 texts"):
        _client().embed_documents(["a", "b"])


def test_no_texts_means_no_requests(sdk_calls):
    assert _client().embed_documents([]) == []
    assert sdk_calls == []


# ── Every provider has the same embedding interface ───────────────────────────


@pytest.mark.parametrize("provider", ["mock", "ollama"])
def test_every_provider_has_embed_documents_and_embed_query(provider, monkeypatch):
    import main
    from core.config import settings

    monkeypatch.setattr(settings, "llm_provider", provider)
    pro, _ = main._build_llm_clients()
    assert callable(pro.embed_documents)
    assert callable(pro.embed_query)


def test_gemini_client_has_embed_documents_and_embed_query():
    from main import GeminiProClient

    assert callable(GeminiProClient.embed_documents)
    assert callable(GeminiProClient.embed_query)


def test_mock_vectors_are_unchanged_by_the_new_methods():
    from core.mock_gemini import MockGeminiProClient

    client = MockGeminiProClient()
    assert client.embed_documents(["like charges repel"]) == [client.embed("like charges repel")]
    assert client.embed_query("like charges repel") == client.embed("like charges repel")


# ── RetrievalService uses them ────────────────────────────────────────────────


class _CountingClient:
    """Records which embedding method RetrievalService calls."""

    def __init__(self):
        self.document_calls: list[int] = []
        self.query_calls = 0

    def embed(self, text):  # pragma: no cover - must not be used any more
        raise AssertionError("RetrievalService should not call embed() per text")

    def embed_documents(self, texts):
        self.document_calls.append(len(texts))
        return [[1.0, float(i % 3), 0.5] for i, _ in enumerate(texts)]

    def embed_query(self, text):
        self.query_calls += 1
        return [1.0, 0.0, 0.5]


def test_add_chunks_embeds_all_chunks_in_one_call_and_retrieve_embeds_a_query():
    client = _CountingClient()
    service = RetrievalService(chroma_client=chromadb.EphemeralClient(), gemini_client=client)
    course_id = uuid.uuid4()
    service.create_course_collection(course_id)
    chunks = [
        Chunk(text=f"chunk {i}", source_file="a.pdf", page_or_slide=1, course_id=course_id, chunk_id=f"c{i}")
        for i in range(165)
    ]

    service.add_chunks(course_id, chunks)
    result = service.retrieve(course_id, "anything", top_k=3)

    assert client.document_calls == [165]
    assert client.query_calls == 1
    assert len(result.chunks) == 3
