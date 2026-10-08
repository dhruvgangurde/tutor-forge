"""
core/token_store.py
-------------------
Persistent JWT revocation (F19, audit 2026-10-06 #4).

A revoked token's ``jti`` is stored in the ``revoked_tokens`` table until the
moment the token would have expired anyway; expired rows are purged whenever a
new revocation is written, so the table stays bounded.

Previously this was an in-memory set: lost on every restart, not shared between
workers, and holding only refresh tokens -- so logging out never ended a
session, because the access token kept working until it expired. Both token
types now carry a jti and are checked here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RevokedToken


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def revoke(db: AsyncSession, jti: str, expires_at: datetime) -> None:
    """Revoke ``jti`` until ``expires_at`` (idempotent), purging expired rows."""
    await purge_expired(db)
    await db.merge(RevokedToken(jti=jti, expires_at=_utc(expires_at)))
    await db.commit()


async def is_revoked(db: AsyncSession, jti: str) -> bool:
    row = await db.execute(select(RevokedToken.jti).where(RevokedToken.jti == jti))
    return row.first() is not None


async def purge_expired(db: AsyncSession) -> int:
    """Delete revocations whose token has expired anyway. Returns rows removed."""
    result = await db.execute(
        delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(timezone.utc))
    )
    return result.rowcount or 0
