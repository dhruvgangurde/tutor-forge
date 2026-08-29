"""
core/dependencies.py
--------------------
FastAPI Depends() factory functions for all shared services.
Services are initialized once during the FastAPI lifespan (main.py)
and stored on app.state. These factories retrieve them per-request.
"""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionFactory

if TYPE_CHECKING:
    # Avoid circular imports at runtime; only used for type hints
    from retrieval.service import RetrievalService
    from langfuse import Langfuse


# ── Database ──────────────────────────────────────────────────────────────────

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession for the duration of the request.
    Commits on success, rolls back on any exception, always closes.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Shared services (all stored on app.state by lifespan) ────────────────────

def get_retrieval_service(request: Request) -> "RetrievalService":
    """Return the singleton RetrievalService initialized at startup."""
    return request.app.state.retrieval_service


def get_langfuse_client(request: Request) -> "Langfuse":
    """Return the singleton Langfuse client initialized at startup."""
    return request.app.state.langfuse


def get_gemini_pro(request: Request):
    """Return the singleton GeminiProClient initialized at startup."""
    return request.app.state.gemini_pro


def get_gemini_flash(request: Request):
    """Return the singleton GeminiFlashClient initialized at startup."""
    return request.app.state.gemini_flash
