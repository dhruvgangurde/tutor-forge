"""
core/retry.py
-------------
Dependency-free bounded retry with exponential backoff + jitter (F10).

Used to wrap external API calls (Gemini) so transient failures are retried and,
once attempts are exhausted, the *real* exception is re-raised — never swallowed
— so the caller's job error-handling can mark the job "failed" instead of
hanging. `sleep` and `jitter` are injectable so the backoff is deterministic and
instant under test.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)


def _first_line(exc: BaseException) -> str:
    """
    The first line of an error, for a log line. Provider errors run on with
    quota details and documentation links; the first line says what happened.
    """
    text = str(exc).strip()
    return text.splitlines()[0][:300] if text else type(exc).__name__

T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int,
    base_delay: float,
    max_delay: float,
    retry_on: tuple[type[BaseException], ...],
    description: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    delay_hint: Callable[[BaseException], float | None] | None = None,
    max_total_wait: float | None = None,
    stop_if: Callable[[BaseException], bool] | None = None,
    attempts_for: Callable[[BaseException], int | None] | None = None,
) -> T:
    """
    Call ``fn`` up to ``attempts`` times, retrying only on ``retry_on`` errors.

    Backoff before retry N is ``min(max_delay, base_delay * 2**(N-1))`` scaled by
    jitter in ``[0.5, 1.5)`` to avoid thundering-herd alignment. An exception not
    in ``retry_on`` propagates immediately (not retried). When the final attempt
    fails, the last exception is re-raised unchanged.

    Optional hooks, all off by default:

    - ``delay_hint(exc)``: the wait the server asked for (a 429's retry delay).
      The retry never comes sooner than that; retrying inside the window only
      spends more of the quota it is waiting for.
    - ``max_total_wait``: seconds of sleeping allowed across all retries. A wait
      that would pass it is not started; the last exception is re-raised.
    - ``stop_if(exc)``: re-raise at once, e.g. a daily quota no wait can fix.
    - ``attempts_for(exc)``: a lower attempt cap for this kind of failure, e.g.
      a timed-out generation is tried at most twice.

    Raises:
        ValueError: if ``attempts < 1``.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    waited = 0.0
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            limit = attempts
            if attempts_for is not None:
                limit = min(attempts, attempts_for(exc) or attempts)
            if stop_if is not None and stop_if(exc):
                logger.error(
                    "%s failed and will not be retried: %s", description, _first_line(exc)
                )
                raise
            if attempt >= limit:
                logger.error(
                    "%s failed after %d attempt(s): %s", description, attempt, _first_line(exc)
                )
                raise
            backoff = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = backoff * (0.5 + jitter())
            hint = delay_hint(exc) if delay_hint is not None else None
            if hint is not None:
                delay = max(delay, hint)
            if max_total_wait is not None and waited + delay > max_total_wait:
                logger.error(
                    "%s failed (attempt %d/%d); the next wait of %.1fs would pass the "
                    "%.0fs limit, giving up: %s",
                    description,
                    attempt,
                    limit,
                    delay,
                    max_total_wait,
                    _first_line(exc),
                )
                raise
            waited += delay
            logger.warning(
                "%s failed (attempt %d/%d); retrying in %.2fs: %s",
                description,
                attempt,
                attempts,
                delay,
                _first_line(exc),
            )
            sleep(delay)

    # Unreachable: the loop either returns or raises on the final attempt.
    raise AssertionError("call_with_retry exited its loop without returning")
