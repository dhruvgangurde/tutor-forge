"""
core/config.py
--------------
Application settings loaded from environment variables via pydantic-settings.
No model names, paths, or secrets are hardcoded anywhere else in the codebase —
all configuration is read from `settings.*`.

Usage:
    from core.config import settings
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:password@localhost:5432/tutorforge"

    # ── Gemini ────────────────────────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_pro_model: str = "gemini-2.5-pro"
    gemini_flash_model: str = "gemini-2.5-flash"
    embedding_model: str = "text-embedding-004"

    # ── ChromaDB ──────────────────────────────────────────────────────────────
    # Path is relative to the working directory (backend/) when uvicorn is launched.
    # Override via CHROMA_PERSIST_PATH env var.
    chroma_persist_path: str = "data/chroma"

    # ── Ollama (LOCAL DEV ONLY) ───────────────────────────────────────────────
    # Local stand-in provider so the project can be developed without a paid
    # Gemini budget. NEVER valid in production — assert_safe_production_config()
    # rejects it, and no acceptance-criteria number (teacher acceptance, zero
    # leakage, zero grading variance) may be measured on it.
    # See docs/OLLAMA_DEV_SETUP.md and docs/OLLAMA-EVALUATION-2026-08-15.md.
    ollama_base_url: str = "http://localhost:11434"
    ollama_generation_model: str = "qwen2.5:7b-instruct"
    ollama_embedding_model: str = "nomic-embed-text"
    # Local models are ~10x slower than Gemini and a cold model load alone can
    # exceed the 30s Gemini deadline, so Ollama gets its own, much larger budget.
    ollama_timeout_seconds: float = 120.0
    # Context window (num_ctx) sent explicitly on every /api/generate call.
    # Ollama otherwise defaults qwen2.5:7b-instruct to 32768 tokens, whose KV
    # cache (~1.7GB) fails to allocate on an 8GB VRAM GPU (cudaMalloc OOM). This
    # app never needs that much: retrieval/service.build_context_window caps
    # retrieved context at a 4000-token budget, so 8192 leaves ample headroom
    # for prompt overhead + system instructions while keeping the KV cache small
    # enough to load alongside the model. Raise only if prompts grow materially.
    ollama_num_ctx: int = 8192

    # ── Retrieval / groundedness ──────────────────────────────────────────────
    # Minimum top retrieval confidence for a topic to be considered "grounded"
    # in course material. Below this, agents refuse generation (no LLM call).
    # One value per provider because each embedding backend has its own
    # similarity scale — the numbers are NOT interchangeable.
    groundedness_threshold: float = 0.65        # real Gemini embeddings
    groundedness_threshold_mock: float = 0.20   # mock stub: bag-of-words ceiling ~0.42,
                                                 # off-topic noise ~0.0-0.1
    # EMPIRICALLY MEASURED 2026-08-16 against nomic-embed-text on the two live
    # ingested courses (Earth, 36 chunks; DSA, 152 chunks). 78 queries total:
    #
    #   on-topic (n=34, drawn from each course's own stored chapter/concept
    #             names plus natural-language student questions)
    #       min 0.5346 · p05 0.6010 · median 0.7286 · max 0.8132
    #   negatives (n=44 = 20 cross-course + 24 unrelated-domain controls)
    #       min 0.3887 · median 0.4610 · p95 0.5038 · max 0.5342
    #
    # The classes are separable but LOPSIDED: on-topic has a single low outlier
    # at 0.5346 ("Surface Features", a bare two-word concept label) and then
    # jumps to 0.6010, while the negatives top out at 0.5342. That leaves an
    # empty band of (0.5346, 0.6010) containing no sample of either class, so
    # the naive midpoint of the raw extremes (~0.5344) would carry essentially
    # zero margin. 0.57 sits mid-band instead: +0.036 above the highest observed
    # negative and -0.031 below the on-topic bulk floor.
    #
    # Cost at 0.57: 1/34 false refusals (2.9%, the bare-label outlier above —
    # its natural-language sibling scores 0.6877) and 0/44 false accepts, which
    # keeps the AC-02 zero-leakage side intact. The previous placeholder 0.65
    # cost 7/34 (20.6%) false refusals, including the topic "Dynamic
    # Programming" at 0.6245 — the failure that prompted this measurement.
    # Re-measure if the embedding model changes; the number is scale-specific.
    groundedness_threshold_ollama: float = 0.57

    # ── Langfuse ──────────────────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    # Refresh tokens (F19): long-lived, revocable, rotated on each use.
    jwt_refresh_expire_minutes: int = 60 * 24 * 7  # 7 days

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins. No wildcard default in
    # production — the frontend and backend are deployed to separate origins
    # (Vercel + Render), so this must be set explicitly per environment.
    cors_allowed_origins: str = "http://localhost:5173"

    # ── Evaluation ────────────────────────────────────────────────────────────
    # When True, eval tests use the real Gemini API instead of mocked responses.
    eval_use_real_gemini: bool = False

    # ── Gemini reliability (F10) ──────────────────────────────────────────────
    # Bounded retry with exponential backoff + jitter around every Gemini call,
    # plus a per-request timeout. Transient failures (429/503/timeout) are
    # retried; once attempts are exhausted the real error propagates so the
    # background job is marked "failed" rather than hanging.
    gemini_max_attempts: int = 4
    gemini_retry_base_delay: float = 0.5     # seconds; first backoff
    gemini_retry_max_delay: float = 8.0      # seconds; backoff ceiling
    gemini_timeout_seconds: float = 30.0     # per-request deadline

    # ── Background job recovery (F4-minimal) ──────────────────────────────────
    # A job left in an in-flight state (assessment "generating", ingestion
    # "pending"/"running") older than this is considered orphaned by a crashed
    # worker and is marked "failed" at startup. Keep comfortably above the
    # longest expected job duration.
    job_stale_after_seconds: int = 1800      # 30 minutes

    # ── Rate limiting (F5) ────────────────────────────────────────────────────
    # Fixed-window limits. Auth endpoints are keyed by client IP (unauthenticated);
    # AI-intensive endpoints are keyed by user id when authenticated.
    rate_limit_enabled: bool = True
    rate_limit_auth_max: int = 10            # login/register attempts per window
    rate_limit_auth_window_seconds: int = 60
    rate_limit_ai_max: int = 20              # generate/grade/chat/hint/upload per window
    rate_limit_ai_window_seconds: int = 60
    # Trust X-Forwarded-For for the client address only when the app is
    # deployed behind a reverse proxy that sets it (TRUSTED_PROXY=true).
    # Otherwise the header is ignored: anyone can send it, and honouring it
    # let a client pick a fresh rate-limit bucket per request (audit #7b).
    trusted_proxy: bool = False

    # ── Security headers (F18) ────────────────────────────────────────────────
    security_headers_enabled: bool = True

    # ── Uploads (F15) ─────────────────────────────────────────────────────────
    # Per-file cap enforced during read (bounded memory; oversized => 413) and a
    # cap on the number of files per request.
    max_upload_bytes: int = 50 * 1024 * 1024   # 50 MB
    max_upload_files: int = 20

    # ── LLM provider selection ────────────────────────────────────────────────
    # Replaces the old binary USE_MOCK_GEMINI flag with an explicit three-way
    # choice. Defaults to "mock" so existing dev workflows and the offline test
    # suite are unchanged unless a provider is opted into deliberately.
    #
    #   "mock"   — deterministic in-process stub. No network, no quota. The only
    #              provider the pytest suite runs against.
    #   "gemini" — real Gemini API. The ONLY provider valid in production, and
    #              the only one whose results may be reported as acceptance
    #              criteria (teacher acceptance, zero leakage, zero variance).
    #   "ollama" — LOCAL DEV ONLY. Free local models via core/ollama_client.py.
    #              Rejected in production. See docs/OLLAMA_DEV_SETUP.md.
    llm_provider: Literal["mock", "gemini", "ollama"] = "mock"

    # ── Deployment environment ────────────────────────────────────────────────
    # "development" | "staging" | "production". Drives fail-closed boot guards
    # (see assert_safe_production_config): a production deploy must not run on
    # mock AI or a default/weak JWT secret.
    environment: str = "development"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def active_groundedness_threshold(self) -> float:
        """
        The groundedness bar for the currently selected provider.

        Single source of truth: every gate that needs a threshold should read it
        from here rather than picking a field itself, so flipping LLM_PROVIDER
        can never leave a caller reading the wrong provider's number.

        Both gates now read this — the assessment agent's and the tutor's
        check_groundedness_node (Critical #1 in docs/BUG-AUDIT-2026-08-15.md,
        fixed). RetrievalService.is_grounded() takes a required keyword-only
        threshold, so a future call site cannot silently fall through to an
        unconfigured default the way the tutor's did.
        """
        if self.llm_provider == "mock":
            return self.groundedness_threshold_mock
        if self.llm_provider == "ollama":
            return self.groundedness_threshold_ollama
        return self.groundedness_threshold


# The placeholder JWT secret shipped in config defaults and .env.example. A
# production deploy that still carries this value can have its tokens forged.
DEFAULT_JWT_SECRET = "change-me-in-production"
_MIN_PROD_JWT_SECRET_LEN = 32

# The pre-LLM_PROVIDER flag. Detected explicitly so a stale .env cannot silently
# flip a developer who had USE_MOCK_GEMINI=false (real Gemini) onto the new
# "mock" default without them noticing.
_LEGACY_MOCK_ENV_VAR = "USE_MOCK_GEMINI"


def _dotenv_path() -> Path | None:
    """The .env file pydantic-settings loads, if it exists (relative to cwd)."""
    configured = Settings.model_config.get("env_file") or ".env"
    # env_file may be a single path or a sequence; take the first that exists.
    candidates = [configured] if isinstance(configured, (str, os.PathLike)) else list(configured)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def _dotenv_keys() -> set[str]:
    """
    Upper-cased keys present in the .env file, or an empty set if there is none.

    Necessary because pydantic-settings parses the dotenv file itself and never
    exports it into os.environ. A membership test against os.environ therefore
    cannot see a setting that lives *only* in .env — which is precisely where a
    stale flag lives. Keys are upper-cased because the model is configured
    case_sensitive=False, so USE_MOCK_GEMINI and use_mock_gemini are the same
    setting as far as loading is concerned.
    """
    path = _dotenv_path()
    if path is None:
        return set()
    try:
        return {key.upper() for key in dotenv_values(path)}
    except OSError:  # unreadable .env must never break startup
        return set()


def legacy_env_warnings() -> list[str]:
    """
    Return migration warnings for removed settings that are still present in the
    environment. Non-fatal outside production; create_app() logs each one.

    Checks BOTH the process environment and the .env file. Checking only
    os.environ was a blind spot that made this warning almost unreachable in
    development: a developer's stale USE_MOCK_GEMINI normally lives in .env, and
    the warning only fired in the rare case where someone had also exported it
    as a shell variable.
    """
    warnings: list[str] = []

    found_in: list[str] = []
    if _LEGACY_MOCK_ENV_VAR in os.environ:
        found_in.append("the process environment")
    if _LEGACY_MOCK_ENV_VAR in _dotenv_keys():
        found_in.append(str(_dotenv_path()))

    if found_in:
        warnings.append(
            f"{_LEGACY_MOCK_ENV_VAR} is set in {' and '.join(found_in)} but no "
            f"longer has any effect. It was replaced by LLM_PROVIDER "
            f"(mock|gemini|ollama). Current provider: '{settings.llm_provider}'. "
            f"Set LLM_PROVIDER explicitly and remove {_LEGACY_MOCK_ENV_VAR}."
        )
    return warnings


def assert_safe_production_config(s: "Settings | None" = None) -> None:
    """
    Fail closed at startup if a *production* deploy is misconfigured (F3, F6).

    No-op outside production, so local dev and tests are unaffected. Raises
    RuntimeError listing every problem so the operator sees all of them at once.
    """
    s = s or settings
    if not s.is_production:
        return

    problems: list[str] = []
    if s.llm_provider == "mock":
        problems.append(
            "LLM_PROVIDER must be 'gemini' in production — 'mock' returns canned "
            "hierarchies and lexical stub embeddings, not real AI (F3)."
        )
    elif s.llm_provider == "ollama":
        problems.append(
            "LLM_PROVIDER must be 'gemini' in production — 'ollama' is a "
            "local-development-only provider. It is unvalidated for production "
            "use, and no acceptance-criteria result (teacher acceptance, zero "
            "leakage, zero grading variance) may be measured on it."
        )
    if _LEGACY_MOCK_ENV_VAR in os.environ:
        problems.append(
            f"{_LEGACY_MOCK_ENV_VAR} is set but was replaced by LLM_PROVIDER. "
            f"Remove it and set LLM_PROVIDER=gemini explicitly, so the provider "
            f"in effect is unambiguous."
        )
    if s.jwt_secret_key == DEFAULT_JWT_SECRET or len(s.jwt_secret_key) < _MIN_PROD_JWT_SECRET_LEN:
        problems.append(
            f"JWT_SECRET_KEY must be a non-default secret of at least "
            f"{_MIN_PROD_JWT_SECRET_LEN} characters in production (F6)."
        )

    if problems:
        raise RuntimeError(
            "Refusing to start: unsafe production configuration:\n  - "
            + "\n  - ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()


# Module-level singleton — import this everywhere
settings: Settings = get_settings()
