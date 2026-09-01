"""
agents/assessment/nodes.py
---------------------------
LangGraph node functions for the Assessment Generation Agent.

Execution order (enforced by graph.py):
    retrieve_concepts → check_groundedness
                              |
              ┌───────────────┴───────────────┐
           grounded                        not grounded
              │                                │
      generate_questions                  refuse_node → END
              │
    ┌─────────┴─────────┐
  ok (questions > 0)  failed / empty
        │                    │
  persist_assessment         END
        │
       END

Design principles:
  - Every node receives injected services via functools.partial (see graph.py).
  - Retrieval groundedness gate is mandatory — no LLM call is made unless grounded.
  - Gemini Flash is used for generation (fast + cost-efficient for bulk questions).
  - Langfuse spans emitted at key stages for observability.
  - JSON parse errors → status="failed", graph routes to END without DB write.
  - ChromaDB errors → status="failed", graph routes to END.
  - persist_assessment_node UPDATES the existing placeholder row (created by
    service.create_assessment) — it never creates a new Assessment INSERT.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from agents.assessment.prompts import (
    ASSESSMENT_SYSTEM_INSTRUCTION,
    GENERATE_QUESTIONS_PROMPT,
    IMPROVE_DISTRACTORS_PROMPT,
)
from agents.assessment.state import AssessmentState
from core.context_phrasing import strip_context_references
from core.prompt_safety import UNTRUSTED_CONTENT_NOTICE, wrap_untrusted
from progress.tagging import load_course_concepts, tag_question_at_generation

if TYPE_CHECKING:
    from retrieval.service import RetrievalService
    from langfuse import Langfuse

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_VALID_BLOOM = frozenset({"remember", "understand", "apply", "analyze", "evaluate", "create"})
_VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})

_REFUSAL_MESSAGE = (
    "Cannot generate assessment: the requested topic was not found in the "
    "uploaded course material. Please choose a topic covered in the course."
)

_DEFAULT_BLOOM_MIX: dict[str, int] = {
    "remember": 2,
    "understand": 3,
    "apply": 2,
    "analyze": 2,
    "evaluate": 1,
}

_DEFAULT_TYPE_MIX: dict[str, int] = {
    "mcq": 6,
    "short_answer": 3,
    "numeric": 1,
}

# Maximum MCQs to run through the distractor improvement pass.
# Each pass = one additional LLM call, so cap to control latency.
_MAX_DISTRACTOR_IMPROVEMENTS = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

#: Leading "A. " / "B) " / "(C) " / "D - " that the model bakes into option text.
#: The UI renders the letter from the option's POSITION, so a prefix here is
#: shown twice: live evidence was an option reading "A. A. Two billion years ago".
_OPTION_PREFIX_RE = re.compile(r"^\s*\(?\s*([A-Da-d])\s*[.):\-]\s+")


def _strip_option_prefix(option: str) -> str:
    """
    Remove a leading letter label from one option.

    Defensive: the prompts now ask for plain text, but prompt-following is
    exactly what failed here, so the stored value is normalised regardless.
    Only a single leading label is removed, and only A-D — an option that
    legitimately starts with something like "B cells produce antibodies" keeps
    its text because the pattern requires a delimiter after the letter.
    """
    if not isinstance(option, str):
        return option
    return _OPTION_PREFIX_RE.sub("", option, count=1).strip()


def _normalise_answer(text: str) -> str:
    """Loose form for comparing an option against another question's answer."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


#: An answer shorter than this is too generic to attribute to one question
#: ("4", "yes"), so matching on it would produce false positives.
_MIN_LEAK_LENGTH = 4


def _other_question_answers(question_list: list[dict], skip_idx: int) -> set[str]:
    """
    Normalised correct answers belonging to every OTHER question in the batch.

    MCQ answers are excluded: they are bare letters ("B"), which carry no text
    to collide with.
    """
    out: set[str] = set()
    for i, other in enumerate(question_list):
        if i == skip_idx or other.get("question_type") == "mcq":
            continue
        norm = _normalise_answer(str(other.get("correct_answer", "")))
        if len(norm) >= _MIN_LEAK_LENGTH:
            out.add(norm)
    return out


def _leaking_options(options: list[str], foreign_answers: set[str]) -> list[str]:
    """
    Options that reproduce another question's answer.

    This is the distractor-pool contamination bug, and it is a different defect
    from weak plausibility: an option can be perfectly plausible prose and still
    be the wrong CATEGORY of thing. Live evidence, twice in two runs, on
    "When did humans emerge in Africa?":

        run 1: option D = "5.972168x10^24 kg"   <- that batch's numeric answer
        run 2: option D = "510072000 km2"       <- that batch's numeric answer

    A mass and an area are not wrong answers to a "when" question; they are not
    answers at all. Matching is containment-based because the model often welds
    the foreign answer into a longer string ("148940000 km2 emerged 300,000
    years ago in Africa and have spread").
    """
    hits = []
    for option in options or []:
        norm = _normalise_answer(option)
        if not norm:
            continue
        if any(f in norm or norm in f for f in foreign_answers):
            hits.append(option)
    return hits


def _strip_json_fences(raw: str) -> str:
    """
    Remove markdown code fences that Gemini sometimes wraps around JSON output.
    Handles both ```json and ``` prefixes.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        # Drop the first line (``` or ```json)
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    return raw.strip()


def _normalize_bloom(level: str) -> str:
    """Lowercase and validate a Bloom level; fall back to 'understand'."""
    normalized = level.lower().strip() if level else "understand"
    return normalized if normalized in _VALID_BLOOM else "understand"


def _normalize_difficulty(diff: str) -> str:
    """Lowercase and validate difficulty; fall back to 'medium'."""
    normalized = diff.lower().strip() if diff else "medium"
    return normalized if normalized in _VALID_DIFFICULTIES else "medium"


def _build_type_distribution(config: dict) -> str:
    """Format the question type distribution block for the prompt."""
    raw: dict[str, int] = config.get("type_mix") or _DEFAULT_TYPE_MIX
    # Normalize keys to prevent prompt injection via type names
    allowed = {"mcq", "short_answer", "numeric"}
    lines = [
        f"  - {qtype}: {cnt} question(s)"
        for qtype, cnt in raw.items()
        if qtype in allowed and cnt > 0
    ]
    return "\n".join(lines) if lines else "  - mcq: 6, short_answer: 3, numeric: 1"


def _build_bloom_distribution(config: dict) -> str:
    """Format the Bloom taxonomy distribution block for the prompt."""
    raw: dict[str, int] = config.get("bloom_mix") or _DEFAULT_BLOOM_MIX
    lines = [
        f"  - {level}: {cnt} question(s)"
        for level, cnt in raw.items()
        if level in _VALID_BLOOM and cnt > 0
    ]
    return "\n".join(lines) if lines else "  - mixed Bloom levels"


def _failed_state(state: AssessmentState, error: str) -> AssessmentState:
    """Return a state update that marks the graph run as failed."""
    return {
        **state,
        "status": "failed",
        "error": error,
        "questions": [],
        "bloom_tags": [],
        "distractors": [],
        "rubric_criteria": [],
        "answer_key": [],
    }


# ── Node 1: retrieve_concepts_node ────────────────────────────────────────────

def retrieve_concepts_node(
    state: AssessmentState,
    retrieval_service: "RetrievalService",
) -> AssessmentState:
    """
    Retrieve semantically relevant course chunks for the requested topic.

    Failures (ChromaDB unavailable, missing collection) produce status="failed"
    and route the graph to END — no LLM call is made.
    """
    config = state["config"]
    topic: str = config.get("topic", "")
    course_id = state["course_id"]
    assessment_id = state.get("assessment_id", "unknown")

    logger.info(
        "[RETRIEVAL-START] assessment_id=%s course_id=%s topic=%r",
        assessment_id,
        course_id,
        topic,
    )

    t0 = time.perf_counter()
    try:
        result = retrieval_service.retrieve(course_id, topic, top_k=8)
    except Exception as exc:
        logger.exception(
            "ChromaDB retrieval failed for course_id=%s topic=%r: %s",
            course_id,
            topic,
            exc,
        )
        logger.error(
            "[RETRIEVAL-ERROR] assessment_id=%s course_id=%s topic=%r exception=%s",
            assessment_id,
            course_id,
            topic,
            exc,
        )
        return _failed_state(state, f"Retrieval failed: {exc}")

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    chunk_count = len(result.chunks)
    top_score = result.top_score if not result.is_empty() else 0.0

    logger.info(
        "Retrieved %d chunks for course_id=%s topic=%r in %dms (top_score=%.3f)",
        chunk_count,
        course_id,
        topic,
        elapsed_ms,
        top_score,
    )

    # DIAGNOSTIC LOGGING: Print all retrieved chunks with scores
    logger.info(
        "[RETRIEVAL-RESULTS] assessment_id=%s course_id=%s topic=%r chunk_count=%d",
        assessment_id,
        course_id,
        topic,
        chunk_count,
    )
    for idx, (chunk, score) in enumerate(zip(result.chunks, result.confidence_scores), 1):
        logger.info(
            "[RETRIEVAL-CHUNK-%d] rank=%d confidence=%.4f source=%s page=%s text_preview=%r",
            idx,
            idx,
            score,
            chunk.source_file,
            chunk.page_or_slide,
            chunk.text[:150].replace("\n", " "),
        )

    # Serialise RetrievalResult for downstream nodes.
    # Chunk.course_id (UUID) is stored as str to survive JSON-like dict pass-through.
    return {
        **state,
        "retrieved_concepts": [
            {
                "text": c.text,
                "source_file": c.source_file,
                "page_or_slide": c.page_or_slide,
                "confidence": round(score, 4),
            }
            for c, score in zip(result.chunks, result.confidence_scores)
        ],
        "retrieval_cache": {
            "query": result.query,
            "chunks": [
                {
                    "text": c.text,
                    "source_file": c.source_file,
                    "page_or_slide": c.page_or_slide,
                    "course_id": str(c.course_id),   # UUID → str for dict storage
                    "chunk_id": c.chunk_id,
                }
                for c in result.chunks
            ],
            "confidence_scores": result.confidence_scores,
            "retrieval_ms": elapsed_ms,
        },
        "status": "running",
    }


# ── Node 2: check_groundedness_node ──────────────────────────────────────────

def check_groundedness_node(
    state: AssessmentState,
    retrieval_service: "RetrievalService",
) -> AssessmentState:
    """
    Hard gate: refuse generation if the topic is not grounded in course material.

    Reconstructs a RetrievalResult from the serialised retrieval_cache,
    correctly converting course_id strings back to UUID objects.
    """
    from retrieval.models import Chunk, RetrievalResult

    course_id = state["course_id"]
    assessment_id = state.get("assessment_id", "unknown")
    topic = state["config"].get("topic", "unknown")

    cache = state.get("retrieval_cache") or {}
    raw_chunks = cache.get("chunks", [])
    confidence_scores = cache.get("confidence_scores", [])

    logger.info(
        "[GROUNDEDNESS-START] assessment_id=%s course_id=%s topic=%r cache_chunks=%d",
        assessment_id,
        course_id,
        topic,
        len(raw_chunks),
    )

    chunks = [
        Chunk(
            text=c["text"],
            source_file=c["source_file"],
            page_or_slide=c.get("page_or_slide"),
            course_id=uuid.UUID(c["course_id"]),   # str → UUID (fixes N2)
            chunk_id=c.get("chunk_id", ""),
        )
        for c in raw_chunks
    ]
    result = RetrievalResult(
        query=cache.get("query", ""),
        chunks=chunks,
        confidence_scores=confidence_scores,
    )

    from core.config import settings

    # Each embedding backend has its own similarity scale, so the numeric bar is
    # provider-specific: the mock is a lexical bag-of-words stub that tops out far
    # below a trained dense model, and Ollama's nomic-embed-text distribution is
    # not assumed to match Gemini's (see settings.groundedness_threshold_ollama —
    # still an untuned placeholder). The gate itself runs unconditionally for every
    # provider — only the numeric bar differs.
    threshold = settings.active_groundedness_threshold
    threshold_source = settings.llm_provider

    top_score = result.top_score if not result.is_empty() else 0.0
    grounded = retrieval_service.is_grounded(result, threshold=threshold)

    logger.info(
        "[GROUNDEDNESS-DECISION] assessment_id=%s course_id=%s topic=%r "
        "top_score=%.4f threshold=%.2f threshold_source=%s grounded=%s",
        assessment_id,
        course_id,
        topic,
        top_score,
        threshold,
        threshold_source,
        grounded,
    )

    # Detailed confidence breakdown
    if confidence_scores:
        logger.info(
            "[GROUNDEDNESS-SCORES] assessment_id=%s top5_confidences=%s",
            assessment_id,
            [f"{s:.4f}" for s in confidence_scores[:5]],
        )

    logger.info(
        "Groundedness check: grounded=%s top_score=%.3f course_id=%s",
        grounded,
        result.top_score,
        course_id,
    )

    return {**state, "is_grounded": grounded}


# ── Node 3a: refuse_node ──────────────────────────────────────────────────────

def refuse_node(state: AssessmentState) -> AssessmentState:
    """
    Topic not grounded — return canned refusal without calling the LLM.
    Routes to END via graph edge.
    """
    logger.warning(
        "Assessment generation refused (not grounded) for course_id=%s topic=%r",
        state["course_id"],
        state["config"].get("topic", ""),
    )
    return _failed_state(state, _REFUSAL_MESSAGE)


# ── Node 3b: generate_questions_node ─────────────────────────────────────────

def generate_questions_node(
    state: AssessmentState,
    retrieval_service: "RetrievalService",
    gemini_flash,
) -> AssessmentState:
    """
    Generate grounded assessment questions using Gemini Flash.

    Steps:
      1. Rebuild RetrievalResult from cache.
      2. Build context window string (respects token budget).
      3. Fill and send GENERATE_QUESTIONS_PROMPT.
      4. Parse JSON response; fail fast on parse error.
      5. Run optional distractor improvement pass (capped at _MAX_DISTRACTOR_IMPROVEMENTS).
      6. Normalise Bloom levels and difficulties.
      7. Extract parallel state arrays.

    Note: gemini_flash.generate() is synchronous (google-generativeai SDK).
    This is acceptable here because this node runs inside a BackgroundTask
    — it does not block the FastAPI event loop.
    """
    from retrieval.models import Chunk, RetrievalResult

    config = state["config"]

    # ── Rebuild RetrievalResult ───────────────────────────────────────────────
    cache = state.get("retrieval_cache") or {}
    raw_chunks = cache.get("chunks", [])
    chunks = [
        Chunk(
            text=c["text"],
            source_file=c["source_file"],
            page_or_slide=c.get("page_or_slide"),
            course_id=uuid.UUID(c["course_id"]),
            chunk_id=c.get("chunk_id", ""),
        )
        for c in raw_chunks
    ]
    result = RetrievalResult(
        query=cache.get("query", ""),
        chunks=chunks,
        confidence_scores=cache.get("confidence_scores", []),
    )

    context = retrieval_service.build_context_window(result, token_budget=4000)

    if not context.strip():
        return _failed_state(state, "No course content available to generate questions from.")

    # ── Build and send prompt ─────────────────────────────────────────────────
    # F23: delimit the uploaded course text so any instructions embedded in it
    # cannot override the question-generation task.
    prompt = GENERATE_QUESTIONS_PROMPT.format(
        context=wrap_untrusted(context, "COURSE CONTEXT"),
        topic=config.get("topic", ""),
        difficulty=config.get("difficulty", "mixed"),
        count=config.get("count", 10),
        type_distribution=_build_type_distribution(config),
        bloom_distribution=_build_bloom_distribution(config),
    )

    t0 = time.perf_counter()
    try:
        raw_response = gemini_flash.generate(
            prompt,
            temperature=0.4,
            system_instruction=ASSESSMENT_SYSTEM_INSTRUCTION + "\n\n" + UNTRUSTED_CONTENT_NOTICE,
        )
    except Exception as exc:
        logger.exception("Gemini Flash generation failed: %s", exc)
        return _failed_state(state, f"LLM generation failed: {exc}")

    gen_ms = round((time.perf_counter() - t0) * 1000)
    logger.info("Gemini Flash generation completed in %dms", gen_ms)

    # ── Parse JSON ────────────────────────────────────────────────────────────
    cleaned = _strip_json_fences(raw_response)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(
            "JSON parse error in Gemini response (first 500 chars): %r — %s",
            cleaned[:500],
            exc,
        )
        return _failed_state(state, f"LLM returned malformed JSON: {exc}")

    question_list: list[dict] = parsed.get("questions", [])
    if not question_list:
        return _failed_state(state, "LLM returned an empty question list.")

    # ── Optional distractor improvement pass (capped) ─────────────────────────
    mcq_improved = 0
    for q in question_list:
        if (
            q.get("question_type") == "mcq"
            and q.get("options")
            and mcq_improved < _MAX_DISTRACTOR_IMPROVEMENTS
        ):
            try:
                improve_prompt = IMPROVE_DISTRACTORS_PROMPT.format(
                    stem=q["stem"],
                    correct_answer=q.get("correct_answer", ""),
                    options="\n".join(q["options"]),
                    context=context[:2000],
                )
                improved_raw = gemini_flash.generate(improve_prompt, temperature=0.3)
                improved_raw = _strip_json_fences(improved_raw)
                improved = json.loads(improved_raw)
                candidate = improved.get("improved_options")
                if isinstance(candidate, list) and len(candidate) == 4:
                    # Refuse an "improvement" that pulls in another question's
                    # answer — that is a regression, not an improvement.
                    foreign = _other_question_answers(question_list, question_list.index(q))
                    leaked = _leaking_options(candidate, foreign)
                    if leaked:
                        logger.warning(
                            "Distractor improvement REJECTED for %r: option(s) %r "
                            "reproduce another question's answer in this batch.",
                            q.get("stem", "")[:60],
                            [x[:40] for x in leaked],
                        )
                    else:
                        q["options"] = candidate
                        mcq_improved += 1
            except Exception:
                # Non-fatal — keep original options on any failure
                pass

    # ── Normalise and extract state arrays ────────────────────────────────────
    questions_out: list[dict] = []
    bloom_tags: list[str] = []
    distractors: list[list[str]] = []
    option_prefixes_stripped = 0
    context_phrases_stripped = 0
    rubric_criteria: list[list[dict]] = []
    answer_key: list[dict] = []

    for idx, q in enumerate(question_list):
        bloom = _normalize_bloom(q.get("bloom_level", ""))
        diff = _normalize_difficulty(q.get("difficulty", ""))
        q_type = q.get("question_type", "short_answer")
        rubric = q.get("rubric_criteria") or []
        # For MCQ/numeric: ignore any LLM-generated rubric (they need no criterion rows)
        if q_type != "short_answer":
            rubric = []

        points = float(sum(
            c.get("max_points", 1.0) for c in (rubric or [{"max_points": 1.0}])
        ))

        # Strip the prompt-block framing the model leaks into stems
        # ("...according to the context?"). The student never saw a context
        # block; see core/context_phrasing.py.
        raw_stem = q.get("stem", "").strip()
        stem = strip_context_references(raw_stem)
        if stem != raw_stem:
            context_phrases_stripped += 1
            logger.info(
                "Stripped context framing from stem: %r -> %r", raw_stem[:70], stem[:70]
            )

        raw_options = q.get("options")
        options = (
            [_strip_option_prefix(o) for o in raw_options]
            if isinstance(raw_options, list)
            else raw_options
        )
        if isinstance(raw_options, list) and options != raw_options:
            option_prefixes_stripped += 1

        questions_out.append({
            "order_index": idx,
            "question_type": q_type,
            "stem": stem,
            "options": options,
            "bloom_level": bloom,
            "difficulty": diff,
            "max_points": points,
        })
        bloom_tags.append(bloom)
        distractors.append(options or [])
        rubric_criteria.append(rubric)
        answer_key.append({
            "order_index": idx,
            "correct_answer": q.get("correct_answer", ""),
            "worked_solution": q.get("worked_solution", ""),
        })

    # ── Audit: contamination that survived from the original generation ───────
    # The improvement pass can only refuse to ADD leakage; if the first
    # generation already produced it, it is still here. Logged loudly rather
    # than silently shipped — there is no safe automatic repair (dropping an
    # option would leave an MCQ with three).
    contaminated = 0
    for idx, q in enumerate(question_list):
        if q.get("question_type") != "mcq":
            continue
        leaked = _leaking_options(
            questions_out[idx].get("options") or [],
            _other_question_answers(question_list, idx),
        )
        if leaked:
            contaminated += 1
            logger.warning(
                "[DISTRACTOR-CONTAMINATION] question %r carries option(s) %r that "
                "answer a DIFFERENT question in this batch. Teacher review needed.",
                questions_out[idx]["stem"][:60],
                [x[:40] for x in leaked],
            )

    logger.info(
        "Post-processing: stripped %d option letter prefix(es), %d context "
        "phrase(s) from stems; %d question(s) still carry cross-question options.",
        option_prefixes_stripped,
        context_phrases_stripped,
        contaminated,
    )

    logger.info(
        "Generated %d questions (gen_ms=%d, improved_distractors=%d) for course_id=%s",
        len(questions_out),
        gen_ms,
        mcq_improved,
        state["course_id"],
    )

    return {
        **state,
        "questions": questions_out,
        "bloom_tags": bloom_tags,
        "distractors": distractors,
        "rubric_criteria": rubric_criteria,
        "answer_key": answer_key,
        "status": "running",
    }


# ── Node 4: persist_assessment_node ──────────────────────────────────────────

async def persist_assessment_node(
    state: AssessmentState,
    db,
    langfuse: "Langfuse",
) -> AssessmentState:
    """
    UPDATE the placeholder Assessment row and INSERT Questions + RubricCriteria.

    IMPORTANT: This node does NOT create a new Assessment row.
    The placeholder was created by service.create_assessment() with
    status="generating". This node:
      1. Inserts Question and RubricCriterion rows in a single batch.
      2. Updates the Assessment status to "draft".
      3. Commits atomically — either all rows land or none do.
      4. Emits a Langfuse trace span (non-fatal on failure).

    "draft" status means: generation complete, teacher must review before publishing.
    """
    from db.models import Assessment, Question, RubricCriterion

    config = state["config"]
    assessment_id: uuid.UUID = state["assessment_id"]

    # ── Fetch placeholder row ─────────────────────────────────────────────────
    result = await db.execute(
        select(Assessment).where(Assessment.id == assessment_id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        logger.error(
            "persist_assessment_node: placeholder not found for id=%s", assessment_id
        )
        return {**state, "status": "failed", "error": "Placeholder assessment not found."}

    # ── Concept tagging (enrichment; never fatal) ─────────────────────────────
    # Questions carry a nullable concept_id so per-concept mastery can be
    # derived from graded work later (progress/). This is the durable tagging
    # path: everything generated from here on gets tagged at write time.
    #
    # Wrapped so it cannot break generation. If the lookup or the match fails
    # for any reason, every question is written with concept_id=None, which is a
    # valid state — such a question still counts toward course-level progress,
    # it just claims no concept mastery.
    concept_candidates: list = []
    try:
        concept_candidates = await load_course_concepts(state["course_id"], db)
    except Exception as exc:  # noqa: BLE001 - tagging must never fail generation
        logger.warning(
            "Concept lookup failed for course %s; questions will be untagged: %s",
            state["course_id"],
            exc,
        )

    topic = config.get("topic", "") or ""

    def _concept_for(stem: str) -> uuid.UUID | None:
        if not concept_candidates:
            return None
        try:
            return tag_question_at_generation(stem, topic, concept_candidates)
        except Exception as exc:  # noqa: BLE001 - same contract as above
            logger.warning("Concept tagging failed for a question: %s", exc)
            return None

    # ── Batch-create Question + RubricCriterion rows ──────────────────────────
    question_orm_list = []
    tagged_count = 0
    for idx, (q_data, rubric, ans) in enumerate(
        zip(state["questions"], state["rubric_criteria"], state["answer_key"])
    ):
        concept_id = _concept_for(q_data["stem"])
        if concept_id is not None:
            tagged_count += 1
        question = Question(
            assessment_id=assessment_id,
            question_type=q_data["question_type"],
            stem=q_data["stem"],
            options=json.dumps(q_data["options"]) if q_data.get("options") else None,
            answer_key=json.dumps(ans),
            bloom_level=q_data.get("bloom_level"),
            difficulty=q_data.get("difficulty"),
            max_points=q_data["max_points"],
            order_index=idx,
            concept_id=concept_id,
        )
        db.add(question)
        question_orm_list.append((question, rubric))

    logger.info(
        "persist_assessment_node: tagged %d/%d questions to concepts "
        "(%d concept candidates for course %s).",
        tagged_count,
        len(question_orm_list),
        len(concept_candidates),
        state["course_id"],
    )

    # Single flush to obtain all question IDs in one round-trip
    await db.flush()

    for question, rubric in question_orm_list:
        for crit_idx, crit in enumerate(rubric):
            criterion = RubricCriterion(
                question_id=question.id,
                description=crit.get("description", ""),
                max_points=float(crit.get("max_points", 1.0)),
                order_index=crit_idx,
            )
            db.add(criterion)

    # ── Update placeholder status to "draft", clear any prior error ──────────
    assessment.status = "draft"
    assessment.generation_error = None  # Clear any error from a previous failed attempt

    # ── Commit atomically (all questions + status update in one transaction) ───
    await db.commit()

    # ── Langfuse trace (non-fatal) ────────────────────────────────────────────
    trace_id: str | None = None
    try:
        # langfuse v4 removed the v2 `.trace()` method; `start_observation()` is
        # its replacement. The span must be ended explicitly or the OTel exporter
        # never ships it, and the trace-level id is `.trace_id` (`.id` is the
        # span id). Same shape as the tutor's emit_pedagogy_trace_node.
        span = langfuse.start_observation(
            name="assessment_generation",
            input={
                "course_id": str(state["course_id"]),
                "topic": config.get("topic", ""),
                "count": config.get("count", 0),
                "difficulty": config.get("difficulty", "mixed"),
            },
            output={
                "assessment_id": str(assessment_id),
                "questions_generated": len(state["questions"]),
                "bloom_distribution": dict(zip(
                    state["bloom_tags"],
                    [1] * len(state["bloom_tags"]),
                )),
            },
            metadata={
                "status": "draft",
                "retrieval_ms": state.get("retrieval_cache", {}).get("retrieval_ms"),
            },
        )
        span.end()
        trace_id = span.trace_id
    except Exception:  # noqa: BLE001 - tracing must never fail generation
        # Loud on purpose. This call sat broken on the removed v2 API for an
        # entire major version because a bare warning hid it; observability
        # coverage is itself an acceptance criterion, so a dead trace is a
        # defect and must look like one in the logs.
        logger.exception(
            "Langfuse trace FAILED for assessment %s - the assessment itself "
            "was persisted successfully, but this run is missing from tracing.",
            assessment_id,
        )

    logger.info(
        "Assessment %s persisted: %d questions, status=draft, trace_id=%s",
        assessment_id,
        len(state["questions"]),
        trace_id,
    )

    return {
        **state,
        "assessment_id": assessment_id,
        "status": "complete",
        "trace_id": trace_id,
    }
