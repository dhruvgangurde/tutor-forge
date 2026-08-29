"""
tests/test_gemini_retry.py
--------------------------
Tests for bounded retry/backoff (F10): the generic core.retry.call_with_retry
helper, and its wiring into the real GeminiFlashClient (SDK model construction
stubbed — no network, no real API key needed).
"""

import pytest

from core.retry import call_with_retry


# ── call_with_retry: core reliability logic ───────────────────────────────────

class _Boom(Exception):
    """Retryable in these tests."""


class _Fatal(Exception):
    """Non-retryable in these tests."""


def _recorder():
    """Return (sleep_fn, delays_list) capturing backoff durations without waiting."""
    delays: list[float] = []
    return (lambda d: delays.append(d)), delays


def test_returns_immediately_on_success():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    result = call_with_retry(
        fn, attempts=4, base_delay=0.5, max_delay=8.0,
        retry_on=(_Boom,), sleep=sleep, jitter=lambda: 0.0,
    )
    assert result == "ok"
    assert calls["n"] == 1
    assert delays == []  # never slept


def test_retries_then_succeeds():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom("transient")
        return "recovered"

    result = call_with_retry(
        fn, attempts=4, base_delay=1.0, max_delay=8.0,
        retry_on=(_Boom,), sleep=sleep, jitter=lambda: 0.0,
    )
    assert result == "recovered"
    assert calls["n"] == 3
    assert len(delays) == 2  # slept before the two retries


def test_exhausts_and_reraises_real_exception():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Boom(f"still down {calls['n']}")

    with pytest.raises(_Boom) as exc:
        call_with_retry(
            fn, attempts=3, base_delay=1.0, max_delay=8.0,
            retry_on=(_Boom,), sleep=sleep, jitter=lambda: 0.0,
        )
    assert "still down 3" in str(exc.value)  # the final real error, not a wrapper
    assert calls["n"] == 3
    assert len(delays) == 2  # attempts-1 sleeps


def test_non_retryable_error_propagates_immediately():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Fatal("bad request")

    with pytest.raises(_Fatal):
        call_with_retry(
            fn, attempts=4, base_delay=1.0, max_delay=8.0,
            retry_on=(_Boom,), sleep=sleep, jitter=lambda: 0.0,
        )
    assert calls["n"] == 1  # no retries
    assert delays == []


def test_exponential_backoff_is_capped_by_max_delay():
    sleep, delays = _recorder()

    def fn():
        raise _Boom("down")

    with pytest.raises(_Boom):
        call_with_retry(
            fn, attempts=5, base_delay=1.0, max_delay=4.0,
            retry_on=(_Boom,), sleep=sleep, jitter=lambda: 0.0,  # jitter->*0.5
        )
    # backoff sequence 1,2,4,8 -> capped at 4 -> *0.5 jitter => 0.5,1.0,2.0,2.0
    assert delays == [0.5, 1.0, 2.0, 2.0]


def test_jitter_widens_delay_window():
    sleep, delays = _recorder()

    def fn():
        raise _Boom("down")

    with pytest.raises(_Boom):
        call_with_retry(
            fn, attempts=2, base_delay=2.0, max_delay=100.0,
            retry_on=(_Boom,), sleep=sleep, jitter=lambda: 1.0,  # *1.5
        )
    assert delays == [3.0]  # 2.0 * (0.5 + 1.0)


def test_invalid_attempts_rejected():
    with pytest.raises(ValueError):
        call_with_retry(
            lambda: None, attempts=0, base_delay=1.0, max_delay=1.0,
            retry_on=(_Boom,),
        )


# ── Real client wiring (no network) ───────────────────────────────────────────

class _FakeResponse:
    text = "generated text"


class _FakeModel:
    """Stand-in for genai.GenerativeModel; fails `fail_times` then succeeds."""

    def __init__(self, *_a, **_k):
        pass

    def generate_content(self, *_a, **_k):
        from google.api_core import exceptions as gexc
        _FakeModel.calls += 1
        if _FakeModel.calls <= _FakeModel.fail_times:
            raise gexc.ServiceUnavailable("temporarily unavailable")
        return _FakeResponse()

    calls = 0
    fail_times = 0


@pytest.fixture
def fast_retry(monkeypatch):
    """Make retries instant and deterministic for client tests."""
    from core.config import settings
    monkeypatch.setattr(settings, "gemini_max_attempts", 3, raising=False)
    monkeypatch.setattr(settings, "gemini_retry_base_delay", 0.0, raising=False)
    monkeypatch.setattr(settings, "gemini_retry_max_delay", 0.0, raising=False)


def _install_fake_model(monkeypatch, fail_times: int):
    import google.generativeai as genai
    _FakeModel.calls = 0
    _FakeModel.fail_times = fail_times
    monkeypatch.setattr(genai, "GenerativeModel", _FakeModel)


def test_flash_client_retries_then_succeeds(monkeypatch, fast_retry):
    from main import GeminiFlashClient

    _install_fake_model(monkeypatch, fail_times=2)
    client = GeminiFlashClient(api_key="test-key", model="gemini-2.5-flash")

    result = client.generate("hello")
    assert result == "generated text"
    assert _FakeModel.calls == 3  # 2 failures + 1 success


def test_flash_client_raises_after_exhaustion(monkeypatch, fast_retry):
    from google.api_core import exceptions as gexc
    from main import GeminiFlashClient

    _install_fake_model(monkeypatch, fail_times=99)  # always fails
    client = GeminiFlashClient(api_key="test-key", model="gemini-2.5-flash")

    with pytest.raises(gexc.ServiceUnavailable):
        client.generate("hello")
    assert _FakeModel.calls == 3  # exactly gemini_max_attempts
