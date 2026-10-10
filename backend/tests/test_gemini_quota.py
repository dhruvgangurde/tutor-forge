"""
tests/test_gemini_quota.py
--------------------------
Gemini free-tier embedding quota, replayed offline.

The fake endpoint below enforces what a real upload showed: at most 100 texts
per minute per model, every text in a batch counts as one request, and a
rejected request still counts. A fake clock stands in for real time in both the
retry sleeps and the client-side pacing. No network, no real SDK call.
"""

import pytest

from core import throttle


class _FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.start = self.now

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    @property
    def elapsed(self) -> float:
        return self.now - self.start


class _QuotaAPI:
    """genai.embed_content stand-in: 100 texts per rolling minute, failures included."""

    def __init__(self, fake_time: _FakeTime, per_minute: int = 100) -> None:
        self.time = fake_time
        self.per_minute = per_minute
        self.log: list[tuple[float, int, bool]] = []  # (time, texts, accepted)

    def embed_content(self, **kwargs):
        from google.api_core import exceptions as gexc

        content = kwargs["content"]
        n = len(content) if isinstance(content, list) else 1
        now = self.time.now
        recent = [(ts, k) for ts, k, _ in self.log if now - ts < 60]
        accepted = sum(k for _, k in recent) + n <= self.per_minute
        self.log.append((now, n, accepted))
        if not accepted:
            wait = min(ts for ts, _ in recent) + 60 - now
            raise gexc.ResourceExhausted(
                "You exceeded your current quota.\n"
                f"Please retry in {wait:.2f}s.\n"
                "  * Quota exceeded for metric: embed_content_free_tier_requests, limit: 100\n"
                "  Learn more: https://ai.google.dev/gemini-api/docs/rate-limits"
            )
        vectors = [[1.0, 0.0] for _ in range(n)]
        return {"embedding": vectors if isinstance(content, list) else vectors[0]}

    @property
    def rejected(self) -> int:
        return sum(1 for _, _, ok in self.log if not ok)


@pytest.fixture
def fake_time(monkeypatch):
    import time

    ft = _FakeTime()
    monkeypatch.setattr(time, "sleep", ft.sleep)          # retry sleeps (core.gemini_calls)
    monkeypatch.setattr(throttle, "_clock", ft.clock)      # pacing
    monkeypatch.setattr(throttle, "_sleep", ft.sleep)
    throttle.reset()
    yield ft
    throttle.reset()


@pytest.fixture
def defaults(monkeypatch):
    """The shipped defaults, with pacing on (the suite turns it off in conftest)."""
    from core.config import Settings, settings

    for name in (
        "gemini_generate_rpm",
        "gemini_embed_rpm",
        "embedding_batch_size",
        "gemini_max_wait_seconds",
        "gemini_max_attempts",
        "gemini_retry_base_delay",
        "gemini_retry_max_delay",
    ):
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)
    return settings


@pytest.fixture
def quota_api(monkeypatch, fake_time):
    import google.generativeai as genai

    api = _QuotaAPI(fake_time)
    monkeypatch.setattr(genai, "embed_content", api.embed_content)
    return api


def _client():
    from main import GeminiProClient

    return GeminiProClient(api_key="test-key", model="gemini-flash-lite-latest")


# ── 1. Pacing counts texts, not calls ─────────────────────────────────────────


def test_152_chunks_finish_in_two_windows_without_a_429(defaults, fake_time, quota_api):
    vectors = _client().embed_documents([f"chunk {i}" for i in range(152)])

    assert len(vectors) == 152
    assert quota_api.rejected == 0
    assert [(n, ok) for _, n, ok in quota_api.log] == [(90, True), (62, True)]
    assert fake_time.elapsed == pytest.approx(60.0)   # the second batch waits one window


def test_effective_batch_is_the_smaller_of_batch_size_and_per_minute_limit(defaults, fake_time, quota_api):
    defaults.gemini_embed_rpm = 40
    _client().embed_documents([f"c{i}" for i in range(100)])
    assert [n for _, n, _ in quota_api.log] == [40, 40, 20]
    assert quota_api.rejected == 0


def test_batch_size_alone_applies_when_pacing_is_off(defaults, fake_time, quota_api):
    defaults.gemini_embed_rpm = 0
    defaults.embedding_batch_size = 50
    _client().embed_documents([f"c{i}" for i in range(100)])
    assert [n for _, n, _ in quota_api.log] == [50, 50]


def test_weighted_accounting_admits_up_to_the_limit_then_waits(fake_time):
    limiter = throttle.SlidingWindowLimiter(100, clock=fake_time.clock, sleep=fake_time.sleep)
    limiter.acquire(max_wait=300, weight=60)
    limiter.acquire(max_wait=300, weight=40)
    assert fake_time.elapsed == 0
    limiter.acquire(max_wait=300, weight=1)
    assert fake_time.elapsed == pytest.approx(60.0)


def test_weighted_wait_frees_only_as_much_as_it_needs(fake_time):
    limiter = throttle.SlidingWindowLimiter(100, clock=fake_time.clock, sleep=fake_time.sleep)
    limiter.acquire(max_wait=300, weight=50)       # t = 0
    fake_time.now += 30
    limiter.acquire(max_wait=300, weight=50)       # t = 30
    fake_time.now += 10
    limiter.acquire(max_wait=300, weight=30)       # needs the t=0 batch to expire
    assert fake_time.elapsed == pytest.approx(60.0)


def test_a_request_heavier_than_the_limit_is_an_error(fake_time):
    limiter = throttle.SlidingWindowLimiter(90, clock=fake_time.clock, sleep=fake_time.sleep)
    with pytest.raises(ValueError, match="never fit"):
        limiter.acquire(max_wait=300, weight=91)


def test_generation_still_takes_one_slot_per_call(defaults, fake_time):
    defaults.gemini_generate_rpm = 2
    throttle.throttle(throttle.GENERATE, "gemini-flash-lite-latest")
    throttle.throttle(throttle.GENERATE, "gemini-flash-lite-latest")
    assert fake_time.elapsed == 0
    throttle.throttle(throttle.GENERATE, "gemini-flash-lite-latest")
    assert fake_time.elapsed == pytest.approx(60.0)
