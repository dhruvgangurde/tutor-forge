"""
eval/suites/novice_learner.py
-----------------------------
A beginner learning dynamic programming, replayed from the live session
5c6672fa (datasets/novice_learner.json): the student's turns verbatim, a
synthetic DP lab sheet as the course, a published quiz in one course.

It runs every turn through the real tutoring service (in-memory SQLite), the
tutor graph, the follow-up rewrite, the output guard and the course-gap
handling, with retrieval over an in-memory Chroma collection on the mock
embedder and bar. Offline (the default, and the pytest gate) the model's
replies and the rewrites are recorded; live (``live=True``, never in pytest)
Gemini writes both. Nothing touches the demo database or the Chroma store.

Scored on:
  - a correct explanation within N tutor turns of a concept being asked
    (golden facts per concept);
  - no false refusals (an on-topic turn refused by the course check), and the
    off-topic question still refused;
  - context kept (a follow-up retrieved on the topic of the conversation);
  - an example (the word and a number) when the student asked for one;
  - graded-question leakage (the published quiz's recurrence never shown);
  - and what the student must never see: a bare "this course doesn't cover
    that part", one glued to other text, or a claim of progress they did not
    make; a full explanation that is only a gap never moves the ladder; "no"
    after a full explanation asks for the explanation again.

Items marked step2 (an explanation within N turns, an example when asked,
"no" at the low rungs) are expected to fail until Explain mode (Step 2); they
are reported as known gaps, not gated.
"""

from __future__ import annotations

import contextlib
import json
import re
import time

from eval.harness import SuiteResult, load_dataset
from eval.suites.tutor_pedagogy import (
    LiveModel,
    ScriptedModel,
    _build_retrieval,
    _conversation_db,
    _guard_rules,
    _live_clients,
    _mock_provider,
    _NoTracing,
    facts_in,
)

DATASET = "novice_learner.json"
LIVE_NOT_MEASURED = "not measured (offline run: recorded model outputs, no live Gemini call)"
_PROGRESS_RE = re.compile(r"\bnow that you(?:'ve|\s+have|'re|\s+are)\b", re.IGNORECASE)
_EXPLAIN_AGAIN = "Explain it again from a different angle"


class RecordedRewriter:
    """The rewrite recorded for the current turn; "new topic" (no rewrite) when none."""

    def __init__(self):
        self.current: dict | None = None
        self.calls = 0

    def generate(self, prompt: str, **_kwargs) -> str:
        self.calls += 1
        return json.dumps(self.current or {"standalone": "", "new_topic": True})


def _is_out_of_corpus(final: dict) -> bool:
    trace = final.get("pedagogy_trace", {})
    return not final.get("is_grounded") or trace.get("refusal_kind") == "out_of_corpus"


async def _run_conversation(conv, data, factory, retrieval, course_id, student_id, live, rewriter):
    from agents.tutor.output_guard import is_course_gap_reply
    from agents.tutor.prompts import COURSE_GAP_MESSAGE
    from tutoring.service import create_session, request_hint, send_chat_message

    rows: list[dict] = []
    async with factory() as db:
        session = await create_session(course_id, student_id, db)
        for n, turn in enumerate(conv["turns"], 1):
            model = LiveModel(live["pro"], live["counter"]) if live else ScriptedModel(turn.get("model_output", []))
            flash = LiveModel(live["flash"], live["counter"]) if live else rewriter
            rewriter.current = turn.get("rewrite")
            level_before = session.current_hint_level
            with _guard_rules() as rules:
                if turn["kind"] == "hint":
                    try:
                        final = await request_hint(session, db, retrieval, model, _NoTracing())
                    except ValueError:
                        final = {"is_grounded": False, "response": "", "pedagogy_trace": {}}
                    text = None
                else:
                    text = turn["text"]
                    final = await send_chat_message(
                        session, text, db, retrieval, model, _NoTracing(), gemini_flash=flash
                    )
            shown = final.get("response", "")
            query = final.get("retrieval_query") or final.get("resolved_question") or ""
            row = {
                "conversation": conv["id"],
                "turn": n,
                "kind": turn["kind"],
                "message": text,
                "expect": turn["expect"],
                "refused": _is_out_of_corpus(final),
                "course_gap_message": shown == COURSE_GAP_MESSAGE,
                "bare_gap_shown": is_course_gap_reply(shown) and shown != COURSE_GAP_MESSAGE,
                "glued_gap_shown": bool(re.search(r"course\s+(?:material\s+)?(?:doesn't|does not)\s+cover", shown))
                and shown != COURSE_GAP_MESSAGE and not is_course_gap_reply(shown),
                "progress_claim_shown": bool(_PROGRESS_RE.search(shown)),
                "level_before": level_before,
                "level_after": session.current_hint_level,
                "query_source": final.get("retrieval_query_source"),
                "rules": rules,
                "response": shown,
                "step2": turn.get("step2", []),
            }
            row["as_expected"] = row["refused"] == (turn["expect"] == "refused")
            if turn.get("concept"):
                row["concept"] = turn["concept"]
            if turn.get("context"):
                row["context_kept"] = turn["context"].lower() in query.lower()
            if turn.get("example_requested"):
                row["example_given"] = "example" in shown.lower() and bool(re.search(r"\d", shown))
            if turn.get("graded"):
                row["graded_leak"] = sorted(facts_in(shown, data["graded_facts"]))
            if turn.get("check_explain_again"):
                system = model.calls[0]["system"] if model.calls else ""
                row["explain_again_sent"] = _EXPLAIN_AGAIN in system
            if turn.get("check_gap_keeps_level"):
                row["gap_kept_level"] = row["course_gap_message"] and row["level_after"] == row["level_before"]
            rows.append(row)
    return rows


def _explained_within(rows: list[dict], data: dict) -> list[dict]:
    """For each turn that asks about a concept: were its facts explained within N tutor turns?"""
    n = data["explain_within_turns"]
    out = []
    for i, row in enumerate(rows):
        if "concept" not in row:
            continue
        facts = data["concepts"][row["concept"]]
        window = [r["response"] for r in rows[i:] if r["conversation"] == row["conversation"]][:n]
        found = set().union(*(facts_in(t, facts) for t in window)) if window else set()
        out.append({
            "conversation": row["conversation"], "turn": row["turn"], "concept": row["concept"],
            "facts_found": sorted(found), "explained": len(found) >= 2,
        })
    return out


async def run(live: bool = False) -> SuiteResult:
    from core.config import settings
    from core.gemini_calls import background_work

    data = load_dataset(DATASET)
    fixture = data["fixture"]
    if live and settings.llm_provider != "gemini":
        return SuiteResult(
            name="novice-learner", criterion="Novice learner (session 5c6672fa), live", target="-",
            status="skipped",
            headline=f"Live mode needs LLM_PROVIDER=gemini (it is {settings.llm_provider!r}); nothing was run.",
        )
    live_ctx = _live_clients() if live else None
    counter = live_ctx["counter"] if live_ctx else {}
    rewriter = RecordedRewriter()
    started = time.perf_counter()
    rows: list[dict] = []
    with _mock_provider(), (background_work() if live else contextlib.nullcontext()):
        engine, factory, courses, student_id = await _conversation_db(data, data["conversations"])
        try:
            retrieval = _build_retrieval(fixture["passages"], fixture["source_file"], list(courses.values()))
            try:
                for conv in data["conversations"]:
                    if counter.get("error"):
                        break
                    conv_rows = await _run_conversation(
                        conv, data, factory, retrieval, courses[conv["id"]], student_id, live_ctx, rewriter
                    )
                    if not counter.get("error"):
                        rows += conv_rows
            except Exception:
                if not live:
                    raise
                counter["error"] = counter.get("error") or "stopped by an exception outside a model call"
        finally:
            await engine.dispose()

    explained = _explained_within(rows, data)
    on_topic = [r for r in rows if r["expect"] == "tutored"]
    off_topic = [r for r in rows if r["expect"] == "refused"]
    context = [r for r in rows if "context_kept" in r]
    examples = [r for r in rows if "example_given" in r]
    graded = [r for r in rows if "graded_leak" in r]
    step1 = {
        "no_false_refusals": not any(r["refused"] for r in on_topic),
        "off_topic_refused": all(r["refused"] for r in off_topic),
        "context_kept": all(r["context_kept"] for r in context),
        "no_graded_leak": not any(r["graded_leak"] for r in graded),
        "no_bare_course_gap_shown": not any(r["bare_gap_shown"] for r in rows),
        "no_glued_course_gap_shown": not any(r["glued_gap_shown"] for r in rows),
        "no_progress_claim_shown": not any(r["progress_claim_shown"] for r in rows),
        "full_explanation_gap_keeps_the_level": all(r["gap_kept_level"] for r in rows if "gap_kept_level" in r),
        "explanation_again_after_no": all(r["explain_again_sent"] for r in rows if "explain_again_sent" in r),
    }
    step2 = {
        "explained_within_n_turns": all(e["explained"] for e in explained),
        "example_when_asked": all(r["example_given"] for r in examples),
    }
    metrics = {
        "mode": "live" if live else "offline",
        "turns": len(rows),
        "checks": step1,
        "step2_checks": step2,
        "explained_within_n_turns": f"{sum(e['explained'] for e in explained)} of {len(explained)}",
        "explanations": explained,
        "false_refusals": [f"{r['conversation']}#{r['turn']}" for r in on_topic if r["refused"]],
        "off_topic_accepted": [f"{r['conversation']}#{r['turn']}" for r in off_topic if not r["refused"]],
        "context_kept": f"{sum(r['context_kept'] for r in context)} of {len(context)}",
        "example_when_asked": f"{sum(r['example_given'] for r in examples)} of {len(examples)}",
        "graded_leaks": sum(1 for r in graded if r["graded_leak"]),
        "course_gap_messages": sum(1 for r in rows if r["course_gap_message"]),
        "rewrites_used": sum(1 for r in rows if r["query_source"] == "rewrite"),
        "live_gemini": "measured" if live else LIVE_NOT_MEASURED,
    }
    if live:
        metrics.update({
            "live_calls": counter["calls"],
            "live_duration_s": round(time.perf_counter() - started, 1),
            "live_stopped_early": counter.get("error"),
        })
    met = all(step1.values()) and all(step2.values())
    failing2 = [k for k, ok in step2.items() if not ok]
    headline = (
        f"{'Live' if live else 'Offline'}: Step 1 checks {'all hold' if all(step1.values()) else 'FAILED'}"
        f" ({sum(step1.values())} of {len(step1)}); explained within {data['explain_within_turns']} turns: "
        f"{metrics['explained_within_n_turns']}; example when asked: {metrics['example_when_asked']}"
        + (f"; Step 2 gaps: {', '.join(failing2)}" if failing2 else "")
        + ("" if live else ". Live Gemini: not measured.")
    )
    notes = [
        (
            "Offline replays the live session's replies through the real service, graph, rewrite plumbing and "
            "guard; it measures those layers, not the model. Retrieval and the course check use the mock "
            "embedder and bar, so refusal numbers say nothing about Gemini's scale."
            if not live else
            "Live: Gemini wrote every reply and every follow-up rewrite; retrieval stayed on the mock embedder."
        ),
        (
            "Step 2 (Explain mode) is expected to close: an explanation within N turns, an example when "
            "asked, and 'no' at the low rungs."
        ),
    ]
    return SuiteResult(
        name="novice-learner",
        criterion=f"Novice learner (session 5c6672fa), {'live' if live else 'offline'}",
        target="every Step 1 check holds; Step 2 checks once Explain mode lands",
        status="pass" if met else "fail",
        headline=headline,
        metrics=metrics,
        rows=[{"record": "turn", **r} for r in rows] + [{"record": "explanation", **e} for e in explained],
        notes=notes,
    )
