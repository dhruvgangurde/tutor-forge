"""
tests/test_assessments.py
--------------------------
Tests for the assessment API and agent layer.

Strategy:
  - Uses SQLite in-memory DB (same pattern as test_auth.py).
  - Gemini Flash and RetrievalService are mocked — no real API calls.
  - BackgroundTasks are executed synchronously via a stub so we can assert
    DB state after generation.
  - Every test is independent; DB is reset between tests via autouse fixture.
"""

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assessments.schemas import GenerateRequest
from core.database import Base
from core.dependencies import get_db_session, get_gemini_flash, get_retrieval_service
from core.security import hash_password
from db.models import Assessment, Course, Enrollment, User
from main import app

# ── Test database ─────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

_test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_TestSessionFactory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def override_get_db_session():
    async with _TestSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Mock services ─────────────────────────────────────────────────────────────

def _make_mock_retrieval(grounded: bool = True, empty: bool = False):
    """Create a mock RetrievalService."""
    from retrieval.models import Chunk, RetrievalResult

    svc = MagicMock()
    if empty:
        result = RetrievalResult(query="test")
    else:
        chunk = Chunk(
            text="Photosynthesis converts light energy into chemical energy.",
            source_file="chapter1.pdf",
            page_or_slide=5,
            course_id=uuid.UUID(int=0),
        )
        result = RetrievalResult(
            query="test",
            chunks=[chunk],
            confidence_scores=[0.9 if grounded else 0.3],
        )
    svc.retrieve.return_value = result
    svc.is_grounded.return_value = grounded
    svc.build_context_window.return_value = (
        "[Source: chapter1.pdf, p.5]\nPhotosynthesis converts light energy into chemical energy."
    )
    return svc


def _make_mock_gemini_flash(valid_json: bool = True, empty_list: bool = False):
    """Create a mock GeminiFlashClient."""
    svc = MagicMock()
    if not valid_json:
        svc.generate.return_value = "not json at all"
    elif empty_list:
        svc.generate.return_value = json.dumps({"questions": []})
    else:
        svc.generate.return_value = json.dumps({
            "questions": [
                {
                    "question_type": "mcq",
                    "stem": "What does photosynthesis convert?",
                    "bloom_level": "remember",
                    "difficulty": "easy",
                    "options": [
                        "A. Light to sound",
                        "B. Light to chemical energy",
                        "C. Chemical to kinetic energy",
                        "D. Heat to light",
                    ],
                    "correct_answer": "B",
                    "worked_solution": "Photosynthesis converts light energy into chemical energy stored in glucose.",
                    "rubric_criteria": [],
                },
                {
                    "question_type": "short_answer",
                    "stem": "Explain the role of chlorophyll in photosynthesis.",
                    "bloom_level": "understand",
                    "difficulty": "medium",
                    "options": None,
                    "correct_answer": "Chlorophyll absorbs light energy.",
                    "worked_solution": "Chlorophyll is the primary pigment that captures light.",
                    "rubric_criteria": [
                        {"description": "Mentions chlorophyll as pigment", "max_points": 1.0},
                        {"description": "Explains energy absorption", "max_points": 1.0},
                    ],
                },
            ]
        })
    return svc


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables and seed users + course before each test."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _TestSessionFactory() as db:
        teacher = User(
            email="teacher@demo.com",
            hashed_password=hash_password("password123"),
            role="teacher",
        )
        student = User(
            email="student@demo.com",
            hashed_password=hash_password("password123"),
            role="student",
        )
        db.add_all([teacher, student])
        await db.flush()

        course = Course(
            owner_id=teacher.id,
            name="Biology 101",
            status="ready",
        )
        db.add(course)
        await db.flush()
        # Course access requires an enrollment; the seeded student is in this class.
        db.add(Enrollment(course_id=course.id, student_id=student.id))
        await db.commit()

    yield

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def teacher_token(client):
    resp = await client.post("/auth/login", json={
        "email": "teacher@demo.com",
        "password": "password123",
    })
    return resp.json()["access_token"]


@pytest.fixture
async def student_token(client):
    resp = await client.post("/auth/login", json={
        "email": "student@demo.com",
        "password": "password123",
    })
    return resp.json()["access_token"]


@pytest.fixture
async def ready_course_id():
    async with _TestSessionFactory() as db:
        from sqlalchemy import select
        result = await db.execute(select(Course).where(Course.name == "Biology 101"))
        course = result.scalar_one()
        return course.id


# ── Schema validation tests ───────────────────────────────────────────────────

class TestGenerateRequestValidation:
    """Unit tests for GenerateRequest schema validators."""

    def test_valid_minimal_request(self):
        req = GenerateRequest(
            course_id=uuid.uuid4(),
            title="Test Assessment",
            topic="Photosynthesis",
        )
        assert req.difficulty == "mixed"
        assert req.count == 10

    def test_topic_blank_rejected(self):
        with pytest.raises(Exception):  # pydantic ValidationError
            GenerateRequest(
                course_id=uuid.uuid4(),
                title="Test",
                topic="   ",
            )

    def test_invalid_difficulty_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(
                course_id=uuid.uuid4(),
                title="Test",
                topic="Topic",
                difficulty="extreme",
            )

    def test_count_below_min_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(
                course_id=uuid.uuid4(),
                title="Test",
                topic="Topic",
                count=0,
            )

    def test_count_above_max_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(
                course_id=uuid.uuid4(),
                title="Test",
                topic="Topic",
                count=31,
            )

    def test_invalid_bloom_level_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(
                course_id=uuid.uuid4(),
                title="Test",
                topic="Topic",
                bloom_mix={"synthesize": 2},  # not a valid Bloom level
                count=2,
            )

    def test_bloom_total_mismatch_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(
                course_id=uuid.uuid4(),
                title="Test",
                topic="Topic",
                bloom_mix={"remember": 3},
                count=10,  # bloom total (3) != count (10)
            )

    def test_type_mix_total_mismatch_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(
                course_id=uuid.uuid4(),
                title="Test",
                topic="Topic",
                type_mix={"mcq": 5},
                count=10,  # type total (5) != count (10)
            )


# ── Generate endpoint tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_returns_202(client, teacher_token, ready_course_id):
    """Successful generate call returns 202 with assessment_id."""
    mock_retrieval = _make_mock_retrieval(grounded=True)
    mock_flash = _make_mock_gemini_flash(valid_json=True)
    app.dependency_overrides[get_retrieval_service] = lambda: mock_retrieval
    app.dependency_overrides[get_gemini_flash] = lambda: mock_flash

    # The 202 handler schedules _run_assessment_graph as a BackgroundTask; it is
    # stubbed globally in conftest (stub_background_jobs) so no real DB/infra is
    # touched here.
    resp = await client.post(
        "/assessments/generate",
        json={
            "course_id": str(ready_course_id),
            "title": "Photosynthesis Quiz",
            "topic": "Photosynthesis",
            "count": 10,
        },
        headers={"Authorization": f"Bearer {teacher_token}"},
    )

    app.dependency_overrides.pop(get_retrieval_service, None)
    app.dependency_overrides.pop(get_gemini_flash, None)

    assert resp.status_code == 202
    data = resp.json()
    assert "assessment_id" in data
    assert data["status"] == "generating"


@pytest.mark.asyncio
async def test_generate_wrong_course_returns_404(client, teacher_token):
    resp = await client.post(
        "/assessments/generate",
        json={
            "course_id": str(uuid.uuid4()),  # non-existent course
            "title": "Quiz",
            "topic": "Topic",
            "count": 5,
        },
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_requires_teacher_role(client, student_token, ready_course_id):
    resp = await client.post(
        "/assessments/generate",
        json={
            "course_id": str(ready_course_id),
            "title": "Quiz",
            "topic": "Topic",
        },
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code in {401, 403}


@pytest.mark.asyncio
async def test_generate_requires_auth(client, ready_course_id):
    resp = await client.post(
        "/assessments/generate",
        json={
            "course_id": str(ready_course_id),
            "title": "Quiz",
            "topic": "Topic",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_generate_invalid_request_body_rejected(client, teacher_token, ready_course_id):
    """Blank topic should fail schema validation."""
    resp = await client.post(
        "/assessments/generate",
        json={
            "course_id": str(ready_course_id),
            "title": "Quiz",
            "topic": "  ",  # blank
        },
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 422


# ── List and detail endpoint tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_returns_summary_not_questions(client, teacher_token, ready_course_id):
    """List endpoint returns AssessmentSummary (no question content)."""
    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        result = await db.execute(_select(Course).where(Course.id == ready_course_id))
        course = result.scalar_one()
        teacher_id = course.owner_id
        assessment = Assessment(
            course_id=ready_course_id,
            created_by=teacher_id,
            title="Draft",
            status="draft",
            config="{}",
        )
        db.add(assessment)
        await db.commit()

    resp = await client.get(
        f"/assessments/course/{ready_course_id}",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    # Must not contain question content
    assert "questions" not in data[0]
    assert data[0]["title"] == "Draft"


@pytest.mark.asyncio
async def test_get_assessment_detail(client, teacher_token, ready_course_id):
    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        result = await db.execute(_select(Course).where(Course.id == ready_course_id))
        course = result.scalar_one()
        assessment = Assessment(
            course_id=ready_course_id,
            created_by=course.owner_id,
            title="Detail Test",
            status="draft",
            config="{}",
        )
        db.add(assessment)
        await db.commit()
        await db.refresh(assessment)
        aid = assessment.id

    resp = await client.get(
        f"/assessments/{aid}",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Detail Test"
    assert "questions" in resp.json()


# ── Publish endpoint tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_draft_assessment(client, teacher_token, ready_course_id):
    """Publishing a draft assessment with questions should succeed."""
    from db.models import Question

    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        result = await db.execute(_select(Course).where(Course.id == ready_course_id))
        course = result.scalar_one()
        assessment = Assessment(
            course_id=ready_course_id,
            created_by=course.owner_id,
            title="Publish Test",
            status="draft",
            config="{}",
        )
        db.add(assessment)
        await db.flush()
        question = Question(
            assessment_id=assessment.id,
            question_type="mcq",
            stem="Test question?",
            options=json.dumps(["A. a", "B. b", "C. c", "D. d"]),
            answer_key=json.dumps({"correct_answer": "A", "worked_solution": "A is correct."}),
            bloom_level="remember",
            difficulty="easy",
            max_points=1.0,
            order_index=0,
        )
        db.add(question)
        await db.commit()
        await db.refresh(assessment)
        aid = assessment.id

    resp = await client.patch(
        f"/assessments/{aid}/publish",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


@pytest.mark.asyncio
async def test_publish_empty_assessment_rejected(client, teacher_token, ready_course_id):
    """Publishing an empty assessment (no questions) should return 400."""
    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        result = await db.execute(_select(Course).where(Course.id == ready_course_id))
        course = result.scalar_one()
        assessment = Assessment(
            course_id=ready_course_id,
            created_by=course.owner_id,
            title="Empty",
            status="draft",
            config="{}",
        )
        db.add(assessment)
        await db.commit()
        await db.refresh(assessment)
        aid = assessment.id

    resp = await client.patch(
        f"/assessments/{aid}/publish",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 400


# ── Submit endpoint tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_duplicate_rejected(client, student_token, teacher_token, ready_course_id):
    """Second submission from same student should return 409."""
    from db.models import Question

    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        result = await db.execute(_select(Course).where(Course.id == ready_course_id))
        course = result.scalar_one()
        assessment = Assessment(
            course_id=ready_course_id,
            created_by=course.owner_id,
            title="Dup Test",
            status="published",
            config="{}",
        )
        db.add(assessment)
        await db.flush()
        question = Question(
            assessment_id=assessment.id,
            question_type="mcq",
            stem="Q?",
            options=json.dumps(["A. a", "B. b", "C. c", "D. d"]),
            answer_key=json.dumps({"correct_answer": "A", "worked_solution": "A."}),
            bloom_level="remember",
            difficulty="easy",
            max_points=1.0,
            order_index=0,
        )
        db.add(question)
        await db.commit()
        await db.refresh(assessment)
        await db.refresh(question)
        aid = assessment.id
        qid = question.id

    payload = {"responses": [{"question_id": str(qid), "answer_choice": "A"}]}
    headers = {"Authorization": f"Bearer {student_token}"}

    resp1 = await client.post(f"/assessments/{aid}/submit", json=payload, headers=headers)
    assert resp1.status_code == 201

    resp2 = await client.post(f"/assessments/{aid}/submit", json=payload, headers=headers)
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_submit_unpublished_rejected(client, student_token, ready_course_id):
    """Submitting to a draft (not published) assessment should return 404."""
    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        result = await db.execute(_select(Course).where(Course.id == ready_course_id))
        course = result.scalar_one()
        assessment = Assessment(
            course_id=ready_course_id,
            created_by=course.owner_id,
            title="Draft",
            status="draft",  # not published
            config="{}",
        )
        db.add(assessment)
        await db.commit()
        await db.refresh(assessment)
        aid = assessment.id

    resp = await client.post(
        f"/assessments/{aid}/submit",
        json={"responses": [{"question_id": str(uuid.uuid4()), "answer_choice": "A"}]},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_submit_invalid_question_id_rejected(client, student_token, ready_course_id):
    """Submitting a question_id not belonging to the assessment should return 400."""
    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        result = await db.execute(_select(Course).where(Course.id == ready_course_id))
        course = result.scalar_one()
        assessment = Assessment(
            course_id=ready_course_id,
            created_by=course.owner_id,
            title="Q Ownership Test",
            status="published",
            config="{}",
        )
        db.add(assessment)
        await db.commit()
        await db.refresh(assessment)
        aid = assessment.id

    resp = await client.post(
        f"/assessments/{aid}/submit",
        json={"responses": [{"question_id": str(uuid.uuid4()), "answer_choice": "B"}]},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 400


# ── Node unit tests (no HTTP) ─────────────────────────────────────────────────

class TestNodes:
    """Unit tests for agent nodes in isolation."""

    def _base_state(self) -> dict:
        return {
            "course_id": uuid.UUID(int=0),
            "teacher_id": uuid.UUID(int=1),
            "config": {"topic": "Photosynthesis", "difficulty": "mixed", "count": 2},
            "retrieved_concepts": [],
            "retrieval_cache": {},
            "is_grounded": False,
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

    def test_refuse_node_sets_failed(self):
        from agents.assessment.nodes import refuse_node
        state = self._base_state()
        result = refuse_node(state)
        assert result["status"] == "failed"
        assert result["error"] is not None
        assert result["questions"] == []

    def test_retrieve_concepts_chromadb_failure(self):
        from agents.assessment.nodes import retrieve_concepts_node
        svc = MagicMock()
        svc.retrieve.side_effect = Exception("Chroma down")
        state = self._base_state()
        result = retrieve_concepts_node(state, retrieval_service=svc)
        assert result["status"] == "failed"
        assert "Retrieval failed" in result["error"]

    def test_generate_questions_malformed_json(self):
        from agents.assessment.nodes import generate_questions_node
        svc = _make_mock_retrieval()
        flash = MagicMock()
        flash.generate.return_value = "definitely not json"
        state = {
            **self._base_state(),
            "retrieval_cache": {
                "query": "test",
                "chunks": [{
                    "text": "x", "source_file": "f.pdf",
                    "page_or_slide": 1,
                    "course_id": str(uuid.UUID(int=0)),
                    "chunk_id": "",
                }],
                "confidence_scores": [0.9],
            },
            "is_grounded": True,
        }
        result = generate_questions_node(state, retrieval_service=svc, gemini_flash=flash)
        assert result["status"] == "failed"
        assert "malformed JSON" in result["error"]

    def test_generate_questions_empty_list(self):
        from agents.assessment.nodes import generate_questions_node
        svc = _make_mock_retrieval()
        flash = MagicMock()
        flash.generate.return_value = json.dumps({"questions": []})
        state = {
            **self._base_state(),
            "retrieval_cache": {
                "query": "test",
                "chunks": [{
                    "text": "x", "source_file": "f.pdf",
                    "page_or_slide": 1,
                    "course_id": str(uuid.UUID(int=0)),
                    "chunk_id": "",
                }],
                "confidence_scores": [0.9],
            },
            "is_grounded": True,
        }
        result = generate_questions_node(state, retrieval_service=svc, gemini_flash=flash)
        assert result["status"] == "failed"
        assert "empty" in result["error"].lower()

    def test_bloom_normalization(self):
        """LLM-returned 'APPLY' should be normalized to 'apply'."""
        from agents.assessment.nodes import _normalize_bloom
        assert _normalize_bloom("APPLY") == "apply"
        assert _normalize_bloom("unknown_level") == "understand"
        assert _normalize_bloom("") == "understand"

    def test_difficulty_normalization(self):
        from agents.assessment.nodes import _normalize_difficulty
        assert _normalize_difficulty("HARD") == "hard"
        assert _normalize_difficulty("extreme") == "medium"


# ── Stabilization: failure reporting tests ────────────────────────────────────

class TestFailureReporting:
    """
    Regression tests for generation_error surfacing (Stabilization Pass).
    These tests do NOT call the LLM — they seed the DB directly.
    """

    @pytest.mark.asyncio
    async def test_failed_assessment_shows_error_in_detail(
        self, client, teacher_token, ready_course_id
    ):
        """GET /assessments/{id} returns generation_error for a failed assessment."""
        async with _TestSessionFactory() as db:
            from sqlalchemy import select as _select
            result = await db.execute(_select(Course).where(Course.id == ready_course_id))
            course = result.scalar_one()
            assessment = Assessment(
                course_id=ready_course_id,
                created_by=course.owner_id,
                title="Failed Gen",
                status="failed",
                config="{}",
                generation_error="Topic not found in course material.",
            )
            db.add(assessment)
            await db.commit()
            await db.refresh(assessment)
            aid = assessment.id

        resp = await client.get(
            f"/assessments/{aid}",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["generation_error"] == "Topic not found in course material."
        assert data["questions"] == []

    @pytest.mark.asyncio
    async def test_failed_assessment_shows_error_in_list(
        self, client, teacher_token, ready_course_id
    ):
        """GET /assessments/course/{id} includes generation_error in summary."""
        async with _TestSessionFactory() as db:
            from sqlalchemy import select as _select
            result = await db.execute(_select(Course).where(Course.id == ready_course_id))
            course = result.scalar_one()
            assessment = Assessment(
                course_id=ready_course_id,
                created_by=course.owner_id,
                title="Failed List Test",
                status="failed",
                config="{}",
                generation_error="ChromaDB collection not found.",
            )
            db.add(assessment)
            await db.commit()

        resp = await client.get(
            f"/assessments/course/{ready_course_id}",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["generation_error"] == "ChromaDB collection not found."
        assert data[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_successful_assessment_has_null_error(
        self, client, teacher_token, ready_course_id
    ):
        """A draft assessment (successful generation) has generation_error=null."""
        async with _TestSessionFactory() as db:
            from sqlalchemy import select as _select
            result = await db.execute(_select(Course).where(Course.id == ready_course_id))
            course = result.scalar_one()
            assessment = Assessment(
                course_id=ready_course_id,
                created_by=course.owner_id,
                title="Successful Draft",
                status="draft",
                config="{}",
                generation_error=None,
            )
            db.add(assessment)
            await db.commit()
            await db.refresh(assessment)
            aid = assessment.id

        resp = await client.get(
            f"/assessments/{aid}",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "draft"
        assert data["generation_error"] is None

    @pytest.mark.asyncio
    async def test_published_assessment_has_published_at(
        self, client, teacher_token, ready_course_id
    ):
        """GET /assessments/{id} includes published_at for a published assessment."""
        import datetime as dt
        from db.models import Question

        published_time = dt.datetime(2026, 7, 8, 12, 0, 0, tzinfo=dt.timezone.utc)

        async with _TestSessionFactory() as db:
            from sqlalchemy import select as _select
            result = await db.execute(_select(Course).where(Course.id == ready_course_id))
            course = result.scalar_one()
            assessment = Assessment(
                course_id=ready_course_id,
                created_by=course.owner_id,
                title="Published",
                status="published",
                config="{}",
                published_at=published_time,
            )
            db.add(assessment)
            await db.flush()
            question = Question(
                assessment_id=assessment.id,
                question_type="mcq",
                stem="Q?",
                options=json.dumps(["A. a", "B. b", "C. c", "D. d"]),
                answer_key=json.dumps({"correct_answer": "A", "worked_solution": "A."}),
                bloom_level="remember",
                difficulty="easy",
                max_points=1.0,
                order_index=0,
            )
            db.add(question)
            await db.commit()
            await db.refresh(assessment)
            aid = assessment.id

        resp = await client.get(
            f"/assessments/{aid}",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "published"
        assert data["published_at"] is not None

    def test_sanitise_error_truncates_long_message(self):
        """_sanitise_error caps output at _MAX_ERROR_LEN characters."""
        from assessments.service import _sanitise_error, _MAX_ERROR_LEN
        long_exc = Exception("x" * 1000)
        result = _sanitise_error(long_exc)
        assert len(result) <= _MAX_ERROR_LEN + 1  # +1 for ellipsis char
        assert result.endswith("…")

    def test_sanitise_error_empty_message_replaced(self):
        """_sanitise_error replaces empty/None messages with a user-friendly default."""
        from assessments.service import _sanitise_error
        result = _sanitise_error(Exception(""))
        assert "unexpected error" in result.lower()

    def test_sanitise_error_none_str(self):
        from assessments.service import _sanitise_error
        result = _sanitise_error(Exception("none"))
        assert "unexpected error" in result.lower()


# ── Stabilization: DB model integrity checks ──────────────────────────────────

class TestModelIntegrity:
    """Structural checks on the ORM model — no DB connection required."""

    def test_assessment_has_generation_error_column(self):
        """Assessment ORM model must have a generation_error column."""
        from db.models import Assessment
        cols = {c.key for c in Assessment.__table__.columns}
        assert "generation_error" in cols

    def test_assessment_has_published_at_column(self):
        from db.models import Assessment
        cols = {c.key for c in Assessment.__table__.columns}
        assert "published_at" in cols

    def test_assessment_status_check_constraint_exists(self):
        """The ck_assessments_status CheckConstraint must be registered on the table."""
        from db.models import Assessment
        from sqlalchemy import CheckConstraint
        constraint_names = {
            c.name
            for c in Assessment.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert "ck_assessments_status" in constraint_names

    def test_assessment_status_constraint_covers_all_values(self):
        """The CheckConstraint SQL expression must include all four valid status values."""
        from db.models import Assessment
        from sqlalchemy import CheckConstraint
        for c in Assessment.__table__.constraints:
            if isinstance(c, CheckConstraint) and c.name == "ck_assessments_status":
                expr = str(c.sqltext)
                for val in ("generating", "draft", "published", "failed"):
                    assert val in expr, f"'{val}' missing from CheckConstraint: {expr}"
                return
        raise AssertionError("ck_assessments_status constraint not found")

    def test_assessment_default_status_is_generating(self):
        """Assessment.status default must be 'generating' (not 'draft')."""
        from db.models import Assessment
        status_col = Assessment.__table__.c["status"]
        assert status_col.default.arg == "generating"


# ── Route registration order (shadowing) ──────────────────────────────────────
#
# GET /assessments/{assessment_id} (require_teacher) was declared BEFORE the
# literal GET /assessments/published and GET /assessments/my-submissions
# (require_student). FastAPI matches in registration order, so the
# parameterized route won both literal paths and every student request got
# 403 "Teacher role required." — before the uuid.UUID coercion of "published"
# could even produce a 422.
#
# No test exercised either endpoint, which is how it reached manual testing.
# These go through the real ASGI app with a real student token, because a unit
# test of the handler function could never catch a registration-order bug.


class TestStudentAssessmentRoutesNotShadowed:
    """The student-facing literal routes must win over /{assessment_id}."""

    @pytest.mark.asyncio
    async def test_published_is_reachable_by_student(self, client, student_token):
        resp = await client.get(
            "/assessments/published",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_my_submissions_is_reachable_by_student(self, client, student_token):
        resp = await client.get(
            "/assessments/my-submissions",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_published_accepts_course_id_filter(
        self, client, student_token, ready_course_id
    ):
        """The live repro included the query-param form; it 403'd too."""
        resp = await client.get(
            f"/assessments/published?course_id={ready_course_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_my_submissions_accepts_course_id_filter(
        self, client, student_token, ready_course_id
    ):
        resp = await client.get(
            f"/assessments/my-submissions?course_id={ready_course_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_published_lists_only_published_assessments(
        self, client, student_token, teacher_token, ready_course_id
    ):
        """Beyond reachability: the handler that now runs is the right one."""
        async with _TestSessionFactory() as db:
            from sqlalchemy import select

            course = (
                await db.execute(select(Course).where(Course.id == ready_course_id))
            ).scalar_one()
            db.add_all([
                Assessment(
                    course_id=ready_course_id,
                    created_by=course.owner_id,
                    title="Draft one",
                    status="draft",
                    config="{}",
                ),
                Assessment(
                    course_id=ready_course_id,
                    created_by=course.owner_id,
                    title="Published one",
                    status="published",
                    config="{}",
                ),
            ])
            await db.commit()

        resp = await client.get(
            "/assessments/published",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 200, resp.text
        titles = [a["title"] for a in resp.json()]
        assert "Published one" in titles
        assert "Draft one" not in titles

    @pytest.mark.asyncio
    async def test_teacher_detail_route_still_works(
        self, client, teacher_token, ready_course_id
    ):
        """The reorder must not have broken the route it was moved above."""
        async with _TestSessionFactory() as db:
            from sqlalchemy import select

            course = (
                await db.execute(select(Course).where(Course.id == ready_course_id))
            ).scalar_one()
            a = Assessment(
                course_id=ready_course_id,
                created_by=course.owner_id,
                title="Detail me",
                status="draft",
                config="{}",
            )
            db.add(a)
            await db.commit()
            assessment_id = a.id

        resp = await client.get(
            f"/assessments/{assessment_id}",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "Detail me"

    @pytest.mark.asyncio
    async def test_published_still_denied_to_teacher(self, client, teacher_token):
        """
        Guards are untouched: /published is still student-only.

        Without this, moving the route above /{assessment_id} could be
        "fixed" by loosening a guard and the suite would not notice.
        """
        resp = await client.get(
            "/assessments/published",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        assert resp.status_code == 403

    def test_no_literal_route_is_shadowed_by_a_parameterized_one(self):
        """
        Generic guard: no literal path in the assessments router may be
        declared after a parameterized route that would swallow it.

        Catches the next occurrence of this bug for any route added later,
        rather than only the two that happened to break this time.
        """
        from assessments.router import router

        seen_params: list[tuple[frozenset, list[str]]] = []
        for route in router.routes:
            parts = [p for p in route.path.split("/") if p]
            methods = frozenset(route.methods - {"HEAD", "OPTIONS"})
            is_literal = not any(p.startswith("{") for p in parts)

            if is_literal:
                for prev_methods, prev_parts in seen_params:
                    if not (methods & prev_methods) or len(prev_parts) != len(parts):
                        continue
                    swallows = all(
                        prev.startswith("{") or prev == cur
                        for prev, cur in zip(prev_parts, parts)
                    )
                    assert not swallows, (
                        f"{sorted(methods)} {route.path} is shadowed by the earlier "
                        f"parameterized route {'/' + '/'.join(prev_parts)} — move the "
                        f"literal route above it."
                    )
            else:
                seen_params.append((methods, parts))


# ── Skipped-question submission + teacher question_count ──────────────────────

async def _seed_published_assessment(course_id, *, n_questions: int = 3):
    """Create a published assessment with n MCQ questions; return (aid, [qids])."""
    from sqlalchemy import select as _select

    from db.models import Question

    async with _TestSessionFactory() as db:
        result = await db.execute(_select(Course).where(Course.id == course_id))
        course = result.scalar_one()
        assessment = Assessment(
            course_id=course_id,
            created_by=course.owner_id,
            title="Skip Test",
            status="published",
            config="{}",
        )
        db.add(assessment)
        await db.flush()
        qids = []
        for i in range(n_questions):
            q = Question(
                assessment_id=assessment.id,
                question_type="mcq",
                stem=f"Q{i}?",
                options=json.dumps(["A. a", "B. b", "C. c", "D. d"]),
                answer_key=json.dumps({"correct_answer": "A", "worked_solution": "A."}),
                bloom_level="remember",
                difficulty="easy",
                max_points=1.0,
                order_index=i,
            )
            db.add(q)
            await db.flush()
            qids.append(q.id)
        await db.commit()
        return assessment.id, qids


@pytest.mark.asyncio
async def test_submit_with_unanswered_question_succeeds(
    client, student_token, ready_course_id
):
    """
    A student who skips a question must still be able to submit.

    The frontend sends one item per question, with both answer fields null for
    the untouched one. Previously the at_least_one_answer validator rejected
    that item and failed the entire request with 422.
    """
    aid, qids = await _seed_published_assessment(ready_course_id, n_questions=3)

    payload = {"responses": [
        {"question_id": str(qids[0]), "answer_choice": "A"},
        {"question_id": str(qids[1]), "answer_text": None, "answer_choice": None},
        {"question_id": str(qids[2]), "answer_choice": "B"},
    ]}
    resp = await client.post(
        f"/assessments/{aid}/submit",
        json=payload,
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 201, resp.text

    # The skipped question is persisted as a row with both columns NULL —
    # not dropped, so grading still sees every question.
    from sqlalchemy import select as _select

    from db.models import Submission, SubmissionResponse

    async with _TestSessionFactory() as db:
        submission = (
            await db.execute(_select(Submission).where(Submission.assessment_id == aid))
        ).scalar_one()
        rows = (
            await db.execute(
                _select(SubmissionResponse).where(
                    SubmissionResponse.submission_id == submission.id
                )
            )
        ).scalars().all()
    assert len(rows) == 3
    blank = [r for r in rows if r.question_id == qids[1]]
    assert len(blank) == 1
    assert blank[0].answer_text is None
    assert blank[0].answer_choice is None


@pytest.mark.asyncio
async def test_submit_all_questions_unanswered_succeeds(
    client, student_token, ready_course_id
):
    """An entirely blank submission is still a submission (scored 0 later)."""
    aid, qids = await _seed_published_assessment(ready_course_id, n_questions=2)
    payload = {"responses": [{"question_id": str(q)} for q in qids]}
    resp = await client.post(
        f"/assessments/{aid}/submit",
        json=payload,
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_submit_invalid_choice_still_rejected_with_string_detail(
    client, student_token, ready_course_id
):
    """
    Relaxing the blank-answer rule must not relax real validation — and the 422
    body must carry a string `detail` the frontend can display.
    """
    aid, qids = await _seed_published_assessment(ready_course_id, n_questions=1)
    resp = await client.post(
        f"/assessments/{aid}/submit",
        json={"responses": [{"question_id": str(qids[0]), "answer_choice": "Z"}]},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "validation_error"
    assert isinstance(body["detail"], str)
    assert "A, B, C, D" in body["detail"]


@pytest.mark.asyncio
async def test_teacher_list_reports_real_question_count(
    client, teacher_token, ready_course_id
):
    """
    GET /assessments/course/{id} must report the true question count.

    It previously hardcoded 0 to avoid eager-loading questions; the count now
    comes from a correlated COUNT subquery, so no N+1 is reintroduced.
    """
    aid, qids = await _seed_published_assessment(ready_course_id, n_questions=3)

    resp = await client.get(
        f"/assessments/course/{ready_course_id}",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(aid)
    assert data[0]["question_count"] == 3

    # ...and it must agree with the detail endpoint for the same assessment.
    detail = await client.get(
        f"/assessments/{aid}",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert len(detail.json()["questions"]) == 3


@pytest.mark.asyncio
async def test_teacher_list_question_count_zero_for_empty_assessment(
    client, teacher_token, ready_course_id
):
    """An assessment still generating has no questions — count must be 0, not null."""
    async with _TestSessionFactory() as db:
        from sqlalchemy import select as _select
        course = (
            await db.execute(_select(Course).where(Course.id == ready_course_id))
        ).scalar_one()
        db.add(Assessment(
            course_id=ready_course_id,
            created_by=course.owner_id,
            title="Generating",
            status="generating",
            config="{}",
        ))
        await db.commit()

    resp = await client.get(
        f"/assessments/course/{ready_course_id}",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["question_count"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Fix 1 - GET /assessments/{id}/take: students can open a published assessment
# Fix 2 - submitting auto-enqueues AI grading (recommendation only; the teacher
#         approve/override gate is untouched)
# ══════════════════════════════════════════════════════════════════════════════

_ANSWER_LEAK_MARKERS = (
    "answer_key",
    "correct_answer",
    "worked_solution",
    "bloom_level",   # authoring metadata, deliberately not in the student view
    "difficulty",
    "generation_error",
)


async def _seed_assessment(
    course_id,
    *,
    status: str = "published",
    owner_id=None,
    title: str = "Take Test",
):
    """Create an assessment with one MCQ + one short-answer question (with rubric)."""
    from sqlalchemy import select as _select

    from db.models import Question, RubricCriterion

    async with _TestSessionFactory() as db:
        if owner_id is None:
            course = (
                await db.execute(_select(Course).where(Course.id == course_id))
            ).scalar_one()
            owner_id = course.owner_id

        assessment = Assessment(
            course_id=course_id,
            created_by=owner_id,
            title=title,
            status=status,
            config="{}",
        )
        db.add(assessment)
        await db.flush()

        mcq = Question(
            assessment_id=assessment.id,
            question_type="mcq",
            stem="Which organelle performs photosynthesis?",
            options=json.dumps(["A. Chloroplast", "B. Nucleus", "C. Ribosome", "D. Golgi"]),
            answer_key=json.dumps(
                {"correct_answer": "A", "worked_solution": "Chloroplasts contain chlorophyll."}
            ),
            bloom_level="remember",
            difficulty="easy",
            max_points=1.0,
            order_index=0,
        )
        short = Question(
            assessment_id=assessment.id,
            question_type="short_answer",
            stem="Explain the role of chlorophyll.",
            options=None,
            answer_key=json.dumps(
                {"correct_answer": "It absorbs light energy.", "worked_solution": "Pigment."}
            ),
            bloom_level="understand",
            difficulty="medium",
            max_points=2.0,
            order_index=1,
        )
        db.add_all([mcq, short])
        await db.flush()
        db.add(
            RubricCriterion(
                question_id=short.id,
                description="Mentions chlorophyll as the light-absorbing pigment",
                max_points=2.0,
                order_index=0,
            )
        )
        await db.commit()
        return assessment.id, [mcq.id, short.id]


# ── Fix 1: the take endpoint ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_student_can_fetch_published_assessment_to_take_it(
    client, student_token, ready_course_id
):
    """
    The headline fix: the take page's fetch used to hit the teacher-only
    GET /assessments/{id}, get 403, and render "Assessment Not Found".
    """
    aid, qids = await _seed_assessment(ready_course_id)

    resp = await client.get(
        f"/assessments/{aid}/take",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == str(aid)
    assert data["question_count"] == 2
    assert len(data["questions"]) == 2

    # Everything the take UI needs in order to render an answerable question.
    mcq = data["questions"][0]
    assert mcq["question_type"] == "mcq"
    assert mcq["stem"]
    assert mcq["options"] == [
        "A. Chloroplast",
        "B. Nucleus",
        "C. Ribosome",
        "D. Golgi",
    ]
    assert data["questions"][1]["question_type"] == "short_answer"
    assert data["questions"][1]["options"] is None


@pytest.mark.asyncio
async def test_take_response_contains_no_answer_key_anywhere(
    client, student_token, ready_course_id
):
    """
    Scanned over the raw response body, not field by field: a nested leak
    anywhere in the payload has to fail this, including one introduced later by
    a field added to an ORM model.
    """
    await _seed_assessment(ready_course_id)
    aid, _ = await _seed_assessment(ready_course_id, title="Leak Check")

    resp = await client.get(
        f"/assessments/{aid}/take",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200
    body = resp.text.lower()

    for marker in _ANSWER_LEAK_MARKERS:
        assert marker not in body, f"{marker!r} leaked into the student take payload"
    # The actual answer text, not just the field name.
    assert "chloroplasts contain chlorophyll" not in body
    assert "it absorbs light energy" not in body
    # ...while the answer-bearing key really is set on the seeded rows, so this
    # test would notice if the seed stopped exercising the risk.
    assert "chloroplast" in body  # present only as an MCQ option


@pytest.mark.asyncio
async def test_take_exposes_rubric_descriptions_but_no_answers(
    client, student_token, ready_course_id
):
    """
    Rubric criteria describe what the answer is judged on, which is what a
    rubric is for. The correct answer lives in Question.answer_key and is not
    modelled by any student schema.
    """
    aid, _ = await _seed_assessment(ready_course_id)

    resp = await client.get(
        f"/assessments/{aid}/take",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    criteria = resp.json()["questions"][1]["rubric_criteria"]
    assert len(criteria) == 1
    assert criteria[0]["description"] == (
        "Mentions chlorophyll as the light-absorbing pigment"
    )
    assert criteria[0]["max_points"] == 2.0
    assert "correct_answer" not in criteria[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "generating", "failed"])
async def test_student_cannot_take_an_unpublished_assessment(
    client, student_token, ready_course_id, status
):
    """
    404, not 403: a non-published assessment must not be reachable by guessing
    an id, and the response must not confirm that one with that id exists.
    Same status and wording the submit handler already uses.
    """
    aid, _ = await _seed_assessment(ready_course_id, status=status)

    resp = await client.get(
        f"/assessments/{aid}/take",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Assessment not found or not published."


@pytest.mark.asyncio
async def test_take_unknown_id_is_404(client, student_token):
    resp = await client.get(
        f"/assessments/{uuid.uuid4()}/take",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_take_rejects_a_teacher(client, teacher_token, ready_course_id):
    """require_student: this route must never serve the teacher-shaped view."""
    aid, _ = await _seed_assessment(ready_course_id)

    resp = await client.get(
        f"/assessments/{aid}/take",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_take_requires_auth(client, ready_course_id):
    aid, _ = await _seed_assessment(ready_course_id)
    resp = await client.get(f"/assessments/{aid}/take")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_teacher_detail_endpoint_is_still_owner_scoped(
    client, teacher_token, ready_course_id
):
    """
    Adding the student route must not have loosened the teacher one: an
    assessment on another teacher's course still reads as not found.
    """
    from sqlalchemy import select as _select

    async with _TestSessionFactory() as db:
        other_teacher = User(
            email="other-teacher@demo.com",
            hashed_password=hash_password("password123"),
            role="teacher",
        )
        db.add(other_teacher)
        await db.flush()
        other_course = Course(
            owner_id=other_teacher.id, name="Someone Else's Course", status="ready"
        )
        db.add(other_course)
        await db.commit()
        other_course_id = other_course.id
        other_owner_id = other_teacher.id

    foreign_aid, _ = await _seed_assessment(
        other_course_id, owner_id=other_owner_id, title="Not Yours"
    )

    resp = await client.get(
        f"/assessments/{foreign_aid}",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 404

    # A student may still take it once published - published assessments are
    # not owner-scoped for students, matching GET /assessments/published.
    async with _TestSessionFactory() as db:
        a = (
            await db.execute(_select(Assessment).where(Assessment.id == foreign_aid))
        ).scalar_one()
        assert a.status == "published"


# ── Fix 2: submitting auto-enqueues grading ───────────────────────────────────


@pytest.mark.asyncio
async def test_submission_enqueues_grading_without_a_manual_trigger(
    client, student_token, ready_course_id
):
    """
    Nothing used to call POST /grading/{id}/grade, so every submission sat at
    pending_grading forever.

    Asserted the way this suite already tests the ingestion and
    assessment-generation background tasks (see tests/conftest.py
    ``stub_background_jobs``): the orchestrator entrypoint is stubbed and the
    assertion is that it ran with the right arguments. The real one opens the
    production AsyncSessionFactory, which must not be reached from a test.
    """
    aid, qids = await _seed_assessment(ready_course_id)

    with patch(
        "grading.service._run_grading_graph", new_callable=AsyncMock
    ) as run_graph:
        resp = await client.post(
            f"/assessments/{aid}/submit",
            json={"responses": [{"question_id": str(qids[0]), "answer_choice": "A"}]},
            headers={"Authorization": f"Bearer {student_token}"},
        )

    assert resp.status_code == 201, resp.text
    submission_id = uuid.UUID(resp.json()["submission_id"])

    run_graph.assert_awaited_once()
    kwargs = run_graph.await_args.kwargs
    assert kwargs["submission_id"] == submission_id
    assert kwargs["course_id"] == ready_course_id


@pytest.mark.asyncio
async def test_submission_produces_a_grade_recommendation(
    client, student_token, ready_course_id
):
    """
    The end the user actually cares about: after a submit and with no manual
    API call, a GradeRecommendation exists for that submission.

    The grading graph itself is stubbed (it needs a real LLM and the production
    session factory); the stub writes the recommendation the real
    persist_recommendation_node would. What this proves is the wiring — the
    task is scheduled, it runs, and the arguments it receives are enough to
    identify the submission being graded.
    """
    from db.models import GradeRecommendation

    aid, qids = await _seed_assessment(ready_course_id)

    async def _fake_graph(submission_id, course_id, app_state):
        async with _TestSessionFactory() as db:
            db.add(
                GradeRecommendation(
                    submission_id=submission_id,
                    status="pending_review",
                    recommended_score=1.0,
                    max_score=3.0,
                    rationale=json.dumps({"grading_method": "deterministic_exact_match"}),
                )
            )
            await db.commit()

    with patch("grading.service._run_grading_graph", side_effect=_fake_graph):
        resp = await client.post(
            f"/assessments/{aid}/submit",
            json={"responses": [{"question_id": str(qids[0]), "answer_choice": "A"}]},
            headers={"Authorization": f"Bearer {student_token}"},
        )
    assert resp.status_code == 201
    submission_id = uuid.UUID(resp.json()["submission_id"])

    from sqlalchemy import select as _select

    async with _TestSessionFactory() as db:
        rec = (
            await db.execute(
                _select(GradeRecommendation).where(
                    GradeRecommendation.submission_id == submission_id
                )
            )
        ).scalar_one_or_none()

    assert rec is not None, "no recommendation was produced by the automatic trigger"
    assert rec.status == "pending_review"

    # Human-in-the-loop is untouched: a recommendation is NOT a grade.
    from db.models import FinalGrade

    async with _TestSessionFactory() as db:
        finals = (await db.execute(_select(FinalGrade))).scalars().all()
    assert finals == [], "auto-grading must never write a FinalGrade"


@pytest.mark.asyncio
async def test_submission_with_a_skipped_question_still_enqueues_grading(
    client, student_token, ready_course_id
):
    """The skipped-answer path and the auto-grading path must compose."""
    aid, qids = await _seed_assessment(ready_course_id)

    with patch(
        "grading.service._run_grading_graph", new_callable=AsyncMock
    ) as run_graph:
        resp = await client.post(
            f"/assessments/{aid}/submit",
            json={
                "responses": [
                    {"question_id": str(qids[0]), "answer_choice": "A"},
                    {"question_id": str(qids[1])},  # skipped
                ]
            },
            headers={"Authorization": f"Bearer {student_token}"},
        )

    assert resp.status_code == 201, resp.text
    run_graph.assert_awaited_once()


@pytest.mark.asyncio
async def test_submission_reuses_trigger_grading_rather_than_duplicating_it(
    client, student_token, ready_course_id
):
    """
    The auto path and POST /grading/{id}/grade must share one set of guards
    (already-graded, unpublished assessment, duplicate recommendation) so the
    two cannot drift apart.
    """
    aid, qids = await _seed_assessment(ready_course_id)

    with patch(
        "assessments.router.trigger_grading", new_callable=AsyncMock
    ) as trigger:
        resp = await client.post(
            f"/assessments/{aid}/submit",
            json={"responses": [{"question_id": str(qids[0]), "answer_choice": "A"}]},
            headers={"Authorization": f"Bearer {student_token}"},
        )

    assert resp.status_code == 201
    trigger.assert_awaited_once()
    kwargs = trigger.await_args.kwargs
    assert kwargs["submission_id"] == uuid.UUID(resp.json()["submission_id"])
    # The background task must open its own session, so what is handed over is
    # the app state, not anything request-scoped beyond the validation session.
    assert "app_state" in kwargs and "background_tasks" in kwargs


@pytest.mark.asyncio
async def test_submission_survives_a_grading_enqueue_failure(
    client, student_token, ready_course_id
):
    """
    The submission is the student's work and is already committed. Losing it
    because the grading queue rejected it would be far worse than a
    recommendation a teacher can re-trigger.
    """
    from sqlalchemy import select as _select

    from db.models import Submission, SubmissionResponse

    aid, qids = await _seed_assessment(ready_course_id)

    with patch(
        "assessments.router.trigger_grading",
        new_callable=AsyncMock,
        side_effect=RuntimeError("grading queue is down"),
    ):
        resp = await client.post(
            f"/assessments/{aid}/submit",
            json={"responses": [{"question_id": str(qids[0]), "answer_choice": "A"}]},
            headers={"Authorization": f"Bearer {student_token}"},
        )

    assert resp.status_code == 201, "a grading failure must not fail the submission"
    submission_id = uuid.UUID(resp.json()["submission_id"])

    async with _TestSessionFactory() as db:
        submission = (
            await db.execute(_select(Submission).where(Submission.id == submission_id))
        ).scalar_one()
        responses = (
            await db.execute(
                _select(SubmissionResponse).where(
                    SubmissionResponse.submission_id == submission_id
                )
            )
        ).scalars().all()

    # Left retry-able, exactly where the manual grade endpoint expects it.
    assert submission.status == "pending_grading"
    assert len(responses) == 1


@pytest.mark.asyncio
async def test_duplicate_submission_does_not_enqueue_a_second_grading_run(
    client, student_token, ready_course_id
):
    """The 409 path must not queue work for a submission it did not create."""
    aid, qids = await _seed_assessment(ready_course_id)
    payload = {"responses": [{"question_id": str(qids[0]), "answer_choice": "A"}]}
    headers = {"Authorization": f"Bearer {student_token}"}

    with patch("grading.service._run_grading_graph", new_callable=AsyncMock):
        first = await client.post(f"/assessments/{aid}/submit", json=payload, headers=headers)
    assert first.status_code == 201

    with patch(
        "grading.service._run_grading_graph", new_callable=AsyncMock
    ) as run_graph:
        second = await client.post(f"/assessments/{aid}/submit", json=payload, headers=headers)

    assert second.status_code == 409
    run_graph.assert_not_awaited()
