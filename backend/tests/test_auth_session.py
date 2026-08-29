"""
tests/test_auth_session.py
--------------------------
Tests for the session foundation (F19): refresh tokens, rotation, revocation,
logout, and access/refresh token-type separation.
"""

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import decode_token
from core.token_store import RevokedTokenStore, revoked_tokens
from db.models import User
from core.security import hash_password
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
    revoked_tokens.clear()  # module-global store must not leak across tests
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as db:
        db.add(User(email="teacher@demo.com", hashed_password=hash_password("password123"), role="teacher"))
        await db.commit()
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.pop(get_db_session, None)
    revoked_tokens.clear()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _login(client) -> dict:
    resp = await client.post("/auth/login", json={"email": "teacher@demo.com", "password": "password123"})
    assert resp.status_code == 200
    return resp.json()


# ── Revocation store (unit) ───────────────────────────────────────────────────

def test_store_revoke_and_check():
    store = RevokedTokenStore()
    store.revoke("jti-1", expires_at=time.time() + 100)
    assert store.is_revoked("jti-1") is True
    assert store.is_revoked("jti-2") is False


def test_store_prunes_expired_entries():
    store = RevokedTokenStore()
    store.revoke("old", expires_at=time.time() - 1)  # already expired
    assert store.is_revoked("old") is False  # pruned, not reported as revoked


# ── Login issues a token pair ─────────────────────────────────────────────────

async def test_login_returns_access_and_refresh(client):
    body = await _login(client)
    assert body["access_token"] and body["refresh_token"]
    assert decode_token(body["access_token"]).type == "access"
    refresh_payload = decode_token(body["refresh_token"])
    assert refresh_payload.type == "refresh"
    assert refresh_payload.jti


# ── Refresh + rotation ────────────────────────────────────────────────────────

async def test_refresh_returns_new_pair_and_rotates(client):
    old = await _login(client)
    resp = await client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]})
    assert resp.status_code == 200
    new = resp.json()
    assert new["access_token"] and new["refresh_token"]
    assert new["refresh_token"] != old["refresh_token"]

    # The old refresh token was rotated out — it can no longer be used.
    reuse = await client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]})
    assert reuse.status_code == 401


async def test_refresh_rejects_access_token(client):
    body = await _login(client)
    resp = await client.post("/auth/refresh", json={"refresh_token": body["access_token"]})
    assert resp.status_code == 401


async def test_refresh_rejects_garbage(client):
    resp = await client.post("/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert resp.status_code == 401


# ── Token-type separation on /auth/me ─────────────────────────────────────────

async def test_refresh_token_cannot_be_used_as_bearer(client):
    body = await _login(client)
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['refresh_token']}"})
    assert resp.status_code == 401


async def test_access_token_works_as_bearer(client):
    body = await _login(client)
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "teacher@demo.com"


# ── Logout revokes ────────────────────────────────────────────────────────────

async def test_logout_revokes_refresh_token(client):
    body = await _login(client)
    logout = await client.post("/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert logout.status_code == 200

    resp = await client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 401
