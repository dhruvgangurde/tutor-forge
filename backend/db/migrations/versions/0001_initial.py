"""Initial schema — all 16 tables plus assessment stabilization.

Revision ID: 0001_initial
Revises: (none)
Create Date: 2026-07-08

Includes:
- All 16 ORM tables from db/models.py
- assessments.generation_error column (TEXT, nullable)
- assessments.published_at column (TIMESTAMPTZ, nullable)
- CHECK CONSTRAINT ck_assessments_status limiting status to
  ('generating', 'draft', 'published', 'failed')
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("hashed_password", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── 2. courses ────────────────────────────────────────────────────────────
    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 3. chapters ───────────────────────────────────────────────────────────
    op.create_table(
        "chapters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
    )

    # ── 4. concepts ───────────────────────────────────────────────────────────
    op.create_table(
        "concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("keywords", sa.Text, nullable=True),
        sa.Column("difficulty", sa.String(32), nullable=True),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
    )

    # ── 5. concept_prerequisites ──────────────────────────────────────────────
    op.create_table(
        "concept_prerequisites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prerequisite_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("concept_id", "prerequisite_id"),
    )

    # ── 6. ingestion_jobs ─────────────────────────────────────────────────────
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 7. tutoring_sessions ──────────────────────────────────────────────────
    op.create_table(
        "tutoring_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("current_hint_level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 8. tutoring_messages ──────────────────────────────────────────────────
    op.create_table(
        "tutoring_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tutoring_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("citations", sa.Text, nullable=True),
        sa.Column("hint_level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_refusal", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 9. assessments ────────────────────────────────────────────────────────
    op.create_table(
        "assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="generating"),
        sa.Column("config", sa.Text, nullable=True),
        sa.Column(
            "generation_error",
            sa.Text,
            nullable=True,
            comment="User-friendly error message when status='failed'. Null on success.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when status transitions to 'published'.",
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'draft', 'published', 'failed')",
            name="ck_assessments_status",
        ),
    )

    # ── 10. questions ─────────────────────────────────────────────────────────
    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_type", sa.String(32), nullable=False),
        sa.Column("stem", sa.Text, nullable=False),
        sa.Column("options", sa.Text, nullable=True),
        sa.Column("answer_key", sa.Text, nullable=True),
        sa.Column("bloom_level", sa.String(32), nullable=True),
        sa.Column("difficulty", sa.String(32), nullable=True),
        sa.Column("max_points", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
    )

    # ── 11. rubric_criteria ───────────────────────────────────────────────────
    op.create_table(
        "rubric_criteria",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("max_points", sa.Float, nullable=False),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
    )

    # ── 12. submissions ───────────────────────────────────────────────────────
    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_grading"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 13. submission_responses ──────────────────────────────────────────────
    op.create_table(
        "submission_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answer_text", sa.Text, nullable=True),
        sa.Column("answer_choice", sa.String(8), nullable=True),
    )

    # ── 14. grade_recommendations ─────────────────────────────────────────────
    op.create_table(
        "grade_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_review"),
        sa.Column("recommended_score", sa.Float, nullable=False),
        sa.Column("max_score", sa.Float, nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("evidence_citations", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 15. final_grades ──────────────────────────────────────────────────────
    op.create_table(
        "final_grades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grade_recommendations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "teacher_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("final_score", sa.Float, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("teacher_note", sa.Text, nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 16. grade_audit_records ───────────────────────────────────────────────
    op.create_table(
        "grade_audit_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "final_grade_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("final_grades.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("ai_recommended_score", sa.Float, nullable=False),
        sa.Column("teacher_final_score", sa.Float, nullable=False),
        sa.Column("action_taken", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Drop in reverse dependency order.
    op.drop_table("grade_audit_records")
    op.drop_table("final_grades")
    op.drop_table("grade_recommendations")
    op.drop_table("submission_responses")
    op.drop_table("submissions")
    op.drop_table("rubric_criteria")
    op.drop_table("questions")
    op.drop_table("assessments")
    op.drop_table("tutoring_messages")
    op.drop_table("tutoring_sessions")
    op.drop_table("ingestion_jobs")
    op.drop_table("concept_prerequisites")
    op.drop_table("concepts")
    op.drop_table("chapters")
    op.drop_table("courses")
    op.drop_index("ix_users_email", "users")
    op.drop_table("users")
