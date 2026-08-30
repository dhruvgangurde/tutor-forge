"""
eval/suites/assessment_correctness.py
--------------------------------------
AC: assessment correctness >= 95% (docs/PROJECT-BRIEF.md §6).

What the brief actually says
----------------------------
The criterion is one table row and nothing else:

    | Assessment correctness  | >=95%      | Evaluation suite          |

There is no prose section defining it. The word "correct" appears twice in the
whole brief: that row, and "Factually correct" in §5.4 — whose five qualities
are explicitly validated by "LLM-as-Judge evaluation", i.e. the *other* §6 row
(LLM-as-Judge score >= 4/5). So this suite is built to the reading the two rows'
measurement shapes imply: LLM-as-Judge is a 1-5 mean about question *quality*;
a >=95% *rate* implies a per-question pass/fail verdict about whether the
question is *right*.

Correct here means both of:
  - the answer the generator MARKED is actually the correct one, and
  - the question is answerable from the retrieved course context.

Why this is not the distractor-quality suite
--------------------------------------------
That suite scores "is exactly one option defensibly correct" (single_answer).
A well-formed MCQ with exactly one defensible answer, where the generator marked
the WRONG letter as the key, scores 5/5 there and is still wrong. Nothing in the
eval suite caught that failure mode before this.

Two phases, and the order matters
---------------------------------
  1. CALIBRATION over datasets/assessment_correctness.json, whose items carry
     known verdicts including deliberately mis-keyed and unanswerable ones. This
     measures the JUDGE.
  2. CORPUS rate over the real generated questions in the database. This is the
     number the criterion is about.

Phase 1 gates the credibility of phase 2. A judge that cannot catch a
deliberately mis-keyed question cannot be trusted to certify that 95% of real
questions are correctly keyed, so the suite reports calibration alongside the
rate and refuses to claim a pass on an uncalibrated judge.
"""

from __future__ import annotations

import json
import re

from eval.harness import (
    DATASETS,
    SuiteResult,
    build_services,
    courses_by_name,
    load_dataset,
    retrieve_evidence,
)

TARGET_RATE = 0.95

#: Minimum judge accuracy on the labeled set before the corpus rate is treated
#: as a measurement rather than as an indication.
MIN_JUDGE_ACCURACY = 0.80

#: Cap on real questions judged per run — one LLM call each.
MAX_CORPUS_QUESTIONS = 12


def resolve_marked_answer(correct: str, options: list[str] | None) -> str:
    """
    Render the marked answer as text, not a bare letter.

    Calibration showed the judge reading the context correctly and then failing
    the letter->option lookup: for a question keyed "B" whose option B was
    "O(log n)", it wrote "the context says O(log n), which contradicts the
    marked correct answer B. The correct answer is A." Making the judge do MCQ
    letter arithmetic was a harness defect, not a model opinion, so the marked
    answer is now spelled out.
    """
    if not options:
        return correct or "(none recorded)"
    letter = (correct or "").strip().rstrip(".").upper()
    for option in options:
        head = option.split(".", 1)[0].strip().upper()
        if head == letter:
            return f"{option}   (marked as option {letter})"
    return f"{correct}   (WARNING: no option matches this key)"


def _rubric_prompt(
    question_type: str, stem: str, options: list[str], correct: str, context: str
) -> str:
    template = (DATASETS / "assessment_correctness_rubric.md").read_text(encoding="utf-8")
    return (
        template.replace("{question_type}", question_type)
        .replace("{stem}", stem)
        .replace("{options}", "\n".join(options) if options else "(not a multiple-choice question)")
        .replace("{correct_answer}", correct)
        .replace("{context}", context[:2500] or "(no course context retrieved)")
    )


def _parse_verdict(raw: str) -> dict | None:
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


def _context_text(evidence: list[dict]) -> str:
    return "\n\n".join(
        f"[Source: {e['source_file']}, p.{e['page_or_slide']}]\n{e['text']}" for e in evidence
    )


def _judge(gemini_pro, question_type, stem, options, correct, context) -> dict | None:
    prompt = _rubric_prompt(
        question_type, stem, options, resolve_marked_answer(correct, options), context
    )
    try:
        raw = gemini_pro.generate_deterministic(prompt)
    except Exception:  # noqa: BLE001 - one bad call must not sink the suite
        return None
    return _parse_verdict(raw)


async def run(limit: int = MAX_CORPUS_QUESTIONS) -> SuiteResult:
    from sqlalchemy import select

    from core.database import AsyncSessionFactory
    from db.models import Assessment, Question

    data = load_dataset("assessment_correctness.json")
    retrieval, gemini_pro, _flash = build_services()
    courses = await courses_by_name()
    corpus_name = data.get("corpus", "DSA")
    calibration_course = courses.get(corpus_name)

    if calibration_course is None:
        return SuiteResult(
            name="assessment-correctness",
            criterion="Assessment correctness",
            target=f">= {TARGET_RATE:.0%}",
            status="skipped",
            headline=f"Calibration corpus {corpus_name!r} is not ingested.",
            notes=["The judge cannot be calibrated without real course context."],
        )

    # ── Phase 1: calibrate the judge on known verdicts ────────────────────────
    calibration_rows: list[dict] = []
    for item in data["items"]:
        evidence = retrieve_evidence(item["stem"], calibration_course, retrieval)
        verdict = _judge(
            gemini_pro,
            item["question_type"],
            item["stem"],
            item.get("options"),
            item.get("correct_answer", ""),
            _context_text(evidence),
        )
        judged = None if verdict is None else bool(verdict.get("correct"))
        calibration_rows.append(
            {
                "id": item["id"],
                "expected_correct": item["expected_correct"],
                "judged_correct": judged,
                "agrees": judged is not None and judged == item["expected_correct"],
                "failure_mode": item.get("failure_mode"),
                "evidence_chunks": len(evidence),
                "reason": (verdict or {}).get("reason", "")[:160],
            }
        )

    scored = [r for r in calibration_rows if r["judged_correct"] is not None]
    judge_accuracy = (
        sum(1 for r in scored if r["agrees"]) / len(scored) if scored else 0.0
    )
    # Catching a deliberately wrong key is the capability this suite depends on.
    miskeyed = [r for r in calibration_rows if r["failure_mode"] == "miskeyed"]
    miskeyed_caught = sum(1 for r in miskeyed if r["judged_correct"] is False)
    unanswerable = [
        r for r in calibration_rows if r["failure_mode"] == "unanswerable_from_corpus"
    ]
    unanswerable_caught = sum(1 for r in unanswerable if r["judged_correct"] is False)

    # ── Phase 2: the real corpus rate ─────────────────────────────────────────
    async with AsyncSessionFactory() as db:
        db_rows = (
            await db.execute(
                select(Question, Assessment.title, Assessment.course_id)
                .join(Assessment, Question.assessment_id == Assessment.id)
                .order_by(Question.order_index)
                .limit(limit)
            )
        ).all()

    corpus_rows: list[dict] = []
    for question, title, course_id in db_rows:
        try:
            options = json.loads(question.options) if question.options else []
        except json.JSONDecodeError:
            options = []
        try:
            key = json.loads(question.answer_key) if question.answer_key else {}
        except json.JSONDecodeError:
            key = {}

        evidence = retrieve_evidence(question.stem, course_id, retrieval)
        verdict = _judge(
            gemini_pro,
            question.question_type,
            question.stem,
            options,
            str(key.get("correct_answer", "")),
            _context_text(evidence),
        )
        corpus_rows.append(
            {
                "assessment": title,
                "type": question.question_type,
                "stem": question.stem[:80],
                "evidence_chunks": len(evidence),
                "key_correct": None if verdict is None else verdict.get("key_correct"),
                "answerable": None if verdict is None else verdict.get("answerable_from_context"),
                "correct": None if verdict is None else bool(verdict.get("correct")),
                "reason": (verdict or {}).get("reason", "")[:160],
            }
        )

    judged_corpus = [r for r in corpus_rows if r["correct"] is not None]
    correct_count = sum(1 for r in judged_corpus if r["correct"])
    rate = correct_count / len(judged_corpus) if judged_corpus else 0.0
    unparseable = len(corpus_rows) - len(judged_corpus)

    notes = [
        "Correct = the MARKED answer is right AND the question is answerable "
        "from retrieved course context. Distinct from distractor-quality, which "
        "scores whether exactly one option is defensible and would pass a "
        "well-formed question with the wrong letter keyed.",
        f"Judge calibration on {len(scored)} labeled item(s): "
        f"{judge_accuracy:.0%} agreement with known verdicts.",
        f"Deliberately mis-keyed items caught: {miskeyed_caught}/{len(miskeyed)}.",
        f"Unanswerable-from-corpus items caught: {unanswerable_caught}/{len(unanswerable)}.",
        "Judge is the project's own configured provider, judging output from the "
        "same model family — a weak evaluation, which is exactly why the "
        "calibration phase exists.",
    ]
    if unparseable:
        notes.append(f"{unparseable} corpus question(s) returned no parseable verdict.")

    # An uncalibrated judge does not get to certify a pass.
    if not judged_corpus:
        status = "skipped"
        headline = "No generated questions could be judged."
    elif judge_accuracy < MIN_JUDGE_ACCURACY:
        status = "not_measurable"
        headline = (
            f"Judge calibration {judge_accuracy:.0%} is below the "
            f"{MIN_JUDGE_ACCURACY:.0%} bar, so the {rate:.0%} corpus rate is not "
            "a trustworthy measurement."
        )
        notes.append(
            "Reported as NOT MEASURABLE rather than as a pass or a fail: the "
            "judge did not demonstrate it can tell a correctly keyed question "
            "from a deliberately mis-keyed one, so its verdict on real questions "
            "carries no weight either way."
        )
    else:
        status = "pass" if rate >= TARGET_RATE else "fail"
        headline = (
            f"{correct_count}/{len(judged_corpus)} generated questions correct "
            f"({rate:.1%}); judge calibrated at {judge_accuracy:.0%}."
        )

    return SuiteResult(
        name="assessment-correctness",
        criterion="Assessment correctness",
        target=f">= {TARGET_RATE:.0%}",
        status=status,
        headline=headline,
        metrics={
            "corpus_questions_judged": len(judged_corpus),
            "corpus_questions_attempted": len(corpus_rows),
            "corpus_correct": correct_count,
            "corpus_correct_rate": round(rate, 4),
            "unparseable_verdicts": unparseable,
            "judge_calibration_items": len(scored),
            "judge_accuracy": round(judge_accuracy, 4),
            "miskeyed_caught": f"{miskeyed_caught}/{len(miskeyed)}",
            "unanswerable_caught": f"{unanswerable_caught}/{len(unanswerable)}",
            "min_judge_accuracy": MIN_JUDGE_ACCURACY,
            "target": TARGET_RATE,
        },
        rows=[{"phase": "calibration", **r} for r in calibration_rows]
        + [{"phase": "corpus", **r} for r in corpus_rows],
        notes=notes,
    )
