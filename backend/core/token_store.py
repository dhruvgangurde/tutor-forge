"""
core/token_store.py
-------------------
In-memory refresh-token revocation store (F19).

Tracks the ``jti`` of revoked refresh tokens until their natural expiry, so a
rotated or logged-out refresh token can no longer be used. Auto-prunes expired
entries so the set stays bounded.

Single-process, like the rate limiter and job reaper: revocations are lost on
restart and not shared across instances. A durable/shared store is a follow-up
(pairs with F16). Refresh tokens still expire, and access tokens remain the
short-lived credential.
"""

from __future__ import annotations

import time
from threading import Lock


class RevokedTokenStore:
    """Thread-safe set of revoked jti -> expiry (unix seconds)."""

    def __init__(self) -> None:
        self._revoked: dict[str, float] = {}
        self._lock = Lock()

    def revoke(self, jti: str, expires_at: float) -> None:
        with self._lock:
            self._revoked[jti] = expires_at
            self._prune_locked()

    def is_revoked(self, jti: str) -> bool:
        with self._lock:
            self._prune_locked()
            return jti in self._revoked

    def clear(self) -> None:
        with self._lock:
            self._revoked.clear()

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [jti for jti, exp in self._revoked.items() if exp < now]
        for jti in expired:
            del self._revoked[jti]


# Module-global store used by the auth service in the running app.
revoked_tokens = RevokedTokenStore()
