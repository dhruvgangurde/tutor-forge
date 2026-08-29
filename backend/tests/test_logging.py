"""
tests/test_logging.py
---------------------
Unit tests for request-id correlation + logging config (core/logging.py).

Verifies audit fix F30: every request gets a correlation id, it is echoed in the
X-Request-ID response header, an inbound id is honored, and the id is exposed to
log records via the ContextVar + filter.
"""

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.logging import (
    REQUEST_ID_HEADER,
    RequestIdFilter,
    RequestIdMiddleware,
    configure_logging,
    get_request_id,
    request_id_ctx,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo")
    async def _echo():
        return {"request_id": get_request_id()}

    return app


def test_response_carries_generated_request_id():
    client = TestClient(_app())
    resp = client.get("/echo")
    assert resp.status_code == 200
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid and rid != "-"
    # The handler saw the same id that was returned in the header.
    assert resp.json()["request_id"] == rid


def test_inbound_request_id_is_honored():
    client = TestClient(_app())
    resp = client.get("/echo", headers={REQUEST_ID_HEADER: "trace-123"})
    assert resp.headers[REQUEST_ID_HEADER] == "trace-123"
    assert resp.json()["request_id"] == "trace-123"


def test_context_resets_between_requests():
    client = TestClient(_app())
    r1 = client.get("/echo").headers[REQUEST_ID_HEADER]
    r2 = client.get("/echo").headers[REQUEST_ID_HEADER]
    assert r1 != r2
    # Outside any request the contextvar is back to its default.
    assert get_request_id() == "-"


def test_filter_injects_request_id_onto_record():
    token = request_id_ctx.set("abc123")
    try:
        record = logging.LogRecord("n", logging.INFO, __file__, 1, "msg", None, None)
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "abc123"
    finally:
        request_id_ctx.reset(token)


def test_configure_logging_is_idempotent():
    configure_logging()
    configure_logging()  # second call must not stack handlers
    assert len(logging.getLogger().handlers) == 1


# ── Correlation on the unhandled-exception path ───────────────────────────────
#
# Starlette runs the catch-all `Exception` handler in ServerErrorMiddleware,
# which is installed *outside* every user middleware — including
# RequestIdMiddleware. The id therefore had to be recovered from the ASGI scope
# rather than the ContextVar, which `dispatch` has already reset by then.
# Before the fix, 500s logged "[-]" and carried no X-Request-ID header, so the
# one class of response you most need to trace was the one you could not.


def _error_app() -> FastAPI:
    from core.exceptions import register_exception_handlers

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.get("/boom")
    async def _boom():
        raise RuntimeError("kaboom")

    return app


def test_unhandled_exception_response_carries_request_id():
    client = TestClient(_error_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={REQUEST_ID_HEADER: "trace-500"})
    assert resp.status_code == 500
    assert resp.headers.get(REQUEST_ID_HEADER) == "trace-500"


def test_unhandled_exception_generated_id_is_returned():
    """Even without an inbound id, the generated one must come back."""
    client = TestClient(_error_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid and rid != "-"


def test_unhandled_exception_log_record_carries_request_id(caplog):
    """The 500's log line must be correlatable, not '[-]'."""
    caplog.handler.addFilter(RequestIdFilter())
    try:
        client = TestClient(_error_app(), raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="core.exceptions"):
            client.get("/boom", headers={REQUEST_ID_HEADER: "trace-log"})
    finally:
        caplog.handler.filters.clear()

    errors = [r for r in caplog.records if r.name == "core.exceptions"]
    assert errors, "the catch-all handler did not log"
    assert all(getattr(r, "request_id", "-") == "trace-log" for r in errors)


def test_context_is_still_reset_after_error_response():
    """bind_request_id must not leak the id past the handler."""
    client = TestClient(_error_app(), raise_server_exceptions=False)
    client.get("/boom", headers={REQUEST_ID_HEADER: "trace-leak"})
    assert get_request_id() == "-"
