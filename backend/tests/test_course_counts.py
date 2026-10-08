"""
tests/test_course_counts.py
---------------------------
GET /courses/{id} reports the real size of the course outline.

chapter_count and concept_count were hard-coded to 0 for every course, so a
course with seven chapters reported "0 chapters". They are now counted from
the stored chapters and concepts, and are null for a course with no outline
(still ingesting, or failed) instead of a misleading 0.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import hash_password
from db.models import Chapter, Concept, Course, User
from main import app

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
        ready = Course(name="Three Chapters", owner_id=teacher.id, status="ready")
        other = Course(name="Other Ready", owner_id=teacher.id, status="ready")
        empty = Course(name="Ready Without Outline", owner_id=teacher.id, status="ready")
        failed = Course(name="Broken Upload", owner_id=teacher.id, status="failed")
        ingesting = Course(name="Still Ingesting", owner_id=teacher.id, status="ingesting")
        db.add_all([ready, other, empty, failed, ingesting])
        await db.flush()
        # 3 chapters with 2 + 1 + 0 concepts in the course under test...
        for i, n_concepts in enumerate((2, 1, 0)):
            ch = Chapter(course_id=ready.id, title=f"Chapter {i + 1}", order_index=i)
            db.add(ch)
            await db.flush()
            db.add_all(Concept(chapter_id=ch.id, name=f"Concept {i}.{j}", order_index=j) for j in range(n_concepts))
        # ...and another course's outline, which must not be counted in.
        other_ch = Chapter(course_id=other.id, title="Elsewhere", order_index=0)
        db.add(other_ch)
        await db.flush()
        db.add(Concept(chapter_id=other_ch.id, name="Not mine", order_index=0))
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


async def _detail(client, name: str) -> dict:
    async with _Session() as db:
        cid = (await db.execute(select(Course.id).where(Course.name == name))).scalar_one()
    resp = await client.get(f"/courses/{cid}")
    assert resp.status_code == 200
    return resp.json()


async def test_ready_course_reports_its_real_chapter_and_concept_counts(client):
    body = await _detail(client, "Three Chapters")
    assert (body["chapter_count"], body["concept_count"]) == (3, 3)


async def test_ready_course_with_no_outline_reports_zero(client):
    body = await _detail(client, "Ready Without Outline")
    assert (body["chapter_count"], body["concept_count"]) == (0, 0)


@pytest.mark.parametrize("name", ["Broken Upload", "Still Ingesting"])
async def test_course_without_an_outline_reports_no_count(client, name):
    # Not 0: that would read as a fact about the course rather than "unknown".
    body = await _detail(client, name)
    assert body["chapter_count"] is None
    assert body["concept_count"] is None
