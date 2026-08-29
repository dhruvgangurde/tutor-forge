"""
core/logging.py
---------------
Structured logging configuration + per-request correlation IDs.

`configure_logging()` installs a formatter that includes the current request's
correlation id (from a ContextVar), so every log line emitted while handling a
request is traceable to that request. `RequestIdMiddleware` assigns/propagates
the id and echoes it back in the ``X-Request-ID`` response header.

This closes audit finding F30 (no request logging / correlation) and underpins
F8 (exception handlers can now log with request context).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# Key under which the id is mirrored onto the ASGI scope (``request.state``).
# The ContextVar alone is not enough: see bind_request_id() below.
REQUEST_ID_STATE_KEY = "request_id"

# Holds the current request's correlation id; "-" when outside any request.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Rendered when no correlation id is available.
NO_REQUEST_ID = "-"


def get_request_id() -> str:
    """Return the correlation id for the in-flight request (or '-' if none)."""
    return request_id_ctx.get()


@contextmanager
def bind_request_id(request: Request) -> Iterator[str]:
    """
    Re-bind ``request``'s correlation id onto the ContextVar for this block.

    Needed by the global exception handlers. Starlette routes the catch-all
    ``Exception`` handler through ServerErrorMiddleware, which is installed
    *outside* every user middleware — including RequestIdMiddleware. By the
    time that handler runs, RequestIdMiddleware's ``finally`` has already
    reset the ContextVar, so log records rendered "-" instead of the id and
    the 500 response carried no ``X-Request-ID`` header.

    The id is recovered from the ASGI scope instead, which is the same dict
    object across the BaseHTTPMiddleware task boundary and therefore still
    populated. Yields the id so the caller can also set the response header.
    """
    rid = getattr(request.state, REQUEST_ID_STATE_KEY, None) or request_id_ctx.get()
    token = request_id_ctx.set(rid)
    try:
        yield rid
    finally:
        request_id_ctx.reset(token)


class RequestIdFilter(logging.Filter):
    """Inject the current request_id onto every LogRecord so the formatter can
    render it even for records emitted by third-party libraries."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def configure_logging(level: int | str = logging.INFO) -> None:
    """
    Idempotently configure root logging with request-id-aware formatting.

    Safe to call more than once (tests, uvicorn reload): existing handlers are
    replaced rather than stacked, so log lines are never duplicated.
    """
    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
        )
    )
    handler.addFilter(RequestIdFilter())

    root.handlers = [handler]


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Assign a correlation id to every request and echo it in the response header.

    An inbound ``X-Request-ID`` is honored (so a gateway/front-end can propagate
    a trace id); otherwise a short random id is generated. The id is bound to the
    ContextVar for the duration of the request and reset afterwards.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        # Mirror onto the ASGI scope as well as the ContextVar: the ContextVar
        # is reset when this dispatch unwinds, but the outer
        # ServerErrorMiddleware still needs the id to correlate a 500.
        setattr(request.state, REQUEST_ID_STATE_KEY, rid)
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
