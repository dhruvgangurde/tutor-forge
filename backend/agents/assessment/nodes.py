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
from core.prompt_safety import UNTRUSTED_CONTENT_NOTICE, wrap_untrusted

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
                if isinstance(improved.get("improved_options"), list) and len(improved["improved_options"]) == 4:
                    q["options"] = improved["improved_options"]
                    mcq_improved += 1
            except Exception:
                # Non-fatal — keep original options on any failure
                pass

    # ── Normalise and extract state arrays ────────────────────────────────────
    questions_out: list[dict] = []
    bloom_tags: list[str] = []
    distractors: list[list[str]] = []
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

        questions_out.append({
            "order_index": idx,
            "question_type": q_type,
            "stem": q.get("stem", "").strip(),
            "options": q.get("options"),
            "bloom_level": bloom,
            "difficulty": diff,
            "max_points": points,
        })
        bloom_tags.append(bloom)
        distractors.append(q.get("options") or [])
        rubric_criteria.append(rubric)
        answer_key.append({
            "order_index": idx,
            "correct_answer": q.get("correct_answer", ""),
            "worked_solution": q.get("worked_solution", ""),
        })

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

    # ── Batch-create Question + RubricCriterion rows ──────────────────────────
    question_orm_list = []
    for idx, (q_data, rubric, ans) in enumerate(
        zip(state["questions"], state["rubric_criteria"], state["answer_key"])
    ):
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
        )
        db.add(question)
        question_orm_list.append((question, rubric))

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
        trace = langfuse.trace(
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
        trace_id = trace.id
    except Exception as exc:
        logger.warning("Langfuse trace failed (non-fatal): %s", exc)

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
