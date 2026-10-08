"""Persist token revocation: logout ends the session, and survives a restart.

Revision ID: 0006_revoked_tokens
Revises: 0005_enrollments
Create Date: 2026-10-08

Adds revoked_tokens (jti, expires_at). Replaces the in-memory revocation set,
which was lost on every restart and not shared between workers, and which only
ever held refresh tokens -- access tokens could not be revoked at all, so a
logged-out access token kept working until it expired.

Additive and reversible.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_revoked_tokens"
down_revision = "0005_enrollments"
branch_labels = None
depends_on = None

_TABLE = "revoked_tokens"
_INDEX = "ix_revoked_tokens_expires_at"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(_INDEX, _TABLE, ["expires_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
