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
"""

import asyncio
import sys

from sqlalchemy import select

from core.database import AsyncSessionFactory, engine
from core.security import hash_password
from db.models import Base, User

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

    print(f"\nSeed complete. {created} user(s) created.")


if __name__ == "__main__":
    print("Seeding demo users...")
    asyncio.run(seed())
    sys.exit(0)
