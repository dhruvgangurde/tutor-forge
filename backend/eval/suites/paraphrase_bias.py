"""
eval/suites/paraphrase_bias.py
-------------------------------
AC: paraphrase score delta <= 5% (docs/PROJECT-BRIEF.md §5.3, §6).

Semantically equivalent answers must score equivalently. Each dataset group
holds one question and several paraphrases that vary register, hedging, length
and voice — the surface features a grader biased toward fluency or length would
reward. Every variant in a group is graded by the real grade_responses_node and
the spread within the group is measured.

Delta is expressed as a fraction of the question's max points, so a 0.15-point
spread on a 3-point question is 5%. That is the same normalisation the brief
implies by stating the target as a percentage rather than a raw score.
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

TARGET_DELTA = 0.05

#: Corpus the paraphrase questions are drawn from. Real evidence is retrieved
#: from it per group: grading a short answer with no evidence is not the
#: production path and forces every score to 0 (see harness.retrieve_evidence).
CORPUS = "DSA"


async def run() -> SuiteResult:
    from agents.grading.nodes import grade_responses_node

    data = load_dataset("paraphrase_pairs.json")
    retrieval, gemini_pro, _flash = build_services()
    courses = await courses_by_name()
    course_id = courses.get(CORPUS)
    if course_id is None:
        return SuiteResult(
            name="paraphrase-bias",
            criterion="Paraphrase score delta",
            target=f"<= {TARGET_DELTA:.0%}",
            status="skipped",
            headline=f"Corpus {CORPUS!r} is not ingested; cannot retrieve evidence.",
            notes=["Grading short answers without evidence would force every score to 0."],
        )

    rows: list[dict] = []
    group_deltas: list[float] = []

    for group in data["groups"]:
        max_points = group["max_points"]
        # Retrieve once per group: every variant answers the same question, so
        # they must be judged against identical evidence or the comparison is
        # measuring retrieval noise instead of paraphrase sensitivity.
        evidence = retrieve_evidence(group["question_stem"], course_id, retrieval)
        scores: dict[str, float] = {}
        for variant in group["variants"]:
            response = grading_response(
                question_type="short_answer",
                stem=group["question_stem"],
                answer_key={"correct_answer": ""},
                max_points=max_points,
                answer_text=variant["text"],
                rubric_criteria=group["rubric"],
            )
            out = grade_responses_node(
                grading_state([response], course_id=course_id, evidence=[evidence]),
                gemini_pro=gemini_pro,
            )
            scores[variant["id"]] = round(out["total_score"], 4)

        spread = max(scores.values()) - min(scores.values())
        delta = spread / max_points if max_points else 0.0
        group_deltas.append(delta)
        rows.append(
            {
                "group": group["id"],
                "evidence_chunks": len(evidence),
                "max_points": max_points,
                "scores": scores,
                "spread_points": round(spread, 4),
                "delta_fraction": round(delta, 4),
                "within_target": delta <= TARGET_DELTA,
            }
        )

    worst = max(group_deltas) if group_deltas else 0.0
    breaches = [r for r in rows if not r["within_target"]]

    notes = [
        f"Delta is spread / max_points per group; target <= {TARGET_DELTA:.0%}.",
        f"Each group graded against real evidence retrieved from the {CORPUS!r} "
        "corpus, identical across the group's variants.",
        "Variants differ only in register, hedging, length and voice — the "
        "content asserted by each is identical, so any spread is surface bias.",
        "STABILITY CAVEAT: this suite grades each variant ONCE, and two "
        "consecutive runs on identical inputs have produced different group "
        "deltas (e.g. huffman-frequency 50% then 0%; dijkstra-greedy 50% then "
        "100%). A single run's delta is therefore not yet a trustworthy number. "
        "Note this does not contradict grading-variance, which repeats ONE input "
        "in ONE process and reproduces exactly; the movement here is across "
        "processes, where evidence is retrieved afresh. Repeat-grading each "
        "variant would settle it and is not done yet.",
    ]
    for r in breaches:
        low = min(r["scores"], key=r["scores"].get)
        high = max(r["scores"], key=r["scores"].get)
        notes.append(
            f"{r['group']}: {r['delta_fraction']:.1%} spread — "
            f"lowest '{low}' at {r['scores'][low]}, highest '{high}' at {r['scores'][high]}."
        )

    return SuiteResult(
        name="paraphrase-bias",
        criterion="Paraphrase score delta",
        target=f"<= {TARGET_DELTA:.0%}",
        status="pass" if not breaches else "fail",
        headline=(
            f"worst group delta {worst:.1%} across {len(rows)} groups "
            f"({sum(len(g['variants']) for g in data['groups'])} variants graded); "
            f"{len(breaches)} group(s) over target."
        ),
        metrics={
            "groups": len(rows),
            "variants_graded": sum(len(g["variants"]) for g in data["groups"]),
            "worst_delta": round(worst, 4),
            "mean_delta": round(sum(group_deltas) / len(group_deltas), 4) if group_deltas else 0.0,
            "groups_over_target": len(breaches),
            "target_delta": TARGET_DELTA,
        },
        rows=rows,
        notes=notes,
    )
