"""
tests/test_gemini_quota.py
--------------------------
Gemini free-tier embedding quota, replayed offline.

The fake endpoint below enforces what a real upload showed: at most 100 texts
per minute per model, every text in a batch counts as one request, and a
rejected request still counts. A fake clock stands in for real time in both the
retry sleeps and the client-side pacing. No network, no real SDK call.
"""

from itertools import pairwise

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


# ── 2. Reading the retry hint; never retrying inside the same window ──────────


def _rate_limited(text_hint=None, detail_seconds=None, empty_detail=False):
    from google.api_core import exceptions as gexc
    from google.protobuf import duration_pb2
    from google.rpc import error_details_pb2

    details = []
    if detail_seconds is not None:
        details.append(error_details_pb2.RetryInfo(retry_delay=duration_pb2.Duration(seconds=detail_seconds)))
    elif empty_detail:
        details.append(error_details_pb2.RetryInfo())  # "retry_delay { }"
    message = "You exceeded your current quota."
    if text_hint:
        message += f"\nPlease retry in {text_hint}."
    return gexc.ResourceExhausted(message, details=details)


@pytest.mark.parametrize(
    "exc_kwargs, expected",
    [
        ({"text_hint": "396.356914ms", "empty_detail": True}, 0.396356914),
        ({"text_hint": "18.67s", "detail_seconds": 18}, 18.0),     # structured detail first
        ({"text_hint": "59.09s"}, 59.09),
        ({"empty_detail": True}, None),                            # empty means unknown
        ({"detail_seconds": 0}, None),                             # zero means unknown
        ({}, None),
    ],
)
def test_retry_hint_parsing(exc_kwargs, expected):
    from core.gemini_calls import retry_delay_seconds

    delay = retry_delay_seconds(_rate_limited(**exc_kwargs))
    if expected is None:
        assert delay is None
    else:
        assert delay == pytest.approx(expected)


def test_a_429_with_no_usable_hint_waits_a_full_window_not_a_backoff(defaults, fake_time, monkeypatch):
    import google.generativeai as genai

    calls: list[float] = []

    def flaky(**kwargs):
        calls.append(fake_time.now)
        if len(calls) == 1:
            raise _rate_limited(empty_detail=True)
        return {"embedding": [[1.0, 0.0]]}

    monkeypatch.setattr(genai, "embed_content", flaky)
    defaults.gemini_embed_rpm = 0  # pacing off: the limiter cannot help here
    _client().embed_query("q")
    assert calls[1] - calls[0] >= 60.0


class _ScriptedAPI:
    """First request succeeds, then the scripted errors, then success; records times."""

    def __init__(self, fake_time, script):
        self.time = fake_time
        self.script = list(script)
        self.calls: list[tuple[float, int]] = []

    def embed_content(self, **kwargs):
        content = kwargs["content"]
        n = len(content) if isinstance(content, list) else 1
        self.calls.append((self.time.now, n))
        if len(self.calls) > 1 and self.script:
            raise self.script.pop(0)
        vectors = [[1.0, 0.0] for _ in range(n)]
        return {"embedding": vectors if isinstance(content, list) else vectors[0]}


def _log_replay():
    """The three 429s from the failed upload, in order."""
    return [
        _rate_limited(text_hint="18.67s", detail_seconds=18),
        _rate_limited(text_hint="396.356914ms", empty_detail=True),
        _rate_limited(text_hint="59.09s", detail_seconds=59),
    ]


def test_logged_429s_replayed_never_retry_inside_the_same_window(defaults, fake_time, monkeypatch):
    import google.generativeai as genai

    api = _ScriptedAPI(fake_time, _log_replay())
    monkeypatch.setattr(genai, "embed_content", api.embed_content)
    defaults.gemini_max_wait_seconds = 300.0

    vectors = _client().embed_documents([f"chunk {i}" for i in range(152)])

    assert len(vectors) == 152
    times = [t for t, _ in api.calls]
    assert [n for _, n in api.calls] == [90, 62, 62, 62, 62]
    # Each retry of the second batch starts at least a window after the attempt before it.
    for earlier, later in pairwise(times[1:]):
        assert later - earlier >= 60.0
