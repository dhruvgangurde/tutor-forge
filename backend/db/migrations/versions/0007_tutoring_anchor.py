"""Remember which question a tutoring conversation is about.

Revision ID: 0007_tutoring_anchor
Revises: 0006_revoked_tokens
Create Date: 2026-10-10

Adds tutoring_sessions.anchor_message_id: the student message holding the open
learning question. Replies ("no", "I don't know") and hint requests are judged
against it instead of against the latest message alone, which is what refused
short answers to the tutor's own questions and sent hints after the wrong
question.

Nullable, ON DELETE SET NULL, indexed. Existing sessions start with NULL and
the service falls back to the history until a new question sets it. No data is
rewritten.

Additive and reversible.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007_tutoring_anchor"
down_revision = "0006_revoked_tokens"
branch_labels = None
depends_on = None

_TABLE = "tutoring_sessions"
_COLUMN = "anchor_message_id"
_FK = "fk_tutoring_sessions_anchor_message_id"
_INDEX = "ix_tutoring_sessions_anchor_message_id"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        _FK,
        _TABLE,
        "tutoring_messages",
        [_COLUMN],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, _COLUMN)
