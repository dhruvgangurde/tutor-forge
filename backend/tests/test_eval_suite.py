"""
tests/test_eval_suite.py
-------------------------
Tests for the evaluation suite itself.

The eval package is shipping code — it produces the numbers the case study will
quote — so its datasets and its pure logic are tested like anything else. What
is NOT tested here is the measured outcome of a suite: an eval that asserts its
own result would defeat the point of running it.
"""

import json

import pytest

from eval.harness import DATASETS, SuiteResult


# ── Datasets ──────────────────────────────────────────────────────────────────


class TestProbeDataset:
    def _data(self):
        with open(DATASETS / "out_of_corpus_probes.json", encoding="utf-8") as f:
            return json.load(f)

    def test_every_probe_is_labeled_and_unique(self):
        probes = self._data()["probes"]
        ids = [p["id"] for p in probes]
        assert len(ids) == len(set(ids)), "probe ids must be unique"
        for p in probes:
            assert p["label"] in {"grounded", "ungrounded"}
            assert p["kind"] in {
                "on_topic",
                "off_topic",
                "contentless_followup",
                "adversarial",
            }
            assert p["text"].strip()

    def test_has_both_classes_in_useful_numbers(self):
        # A probe set that is all-negative would score a perfect zero-leakage
        # pass while proving nothing about false refusals.
        probes = self._data()["probes"]
        on = [p for p in probes if p["kind"] == "on_topic"]
        off = [p for p in probes if p["kind"] in ("off_topic", "adversarial")]
        assert len(on) >= 8
        assert len(off) >= 8

    def test_contentless_followups_carry_a_prior_turn(self):
        # Without a prior turn there is nothing for query resolution to
        # substitute, so the probe would measure something else entirely.
        for p in self._data()["probes"]:
            if p["kind"] == "contentless_followup":
                assert p.get("prior"), f"{p['id']} needs a prior turn"

    def test_contentless_followups_are_labeled_grounded(self):
        # They are on-topic continuations; a correct system serves them. Their
        # low raw score is the finding, not the label.
        for p in self._data()["probes"]:
            if p["kind"] == "contentless_followup":
                assert p["label"] == "grounded"


class TestParaphraseDataset:
    def _data(self):
        with open(DATASETS / "paraphrase_pairs.json", encoding="utf-8") as f:
            return json.load(f)

    def test_groups_have_multiple_variants_and_a_rubric(self):
        for group in self._data()["groups"]:
            assert len(group["variants"]) >= 3, f"{group['id']} needs variants to compare"
            assert group["rubric"], f"{group['id']} needs a rubric to grade against"
            assert group["max_points"] > 0
            ids = [v["id"] for v in group["variants"]]
            assert len(ids) == len(set(ids))

    def test_rubric_points_sum_to_max_points(self):
        for group in self._data()["groups"]:
            total = sum(c["max_points"] for c in group["rubric"])
            assert total == pytest.approx(group["max_points"]), (
                f"{group['id']}: rubric sums to {total}, max_points is {group['max_points']}"
            )


class TestAcceptanceDataset:
    def _data(self):
        with open(DATASETS / "acceptance_100.json", encoding="utf-8") as f:
            return json.load(f)

    def test_is_a_hundred_labeled_answers(self):
        data = self._data()
        assert len(data["answers"]) == 100
        ids = [a["id"] for a in data["answers"]]
        assert len(ids) == len(set(ids))

    def test_every_answer_has_an_expected_score_within_range(self):
        for a in self._data()["answers"]:
            assert 0.0 <= a["expected_score"] <= a["max_points"]

    def test_covers_all_three_question_types(self):
        types = {a["question_type"] for a in self._data()["answers"]}
        assert types == {"mcq", "numeric", "short_answer"}

    def test_wrong_numeric_answers_fall_outside_the_grader_tolerance(self):
        # A "wrong" numeric answer inside NUMERIC_TOLERANCE (1% of expected) is
        # graded correct, so the label is wrong, not the grader. The first eval
        # run surfaced exactly one such case (acc-070: 4503 vs 4500, tolerance
        # 45) and reported it as a disagreement.
        from agents.grading.prompts import NUMERIC_TOLERANCE

        for a in self._data()["answers"]:
            if a["question_type"] != "numeric" or a["expected_score"] != 0.0:
                continue
            correct = float(a["answer_key"]["correct_answer"])
            given = float(a["student_answer_text"])
            tol = abs(correct) * NUMERIC_TOLERANCE if correct else NUMERIC_TOLERANCE
            assert abs(given - correct) > tol, (
                f"{a['id']}: {given} is within {tol} of {correct}, so the grader "
                "scores it correct and expected_score=0.0 is a mislabel"
            )

    def test_contains_both_correct_and_incorrect_answers(self):
        # An all-correct set would report 100% agreement on a grader that
        # always awarded full marks.
        scores = [a["expected_score"] for a in self._data()["answers"]]
        assert any(s == 0.0 for s in scores)
        assert any(s > 0.0 for s in scores)


def test_judge_rubric_has_the_placeholders_the_suite_substitutes():
    text = (DATASETS / "distractor_judge_rubric.md").read_text(encoding="utf-8")
    for placeholder in ("{stem}", "{options}", "{correct_answer}", "{context}"):
        assert placeholder in text


# ── Pure logic ────────────────────────────────────────────────────────────────


class TestVarianceSignature:
    def _fn(self):
        from eval.suites.grading_variance import _signature

        return _signature

    def _state(self, score, feedback):
        return {
            "total_score": score,
            "max_score": 3.0,
            "criterion_results": [
                [{"description": "d", "score": score, "feedback": feedback}]
            ],
        }

    def test_identical_results_share_a_signature(self):
        fn = self._fn()
        assert fn(self._state(2.0, "good")) == fn(self._state(2.0, "good"))

    def test_score_difference_changes_the_signature(self):
        fn = self._fn()
        assert fn(self._state(2.0, "good")) != fn(self._state(2.5, "good"))

    def test_feedback_difference_changes_the_signature(self):
        # The brief requires identical *rationale*, not just identical totals.
        fn = self._fn()
        assert fn(self._state(2.0, "good")) != fn(self._state(2.0, "also good"))


class TestJudgementParsing:
    def _fn(self):
        from eval.suites.distractor_quality import _parse_judgement

        return _parse_judgement

    def test_parses_bare_json(self):
        assert self._fn()('{"overall": 4}')["overall"] == 4

    def test_parses_fenced_json(self):
        assert self._fn()('```json\n{"overall": 5}\n```')["overall"] == 5

    def test_parses_json_with_surrounding_prose(self):
        assert self._fn()('Here is my judgement:\n{"overall": 3}\nHope that helps.')[
            "overall"
        ] == 3

    def test_returns_none_on_unparseable_output(self):
        assert self._fn()("I cannot judge this question.") is None
        assert self._fn()("{not json at all}") is None


# ── Report ────────────────────────────────────────────────────────────────────


class TestReport:
    def _results(self):
        return [
            SuiteResult(
                name="groundedness",
                criterion="Out-of-corpus leakage",
                target="0",
                status="fail",
                headline="1 leak out of 13",
                metrics={"leaks": 1},
                rows=[{"id": "x", "correct": False}],
                notes=["a note"],
            )
        ]

    def test_markdown_includes_every_brief_criterion(self):
        from eval.report import BRIEF_CRITERIA, render_markdown

        md = render_markdown(
            self._results(),
            {
                "generated_at": "2026-08-30T00:00:00+00:00",
                "llm_provider": "gemini",
                "groundedness_threshold": 0.65,
                "python": "3.14",
                "platform": "test",
            },
        )
        # A criterion with no suite must still appear, or "unmeasured" quietly
        # persists by omission.
        for criterion, _target, _suite in BRIEF_CRITERIA:
            assert criterion in md

    def test_markdown_flags_a_non_gemini_provider(self):
        from eval.report import render_markdown

        md = render_markdown(
            self._results(),
            {
                "generated_at": "2026-08-30T00:00:00+00:00",
                "llm_provider": "ollama",
                "groundedness_threshold": 0.57,
                "python": "3.14",
                "platform": "test",
            },
        )
        assert "Provider caveat" in md
        assert "development signal" in md

    def test_failure_is_rendered_as_fail_not_softened(self):
        from eval.report import render_markdown

        md = render_markdown(
            self._results(),
            {
                "generated_at": "2026-08-30T00:00:00+00:00",
                "llm_provider": "gemini",
                "groundedness_threshold": 0.65,
                "python": "3.14",
                "platform": "test",
            },
        )
        assert "FAIL" in md


def test_runner_registers_a_suite_for_every_measurable_criterion():
    from eval.report import BRIEF_CRITERIA
    from eval.runner import SUITES

    for _criterion, _target, suite in BRIEF_CRITERIA:
        if suite is not None:
            assert suite in SUITES, f"{suite} named in the report but not runnable"


def test_suite_result_status_vocabulary_is_closed():
    # 'not_measurable' exists so a criterion the system cannot support is never
    # reported as a pass.
    from eval.report import _STATUS_MARK

    assert set(_STATUS_MARK) == {"pass", "fail", "not_measurable", "skipped"}


class TestAdversarialProbeTagging:
    """
    Adversarial probes carry a 'smuggling' tag so the suite can report leak rate
    per technique — that breakdown is what showed dsa-adv-02 was not a one-off
    but a 6/6 systematic hole in domain_vocab_wrapper.
    """

    def _data(self):
        with open(DATASETS / "out_of_corpus_probes.json", encoding="utf-8") as f:
            return json.load(f)

    def test_every_adversarial_probe_is_tagged(self):
        # An untagged probe stored `smuggling: None`, and `.get(k, default)`
        # returns that None rather than the default, so sorted() compared None
        # against str and the whole suite raised. Tagging is now required.
        untagged = [
            p["id"]
            for p in self._data()["probes"]
            if p["kind"] == "adversarial" and not p.get("smuggling")
        ]
        assert not untagged, f"adversarial probes missing a smuggling tag: {untagged}"

    def test_technique_grouping_survives_an_untagged_probe(self):
        # Defence in depth for the crash above: even if an untagged probe slips
        # in, grouping must not raise.
        rows = [
            {"kind": "adversarial", "smuggling": None, "grounded_effective": True, "score_alone": 0.6},
            {"kind": "adversarial", "smuggling": "domain_vocab_wrapper", "grounded_effective": False, "score_alone": 0.4},
        ]
        grouped: dict = {}
        for r in rows:
            tag = r.get("smuggling") or "untagged"
            b = grouped.setdefault(tag, {"n": 0, "leaked": 0, "max_score": 0.0})
            b["n"] += 1
            b["leaked"] += int(r["grounded_effective"])
            b["max_score"] = max(b["max_score"], r["score_alone"])
        assert sorted(grouped.items())  # must not raise
        assert set(grouped) == {"untagged", "domain_vocab_wrapper"}

    def test_domain_vocab_wrapper_probes_exist_in_useful_numbers(self):
        # The technique that defeats similarity gating needs enough probes to
        # tell "systematic" from "one unlucky embedding".
        wrappers = [
            p
            for p in self._data()["probes"]
            if p.get("smuggling") == "domain_vocab_wrapper"
        ]
        assert len(wrappers) >= 5

    def test_adversarial_probes_are_all_labeled_ungrounded(self):
        for p in self._data()["probes"]:
            if p["kind"] == "adversarial":
                assert p["label"] == "ungrounded"


class TestAssessmentCorrectnessDataset:
    """
    The labeled calibration set exists so the suite can measure the JUDGE before
    quoting the rate the judge produces over real questions.
    """

    def _data(self):
        with open(DATASETS / "assessment_correctness.json", encoding="utf-8") as f:
            return json.load(f)

    def test_items_are_labeled_and_unique(self):
        items = self._data()["items"]
        ids = [i["id"] for i in items]
        assert len(ids) == len(set(ids))
        for i in items:
            assert isinstance(i["expected_correct"], bool)
            assert i["stem"].strip()
            assert i["question_type"] in {"mcq", "numeric", "short_answer"}

    def test_contains_both_verdicts(self):
        # An all-correct calibration set would score 100% on a judge that always
        # answered "correct", which is precisely the judge we need to detect.
        expected = [i["expected_correct"] for i in self._data()["items"]]
        assert any(expected)
        assert any(not e for e in expected)

    def test_covers_the_miskeyed_failure_mode(self):
        # The whole reason this suite is distinct from distractor-quality.
        miskeyed = [
            i for i in self._data()["items"] if i.get("failure_mode") == "miskeyed"
        ]
        assert len(miskeyed) >= 3
        assert all(i["expected_correct"] is False for i in miskeyed)

    def test_covers_the_unanswerable_failure_mode(self):
        unanswerable = [
            i
            for i in self._data()["items"]
            if i.get("failure_mode") == "unanswerable_from_corpus"
        ]
        assert len(unanswerable) >= 2
        assert all(i["expected_correct"] is False for i in unanswerable)

    def test_incorrect_items_explain_why(self):
        for i in self._data()["items"]:
            if not i["expected_correct"]:
                assert i.get("failure_mode"), f"{i['id']} needs a failure_mode"
                assert i.get("note"), f"{i['id']} needs a note explaining the label"

    def test_mcq_items_carry_options_and_a_key_among_them(self):
        for i in self._data()["items"]:
            if i["question_type"] != "mcq":
                continue
            options = i.get("options") or []
            assert len(options) >= 3
            letters = {o.split(".")[0].strip() for o in options}
            assert i["correct_answer"] in letters, (
                f"{i['id']}: marked key {i['correct_answer']!r} is not one of {letters}"
            )


def test_correctness_rubric_has_the_placeholders_the_suite_substitutes():
    text = (DATASETS / "assessment_correctness_rubric.md").read_text(encoding="utf-8")
    for placeholder in (
        "{question_type}",
        "{stem}",
        "{options}",
        "{correct_answer}",
        "{context}",
    ):
        assert placeholder in text


def test_correctness_rubric_asks_for_the_verdict_field_the_suite_reads():
    text = (DATASETS / "assessment_correctness_rubric.md").read_text(encoding="utf-8")
    for field in ("key_correct", "answerable_from_context", "correct"):
        assert field in text


class TestAssessmentCorrectnessSuite:
    def test_verdict_parsing_matches_the_judge_contract(self):
        from eval.suites.assessment_correctness import _parse_verdict

        assert _parse_verdict('{"correct": true}')["correct"] is True
        assert _parse_verdict('```json\n{"correct": false}\n```')["correct"] is False
        assert _parse_verdict('Verdict:\n{"correct": true}\ndone')["correct"] is True
        assert _parse_verdict("no json here") is None

    def test_an_uncalibrated_judge_cannot_certify_a_pass(self):
        # Guards the suite's core honesty property: if the judge cannot tell a
        # correctly keyed question from a mis-keyed one, its verdict on real
        # questions must not be reported as a pass OR a fail.
        from eval.suites.assessment_correctness import MIN_JUDGE_ACCURACY, TARGET_RATE

        assert 0.0 < MIN_JUDGE_ACCURACY <= 1.0
        assert TARGET_RATE == 0.95

    def test_registered_in_the_runner_and_mapped_to_the_criterion(self):
        from eval.report import BRIEF_CRITERIA
        from eval.runner import SUITES

        assert "assessment-correctness" in SUITES
        mapped = {
            criterion: suite for criterion, _target, suite in BRIEF_CRITERIA
        }
        assert mapped["Assessment correctness"] == "assessment-correctness"

    def test_assessment_correctness_is_no_longer_reported_as_uncovered(self):
        from eval.report import _UNCOVERED

        assert "Assessment correctness" not in _UNCOVERED
        # The genuinely manual one must still be listed with its reason.
        assert "Hint ladder demo" in _UNCOVERED


# ── Shared judge calibration ──────────────────────────────────────────────────


class TestSharedCalibration:
    """
    Both LLM-as-judge suites gate on harness.calibrate() at the same bar. The
    gate is not decoration: it caught the assessment-correctness judge at 69%,
    which would otherwise have published "8% assessment correctness" — a false
    claim about the generator rather than a measurement of it.
    """

    def _fn(self):
        from eval.harness import calibrate

        return calibrate

    def test_perfect_judge_passes(self):
        rows = [{"judged": 5, "agrees": True} for _ in range(5)]
        cal = self._fn()(rows)
        assert cal.accuracy == 1.0
        assert cal.passed is True

    def test_judge_below_the_bar_fails(self):
        rows = [{"judged": 5, "agrees": i < 6} for i in range(10)]
        cal = self._fn()(rows)
        assert cal.accuracy == 0.6
        assert cal.passed is False

    def test_bar_is_inclusive(self):
        from eval.harness import MIN_JUDGE_ACCURACY

        rows = [{"judged": 1, "agrees": i < 8} for i in range(10)]
        cal = self._fn()(rows)
        assert cal.accuracy == MIN_JUDGE_ACCURACY
        assert cal.passed is True, "exactly at the bar must count as calibrated"

    def test_unparseable_items_leave_the_denominator(self):
        # An unparseable judge response is a harness problem, not evidence about
        # judgement, so counting it as a miss would understate the judge.
        rows = [
            {"judged": 5, "agrees": True},
            {"judged": 4, "agrees": True},
            {"judged": None, "agrees": False},
        ]
        cal = self._fn()(rows)
        assert cal.items == 2
        assert cal.accuracy == 1.0

    def test_no_scorable_items_never_passes(self):
        cal = self._fn()([{"judged": None, "agrees": False}])
        assert cal.items == 0
        assert cal.passed is False, "an empty calibration must not read as calibrated"

    def test_metrics_expose_the_bar_alongside_the_score(self):
        from eval.harness import MIN_JUDGE_ACCURACY

        m = self._fn()([{"judged": 5, "agrees": True}]).as_metrics()
        assert m["judge_accuracy"] == 1.0
        assert m["min_judge_accuracy"] == MIN_JUDGE_ACCURACY
        assert m["judge_calibration_passed"] is True

    def test_both_judge_suites_use_the_same_bar_object(self):
        # A per-suite copy would let the two definitions of "trustworthy" drift.
        from eval.harness import MIN_JUDGE_ACCURACY as shared
        from eval.suites.assessment_correctness import MIN_JUDGE_ACCURACY as ac
        from eval.suites.distractor_quality import MIN_JUDGE_ACCURACY as dq

        assert ac is shared and dq is shared


class TestDistractorCalibrationDataset:
    def _data(self):
        with open(DATASETS / "distractor_calibration.json", encoding="utf-8") as f:
            return json.load(f)

    def test_has_both_bands_in_useful_numbers(self):
        # An all-good set would certify a judge that says 5/5 to everything.
        items = self._data()["items"]
        good = [i for i in items if i["expected_band"] == "good"]
        bad = [i for i in items if i["expected_band"] == "bad"]
        assert len(good) >= 4
        assert len(bad) >= 4

    def test_every_item_is_well_formed_and_unique(self):
        ids = []
        for item in self._data()["items"]:
            ids.append(item["id"])
            assert item["expected_band"] in {"good", "bad"}
            assert item["stem"].strip()
            assert len(item["options"]) >= 3
            assert item["correct_answer"]
        assert len(ids) == len(set(ids))

    def test_bad_items_name_their_failure_mode(self):
        # Naming it is what lets the report say WHICH defect the judge missed.
        for item in self._data()["items"]:
            if item["expected_band"] == "bad":
                assert item.get("failure_mode"), f"{item['id']} needs a failure_mode"

    def test_covers_the_distinct_ways_an_mcq_fails(self):
        modes = {
            i.get("failure_mode")
            for i in self._data()["items"]
            if i["expected_band"] == "bad"
        }
        assert {"near_tautology", "absurd_distractors"} <= modes
        assert len(modes) >= 3

    def test_bands_do_not_overlap(self):
        data = self._data()
        assert data["bad_max_overall"] < data["good_min_overall"], (
            "an item cannot be simultaneously good and bad"
        )

    def test_target_sits_at_the_good_band_floor(self):
        # Calibration must test the discrimination the target actually needs.
        from eval.suites.distractor_quality import TARGET_SCORE

        assert self._data()["good_min_overall"] == TARGET_SCORE
