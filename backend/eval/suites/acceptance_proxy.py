"""
eval/suites/acceptance_proxy.py
--------------------------------
AC: teacher acceptance rate >= 80% on a 100-answer set
(docs/PROJECT-BRIEF.md §6).

PROXY, NOT THE CRITERION. The brief's number is the fraction of AI grading
decisions a *teacher* accepts rather than overrides. That requires a teacher.
What this measures is agreement between the AI's score and a labeled expected
score on the committed 100-answer set — a necessary condition for a teacher to
accept, not the same thing. The report labels it accordingly and the number must
never be quoted as a teacher-acceptance rate.

Once real teacher decisions exist, the honest version of this metric is already
available in the schema: FinalGrade.action is "approved" or "overridden", so
acceptance = approved / (approved + overridden). That query is included in the
report as the real measurement to run later.
"""

from __future__ import annotations

from eval.harness import (
    SuiteResult,
    build_services,
    courses_by_name,
    grading_response,
    grading_state,
    load_dataset,
    retrieve_evidence,
)

TARGET_AGREEMENT = 0.80

#: Corpus the short-answer questions are drawn from. MCQ and numeric are graded
#: deterministically and need no evidence; short answers must have it, or the
#: grading prompt's "no evidence -> award 0" instruction zeroes every model
#: answer (see harness.retrieve_evidence).
CORPUS = "DSA"


async def run(limit: int | None = None) -> SuiteResult:
    from agents.grading.nodes import grade_responses_node

    data = load_dataset("acceptance_100.json")
    tolerance = float(data.get("tolerance", 0.001))
    answers = data["answers"]
    if limit:
        answers = answers[:limit]

    retrieval, gemini_pro, _flash = build_services()
    courses = await courses_by_name()
    course_id = courses.get(CORPUS)

    rows: list[dict] = []
    by_type: dict[str, dict[str, int]] = {}
    evidence_cache: dict[str, list[dict]] = {}
    missing_evidence = 0

    for item in answers:
        # Short answers go through the LLM rubric path, which is only meaningful
        # with retrieved evidence. Cached per stem so the set's repeated stems
        # cost one retrieval each rather than one per answer.
        evidence: list[dict] = []
        if item["question_type"] == "short_answer" and course_id is not None:
            stem = item["stem"]
            if stem not in evidence_cache:
                evidence_cache[stem] = retrieve_evidence(stem, course_id, retrieval)
            evidence = evidence_cache[stem]
            if not evidence:
                missing_evidence += 1

        response = grading_response(
            question_type=item["question_type"],
            stem=item["stem"],
            answer_key=item["answer_key"],
            max_points=item["max_points"],
            answer_text=item.get("student_answer_text"),
            answer_choice=item.get("student_answer_choice"),
            rubric_criteria=item.get("rubric_criteria"),
        )
        out = grade_responses_node(
            grading_state([response], course_id=course_id, evidence=[evidence]),
            gemini_pro=gemini_pro,
        )
        actual = round(out["total_score"], 4)
        expected = float(item["expected_score"])
        agrees = abs(actual - expected) <= tolerance

        bucket = by_type.setdefault(item["question_type"], {"n": 0, "agree": 0})
        bucket["n"] += 1
        bucket["agree"] += int(agrees)

        rows.append(
            {
                "id": item["id"],
                "type": item["question_type"],
                "expected": expected,
                "actual": actual,
                "agrees": agrees,
                "max_points": item["max_points"],
                "evidence_chunks": len(evidence),
            }
        )

    total = len(rows)
    agreed = sum(1 for r in rows if r["agrees"])
    rate = agreed / total if total else 0.0

    disagreements = [r for r in rows if not r["agrees"]]
    notes = [
        "PROXY METRIC — agreement with a labeled score, not teacher acceptance. "
        "The real measurement is approved / (approved + overridden) over "
        "FinalGrade.action once teachers have reviewed real submissions.",
        f"Tolerance for agreement: {tolerance} points.",
    ]
    if course_id is None:
        notes.append(
            f"WARNING: corpus {CORPUS!r} is not ingested, so short answers were "
            "graded with no evidence. The grading prompt awards 0 in that case, "
            "so the short_answer figure below is not a measurement of the grader."
        )
    elif missing_evidence:
        notes.append(
            f"WARNING: {missing_evidence} short answer(s) retrieved no evidence; "
            "those are forced to 0 by the grading prompt."
        )
    for qtype, b in sorted(by_type.items()):
        notes.append(f"{qtype}: {b['agree']}/{b['n']} agree ({b['agree'] / b['n']:.0%}).")
    if disagreements:
        sample = ", ".join(
            f"{d['id']}({d['type']} expected {d['expected']} got {d['actual']})"
            for d in disagreements[:6]
        )
        notes.append(f"{len(disagreements)} disagreement(s); first few: {sample}")

    return SuiteResult(
        name="acceptance-proxy",
        criterion="Teacher acceptance rate (PROXY)",
        target=f">= {TARGET_AGREEMENT:.0%}",
        status="pass" if rate >= TARGET_AGREEMENT else "fail",
        headline=f"{agreed}/{total} agree with label ({rate:.1%}).",
        metrics={
            "answers_graded": total,
            "agreements": agreed,
            "agreement_rate": round(rate, 4),
            "by_type": {k: {**v, "rate": round(v["agree"] / v["n"], 4)} for k, v in by_type.items()},
            "target": TARGET_AGREEMENT,
            "is_proxy": True,
            "short_answers_without_evidence": missing_evidence,
        },
        rows=rows,
        notes=notes,
    )
