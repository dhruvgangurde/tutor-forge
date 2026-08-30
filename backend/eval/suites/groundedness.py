"""
eval/suites/groundedness.py
----------------------------
AC: out-of-corpus leakage = 0 (docs/PROJECT-BRIEF.md §5.1, §6).

Runs every labeled probe through the real retrieval stack and the real gate
(``RetrievalService.is_grounded`` at ``settings.active_groundedness_threshold``)
and reports what the gate actually decided.

Two numbers, kept apart on purpose:

  LEAKAGE       an off-topic probe the gate ACCEPTED. This is the acceptance
                criterion. Any non-zero value is a failure.
  FALSE REFUSAL an on-topic probe the gate REFUSED. Not the criterion, but
                reported because a gate that refuses everything would otherwise
                score a perfect zero-leakage pass.

Contentless follow-ups are scored separately again. LIVE-TESTING item 14 found
they fall below the bar on their own text; the tutor now substitutes the prior
turn's question before retrieving (agents/tutor/query_resolution.py), so this
suite measures both paths and reports the difference rather than assuming
either is correct.

Scope: the tutor and assessment agents share this gate. The grading agent has
none (BUG-AUDIT Critical #2, still open), so grading contributes no number here.
"""

from __future__ import annotations

from eval.harness import SuiteResult, build_services, courses_by_name, load_dataset


async def run() -> SuiteResult:
    from core.config import settings

    data = load_dataset("out_of_corpus_probes.json")
    retrieval, _pro, _flash = build_services()
    courses = await courses_by_name()
    threshold = settings.active_groundedness_threshold

    rows: list[dict] = []
    skipped_corpora: set[str] = set()

    for probe in data["probes"]:
        course_id = courses.get(probe["corpus"])
        if course_id is None:
            skipped_corpora.add(probe["corpus"])
            continue

        # Score the probe on its own text — what the gate sees with no history.
        alone = retrieval.retrieve(course_id, probe["text"])
        alone_score = alone.top_score if not alone.is_empty() else 0.0
        alone_grounded = retrieval.is_grounded(alone, threshold=threshold)

        # For contentless follow-ups, also score the query the tutor actually
        # sends after resolution, so the report can separate a threshold
        # artefact from a genuine gate failure.
        resolved_score = None
        resolved_grounded = None
        if probe.get("prior"):
            from agents.tutor.query_resolution import resolve_retrieval_query

            resolved_query, source = resolve_retrieval_query(
                probe["text"], probe["prior"]
            )
            if source != "message":
                res = retrieval.retrieve(course_id, resolved_query)
                resolved_score = res.top_score if not res.is_empty() else 0.0
                resolved_grounded = retrieval.is_grounded(res, threshold=threshold)

        effective = resolved_grounded if resolved_grounded is not None else alone_grounded
        expected = probe["label"] == "grounded"
        rows.append(
            {
                "id": probe["id"],
                "kind": probe["kind"],
                "corpus": probe["corpus"],
                "text": probe["text"],
                "expected": probe["label"],
                "smuggling": probe.get("smuggling"),
                "score_alone": round(alone_score, 4),
                "grounded_alone": alone_grounded,
                "score_resolved": None if resolved_score is None else round(resolved_score, 4),
                "grounded_effective": effective,
                "correct": effective == expected,
            }
        )

    if not rows:
        return SuiteResult(
            name="groundedness",
            criterion="Out-of-corpus leakage",
            target="0",
            status="skipped",
            headline="No ingested course matched any probe corpus.",
            notes=[f"Corpora referenced but absent: {sorted(skipped_corpora)}"],
        )

    off = [r for r in rows if r["kind"] in ("off_topic", "adversarial")]
    on = [r for r in rows if r["kind"] == "on_topic"]
    cf = [r for r in rows if r["kind"] == "contentless_followup"]

    leaks = [r for r in off if r["grounded_effective"]]
    false_refusals = [r for r in on if not r["grounded_effective"]]
    cf_refused_alone = [r for r in cf if not r["grounded_alone"]]
    cf_served = [r for r in cf if r["grounded_effective"]]

    # Separation check: does the raw score actually distinguish the classes?
    on_scores = [r["score_alone"] for r in on]
    off_scores = [r["score_alone"] for r in off]
    cf_scores = [r["score_alone"] for r in cf]
    min_on = min(on_scores) if on_scores else None
    max_off = max(off_scores) if off_scores else None
    separated = min_on is not None and max_off is not None and min_on > max_off

    notes = [
        f"Threshold in effect: {threshold} (provider={settings.llm_provider}).",
        (
            f"Score separation on-topic vs off-topic: min(on)={min_on}, "
            f"max(off)={max_off} -> "
            + ("separable" if separated else "OVERLAPPING — no threshold separates these classes")
        ),
    ]
    if cf:
        notes.append(
            f"Contentless follow-ups scored on their own text: "
            f"{len(cf_refused_alone)}/{len(cf)} fall below the bar "
            f"(range {min(cf_scores)}-{max(cf_scores)}). After query resolution "
            f"substitutes the prior turn, {len(cf_served)}/{len(cf)} are served. "
            "This reproduces LIVE-TESTING item 14: the raw score does not "
            "separate on-topic-but-contentless from off-topic."
        )
        overlap = [
            r for r in cf if max_off is not None and r["score_alone"] <= max_off
        ]
        if overlap:
            notes.append(
                f"{len(overlap)} contentless follow-up(s) score at or BELOW the "
                f"highest off-topic probe ({max_off}) — measured, not assumed: "
                + ", ".join(f"{r['id']}={r['score_alone']}" for r in overlap)
            )
    if skipped_corpora:
        notes.append(f"Corpora absent, probes skipped: {sorted(skipped_corpora)}")
    notes.append(
        "SCOPE: this number covers the tutor and assessment agents, which share "
        "RetrievalService.is_grounded. The GRADING agent has no groundedness gate "
        "at all (docs/BUG-AUDIT-2026-08-15.md Critical #2 - re-confirmed open: no "
        "is_grounded call exists anywhere in agents/grading/). There is no gate "
        "decision to probe there, so grading contributes no leakage number and "
        "this criterion is NOT fully covered for the system as a whole."
    )

    # ── Threshold sweep ───────────────────────────────────────────────────────
    # The one-probe question ("would a higher bar close this leak?") is the wrong
    # question; the right one is what that bar costs in false refusals. Swept
    # here so the tradeoff is data in the report rather than an argument.
    sweep = []
    for bar in [round(0.50 + 0.01 * i, 2) for i in range(26)]:
        sweep_leaks = sum(1 for r in off if r["score_alone"] >= bar)
        # On-topic and contentless both represent work a student legitimately
        # expects to be served; contentless is scored on its resolved query,
        # which is what the tutor actually sends.
        sweep_false_on = sum(1 for r in on if r["score_alone"] < bar)
        sweep_false_cf = sum(
            1
            for r in cf
            if (r["score_resolved"] if r["score_resolved"] is not None else r["score_alone"])
            < bar
        )
        sweep.append(
            {
                "threshold": bar,
                "leaks": sweep_leaks,
                "false_refusals_on_topic": sweep_false_on,
                "false_refusals_contentless": sweep_false_cf,
            }
        )

    zero_leak = [row for row in sweep if row["leaks"] == 0]
    cheapest_zero_leak = min(
        zero_leak,
        key=lambda row: (
            row["false_refusals_on_topic"] + row["false_refusals_contentless"],
            row["threshold"],
        ),
        default=None,
    )
    if cheapest_zero_leak:
        notes.append(
            "THRESHOLD SWEEP: the lowest bar reaching zero leakage on this probe "
            f"set is {cheapest_zero_leak['threshold']}, costing "
            f"{cheapest_zero_leak['false_refusals_on_topic']} on-topic and "
            f"{cheapest_zero_leak['false_refusals_contentless']} contentless false "
            "refusal(s) HERE. That is not a recommendation: core/config.py records "
            "the original calibration over 78 queries, where 0.65 cost 7/34 (20.6%) "
            "false refusals. A bar tuned on this 36-probe set would be overfitted "
            "to it."
        )
    else:
        notes.append(
            "THRESHOLD SWEEP: no threshold in 0.50-0.75 reaches zero leakage "
            "without refusing on-topic work - the classes are not separable by "
            "score alone on this probe set."
        )

    if not separated:
        notes.append(
            "NOT SEPARABLE: the highest-scoring off-topic probe now outscores the "
            "lowest-scoring on-topic one, so NO threshold achieves zero leakage "
            "without refusing legitimate work. Raising the bar is not a fix for "
            "this failure mode - see the per-technique breakdown."
        )

    smuggling = {}
    for r in rows:
        if r["kind"] != "adversarial":
            continue
        # `or` not a .get default: untagged probes store None explicitly, so
        # the default never fires and sorted() would compare None with str.
        tag = r.get("smuggling") or "untagged"
        bucket = smuggling.setdefault(tag, {"n": 0, "leaked": 0, "max_score": 0.0})
        bucket["n"] += 1
        bucket["leaked"] += int(r["grounded_effective"])
        bucket["max_score"] = max(bucket["max_score"], r["score_alone"])
    dominant = [
        tag
        for tag, b in smuggling.items()
        if b["n"] >= 3 and b["leaked"] == b["n"]
    ]
    for tag in dominant:
        notes.append(
            f"SYSTEMATIC: every {tag} probe leaked ({smuggling[tag]['n']}/"
            f"{smuggling[tag]['n']}). This technique wraps an off-topic REQUEST in "
            "on-topic VOCABULARY, so the embedding genuinely is close to the "
            "corpus. That is a semantic mismatch a similarity score cannot see, "
            "which is why no threshold closes it."
        )
    if smuggling:
        notes.append(
            "ADVERSARIAL BY TECHNIQUE: "
            + "; ".join(
                f"{tag} {b['leaked']}/{b['n']} leaked (max score {b['max_score']:.4f})"
                for tag, b in sorted(smuggling.items())
            )
        )

    status = "pass" if not leaks else "fail"
    return SuiteResult(
        name="groundedness",
        criterion="Out-of-corpus leakage",
        target="0",
        status=status,
        headline=(
            f"{len(leaks)} leak(s) out of {len(off)} off-topic/adversarial probes; "
            f"{len(false_refusals)} false refusal(s) out of {len(on)} on-topic probes."
        ),
        metrics={
            "probes_run": len(rows),
            "off_topic_probes": len(off),
            "leaks": len(leaks),
            "leak_rate": round(len(leaks) / len(off), 4) if off else None,
            "on_topic_probes": len(on),
            "false_refusals": len(false_refusals),
            "false_refusal_rate": round(len(false_refusals) / len(on), 4) if on else None,
            "contentless_followups": len(cf),
            "contentless_refused_on_own_text": len(cf_refused_alone),
            "contentless_served_after_resolution": len(cf_served),
            "threshold": threshold,
            "min_on_topic_score": min_on,
            "max_off_topic_score": max_off,
            "classes_separable_by_score": separated,
            "threshold_sweep": sweep,
            "cheapest_zero_leak_threshold": cheapest_zero_leak,
            "adversarial_by_technique": smuggling,
        },
        rows=rows,
        notes=notes,
    )
