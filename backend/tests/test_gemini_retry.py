"""
tests/test_gemini_retry.py
--------------------------
Tests for bounded retry/backoff (F10): the generic core.retry.call_with_retry
helper, and its wiring into the real GeminiFlashClient (SDK model construction
stubbed — no network, no real API key needed).
"""

from typing import ClassVar

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


# ── Honouring the server's retry delay (Gemini round 2) ───────────────────────


def test_delay_hint_is_never_undercut():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom()
        return "ok"

    assert call_with_retry(
        fn, attempts=4, base_delay=0.5, max_delay=8.0, retry_on=(_Boom,),
        sleep=sleep, jitter=lambda: 0.5, delay_hint=lambda exc: 20.0,
    ) == "ok"
    assert delays == [20.0, 20.0]


def test_backoff_wins_when_longer_than_the_hint():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _Boom()
        return "ok"

    call_with_retry(
        fn, attempts=3, base_delay=5.0, max_delay=8.0, retry_on=(_Boom,),
        sleep=sleep, jitter=lambda: 0.5, delay_hint=lambda exc: 1.0,
    )
    assert delays == [5.0]


def test_total_wait_cap_stops_before_a_wait_that_would_pass_it():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Boom()

    with pytest.raises(_Boom):
        call_with_retry(
            fn, attempts=5, base_delay=0.0, max_delay=0.0, retry_on=(_Boom,),
            sleep=sleep, delay_hint=lambda exc: 40.0, max_total_wait=60.0,
        )
    assert delays == [40.0]
    assert calls["n"] == 2


def test_stop_if_reraises_without_retrying():
    sleep, delays = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Boom()

    with pytest.raises(_Boom):
        call_with_retry(
            fn, attempts=4, base_delay=0.0, max_delay=0.0, retry_on=(_Boom,),
            sleep=sleep, stop_if=lambda exc: True,
        )
    assert calls["n"] == 1
    assert delays == []


def test_attempts_for_lowers_the_cap_for_one_kind_of_failure():
    sleep, _ = _recorder()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Boom()

    with pytest.raises(_Boom):
        call_with_retry(
            fn, attempts=4, base_delay=0.0, max_delay=0.0, retry_on=(_Boom,),
            sleep=sleep, attempts_for=lambda exc: 2,
        )
    assert calls["n"] == 2


# ── Gemini clients: 429s, daily quota, timeouts (no network) ──────────────────


def _quota_error(delay_seconds=20, per_day=False):
    from google.api_core import exceptions as gexc
    from google.protobuf import duration_pb2
    from google.rpc import error_details_pb2

    quota = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if per_day else (
        "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    )
    details = [error_details_pb2.RetryInfo(retry_delay=duration_pb2.Duration(seconds=delay_seconds))]
    return gexc.ResourceExhausted(f"You exceeded your current quota. quota_id: {quota}", details=details)


class _ScriptedModel:
    """genai.GenerativeModel stand-in that raises the scripted errors, then answers."""

    script: ClassVar[list] = []
    calls = 0

    def __init__(self, *_a, **_k):
        pass

    def generate_content(self, *_a, **_k):
        _ScriptedModel.calls += 1
        if _ScriptedModel.script:
            raise _ScriptedModel.script.pop(0)
        return _FakeResponse()


@pytest.fixture
def scripted(monkeypatch):
    import time

    import google.generativeai as genai

    from core.config import settings

    slept: list[float] = []
    monkeypatch.setattr(genai, "GenerativeModel", _ScriptedModel)
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(settings, "gemini_max_attempts", 4, raising=False)
    monkeypatch.setattr(settings, "gemini_retry_base_delay", 0.5, raising=False)
    monkeypatch.setattr(settings, "gemini_retry_max_delay", 8.0, raising=False)
    monkeypatch.setattr(settings, "gemini_max_wait_seconds", 60.0, raising=False)
    _ScriptedModel.calls = 0
    _ScriptedModel.script = []
    return slept


def test_gemini_429_waits_at_least_the_delay_the_server_asked_for(scripted):
    from main import GeminiFlashClient

    _ScriptedModel.script = [_quota_error(20)]
    client = GeminiFlashClient(api_key="test-key", model="gemini-flash-lite-latest")

    assert client.generate("hello") == "generated text"
    assert _ScriptedModel.calls == 2
    assert len(scripted) == 1 and scripted[0] >= 20.0


@pytest.mark.parametrize(
    "message",
    [
        "429 You exceeded your current quota. Please retry in 20.468s.",
        '429 quota exceeded {"retryDelay": "20s"}',
    ],
)
def test_retry_delay_is_read_from_the_error_text_too(message):
    from google.api_core import exceptions as gexc

    from core.gemini_calls import retry_delay_seconds

    assert retry_delay_seconds(gexc.ResourceExhausted(message)) >= 20.0


def test_retry_after_header_is_honoured():
    from google.api_core import exceptions as gexc

    from core.gemini_calls import retry_delay_seconds

    class _Response:
        headers: ClassVar[dict] = {"Retry-After": "17"}

    assert retry_delay_seconds(gexc.TooManyRequests("slow down", response=_Response())) == 17.0


def test_gemini_429_gives_up_as_busy_when_the_wait_would_pass_the_cap(scripted):
    from core.config import settings
    from core.exceptions import AI_BUSY_MESSAGE, AIServiceBusy
    from main import GeminiFlashClient

    settings.gemini_max_wait_seconds = 30.0
    _ScriptedModel.script = [_quota_error(20), _quota_error(20), _quota_error(20)]
    client = GeminiFlashClient(api_key="test-key", model="gemini-flash-lite-latest")

    with pytest.raises(AIServiceBusy) as caught:
        client.generate("hello")
    assert _ScriptedModel.calls == 2          # one wait of 20s fitted, the next did not
    assert scripted == [pytest.approx(20.0, abs=1.0)]
    assert str(caught.value) == AI_BUSY_MESSAGE
    assert "quota" not in str(caught.value).lower()
    assert "rate limited" in caught.value.reason


def test_gemini_daily_quota_fails_fast(scripted):
    from core.exceptions import AIServiceBusy
    from main import GeminiProClient

    _ScriptedModel.script = [_quota_error(20, per_day=True)]
    client = GeminiProClient(api_key="test-key", model="gemini-flash-lite-latest")

    with pytest.raises(AIServiceBusy) as caught:
        client.generate("hello")
    assert _ScriptedModel.calls == 1
    assert scripted == []
    assert caught.value.reason.endswith("daily quota reached")


def test_timed_out_generation_is_retried_at_most_once(scripted):
    from google.api_core import exceptions as gexc

    from core.exceptions import AIServiceBusy
    from main import GeminiProClient

    _ScriptedModel.script = [gexc.DeadlineExceeded("deadline")] * 4
    client = GeminiProClient(api_key="test-key", model="gemini-flash-lite-latest")

    with pytest.raises(AIServiceBusy) as caught:
        client.generate("a long outline")
    assert _ScriptedModel.calls == 2
    assert "timed out" in caught.value.reason


def test_non_capacity_errors_still_propagate_unchanged(scripted):
    from google.api_core import exceptions as gexc

    from main import GeminiFlashClient

    _ScriptedModel.script = [gexc.InternalServerError("boom")] * 4
    client = GeminiFlashClient(api_key="test-key", model="gemini-flash-lite-latest")

    with pytest.raises(gexc.InternalServerError):
        client.generate("hello")
    assert _ScriptedModel.calls == 4


def test_retry_logs_keep_only_the_first_line_of_the_provider_message(caplog):
    import logging

    sleep, _ = _recorder()
    message = (
        "429 You exceeded your current quota.\n"
        "  * Quota exceeded for metric: embed_content_free_tier_requests, limit: 100\n"
        "  Learn more: https://ai.google.dev/gemini-api/docs/rate-limits"
    )

    def fn():
        raise _Boom(message)

    with caplog.at_level(logging.WARNING, logger="core.retry"), pytest.raises(_Boom):
        call_with_retry(
            fn, attempts=2, base_delay=0.0, max_delay=0.0, retry_on=(_Boom,), sleep=sleep,
            description="Gemini embed",
        )

    logged = [r.getMessage() for r in caplog.records]
    assert len(logged) == 2  # one retry warning, one give-up error
    for line in logged:
        assert line.endswith("429 You exceeded your current quota.")
        assert "https://" not in line and "metric" not in line
