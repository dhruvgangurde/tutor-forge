"""
tutoring/service.py
--------------------
Business logic for Socratic tutoring sessions.

Design:
  - Sessions persist per student-course pair with a current_hint_level.
  - Each /chat request resets hint_level to 0 (fresh Socratic question).
  - Each /hint request increments hint_level (up to MAX_HINT_LEVEL=4, the
    terminal Full Explanation rung) and regenerates guidance at that level.
  - Both paths supply ``prior_question`` -- the most recent student turn that
    carried real topical content -- so the tutor graph can retrieve on the
    conversation's topic when the current message has none of its own. Without
    it a contentless follow-up ("give me the answer") retrieved on itself,
    scored below the groundedness bar, and was refused as out-of-corpus.
  - Both operations invoke the Tutor LangGraph synchronously (no BackgroundTasks).
  - Messages are persisted with role ("student" | "tutor") and citations (JSON).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agents.tutor.query_resolution import is_low_content
from db.models import Course, TutoringMessage, TutoringSession, User

if TYPE_CHECKING:
    from langfuse import Langfuse
    from retrieval.service import RetrievalService

logger = logging.getLogger(__name__)


#: How far back to look for a substantive question. Small on purpose: the
#: fallback is meant to recover the topic of the conversation the student is
#: currently in, not to resurrect one from much earlier in the session.
_PRIOR_QUESTION_LOOKBACK = 10


async def _recent_student_messages(
    session_id: uuid.UUID,
    db: AsyncSession,
    limit: int = _PRIOR_QUESTION_LOOKBACK,
) -> list[TutoringMessage]:
    """Return this session's student messages, most recent first."""
    result = await db.execute(
        select(TutoringMessage)
        .where(
            TutoringMessage.session_id == session_id,
            TutoringMessage.role == "student",
        )
        .order_by(TutoringMessage.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def _first_substantive(messages: list[TutoringMessage]) -> str | None:
    """
    Return the content of the most recent message carrying real topic signal.

    Skipping the low-content ones matters: a student who types "give me the
    answer" and then clicks hint would otherwise have that string become the
    retrieval query for every subsequent hint in the session.
    """
    for msg in messages:
        if not is_low_content(msg.content):
            return msg.content
    return None


async def create_session(
    course_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession,
) -> TutoringSession:
    """
    Create a new tutoring session for a student on a course.

    Raises ValueError if:
      - Course does not exist
      - Course status is not 'ready'
    """
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course:
        raise ValueError(f"Course {course_id} not found.")
    if course.status != "ready":
        raise ValueError(
            f"Course ingestion is not complete (status='{course.status}'). "
            "Please wait until the course is ready before starting a tutoring session."
        )

    session = TutoringSession(
        student_id=student_id,
        course_id=course_id,
        current_hint_level=0,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def send_chat_message(
    session: TutoringSession,
    question: str,
    db: AsyncSession,
    retrieval_service: "RetrievalService",
    gemini_pro,
    langfuse: "Langfuse",
) -> dict:
    """
    Process a student's question and return a Socratic guiding response.

    1. Resolves prior_question (last substantive student turn) for retrieval
       fallback, BEFORE this message is persisted.
    2. Builds TutorState with hint_level=0 (fresh chat resets hints).
    3. Invokes Tutor graph in 'chat' mode.
    4. Persists student message and tutor response.
    5. Resets session.current_hint_level to 0.
    6. Returns final graph state.
    """
    from agents.tutor.graph import build_tutor_graph
    from agents.tutor.state import TutorState

    # Read the history BEFORE persisting this turn, so the current message can
    # never be selected as its own "prior" question.
    prior_question = _first_substantive(
        await _recent_student_messages(session.id, db)
    )

    # Build initial state — all keys must be present
    initial_state: TutorState = {
        "session_id": session.id,
        "course_id": session.course_id,
        "student_id": session.student_id,
        "question": question,
        "prior_question": prior_question,
        "resolved_question": None,
        "retrieval_query_source": "message",
        "pedagogy_skip_requested": False,
        "retrieval_result": None,
        "is_grounded": False,
        "hint_level": 0,  # Fresh question resets hint level
        "response": "",
        "citations": [],
        "pedagogy_trace": {},
    }

    # Build and invoke graph
    graph = build_tutor_graph(
        retrieval_service=retrieval_service,
        gemini_pro=gemini_pro,
        langfuse=langfuse,
        mode="chat",
    )
    final_state = await graph.ainvoke(initial_state)

    # Persist student message
    student_msg = TutoringMessage(
        session_id=session.id,
        role="student",
        content=question,
        citations=None,
        hint_level=0,
        is_refusal=False,
    )
    db.add(student_msg)

    # Persist tutor response. A grounded turn that declined to hand over the
    # answer (FR-03.3) is also a refusal from the UI's point of view -- it is
    # styled as one and carries no tutoring content -- so is_refusal is driven
    # by the trace's refusal_kind rather than by groundedness alone.
    is_refusal = bool(final_state.get("pedagogy_trace", {}).get("refused", False))
    tutor_msg = TutoringMessage(
        session_id=session.id,
        role="tutor",
        content=final_state.get("response", ""),
        citations=json.dumps(final_state.get("citations", [])),
        hint_level=0,
        is_refusal=is_refusal,
    )
    db.add(tutor_msg)

    # Reset hint level for this session (new question, fresh hints)
    session.current_hint_level = 0

    await db.commit()

    logger.info(
        "Tutor response sent: session_id=%s student_id=%s grounded=%s "
        "query_source=%s refusal_kind=%s",
        session.id,
        session.student_id,
        final_state.get("is_grounded"),
        final_state.get("retrieval_query_source"),
        final_state.get("pedagogy_trace", {}).get("refusal_kind"),
    )

    return final_state


async def request_hint(
    session: TutoringSession,
    db: AsyncSession,
    retrieval_service: "RetrievalService",
    gemini_pro,
    langfuse: "Langfuse",
) -> dict:
    """
    Escalate the hint level for the current question.

    1. Loads this session's recent student messages.
    2. Raises ValueError if no prior question exists.
    3. Builds TutorState with question=<last student message> and
       prior_question=<last SUBSTANTIVE student message>, hint_level=<current+1>.
    4. Invokes Tutor graph in 'hint' mode.
    5. Persists tutor hint message, updates session.current_hint_level.
    6. Returns final graph state.

    Step 3 is what stops one bad message poisoning the rest of the session. The
    question still defaults to the latest student turn, but if that turn is
    contentless ("give me the answer"), query resolution falls back to
    prior_question -- otherwise every subsequent hint click would retrieve on
    that string, score below the bar, and refuse.
    """
    from agents.tutor.graph import build_tutor_graph
    from agents.tutor.state import TutorState

    recent = await _recent_student_messages(session.id, db)
    if not recent:
        raise ValueError("Ask a question before requesting a hint.")
    last_student_msg = recent[0]
    prior_question = _first_substantive(recent)

    # Build initial state — question comes from student's last message
    initial_state: TutorState = {
        "session_id": session.id,
        "course_id": session.course_id,
        "student_id": session.student_id,
        "question": last_student_msg.content,
        "prior_question": prior_question,
        "resolved_question": None,
        "retrieval_query_source": "message",
        "pedagogy_skip_requested": False,
        "retrieval_result": None,
        "is_grounded": False,
        "hint_level": session.current_hint_level,  # Current level (advance_hint_node will increment)
        "response": "",
        "citations": [],
        "pedagogy_trace": {},
    }

    # Build and invoke graph in 'hint' mode
    graph = build_tutor_graph(
        retrieval_service=retrieval_service,
        gemini_pro=gemini_pro,
        langfuse=langfuse,
        mode="hint",
    )
    final_state = await graph.ainvoke(initial_state)

    # Persist tutor hint message
    hint_msg = TutoringMessage(
        session_id=session.id,
        role="tutor",
        content=final_state.get("response", ""),
        citations=json.dumps(final_state.get("citations", [])),
        hint_level=final_state.get("hint_level", session.current_hint_level),
        is_refusal=bool(final_state.get("pedagogy_trace", {}).get("refused", False)),
    )
    db.add(hint_msg)

    # Update session hint level to match final state
    session.current_hint_level = final_state.get("hint_level", session.current_hint_level)

    await db.commit()

    logger.info(
        "Hint escalated: session_id=%s hint_level=%s",
        session.id,
        session.current_hint_level,
    )

    return final_state


async def get_messages(
    session_id: uuid.UUID,
    db: AsyncSession,
) -> list[TutoringMessage]:
    """Fetch all messages for a session in chronological order."""
    result = await db.execute(
        select(TutoringMessage)
        .where(TutoringMessage.session_id == session_id)
        .order_by(TutoringMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def list_sessions(
    student_id: uuid.UUID,
    db: AsyncSession,
) -> list[TutoringSession]:
    """Fetch all tutoring sessions for a student, newest first."""
    result = await db.execute(
        select(TutoringSession)
        .where(TutoringSession.student_id == student_id)
        .order_by(TutoringSession.created_at.desc())
    )
    return list(result.scalars().all())
