"""
tests/test_courses.py
---------------------
Course-upload endpoint tests (part of F11; also completes the endpoint-level
upload cap coverage deferred from PR-13 / F15).

The ingestion background task is stubbed by conftest (stub_background_jobs), so
these tests exercise only the request/validation/authz path.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import settings
from core.database import Base
from core.dependencies import get_db_session
from core.security import hash_password
from db.models import User
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
    app.dependency_overrides[get_db_session] = _override_db
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as db:
        db.add(User(email="teacher@demo.com", hashed_password=hash_password("password123"), role="teacher"))
        db.add(User(email="student@demo.com", hashed_password=hash_password("password123"), role="student"))
        await db.commit()
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _token(client, email: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "password123"})
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_upload_succeeds_for_teacher(client):
    token = await _token(client, "teacher@demo.com")
    resp = await client.post(
        "/courses/upload",
        data={"name": "Biology 101"},
        files={"files": ("notes.txt", b"course content", "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert "course_id" in body and "job_id" in body


async def test_upload_rejects_unsupported_extension(client):
    token = await _token(client, "teacher@demo.com")
    resp = await client.post(
        "/courses/upload",
        data={"name": "C"},
        files={"files": ("malware.exe", b"x", "application/octet-stream")},
        headers=_auth(token),
    )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


async def test_upload_rejects_too_many_files(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_files", 2)
    token = await _token(client, "teacher@demo.com")
    files = [("files", (f"f{i}.txt", b"data", "text/plain")) for i in range(3)]
    resp = await client.post(
        "/courses/upload", data={"name": "C"}, files=files, headers=_auth(token)
    )
    assert resp.status_code == 400
    assert "Too many files" in resp.json()["detail"]


async def test_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 100)  # tiny cap for the test
    token = await _token(client, "teacher@demo.com")
    resp = await client.post(
        "/courses/upload",
        data={"name": "C"},
        files={"files": ("big.txt", b"x" * 200, "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code == 413
    assert "exceeds the maximum upload size" in resp.json()["detail"]


async def test_upload_requires_teacher_role(client):
    token = await _token(client, "student@demo.com")
    resp = await client.post(
        "/courses/upload",
        data={"name": "C"},
        files={"files": ("notes.txt", b"content", "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code in {401, 403}


async def test_upload_requires_auth(client):
    resp = await client.post(
        "/courses/upload",
        data={"name": "C"},
        files={"files": ("notes.txt", b"content", "text/plain")},
    )
    assert resp.status_code == 401
