"""
agents/grading/nodes.py
------------------------
LangGraph node implementations for the Grading Agent.

Node execution order (set by graph.py):
    load_submission_node
        ↓
    retrieve_evidence_node
        ↓
    grade_responses_node
        ↓
    persist_recommendation_node

Grading strategy by question type
----------------------------------
  MCQ:          Exact string match against answer_key["correct_answer"].
                Full points if correct, zero if incorrect. No LLM call.
  Numeric:      Float comparison with ±NUMERIC_TOLERANCE tolerance.
                Full points if within tolerance, zero otherwise. No LLM call.
  Short-answer: Rubric-based LLM grading via Gemini Pro (temperature=0).
                One call per question (all criteria batched).
  Unanswered:   A question the student skipped arrives as a submission_responses
                row with both answer columns NULL. It is scored 0 before the
                type dispatch, with a "not answered" rationale — no LLM call and
                no evidence retrieval.

Design constraints honoured
----------------------------
  - Temperature = 0 for all LLM grading calls (FR-05.3 / PROJECT-BRIEF §9).
  - Evidence is required: if ChromaDB returns no chunks for a short-answer
    question, the criterion is scored 0 and the absence is recorded.
  - GradeRecommendation is the ONLY DB write in this agent — FinalGrade is
    written exclusively by grading/service.finalize_grade().
  - Langfuse trace is non-fatal: a tracing failure never blocks grading.
"""

import json
import logging
import math
import time
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agents.grading.prompts import (
    GRADE_SHORT_ANSWER_PROMPT,
    GRADING_SYSTEM_INSTRUCTION,
    MCQ_GRADING_METHOD,
    NUMERIC_GRADING_METHOD,
    NUMERIC_TOLERANCE,
)
from core.prompt_safety import UNTRUSTED_CONTENT_NOTICE, wrap_untrusted
from agents.grading.state import GradingState

if TYPE_CHECKING:
    from langfuse import Langfuse
    from retrieval.service import RetrievalService

logger = logging.getLogger(__name__)

# Grading-method label and rationale text for questions the student skipped.
UNANSWERED_GRADING_METHOD = "not_answered"
UNANSWERED_FEEDBACK = (
    "Not answered - the student left this question blank. Scored 0."
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _failed_state(state: GradingState, error: str) -> GradingState:
    """Return a failed state copy — used by every node on unrecoverable errors."""
    return {**state, "status": "failed", "error": error}


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap JSON in."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence (```json or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, value))


def _safe_float(value, default: float = 0.0) -> float:
    """Convert value to float safely, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_unanswered(resp: dict) -> bool:
    """
    True when the student left this question blank.

    A skipped question is submitted explicitly (see assessments.schemas.
    SubmissionResponseItem) and stored as a submission_responses row with both
    answer columns NULL, so every node must treat "no answer" as data, not as a
    missing record.
    """
    return not (resp.get("answer_text") or "").strip() and not (
        resp.get("answer_choice") or ""
    ).strip()


# ── Node 1: load_submission_node ──────────────────────────────────────────────

async def load_submission_node(state: GradingState, db) -> GradingState:
    """
    Load the Submission and all related data from the database.

    Reads:
      - Submission (validates it exists and is in 'pending_grading' status)
      - SubmissionResponse × N  (one per question answered)
      - Question × N           (includes answer_key, type, stem, max_points)
      - RubricCriterion × M    (only for short_answer questions)

    Returns a state with `responses` populated — each element is a dict
    containing everything the grading node needs for one question.

    This node is async because it queries the DB.
    """
    from db.models import Submission, SubmissionResponse, Question, RubricCriterion

    submission_id = state["submission_id"]

    # ── Load Submission with eager-loaded responses ────────────────────────────
    result = await db.execute(
        select(Submission)
        .options(
            selectinload(Submission.responses).selectinload(
                SubmissionResponse.question
            ).selectinload(Question.rubric_criteria)
        )
        .where(Submission.id == submission_id)
    )
    submission = result.scalar_one_or_none()

    if not submission:
        return _failed_state(state, f"Submission {submission_id} not found.")

    if submission.status not in ("pending_grading", "graded"):
        # Allow re-grading of already-graded submissions (idempotent retry)
        if submission.status != "graded":
            return _failed_state(
                state,
                f"Submission has unexpected status '{submission.status}'. "
                "Only 'pending_grading' submissions can be graded.",
            )

    if not submission.responses:
        return _failed_state(state, "Submission has no responses to grade.")

    # ── Serialise into plain dicts (keeps nodes decoupled from ORM) ──────────
    responses_out: list[dict] = []
    for sr in submission.responses:
        q: Question = sr.question
        if q is None:
            logger.warning(
                "SubmissionResponse %s has no associated Question — skipping.", sr.id
            )
            continue

        answer_key_parsed: dict = {}
        if q.answer_key:
            try:
                answer_key_parsed = json.loads(q.answer_key)
            except json.JSONDecodeError:
                logger.warning("Could not parse answer_key for question %s", q.id)

        rubric: list[dict] = [
            {
                "criterion_id": str(rc.id),
                "description": rc.description,
                "max_points": rc.max_points,
                "order_index": rc.order_index,
            }
            for rc in sorted(q.rubric_criteria, key=lambda x: x.order_index)
        ]

        responses_out.append({
            "question_id": str(q.id),
            "question_type": q.question_type,
            "stem": q.stem,
            "answer_key": answer_key_parsed,
            "answer_text": sr.answer_text,
            "answer_choice": sr.answer_choice,
            "max_points": q.max_points,
            "rubric_criteria": rubric,
        })

    if not responses_out:
        return _failed_state(state, "No valid question responses found to grade.")

    logger.info(
        "load_submission_node: loaded %d responses for submission %s",
        len(responses_out),
        submission_id,
    )

    return {
        **state,
        "responses": responses_out,
        "evidence_per_question": [[] for _ in responses_out],  # pre-allocate
        "criterion_results": [],
        "total_score": 0.0,
        "max_score": 0.0,
        "recommendation_rationale": {},
        "recommendation_id": None,
        "trace_id": None,
        "status": "running",
    }


# ── Node 2: retrieve_evidence_node ────────────────────────────────────────────

def retrieve_evidence_node(
    state: GradingState,
    retrieval_service: "RetrievalService",
) -> GradingState:
    """
    Retrieve course evidence for each short-answer question.

    Evidence is retrieved using the question stem as the query.
    MCQ and numeric questions are skipped — they are scored deterministically.

    This node is synchronous (same pattern as assessment agent).
    """
    course_id = state["course_id"]
    responses = state["responses"]
    evidence_per_question: list[list[dict]] = []

    for resp in responses:
        if resp["question_type"] != "short_answer" or not resp["rubric_criteria"]:
            # No evidence needed — deterministic grading
            evidence_per_question.append([])
            continue

        if _is_unanswered(resp):
            # Blank answer: grade_responses_node scores it 0 without an LLM
            # call, so retrieving evidence for it would be wasted work.
            evidence_per_question.append([])
            continue

        try:
            retrieval_result = retrieval_service.retrieve(
                query=resp["stem"],
                course_id=course_id,
                top_k=5,
            )
            chunks = [
                {
                    "text": chunk.text,
                    "source_file": chunk.source_file,
                    "page_or_slide": chunk.page_or_slide,
                    "confidence": float(score),
                }
                for chunk, score in zip(
                    retrieval_result.chunks,
                    retrieval_result.confidence_scores,
                )
            ]
            evidence_per_question.append(chunks)
            logger.debug(
                "Retrieved %d evidence chunks for question %s",
                len(chunks),
                resp["question_id"],
            )
        except Exception as exc:
            # ChromaDB failure: continue with empty evidence and score 0 for this question.
            # This is non-fatal — the grade_responses_node will record 0 + explanation.
            logger.warning(
                "Evidence retrieval failed for question %s (ChromaDB error): %s. "
                "Grading will proceed with 0 points for rubric criteria.",
                resp["question_id"],
                exc,
            )
            evidence_per_question.append([])

    return {**state, "evidence_per_question": evidence_per_question}


# ── Node 3: grade_responses_node ──────────────────────────────────────────────

def grade_responses_node(
    state: GradingState,
    gemini_pro,
) -> GradingState:
    """
    Score each response against its question type and rubric.

    MCQ:          Exact match against answer_key["correct_answer"]. No LLM.
    Numeric:      Float comparison within NUMERIC_TOLERANCE. No LLM.
    Short-answer: One Gemini Pro call per question (all criteria batched).
                  Temperature = 0 enforced via generate_deterministic().

    On LLM failure for a short-answer question: all criteria for that question
    receive score 0.0 and the error is recorded in the feedback.
    The graph does NOT abort — partial grading is written to the DB so the
    teacher can review and override.
    """
    responses = state["responses"]
    evidence_per_question = state["evidence_per_question"]
    criterion_results: list[list[dict]] = []
    total_score = 0.0
    max_score = 0.0
    grading_method_used = set()

    for idx, resp in enumerate(responses):
        evidence = evidence_per_question[idx] if idx < len(evidence_per_question) else []
        q_type = resp["question_type"]
        q_max = resp["max_points"]
        max_score += q_max

        # ── Unanswered: score 0 with an explicit rationale ────────────────────
        # Both answer columns are NULL because the student skipped the question.
        # Score it 0 here rather than falling through to the type-specific
        # branches, which would either burn an LLM call on an empty answer or
        # report a misleading "non-numeric response" reason.
        if _is_unanswered(resp):
            grading_method_used.add(UNANSWERED_GRADING_METHOD)
            rubric = resp.get("rubric_criteria") or []
            if rubric:
                criterion_results.append([
                    {
                        "criterion_id": c["criterion_id"],
                        "description": c["description"],
                        "score": 0.0,
                        "max_points": c["max_points"],
                        "feedback": UNANSWERED_FEEDBACK,
                        "citations": [],
                    }
                    for c in rubric
                ])
            else:
                criterion_results.append([{
                    "criterion_id": None,
                    "description": "Not answered",
                    "score": 0.0,
                    "max_points": q_max,
                    "feedback": UNANSWERED_FEEDBACK,
                    "citations": [],
                }])
            logger.info(
                "Question %s was not answered - scoring 0 of %.2f.",
                resp["question_id"],
                q_max,
            )
            continue

        # ── MCQ: exact string match ───────────────────────────────────────────
        if q_type == "mcq":
            grading_method_used.add(MCQ_GRADING_METHOD)
            correct = str(resp["answer_key"].get("correct_answer", "")).strip().upper()
            student = str(resp.get("answer_choice") or "").strip().upper()
            score = q_max if student and student == correct else 0.0
            feedback = (
                f"Correct. The answer '{correct}' matches the answer key."
                if score > 0
                else f"Incorrect. The answer '{correct}' was expected; you answered '{student or '(no answer)'}'."
            )
            criterion_results.append([{
                "criterion_id": None,
                "description": "MCQ correctness",
                "score": score,
                "max_points": q_max,
                "feedback": feedback,
                "citations": [],
            }])
            total_score += score
            continue

        # ── Numeric: tolerance comparison ─────────────────────────────────────
        if q_type == "numeric":
            grading_method_used.add(NUMERIC_GRADING_METHOD)
            expected_raw = resp["answer_key"].get("correct_answer", "")
            student_raw = resp.get("answer_text") or ""
            try:
                expected_val = float(expected_raw)
                student_val = float(student_raw.strip())
                tolerance = abs(expected_val) * NUMERIC_TOLERANCE if expected_val != 0 else NUMERIC_TOLERANCE
                if abs(student_val - expected_val) <= tolerance:
                    score = q_max
                    feedback = f"Correct. Your answer {student_val} is within the accepted range."
                else:
                    score = 0.0
                    feedback = f"Incorrect. Expected approximately {expected_val}; you answered {student_val}."
            except (ValueError, TypeError):
                score = 0.0
                feedback = "Could not evaluate numeric answer — non-numeric response received."

            criterion_results.append([{
                "criterion_id": None,
                "description": "Numeric correctness",
                "score": score,
                "max_points": q_max,
                "feedback": feedback,
                "citations": [],
            }])
            total_score += score
            continue

        # ── Short-answer: LLM rubric grading ─────────────────────────────────
        rubric_criteria = resp["rubric_criteria"]

        if not rubric_criteria:
            # Short-answer with no rubric — score 0, note the gap
            logger.warning(
                "Short-answer question %s has no rubric criteria — scoring 0.",
                resp["question_id"],
            )
            criterion_results.append([{
                "criterion_id": None,
                "description": "No rubric available",
                "score": 0.0,
                "max_points": q_max,
                "feedback": "This question has no rubric criteria. Score set to 0 pending manual review.",
                "citations": [],
            }])
            continue

        # Build evidence context string
        if evidence:
            evidence_context = "\n\n".join(
                f"[Source: {e['source_file']}, p.{e['page_or_slide']}]\n{e['text']}"
                for e in evidence
            )
        else:
            evidence_context = (
                "No course evidence was retrieved for this question. "
                "Score criteria accordingly — if the student cannot be evaluated "
                "against the corpus, award 0 points and note the absence of evidence."
            )

        rubric_json = json.dumps(rubric_criteria, indent=2)
        student_response = resp.get("answer_text") or "(no response provided)"

        # F23: delimit the untrusted student answer and uploaded course evidence,
        # and prepend the data-boundary notice (grading uses generate_deterministic,
        # which has no system-instruction channel). The rubric is trusted authority
        # and is left un-wrapped. Scores are still clamped to [0, max_points] below.
        prompt = UNTRUSTED_CONTENT_NOTICE + "\n\n" + GRADE_SHORT_ANSWER_PROMPT.format(
            stem=resp["stem"],
            student_response=wrap_untrusted(student_response, "STUDENT RESPONSE"),
            rubric_json=rubric_json,
            evidence_context=wrap_untrusted(evidence_context, "COURSE EVIDENCE"),
        )

        t0 = time.perf_counter()
        try:
            raw = gemini_pro.generate_deterministic(prompt)
        except Exception as exc:
            logger.exception(
                "Gemini Pro grading call failed for question %s: %s",
                resp["question_id"],
                exc,
            )
            # Non-fatal: score all criteria 0 and record the error
            error_criteria = [
                {
                    "criterion_id": c["criterion_id"],
                    "description": c["description"],
                    "score": 0.0,
                    "max_points": c["max_points"],
                    "feedback": f"LLM grading failed: {exc}. Pending manual review.",
                    "citations": [],
                }
                for c in rubric_criteria
            ]
            criterion_results.append(error_criteria)
            continue

        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        logger.info(
            "Gemini Pro graded question %s in %dms",
            resp["question_id"],
            elapsed_ms,
        )

        # ── Parse LLM response ────────────────────────────────────────────────
        cleaned = _strip_json_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error(
                "JSON parse error in grading response for question %s (first 400 chars): %r — %s",
                resp["question_id"],
                cleaned[:400],
                exc,
            )
            error_criteria = [
                {
                    "criterion_id": c["criterion_id"],
                    "description": c["description"],
                    "score": 0.0,
                    "max_points": c["max_points"],
                    "feedback": "LLM returned malformed JSON. Pending manual review.",
                    "citations": [],
                }
                for c in rubric_criteria
            ]
            criterion_results.append(error_criteria)
            continue

        llm_scores = parsed.get("criterion_scores", [])

        # ── Reconcile LLM output against known rubric criteria ─────────────────
        # Build a lookup by criterion_id for the LLM's scores
        llm_by_criterion_id: dict[str, dict] = {
            s.get("criterion_id", ""): s for s in llm_scores
        }

        question_criteria_out: list[dict] = []
        q_score = 0.0

        for crit in rubric_criteria:
            cid = crit["criterion_id"]
            cmax = crit["max_points"]
            llm_entry = llm_by_criterion_id.get(cid)

            if llm_entry:
                raw_score = _safe_float(llm_entry.get("score"), 0.0)
                clamped_score = _clamp(raw_score, 0.0, cmax)
                if clamped_score != raw_score:
                    logger.warning(
                        "LLM score %.2f out of range [0, %.2f] for criterion %s — clamped.",
                        raw_score,
                        cmax,
                        cid,
                    )
                citations = [
                    {
                        "text": cite.get("quoted_text", ""),
                        "source_file": cite.get("source_file", ""),
                        "page_or_slide": cite.get("page_or_slide"),
                        "confidence": next(
                            (e["confidence"] for e in evidence
                             if cite.get("quoted_text", "") in e.get("text", "")),
                            0.0,
                        ),
                    }
                    for cite in llm_entry.get("citations", [])
                ]
                question_criteria_out.append({
                    "criterion_id": cid,
                    "description": crit["description"],
                    "score": clamped_score,
                    "max_points": cmax,
                    "feedback": str(llm_entry.get("feedback", "")),
                    "citations": citations,
                })
                q_score += clamped_score
            else:
                # LLM omitted this criterion — score 0, flag for review
                logger.warning(
                    "LLM did not return a score for criterion %s — defaulting to 0.",
                    cid,
                )
                question_criteria_out.append({
                    "criterion_id": cid,
                    "description": crit["description"],
                    "score": 0.0,
                    "max_points": cmax,
                    "feedback": "Criterion was not evaluated by the model. Pending manual review.",
                    "citations": [],
                })

        criterion_results.append(question_criteria_out)
        total_score += q_score
        grading_method_used.add("llm_rubric")

    # ── Determine overall grading method label ────────────────────────────────
    if len(grading_method_used) == 1:
        method = grading_method_used.pop()
    elif grading_method_used:
        method = "mixed"
    else:
        method = "unknown"

    # ── Build rationale dict (stored as JSON in GradeRecommendation.rationale) ─
    rationale = {
        "grading_method": method,
        "questions": [
            {
                "question_id": resp["question_id"],
                "question_type": resp["question_type"],
                "criteria": criterion_results[i] if i < len(criterion_results) else [],
            }
            for i, resp in enumerate(responses)
        ],
    }

    logger.info(
        "grade_responses_node: total_score=%.2f / max_score=%.2f (method=%s) for submission %s",
        total_score,
        max_score,
        method,
        state["submission_id"],
    )

    return {
        **state,
        "criterion_results": criterion_results,
        "total_score": round(total_score, 4),
        "max_score": round(max_score, 4),
        "recommendation_rationale": rationale,
        "status": "running",
    }


# ── Node 4: persist_recommendation_node ──────────────────────────────────────

async def persist_recommendation_node(
    state: GradingState,
    db,
    langfuse: "Langfuse",
) -> GradingState:
    """
    Write the GradeRecommendation to the database and mark the Submission as graded.

    Architecture constraint:
      This node writes ONLY GradeRecommendation — never FinalGrade.
      FinalGrade is exclusively written by grading/service.finalize_grade()
      after explicit instructor action.

    Transaction:
      - GradeRecommendation INSERT
      - Submission.status UPDATE to "graded"
      Both are committed atomically.
    """
    from db.models import GradeRecommendation, Submission

    submission_id = state["submission_id"]

    # ── Fetch Submission to update status ─────────────────────────────────────
    result = await db.execute(
        select(Submission).where(Submission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        return _failed_state(
            state,
            f"Submission {submission_id} disappeared before recommendation could be persisted.",
        )

    # ── Build rationale and citations JSON ────────────────────────────────────
    rationale_json = json.dumps(state["recommendation_rationale"])

    # Flatten all citations across all criteria for the evidence_citations field
    all_citations: list[dict] = []
    for question_criteria in state["criterion_results"]:
        for crit in question_criteria:
            all_citations.extend(crit.get("citations", []))
    citations_json = json.dumps(all_citations)

    # ── INSERT GradeRecommendation ────────────────────────────────────────────
    recommendation = GradeRecommendation(
        submission_id=submission_id,
        status="pending_review",
        recommended_score=state["total_score"],
        max_score=state["max_score"],
        rationale=rationale_json,
        evidence_citations=citations_json,
    )
    db.add(recommendation)

    # ── UPDATE Submission.status ──────────────────────────────────────────────
    submission.status = "graded"

    # ── Commit atomically ─────────────────────────────────────────────────────
    await db.flush()   # get recommendation.id before commit
    recommendation_id = recommendation.id
    await db.commit()

    logger.info(
        "GradeRecommendation %s persisted for submission %s (%.2f / %.2f)",
        recommendation_id,
        submission_id,
        state["total_score"],
        state["max_score"],
    )

    # ── Langfuse trace (non-fatal) ────────────────────────────────────────────
    trace_id: str | None = None
    try:
        trace = langfuse.trace(
            name="grading_recommendation",
            input={
                "submission_id": str(submission_id),
                "course_id": str(state["course_id"]),
                "question_count": len(state["responses"]),
            },
            output={
                "recommendation_id": str(recommendation_id),
                "recommended_score": state["total_score"],
                "max_score": state["max_score"],
                "grading_method": state["recommendation_rationale"].get("grading_method"),
            },
            metadata={"status": "pending_review"},
        )
        trace_id = trace.id
    except Exception as exc:
        logger.warning("Langfuse trace failed for grading recommendation (non-fatal): %s", exc)

    return {
        **state,
        "recommendation_id": recommendation_id,
        "trace_id": trace_id,
        "status": "complete",
        "error": None,
    }
