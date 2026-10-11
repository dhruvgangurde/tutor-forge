"""
tutoring/service.py
--------------------
Business logic for Socratic tutoring sessions.

Design:
  - Sessions persist per student-course pair with a current_hint_level and an
    anchor: the student message holding the open learning question.
  - A /chat message that is a NEW question is judged on its own text. If it is
    grounded it becomes the anchor; if it is refused the anchor is cleared.
    Either way the hint ladder resets to 0.
  - Any other /chat message (a reply such as "no" or "I don't know", or a
    follow-up such as "why?") is judged against the anchor, with the tutor's
    last turn and the reply in the prompt. The ladder level is kept.
  - Each /hint request retrieves on the anchor, never on the latest message,
    and increments hint_level (up to MAX_HINT_LEVEL=4, the terminal Full
    Explanation rung). With no grounded open question it is a 400, with no
    model call and nothing saved; a hint never returns the out-of-corpus
    refusal.
  - Both operations invoke the Tutor LangGraph synchronously (no BackgroundTasks).
  - Messages are persisted with role ("student" | "tutor") and citations (JSON).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.tutor.prompts import NO_OPEN_QUESTION_FOR_HINT
from agents.tutor.query_resolution import TURN_NEW_QUESTION, classify_turn
from db.models import Course, TutoringMessage, TutoringSession

if TYPE_CHECKING:
    from langfuse import Langfuse

    from retrieval.service import RetrievalService

logger = logging.getLogger(__name__)


#: Tie-break for messages with the same timestamp: the student's message comes
#: first. On Windows the Python clock ticks every 15.6 ms, so a turn's two rows
#: could share a timestamp and came back in either order.
_STUDENT_FIRST = case((TutoringMessage.role == "student", 0), else_=1)


def _now() -> datetime:
    """Wall-clock time, taken in Python when the row is created.

    Never a server-side now(): that is the transaction's start time, so every
    row written in one request would tie again.
    """
    return datetime.now(UTC)


async def _open_question(session: TutoringSession, db: AsyncSession) -> TutoringMessage | None:
    """
    The student message holding the open learning question, or None.

    The stored anchor wins. Without one (sessions from before the anchor
    existed, or after a new question was refused) it falls back to the most
    recent student message the classifier calls a new question, else the
    session's first student message -- unless the tutor refused that message,
    in which case there is no open question (None). Whatever it returns still
    has to pass the groundedness gate before anything is generated from it.
    """
    if session.anchor_message_id is not None:
        anchored = await db.get(TutoringMessage, session.anchor_message_id)
        if anchored is not None:
            return anchored

    result = await db.execute(
        select(TutoringMessage)
        .where(TutoringMessage.session_id == session.id)
        .order_by(TutoringMessage.created_at.asc(), _STUDENT_FIRST)
    )
    messages = list(result.scalars().all())
    students = [m for m in messages if m.role == "student"]

    def was_refused(student_msg: TutoringMessage) -> bool:
        """True when the tutor's answer to this message was a refusal."""
        after = messages[messages.index(student_msg) + 1:]
        reply = next((m for m in after if m.role == "tutor"), None)
        return reply is not None and reply.is_refusal

    # A refused question is never the open question. It used to be picked up
    # here once its refusal had cleared the anchor, so every later reply,
    # however on-topic, was judged against it and refused too. With no open
    # question the message is judged on its own text instead.
    for msg in reversed(students):
        if classify_turn(msg.content) == TURN_NEW_QUESTION:
            return None if was_refused(msg) else msg
    if students and not was_refused(students[0]):
        return students[0]
    return None


async def _last_tutor_turn(session_id: uuid.UUID, db: AsyncSession) -> TutoringMessage | None:
    """The tutor's most recent turn that was not a refusal: what the student is replying to."""
    result = await db.execute(
        select(TutoringMessage)
        .where(
            TutoringMessage.session_id == session_id,
            TutoringMessage.role == "tutor",
            TutoringMessage.is_refusal.is_(False),
        )
        .order_by(TutoringMessage.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


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
    if course.archived_at is not None:
        # Archiving hides a course from NEW activity only. Existing sessions
        # keep working, so a student mid-conversation is not cut off.
        raise ValueError(
            "This course has been archived by the teacher and is no longer "
            "available for new tutoring sessions."
        )
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
    retrieval_service: RetrievalService,
    gemini_pro,
    langfuse: Langfuse,
) -> dict:
    """
    Process a student's question and return a Socratic guiding response.

    1. Finds the open learning question and the tutor's last turn, BEFORE this
       message is persisted, and classifies the message (new question or not).
    2. Persists the student message at receipt (flushed, committed with the reply).
    3. Builds TutorState: a new question starts at hint level 0; a reply or
       follow-up keeps the current level and carries the open question.
    4. Invokes Tutor graph in 'chat' mode.
    5. Persists the tutor response, and moves the anchor and the ladder: a
       grounded new question becomes the anchor, a refused one clears it.
    6. Returns final graph state.
    """
    from agents.tutor.graph import build_tutor_graph
    from agents.tutor.state import TutorState

    # Read the history BEFORE persisting this turn, so the current message can
    # never be selected as its own open question.
    open_question = await _open_question(session, db)
    is_new_question = open_question is None or classify_turn(question) == TURN_NEW_QUESTION
    last_tutor = None if is_new_question else await _last_tutor_turn(session.id, db)

    # Persist the student's message at receipt, stamped now. It used to be
    # written after the reply, in the same flush, so it carried the reply's
    # time. Flushed, not committed: if generation fails the request rolls back
    # and no unanswered message is left behind.
    student_msg = TutoringMessage(
        session_id=session.id,
        role="student",
        content=question,
        citations=None,
        hint_level=0,
        is_refusal=False,
        created_at=_now(),
    )
    db.add(student_msg)
    await db.flush()

    # Build initial state — all keys must be present
    initial_state: TutorState = {
        "session_id": session.id,
        "course_id": session.course_id,
        "student_id": session.student_id,
        "question": question,
        "prior_question": None if is_new_question else open_question.content,
        "tutor_last_message": last_tutor.content if last_tutor else None,
        "resolved_question": None,
        "retrieval_query_source": "message",
        "pedagogy_skip_requested": False,
        "retrieval_result": None,
        "is_grounded": False,
        # A new question starts a fresh ladder; a reply stays on the current rung.
        "hint_level": 0 if is_new_question else session.current_hint_level,
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
    grounded = bool(final_state.get("is_grounded"))

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
        created_at=_now(),
    )
    db.add(tutor_msg)

    if is_new_question:
        # A grounded new question opens a conversation about itself; a refused
        # one leaves no open question, so a hint has nothing to build on.
        session.anchor_message_id = student_msg.id if grounded else None
        session.current_hint_level = 0
    elif grounded and session.anchor_message_id is None:
        # Older sessions: adopt the question found in the history once it has
        # passed the gate, so later turns do not have to guess again.
        session.anchor_message_id = open_question.id

    await db.commit()

    logger.info(
        "Tutor response sent: session_id=%s student_id=%s grounded=%s "
        "turn_kind=%s query_source=%s refusal_kind=%s",
        session.id,
        session.student_id,
        grounded,
        final_state.get("turn_kind"),
        final_state.get("retrieval_query_source"),
        final_state.get("pedagogy_trace", {}).get("refusal_kind"),
    )

    return final_state


async def request_hint(
    session: TutoringSession,
    db: AsyncSession,
    retrieval_service: RetrievalService,
    gemini_pro,
    langfuse: Langfuse,
) -> dict:
    """
    Escalate the hint level for the open learning question.

    1. Finds the open learning question (the anchor), never the latest message.
    2. Raises ValueError (a 400) if there is none.
    3. Builds TutorState with question=<the open question>, hint_level=<current>.
    4. Invokes Tutor graph in 'hint' mode; advance_hint_node adds one level.
    5. If the gate refused the question, raises the same ValueError: nothing is
       saved, the ladder does not move, and the out-of-corpus refusal is never
       returned as a hint.
    6. Otherwise persists the hint and updates session.current_hint_level.

    Anchoring is what keeps the ladder on the learning question. Hints used to
    follow the latest student message with three topical words, so after
    "because they have same charge?" all four rungs were about that reply and
    never returned to "what is potential difference?".
    """
    from agents.tutor.graph import build_tutor_graph
    from agents.tutor.state import TutorState

    open_question = await _open_question(session, db)
    if open_question is None:
        raise ValueError(NO_OPEN_QUESTION_FOR_HINT)

    # The student's later replies are deliberately left out of the hint prompt:
    # a hint is about the learning question, and a reply that drifted is
    # exactly what used to drag the ladder off it.
    initial_state: TutorState = {
        "session_id": session.id,
        "course_id": session.course_id,
        "student_id": session.student_id,
        "question": open_question.content,
        "prior_question": None,
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

    if not final_state.get("is_grounded"):
        # The gate refused before any model call. Report it as "ask a question
        # first" rather than saving an out-of-corpus refusal as a hint.
        raise ValueError(NO_OPEN_QUESTION_FOR_HINT)

    # Persist tutor hint message
    hint_msg = TutoringMessage(
        session_id=session.id,
        role="tutor",
        content=final_state.get("response", ""),
        citations=json.dumps(final_state.get("citations", [])),
        hint_level=final_state.get("hint_level", session.current_hint_level),
        is_refusal=bool(final_state.get("pedagogy_trace", {}).get("refused", False)),
        created_at=_now(),
    )
    db.add(hint_msg)

    # Update session hint level to match final state
    session.current_hint_level = final_state.get("hint_level", session.current_hint_level)
    if session.anchor_message_id is None:
        session.anchor_message_id = open_question.id  # older session: keep what passed the gate

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
    """Fetch all messages for a session in chronological order, student first on a tie."""
    result = await db.execute(
        select(TutoringMessage)
        .where(TutoringMessage.session_id == session_id)
        .order_by(TutoringMessage.created_at.asc(), _STUDENT_FIRST)
    )
    return list(result.scalars().all())


#: Longest session title shown in the list. Long enough that two sessions on
#: neighbouring topics stay distinguishable, short enough for one card line.
SESSION_TITLE_MAX_CHARS = 70


def derive_session_title(first_question: str | None) -> str | None:
    """
    A recognisable name for a session, from the first thing the student asked.

    Returns None when the session has no student turn yet; the caller decides
    what to show for an empty session. Chat clients title threads this way for
    the same reason it applies here: a student scanning their list recognises
    "How does photosynthesis convert light" instantly and "Session 4bc8fb2b"
    never.

    Collapses whitespace first so a pasted multi-line question does not become
    a multi-line card title, and truncates on a word boundary so the label does
    not end mid-word.
    """
    if first_question is None:
        return None
    text = " ".join(first_question.split())
    if not text:
        return None
    if len(text) <= SESSION_TITLE_MAX_CHARS:
        return text
    clipped = text[:SESSION_TITLE_MAX_CHARS]
    # Prefer a word boundary, but only if one exists reasonably near the end —
    # a single very long token should still be cut rather than returned whole.
    space = clipped.rfind(" ")
    if space > SESSION_TITLE_MAX_CHARS // 2:
        clipped = clipped[:space]
    return clipped.rstrip(" ,;:.") + "…"


@dataclass(frozen=True)
class SessionListItem:
    """One row of the student's session list, with everything needed to name it."""

    session: TutoringSession
    course_name: str
    title: str | None
    message_count: int
    last_activity_at: datetime


async def list_sessions(
    student_id: uuid.UUID,
    db: AsyncSession,
) -> list[SessionListItem]:
    """
    Every tutoring session for a student, newest first, with display metadata.

    The title comes from the session's first *student* message. It is derived at
    read time rather than stored: the first question is already persisted, so a
    stored copy would be a second source of truth that could drift, and deriving
    it means existing sessions get titles with no backfill.

    ``current_hint_level`` deliberately does not appear in the title. It is the
    depth of the ladder on the last question only, so as a list-level label it
    is close to meaningless -- it says nothing about what the session is about.
    """
    result = await db.execute(
        select(TutoringSession, Course.name)
        .join(Course, TutoringSession.course_id == Course.id)
        .where(TutoringSession.student_id == student_id)
        .order_by(TutoringSession.created_at.desc())
    )
    rows = result.all()
    if not rows:
        return []

    session_ids = [row[0].id for row in rows]

    # First student message per session, in one query rather than one per row.
    first_q_rows = await db.execute(
        select(TutoringMessage.session_id, TutoringMessage.content)
        .where(
            TutoringMessage.session_id.in_(session_ids),
            TutoringMessage.role == "student",
        )
        .order_by(TutoringMessage.session_id, TutoringMessage.created_at.asc())
    )
    first_question: dict[uuid.UUID, str] = {}
    for session_id, content in first_q_rows.all():
        first_question.setdefault(session_id, content)

    stats_rows = await db.execute(
        select(
            TutoringMessage.session_id,
            func.count(TutoringMessage.id),
            func.max(TutoringMessage.created_at),
        )
        .where(TutoringMessage.session_id.in_(session_ids))
        .group_by(TutoringMessage.session_id)
    )
    stats = {row[0]: (row[1], row[2]) for row in stats_rows.all()}

    items = []
    for session, course_name in rows:
        count, last_at = stats.get(session.id, (0, None))
        items.append(
            SessionListItem(
                session=session,
                course_name=course_name,
                title=derive_session_title(first_question.get(session.id)),
                message_count=count,
                # An empty session has no messages; its own creation time is the
                # only activity there has been.
                last_activity_at=last_at or session.created_at,
            )
        )
    return items
