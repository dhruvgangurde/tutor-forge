"""Course enrollment: only students a teacher assigns can access a course.

Revision ID: 0005_enrollments
Revises: 0004_course_archive
Create Date: 2026-10-07

Adds the enrollments table (course_id, student_id, enrolled_at), unique on the
pair.

Why: before this, every self-registered student could list every ready course,
open tutoring sessions on it, and list, take and submit its assessments. Tutor
citations return course text verbatim, so any account could read effectively an
entire course. Every student-facing course surface now requires an enrollment
row, which only the course's owning teacher can create.

No backfill. Enrolling every student who already has a session or submission
would re-grant exactly the access this migration exists to remove. Teachers
re-add their real students; the demo student is enrolled by db/seed.py.

Additive and reversible.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "0005_enrollments"
down_revision = "0004_course_archive"
branch_labels = None
depends_on = None

_TABLE = "enrollments"
_STUDENT_INDEX = "ix_enrollments_student_id"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "course_id",
            UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("course_id", "student_id", name="uq_enrollments_course_student"),
    )
    op.create_index(_STUDENT_INDEX, _TABLE, ["student_id"])


def downgrade() -> None:
    op.drop_index(_STUDENT_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
