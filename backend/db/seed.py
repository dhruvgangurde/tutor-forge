"""
db/seed.py
----------
Idempotent seed script for demo users.

Run from the backend/ directory:
    python -m db.seed

Demo credentials (documented in README.md):
    teacher@demo.com  /  password123  →  role: teacher
    student@demo.com  /  password123  →  role: student

The script is idempotent: it skips insertion if the user already exists.

It also enrolls the demo student in every course the demo teacher owns.
Course access requires an enrollment (see courses/enrollment.py), and no
course is seeded here -- demo content is uploaded through the app -- so re-run
this after uploading demo courses to give the demo student access to them.
Existing enrollments are skipped.
"""

import asyncio
import sys

from sqlalchemy import select

from core.database import AsyncSessionFactory, engine
from core.security import hash_password
from db.models import Base, Course, Enrollment, User

DEMO_TEACHER_EMAIL = "teacher@demo.com"
DEMO_STUDENT_EMAIL = "student@demo.com"

DEMO_USERS = [
    {
        "email": "teacher@demo.com",
        "password": "password123",
        "role": "teacher",
    },
    {
        "email": "student@demo.com",
        "password": "password123",
        "role": "student",
    },
]


async def seed() -> None:
    # Ensure tables exist (useful when running seed before alembic in dev)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionFactory() as db:
        created = 0
        for user_data in DEMO_USERS:
            result = await db.execute(
                select(User).where(User.email == user_data["email"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                print(f"  ✓ {user_data['email']} already exists — skipping")
                continue

            user = User(
                email=user_data["email"],
                hashed_password=hash_password(user_data["password"]),
                role=user_data["role"],
            )
            db.add(user)
            created += 1
            print(f"  + Created {user_data['role']}: {user_data['email']}")

        await db.commit()

        enrolled = await _enroll_demo_student(db)

    print(f"\nSeed complete. {created} user(s) created, {enrolled} enrollment(s) added.")


async def _enroll_demo_student(db) -> int:
    """Enroll the demo student in each of the demo teacher's courses (idempotent)."""
    teacher = (
        await db.execute(select(User).where(User.email == DEMO_TEACHER_EMAIL))
    ).scalar_one_or_none()
    student = (
        await db.execute(select(User).where(User.email == DEMO_STUDENT_EMAIL))
    ).scalar_one_or_none()
    if teacher is None or student is None:
        return 0

    course_ids = (
        await db.execute(select(Course.id).where(Course.owner_id == teacher.id))
    ).scalars().all()
    already = set(
        (
            await db.execute(
                select(Enrollment.course_id).where(Enrollment.student_id == student.id)
            )
        ).scalars().all()
    )
    added = 0
    for course_id in course_ids:
        if course_id in already:
            continue
        db.add(Enrollment(course_id=course_id, student_id=student.id))
        added += 1
    await db.commit()
    if course_ids:
        print(
            f"  ✓ {DEMO_STUDENT_EMAIL} enrolled in {len(course_ids)} demo course(s) "
            f"({added} new)"
        )
    return added


if __name__ == "__main__":
    print("Seeding demo users...")
    asyncio.run(seed())
    sys.exit(0)
