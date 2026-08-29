"""
main.py
-------
TutorForge AI — FastAPI application entry point.

Startup sequence (lifespan):
  1. Initialize async DB engine + session factory (core/database.py)
  2. Initialize ChromaDB persistent client
  3. Initialize Gemini Pro and Flash clients
  4. Initialize Langfuse client
  5. Initialize RetrievalService singleton (depends on Chroma + Gemini)
  6. Store all singletons on app.state for Depends() factories

Routers registered here (add each as the corresponding unit is built):
  - auth/router.py        (active)
  - courses/router.py     (active)
  - assessments/router.py (active)
  - grading/router.py     (active)
  - tutoring/router.py    (active)
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import chromadb
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langfuse import Langfuse

from auth.router import router as auth_router
from assessments.router import router as assessments_router
from courses.router import router as courses_router
from grading.router import router as grading_router
from health.router import router as health_router
from tutoring.router import router as tutoring_router
from core.config import assert_safe_production_config, legacy_env_warnings, settings
from core.exceptions import register_exception_handlers
from core.logging import RequestIdMiddleware, configure_logging
from core.ratelimit import RateLimitMiddleware
from core.retry import call_with_retry
from core.security_headers import SecurityHeadersMiddleware


# ── Gemini client wrappers ────────────────────────────────────────────────────
# Imported here to keep lifespan readable; actual classes live in their own modules.

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
    except Exception:  # noqa: BLE001 - retry set is best-effort
        pass
    return tuple(errors)


# Computed once; used by both real clients.
_GEMINI_TRANSIENT = _transient_gemini_errors()


class GeminiProClient:
    """Thin wrapper around google-generativeai for Gemini Pro."""

    def __init__(self, api_key: str, model: str) -> None:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._model_name = model
        self._client = genai.GenerativeModel(model)

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        system_instruction: str = "",
    ) -> str:
        import google.generativeai as genai

        def _call() -> str:
            model = genai.GenerativeModel(
                self._model_name,
                system_instruction=system_instruction or None,
            )
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=temperature),
                request_options={"timeout": settings.gemini_timeout_seconds},
            )
            return response.text

        return call_with_retry(
            _call,
            attempts=settings.gemini_max_attempts,
            base_delay=settings.gemini_retry_base_delay,
            max_delay=settings.gemini_retry_max_delay,
            retry_on=_GEMINI_TRANSIENT,
            description=f"Gemini Pro generate ({self._model_name})",
        )

    def generate_deterministic(self, prompt: str) -> str:
        """Enforces temperature=0 — required for all grading calls (FR-05.3)."""
        return self.generate(prompt, temperature=0.0)

    def embed(self, text: str) -> list[float]:
        import google.generativeai as genai

        def _call() -> list[float]:
            result = genai.embed_content(
                model=settings.embedding_model,
                content=text,
                task_type="retrieval_document",
                request_options={"timeout": settings.gemini_timeout_seconds},
            )
            return result["embedding"]

        return call_with_retry(
            _call,
            attempts=settings.gemini_max_attempts,
            base_delay=settings.gemini_retry_base_delay,
            max_delay=settings.gemini_retry_max_delay,
            retry_on=_GEMINI_TRANSIENT,
            description=f"Gemini embed ({settings.embedding_model})",
        )



class GeminiFlashClient:
    """Thin wrapper for Gemini Flash — used for high-throughput assessment generation."""

    def __init__(self, api_key: str, model: str) -> None:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._model_name = model

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        system_instruction: str = "",
    ) -> str:
        import google.generativeai as genai

        def _call() -> str:
            model = genai.GenerativeModel(
                self._model_name,
                system_instruction=system_instruction or None,
            )
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=temperature),
                request_options={"timeout": settings.gemini_timeout_seconds},
            )
            return response.text

        return call_with_retry(
            _call,
            attempts=settings.gemini_max_attempts,
            base_delay=settings.gemini_retry_base_delay,
            max_delay=settings.gemini_retry_max_delay,
            retry_on=_GEMINI_TRANSIENT,
            description=f"Gemini Flash generate ({self._model_name})",
        )


# ── Provider factory ──────────────────────────────────────────────────────────

def _build_llm_clients() -> tuple[object, object]:
    """
    Construct the (pro, flash) client pair for the configured LLM_PROVIDER.

    Every provider satisfies the same duck-typed interface:
        generate(prompt, temperature=..., system_instruction=...) -> str
        generate_deterministic(prompt)                            -> str
        embed(text)                                               -> list[float]

    The "pro" client is also what RetrievalService uses for embeddings, so it
    must always implement embed(); the "flash" client only ever generates.

    Extracted from lifespan() so provider selection is unit-testable without
    booting the app (tests/test_llm_provider.py).
    """
    provider = settings.llm_provider

    if provider == "mock":
        from core.mock_gemini import MockGeminiProClient, MockGeminiFlashClient
        print("[INIT] LLM_PROVIDER=mock — deterministic in-process clients (no API quota usage)")
        return (
            MockGeminiProClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_pro_model,
            ),
            MockGeminiFlashClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_flash_model,
            ),
        )

    if provider == "ollama":
        # LOCAL DEV ONLY. assert_safe_production_config() has already refused to
        # boot if this is production, so reaching here means a non-prod deploy.
        from core.ollama_client import OllamaClient
        print(
            "[INIT] LLM_PROVIDER=ollama — LOCAL DEV ONLY.\n"
            "       Results from this provider are development signal only and must NOT be\n"
            "       reported as acceptance criteria (teacher acceptance / zero leakage /\n"
            "       zero grading variance). Those require LLM_PROVIDER=gemini.\n"
            f"       generation={settings.ollama_generation_model} "
            f"embedding={settings.ollama_embedding_model} url={settings.ollama_base_url}"
        )
        # Ollama has no pro/flash tiering; both roles use the same model.
        return (
            OllamaClient(role="pro"),
            OllamaClient(role="flash"),
        )

    print("[INIT] LLM_PROVIDER=gemini — real Gemini clients")
    return (
        GeminiProClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_pro_model,
        ),
        GeminiFlashClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_flash_model,
        ),
    )


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Initialize all long-lived services at startup.
    Services are stored on app.state and retrieved via Depends() factories
    in core/dependencies.py.
    """
    # 1. Chroma persistent client
    #    Existing course collections are automatically loaded from the persist
    #    directory on startup — no re-ingestion required after restart.
    chroma_client = chromadb.PersistentClient(path=settings.chroma_persist_path)
    app.state.chroma_client = chroma_client

    # 2. LLM clients — selected by LLM_PROVIDER (mock | gemini | ollama).
    #    All three expose the same interface (generate / generate_deterministic /
    #    embed), so app.state keeps the historical `gemini_pro` / `gemini_flash`
    #    attribute names and no downstream consumer changes.
    gemini_pro, gemini_flash = _build_llm_clients()
    app.state.gemini_pro = gemini_pro
    app.state.gemini_flash = gemini_flash

    # 3. Langfuse observability client
    langfuse = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    app.state.langfuse = langfuse

    # 4. RetrievalService — depends on Chroma client and Gemini Pro for embedding
    from retrieval.service import RetrievalService  # deferred to avoid circular import at module level
    retrieval_service = RetrievalService(
        chroma_client=chroma_client,
        gemini_client=gemini_pro,
    )
    app.state.retrieval_service = retrieval_service

    # 4b. Ollama warm-up: load both local models before serving traffic, so the
    #     first real request does not pay the cold-start penalty (measured: 30.8s
    #     cold vs 21.1s warm for hierarchy extraction). Runs in a worker thread
    #     because the client is blocking; failures are logged, never fatal.
    if settings.llm_provider == "ollama":
        import asyncio
        await asyncio.to_thread(gemini_pro.warm_up)

    # 5. Reap jobs orphaned by a previous worker crash (F4-minimal). Runs before
    #    the app accepts traffic so users never see permanently-stuck jobs.
    from core.database import AsyncSessionFactory
    from core.job_recovery import recover_stale_jobs
    try:
        async with AsyncSessionFactory() as recovery_db:
            await recover_stale_jobs(recovery_db)
    except Exception:  # noqa: BLE001 - startup recovery must never block boot
        import logging
        logging.getLogger("main").exception("Startup job recovery failed (non-fatal)")

    yield  # ── application runs ──

    # Shutdown: flush Langfuse traces
    langfuse.flush()


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    # Configure structured, request-id-aware logging before anything else so
    # startup and every subsequent log line carries a correlation id (F30).
    configure_logging()

    # Surface removed settings that are still set in the environment, so a stale
    # .env cannot silently change which provider is in effect.
    import logging as _logging
    for _warning in legacy_env_warnings():
        _logging.getLogger("main").warning("Config migration: %s", _warning)

    # Fail closed before building the app: a production deploy must run on real
    # Gemini (not mock, not ollama) and must not use a default/weak JWT secret
    # (F3, F6). No-op outside production.
    assert_safe_production_config()

    app = FastAPI(
        title="TutorForge AI",
        description="Grounded tutoring, assessment, and grading platform.",
        version="0.1.0",
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    # Assign/propagate the per-request correlation id. Bound before routing, so
    # it is available to every route handler and exception handler when they log.
    app.add_middleware(RequestIdMiddleware)

    # Rate limiting (F5). Added before CORS so CORS remains the outer layer and a
    # 429 still carries CORS headers (browsers can read the response).
    app.add_middleware(RateLimitMiddleware)

    # Security headers (F18) on every response, including rate-limited ones.
    app.add_middleware(SecurityHeadersMiddleware)

    # ── CORS ───────────────────────────────────────────────────────────────────
    # Origins are environment-driven (settings.cors_allowed_origins) so the
    # same code works for local dev (Vite on localhost:5173) and the
    # documented Render + Vercel split-origin deployment without a wildcard.
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(courses_router)
    app.include_router(assessments_router)
    app.include_router(grading_router)
    app.include_router(tutoring_router)

    return app


app = create_app()
