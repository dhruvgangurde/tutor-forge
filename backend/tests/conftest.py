"""
tests/conftest.py
-----------------
Shared test fixtures.

The FastAPI lifespan does NOT run under httpx's ASGITransport, so the service
singletons it normally creates (langfuse, retrieval service, gemini clients) are
never attached to ``app.state``. Any endpoint whose dependencies read
``app.state`` therefore raised ``AttributeError`` during dependency resolution —
which is why the assessment ``/generate`` integration tests failed before real
routing/authz was ever exercised (audit finding F13).

The autouse fixture below supplies default ``MagicMock`` overrides for those
``app.state``-backed dependencies so endpoint tests exercise real routing,
validation, and authorization without a lifespan. Individual tests may still
override any of them with a purpose-built mock; because we only fill in
dependencies that a test has not already overridden, those explicit overrides
take precedence.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import settings
from core.dependencies import (
    get_gemini_flash,
    get_gemini_pro,
    get_langfuse_client,
    get_retrieval_service,
)
from main import app


@pytest.fixture(scope="session", autouse=True)
def disable_rate_limiting():
    """
    Turn rate limiting off for the suite. Existing tests perform many logins from
    the same client within the window and would otherwise trip the limiter; the
    limiter itself is covered in isolation by tests/test_rate_limit.py, which
    re-enables it locally.
    """
    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    yield
    settings.rate_limit_enabled = original


@pytest.fixture(scope="session", autouse=True)
def pin_mock_provider():
    """
    Pin the suite to LLM_PROVIDER=mock regardless of the developer's .env.

    The suite is built around the offline mock provider: no network, no quota,
    deterministic stub embeddings, and groundedness scores chosen against the
    mock's 0.20 bar. Without this pin those assumptions silently follow whatever
    a developer happens to have in backend/.env — flipping it to `ollama` turned
    five tests red purely through ambient config, which is a property of the
    harness, not of the code under test.

    Tests that genuinely need another provider construct their own
    ``Settings(llm_provider=...)`` (see tests/test_llm_provider.py), which this
    does not affect.
    """
    original = settings.llm_provider
    settings.llm_provider = "mock"
    yield
    settings.llm_provider = original


# The service dependencies that are normally initialized by the app lifespan.
_STATE_BACKED_DEPS = (
    get_langfuse_client,
    get_retrieval_service,
    get_gemini_pro,
    get_gemini_flash,
)


@pytest.fixture(autouse=True)
def default_service_overrides():
    """
    Provide MagicMock defaults for lifespan-initialized service dependencies,
    without clobbering any override a test set up itself.
    """
    added = []
    for dep in _STATE_BACKED_DEPS:
        if dep not in app.dependency_overrides:
            app.dependency_overrides[dep] = lambda: MagicMock()
            added.append(dep)
    yield
    # Only remove what this fixture added; leave test-owned overrides alone.
    for dep in added:
        app.dependency_overrides.pop(dep, None)


@pytest.fixture(autouse=True)
def stub_background_jobs():
    """
    Neutralize the long-running background-task entrypoints in every test.

    The ``/assessments/generate``, ``/courses/upload``, and ``/grading/*/grade``
    endpoints schedule these via FastAPI BackgroundTasks, and each opens the real
    ``AsyncSessionFactory`` (Postgres) — not the per-test SQLite override. Under
    Windows' proactor event loop that real connection, created inside a
    function-scoped test loop, poisons the loop when it closes and makes
    unrelated later tests crash with an asyncpg ``'NoneType' ... send`` error.
    Stubbing the entrypoints keeps endpoint tests to the request/response path.
    No test exercises these orchestrators directly (verified), so this is safe.
    """
    with (
        patch("assessments.service._run_assessment_graph", new_callable=AsyncMock),
        patch("courses.service._run_ingestion", new_callable=AsyncMock),
        patch("grading.service._run_grading_graph", new_callable=AsyncMock),
    ):
        yield
