"""
eval/suites/grading_variance.py
--------------------------------
AC: grading variance = 0 (docs/PROJECT-BRIEF.md §5.2, §6).

"Same answer x 10 runs" must produce identical criterion scores, identical
totals, and identical feedback. Grading runs at temperature=0 by design
(``generate_deterministic``), so this is directly measurable.

Runs the real ``grade_responses_node`` repeatedly on identical input and diffs
the outputs. Both paths are covered and reported separately, because they have
genuinely different determinism guarantees:

  deterministic  MCQ + numeric — exact match / tolerance comparison, no model
                 call. Variance here would be a code defect.
  llm_rubric     short answer — one temperature=0 model call per question.
                 Variance here is the provider's determinism, which the repo
                 already documents as weaker on Ollama than on Gemini
                 (core/ollama_client.py). Reported separately so a provider
                 artefact is never mistaken for a grading-logic defect.
"""

from __future__ import annotations

import json

from eval.harness import (
    SuiteResult,
    build_services,
    courses_by_name,
    grading_response,
    grading_state,
    retrieve_evidence,
)

DEFAULT_RUNS = 10

#: Corpus the short-answer case is graded against. Load-bearing since the
#: grading groundedness gate landed: an ungrounded response is flagged for
#: review WITHOUT an LLM call, so running this suite with no evidence would
#: measure the deterministic gate path and report a trivially perfect PASS while
#: never exercising the model at all.
CORPUS = "DSA"


def _signature(state: dict) -> str:
    """Canonical form of one grading result: per-criterion scores + feedback."""
    return json.dumps(
        {
            "total": round(state["total_score"], 6),
            "max": round(state["max_score"], 6),
            "criteria": [
                [
                    {
                        "description": c["description"],
                        "score": round(c["score"], 6),
                        "feedback": c["feedback"],
                    }
                    for c in q
                ]
                for q in state["criterion_results"]
            ],
        },
        sort_keys=True,
    )


async def run(runs: int = DEFAULT_RUNS) -> SuiteResult:
    from agents.grading.nodes import grade_responses_node

    retrieval, gemini_pro, _flash = build_services()
    courses = await courses_by_name()
    course_id = courses.get(CORPUS)
    if course_id is None:
        return SuiteResult(
            name="grading-variance",
            criterion="Grading variance",
            target="0 across repeat runs",
            status="skipped",
            headline=f"Corpus {CORPUS!r} is not ingested.",
            notes=[
                "Without evidence the groundedness gate flags the short answer "
                "without calling the model, so this run would report a PASS "
                "that never exercised the LLM path."
            ],
        )

    deterministic = [
        grading_response(
            question_type="mcq",
            stem="What is the time complexity of binary search?",
            answer_key={"correct_answer": "B"},
            max_points=1.0,
            answer_choice="B",
        ),
        grading_response(
            question_type="mcq",
            stem="Which algorithm expands the nearest unvisited vertex?",
            answer_key={"correct_answer": "C"},
            max_points=1.0,
            answer_choice="A",  # wrong on purpose
        ),
        grading_response(
            question_type="numeric",
            stem="log2(256)?",
            answer_key={"correct_answer": "8"},
            max_points=1.0,
            answer_text="8",
        ),
        grading_response(
            question_type="numeric",
            stem="Minimum multiplications for (AB)C?",
            answer_key={"correct_answer": "4500"},
            max_points=1.0,
            answer_text="12",  # wrong on purpose
        ),
    ]
    llm_backed = [
        grading_response(
            question_type="short_answer",
            stem="Explain why binary search requires a sorted array.",
            answer_key={"correct_answer": "Ordering lets you discard half the range."},
            max_points=3.0,
            answer_text=(
                "Because the array is ordered, comparing the target with the middle "
                "element tells you which half it must be in, so the other half is discarded."
            ),
            rubric_criteria=[
                {"description": "States that ordering allows halving the search space.", "max_points": 2.0},
                {"description": "Mentions comparison with the middle element.", "max_points": 1.0},
            ],
        )
    ]

    # Retrieved once and reused across all runs: the point is to vary nothing.
    llm_evidence = retrieve_evidence(llm_backed[0]["stem"], course_id, retrieval)

    groups = {"deterministic": deterministic, "llm_rubric": llm_backed}
    evidence_for = {
        "deterministic": None,
        "llm_rubric": [llm_evidence],
    }
    metrics: dict = {"runs": runs, "llm_evidence_chunks": len(llm_evidence)}
    rows: list[dict] = []
    notes: list[str] = []
    any_variance = False

    for group_name, responses in groups.items():
        signatures: list[str] = []
        totals: list[float] = []
        for _ in range(runs):
            state = grading_state(
                list(responses),
                course_id=course_id,
                evidence=evidence_for[group_name],
            )
            out = grade_responses_node(state, gemini_pro=gemini_pro)
            signatures.append(_signature(out))
            totals.append(round(out["total_score"], 6))

        distinct = sorted(set(signatures))
        identical = len(distinct) == 1
        if not identical:
            any_variance = True

        metrics[group_name] = {
            "distinct_results": len(distinct),
            "identical": identical,
            "totals_observed": sorted(set(totals)),
            "score_spread": round(max(totals) - min(totals), 6) if totals else 0.0,
        }
        rows.append(
            {
                "group": group_name,
                "runs": runs,
                "distinct_results": len(distinct),
                "identical": identical,
                "totals_observed": sorted(set(totals)),
            }
        )
        if not identical:
            notes.append(
                f"{group_name}: {len(distinct)} distinct outputs across {runs} runs, "
                f"totals {sorted(set(totals))}."
            )

    if not llm_evidence:
        notes.append(
            "WARNING: no evidence retrieved for the short-answer case, so the "
            "groundedness gate flagged it without an LLM call. The llm_rubric "
            "result below measures the gate, not the model."
        )
    if metrics["deterministic"]["identical"] and not metrics["llm_rubric"]["identical"]:
        notes.append(
            "Deterministic scoring (MCQ/numeric) is bit-identical; the variance is "
            "confined to the temperature=0 model call on short answers. That is "
            "provider determinism, not grading logic — core/ollama_client.py "
            "already records Ollama's determinism as weaker than Gemini's. "
            "Re-run under LLM_PROVIDER=gemini before quoting this against the AC."
        )

    return SuiteResult(
        name="grading-variance",
        criterion="Grading variance",
        target="0 across repeat runs",
        status="fail" if any_variance else "pass",
        headline=(
            f"deterministic: {metrics['deterministic']['distinct_results']} distinct / {runs} runs; "
            f"llm_rubric: {metrics['llm_rubric']['distinct_results']} distinct / {runs} runs."
        ),
        metrics=metrics,
        rows=rows,
        notes=notes,
    )
