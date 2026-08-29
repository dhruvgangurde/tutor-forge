"""
core/exceptions.py
------------------
Global FastAPI exception handlers.

All handlers return structured JSON: {"error": <type>, "detail": <message>}.
Internal details (stack traces, DB errors, unexpected failures) are logged
server-side — with the request correlation id, see core/logging.py — but never
exposed to clients.

Audit fixes:
  F8  — every handler now logs; the catch-all no longer silently swallows
        tracebacks (which also suppressed Starlette's default logging).
  F33 — RequestValidationError (FastAPI's 422 path) is handled here too.
        FastAPI's built-in handler bypasses this module entirely, so 422s were
        the one error class that was neither logged nor request-id correlated.
  F27 — DomainError is the explicit, client-safe 400 path for intentional
        validation failures. A bare ValueError reaching the fallback handler is
        logged so it can no longer hide a bug. (The status stays 400 for
        backward compatibility; the strict 400->500 flip is deferred until every
        endpoint's ValueError catching is audited — Phase 3.)

Correlation: every handler runs inside ``bind_request_id`` so its log records
carry the request id, and echoes that id back in the ``X-Request-ID`` response
header. This matters most for the catch-all ``Exception`` handler, which
Starlette runs in ServerErrorMiddleware — outside RequestIdMiddleware, and
therefore outside the ContextVar it manages. See core/logging.bind_request_id.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jose import JWTError
from sqlalchemy.exc import SQLAlchemyError

from core.logging import NO_REQUEST_ID, REQUEST_ID_HEADER, bind_request_id

logger = logging.getLogger(__name__)

# Cap how many field errors are folded into the client-facing `detail` string;
# the full list is always returned under `errors` and logged server-side.
_MAX_VALIDATION_DETAILS = 5


def _correlation_headers(rid: str) -> dict[str, str] | None:
    """Echo the request id back on error responses, when one is known."""
    return {REQUEST_ID_HEADER: rid} if rid and rid != NO_REQUEST_ID else None


def _validation_detail(errors: list[dict]) -> str:
    """
    Render FastAPI's per-field validation errors as one human-readable string.

    The frontend surfaces `detail` verbatim, so this must read as a sentence,
    not as a dump of Pydantic internals.
    """
    if not errors:
        return "Request validation failed."
    parts: list[str] = []
    for err in errors[:_MAX_VALIDATION_DETAILS]:
        # Drop the leading "body"/"query" location segment — it is noise to a user.
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        field = ".".join(loc)
        msg = str(err.get("msg", "is invalid")).removeprefix("Value error, ")
        parts.append(f"{field}: {msg}" if field else msg)
    if len(errors) > _MAX_VALIDATION_DETAILS:
        parts.append(f"(+{len(errors) - _MAX_VALIDATION_DETAILS} more)")
    return "; ".join(parts)


class DomainError(Exception):
    """
    Raised by service/business logic for an expected, client-safe validation
    failure. Mapped to HTTP 400 with the message shown to the caller.

    Prefer this over a bare ``ValueError`` for intentional 400s so that an
    *unexpected* ValueError remains distinguishable as a server-side bug.
    """


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI application."""

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        with bind_request_id(request) as rid:
            logger.info(
                "Domain error on %s %s: %s", request.method, request.url.path, exc
            )
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "detail": str(exc)},
            headers=_correlation_headers(rid),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI installs its own RequestValidationError handler by default,
        # which returns 422 without logging and without the request id. Register
        # ours over it so a rejected request body is as traceable as any other
        # error, and so the body carries a string `detail` like every other
        # handler here (the frontend's getErrorMessage reads `detail`). The raw
        # per-field errors are preserved under `errors`.
        errors = jsonable_encoder(exc.errors())
        with bind_request_id(request) as rid:
            logger.warning(
                "Request validation failed on %s %s: %s",
                request.method,
                request.url.path,
                errors,
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": _validation_detail(errors),
                "errors": errors,
            },
            headers=_correlation_headers(rid),
        )

    @app.exception_handler(JWTError)
    async def jwt_error_handler(request: Request, exc: JWTError) -> JSONResponse:
        with bind_request_id(request) as rid:
            logger.warning(
                "JWT rejected on %s %s: %s", request.method, request.url.path, exc
            )
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "detail": "Invalid or expired token."},
            headers=_correlation_headers(rid),
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Log the real error server-side (with traceback + request id); never
        # expose internals to clients.
        with bind_request_id(request) as rid:
            logger.exception(
                "Database error on %s %s", request.method, request.url.path
            )
        return JSONResponse(
            status_code=500,
            content={"error": "database_error", "detail": "A database error occurred."},
            headers=_correlation_headers(rid),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        # Intentional validation should raise DomainError. A bare ValueError
        # reaching here is logged so it cannot silently mask a bug.
        with bind_request_id(request) as rid:
            logger.warning(
                "Unhandled ValueError on %s %s: %s", request.method, request.url.path, exc
            )
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "detail": str(exc)},
            headers=_correlation_headers(rid),
        )

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception) -> JSONResponse:
        # Catch-all: fail closed, log the full traceback, expose nothing.
        # Runs in ServerErrorMiddleware, outside RequestIdMiddleware — so the
        # id must be recovered from the scope, and the header set here because
        # the response never unwinds back through RequestIdMiddleware.
        with bind_request_id(request) as rid:
            logger.exception(
                "Unhandled exception on %s %s", request.method, request.url.path
            )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "An unexpected error occurred."},
            headers=_correlation_headers(rid),
        )
