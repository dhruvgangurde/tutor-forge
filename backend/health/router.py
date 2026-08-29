"""
health/router.py
----------------
Liveness and readiness probes (audit finding F9).

  GET /health        — liveness. Cheap, dependency-free; 200 while the process
                       is up. This is the path a platform (Render) polls to
                       decide whether the instance should receive traffic.
  GET /health/ready  — readiness. Verifies the backing services (Postgres,
                       ChromaDB) are actually reachable; 200 when all checks
                       pass, 503 otherwise. Use this to gate a deploy cutover.

Readiness logic lives in `check_readiness()` so it can be unit-tested without a
running server or real infrastructure.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.dependencies import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    """Return 200 while the process is alive. No external dependencies."""
    return {
        "status": "ok",
        "service": "tutorforge",
        "environment": settings.environment,
    }


async def check_readiness(db: AsyncSession, chroma_client) -> tuple[bool, dict[str, str]]:
    """
    Probe each backing service. Returns (all_ok, per-check status map).

    Each check is isolated so one failing dependency does not mask the others,
    and the caller can report exactly what is down.
    """
    checks: dict[str, str] = {}

    # Postgres: a trivial round-trip proves connectivity + a live pool.
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, don't crash the probe
        logger.warning("Readiness: database check failed: %s", exc)
        checks["database"] = "error"

    # ChromaDB: heartbeat() is the SDK's cheap liveness call.
    try:
        if chroma_client is None:
            checks["chroma"] = "unavailable"
        else:
            chroma_client.heartbeat()
            checks["chroma"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness: chroma check failed: %s", exc)
        checks["chroma"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    return all_ok, checks


@router.get("/health/ready", summary="Readiness probe")
async def readiness(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """200 when Postgres and ChromaDB are reachable; 503 otherwise."""
    chroma_client = getattr(request.app.state, "chroma_client", None)
    all_ok, checks = await check_readiness(db, chroma_client)
    response.status_code = (
        status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return {"status": "ready" if all_ok else "not_ready", "checks": checks}
