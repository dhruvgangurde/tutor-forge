"""
tests/test_ingestion.py
-----------------------
Tests for the course ingestion pipeline (F11) — previously untested.

Covers each node (validate, parse, build_hierarchy, chunk_and_embed,
persist_to_db) plus a full graph run end-to-end with mocked Gemini/retrieval and
a real in-memory SQLite database.
"""

import json
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.ingestion.graph import build_ingestion_graph
from agents.ingestion.nodes import (
    _MAX_FILE_BYTES,
    build_hierarchy_node,
    chunk_and_embed_node,
    parse_content_node,
    persist_to_db_node,
    validate_files_node,
)
from core.database import Base
from core.security import hash_password
from db.models import Chapter, Concept, ConceptPrerequisite, Course, IngestionJob, User

HIERARCHY = {
    "course_title": "Test Course",
    "chapters": [
        {
            "title": "Chapter 1",
            "order_index": 0,
            "concepts": [
                {"name": "A", "description": "concept a", "keywords": ["k"],
                 "difficulty": "beginner", "order_index": 0, "prerequisites": []},
                {"name": "B", "description": "concept b", "keywords": [],
                 "difficulty": "beginner", "order_index": 1, "prerequisites": ["A"]},
            ],
        }
    ],
}


def _state(**overrides) -> dict:
    base = {
        "job_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "files": [],
        "parsed_content": [],
        "hierarchy": {},
        "chunks": [],
        "status": "running",
        "error": None,
    }
    base.update(overrides)
    return base


def _txt(name: str, text: str) -> dict:
    return {"filename": name, "content": text.encode(), "mime_type": "text/plain"}


# ── validate_files_node ───────────────────────────────────────────────────────

def test_validate_accepts_supported_file():
    out = validate_files_node(_state(files=[_txt("a.txt", "hi")]))
    assert out["status"] == "running"


def test_validate_rejects_unsupported_mime():
    bad = {"filename": "a.exe", "content": b"x", "mime_type": "application/x-msdownload"}
    out = validate_files_node(_state(files=[bad]))
    assert out["status"] == "failed"
    assert "Unsupported" in out["error"]


def test_validate_rejects_oversized_file():
    big = {"filename": "a.txt", "content": b"x" * (_MAX_FILE_BYTES + 1), "mime_type": "text/plain"}
    out = validate_files_node(_state(files=[big]))
    assert out["status"] == "failed"
    assert "too large" in out["error"].lower()


# ── parse_content_node ────────────────────────────────────────────────────────

def test_parse_txt_decodes_text():
    out = parse_content_node(_state(files=[_txt("notes.txt", "Hello world")]))
    parsed = out["parsed_content"]
    assert len(parsed) == 1
    assert parsed[0]["text"] == "Hello world"
    assert parsed[0]["source_file"] == "notes.txt"


# ── build_hierarchy_node ──────────────────────────────────────────────────────

def _parsed(text: str = "some course content") -> list[dict]:
    return [{"source_file": "a.txt", "page_or_slide": None, "text": text}]


def test_build_hierarchy_parses_json():
    gp = MagicMock()
    gp.generate.return_value = json.dumps(HIERARCHY)
    out = build_hierarchy_node(_state(parsed_content=_parsed()), gp)
    assert out["hierarchy"]["chapters"][0]["title"] == "Chapter 1"


def test_build_hierarchy_strips_code_fences():
    gp = MagicMock()
    gp.generate.return_value = "```json\n" + json.dumps(HIERARCHY) + "\n```"
    out = build_hierarchy_node(_state(parsed_content=_parsed()), gp)
    assert out["hierarchy"]["chapters"]


def test_build_hierarchy_invalid_json_fails():
    gp = MagicMock()
    gp.generate.return_value = "this is not json"
    out = build_hierarchy_node(_state(parsed_content=_parsed()), gp)
    assert out["status"] == "failed"
    assert "parse error" in out["error"].lower()


# ── chunk_and_embed_node ──────────────────────────────────────────────────────

def test_chunk_and_embed_produces_overlapping_chunks():
    rs = MagicMock()
    course_id = uuid.uuid4()
    state = _state(
        course_id=course_id,
        parsed_content=[{"source_file": "a.txt", "page_or_slide": 1, "text": "x" * 1000}],
    )
    out = chunk_and_embed_node(state, rs)

    rs.add_chunks.assert_called_once()
    passed_course_id, chunks = rs.add_chunks.call_args[0]
    assert passed_course_id == course_id
    # 1000 chars, window 400, stride 340 -> windows at 0, 340, 680 => 3 chunks
    assert len(chunks) == 3
    assert out["chunks"][0]["chunk_id"] == f"{course_id}_a.txt_1_0"


def test_chunk_and_embed_empty_content_adds_nothing():
    rs = MagicMock()
    out = chunk_and_embed_node(_state(parsed_content=[]), rs)
    passed_course_id, chunks = rs.add_chunks.call_args[0]
    assert chunks == []
    assert out["chunks"] == []


# ── persist_to_db_node + full graph (SQLite) ──────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = async_sessionmaker(_engine, expire_on_commit=False)


@pytest.fixture
async def seeded():
    """Fresh DB with a pending course + ingestion job; yields (db, course_id, job_id)."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as db:
        teacher = User(email=f"t-{uuid.uuid4()}@x.com", hashed_password=hash_password("pw"), role="teacher")
        db.add(teacher)
        await db.flush()
        course = Course(name="C", owner_id=teacher.id, status="pending")
        db.add(course)
        await db.flush()
        job = IngestionJob(course_id=course.id, status="running")
        db.add(job)
        await db.commit()
        yield db, course.id, job.id
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def test_persist_to_db_writes_hierarchy_and_marks_ready(seeded):
    db, course_id, job_id = seeded
    out = await persist_to_db_node(_state(course_id=course_id, job_id=job_id, hierarchy=HIERARCHY), db)
    assert out["status"] == "complete"

    chapters = (await db.execute(select(Chapter).where(Chapter.course_id == course_id))).scalars().all()
    assert len(chapters) == 1
    concepts = (await db.execute(select(Concept))).scalars().all()
    assert {c.name for c in concepts} == {"A", "B"}
    prereqs = (await db.execute(select(ConceptPrerequisite))).scalars().all()
    assert len(prereqs) == 1  # B requires A

    job = (await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one()
    course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
    assert job.status == "complete"
    assert course.status == "ready"


async def test_ingestion_graph_end_to_end(seeded):
    db, course_id, job_id = seeded
    gemini_pro = MagicMock()
    gemini_pro.generate.return_value = json.dumps(HIERARCHY)
    retrieval = MagicMock()

    graph = build_ingestion_graph(retrieval_service=retrieval, gemini_pro=gemini_pro, db=db)
    initial = _state(
        course_id=course_id,
        job_id=job_id,
        files=[_txt("notes.txt", "Some course content about A and B.")],
    )
    final = await graph.ainvoke(initial)

    assert final["status"] == "complete"
    retrieval.create_course_collection.assert_called_once()
    retrieval.add_chunks.assert_called_once()
    course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
    assert course.status == "ready"
