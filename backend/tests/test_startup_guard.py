"""
tests/test_startup_guard.py
---------------------------
Tests for the fail-closed production config guard (audit findings F3, F6).

The guard must be a no-op outside production (so dev/tests are unaffected) and
must refuse to start a production deploy that runs on a non-Gemini provider
(mock or the local-dev-only ollama) or a default/weak JWT secret.
"""

import pytest

from core.config import DEFAULT_JWT_SECRET, Settings, assert_safe_production_config


def _prod(**overrides) -> Settings:
    """A safe production Settings, with per-test overrides."""
    base = dict(
        environment="production",
        llm_provider="gemini",
        jwt_secret_key="k" * 40,  # strong, non-default
    )
    base.update(overrides)
    return Settings(**base)


def test_development_is_a_noop_even_when_unsafe():
    s = Settings(
        environment="development",
        llm_provider="mock",
        jwt_secret_key=DEFAULT_JWT_SECRET,
    )
    assert_safe_production_config(s)  # must not raise


def test_development_allows_ollama():
    """Ollama is a legitimate local-dev provider — the guard must not block it."""
    s = Settings(environment="development", llm_provider="ollama")
    assert_safe_production_config(s)  # must not raise


def test_safe_production_config_passes():
    assert_safe_production_config(_prod())  # must not raise


def test_production_rejects_mock_provider():
    with pytest.raises(RuntimeError) as exc:
        assert_safe_production_config(_prod(llm_provider="mock"))
    assert "LLM_PROVIDER" in str(exc.value)


def test_production_rejects_ollama_provider():
    """Ollama is dev-only: production must fail closed on it, like mock (F3)."""
    with pytest.raises(RuntimeError) as exc:
        assert_safe_production_config(_prod(llm_provider="ollama"))
    message = str(exc.value)
    assert "LLM_PROVIDER" in message
    assert "local-development-only" in message


def test_production_rejects_default_jwt_secret():
    with pytest.raises(RuntimeError) as exc:
        assert_safe_production_config(_prod(jwt_secret_key=DEFAULT_JWT_SECRET))
    assert "JWT_SECRET_KEY" in str(exc.value)


def test_production_rejects_short_jwt_secret():
    with pytest.raises(RuntimeError):
        assert_safe_production_config(_prod(jwt_secret_key="tooshort"))


def test_production_reports_all_problems_at_once():
    with pytest.raises(RuntimeError) as exc:
        assert_safe_production_config(
            _prod(llm_provider="mock", jwt_secret_key="tooshort")
        )
    message = str(exc.value)
    assert "LLM_PROVIDER" in message
    assert "JWT_SECRET_KEY" in message


def test_is_production_property():
    assert Settings(environment="production").is_production is True
    assert Settings(environment="Production ").is_production is True  # normalized
    assert Settings(environment="development").is_production is False
