"""
tests/test_auth.py
------------------
Unit and integration tests for the auth package.
Uses httpx AsyncClient + SQLite in-memory DB (no PostgreSQL required).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import hash_password
from db.models import User
from main import app

# ── Test database (SQLite in-memory) ─────────────────────────────────────────

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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables and seed demo users before each test; drop after."""
    # Re-establish the DB override on every test. Registering it only at module
    # import (as before) was fragile: other test files' client fixtures call
    # app.dependency_overrides.clear() on teardown, which wiped this override and
    # made later auth tests hit the real Postgres engine (asyncpg loop crash).
    app.dependency_overrides[get_db_session] = override_get_db_session

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _TestSessionFactory() as db:
        db.add(User(
            email="teacher@demo.com",
            hashed_password=hash_password("password123"),
            role="teacher",
        ))
        db.add(User(
            email="student@demo.com",
            hashed_password=hash_password("password123"),
            role="student",
        ))
        await db.commit()

    yield

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── POST /auth/login ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_teacher_success(client):
    resp = await client.post("/auth/login", json={
        "email": "teacher@demo.com",
        "password": "password123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["role"] == "teacher"


@pytest.mark.asyncio
async def test_login_student_success(client):
    resp = await client.post("/auth/login", json={
        "email": "student@demo.com",
        "password": "password123",
    })
    assert resp.status_code == 200
    assert resp.json()["role"] == "student"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    resp = await client.post("/auth/login", json={
        "email": "teacher@demo.com",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(client):
    resp = await client.post("/auth/login", json={
        "email": "nobody@example.com",
        "password": "password123",
    })
    assert resp.status_code == 401


# ── GET /auth/me ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_with_valid_token(client):
    # Login first
    login_resp = await client.post("/auth/login", json={
        "email": "teacher@demo.com",
        "password": "password123",
    })
    token = login_resp.json()["access_token"]

    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    data = me_resp.json()
    assert data["email"] == "teacher@demo.com"
    assert data["role"] == "teacher"


@pytest.mark.asyncio
async def test_me_without_token(client):
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_invalid_token(client):
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.valid.token"})
    assert resp.status_code == 401


# ── Role guard ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_teacher_token_has_correct_role(client):
    resp = await client.post("/auth/login", json={
        "email": "teacher@demo.com", "password": "password123"
    })
    assert resp.json()["role"] == "teacher"


@pytest.mark.asyncio
async def test_student_token_has_correct_role(client):
    resp = await client.post("/auth/login", json={
        "email": "student@demo.com", "password": "password123"
    })
    assert resp.json()["role"] == "student"
