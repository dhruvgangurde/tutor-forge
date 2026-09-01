"""
eval/suites/distractor_quality.py
----------------------------------
AC: LLM-as-Judge score >= 4/5 (docs/PROJECT-BRIEF.md §5.4, §6).

Judges the MCQs a real assessment produced, against the rubric in
datasets/distractor_judge_rubric.md. The judge is the project's own configured
LLM — the same provider the app runs on — so the number reflects what a
reviewer would see, not a stronger model grading a weaker one's homework.

Two phases, and the order matters
---------------------------------
  1. CALIBRATION over datasets/distractor_calibration.json, whose MCQs carry a
     known quality band — five well-formed, five with a specific defect
     (near-tautology, absurd distractors, duplicate options, multiple defensible
     answers). This measures the JUDGE.
  2. CORPUS mean over the real generated MCQs in the database. This is the
     number the criterion is about.

Phase 1 gates the credibility of phase 2, using the same harness.calibrate()
and the same harness.MIN_JUDGE_ACCURACY bar as the assessment-correctness
suite — one mechanism, one threshold, so the two cannot drift apart.

Why this suite needed the gate too
----------------------------------
The same 7B judge failed calibration at 69% on assessment-correctness: it
flagged every deliberately mis-keyed question but also rejected 4 of 5
known-correct ones, contradicting its own reasoning mid-sentence. That says
nothing directly about its skill at *this* task — the two rubrics ask different
questions — but it does mean an uncalibrated mean from it is an unvalidated
number, not a confirmed one. So the same evidence is now required here.

What the calibration asks
-------------------------
Not "does the judge reproduce my exact 1-5 score" — that would be scoring the
judge's calibration of a subjective scale. It asks the weaker, decision-relevant
question: can the judge put a good question at or above the target bar and a
defective one below it? That is precisely the discrimination the >= 4/5 target
depends on, and nothing more.
"""

from __future__ import annotations

import json
import re

from eval.harness import (
    DATASETS,
    MIN_JUDGE_ACCURACY,
    SuiteResult,
    build_services,
    calibrate,
    courses_by_name,
    load_dataset,
    retrieve_evidence,
)

TARGET_SCORE = 4.0
MAX_QUESTIONS = 8


def _rubric_prompt(stem: str, options: list[str], correct: str, context: str) -> str:
    template = (DATASETS / "distractor_judge_rubric.md").read_text(encoding="utf-8")
    return (
        template.replace("{stem}", stem)
        .replace("{options}", "\n".join(options))
        .replace("{correct_answer}", correct)
        .replace("{context}", context[:2000] or "(no context recorded)")
    )


def _parse_judgement(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _judge(gemini_pro, stem, options, correct, context) -> tuple[dict | None, str]:
    """Run one judgement. Returns (parsed, error_reason)."""
    try:
        raw = gemini_pro.generate_deterministic(
            _rubric_prompt(stem, options, correct, context)
        )
    except Exception as exc:  # noqa: BLE001 - one bad call must not sink the suite
        return None, str(exc)[:200]
    parsed = _parse_judgement(raw)
    if parsed is None:
        return None, f"unparseable judge output: {raw[:160]!r}"
    return parsed, ""


async def run(limit: int = MAX_QUESTIONS) -> SuiteResult:
    from sqlalchemy import select

    from core.database import AsyncSessionFactory
    from db.models import Assessment, Question

    retrieval, gemini_pro, _flash = build_services()
    courses = await courses_by_name()

    # ── Phase 1: calibration ──────────────────────────────────────────────────
    cal_data = load_dataset("distractor_calibration.json")
    good_min = float(cal_data.get("good_min_overall", 4))
    bad_max = float(cal_data.get("bad_max_overall", 3))
    cal_course = courses.get(cal_data.get("corpus", ""))

    calibration_rows: list[dict] = []
    for item in cal_data["items"]:
        # Real retrieved context, not the stem. The rubric scores a `grounded`
        # dimension, and handing the question its own stem as "context" makes
        # that dimension vacuously 5/5 — which is exactly what the earlier
        # uncalibrated runs did.
        context = ""
        if cal_course is not None:
            chunks = retrieve_evidence(item["stem"], cal_course, retrieval)
            context = "\n\n".join(c["text"] for c in chunks)

        judged, error = _judge(
            gemini_pro, item["stem"], item["options"], item["correct_answer"], context
        )
        overall = None if judged is None else float(judged.get("overall", 0))
        if overall is None:
            agrees = False
        elif item["expected_band"] == "good":
            agrees = overall >= good_min
        else:
            agrees = overall <= bad_max

        calibration_rows.append(
            {
                "phase": "calibration",
                "id": item["id"],
                "expected_band": item["expected_band"],
                "failure_mode": item.get("failure_mode"),
                "judged": overall,
                "agrees": agrees,
                "error": error or None,
                "evidence_chars": len(context),
            }
        )

    cal = calibrate(calibration_rows)

    # ── Phase 2: the real corpus ──────────────────────────────────────────────
    async with AsyncSessionFactory() as db:
        rows_db = (
            await db.execute(
                select(Question, Assessment.title, Assessment.course_id)
                .join(Assessment, Question.assessment_id == Assessment.id)
                .where(Question.question_type == "mcq")
                .order_by(Question.order_index)
                .limit(limit)
            )
        ).all()

    if not rows_db:
        return SuiteResult(
            name="distractor-quality",
            criterion="LLM-as-Judge score",
            target=f">= {TARGET_SCORE}/5",
            status="skipped",
            headline="No MCQ questions exist in the database to judge.",
            metrics=cal.as_metrics(),
            rows=calibration_rows,
            notes=["Generate and publish an assessment, then re-run this suite."],
        )

    corpus_rows: list[dict] = []
    overalls: list[float] = []
    unparsed = 0

    for question, title, course_id in rows_db:
        try:
            options = json.loads(question.options) if question.options else []
        except json.JSONDecodeError:
            options = []
        try:
            key = json.loads(question.answer_key) if question.answer_key else {}
        except json.JSONDecodeError:
            key = {}
        correct = str(key.get("correct_answer", ""))

        chunks = retrieve_evidence(question.stem, course_id, retrieval)
        context = "\n\n".join(c["text"] for c in chunks)

        judged, error = _judge(gemini_pro, question.stem, options, correct, context)
        if judged is None:
            unparsed += 1
            corpus_rows.append(
                {
                    "phase": "corpus",
                    "assessment": title,
                    "stem": question.stem[:80],
                    "error": error,
                }
            )
            continue

        overall = float(judged.get("overall", 0))
        overalls.append(overall)
        corpus_rows.append(
            {
                "phase": "corpus",
                "assessment": title,
                "stem": question.stem[:80],
                "plausibility": judged.get("plausibility"),
                "distinctness": judged.get("distinctness"),
                "single_answer": judged.get("single_answer"),
                "grounded": judged.get("grounded"),
                "overall": overall,
                "evidence_chunks": len(chunks),
                "notes": str(judged.get("notes", ""))[:160],
            }
        )

    mean = sum(overalls) / len(overalls) if overalls else 0.0

    # ── Verdict ───────────────────────────────────────────────────────────────
    good_scores = [
        r["judged"] for r in calibration_rows
        if r["expected_band"] == "good" and r["judged"] is not None
    ]
    bad_scores = [
        r["judged"] for r in calibration_rows
        if r["expected_band"] == "bad" and r["judged"] is not None
    ]
    separation = (
        (sum(good_scores) / len(good_scores)) - (sum(bad_scores) / len(bad_scores))
        if good_scores and bad_scores
        else None
    )

    notes = [
        f"Judge calibration on {cal.items} labeled MCQ(s): {cal.correct} correct "
        f"({cal.accuracy:.0%}); bar is {MIN_JUDGE_ACCURACY:.0%}. Same "
        "harness.calibrate() and same bar as the assessment-correctness suite.",
        "Calibration asks only whether the judge can place a good question at or "
        f"above {good_min:.0f}/5 and a defective one at or below {bad_max:.0f}/5 — "
        "the exact discrimination the >= 4/5 target depends on.",
        "Both phases judge against REAL retrieved course context. Earlier runs "
        "passed the question's own stem as its 'context', which made the "
        "rubric's `grounded` dimension vacuously 5/5 and inflated the mean.",
    ]
    if separation is not None:
        notes.append(
            f"Mean judged score: good items {sum(good_scores) / len(good_scores):.2f}, "
            f"defective items {sum(bad_scores) / len(bad_scores):.2f} "
            f"(separation {separation:+.2f})."
        )
    missed = [r for r in calibration_rows if not r["agrees"]]
    if missed:
        notes.append(
            "Calibration misses: "
            + ", ".join(
                f"{r['id']}(expected {r['expected_band']}, judged {r['judged']})"
                for r in missed
            )
        )

    if not overalls:
        status = "skipped"
        headline = f"Judged 0 of {len(rows_db)} questions — no parseable judgements."
    elif not cal.passed:
        status = "not_measurable"
        headline = (
            f"Judge calibration {cal.accuracy:.0%} is below the "
            f"{MIN_JUDGE_ACCURACY:.0%} bar, so the {mean:.2f}/5 corpus mean is not "
            "a trustworthy measurement."
        )
        notes.append(
            "NOT MEASURABLE, not a failure of the generator. The corpus mean is "
            "retained above for inspection but must not be quoted as the "
            "LLM-as-Judge score. Re-run under LLM_PROVIDER=gemini, or judge with "
            "a stronger model, before treating this criterion as measured."
        )
    else:
        status = "pass" if mean >= TARGET_SCORE else "fail"
        headline = (
            f"mean overall {mean:.2f}/5 across {len(overalls)} MCQ(s) judged "
            f"(judge calibrated at {cal.accuracy:.0%})."
        )

    return SuiteResult(
        name="distractor-quality",
        criterion="LLM-as-Judge score",
        target=f">= {TARGET_SCORE}/5",
        status=status,
        headline=headline,
        metrics={
            **cal.as_metrics(),
            "calibration_separation": None if separation is None else round(separation, 3),
            "questions_judged": len(overalls),
            "questions_attempted": len(rows_db),
            "unparseable": unparsed,
            "mean_overall": round(mean, 3),
            "min_overall": min(overalls) if overalls else None,
            "max_overall": max(overalls) if overalls else None,
            "target": TARGET_SCORE,
        },
        rows=calibration_rows + corpus_rows,
        notes=notes,
    )
