"""
tests/test_eval_tutor_pedagogy.py
---------------------------------
The offline tutor-pedagogy eval suite (eval/suites/tutor_pedagogy.py) as a
regression gate. It is offline and takes a few seconds: recorded model outputs,
the mock provider's embeddings, an in-memory Chroma collection and an
in-memory SQLite database. No model, no network.

The suite's own status is FAIL while known gaps remain, on purpose. This test
gates something narrower: the live failures that were fixed stay fixed, no
on-topic message is refused, and every leak, repeat, definition mismatch or
validator disagreement the suite finds is one the dataset already documents as
a known gap. A new one fails here until it is fixed or written down.
"""

import asyncio
import json
import re

import pytest

from eval.harness import DATASETS

_CASES = {"T1", "T2", "T3", "T4", "T5", "T6"}


def _data():
    with open(DATASETS / "tutor_pedagogy.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def result():
    from eval.suites.tutor_pedagogy import run

    return asyncio.run(run())


def _rows(result, record):
    return [r for r in result.rows if r["record"] == record]


# ── The dataset ───────────────────────────────────────────────────────────────


def test_covers_every_finding_as_cases():
    data = _data()
    cases = {p["case"] for p in data["reply_probes"]} | {c["case"] for c in data["conversations"]}
    assert cases >= _CASES
    assert any(i["id"].startswith("G1-") for i in data["generation"]["items"])


@pytest.mark.parametrize("case", ["T1", "T2", "T3", "T4", "T5"])
def test_each_reply_finding_has_its_live_failure_as_a_fixture(case):
    assert any(p["case"] == case and p["source"] == "live" for p in _data()["reply_probes"])


def test_the_live_g1_quiz_is_a_fixture():
    live = [i for i in _data()["generation"]["items"] if i["source"] == "live"]
    assert {i["id"] for i in live} == set(_data()["generation"]["pipeline"]["batch"])


def test_t6_is_a_conversation_with_a_refused_question_then_an_attempt():
    conv = next(c for c in _data()["conversations"] if c["id"] == "C-T6-refusal-cascade")
    assert [t["expect"] for t in conv["turns"]] == ["tutored", "refused", "tutored"]


def test_ids_are_unique_and_keys_resolve():
    data = _data()
    ids = [p["id"] for p in data["reply_probes"]] + [c["id"] for c in data["conversations"]]
    assert len(ids) == len(set(ids))
    for p in data["reply_probes"]:
        assert p["question"] in data["questions"] and p["question"] in data["golden_facts"]
        assert p.get("message") is None or p["message"] in data["students"]
        assert p["mode"] in ("chat", "hint")


def test_golden_fact_patterns_compile_and_match_the_live_leaks():
    from eval.suites.tutor_pedagogy import facts_in

    data = _data()
    for facts in data["golden_facts"].values():
        for fact in facts:
            for pattern in fact["patterns"]:
                re.compile(pattern)
    # Not vacuous: the recorded live T1 and T2 outputs do state a golden fact.
    for probe in data["reply_probes"]:
        if probe["case"] in ("T1", "T2") and probe["source"] == "live":
            assert facts_in(probe["model_output"], data["golden_facts"][probe["question"]]), probe["id"]


def test_fixture_text_is_synthetic_course_text_only():
    fixture = _data()["fixture"]
    assert "synthetic" in fixture["course_name"].lower()
    assert all("@" not in p for p in fixture["passages"])


# ── The run ───────────────────────────────────────────────────────────────────


def test_reports_every_metric_and_says_live_is_not_measured(result):
    for key in (
        "leakage_rate", "on_topic_refusal_rate", "repeated_question_rate",
        "fallback_rate_on_non_leaking", "definition_kept_as_expected",
        "definition_removed_as_expected", "mcq_keys_supported", "live_quiz_kept_mcq_keys_supported",
    ):
        assert key in result.metrics, key
    assert result.metrics["live_gemini"].startswith("not measured")
    assert result.metrics["provider_used"] == "mock (offline)"
    assert "Live Gemini: not measured" in result.headline
    assert result.status in ("pass", "fail")


def test_the_fixed_live_failures_stay_fixed(result):
    assert result.metrics["gated_cases"] >= 3
    assert result.metrics["gated_cases_failed"] == []


def test_no_on_topic_message_is_refused_and_off_topic_is(result):
    assert result.metrics["on_topic_turns"] >= 10
    assert result.metrics["on_topic_refused"] == 0
    assert result.metrics["off_topic_accepted"] == 0
    assert result.metrics["single_reply_probes_refused"] == 0


def test_the_prompt_checks_hold(result):
    assert all(result.metrics["checks"].values()), result.metrics["checks"]


def test_every_failure_found_is_a_documented_known_gap(result):
    undocumented = []
    for r in _rows(result, "reply") + _rows(result, "turn"):
        definition_wrong = r.get("definition_ok") is False
        if (r.get("leak") or r.get("repeat_survived") or definition_wrong) and not r.get("known_gap"):
            undocumented.append(r.get("id") or f"{r['conversation']}#{r['turn']}")
    for r in _rows(result, "question"):
        if not r["agrees"] and not r.get("known_gap"):
            undocumented.append(r["id"])
    assert undocumented == []


def test_known_gaps_that_show_are_in_the_report_notes(result):
    gap_notes = [n for n in result.notes if n.startswith("Known gap: ")]
    leaking = [r for r in _rows(result, "reply") if r["leak"]]
    for r in leaking:
        assert f"Known gap: {r['known_gap']}" in gap_notes


def test_the_live_g1_quiz_keeps_the_fixed_question_and_drops_the_ambiguous_one(result):
    pipeline = result.metrics["live_quiz_pipeline"]
    assert pipeline["status"] != "failed"
    assert (pipeline["requested"], pipeline["kept"], pipeline["regeneration_calls"]) == (3, 2, 2)
    kept = _rows(result, "live_quiz_kept")
    assert all(q["key_supported"] and q["problems"] == [] for q in kept)
    assert all("simplest" not in q["stem"] for q in kept)


def test_the_validator_matches_every_live_g1_label(result):
    live = [r for r in _rows(result, "question") if r["source"] == "live"]
    assert len(live) == 3
    assert all(r["agrees"] for r in live), live


def test_the_run_leaves_the_provider_setting_as_it_was(result):
    from core.config import settings

    assert settings.llm_provider == "mock"  # pinned by tests/conftest.py, restored after the run


def test_registered_in_the_runner():
    from eval.runner import SUITES

    assert SUITES["tutor-pedagogy"] == "eval.suites.tutor_pedagogy"


class _FakeGemini:
    """Stands in for Gemini in the live-mode wiring tests: one safe question for every call."""

    def __init__(self, fail_on: int | None = None):
        self.calls = 0
        self.fail_on = fail_on

    def generate(self, prompt, temperature=0.7, system_instruction=""):
        self.calls += 1
        if self.fail_on and self.calls >= self.fail_on:
            raise RuntimeError("quota exhausted")
        return "What do you think happens next?"


def _live_run(monkeypatch, pro, flash):
    import main
    from core.config import settings
    from eval.suites.tutor_pedagogy import run

    monkeypatch.setattr(settings, "llm_provider", "gemini")  # live runs only on Gemini
    monkeypatch.setattr(main, "_build_llm_clients", lambda: (pro, flash))
    return asyncio.run(run(live=True))


def test_live_mode_wiring_uses_the_model_and_counts_calls(monkeypatch):
    pro, flash = _FakeGemini(), _FakeGemini()
    live = _live_run(monkeypatch, pro, flash)
    assert live.metrics["live_gemini"] == "measured"
    assert live.metrics["live_calls"] == pro.calls + flash.calls > 0
    assert live.metrics["live_stopped_early"] is None
    assert live.metrics["probes_run"] == len(_data()["reply_probes"])
    assert live.metrics["leaks"] == 0  # a safe question leaks nothing
    assert live.headline.startswith("Live:")


def test_live_mode_stops_at_the_first_failure_without_retrying(monkeypatch):
    pro, flash = _FakeGemini(fail_on=3), _FakeGemini()
    live = _live_run(monkeypatch, pro, flash)
    assert pro.calls == 3 and flash.calls == 0  # nothing reaches a model after the failure
    assert live.metrics["live_stopped_early"].startswith("RuntimeError")
    assert live.metrics["probes_run"] < len(_data()["reply_probes"])
    assert "Stopped early" in live.headline


def test_live_mode_refuses_a_provider_other_than_gemini():
    from eval.suites.tutor_pedagogy import run

    assert asyncio.run(run(live=True)).status == "skipped"  # tests pin LLM_PROVIDER=mock


# ── T4: a kept definition must be grounded, whatever its wording ─────────────

_T4_CASE = "T4-definition-not-from-course"
_GROUNDED_PIVOT = "The pivot is the element of the array that the other elements are partitioned around."
_UNGROUNDED_PIVOT = "A pivot is a reference value used to split data into two groups in many algorithms."


def test_the_t4_case_expects_a_kept_definition_to_be_grounded_and_the_stale_gap_is_gone():
    data = _data()
    probe = next(p for p in data["reply_probes"] if p["id"] == _T4_CASE)
    assert probe["definition"]["expected"] == "grounded"
    # The definition rule now removes a definition that is not allowed even when
    # the question names more than the term, so no T4 case documents that gap.
    assert all("known_gap" not in p for p in data["reply_probes"] if p["case"] == "T4")
    assert all("known_gap" not in t for c in data["conversations"] if c["case"] == "T4" for t in c["turns"])


def test_the_groundedness_measure_is_the_guards():
    from eval.suites.tutor_pedagogy import _definition_grounded

    passages = "\n\n".join(_data()["fixture"]["passages"])
    question = _data()["questions"]["PIVOT"]
    assert _definition_grounded(_GROUNDED_PIVOT + " What next?", question, passages)
    assert not _definition_grounded(_UNGROUNDED_PIVOT + " What next?", question, passages)


def test_a_grounded_case_accepts_a_kept_definition_only_when_grounded():
    from eval.suites.tutor_pedagogy import _definition_fields

    passages = "\n\n".join(_data()["fixture"]["passages"])
    question = _data()["questions"]["PIVOT"]
    spec = {"sentence": _UNGROUNDED_PIVOT, "expected": "grounded"}
    follow = " Which element does this lab choose as the pivot?"

    kept_grounded = _definition_fields(_GROUNDED_PIVOT + follow, question, spec, passages, live=True)
    assert kept_grounded["definition_kept"] and kept_grounded["definition_grounded"] and kept_grounded["definition_ok"]

    kept_ungrounded = _definition_fields(_UNGROUNDED_PIVOT + follow, question, spec, passages, live=True)
    assert kept_ungrounded["definition_kept"] and not kept_ungrounded["definition_grounded"]
    assert not kept_ungrounded["definition_ok"]

    removed = _definition_fields(follow.strip(), question, spec, passages, live=True)
    assert not removed["definition_kept"] and removed["definition_ok"]

    # "kept" and "removed" mean what they did.
    assert _definition_fields(_GROUNDED_PIVOT + follow, question, {**spec, "expected": "kept"}, passages, True)["definition_ok"]
    assert not _definition_fields(_GROUNDED_PIVOT + follow, question, {**spec, "expected": "removed"}, passages, True)["definition_ok"]


def test_offline_the_ungrounded_recorded_definition_is_removed_and_the_case_passes(result):
    row = next(r for r in _rows(result, "reply") if r["id"] == _T4_CASE)
    assert row["definition_kept"] is False
    assert row["definition_ok"] is True
    assert _T4_CASE not in result.metrics["definition_mismatches"]
    assert result.metrics["definition_grounded_as_expected"] == 1


class _DefiningGemini(_FakeGemini):
    """Opens the pivot question with a definition; every other call gets the safe question."""

    def __init__(self, sentence: str):
        super().__init__()
        self.sentence = sentence

    def generate(self, prompt, temperature=0.7, system_instruction=""):
        self.calls += 1
        if _data()["questions"]["PIVOT"] in prompt and "THEIR REPLY" not in prompt:
            return self.sentence + " Which element of the range does this lab choose as the pivot?"
        return "What do you think happens next?"


def test_live_a_grounded_definition_of_the_models_own_is_accepted(monkeypatch):
    live = _live_run(monkeypatch, _DefiningGemini(_GROUNDED_PIVOT), _FakeGemini())
    row = next(r for r in _rows(live, "reply") if r["id"] == _T4_CASE)
    assert row["definition_kept"] and row["definition_grounded"] and row["definition_ok"]
    assert _T4_CASE not in live.metrics["definition_mismatches"]


def test_live_an_ungrounded_definition_is_removed_by_the_guard_and_the_case_passes(monkeypatch):
    live = _live_run(monkeypatch, _DefiningGemini(_UNGROUNDED_PIVOT), _FakeGemini())
    row = next(r for r in _rows(live, "reply") if r["id"] == _T4_CASE)
    assert row["definition_kept"] is False  # the guard spares only a grounded opening sentence
    assert row["definition_ok"] is True
