"""Student progress: question->concept tagging + per-concept mastery.

Revision ID: 0003_student_progress
Revises: 0002_integrity_constraints
Create Date: 2026-08-30

Adds the two pieces per-concept progress needs, neither of which existed:

- questions.concept_id — nullable FK to concepts. Questions had no link to the
  concept hierarchy at all, so mastery could not be derived from graded work.
  Nullable and ON DELETE SET NULL on purpose: a question is only tagged when the
  match is confident (progress/concept_matching.py), and deleting a concept must
  never delete graded questions.

- student_concept_mastery — accumulated earned/possible points per
  (student, concept). Written only by progress/mastery.py, from
  grading/service.finalize_grade(), so it reflects released grades only.

Schema only. Existing questions are left untagged; run

    python -m db.backfill_concept_tags

to attempt tagging historical questions. That is a separate, idempotent,
best-effort step held to a stricter confidence bar than generation-time tagging,
and it is deliberately not run inside this migration: it is approximate, and a
migration should not silently write approximate data.

Additive and fully reversible.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "0003_student_progress"
down_revision = "0002_integrity_constraints"
branch_labels = None
depends_on = None

_MASTERY_TABLE = "student_concept_mastery"
_QUESTION_FK = "fk_questions_concept_id"
_QUESTION_INDEX = "ix_questions_concept_id"


def upgrade() -> None:
    # ── questions.concept_id ──────────────────────────────────────────────────
    op.add_column(
        "questions",
        sa.Column(
            "concept_id",
            UUID(as_uuid=True),
            nullable=True,
            comment=(
                "Concept this question assesses; NULL when no confident match "
                "was found. See progress/concept_matching.py."
            ),
        ),
    )
    op.create_foreign_key(
        _QUESTION_FK,
        "questions",
        "concepts",
        ["concept_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(_QUESTION_INDEX, "questions", ["concept_id"])

    # ── student_concept_mastery ───────────────────────────────────────────────
    op.create_table(
        _MASTERY_TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "student_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "concept_id",
            UUID(as_uuid=True),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Graded questions tagged to this concept the student has answered.",
        ),
        sa.Column("earned_points", sa.Float(), nullable=False, server_default="0"),
        sa.Column("possible_points", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "mastery",
            sa.Float(),
            nullable=False,
            server_default="0",
            comment="earned_points / possible_points, 0..1.",
        ),
        sa.Column("last_graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "student_id", "concept_id", name="uq_mastery_student_concept"
        ),
    )
    op.create_index(
        "ix_student_concept_mastery_student_id", _MASTERY_TABLE, ["student_id"]
    )
    op.create_index(
        "ix_student_concept_mastery_concept_id", _MASTERY_TABLE, ["concept_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_student_concept_mastery_concept_id", table_name=_MASTERY_TABLE)
    op.drop_index("ix_student_concept_mastery_student_id", table_name=_MASTERY_TABLE)
    op.drop_table(_MASTERY_TABLE)

    op.drop_index(_QUESTION_INDEX, table_name="questions")
    op.drop_constraint(_QUESTION_FK, "questions", type_="foreignkey")
    op.drop_column("questions", "concept_id")
