"""
tests/test_throttle.py
----------------------
Client-side Gemini pacing (core/throttle.py). A fake clock and sleep stand in
for real time; no network, no real SDK call.
"""

import pytest

from core import throttle
from core.exceptions import AI_BUSY_MESSAGE, AIServiceBusy


class _FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time(monkeypatch):
    ft = _FakeTime()
    monkeypatch.setattr(throttle, "_clock", ft.clock)
    monkeypatch.setattr(throttle, "_sleep", ft.sleep)
    throttle.reset()
    yield ft
    throttle.reset()


@pytest.fixture
def pacing(monkeypatch):
    """Re-enable pacing (the suite turns it off in conftest)."""
    from core.config import settings

    monkeypatch.setattr(settings, "gemini_generate_rpm", 5)
    monkeypatch.setattr(settings, "gemini_embed_rpm", 90)
    monkeypatch.setattr(settings, "gemini_max_wait_seconds", 60.0)
    return settings


def test_calls_within_the_limit_do_not_wait(fake_time):
    limiter = throttle.SlidingWindowLimiter(5, clock=fake_time.clock, sleep=fake_time.sleep)
    for _ in range(5):
        limiter.acquire(max_wait=60)
    assert fake_time.slept == []


def test_the_next_call_waits_for_the_oldest_slot_to_free(fake_time):
    limiter = throttle.SlidingWindowLimiter(5, clock=fake_time.clock, sleep=fake_time.sleep)
    for _ in range(5):
        limiter.acquire(max_wait=60)
        fake_time.now += 2.0  # five calls over ten seconds
    limiter.acquire(max_wait=60)
    assert fake_time.slept == [pytest.approx(50.0)]  # 60s after the first call


def test_a_wait_past_the_cap_fails_as_busy_without_sleeping(fake_time):
    limiter = throttle.SlidingWindowLimiter(2, clock=fake_time.clock, sleep=fake_time.sleep)
    limiter.acquire(max_wait=10)
    limiter.acquire(max_wait=10)
    with pytest.raises(AIServiceBusy) as caught:
        limiter.acquire(max_wait=10)
    assert fake_time.slept == []
    assert str(caught.value) == AI_BUSY_MESSAGE
    assert "rate limit of 2" in caught.value.reason


def test_invalid_limit_is_rejected():
    with pytest.raises(ValueError):
        throttle.SlidingWindowLimiter(0)


def test_each_model_and_kind_has_its_own_window(fake_time, pacing):
    pacing.gemini_generate_rpm = 1
    pacing.gemini_embed_rpm = 1
    throttle.throttle(throttle.GENERATE, "model-a")
    throttle.throttle(throttle.GENERATE, "model-b")
    throttle.throttle(throttle.EMBED, "model-a")
    assert fake_time.slept == []
    throttle.throttle(throttle.GENERATE, "model-a")
    assert fake_time.slept == [pytest.approx(60.0)]


def test_zero_turns_pacing_off(fake_time, pacing):
    pacing.gemini_generate_rpm = 0
    for _ in range(50):
        throttle.throttle(throttle.GENERATE, "model-a")
    assert fake_time.slept == []


# ── Wired into the real clients (fake SDK) ────────────────────────────────────


class _FakeResponse:
    text = "generated text"


class _FakeModel:
    calls = 0

    def __init__(self, *_a, **_k):
        pass

    def generate_content(self, *_a, **_k):
        _FakeModel.calls += 1
        return _FakeResponse()


def test_pro_and_flash_on_the_same_model_share_one_window(monkeypatch, fake_time, pacing):
    import google.generativeai as genai

    from main import GeminiFlashClient, GeminiProClient

    monkeypatch.setattr(genai, "GenerativeModel", _FakeModel)
    _FakeModel.calls = 0
    pacing.gemini_generate_rpm = 2
    pro = GeminiProClient(api_key="test-key", model="gemini-flash-lite-latest")
    flash = GeminiFlashClient(api_key="test-key", model="gemini-flash-lite-latest")

    pro.generate("one")
    flash.generate("two")
    assert fake_time.slept == []
    pro.generate("three")
    assert fake_time.slept == [pytest.approx(60.0)]
    assert _FakeModel.calls == 3


def test_client_fails_as_busy_when_no_slot_comes_in_time(monkeypatch, fake_time, pacing):
    import google.generativeai as genai

    from main import GeminiFlashClient

    monkeypatch.setattr(genai, "GenerativeModel", _FakeModel)
    _FakeModel.calls = 0
    pacing.gemini_generate_rpm = 1
    pacing.gemini_max_wait_seconds = 5.0
    client = GeminiFlashClient(api_key="test-key", model="gemini-flash-lite-latest")

    client.generate("one")
    with pytest.raises(AIServiceBusy):
        client.generate("two")
    assert _FakeModel.calls == 1


def test_embeddings_are_paced_on_their_own_limit(monkeypatch, fake_time, pacing):
    import google.generativeai as genai

    from main import GeminiProClient

    monkeypatch.setattr(genai, "embed_content", lambda **kwargs: {"embedding": [0.1] * 4})
    pacing.gemini_embed_rpm = 1
    client = GeminiProClient(api_key="test-key", model="gemini-flash-lite-latest")

    client.embed("one")
    client.embed("two")
    assert fake_time.slept == [pytest.approx(60.0)]
