"""
tests/test_eval_grading_checks.py
---------------------------------
The offline grading-checks eval suite (eval/suites/grading_checks.py) as a
regression gate: same score twice, blank answers at 0 with no model call, the
rubric unchanged and every score within its maximum, the answer isolated in the
prompt. No model, no network. The --live path is exercised with a fake provider
so its wiring is tested; a real live run is never part of pytest.
"""

import asyncio
import json

import pytest

from eval.harness import DATASETS


@pytest.fixture(scope="module")
def result():
    from eval.suites.grading_checks import run

    return asyncio.run(run())


def _rows(result, record):
    return [r for r in result.rows if r["record"] == record]


def test_every_code_check_holds_offline(result):
    assert result.status == "pass"
    assert all(result.metrics["checks"].values()), result.metrics["checks"]


def test_same_submission_twice_gives_the_same_score_and_prompt(result):
    for r in _rows(result, "short_answer"):
        assert r["same_twice"] and r["same_prompt_twice"], r["id"]
    for r in _rows(result, "closed"):
        assert r["same_twice"], r["id"]


def test_blank_answers_score_zero_without_a_model_call(result):
    blanks = [r for r in _rows(result, "short_answer") if "blank_scored_zero_without_model" in r]
    assert {r["id"] for r in blanks} == {"blank", "whitespace"}
    assert all(r["score"] == 0.0 and r["model_calls"] == 0 for r in blanks)
    closed = {r["id"]: r["score"] for r in _rows(result, "closed")}
    assert closed["mcq-blank"] == closed["numeric-blank"] == 0.0


def test_injected_answers_cannot_change_the_rubric_or_pass_its_maximum(result):
    injected = [r for r in _rows(result, "short_answer") if r["obedient_model"]]
    assert {r["id"] for r in injected} == {"partial-injection", "gibberish-injection"}
    for r in injected:
        assert r["rubric_unchanged"] and r["bounded"] and r["isolated"], r["id"]
        assert r["score"] == r["max"] == 4.0  # an obedient model reaches the cap, no further
    closed = {r["id"]: r["score"] for r in _rows(result, "closed")}
    assert closed["mcq-injection"] == closed["numeric-gibberish"] == 0.0


def test_partial_credit_is_reported(result):
    scores = {r["id"]: r["score"] for r in _rows(result, "short_answer")}
    assert scores["partial"] == 2.0 and scores["fractional"] == 2.0 and scores["full"] == 4.0
    assert any(n.startswith("Partial credit:") for n in result.notes)


def test_live_behaviour_is_reported_as_not_measured(result):
    assert result.metrics["mode"] == "offline"
    assert result.metrics["live_model"].startswith("not measured")
    assert result.metrics["score_ordering"].startswith("not measured")


def test_dataset_is_synthetic_and_ids_are_unique():
    with open(DATASETS / "grading_checks.json", encoding="utf-8") as f:
        data = json.load(f)
    ids = [a["id"] for a in data["answers"]] + [a["id"] for a in data["mcq"]["answers"]] + [
        a["id"] for a in data["numeric"]["answers"]
    ]
    assert len(ids) == len(set(ids))
    assert "@" not in json.dumps(data)  # no email addresses: synthetic text only


class _FakeLiveModel:
    """Stands in for the configured provider: scores by answer length, ignores instructions."""

    def generate_deterministic(self, prompt: str) -> str:
        body = prompt.split("<<UNTRUSTED STUDENT RESPONSE>>")[1].split("<<END UNTRUSTED STUDENT RESPONSE>>")[0]
        rubric = json.loads(prompt.split("## Rubric Criteria\n")[1].split("\n\nEach criterion")[0])
        good = "low - 1" in body
        scores = [
            {"criterion_id": c["criterion_id"], "description": c["description"],
             "score": c["max_points"] if good and i == 0 else 0.0, "max_points": c["max_points"],
             "feedback": "x", "citations": []}
            for i, c in enumerate(rubric)
        ]
        return json.dumps({"criterion_scores": scores, "overall_feedback": "x"})


def test_live_mode_wiring_checks_score_ordering(monkeypatch):
    import main
    from eval.suites.grading_checks import run

    monkeypatch.setattr(main, "_build_llm_clients", lambda: (_FakeLiveModel(), None))
    live = asyncio.run(run(live=True))
    assert live.metrics["mode"] == "live"
    assert isinstance(live.metrics["score_ordering"], dict)
    assert all(live.metrics["score_ordering"].values()), live.metrics["score_ordering"]
    assert live.status == "pass"


def test_registered_in_the_runner():
    from eval.runner import SUITES

    assert SUITES["grading-checks"] == "eval.suites.grading_checks"
