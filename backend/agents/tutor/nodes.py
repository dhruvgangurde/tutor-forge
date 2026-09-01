"""
agents/tutor/nodes.py
----------------------
Node functions for the Socratic tutor LangGraph graph.

Execution path:

  retrieve_context -> check_groundedness
        |                    |                          |
        | not grounded       | grounded + skip request  | grounded
        v                    v                          v
    refuse_node    decline_direct_answer_node   generate_guiding_question
        |                    |                          |
        +--------------------+--------------------------+
                             v
                     emit_pedagogy_trace

(hint requests substitute advance_hint_node for generate_guiding_question)

The groundedness check is mandatory - no LLM call is made for out-of-corpus
questions (AC-02: zero leakage requirement).

Two refusals, not one
---------------------
FR-02.7 ("outside the uploaded corpus") and FR-03.3 ("on-topic, but the student
is asking to be handed the answer") are different behaviours needing different
messages. They used to be one branch, so a student mid-conversation who typed
"give me the answer" was told their question was off-syllabus. ``refuse_node``
now serves only FR-02.7; ``decline_direct_answer_node`` serves FR-03.3 and can
only fire on a topic that IS grounded.

Retrieval query resolution
--------------------------
``retrieve_context_node`` no longer embeds the raw message unconditionally. A
contentless follow-up carries almost no topical signal and scored below the
groundedness bar, so it now retrieves on the prior substantive turn instead.
See agents/tutor/query_resolution.py for the live measurements behind that rule,
and for why concatenating the history was measured and rejected.

The hint ladder
---------------
Each level gets its own system instruction from agents/tutor/prompts.py rather
than one shared "never give a direct answer" prompt plus a bare integer. That
contradiction is why hint 3 used to read no more directly than hint 1.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from agents.tutor.prompts import (
    DIRECT_ANSWER_DECLINE,
    FULL_EXPLANATION_LEVEL,
    MAX_HINT_LEVEL,
    OUT_OF_CORPUS_REFUSAL,
    TUTOR_TEMPERATURE,
    system_instruction_for_level,
)
from agents.tutor.query_resolution import (
    SOURCE_MESSAGE,
    is_pedagogy_skip_request,
    resolve_retrieval_query,
)
from agents.tutor.state import TutorState
from core.context_phrasing import mentions_context, strip_context_references
from core.prompt_safety import UNTRUSTED_CONTENT_NOTICE, wrap_untrusted

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from retrieval.service import RetrievalService
    from main import GeminiProClient
    from langfuse import Langfuse

#: Kept as an alias: the out-of-corpus wording moved to agents/tutor/prompts.py
#: when FR-03.3 gained its own, differently-worded decline.
_REFUSAL_MESSAGE = OUT_OF_CORPUS_REFUSAL


# ── Node 1 ────────────────────────────────────────────────────────────────────

def retrieve_context_node(
    state: TutorState,
    retrieval_service: "RetrievalService",
) -> TutorState:
    """
    Resolve what to retrieve on, then retrieve the course context for it.

    The message the student typed is not always the right retrieval query. A
    contentless follow-up ("give me the answer", "what about the worst case")
    has no topic of its own to embed, so it retrieves badly and used to be
    refused as out-of-corpus. When that happens we retrieve on the prior
    substantive question instead -- substituted, never concatenated
    (agents/tutor/query_resolution.py explains why).

    This node also classifies the *message* -- not the resolved query -- as a
    pedagogy-skip request or not, so the graph can route FR-03.3 separately.
    The classification reads the literal message because that is where the
    student's intent lives; the resolved query is only about finding the topic.
    """
    question = state["question"]
    resolved, source = resolve_retrieval_query(question, state.get("prior_question"))

    if source != SOURCE_MESSAGE:
        logger.info(
            "[QUERY-RESOLUTION] session_id=%s message=%r had too little topical "
            "content to retrieve on; using prior turn %r instead.",
            state.get("session_id"),
            question[:80],
            resolved[:80],
        )

    result = retrieval_service.retrieve(state["course_id"], resolved)
    return {
        **state,
        "resolved_question": resolved,
        "retrieval_query_source": source,
        "pedagogy_skip_requested": is_pedagogy_skip_request(question),
        "retrieval_result": {
            "query": result.query,
            "chunks": [asdict(c) for c in result.chunks],
            "confidence_scores": result.confidence_scores,
        },
    }


# ── Node 2 ────────────────────────────────────────────────────────────────────

def check_groundedness_node(
    state: TutorState,
    retrieval_service: "RetrievalService",
) -> TutorState:
    """
    Call is_grounded() to determine if the question can be answered
    from course material. Sets state["is_grounded"] which the conditional
    edge in the graph uses to route.

    The bar is provider-specific and comes from
    settings.active_groundedness_threshold — the single source of truth shared
    with the assessment agent. This used to call is_grounded() with no
    threshold at all, silently taking its old 0.75 default, so under the mock
    provider (real bar 0.20) on-topic questions were refused outright. The
    threshold argument is now required, so that failure mode is unavailable.
    """
    from retrieval.models import Chunk, RetrievalResult

    from core.config import settings

    raw = state.get("retrieval_result") or {}
    chunks = [Chunk(**c) for c in raw.get("chunks", [])]
    result = RetrievalResult(
        query=raw.get("query", ""),
        chunks=chunks,
        confidence_scores=raw.get("confidence_scores", []),
    )

    threshold = settings.active_groundedness_threshold
    top_score = result.top_score if not result.is_empty() else 0.0
    grounded = retrieval_service.is_grounded(result, threshold=threshold)

    logger.info(
        "[GROUNDEDNESS-DECISION] session_id=%s course_id=%s query=%r "
        "query_source=%s top_score=%.4f threshold=%.2f threshold_source=%s grounded=%s",
        state.get("session_id"),
        state.get("course_id"),
        (state.get("resolved_question") or state["question"])[:120],
        state.get("retrieval_query_source", SOURCE_MESSAGE),
        top_score,
        threshold,
        settings.llm_provider,
        grounded,
    )

    return {**state, "is_grounded": grounded}


# ── Node 3 (not-grounded path) ────────────────────────────────────────────────

def refuse_node(state: TutorState) -> TutorState:
    """
    FR-02.7 only: the question is outside the uploaded corpus.

    NO LLM call is made here - this is intentional (AC-02 compliance).
    The Langfuse span is emitted with grounded=False for audit purposes.

    A student asking an ON-topic question to be handed the answer must NOT
    reach this node; that is decline_direct_answer_node, whose message says
    something entirely different.
    """
    return {
        **state,
        "response": OUT_OF_CORPUS_REFUSAL,
        "citations": [],
        "pedagogy_trace": {
            **state.get("pedagogy_trace", {}),
            "grounded": False,
            "refused": True,
            "refusal_kind": "out_of_corpus",
        },
    }


def decline_direct_answer_node(
    state: TutorState,
    retrieval_service: "RetrievalService",
) -> TutorState:
    """
    FR-03.3: the topic IS in the corpus, but the student asked to skip the
    pedagogy and be handed the answer.

    Declines to hand over the answer while staying engaged and pointing at the
    hint ladder, so this is a next step rather than a dead end. Like
    refuse_node it makes no LLM call - the wording is fixed on purpose, because
    an LLM asked to decline an answer request is exactly the setup most likely
    to leak the answer while declining.

    Citations ARE returned: the topic is grounded, and showing the student which
    course material covers it is the useful half of the response.
    """
    from retrieval.models import Chunk, RetrievalResult

    raw = state.get("retrieval_result") or {}
    chunks = [Chunk(**c) for c in raw.get("chunks", [])]
    result = RetrievalResult(
        query=raw.get("query", ""),
        chunks=chunks,
        confidence_scores=raw.get("confidence_scores", []),
    )
    citations = retrieval_service.build_citation_bundle(result)

    logger.info(
        "[PEDAGOGY-DECLINE] session_id=%s declined a direct-answer request on a "
        "grounded topic (hint_level=%s).",
        state.get("session_id"),
        state.get("hint_level"),
    )

    return {
        **state,
        "response": DIRECT_ANSWER_DECLINE,
        "citations": [asdict(c) for c in citations],
        "pedagogy_trace": {
            **state.get("pedagogy_trace", {}),
            "grounded": True,
            "refused": True,
            "refusal_kind": "direct_answer_declined",
        },
    }


# ── Node 4 (grounded path) ────────────────────────────────────────────────────

def generate_guiding_question_node(
    state: TutorState,
    retrieval_service: "RetrievalService",
    gemini_pro: "GeminiProClient",
) -> TutorState:
    """
    Generate the tutor turn for this hint level, grounded in retrieved context.

    The system instruction comes from the per-level ladder in
    agents/tutor/prompts.py, so level 2 is told to give a substantive partial
    hint and level 4 is told to give the complete worked answer. Previously
    every level shared one prompt that said "never give a direct answer" and
    differed only by an integer appended to the user turn, which is why the
    upper rungs never escalated.

    The prompt states the topic using ``resolved_question`` when retrieval fell
    back to the prior turn: generating a guiding question about the literal
    string "give me the answer" produces nonsense, and the retrieved context is
    about the prior topic anyway.
    """
    from retrieval.models import Chunk, RetrievalResult

    raw = state.get("retrieval_result") or {}
    chunks = [Chunk(**c) for c in raw.get("chunks", [])]
    result = RetrievalResult(
        query=raw.get("query", ""),
        chunks=chunks,
        confidence_scores=raw.get("confidence_scores", []),
    )

    context = retrieval_service.build_context_window(result, token_budget=3000)
    citations = retrieval_service.build_citation_bundle(result)

    hint_level = state["hint_level"]
    topic = state.get("resolved_question") or state["question"]

    # F23: delimit untrusted inputs (student question + uploaded course text) so
    # instructions embedded inside them cannot override the tutor's task.
    prompt = (
        f"Student question:\n{wrap_untrusted(topic, 'STUDENT QUESTION')}\n\n"
        f"Course context:\n{wrap_untrusted(context, 'COURSE CONTEXT')}"
    )
    raw_response = gemini_pro.generate(
        prompt,
        temperature=TUTOR_TEMPERATURE,
        system_instruction=system_instruction_for_level(hint_level)
        + "\n\n"
        + UNTRUSTED_CONTENT_NOTICE,
    )

    # Every ladder rung now tells the model never to mention "the context", but
    # that instruction is exactly what was already failing — a student was shown
    # "the course context provided does not directly mention...". So the output
    # is sanitised too, on the same principle as the grading groundedness gate:
    # an instruction the model may ignore is not an enforcement.
    response = strip_context_references(raw_response)
    if response != raw_response:
        logger.info(
            "Stripped context framing from tutor response (session %s).",
            state.get("session_id"),
        )
    elif mentions_context(response):
        # The sanitiser did not match but the phrase is present: a wording this
        # module does not handle yet. Loud so it can be added, not shipped
        # silently.
        logger.warning(
            "Tutor response still mentions the context and was NOT sanitised "
            "(session %s): %r",
            state.get("session_id"),
            response[:160],
        )

    return {
        **state,
        "response": response,
        "citations": [asdict(c) for c in citations],
        "pedagogy_trace": {
            **state.get("pedagogy_trace", {}),
            "grounded": True,
            "hint_level": hint_level,
            "is_full_explanation": hint_level >= FULL_EXPLANATION_LEVEL,
            "retrieval_query_source": state.get("retrieval_query_source", SOURCE_MESSAGE),
        },
    }


# ── Node 5 (hint escalation) ──────────────────────────────────────────────────

def advance_hint_node(
    state: TutorState,
    retrieval_service: "RetrievalService",
    gemini_pro: "GeminiProClient",
) -> TutorState:
    """
    Escalate one rung and regenerate at that level's directness.

    Caps at MAX_HINT_LEVEL (4), the terminal Full Explanation rung required by
    FR-03.2. The cap used to be 3, so the ladder ended one rung short of the
    full worked explanation the requirement calls for, and the UI dead-ended on
    "Max hint level reached".

    Still grounded - reuses the same retrieved context.
    """
    new_level = min(state["hint_level"] + 1, MAX_HINT_LEVEL)
    state_with_level = {**state, "hint_level": new_level}
    return generate_guiding_question_node(state_with_level, retrieval_service, gemini_pro)


# ── Node 6 ────────────────────────────────────────────────────────────────────

def emit_pedagogy_trace_node(
    state: TutorState,
    langfuse: "Langfuse",
) -> TutorState:
    """
    Write pedagogy trace metadata to Langfuse for observability.

    Non-fatal, matching the assessment and grading trace sites: observability
    must never turn a successful tutor turn into a 500. Both graph branches
    (generate_response and refuse) route through this node, so an exception
    raised here takes down *every* chat and hint request — which is exactly
    what the langfuse v2->v4 upgrade did before this guard existed.
    """
    trace_id: str | None = None
    try:
        # langfuse v4 removed the v2 `.trace()` method. `start_observation()`
        # is its replacement; the span must be ended explicitly or the OTel
        # exporter never ships it, and the trace-level id is `.trace_id`
        # (`.id` is the span id).
        span = langfuse.start_observation(
            name="tutor_interaction",
            input={"question": state["question"], "hint_level": state["hint_level"]},
            output={"response": state["response"][:200]},
            metadata=state.get("pedagogy_trace", {}),
        )
        span.end()
        trace_id = span.trace_id
    except Exception:  # noqa: BLE001 - tracing must never break the turn
        # Loud on purpose: observability coverage is an acceptance criterion, so
        # a silently dead trace is a defect, not a footnote.
        logger.exception(
            "Langfuse trace FAILED for tutor interaction on session %s - the "
            "turn itself succeeded, but this one is missing from tracing.",
            state.get("session_id"),
        )

    return {**state, "pedagogy_trace": {**state.get("pedagogy_trace", {}), "trace_id": trace_id}}
