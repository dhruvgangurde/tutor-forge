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
        definition_wrong = "definition_expected" in r and r["definition_kept"] != (r["definition_expected"] == "kept")
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
