"""
eval/suites/tutor_pedagogy.py
------------------------------
Tutor pedagogy and quiz quality: findings T1-T6 and G1 from live testing on
Gemini (a quick sort course), as recorded cases.

OFFLINE ONLY. The model's output is recorded in the dataset, not generated, so
this suite measures the deterministic layers around the model, not the model:

  - the tutor graph as the service runs it: query resolution, the groundedness
    gate, prompt building, and the output guard (agents/tutor/output_guard.py);
  - the tutoring service's open-question logic, over an in-memory SQLite
    database (tutoring/service.py);
  - the generated-question validator and the regenerate-once pipeline
    (agents/assessment/validation.py, agents/assessment/nodes.py).

Retrieval is real: an in-memory Chroma collection built from the dataset's
synthetic lab guide, with the mock provider's bag-of-words embeddings and the
mock groundedness bar. Refusal numbers are therefore about the mock embedder's
scale, not Gemini's.

What a live run (real Gemini writing the replies) would measure is NOT measured
here, and the report says so.

Metrics (levels 0-2 are the rungs that must not state the answer):

  leakage            a golden fact the student did not write appears in the reply
                     shown at levels 0-2
  on-topic refusal   an on-topic message (an attempt, "no", "I don't know", a
                     course question) answered with the out-of-course refusal
  repeated question  a question labeled as a repeat of the tutor's last
                     question is still in the reply shown
  fallback           a reply whose recorded output stated no golden fact, shown
                     as the generic fallback question anyway (false positives
                     are what make the tutor feel generic)
  definition         the one "what is X?" definition sentence, kept or removed,
                     against the expected outcome
  MC key support     the validator's verdict on generated questions against
                     labels, and the live G1 quiz through the real pipeline
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid

from eval.harness import SuiteResult, load_dataset

DATASET = "tutor_pedagogy.json"
#: Hint levels whose replies must not state the answer (MAX_ANSWER_GUARD_LEVEL).
_GUARDED_LEVELS = range(3)
LIVE_NOT_MEASURED = "not measured (offline run: recorded model outputs, no live Gemini call)"


# ── Offline stand-ins ─────────────────────────────────────────────────────────


class ScriptedModel:
    """Returns the recorded outputs in order and keeps every prompt it was sent."""

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def generate(self, prompt: str, temperature: float | None = None, system_instruction: str = "") -> str:
        self.calls.append({"prompt": prompt, "system": system_instruction})
        # A call with nothing recorded is a gate that should have refused.
        return self.outputs.pop(0) if self.outputs else "(no recorded output: unexpected model call)"


class _Span:
    trace_id = None

    def end(self) -> None:
        pass


class _NoTracing:
    def start_observation(self, **_kwargs) -> _Span:
        return _Span()


class _GuardRules(logging.Handler):
    """Collects the [TUTOR-GUARD] rule names logged while one reply is guarded."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.rules: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        m = re.match(r"\[TUTOR-GUARD\] rule=(\w+)", record.getMessage())
        if m:
            self.rules.append(m.group(1))


@contextlib.contextmanager
def _guard_rules():
    handler = _GuardRules()
    log = logging.getLogger("agents.tutor.output_guard")
    old_level, old_propagate = log.level, log.propagate
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False  # collected here, not printed by the runner
    try:
        yield handler.rules
    finally:
        log.removeHandler(handler)
        log.setLevel(old_level)
        log.propagate = old_propagate


@contextlib.contextmanager
def _mock_provider():
    """The mock groundedness bar for the duration of the run, whatever .env says."""
    from core.config import settings

    original = settings.llm_provider
    settings.llm_provider = "mock"
    try:
        yield
    finally:
        settings.llm_provider = original


def _build_retrieval(passages: list[str], source_file: str, course_ids: list[uuid.UUID]):
    import chromadb

    from core.mock_gemini import MockGeminiProClient
    from retrieval.models import Chunk
    from retrieval.service import RetrievalService

    service = RetrievalService(chroma_client=chromadb.EphemeralClient(), gemini_client=MockGeminiProClient())
    for course_id in course_ids:
        service.create_course_collection(course_id)
        service.add_chunks(
            course_id,
            [
                Chunk(text=t, source_file=source_file, page_or_slide=i + 1, course_id=course_id,
                      chunk_id=f"{course_id}-{i}")
                for i, t in enumerate(passages)
            ],
        )
    return service


# ── Scoring helpers ───────────────────────────────────────────────────────────


def facts_in(text: str | None, facts: list[dict]) -> set[str]:
    """Ids of the golden facts whose patterns appear in ``text``."""
    return {
        f["id"] for f in facts
        if any(re.search(p, text or "", re.IGNORECASE) for p in f["patterns"])
    }


def _norm(text: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def contains(haystack: str | None, needle: str | None) -> bool:
    """``needle`` appears in ``haystack``, ignoring case, punctuation and spacing."""
    return bool(needle) and _norm(needle) in _norm(haystack)


def is_fallback(text: str | None) -> bool:
    from agents.tutor.prompts import GUARD_FALLBACK_NON_ANSWER, GUARD_FALLBACK_QUESTION

    return GUARD_FALLBACK_QUESTION in (text or "") or GUARD_FALLBACK_NON_ANSWER in (text or "")


def _refused(final: dict) -> bool:
    return bool(final.get("pedagogy_trace", {}).get("refused")) or not final.get("is_grounded")


def _guard_passages(final: dict) -> str:
    """The passages the guard compared the reply with: those that cleared the bar (agents/tutor/nodes.py)."""
    from core.config import settings

    raw = final.get("retrieval_result") or {}
    pairs = list(zip(raw.get("chunks", []), raw.get("confidence_scores", []), strict=False))
    relevant = [c["text"] for c, s in pairs if s >= settings.active_groundedness_threshold]
    return "\n\n".join(relevant or [c["text"] for c, _ in pairs])


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


# ── 1. Single replies through the tutor graph ─────────────────────────────────


def _probe_state(probe: dict, data: dict) -> dict:
    question = data["questions"][probe["question"]]
    message = probe.get("message_text") or data["students"].get(probe.get("message", ""))
    is_reply = probe["mode"] == "chat" and message is not None
    if probe["mode"] == "hint":
        asked, prior, last = question, None, probe.get("previous")
    elif is_reply:
        asked, prior, last = message, question, probe.get("previous")
    else:
        asked, prior, last = question, None, None
    return {
        "session_id": uuid.uuid4(),
        "course_id": None,  # set by the caller
        "student_id": uuid.uuid4(),
        "question": asked,
        "prior_question": prior,
        "tutor_last_message": last,
        "resolved_question": None,
        "retrieval_query_source": "message",
        "pedagogy_skip_requested": False,
        "retrieval_result": None,
        "is_grounded": False,
        "hint_level": probe.get("hint_level", 0),
        "definition_allowed": bool(probe.get("definition_allowed")) and not is_reply,
        "response": "",
        "citations": [],
        "pedagogy_trace": {},
    }


async def _run_probe(probe: dict, data: dict, retrieval, course_id: uuid.UUID) -> dict:
    from agents.tutor.graph import build_tutor_graph

    state = {**_probe_state(probe, data), "course_id": course_id}
    model = ScriptedModel([probe["model_output"]])
    graph = build_tutor_graph(retrieval_service=retrieval, gemini_pro=model, langfuse=_NoTracing(),
                              mode=probe["mode"])
    with _guard_rules() as rules:
        final = await graph.ainvoke(state)

    facts = data["golden_facts"][probe["question"]]
    student_text = f"{state['question']} {state['prior_question'] or ''}"
    student_facts = facts_in(student_text, facts)
    shown = final.get("response", "")
    raw_leak = sorted(facts_in(probe["model_output"], facts) - student_facts)
    final_leak = sorted(facts_in(shown, facts) - student_facts)
    level = final.get("hint_level", state["hint_level"])
    definition = probe.get("definition")
    row = {
        "id": probe["id"],
        "case": probe["case"],
        "source": probe["source"],
        "mode": probe["mode"],
        "level_shown": level,
        "refused": _refused(final),
        "raw_leak": raw_leak,
        "leak": final_leak,
        # A leaked fact the guard could not have recognised: it is in no passage
        # that cleared the groundedness bar, which is all the guard is given.
        "leak_outside_guard_passages": sorted(set(final_leak) - facts_in(_guard_passages(final), facts)),
        "fallback": is_fallback(shown),
        "rules": rules,
        "response": shown,
        "known_gap": probe.get("known_gap"),
        "gate": bool(probe.get("gate")),
    }
    if probe.get("repeated_question"):
        row["repeat_survived"] = contains(shown, probe["repeated_question"])
    if definition:
        row["definition_expected"] = definition["expected"]
        row["definition_kept"] = contains(shown, definition["sentence"])
    row["gate_ok"] = (
        not row["refused"]
        and not row["leak"]
        and not row.get("repeat_survived", False)
    ) if row["gate"] else None
    return row


# ── 2. Conversations through the tutoring service ────────────────────────────


async def _conversation_db(data: dict, conversations: list[dict]):
    """An in-memory database with a teacher, a student and one ready course per conversation."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from db.models import Assessment, Base, Course, Question, User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    course_ids: dict[str, uuid.UUID] = {}
    async with factory() as db:
        # Synthetic accounts in a throwaway in-memory database; never a login.
        teacher = User(email="eval-teacher@example.invalid", hashed_password="-", role="teacher")
        student = User(email="eval-student@example.invalid", hashed_password="-", role="student")
        db.add_all([teacher, student])
        await db.flush()
        for conv in conversations:
            course = Course(owner_id=teacher.id, name=f"eval {conv['id']}", status="ready")
            db.add(course)
            await db.flush()
            course_ids[conv["id"]] = course.id
            if conv.get("published_quiz"):
                quiz = Assessment(course_id=course.id, created_by=teacher.id, title="Quiz", status="published")
                db.add(quiz)
                await db.flush()
                for i, stem in enumerate(data["fixture"]["published_quiz_stems"]):
                    db.add(Question(assessment_id=quiz.id, question_type="short_answer", stem=stem,
                                    max_points=1.0, order_index=i))
        await db.commit()
        student_id = student.id
    return engine, factory, course_ids, student_id


async def _run_conversation(conv: dict, data: dict, factory, retrieval, course_id, student_id) -> list[dict]:
    from tutoring.service import create_session, request_hint, send_chat_message

    rows: list[dict] = []
    async with factory() as db:
        session = await create_session(course_id, student_id, db)
        for n, turn in enumerate(conv["turns"], 1):
            model = ScriptedModel([turn["model_output"]] if "model_output" in turn else [])
            text = None
            with _guard_rules() as rules:
                if turn["kind"] == "hint":
                    try:
                        final = await request_hint(session, db, retrieval, model, _NoTracing())
                    except ValueError:
                        final = {"is_grounded": False, "response": ""}
                else:
                    key = turn["text"]
                    text = data["questions"].get(key) or data["students"].get(key) or key
                    final = await send_chat_message(session, text, db, retrieval, model, _NoTracing())
            shown = final.get("response", "")
            row = {
                "conversation": conv["id"],
                "case": conv["case"],
                "turn": n,
                "kind": turn["kind"],
                "message": text,
                "expect": turn["expect"],
                "refused": _refused(final),
                "model_called": bool(model.calls),
                "rules": rules,
                "response": shown,
                "known_gap": turn.get("known_gap"),
            }
            row["as_expected"] = row["refused"] == (turn["expect"] == "refused")
            system = " ".join(model.calls[0]["system"].split()) if model.calls else ""
            prompt = model.calls[0]["prompt"] if model.calls else ""
            if turn.get("check_reply_prompt"):
                row["reply_prompt_names_the_question"] = (
                    "say in a few words which question you are working on together" in system
                )
            if turn.get("check_hint_prompt"):
                row["hint_prompt_has_last_message"] = "<<UNTRUSTED YOUR LAST MESSAGE>>" in prompt
            if turn.get("repeated_question"):
                row["repeat_survived"] = contains(shown, turn["repeated_question"])
            if turn.get("definition"):
                row["definition_expected"] = turn["definition"]["expected"]
                row["definition_kept"] = contains(shown, turn["definition"]["sentence"])
            rows.append(row)
    return rows


# ── 3. Generated questions ────────────────────────────────────────────────────


class _RecordedFlash:
    """The recorded quiz batch, then one recorded regeneration per call."""

    def __init__(self, batch: dict, regenerations: list[dict]):
        self.batch = batch
        self.regenerations = list(regenerations)
        self.regenerate_calls = 0

    def generate(self, prompt: str, **_kwargs) -> str:
        if prompt.startswith("Improve the three incorrect MCQ options"):
            return "{}"  # leaves the options as recorded
        if prompt.startswith("Write ONE assessment question"):
            self.regenerate_calls += 1
            return json.dumps({"question": self.regenerations.pop(0)}) if self.regenerations else "{}"
        return json.dumps(self.batch)


def _generation(data: dict, retrieval) -> tuple[list[dict], dict]:
    from agents.assessment.nodes import generate_questions_node
    from agents.assessment.validation import (
        REASON_UNSUPPORTED_KEY,
        evidence_sentences,
        mcq_key_index,
        question_problems,
    )

    passages = data["fixture"]["passages"]
    evidence = evidence_sentences(passages)
    gen = data["generation"]
    rows = []
    for item in gen["items"]:
        q = item["question"]
        problems = question_problems(q, evidence)
        rows.append({
            "id": item["id"],
            "source": item["source"],
            "type": q["question_type"],
            "bloom": q["bloom_level"],
            "problems": problems,
            "expected": item["expected_problems"],
            "agrees": sorted(problems) == sorted(item["expected_problems"]),
            "known_gap": item.get("known_gap"),
            "key_supported": REASON_UNSUPPORTED_KEY not in problems,
        })

    # The live quiz through the real node: validate, regenerate once, drop.
    by_id = {i["id"]: i["question"] for i in gen["items"]}
    pipeline = gen["pipeline"]
    batch = {"questions": [dict(by_id[i], difficulty="easy", worked_solution="", rubric_criteria=[])
                           for i in pipeline["batch"]]}
    regens = [dict(q, difficulty="easy", worked_solution="", rubric_criteria=[]) for q in pipeline["regenerations"]]
    course_id = uuid.uuid4()
    state = {
        "course_id": course_id, "teacher_id": uuid.uuid4(),
        "config": {"topic": "quick sort", "difficulty": "mixed", "count": len(pipeline["batch"])},
        "retrieved_concepts": [],
        "retrieval_cache": {
            "query": "quick sort",
            "chunks": [{"text": t, "source_file": data["fixture"]["source_file"], "page_or_slide": i + 1,
                        "course_id": str(course_id), "chunk_id": ""} for i, t in enumerate(passages)],
            "confidence_scores": [0.9] * len(passages),
        },
        "is_grounded": True, "questions": [], "bloom_tags": [], "distractors": [], "rubric_criteria": [],
        "answer_key": [], "assessment_id": uuid.uuid4(), "trace_id": None, "status": "running", "error": None,
    }
    flash = _RecordedFlash(batch, regens)
    result = generate_questions_node(state, retrieval_service=retrieval, gemini_flash=flash)
    kept = []
    for q, key in zip(result.get("questions", []), result.get("answer_key", []), strict=False):
        full = {**q, "correct_answer": key["correct_answer"]}
        kept.append({
            "stem": q["stem"],
            "type": q["question_type"],
            "problems": question_problems(full, evidence),
            "key_supported": q["question_type"] != "mcq" or (
                mcq_key_index(full) is not None
                and REASON_UNSUPPORTED_KEY not in question_problems(full, evidence)
            ),
        })
    summary = {
        "status": result.get("status"),
        "requested": len(pipeline["batch"]),
        "kept": len(kept),
        "regeneration_calls": flash.regenerate_calls,
        "questions": kept,
    }
    return rows, summary


# ── The suite ─────────────────────────────────────────────────────────────────


async def run() -> SuiteResult:
    data = load_dataset(DATASET)
    fixture = data["fixture"]
    with _mock_provider():
        probe_course = uuid.uuid4()
        engine, factory, conv_courses, student_id = await _conversation_db(data, data["conversations"])
        try:
            retrieval = _build_retrieval(
                fixture["passages"], fixture["source_file"], [probe_course, *conv_courses.values()]
            )
            probes = [await _run_probe(p, data, retrieval, probe_course) for p in data["reply_probes"]]
            turns: list[dict] = []
            for conv in data["conversations"]:
                turns += await _run_conversation(
                    conv, data, factory, retrieval, conv_courses[conv["id"]], student_id
                )
            gen_rows, pipeline = _generation(data, retrieval)
        finally:
            await engine.dispose()

    return _result(probes, turns, gen_rows, pipeline)


def _result(probes: list[dict], turns: list[dict], gen_rows: list[dict], pipeline: dict) -> SuiteResult:
    guarded = [r for r in probes if not r["refused"] and r["level_shown"] in _GUARDED_LEVELS]
    leaks = [r for r in guarded if r["leak"]]
    raw_leaks = [r for r in guarded if r["raw_leak"]]
    non_leaking = [r for r in guarded if not r["raw_leak"]]
    fallback_on_non_leaking = [r for r in non_leaking if r["fallback"]]
    # A labeled repeat that falls back is the repeated-question rule working;
    # any other non-leaking reply that falls back is a false positive.
    fallback_false_positive = [r for r in fallback_on_non_leaking if "repeat_survived" not in r]

    repeat_rows = [r for r in probes + turns if "repeat_survived" in r]
    repeats = [r for r in repeat_rows if r["repeat_survived"]]

    on_topic = [t for t in turns if t["expect"] == "tutored"]
    off_topic = [t for t in turns if t["expect"] == "refused"]
    on_topic_refused = [t for t in on_topic if t["refused"]]
    off_topic_accepted = [t for t in off_topic if not t["refused"]]
    probes_refused = [r for r in probes if r["refused"]]

    definition_rows = [r for r in probes + turns if "definition_expected" in r]
    kept_as_expected = sum(1 for r in definition_rows if r["definition_expected"] == "kept" and r["definition_kept"])
    removed_as_expected = sum(
        1 for r in definition_rows if r["definition_expected"] == "removed" and not r["definition_kept"]
    )
    definition_mismatch = [
        r.get("id") or f"{r['conversation']}#{r['turn']}"
        for r in definition_rows
        if r["definition_kept"] != (r["definition_expected"] == "kept")
    ]

    mcq = [r for r in gen_rows if r["type"] == "mcq"]
    gates = [r for r in probes if r["gate"]]
    gates_failed = [r["id"] for r in gates if not r["gate_ok"]]
    checks = {
        "T5_reply_prompt_names_the_question": all(
            t["reply_prompt_names_the_question"] for t in turns if "reply_prompt_names_the_question" in t
        ),
        "T3_hint_prompt_has_last_message": all(
            t["hint_prompt_has_last_message"] for t in turns if "hint_prompt_has_last_message" in t
        ),
    }

    metrics = {
        "replies_at_levels_0_2": len(guarded),
        "leaks": len(leaks),
        "leakage_rate": _rate(len(leaks), len(guarded)),
        "recorded_outputs_that_leaked": len(raw_leaks),
        "leaks_removed_by_the_guard": len(raw_leaks) - len(leaks),
        "leaks_outside_guard_passages": sum(1 for r in leaks if r["leak_outside_guard_passages"]),
        "on_topic_turns": len(on_topic),
        "on_topic_refused": len(on_topic_refused),
        "on_topic_refusal_rate": _rate(len(on_topic_refused), len(on_topic)),
        "off_topic_turns": len(off_topic),
        "off_topic_accepted": len(off_topic_accepted),
        "single_reply_probes_refused": len(probes_refused),
        "labeled_repeats": len(repeat_rows),
        "repeats_shown": len(repeats),
        "repeated_question_rate": _rate(len(repeats), len(repeat_rows)),
        "non_leaking_replies": len(non_leaking),
        "fallback_on_non_leaking": len(fallback_on_non_leaking),
        "fallback_rate_on_non_leaking": _rate(len(fallback_on_non_leaking), len(non_leaking)),
        "fallback_after_a_labeled_repeat": len(fallback_on_non_leaking) - len(fallback_false_positive),
        "fallback_false_positives": [r["id"] for r in fallback_false_positive],
        "definition_cases": len(definition_rows),
        "definition_kept_as_expected": kept_as_expected,
        "definition_removed_as_expected": removed_as_expected,
        "definition_mismatches": definition_mismatch,
        "mcq_items": len(mcq),
        "mcq_keys_supported": sum(1 for r in mcq if r["key_supported"]),
        "validator_agrees_with_labels": sum(1 for r in gen_rows if r["agrees"]),
        "validator_items": len(gen_rows),
        "live_quiz_pipeline": {k: v for k, v in pipeline.items() if k != "questions"},
        "live_quiz_kept_mcq_keys_supported": sum(1 for q in pipeline["questions"] if q["key_supported"]),
        "gated_cases": len(gates),
        "gated_cases_failed": gates_failed,
        "checks": checks,
        "live_gemini": LIVE_NOT_MEASURED,
        # Whatever LLM_PROVIDER says in .env (and in the report header), this run
        # used the mock provider's embeddings and bar, and recorded model outputs.
        "provider_used": "mock (offline)",
    }

    met = (
        not leaks and not repeats and not on_topic_refused and not off_topic_accepted
        and not definition_mismatch and not gates_failed and all(checks.values())
    )
    headline = (
        f"Offline: {len(leaks)} of {len(guarded)} replies at levels 0-2 leak; "
        f"{len(on_topic_refused)} of {len(on_topic)} on-topic turns refused; "
        f"{len(repeats)} of {len(repeat_rows)} labeled repeats shown; "
        f"{len(fallback_on_non_leaking)} of {len(non_leaking)} non-leaking replies fell back "
        f"({len(fallback_false_positive)} not after a repeat). "
        "Live Gemini: not measured."
    )
    def shows_gap(r: dict) -> bool:
        return bool(
            r.get("leak") or r.get("repeat_survived") or r.get("agrees") is False
            or ("definition_expected" in r and r["definition_kept"] != (r["definition_expected"] == "kept"))
        )

    gaps = sorted({r["known_gap"] for r in probes + turns + gen_rows if r.get("known_gap") and shows_gap(r)})
    notes = [
        (
            "Offline mode replays recorded model outputs through the real graph, service, guard and "
            "validator. It measures those deterministic layers, not the model. Live (Gemini) numbers: "
            "not measured."
        ),
        (
            "Retrieval and the groundedness gate are real but use the mock provider's bag-of-words "
            "embeddings and the mock bar, so refusal numbers say nothing about Gemini's scale."
        ),
        "A golden fact counts as leaked only when the student did not write it themselves.",
        *[f"Known gap: {g}" for g in gaps],
    ]
    return SuiteResult(
        name="tutor-pedagogy",
        criterion="Tutor pedagogy and quiz quality (T1-T6, G1), offline",
        target="0 leaks at levels 0-2, 0 on-topic refusals, 0 repeated questions",
        status="pass" if met else "fail",
        headline=headline,
        metrics=metrics,
        rows=[*({"record": "reply", **r} for r in probes), *({"record": "turn", **t} for t in turns),
              *({"record": "question", **g} for g in gen_rows),
              *({"record": "live_quiz_kept", **q} for q in pipeline["questions"])],
        notes=notes,
    )
