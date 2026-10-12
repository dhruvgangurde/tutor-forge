"""Remember a student message as the tutor understood it.

Revision ID: 0008_tutoring_resolved_content
Revises: 0007_tutoring_anchor
Create Date: 2026-10-12

Adds tutoring_messages.resolved_content: a follow-up rewritten into a
standalone question for retrieval (agents/tutor/rewrite.py), when that rewrite
was used. "solve with an solved example" is stored as typed in content and,
here, as the question it stood for, so later hints retrieve on the latter.

Nullable, no default, no index. Existing rows stay NULL and are read as
before. Additive and reversible.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0008_tutoring_resolved_content"
down_revision = "0007_tutoring_anchor"
branch_labels = None
depends_on = None

_TABLE = "tutoring_messages"
_COLUMN = "resolved_content"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
