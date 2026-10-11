"""
tests/test_assessment_log_hygiene.py
------------------------------------
Assessment generation logs counts and positions, never generated text.

Four log lines used to carry a stem, options or the raw model response: the
JSON parse error, the rejected distractor improvement, the stripped stem and
the cross-question contamination warning. Generated questions and answer keys
are teacher-only until published, so none of it belongs in a log.

Fake model outputs only. No model, no network.
"""

import json
import logging
import uuid
from unittest.mock import MagicMock

from agents.assessment.nodes import generate_questions_node

#: Marks every piece of generated text, so a leak into any log line is visible.
_MARK = "zqxmark"


def _state():
    return {
        "course_id": uuid.UUID(int=0),
        "teacher_id": uuid.UUID(int=1),
        "config": {"topic": "Photosynthesis", "difficulty": "mixed", "count": 2},
        "retrieved_concepts": [],
        "retrieval_cache": {
            "query": "photosynthesis",
            "chunks": [{
                "text": "Photosynthesis converts light energy into chemical energy.",
                "source_file": "chapter1.pdf",
                "page_or_slide": 5,
                "course_id": str(uuid.UUID(int=0)),
                "chunk_id": "",
            }],
            "confidence_scores": [0.9],
        },
        "is_grounded": True,
        "questions": [],
        "bloom_tags": [],
        "distractors": [],
        "rubric_criteria": [],
        "answer_key": [],
        "assessment_id": uuid.UUID(int=2),
        "trace_id": None,
        "status": "running",
        "error": None,
    }


def _retrieval():
    svc = MagicMock()
    svc.build_context_window.return_value = (
        "[Source: chapter1.pdf, p.5]\nPhotosynthesis converts light energy into chemical energy."
    )
    return svc


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name.startswith("agents.assessment")]


def test_malformed_json_is_logged_by_length_only(caplog):
    flash = MagicMock()
    flash.generate.return_value = f'{{"questions": [{{"stem": "What is {_MARK} light?" '
    with caplog.at_level(logging.INFO, logger="agents.assessment"):
        result = generate_questions_node(_state(), retrieval_service=_retrieval(), gemini_flash=flash)

    assert result["status"] == "failed"
    messages = _messages(caplog)
    assert any(m.startswith("JSON parse error in Gemini response (") and " chars)" in m for m in messages)
    assert all(_MARK not in m for m in messages)


def test_stem_and_option_warnings_carry_positions_and_counts_only(caplog):
    leaked_answer = f"{_MARK} chlorophyll pigment molecule"
    questions = {
        "questions": [
            {
                "question_type": "short_answer",
                "stem": f"Name the green pigment {_MARK}.",
                "correct_answer": leaked_answer,
                "bloom_level": "remember",
                "difficulty": "easy",
                "rubric": [{"criterion": "Names it", "max_points": 1.0}],
            },
            {
                "question_type": "mcq",
                # "according to the context" is stripped, which used to log the stem.
                "stem": f"According to the context, which process {_MARK} makes sugar?",
                "options": [f"A. Photosynthesis {_MARK}", "B. Respiration", f"C. {leaked_answer}", "D. Digestion"],
                "correct_answer": "A",
                "bloom_level": "apply",  # a recall key must appear in the passages
                "difficulty": "easy",
            },
        ]
    }
    improved = {"improved_options": [f"Photosynthesis {_MARK}", "Respiration", leaked_answer, "Fermentation"]}
    flash = MagicMock()
    flash.generate.side_effect = [json.dumps(questions), json.dumps(improved)]

    with caplog.at_level(logging.INFO, logger="agents.assessment"):
        result = generate_questions_node(_state(), retrieval_service=_retrieval(), gemini_flash=flash)

    assert result.get("status") != "failed"
    messages = _messages(caplog)
    assert "Stripped context framing from the stem of question 2" in messages
    assert any(m.startswith("Distractor improvement REJECTED for question 2: 1 option(s)") for m in messages)
    assert any(m.startswith("[DISTRACTOR-CONTAMINATION] question 2 carries 1 option(s)") for m in messages)
    for message in messages:
        assert _MARK not in message, message
        assert "chlorophyll" not in message and "According" not in message, message


# ── Retrieval, groundedness and refusal: the topic and the chunk text ─────────


def _retrieval_state(topic):
    return {**_state(), "config": {"topic": topic, "difficulty": "mixed", "count": 2}, "retrieval_cache": {}}


def _chunk_retrieval():
    from retrieval.models import Chunk, RetrievalResult

    svc = MagicMock()
    svc.retrieve.return_value = RetrievalResult(
        query="photosynthesis",
        chunks=[Chunk(text=f"Course text {_MARK} about light.", source_file="chapter1.pdf",
                      page_or_slide=5, course_id=uuid.UUID(int=0))],
        confidence_scores=[0.9],
    )
    svc.is_grounded.return_value = False
    return svc


def test_retrieval_and_groundedness_log_the_topic_length_and_no_chunk_text(caplog):
    from agents.assessment.nodes import check_groundedness_node, refuse_node, retrieve_concepts_node

    topic = f"Photosynthesis {_MARK}"
    svc = _chunk_retrieval()
    with caplog.at_level(logging.DEBUG, logger="agents.assessment"):
        state = retrieve_concepts_node(_retrieval_state(topic), retrieval_service=svc)
        state = check_groundedness_node(state, retrieval_service=svc)
        refuse_node(state)

    messages = _messages(caplog)
    assert any(m.startswith("[RETRIEVAL-START]") and f"topic_chars={len(topic)}" in m for m in messages)
    assert any(m.startswith("[RETRIEVAL-CHUNK-1]") and "text_chars=" in m for m in messages)
    assert any(m.startswith("[GROUNDEDNESS-DECISION]") and f"topic_chars={len(topic)}" in m for m in messages)
    assert any(m.startswith("Assessment generation refused") for m in messages)
    for message in messages:
        assert _MARK not in message, message
        assert "Photosynthesis" not in message, message


def test_a_retrieval_failure_logs_the_topic_length_only(caplog):
    from agents.assessment.nodes import retrieve_concepts_node

    svc = MagicMock()
    svc.retrieve.side_effect = RuntimeError("Chroma down")
    with caplog.at_level(logging.DEBUG, logger="agents.assessment"):
        result = retrieve_concepts_node(_retrieval_state(f"Photosynthesis {_MARK}"), retrieval_service=svc)

    assert result["status"] == "failed"
    messages = _messages(caplog)
    assert any(m.startswith("[RETRIEVAL-ERROR]") for m in messages)
    assert all(_MARK not in m for m in messages)
