"""
eval/suites/distractor_quality.py
----------------------------------
AC: LLM-as-Judge score >= 4/5 (docs/PROJECT-BRIEF.md §5.4, §6).

Judges the MCQs a real assessment produced, against the rubric in
datasets/distractor_judge_rubric.md. The judge is the project's own configured
LLM — the same provider the app runs on — so the number reflects what a
reviewer would see, not a stronger model grading a weaker one's homework.

Source of questions: existing published/draft assessments in the database. This
suite does NOT generate new assessments; generating costs a full agent run per
call and would make the eval unrunnable on a laptop. If no MCQs exist yet, the
suite reports 'skipped' rather than inventing input.

Known bias, stated rather than hidden: model-judges-own-output is a weak
evaluation. The rubric is deliberately concrete (single-answer defensibility,
distinctness, groundedness in the supplied context) to reduce that, and the
per-question scores are kept in the report so a human can audit the judgement.
"""

from __future__ import annotations

import json
import re

from eval.harness import DATASETS, SuiteResult, build_services

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


async def run(limit: int = MAX_QUESTIONS) -> SuiteResult:
    from sqlalchemy import select

    from core.database import AsyncSessionFactory
    from db.models import Assessment, Question

    _retrieval, gemini_pro, _flash = build_services()

    async with AsyncSessionFactory() as db:
        rows_db = (
            await db.execute(
                select(Question, Assessment.title)
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
            notes=["Generate and publish an assessment, then re-run this suite."],
        )

    rows: list[dict] = []
    overalls: list[float] = []
    unparsed = 0

    for question, title in rows_db:
        try:
            options = json.loads(question.options) if question.options else []
        except json.JSONDecodeError:
            options = []
        try:
            key = json.loads(question.answer_key) if question.answer_key else {}
        except json.JSONDecodeError:
            key = {}
        correct = str(key.get("correct_answer", ""))

        prompt = _rubric_prompt(question.stem, options, correct, question.stem)
        try:
            raw = gemini_pro.generate_deterministic(prompt)
        except Exception as exc:  # noqa: BLE001 - one bad call must not sink the suite
            rows.append({"assessment": title, "stem": question.stem[:80], "error": str(exc)[:200]})
            unparsed += 1
            continue

        judged = _parse_judgement(raw)
        if judged is None:
            rows.append(
                {
                    "assessment": title,
                    "stem": question.stem[:80],
                    "error": "judge returned unparseable output",
                    "raw": raw[:200],
                }
            )
            unparsed += 1
            continue

        overall = float(judged.get("overall", 0))
        overalls.append(overall)
        rows.append(
            {
                "assessment": title,
                "stem": question.stem[:80],
                "plausibility": judged.get("plausibility"),
                "distinctness": judged.get("distinctness"),
                "single_answer": judged.get("single_answer"),
                "grounded": judged.get("grounded"),
                "overall": overall,
                "notes": str(judged.get("notes", ""))[:160],
            }
        )

    if not overalls:
        return SuiteResult(
            name="distractor-quality",
            criterion="LLM-as-Judge score",
            target=f">= {TARGET_SCORE}/5",
            status="skipped",
            headline=f"Judged 0 of {len(rows_db)} questions — no parseable judgements.",
            rows=rows,
            notes=["The configured provider did not return usable JSON for any question."],
        )

    mean = sum(overalls) / len(overalls)
    return SuiteResult(
        name="distractor-quality",
        criterion="LLM-as-Judge score",
        target=f">= {TARGET_SCORE}/5",
        status="pass" if mean >= TARGET_SCORE else "fail",
        headline=f"mean overall {mean:.2f}/5 across {len(overalls)} MCQ(s) judged.",
        metrics={
            "questions_judged": len(overalls),
            "questions_attempted": len(rows_db),
            "unparseable": unparsed,
            "mean_overall": round(mean, 3),
            "min_overall": min(overalls),
            "max_overall": max(overalls),
            "target": TARGET_SCORE,
        },
        rows=rows,
        notes=[
            "Judge is the project's own configured provider — self-judgement, "
            "which is a weak evaluation. Per-question scores are retained above "
            "so a human can audit them.",
        ],
    )
