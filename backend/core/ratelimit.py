"""
core/ratelimit.py
-----------------
In-process fixed-window rate limiting (F5).

A dependency-free limiter that protects two request classes:
  - "auth"  — POST /auth/login, /auth/register, keyed by client IP.
  - "ai"    — the AI-intensive POSTs (assessment generation, grading, tutor
              chat/hint, course upload), keyed by the authenticated user id when
              a valid bearer token is present, else by IP.

Limits and windows are configurable via settings. A blocked request gets a 429
with a ``Retry-After`` header. This is a single-process safety net; multi-instance
enforcement (shared store) is deferred to Phase 3 (F16). Written so the limiter
is injectable and clock-controllable for tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.config import settings


@dataclass
class _Bucket:
    window_start: float
    count: int


class FixedWindowRateLimiter:
    """Counts requests per key within a fixed window. Thread/async safe."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._clock = clock
        self._lock = Lock()

    def check(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        """
        Register a hit for ``key``. Returns (allowed, retry_after_seconds).

        retry_after is 0 when allowed, otherwise the whole seconds until the
        current window resets.
        """
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or (now - bucket.window_start) >= window:
                self._buckets[key] = _Bucket(window_start=now, count=1)
                return True, 0
            if bucket.count < limit:
                bucket.count += 1
                return True, 0
            retry_after = int(window - (now - bucket.window_start)) + 1
            return False, retry_after

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# Module-global limiter used by the middleware in the running app.
limiter = FixedWindowRateLimiter()

_AUTH_PATHS = frozenset({"/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"})


def classify(path: str, method: str) -> str | None:
    """Return the limit class for a request, or None if it is not limited."""
    if method != "POST":
        return None
    if path in _AUTH_PATHS:
        return "auth"
    if (
        path == "/assessments/generate"
        or path == "/courses/upload"
        or path.endswith("/grade")
        or path.endswith("/chat")
        or path.endswith("/hint")
    ):
        return "ai"
    return None


def client_key(request: Request) -> str:
    """
    Identity for rate-limiting: the authenticated user id when a valid bearer
    token is present (so limits are per-user), otherwise the client IP.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from core.security import decode_token

            payload = decode_token(auth[len("Bearer ") :])
            return f"user:{payload.sub}"
        except Exception:  # noqa: BLE001 - fall back to IP on any decode failure
            pass
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-class fixed-window limits; 429 + Retry-After when exceeded."""

    def __init__(self, app, limiter: FixedWindowRateLimiter | None = None) -> None:
        super().__init__(app)
        self._limiter = limiter if limiter is not None else globals()["limiter"]

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        limit_class = classify(request.url.path, request.method)
        if limit_class is None:
            return await call_next(request)

        if limit_class == "auth":
            limit = settings.rate_limit_auth_max
            window = settings.rate_limit_auth_window_seconds
        else:
            limit = settings.rate_limit_ai_max
            window = settings.rate_limit_ai_window_seconds

        key = f"{limit_class}:{client_key(request)}"
        allowed, retry_after = self._limiter.check(key, limit, float(window))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "detail": "Too many requests. Please slow down and try again.",
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
