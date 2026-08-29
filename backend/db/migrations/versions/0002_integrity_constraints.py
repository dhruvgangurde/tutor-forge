"""Database integrity: submission uniqueness + foreign-key indexes.

Revision ID: 0002_integrity_constraints
Revises: 0001_initial
Create Date: 2026-07-18

Additive integrity hardening (F7 + F20):
- F7: UNIQUE(assessment_id, student_id) on submissions so a student cannot have
      two submissions for the same assessment even under a concurrent race.
      Existing duplicates are de-duplicated first (earliest submission kept).
- F20: indexes on foreign-key columns used in WHERE/JOIN lookups. Postgres does
       not auto-index FKs, so these were sequential scans.

No table/column drops; safe and reversible.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_integrity_constraints"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


# (index_name, table, column) for every FK index added by this migration.
_FK_INDEXES = [
    ("ix_courses_owner_id", "courses", "owner_id"),
    ("ix_chapters_course_id", "chapters", "course_id"),
    ("ix_concepts_chapter_id", "concepts", "chapter_id"),
    ("ix_ingestion_jobs_course_id", "ingestion_jobs", "course_id"),
    ("ix_tutoring_sessions_student_id", "tutoring_sessions", "student_id"),
    ("ix_tutoring_sessions_course_id", "tutoring_sessions", "course_id"),
    ("ix_tutoring_messages_session_id", "tutoring_messages", "session_id"),
    ("ix_assessments_course_id", "assessments", "course_id"),
    ("ix_assessments_created_by", "assessments", "created_by"),
    ("ix_questions_assessment_id", "questions", "assessment_id"),
    ("ix_rubric_criteria_question_id", "rubric_criteria", "question_id"),
    ("ix_submissions_student_id", "submissions", "student_id"),
    ("ix_submission_responses_submission_id", "submission_responses", "submission_id"),
    ("ix_submission_responses_question_id", "submission_responses", "question_id"),
    ("ix_final_grades_teacher_id", "final_grades", "teacher_id"),
]

_UNIQUE_NAME = "uq_submissions_assessment_student"


def upgrade() -> None:
    # ── F7: de-duplicate then enforce one submission per (assessment, student) ──
    # Keep the earliest submission per pair; drop later duplicates. Dependent
    # rows (submission_responses, grade_recommendations) cascade on delete.
    op.execute(
        """
        DELETE FROM submissions
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY assessment_id, student_id
                           ORDER BY submitted_at ASC, id ASC
                       ) AS rn
                FROM submissions
            ) ranked
            WHERE ranked.rn > 1
        )
        """
    )
    op.create_unique_constraint(
        _UNIQUE_NAME, "submissions", ["assessment_id", "student_id"]
    )

    # ── F20: foreign-key indexes ──────────────────────────────────────────────
    for name, table, column in _FK_INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _column in _FK_INDEXES:
        op.drop_index(name, table_name=table)
    op.drop_constraint(_UNIQUE_NAME, "submissions", type_="unique")
    # Note: de-duplicated rows cannot be restored by a downgrade.
