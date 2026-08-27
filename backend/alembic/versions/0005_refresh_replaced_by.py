"""a rotated refresh token records what replaced it

Revision ID: 0005_refresh_replaced_by
Revises: 0004_application_critique

`revoked_at` alone cannot say WHY a token died. Rotation, logout and the
family-wide kill after a detected replay all set the same column, so a token
revoked seconds ago by logout was indistinguishable from one rotated seconds ago
by a racing tab. /auth/refresh needs that difference: a race deserves a retry,
a replay deserves every session revoked.

Only rotation writes this column. NULL therefore means "revoked for some other
reason", which is exactly the set that must never be forgiven as a race.
Existing rows stay NULL, which is the safe reading of tokens whose history we
cannot reconstruct.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_refresh_replaced_by"
down_revision = "0004_application_critique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("replaced_by_jti", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "replaced_by_jti")
