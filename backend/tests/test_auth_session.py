"""
tests/test_auth_session.py
--------------------------
Tests for the session foundation (F19): refresh tokens, rotation, revocation,
logout, and access/refresh token-type separation.

Revocation is persisted in the revoked_tokens table (audit 2026-10-06 #4):
logout now ends the session -- the access token is refused afterwards -- and a
revocation survives a restart.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import Base
from core.dependencies import get_db_session
from core.security import decode_token
from core import token_store
from db.models import RevokedToken, User
from core.security import hash_password
from main import app, create_app
from sqlalchemy import select

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
    # Revocations live in the per-test database, so nothing leaks across tests.
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


async def _login(client, email: str = "teacher@demo.com") -> dict:
    resp = await client.post("/auth/login", json={"email": email, "password": "password123"})
    assert resp.status_code == 200
    return resp.json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Revocation store (unit, persisted) ────────────────────────────────────────

async def test_store_revoke_and_check():
    now = datetime.now(timezone.utc)
    async with _Session() as db:
        await token_store.revoke(db, "jti-1", now + timedelta(seconds=100))
        assert await token_store.is_revoked(db, "jti-1") is True
        assert await token_store.is_revoked(db, "jti-2") is False


async def test_store_prunes_expired_entries():
    now = datetime.now(timezone.utc)
    async with _Session() as db:
        await token_store.revoke(db, "old", now - timedelta(seconds=1))  # already expired
        await token_store.revoke(db, "new", now + timedelta(seconds=100))  # write triggers the purge
        assert await token_store.is_revoked(db, "old") is False  # pruned, not reported as revoked
        rows = (await db.execute(select(RevokedToken.jti))).scalars().all()
    assert rows == ["new"]


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


# ── Logout ends the session (audit 2026-10-06 #4) ─────────────────────────────

async def test_access_token_carries_a_jti(client):
    body = await _login(client)
    assert decode_token(body["access_token"]).jti


async def test_logout_revokes_the_access_token(client):
    body = await _login(client)
    token = body["access_token"]
    assert (await client.get("/auth/me", headers=_bearer(token))).status_code == 200

    logout = await client.post("/auth/logout", headers=_bearer(token))
    assert logout.status_code == 200

    # Same, still-unexpired token: refused from now on.
    assert (await client.get("/auth/me", headers=_bearer(token))).status_code == 401


async def test_logout_revokes_both_tokens_when_the_refresh_token_is_sent(client):
    body = await _login(client)
    await client.post(
        "/auth/logout", headers=_bearer(body["access_token"]),
        json={"refresh_token": body["refresh_token"]},
    )
    assert (await client.get("/auth/me", headers=_bearer(body["access_token"]))).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})).status_code == 401


async def test_logout_does_not_affect_other_sessions(client):
    teacher = await _login(client)
    student = await _login(client, "student@demo.com")
    teacher_other_device = await _login(client)

    await client.post("/auth/logout", headers=_bearer(teacher["access_token"]))

    assert (await client.get("/auth/me", headers=_bearer(student["access_token"]))).status_code == 200
    # The same user's other session (another device) keeps working too.
    assert (await client.get("/auth/me", headers=_bearer(teacher_other_device["access_token"]))).status_code == 200


async def test_logout_is_idempotent_and_needs_no_token(client):
    assert (await client.post("/auth/logout")).status_code == 200
    assert (await client.post("/auth/logout", headers=_bearer("garbage"))).status_code == 200
    body = await _login(client)
    for _ in range(2):
        assert (await client.post("/auth/logout", headers=_bearer(body["access_token"]))).status_code == 200


async def test_revocation_survives_a_fresh_app_instance(client):
    body = await _login(client)
    await client.post("/auth/logout", headers=_bearer(body["access_token"]))

    # A brand-new application object -- what a restart produces. Nothing about
    # the revocation lives in process memory any more, only in the database.
    fresh = create_app()
    fresh.dependency_overrides[get_db_session] = _override_db
    async with AsyncClient(transport=ASGITransport(app=fresh), base_url="http://test") as restarted:
        resp = await restarted.get("/auth/me", headers=_bearer(body["access_token"]))
    assert resp.status_code == 401

    async with _Session() as db:
        stored = (await db.execute(select(RevokedToken))).scalars().all()
    assert [r.jti for r in stored] == [decode_token(body["access_token"]).jti]
