"""
tests/test_exceptions.py
------------------------
Unit tests for the global exception handlers (core/exceptions.py).

Verifies audit fixes F8 (handlers log; nothing swallowed silently), F27
(DomainError -> 400 client-safe; bare ValueError logged; unexpected Exception ->
500 with no internal detail leaked) and F33 (RequestValidationError -> 422 that
is logged and request-id correlated like every other error class).
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import JWTError
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from auth.schemas import LoginRequest, RegisterRequest
from core.exceptions import DomainError, register_exception_handlers
from core.logging import REQUEST_ID_HEADER, RequestIdFilter, RequestIdMiddleware


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/domain")
    async def _domain():
        raise DomainError("email already registered")

    @app.get("/value")
    async def _value():
        raise ValueError("stray value error")

    @app.get("/jwt")
    async def _jwt():
        raise JWTError("bad token")

    @app.get("/db")
    async def _db():
        raise SQLAlchemyError("connection reset")

    @app.get("/boom")
    async def _boom():
        raise RuntimeError("secret internal detail")

    class _Body(BaseModel):
        name: str = Field(min_length=1)
        count: int

    @app.post("/validated")
    async def _validated(body: _Body):  # pragma: no cover - never reached on 422
        return {"ok": body.name}

    # The real auth request schemas, so the password-redaction tests exercise
    # the actual password fields and validators without needing a database.
    @app.post("/auth/register")
    async def _register(body: RegisterRequest):  # pragma: no cover - never reached on 422
        return {"ok": True}

    @app.post("/auth/login")
    async def _login(body: LoginRequest):  # pragma: no cover - never reached on 422
        return {"ok": True}

    app.add_middleware(RequestIdMiddleware)

    # raise_server_exceptions=False so the catch-all handler runs instead of the
    # test client re-raising.
    return TestClient(app, raise_server_exceptions=False)


def test_domain_error_returns_400_with_message(client):
    resp = client.get("/domain")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "bad_request"
    assert body["detail"] == "email already registered"


def test_value_error_returns_400_and_is_logged(client, caplog):
    with caplog.at_level(logging.WARNING, logger="core.exceptions"):
        resp = client.get("/value")
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
    # F8/F27: the stray ValueError must be logged, not silently swallowed.
    assert any("Unhandled ValueError" in r.message for r in caplog.records)


def test_jwt_error_returns_401(client):
    resp = client.get("/jwt")
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_db_error_returns_500_and_hides_internals(client, caplog):
    with caplog.at_level(logging.ERROR, logger="core.exceptions"):
        resp = client.get("/db")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "A database error occurred."
    assert "connection reset" not in resp.text  # internal detail not leaked
    assert any("Database error" in r.message for r in caplog.records)


def test_generic_exception_returns_500_logged_and_no_leak(client, caplog):
    with caplog.at_level(logging.ERROR, logger="core.exceptions"):
        resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "An unexpected error occurred."
    # F8: traceback logged server-side; internal detail never returned.
    assert "secret internal detail" not in resp.text
    assert any(r.exc_info for r in caplog.records)


# ── F33: RequestValidationError is logged and request-id correlated ───────────

def test_validation_error_returns_422_and_is_logged(client, caplog):
    with caplog.at_level(logging.WARNING, logger="core.exceptions"):
        resp = client.post("/validated", json={"name": "", "count": "abc"})
    assert resp.status_code == 422
    # FastAPI's built-in handler logs nothing; ours must.
    assert any("Request validation failed" in r.message for r in caplog.records)


def test_validation_error_log_carries_request_id(client, caplog):
    rid = "reqid-abc123"
    caplog.handler.addFilter(RequestIdFilter())
    try:
        with caplog.at_level(logging.WARNING, logger="core.exceptions"):
            resp = client.post(
                "/validated", json={"count": 1}, headers={REQUEST_ID_HEADER: rid}
            )
    finally:
        caplog.handler.filters.clear()
    assert resp.status_code == 422
    records = [r for r in caplog.records if "Request validation failed" in r.message]
    assert records, "handler did not log"
    # The RequestIdFilter stamps request_id onto the record via the ContextVar,
    # which bind_request_id re-binds inside the handler.
    assert all(getattr(r, "request_id", None) == rid for r in records)
    # ...and the same id is echoed back to the caller for correlation.
    assert resp.headers[REQUEST_ID_HEADER] == rid


def test_validation_error_body_has_string_detail(client):
    resp = client.post("/validated", json={"name": "", "count": "abc"})
    body = resp.json()
    assert body["error"] == "validation_error"
    # The frontend's getErrorMessage() only surfaces `detail` when it is a
    # string — FastAPI's default 422 body makes it a list, which is why the
    # real reason never reached the user.
    assert isinstance(body["detail"], str) and body["detail"]
    assert "name" in body["detail"]
    # Raw per-field errors are still available for programmatic consumers.
    assert isinstance(body["errors"], list) and len(body["errors"]) == 2


def test_validation_detail_truncates_long_error_lists():
    from core.exceptions import _MAX_VALIDATION_DETAILS, _validation_detail

    errors = [
        {"loc": ["body", "responses", i, "answer_choice"], "msg": "bad"}
        for i in range(_MAX_VALIDATION_DETAILS + 3)
    ]
    detail = _validation_detail(errors)
    assert "(+3 more)" in detail
    assert "body" not in detail  # location noise stripped


# ── Raw input values never reach the response or the log ─────────────────────
# Pydantic puts the submitted value under each error's `input` key. For a bad
# /auth/register or /auth/login body that is the user's password, and it used
# to be both returned in the 422 and written to the WARNING log line.
# validation_error_handler now strips `input` from every error.

def _validation_log_text(caplog) -> str:
    return "\n".join(
        r.getMessage() for r in caplog.records if "Request validation failed" in r.getMessage()
    )


def test_short_register_password_is_not_echoed_or_logged(client, caplog):
    password = "Pw1234"  # 6 chars: fails RegisterRequest's min_length=8
    with caplog.at_level(logging.WARNING, logger="core.exceptions"):
        resp = client.post("/auth/register", json={"email": "a@b.com", "password": password})
    assert resp.status_code == 422
    assert password not in resp.text

    errors = resp.json()["errors"]
    assert len(errors) == 1
    # Only `input` is removed; the rest of the error shape is untouched.
    assert set(errors[0]) == {"type", "loc", "msg", "ctx"}
    assert errors[0]["type"] == "string_too_short"
    assert errors[0]["loc"] == ["body", "password"]

    logged = _validation_log_text(caplog)
    assert logged, "handler did not log"  # guards against a vacuous pass below
    assert password not in logged


def test_non_string_login_password_is_not_echoed_or_logged(client, caplog):
    password = "Secr3tGuess9"
    with caplog.at_level(logging.WARNING, logger="core.exceptions"):
        resp = client.post("/auth/login", json={"email": "a@b.com", "password": [password]})
    assert resp.status_code == 422
    assert password not in resp.text

    errors = resp.json()["errors"]
    assert len(errors) == 1
    assert set(errors[0]) == {"type", "loc", "msg"}
    assert errors[0]["loc"] == ["body", "password"]

    logged = _validation_log_text(caplog)
    assert logged, "handler did not log"
    assert password not in logged


def test_whole_body_input_is_not_echoed_or_logged(client, caplog):
    # A body that is not a JSON object fails at the model level, where Pydantic's
    # `input` is the entire payload -- on any route, not just /auth/*.
    payload = '{"email": "a@b.com", "password": "WholeBody#77"}'
    with caplog.at_level(logging.WARNING, logger="core.exceptions"):
        resp = client.post("/auth/login", json=payload)  # JSON string, not an object
    assert resp.status_code == 422
    assert "WholeBody#77" not in resp.text
    assert all("input" not in err for err in resp.json()["errors"])

    logged = _validation_log_text(caplog)
    assert logged, "handler did not log"
    assert "WholeBody#77" not in logged
