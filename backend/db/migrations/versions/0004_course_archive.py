"""Course archiving: reversible hide without destroying education records.

Revision ID: 0004_course_archive
Revises: 0003_student_progress
Create Date: 2026-08-31

Adds courses.archived_at.

Why archiving rather than a delete:
every foreign key from courses downward is ON DELETE CASCADE, so a hard delete
runs courses -> assessments -> submissions -> grade_recommendations ->
final_grades -> grade_audit_records, and courses -> chapters -> concepts ->
student_concept_mastery. FinalGrade is the only teacher-approved grade record in
the system and GradeAuditRecord exists to be the audit trail; destroying either
because a teacher tidied their course list is not an acceptable outcome.
Measured on live data at the time of writing, deleting one course would have
destroyed 2 FinalGrades and 2 GradeAuditRecords.

A true hard delete is still available through the API, but only for a course
with zero submissions — nothing to destroy.

Additive and reversible.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_course_archive"
down_revision = "0003_student_progress"
branch_labels = None
depends_on = None

_INDEX = "ix_courses_archived_at"


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when archived; NULL = active. History is preserved either way.",
        ),
    )
    # Every student-facing list filters on "not archived", so the partial-ish
    # lookup is worth an index.
    op.create_index(_INDEX, "courses", ["archived_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="courses")
    op.drop_column("courses", "archived_at")
