"""
tests/test_course_failure_reason.py
-----------------------------------
A failed course says why (frontend audit #11).

The ingestion job's raw error is kept in the database for debugging; the
teacher's course list and course detail now carry a one-sentence,
plain-language ``failure_reason`` derived from it -- and only for failed
courses.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import hash_password
from courses.failure import failure_reason
from db.models import Course, IngestionJob, User
from main import app

RAW_PARSE_ERROR = "Hierarchy JSON parse error: Expecting ',' delimiter: line 1 column 312 (char 311)"


@pytest.mark.parametrize(
    "stored, expected_start",
    [
        ("Unsupported file type: application/x-msdownload for notes.txt", "One of the files is not a supported type"),
        ("File too large: deck.pptx exceeds 50 MB", "One of the files is larger than"),
        (RAW_PARSE_ERROR, "The course outline could not be worked out"),
        ("Ingestion did not complete — the worker was interrupted.", "Processing was interrupted"),
        ("PdfminerException: unexpected EOF", "Processing the course materials failed"),
        (None, "Processing the course materials failed"),
    ],
)
def test_stored_errors_become_plain_sentences(stored, expected_start):
    reason = failure_reason(stored)
    assert reason.startswith(expected_start)
    assert "JSON" not in reason and "Exception" not in reason


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_db():
    async with _Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture(autouse=True)
async def setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as db:
        teacher = User(email="teacher@demo.com", hashed_password=hash_password("password123"), role="teacher")
        db.add(teacher)
        await db.flush()
        failed = Course(name="Broken Upload", owner_id=teacher.id, status="failed")
        ready = Course(name="Working Course", owner_id=teacher.id, status="ready")
        db.add_all([failed, ready])
        await db.flush()
        db.add_all(
            [
                IngestionJob(course_id=failed.id, status="failed", error_message=RAW_PARSE_ERROR),
                IngestionJob(course_id=ready.id, status="complete"),
            ]
        )
        await db.commit()
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    app.dependency_overrides[get_db_session] = _override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/auth/login", json={"email": "teacher@demo.com", "password": "password123"})
        ac.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield ac
    app.dependency_overrides.clear()


async def _course_id(name: str):
    async with _Session() as db:
        return (await db.execute(select(Course.id).where(Course.name == name))).scalar_one()


async def test_course_list_gives_the_reason_for_a_failed_course_only(client):
    resp = await client.get("/courses")
    assert resp.status_code == 200
    by_name = {c["name"]: c for c in resp.json()}
    assert by_name["Broken Upload"]["failure_reason"].startswith("The course outline could not be worked out")
    assert by_name["Working Course"]["failure_reason"] is None
    assert "JSON" not in resp.text  # the raw log text is not exposed


async def test_course_detail_gives_the_reason(client):
    resp = await client.get(f"/courses/{await _course_id('Broken Upload')}")
    assert resp.status_code == 200
    assert resp.json()["failure_reason"].startswith("The course outline could not be worked out")


async def test_ready_course_detail_has_no_reason(client):
    resp = await client.get(f"/courses/{await _course_id('Working Course')}")
    assert resp.json()["failure_reason"] is None
