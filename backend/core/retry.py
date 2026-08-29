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
) -> T:
    """
    Call ``fn`` up to ``attempts`` times, retrying only on ``retry_on`` errors.

    Backoff before retry N is ``min(max_delay, base_delay * 2**(N-1))`` scaled by
    jitter in ``[0.5, 1.5)`` to avoid thundering-herd alignment. An exception not
    in ``retry_on`` propagates immediately (not retried). When the final attempt
    fails, the last exception is re-raised unchanged.

    Raises:
        ValueError: if ``attempts < 1``.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt >= attempts:
                logger.error(
                    "%s failed after %d attempt(s): %s", description, attempts, exc
                )
                raise
            backoff = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = backoff * (0.5 + jitter())
            logger.warning(
                "%s failed (attempt %d/%d); retrying in %.2fs: %s",
                description,
                attempt,
                attempts,
                delay,
                exc,
            )
            sleep(delay)

    # Unreachable: the loop either returns or raises on the final attempt.
    raise AssertionError("call_with_retry exited its loop without returning")
