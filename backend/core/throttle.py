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
``gemini_embed_rpm`` per embedding model. A generation call takes one slot; an
embedding batch takes one per text, because the free tier counts each text as
a request (a 100-text batch used the whole minute and the next batch got a 429). Two clients configured with the same
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
        self._calls: deque[tuple[float, int]] = deque()  # (time, weight)
        self._lock = threading.Lock()

    def acquire(self, max_wait: float, description: str = "AI request", weight: int = 1) -> None:
        """
        Take ``weight`` slots, waiting for them if needed; AIServiceBusy if that
        would take longer than ``max_wait``.

        The weight is what the provider counts: one for a generation call, the
        number of texts for an embedding batch (each text is a request on the
        free tier). A taken slot is kept for the whole window even if the
        request then fails, because the provider counts rejected requests too.
        """
        if weight > self.max_calls:
            raise ValueError(
                f"a request of weight {weight} can never fit a limit of {self.max_calls}"
            )
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                while self._calls and now - self._calls[0][0] >= self.window:
                    self._calls.popleft()
                used = sum(w for _, w in self._calls)
                if used + weight <= self.max_calls:
                    self._calls.append((now, weight))
                    return
                # Wait until enough of the oldest requests leave the window.
                needed = used + weight - self.max_calls
                freed = 0
                for started, w in self._calls:
                    freed += w
                    if freed >= needed:
                        wait = started + self.window - now
                        break
            if waited + wait > max_wait:
                raise AIServiceBusy(
                    f"{description}: rate limit of {self.max_calls} per "
                    f"{self.window:.0f}s reached; room for {weight} is {wait:.0f}s away"
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


def throttle(kind: str, model: str, weight: int = 1, max_wait: float | None = None) -> None:
    """
    Wait for request slots for ``model``; ``kind`` is GENERATE or EMBED.

    ``weight`` is 1 for a generation call and the number of texts for an
    embedding batch. ``max_wait`` defaults to the interactive limit
    (``gemini_max_wait_seconds``); background work passes a longer one.
    """
    per_minute = settings.gemini_generate_rpm if kind == GENERATE else settings.gemini_embed_rpm
    if per_minute <= 0:
        return
    _limiter(f"{kind}:{model}", per_minute).acquire(
        settings.gemini_max_wait_seconds if max_wait is None else max_wait,
        description=f"Gemini {kind} ({model})",
        weight=weight,
    )


def embedding_batch_limit(api_limit: int) -> int:
    """
    Texts per embedding request: the configured batch size, never more than the
    API accepts, and never more than the per-minute allowance (a batch heavier
    than the whole allowance could never be sent without a 429).
    """
    size = min(settings.embedding_batch_size, api_limit)
    if settings.gemini_embed_rpm > 0:
        size = min(size, settings.gemini_embed_rpm)
    return max(1, size)


#: The pacing window, in seconds; also how long a rate-limited call with no
#: usable retry hint waits before trying again (core/gemini_calls.py).
WINDOW_SECONDS = 60.0


def reset() -> None:
    """Forget every window (tests only)."""
    with _registry_lock:
        _limiters.clear()
