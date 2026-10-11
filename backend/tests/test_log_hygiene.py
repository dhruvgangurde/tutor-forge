"""
tests/test_log_hygiene.py
-------------------------
The tutor and ingestion logs carry lengths, counts and ids, never what a
student typed or what a course says. Each test runs the real node with a
distinctive marker word in every piece of text and checks the logs at DEBUG
level for it. Fakes only: no network, no database.
"""

import json
import logging
import uuid
from unittest.mock import MagicMock

import pytest

from retrieval.models import RetrievalResult

#: Appears in every question, file and outline below; must appear in no log line.
_MARKER = "quokkamarker"


def _logged(caplog) -> str:
    return " ".join(r.getMessage() for r in caplog.records).lower()


def _ingestion_state(**overrides) -> dict:
    state = {
        "job_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "files": [],
        "parsed_content": [],
        "hierarchy": {},
        "chunks": [],
        "status": "running",
        "error": None,
    }
    state.update(overrides)
    return state


def _outline() -> dict:
    return {
        "course_title": f"{_MARKER} course",
        "chapters": [
            {
                "title": f"{_MARKER} chapter",
                "order_index": 0,
                "concepts": [
                    {"name": f"{_MARKER} concept", "description": f"{_MARKER} text",
                     "keywords": [_MARKER], "difficulty": "beginner", "order_index": 0,
                     "prerequisites": []},
                ],
            }
        ],
    }


# ── Tutor ─────────────────────────────────────────────────────────────────────


def test_tutor_logs_carry_no_question_text(caplog):
    from agents.tutor.nodes import check_groundedness_node, retrieve_context_node

    retrieval = MagicMock()
    retrieval.retrieve.return_value = RetrievalResult(query="q")
    retrieval.is_grounded.return_value = False
    state = {
        "session_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "question": f"because {_MARKER} zebras are striped",   # a reply, not a new question
        "prior_question": f"what is the {_MARKER} habitat?",
    }

    with caplog.at_level(logging.DEBUG, logger="agents.tutor"):
        resolved = retrieve_context_node(state, retrieval)
        check_groundedness_node(resolved, retrieval)

    text = _logged(caplog)
    assert "[query-resolution]" in text and "[groundedness-decision]" in text
    assert _MARKER not in text
    assert "zebras" not in text and "habitat" not in text


# ── Ingestion ─────────────────────────────────────────────────────────────────


def test_parsing_logs_carry_no_file_text(caplog):
    from agents.ingestion.nodes import parse_content_node

    file = {
        "filename": "notes.txt",
        "content": f"{_MARKER} lecture notes on habitats".encode(),
        "mime_type": "text/plain",
    }
    with caplog.at_level(logging.DEBUG, logger="agents.ingestion"):
        out = parse_content_node(_ingestion_state(files=[file]))

    assert out["parsed_content"]
    assert _MARKER not in _logged(caplog)


@pytest.mark.parametrize("reply", [json.dumps(_outline()), f'{{"chapters": "{_MARKER} not json'])
def test_outline_logs_carry_no_course_text_or_model_reply(caplog, reply):
    from agents.ingestion.nodes import build_hierarchy_node

    gemini = MagicMock()
    gemini.generate.return_value = reply
    parsed = [{"source_file": "notes.txt", "page_or_slide": 1, "text": f"{_MARKER} lecture notes"}]
    with caplog.at_level(logging.DEBUG, logger="agents.ingestion"):
        build_hierarchy_node(_ingestion_state(parsed_content=parsed), gemini)

    assert _MARKER not in _logged(caplog)


def test_ollama_outline_logs_carry_no_titles(caplog, monkeypatch):
    from agents.ingestion.nodes import build_hierarchy_node
    from core.config import settings

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    gemini = MagicMock()
    gemini.generate.side_effect = [f"{_MARKER} not json", json.dumps(_outline())]
    parsed = [{"source_file": "notes.txt", "page_or_slide": 1, "text": f"{_MARKER} lecture notes"}]
    with caplog.at_level(logging.DEBUG, logger="agents.ingestion"):
        build_hierarchy_node(_ingestion_state(parsed_content=parsed), gemini)

    assert _MARKER not in _logged(caplog)


def test_chunking_logs_carry_no_chunk_text(caplog):
    from agents.ingestion.nodes import chunk_and_embed_node

    parsed = [{"source_file": "notes.txt", "page_or_slide": 1, "text": f"{_MARKER} lecture notes " * 20}]
    with caplog.at_level(logging.DEBUG, logger="agents.ingestion"):
        out = chunk_and_embed_node(_ingestion_state(parsed_content=parsed), MagicMock())

    assert out["chunks"]
    assert _MARKER not in _logged(caplog)
