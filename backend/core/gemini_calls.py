"""
core/gemini_calls.py
--------------------
How every real Gemini call is retried, and when it gives up as "busy".

What went wrong before
----------------------
On the free tier a 429 says how long to wait (about 20 seconds). The retry
backed off 0.5s, 1s and 2s instead, so every retry landed inside the window,
spent more of the same quota and failed again. A slow outline generation that
hit the 30-second timeout was re-sent up to four times, each one a full
request against the quota.

Now
---
- A 429 is never retried inside the same window. An interactive call (tutor
  chat, hints, query embedding) fails fast. Background work (ingestion,
  assessment generation, grading; see background_work()) waits at least a
  full window, longer if the server asks, within
  ``settings.gemini_ingest_max_wait_seconds``.
- A daily quota fails at once: no wait inside a request can fix it.
- A timed-out generation is retried at most once.
- When it gives up for any of those reasons, the caller gets AIServiceBusy,
  whose text is the plain "The AI service is busy" message, never the
  provider's own. Other errors propagate exactly as before.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from core.config import settings
from core.exceptions import AIServiceBusy
from core.retry import call_with_retry
from core.throttle import WINDOW_SECONDS


def _transient_gemini_errors() -> tuple[type[BaseException], ...]:
    """
    Exception types that warrant a retry (F10): timeouts, connection drops, and
    the transient google-api-core status errors (429/503/deadline/500). Bad
    requests, auth, and not-found errors are intentionally excluded so they fail
    fast. google.api_core is a google-generativeai dependency; if it is somehow
    unavailable we still retry the stdlib transient errors.
    """
    errors: list[type[BaseException]] = [TimeoutError, ConnectionError]
    try:
        from google.api_core import exceptions as gexc

        errors += [
            gexc.TooManyRequests,       # 429
            gexc.ResourceExhausted,     # 429 (quota)
            gexc.ServiceUnavailable,    # 503
            gexc.DeadlineExceeded,      # 504 / timeout
            gexc.InternalServerError,   # 500
            gexc.Aborted,               # transient conflict
        ]
    except ImportError:  # retry set is best-effort
        pass
    return tuple(errors)


def _rate_limit_errors() -> tuple[type[BaseException], ...]:
    try:
        from google.api_core import exceptions as gexc

        return (gexc.TooManyRequests, gexc.ResourceExhausted)
    except ImportError:
        return ()


def _timeout_errors() -> tuple[type[BaseException], ...]:
    try:
        from google.api_core import exceptions as gexc

        return (TimeoutError, gexc.DeadlineExceeded)
    except ImportError:
        return (TimeoutError,)


GEMINI_TRANSIENT = _transient_gemini_errors()
_RATE_LIMITED = _rate_limit_errors()
_TIMED_OUT = _timeout_errors()

# ── Reading the server's retry delay ──────────────────────────────────────────

#: "Please retry in 20.468s." / "retryDelay": "20s" / retry_delay { seconds: 20 }
#: Each pattern captures a number and its unit ("ms" or "s"); "Please retry in
#: 396.356914ms." is seconds / 1000, not 396 seconds.
_DELAY_PATTERNS = (
    re.compile(r"retry in\s+(\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE),
    re.compile(r"retry_?delay\"?\s*[:=]\s*\"?(\d+(?:\.\d+)?)(ms|s)\b", re.IGNORECASE),
    re.compile(r"retry_delay\s*\{\s*seconds:\s*(\d+)()", re.IGNORECASE),
)


def _positive(seconds: float) -> float | None:
    """A delay of zero or less tells us nothing: None, not "retry now"."""
    return seconds if seconds > 0 else None


def _seconds(value: object) -> float | None:
    """A delay from a Duration-like object, a "20s" / "396ms" string or a number."""
    if value is None:
        return None
    if hasattr(value, "seconds"):
        # An empty "retry_delay { }" reads as 0s and 0ns: unknown, not zero.
        return _positive(
            float(getattr(value, "seconds", 0)) + float(getattr(value, "nanos", 0)) / 1e9
        )
    text = str(value).strip().lower()
    scale = 1.0
    if text.endswith("ms"):
        text, scale = text[:-2], 0.001
    elif text.endswith("s"):
        text = text[:-1]
    try:
        return _positive(float(text) * scale)
    except ValueError:
        return None


def retry_delay_seconds(exc: BaseException) -> float | None:
    """
    The wait a rate-limit error asks for, in seconds, or None.

    Looks, in order, at structured RetryInfo details, a Retry-After header, and
    the error text, because the SDK surfaces it differently by transport.
    """
    if not isinstance(exc, _RATE_LIMITED):
        return None
    for detail in getattr(exc, "details", None) or []:
        if hasattr(detail, "retry_delay"):
            delay = _seconds(detail.retry_delay)
        elif isinstance(detail, dict):
            delay = _seconds(detail.get("retryDelay") or detail.get("retry_delay"))
        else:
            delay = None
        if delay is not None:
            return delay
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        retry_after = None
    if retry_after is not None:
        delay = _seconds(retry_after)
        if delay is not None:
            return delay
    text = f"{exc} {getattr(exc, 'details', '')}"
    for pattern in _DELAY_PATTERNS:
        m = pattern.search(text)
        if m:
            delay = _positive(float(m.group(1)) * (0.001 if m.group(2).lower() == "ms" else 1.0))
            if delay is not None:
                return delay
    return None


def rate_limit_wait(exc: BaseException) -> float | None:
    """
    How long background work waits before retrying, or None to use the backoff.

    A 429 waits at least one whole pacing window, longer if the server asks.
    The free tier counts rejected requests too, so a retry inside the same
    window spends quota on a request that cannot succeed: a live upload was told
    "retry in 396ms", retried, and was then told "retry in 59s".
    """
    if not isinstance(exc, _RATE_LIMITED):
        return None
    delay = retry_delay_seconds(exc)
    return max(delay or 0.0, WINDOW_SECONDS)


# ── Background work ───────────────────────────────────────────────────────────
#
# Course ingestion, assessment generation and grading run as background jobs:
# nobody is waiting on a page, so a 429 may wait out the window and retry. A
# tutor reply or hint has someone waiting, so a 429 fails fast as busy instead.
# The job runners mark their graph run with background_work(); LangGraph runs
# sync nodes in worker threads with a copy of the context, so the mark reaches
# every Gemini call the job makes.

_BACKGROUND: ContextVar[bool] = ContextVar("gemini_background_work", default=False)


@contextmanager
def background_work() -> Iterator[None]:
    """Treat every Gemini call made inside this block as background work."""
    token = _BACKGROUND.set(True)
    try:
        yield
    finally:
        _BACKGROUND.reset(token)


def in_background() -> bool:
    return _BACKGROUND.get()


def wait_cap(background: bool | None = None) -> float:
    """The longest a call may wait: 300s for background work, 60s otherwise."""
    if background is None:
        background = in_background()
    return settings.gemini_ingest_max_wait_seconds if background else settings.gemini_max_wait_seconds


def is_daily_quota(exc: BaseException) -> bool:
    """A per-day quota: waiting a minute will not help, so do not retry."""
    if not isinstance(exc, _RATE_LIMITED):
        return False
    text = f"{exc} {getattr(exc, 'details', '')}".lower()
    return "perday" in text or "per day" in text or "daily" in text


def is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, _TIMED_OUT)


def _busy_reason(exc: BaseException) -> str | None:
    """Why the call is being given up as busy, or None if it is another failure."""
    if is_daily_quota(exc):
        return "daily quota reached"
    if isinstance(exc, _RATE_LIMITED):
        delay = retry_delay_seconds(exc)
        asked = f"; the server asked to wait {delay:.0f}s" if delay is not None else ""
        return f"rate limited{asked}"
    if is_timeout(exc):
        return f"timed out after {settings.gemini_timeout_seconds:.0f}s"
    return None


def call_gemini[T](
    fn: Callable[[], T],
    *,
    description: str,
    generation: bool,
    background: bool | None = None,
) -> T:
    """
    Run one Gemini request with retries, as described in the module docstring.

    ``generation`` limits a timed-out generation to one retry; embeddings keep
    the normal attempt count. ``background`` (default: inside background_work())
    decides what a 429 does: background work waits at least a full window and
    retries within ``gemini_ingest_max_wait_seconds``; an interactive call
    fails fast as busy, never retrying inside the window.
    """
    if background is None:
        background = in_background()

    def give_up(exc: BaseException) -> bool:
        if is_daily_quota(exc):
            return True
        return not background and isinstance(exc, _RATE_LIMITED)

    try:
        return call_with_retry(
            fn,
            attempts=settings.gemini_max_attempts,
            base_delay=settings.gemini_retry_base_delay,
            max_delay=settings.gemini_retry_max_delay,
            retry_on=GEMINI_TRANSIENT,
            description=description,
            sleep=lambda seconds: time.sleep(seconds),
            delay_hint=rate_limit_wait,
            max_total_wait=wait_cap(background),
            stop_if=give_up,
            attempts_for=(lambda exc: 2 if is_timeout(exc) else None) if generation else None,
        )
    except GEMINI_TRANSIENT as exc:
        reason = _busy_reason(exc)
        if reason is None:
            raise
        raise AIServiceBusy(f"{description}: {reason}") from exc
