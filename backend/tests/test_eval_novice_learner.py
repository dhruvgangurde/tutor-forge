"""
tests/test_eval_novice_learner.py
---------------------------------
The offline novice-learner eval suite (eval/suites/novice_learner.py) as a
regression gate for Step 1 of the tutor learning-quality work: the beginner
session 5c6672fa replayed with its recorded replies through the real service,
graph, rewrite plumbing and guard. No model, no network.

The Step 2 items (an explanation within N turns, an example when asked) are
reported and checked to be reported, not gated: they are expected to fail
until Explain mode lands.
"""

import asyncio
import json

import pytest

from eval.harness import DATASETS


@pytest.fixture(scope="module")
def result():
    from eval.suites.novice_learner import run

    return asyncio.run(run())


def _data():
    with open(DATASETS / "novice_learner.json", encoding="utf-8") as f:
        return json.load(f)


def _turns(result):
    return [r for r in result.rows if r["record"] == "turn"]


def test_every_step_1_check_holds(result):
    assert all(result.metrics["checks"].values()), result.metrics["checks"]


@pytest.mark.parametrize("check", [
    "no_false_refusals", "off_topic_refused", "context_kept", "no_graded_leak", "no_bare_course_gap_shown",
    "no_glued_course_gap_shown", "no_progress_claim_shown", "full_explanation_gap_keeps_the_level",
    "explanation_again_after_no",
])
def test_each_step_1_check_is_measured(result, check):
    assert result.metrics["checks"][check] is True


def test_the_live_failures_are_fixed(result):
    turns = {(r["conversation"], r["turn"]): r for r in _turns(result)}
    live = "live-5c6672fa"
    assert turns[(live, 15)]["refused"] is False              # "0 1knapsack" judged on the knapsack question
    assert turns[(live, 17)]["refused"] is True               # "what is potential difference?" still refused
    assert "doesn't cover" not in turns[(live, 16)]["response"]
    assert not turns[(live, 11)]["progress_claim_shown"]
    assert turns[(live, 23)]["course_gap_message"] and turns[(live, 23)]["level_after"] == 3
    assert result.metrics["rewrites_used"] >= 2


def test_step_2_items_are_reported_not_gated(result):
    assert set(result.metrics["step2_checks"]) == {"explained_within_n_turns", "example_when_asked"}
    assert result.metrics["explained_within_n_turns"].endswith(" of 3")
    assert result.metrics["example_when_asked"].endswith(" of 2")


def test_offline_says_live_is_not_measured(result):
    assert result.metrics["mode"] == "offline"
    assert result.metrics["live_gemini"].startswith("not measured")
    assert "Live Gemini: not measured" in result.headline


def test_the_dataset_is_synthetic_and_keeps_the_live_turns():
    data = _data()
    text = json.dumps(data)
    assert "@" not in text                                     # no addresses: synthetic course text only
    live = next(c for c in data["conversations"] if c["id"] == "live-5c6672fa")
    said = [t["text"] for t in live["turns"] if t["kind"] == "chat"]
    for line in ("what is dynamic programming?", "solve with an solved example", "0 1knapsack",
                 "what is potential difference?", "teach me how to code in c++ for the matrix multiplication code"):
        assert line in said


class _FakeGemini:
    """Stands in for Gemini Pro and Flash in the live wiring test."""

    def __init__(self, fail_on=None):
        self.calls = 0
        self.fail_on = fail_on

    def generate(self, prompt, temperature=0.7, system_instruction="", **_kwargs):
        self.calls += 1
        if self.fail_on and self.calls >= self.fail_on:
            raise RuntimeError("quota exhausted")
        if "standalone" in system_instruction:
            return json.dumps({"standalone": "", "new_topic": True})
        return "What do you think happens next?"


def _live(monkeypatch, pro, flash):
    import main
    from core.config import settings
    from eval.suites.novice_learner import run

    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(main, "_build_llm_clients", lambda: (pro, flash))
    return asyncio.run(run(live=True))


def test_live_mode_wiring(monkeypatch):
    pro, flash = _FakeGemini(), _FakeGemini()
    live = _live(monkeypatch, pro, flash)
    assert live.metrics["mode"] == "live" and live.metrics["live_gemini"] == "measured"
    assert live.metrics["live_calls"] == pro.calls + flash.calls > 0 and flash.calls > 0
    assert live.metrics["live_stopped_early"] is None


def test_live_mode_stops_at_the_first_failure(monkeypatch):
    pro = _FakeGemini(fail_on=3)
    live = _live(monkeypatch, pro, _FakeGemini())
    assert pro.calls == 3
    assert live.metrics["live_stopped_early"].startswith("RuntimeError")


def test_live_mode_refuses_a_provider_other_than_gemini():
    from eval.suites.novice_learner import run

    assert asyncio.run(run(live=True)).status == "skipped"


def test_registered_in_the_runner():
    from eval.runner import SUITES

    assert SUITES["novice-learner"] == "eval.suites.novice_learner"
