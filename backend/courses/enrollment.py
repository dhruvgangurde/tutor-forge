"""
courses/enrollment.py
---------------------
Course enrollment: which students may access which course.

A student reaches a course's content only through an Enrollment row. The
student-facing gates (courses/router.list_available_courses,
tutoring/router.create_tutoring_session + chat/hint,
assessments/service.list_published_assessments, assessments/router take +
submit) call into this module; roster management is exposed to the course's
owning teacher through the owner-scoped /courses/{course_id}/enrollments
routes.

Like courses/lifecycle.py these are plain operations: the routers own the HTTP
semantics, including the 404-hides-existence convention for course-scoped
denials.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from db.models import Enrollment, User


class StudentNotFoundError(Exception):
    """No student account has the given email."""


class AlreadyEnrolledError(Exception):
    """The student already has an enrollment in the course."""


class NotEnrolledError(Exception):
    """The student has no enrollment in the course to remove."""


def enrolled_course_ids(student_id: uuid.UUID) -> Select:
    """Subquery of the course ids a student is enrolled in, for IN filters."""
    return select(Enrollment.course_id).where(Enrollment.student_id == student_id)


async def is_enrolled(db: AsyncSession, *, student_id: uuid.UUID, course_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Enrollment.id).where(
            Enrollment.student_id == student_id, Enrollment.course_id == course_id
        )
    )
    return result.first() is not None


async def list_enrollments(
    course_id: uuid.UUID, db: AsyncSession
) -> list[tuple[Enrollment, User]]:
    """The course roster, oldest enrollment first."""
    result = await db.execute(
        select(Enrollment, User)
        .join(User, Enrollment.student_id == User.id)
        .where(Enrollment.course_id == course_id)
        .order_by(Enrollment.enrolled_at.asc())
    )
    return [(enrollment, user) for enrollment, user in result.all()]


async def enroll_student(
    course_id: uuid.UUID, email: str, db: AsyncSession
) -> tuple[Enrollment, User]:
    """
    Enroll the student account with ``email`` in the course.

    Raises StudentNotFoundError when no account has that email OR the account is
    not a student -- one error for both, so the roster form cannot be used to
    learn which emails belong to teachers. Raises AlreadyEnrolledError on a
    duplicate, including one that loses a race to the unique constraint.
    """
    result = await db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    )
    student = result.scalar_one_or_none()
    if student is None or student.role != "student":
        raise StudentNotFoundError(email)

    if await is_enrolled(db, student_id=student.id, course_id=course_id):
        raise AlreadyEnrolledError(email)

    enrollment = Enrollment(course_id=course_id, student_id=student.id)
    db.add(enrollment)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AlreadyEnrolledError(email)
    return enrollment, student


async def remove_enrollment(
    course_id: uuid.UUID, student_id: uuid.UUID, db: AsyncSession
) -> None:
    """
    Remove a student from the course. Their history (sessions, submissions,
    grades) is untouched; they simply lose access from now on.
    """
    result = await db.execute(
        sa_delete(Enrollment).where(
            Enrollment.course_id == course_id, Enrollment.student_id == student_id
        )
    )
    if result.rowcount == 0:
        raise NotEnrolledError(str(student_id))
    await db.commit()
