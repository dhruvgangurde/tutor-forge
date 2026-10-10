"""
core/throttle.py
----------------
Client-side request pacing for the real Gemini clients.

On the free tier a model allows only a few requests a minute. Sending faster
just earns 429s, and every 429 is itself a request against the same quota. So
each call first takes a slot in a one-minute sliding window for its model, and
waits for one if the window is full. A wait longer than
``settings.gemini_max_wait_seconds`` is not started: the call fails at once
with AIServiceBusy ("The AI service is busy"), which the caller turns into a
plain message.

Limits come from settings: ``gemini_generate_rpm`` per generation model and
``gemini_embed_rpm`` per embedding model. Two clients configured with the same
model share one window, because the quota is per model. A limit of 0 turns
pacing off.

Single-process, like the request rate limiter and the revoked-token cache: two
workers would each allow the full rate.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from core.config import settings
from core.exceptions import AIServiceBusy

GENERATE = "generate"
EMBED = "embed"


def _clock() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class SlidingWindowLimiter:
    """At most ``max_calls`` acquisitions in any ``window`` seconds."""

    def __init__(
        self,
        max_calls: int,
        window: float = 60.0,
        *,
        clock: Callable[[], float] = _clock,
        sleep: Callable[[float], None] = _sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self.max_calls = max_calls
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, max_wait: float, description: str = "AI request") -> None:
        """Take a slot, waiting for one if needed; AIServiceBusy if that would take too long."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = self._calls[0] + self.window - now
            if waited + wait > max_wait:
                raise AIServiceBusy(
                    f"{description}: rate limit of {self.max_calls} per "
                    f"{self.window:.0f}s reached; the next slot is {wait:.0f}s away"
                )
            self._sleep(wait)
            waited += wait


_limiters: dict[str, SlidingWindowLimiter] = {}
_registry_lock = threading.Lock()


def _limiter(key: str, per_minute: int) -> SlidingWindowLimiter:
    with _registry_lock:
        limiter = _limiters.get(key)
        if limiter is None or limiter.max_calls != per_minute:
            limiter = SlidingWindowLimiter(
                per_minute, clock=lambda: _clock(), sleep=lambda s: _sleep(s)
            )
            _limiters[key] = limiter
        return limiter


def throttle(kind: str, model: str) -> None:
    """Wait for a request slot for ``model``; ``kind`` is GENERATE or EMBED."""
    per_minute = settings.gemini_generate_rpm if kind == GENERATE else settings.gemini_embed_rpm
    if per_minute <= 0:
        return
    _limiter(f"{kind}:{model}", per_minute).acquire(
        settings.gemini_max_wait_seconds, description=f"Gemini {kind} ({model})"
    )


def reset() -> None:
    """Forget every window (tests only)."""
    with _registry_lock:
        _limiters.clear()
