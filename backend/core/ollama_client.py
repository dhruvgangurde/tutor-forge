"""
core/ollama_client.py
---------------------
Local Ollama provider — a LOCAL-DEVELOPMENT-ONLY stand-in for the Gemini clients.

╔══════════════════════════════════════════════════════════════════════════════╗
║  SCOPE WARNING — READ BEFORE USING                                           ║
║                                                                              ║
║  This provider exists so the project can be developed without a paid Gemini  ║
║  API budget. It is NOT a supported production backend, and it must NEVER be  ║
║  the provider behind the project's acceptance-criteria runs:                 ║
║                                                                              ║
║      - >=80% teacher acceptance of AI grade recommendations                  ║
║      - 0 out-of-corpus leakage (groundedness)                                ║
║      - 0 grading variance (determinism)                                      ║
║                                                                              ║
║  Any of those numbers must be produced against REAL Gemini                   ║
║  (LLM_PROVIDER=gemini) before being reported anywhere. Results measured on   ║
║  Ollama are development signal only.                                         ║
║                                                                              ║
║  assert_safe_production_config() enforces this: ENVIRONMENT=production with  ║
║  LLM_PROVIDER=ollama refuses to boot, exactly like the mock guard (F3).      ║
╚══════════════════════════════════════════════════════════════════════════════╝

Interface parity
----------------
Mirrors the Gemini wrappers in main.py (GeminiProClient / GeminiFlashClient) and
the mocks in core/mock_gemini.py exactly, so RetrievalService and every agent
node consume it without a single call-site change:

    generate(prompt, temperature=0.7, system_instruction="") -> str
    generate_deterministic(prompt)                           -> str   (temperature=0)
    embed(text)                                              -> list[float]

`system_instruction` maps to Ollama's top-level `system` field, which is the
closest equivalent to Gemini's system-instruction channel. Prompt-safety
wrapping (core/prompt_safety.wrap_untrusted) happens in the agent nodes, above
this layer, so it is provider-independent and needs no special handling here.

Known limitations, measured (see docs/OLLAMA-EVALUATION-2026-08-15.md)
---------------------------------------------------------------------
  - Determinism is WEAKER than Gemini. generate_deterministic() still forces
    temperature=0 (the invariant is enforced identically for every provider),
    but local models were observed to vary their non-JSON wrapper text between
    identical temperature-0 calls. We deliberately do NOT pin a seed or add
    output-normalising hacks to paper over this — it is a documented property
    of the dev provider, not something to engineer around.
  - Latency is far higher than Gemini: ~21s for hierarchy extraction warm, and
    a cold model load can add ~10-17s on top. Hence the separate, much larger
    OLLAMA_TIMEOUT_SECONDS (default 120s) and the startup warm-up call.
  - Only qwen2.5:7b-instruct was validated for generation. llama3:latest was
    explicitly DISQUALIFIED (unparseable JSON, unstable at temperature 0,
    hallucinated student answers while grading).

Transport
---------
Uses stdlib urllib rather than httpx/requests on purpose: httpx is a dev-only
dependency in pyproject.toml, and adding a runtime HTTP client would mean a new
pinned dependency plus a lockfile change for a dev-only provider. Calls are
synchronous and blocking, matching the google-generativeai SDK's behaviour, so
they stay safe inside LangGraph's executor-offloaded sync nodes.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from core.config import settings
from core.retry import call_with_retry

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """A non-retryable Ollama failure (bad request, model not pulled, 4xx)."""


class OllamaTransientError(RuntimeError):
    """A retryable Ollama failure (429, 5xx, connection reset, timeout)."""


# Retryable set, mirroring main._transient_gemini_errors(). urllib.error.HTTPError
# is a subclass of URLError, so _post() converts every HTTPError into either
# OllamaError or OllamaTransientError before it can escape — that way listing
# URLError here cannot accidentally retry a permanent 4xx.
_OLLAMA_TRANSIENT: tuple[type[BaseException], ...] = (
    OllamaTransientError,
    TimeoutError,
    ConnectionError,
    urllib.error.URLError,
)


class OllamaClient:
    """
    Ollama-backed generation + embedding client.

    Ollama has no Pro/Flash tier distinction, so main.py instantiates this once
    per Gemini role (pro, flash) with the same generation model. The class is
    stateless apart from configuration.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        timeout: float | None = None,
        num_ctx: int | None = None,
        role: str = "generation",
    ) -> None:
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model_name = model or settings.ollama_generation_model
        self._embedding_model = embedding_model or settings.ollama_embedding_model
        # Deliberately NOT settings.gemini_timeout_seconds (30s): local models are
        # an order of magnitude slower and a cold load alone can exceed 30s.
        self._timeout = timeout if timeout is not None else settings.ollama_timeout_seconds
        # Explicit context window sent on every generate call. Captured here (like
        # the fields above) rather than read at request time so a caller/test can
        # override it and so it doesn't depend on the settings singleton mid-call.
        self._num_ctx = num_ctx if num_ctx is not None else settings.ollama_num_ctx
        self._role = role
        logger.info(
            "[OLLAMA] Initialized role=%s model=%s embedding_model=%s base_url=%s "
            "timeout=%.0fs num_ctx=%d",
            role,
            self._model_name,
            self._embedding_model,
            self._base_url,
            self._timeout,
            self._num_ctx,
        )

    # ── Transport ─────────────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict) -> dict:
        """
        POST JSON to Ollama and return the decoded response.

        Converts HTTP failures into OllamaTransientError (retryable: 429/5xx) or
        OllamaError (permanent: everything else) so the retry policy above can
        distinguish them.
        """
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001 - error body is best-effort
                pass
            if exc.code == 429 or exc.code >= 500:
                raise OllamaTransientError(
                    f"Ollama {path} returned {exc.code}: {detail}"
                ) from exc
            raise OllamaError(
                f"Ollama {path} returned {exc.code}: {detail}. "
                f"Is model '{self._model_name}' pulled? Try: ollama pull {self._model_name}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama {path} returned a non-JSON body: {exc}") from exc

    # ── Generation ────────────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        system_instruction: str = "",
    ) -> str:
        """
        Generate text. Signature matches GeminiProClient/GeminiFlashClient exactly.

        `system_instruction` is threaded into Ollama's top-level `system` field,
        the analogue of Gemini's system-instruction channel. Empty string means
        no system field is sent at all (matching Gemini's `or None` behaviour).
        """
        payload: dict = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
            # num_ctx is sent explicitly rather than relying on Ollama's model
            # default (32768 for qwen2.5:7b-instruct), whose ~1.7GB KV cache
            # OOMs on an 8GB VRAM GPU. See settings.ollama_num_ctx for sizing.
            "options": {
                "temperature": temperature,
                "num_ctx": self._num_ctx,
            },
        }
        if system_instruction:
            payload["system"] = system_instruction

        def _call() -> str:
            data = self._post("/api/generate", payload)
            return data.get("response", "")

        return call_with_retry(
            _call,
            attempts=settings.gemini_max_attempts,
            base_delay=settings.gemini_retry_base_delay,
            max_delay=settings.gemini_retry_max_delay,
            retry_on=_OLLAMA_TRANSIENT,
            description=f"Ollama generate ({self._model_name})",
        )

    def generate_deterministic(self, prompt: str) -> str:
        """
        Enforces temperature=0 — required for all grading calls (FR-05.3).

        This is identical to the Gemini path on purpose. The invariant is
        "grading requests temperature 0", and it is enforced for every provider.
        Ollama's *observed* determinism is weaker than Gemini's (see the module
        docstring); that is a recorded limitation of the dev provider, and it is
        why Ollama must never produce a reported grading-variance number.
        """
        return self.generate(prompt, temperature=0.0)

    # ── Embeddings ────────────────────────────────────────────────────────────

    def embed(self, text: str) -> list[float]:
        """
        Embed text via OLLAMA_EMBEDDING_MODEL.

        nomic-embed-text returns 768 dimensions, matching text-embedding-004 and
        the mock's _MOCK_EMBED_DIM, so Chroma collections are dimensionally
        compatible across all three providers. The *similarity distribution* is
        NOT assumed to match — see settings.groundedness_threshold_ollama.
        """
        payload = {"model": self._embedding_model, "prompt": text}

        def _call() -> list[float]:
            data = self._post("/api/embeddings", payload)
            embedding = data.get("embedding") or []
            if not embedding:
                raise OllamaError(
                    f"Ollama returned an empty embedding for model "
                    f"'{self._embedding_model}'. Is it pulled? "
                    f"Try: ollama pull {self._embedding_model}"
                )
            return embedding

        return call_with_retry(
            _call,
            attempts=settings.gemini_max_attempts,
            base_delay=settings.gemini_retry_base_delay,
            max_delay=settings.gemini_retry_max_delay,
            retry_on=_OLLAMA_TRANSIENT,
            description=f"Ollama embed ({self._embedding_model})",
        )

    # ── Startup warm-up ───────────────────────────────────────────────────────

    def warm_up(self) -> None:
        """
        Issue a throwaway generate + embed so Ollama loads both models into
        memory before the first real request.

        Without this, the first user-facing call pays the cold model-load penalty
        (measured: a cold hierarchy-extraction call took 30.8s versus 21.1s warm,
        which would have blown the old 30s Gemini timeout outright).

        Non-fatal by design: a warm-up failure logs a clear warning but must not
        prevent the app from booting, mirroring how startup job recovery is
        treated in main.lifespan.
        """
        try:
            self.generate("warm-up", temperature=0.0)
            logger.info("[OLLAMA] Warm-up: generation model '%s' loaded", self._model_name)
        except Exception as exc:  # noqa: BLE001 - warm-up must never block boot
            logger.warning(
                "[OLLAMA] Warm-up generate failed for '%s' (non-fatal — the first real "
                "request will pay the cold-start cost): %s",
                self._model_name,
                exc,
            )
        try:
            self.embed("warm-up")
            logger.info("[OLLAMA] Warm-up: embedding model '%s' loaded", self._embedding_model)
        except Exception as exc:  # noqa: BLE001 - warm-up must never block boot
            logger.warning(
                "[OLLAMA] Warm-up embed failed for '%s' (non-fatal): %s",
                self._embedding_model,
                exc,
            )
