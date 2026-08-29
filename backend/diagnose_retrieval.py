#!/usr/bin/env python3
"""
Diagnostic / regression checks for the retrieval groundedness pipeline.

Covers the two bugs fixed together:
  1. Mock embedding must be deterministic AND carry lexical signal
     (token-hashing bag-of-words, not whole-string hash).
  2. Chroma collections must use cosine space (via create_course_collection),
     so retrieve()'s `confidence = 1.0 - distance` is mathematically valid.

Run:  python diagnose_retrieval.py
"""

import sys
import uuid
from math import sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import chromadb  # noqa: E402

from core.config import settings  # noqa: E402
from core.mock_gemini import MockGeminiProClient  # noqa: E402
from retrieval.models import Chunk  # noqa: E402
from retrieval.service import RetrievalService  # noqa: E402


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sqrt(sum(x * x for x in a))
    nb = sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def test_mock_embedding_determinism() -> bool:
    """Same text must always produce the same vector (across calls)."""
    print("=" * 80)
    print("TEST 1: Mock embedding determinism + lexical signal")
    print("=" * 80)

    client = MockGeminiProClient()
    topic = "Plate Tectonics"
    on_topic = (
        "Plate tectonics is the scientific theory explaining how the Earth's "
        "lithosphere is divided into large plates that move over the asthenosphere."
    )
    unrelated = (
        "Photosynthesis is the process by which green plants convert sunlight "
        "into chemical energy stored in glucose molecules using chlorophyll."
    )

    e1 = client.embed(topic)
    e2 = client.embed(topic)
    deterministic = e1 == e2
    print(f"  determinism (same text -> same vector): {'PASS' if deterministic else 'FAIL'}")

    sim_on = _cosine(client.embed(topic), client.embed(on_topic))
    sim_off = _cosine(client.embed(topic), client.embed(unrelated))
    print(f"  cosine(topic, on-topic chunk)  = {sim_on:.4f}")
    print(f"  cosine(topic, unrelated chunk) = {sim_off:.4f}")

    has_signal = sim_on > sim_off and sim_on > 0.05
    print(f"  lexical signal (on-topic > unrelated, non-trivial): {'PASS' if has_signal else 'FAIL'}")

    ok = deterministic and has_signal
    print(f"  => {'PASS' if ok else 'FAIL'}")
    print()
    return ok


def test_end_to_end_retrieval() -> bool:
    """
    Full path: RetrievalService + real (in-memory) Chroma with cosine space
    (via create_course_collection) + token-hashing mock embedding.

    Asserts ranking correctness and mock-threshold pass/fail — NOT exact scores.
    """
    print("=" * 80)
    print("TEST 2: End-to-end retrieval (cosine space + token-hashing mock)")
    print("=" * 80)

    service = RetrievalService(
        chroma_client=chromadb.EphemeralClient(),
        gemini_client=MockGeminiProClient(),
    )
    course_id = uuid.uuid4()
    service.create_course_collection(course_id)

    on_topic = Chunk(
        text=(
            "Plate tectonics is the scientific theory explaining how the Earth's "
            "lithosphere is divided into large rigid plates. Convergent plate "
            "boundaries form when plates collide; divergent boundaries occur where "
            "plates separate along mid-ocean ridges."
        ),
        source_file="geology.pdf", page_or_slide=1, course_id=course_id, chunk_id="c_on",
    )
    unrelated = Chunk(
        text=(
            "Quantum mechanics describes the behavior of matter at atomic and "
            "subatomic scales. The Schrodinger equation governs quantum systems and "
            "superposition allows particles to exist in multiple states."
        ),
        source_file="physics.pdf", page_or_slide=5, course_id=course_id, chunk_id="c_off",
    )
    service.add_chunks(course_id, [on_topic, unrelated])

    result = service.retrieve(course_id, "Plate Tectonics", top_k=2)

    # Map confidence back to each chunk regardless of return order.
    by_source = {
        c.source_file: score
        for c, score in zip(result.chunks, result.confidence_scores)
    }
    on_conf = by_source.get("geology.pdf", 0.0)
    off_conf = by_source.get("physics.pdf", 0.0)
    mock_threshold = settings.groundedness_threshold_mock

    print(f"  on-topic confidence   = {on_conf:.4f}")
    print(f"  unrelated confidence  = {off_conf:.4f}")
    print(f"  mock threshold        = {mock_threshold:.2f}")

    ranking_ok = on_conf > off_conf
    on_passes = on_conf >= mock_threshold
    off_refused = off_conf < mock_threshold

    print(f"  ranking (on-topic > unrelated):        {'PASS' if ranking_ok else 'FAIL'}")
    print(f"  on-topic clears mock threshold:        {'PASS' if on_passes else 'FAIL'}")
    print(f"  unrelated below mock threshold:        {'PASS' if off_refused else 'FAIL'}")

    ok = ranking_ok and on_passes and off_refused
    print(f"  => {'PASS' if ok else 'FAIL'}")
    print()
    return ok


if __name__ == "__main__":
    print(f"LLM_PROVIDER={settings.llm_provider}  "
          f"active_threshold={settings.active_groundedness_threshold}  "
          f"threshold(gemini)={settings.groundedness_threshold}  "
          f"threshold(mock)={settings.groundedness_threshold_mock}  "
          f"threshold(ollama)={settings.groundedness_threshold_ollama}")
    print()

    results = [
        test_mock_embedding_determinism(),
        test_end_to_end_retrieval(),
    ]

    print("=" * 80)
    if all(results):
        print("ALL CHECKS PASSED")
        sys.exit(0)
    else:
        print("SOME CHECKS FAILED")
        sys.exit(1)
