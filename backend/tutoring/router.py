"""
tutoring/router.py
-------------------
REST endpoints for Socratic tutoring sessions.

Routes:
  POST   /tutor/sessions                      — create a new session
  POST   /tutor/sessions/{session_id}/chat    — send a question, get guiding response
  POST   /tutor/sessions/{session_id}/hint    — escalate hint level
  GET    /tutor/sessions/{session_id}/messages — fetch conversation history
  GET    /tutor/sessions                      — list sessions for current student

Authorization:
  - All endpoints require student role.
  - Ownership is enforced: students can only access their own sessions.

Error handling:
  - HTTPException is raised only in this router layer.
  - Business logic (service.py) raises ValueError; core/exceptions.py converts to 400.
"""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.service import require_student
from core.dependencies import (
    get_db_session,
    get_gemini_pro,
    get_langfuse_client,
    get_retrieval_service,
)
from db.models import TutoringMessage, TutoringSession, User
from tutoring.schemas import (
    ChatRequest,
    Citation,
    CreateSessionRequest,
    HintResponse,
    SessionCreated,
    SessionSummary,
    TutoringMessageOut,
    TutorResponse,
)
from tutoring.service import (
    create_session,
    get_messages,
    list_sessions,
    request_hint,
    send_chat_message,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tutor", tags=["tutoring"])


# ── Authorization helper ──────────────────────────────────────────────────────


async def _require_student_owns_session(
    session_id: uuid.UUID,
    student: User,
    db: AsyncSession,
) -> TutoringSession:
    """
    Load the tutoring session and verify the requesting student owns it.

    Raises 404 if session doesn't exist or 403 if student doesn't own it.
    """
    result = await db.execute(
        select(TutoringSession).where(TutoringSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutoring session not found.",
        )

    if session.student_id != student.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this tutoring session.",
        )
    return session


# ── POST /tutor/sessions ──────────────────────────────────────────────────────


@router.post(
    "/sessions",
    response_model=SessionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tutoring session",
    description="Start a new Socratic tutoring session on a course.",
)
async def create_tutoring_session(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> SessionCreated:
    """
    Create a new tutoring session for the current student on a course.

    The course must exist and have status='ready'.
    """
    try:
        session = await create_session(
            course_id=body.course_id,
            student_id=student.id,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    logger.info(
        "Tutoring session created: session_id=%s student_id=%s course_id=%s",
        session.id,
        student.id,
        body.course_id,
    )

    return SessionCreated(session_id=session.id, course_id=session.course_id)


# ── POST /tutor/sessions/{session_id}/chat ────────────────────────────────────


@router.post(
    "/sessions/{session_id}/chat",
    response_model=TutorResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a question to the tutor",
    description="Submit a question and receive a Socratic guiding response.",
)
async def send_tutor_chat(
    session_id: uuid.UUID,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
    retrieval_service=Depends(get_retrieval_service),
    gemini_pro=Depends(get_gemini_pro),
    langfuse=Depends(get_langfuse_client),
) -> TutorResponse:
    """
    Submit a question to the Socratic tutor and get a guiding response.

    The response is grounded in the course material. Two refusals are possible
    and they mean different things:
      - out of scope (FR-02.7): the topic is not in the uploaded corpus.
      - direct-answer decline (FR-03.3): the topic IS covered, but the student
        asked to be handed the answer; the tutor declines and points at the
        hint ladder.
    Both come back with is_grounded reflecting the *topic*, so a decline has
    is_grounded=True.
    """
    session = await _require_student_owns_session(session_id, student, db)

    final_state = await send_chat_message(
        session=session,
        question=body.question,
        db=db,
        retrieval_service=retrieval_service,
        gemini_pro=gemini_pro,
        langfuse=langfuse,
    )

    # Build response, converting citation dicts to Citation objects
    citations = [Citation(**c) for c in final_state.get("citations", [])]

    return TutorResponse(
        response=final_state.get("response", ""),
        citations=citations,
        is_grounded=final_state.get("is_grounded", False),
        hint_level=final_state.get("hint_level", 0),
        session_id=session.id,
    )


# ── POST /tutor/sessions/{session_id}/hint ────────────────────────────────────


@router.post(
    "/sessions/{session_id}/hint",
    response_model=HintResponse,
    status_code=status.HTTP_200_OK,
    summary="Request a hint",
    description=(
        "Escalate the hint level for the current question. Levels 1-3 are "
        "progressively more direct hints; level 4 is the full worked explanation."
    ),
)
async def request_tutor_hint(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
    retrieval_service=Depends(get_retrieval_service),
    gemini_pro=Depends(get_gemini_pro),
    langfuse=Depends(get_langfuse_client),
) -> HintResponse:
    """
    Request a more direct hint for the current question.

    Raises 400 if no prior question exists in this session.

    The ladder is FR-03.2's five stages: the chat turn is level 0, hints 1-3
    escalate in directness, and level 4 is the terminal Full Explanation.
    Requests past level 4 keep returning level 4.
    """
    session = await _require_student_owns_session(session_id, student, db)

    try:
        final_state = await request_hint(
            session=session,
            db=db,
            retrieval_service=retrieval_service,
            gemini_pro=gemini_pro,
            langfuse=langfuse,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    citations = [Citation(**c) for c in final_state.get("citations", [])]

    return HintResponse(
        response=final_state.get("response", ""),
        citations=citations,
        hint_level=final_state.get("hint_level", 0),
        session_id=session.id,
    )


# ── GET /tutor/sessions/{session_id}/messages ─────────────────────────────────


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[TutoringMessageOut],
    status_code=status.HTTP_200_OK,
    summary="Fetch conversation history",
    description="Get all messages exchanged in a tutoring session.",
)
async def get_session_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> list[TutoringMessageOut]:
    """Return all messages in a session, in chronological order."""
    session = await _require_student_owns_session(session_id, student, db)

    messages = await get_messages(session_id, db)

    result = []
    for msg in messages:
        citations = []
        if msg.citations:
            try:
                citations = [Citation(**c) for c in json.loads(msg.citations)]
            except json.JSONDecodeError:
                pass
        result.append(
            TutoringMessageOut(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                citations=citations,
                hint_level=msg.hint_level,
                is_refusal=msg.is_refusal,
                created_at=msg.created_at,
            )
        )

    return result


# ── GET /tutor/sessions ───────────────────────────────────────────────────────


@router.get(
    "/sessions",
    response_model=list[SessionSummary],
    status_code=status.HTTP_200_OK,
    summary="List tutoring sessions",
    description="Get all tutoring sessions for the current student.",
)
async def list_student_sessions(
    db: AsyncSession = Depends(get_db_session),
    student: User = Depends(require_student),
) -> list[SessionSummary]:
    """Return all tutoring sessions for the current student, newest first."""
    sessions = await list_sessions(student.id, db)

    return [
        SessionSummary(
            id=s.id,
            course_id=s.course_id,
            current_hint_level=s.current_hint_level,
            created_at=s.created_at,
        )
        for s in sessions
    ]
