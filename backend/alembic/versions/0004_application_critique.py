"""applications carry an advisory critique alongside the blocking guard flags

Revision ID: 0004_application_critique
Revises: 0003_relocation_tristate

`fact_guard_flags` answers "does this document claim anything untrue" and can
block an application. The new `critique` column answers the opposite question --
"is this document as strong as the candidate's verified facts allow" -- and
never blocks. Keeping them in separate columns keeps that difference legible:
code that gates on safety reads one, code that advises a human reads the other.

JSON keyed by document role ("resume", "cover_letter"). Existing rows get an
empty dict, which every reader already treats as "not critiqued", so the
backfill is a no-op rather than a lie about documents drafted before this ran.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_application_critique"
down_revision = "0003_relocation_tristate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "critique",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    # The Python default supplies {} from here on; the server default existed
    # only so the NOT NULL could be added without rewriting existing rows.
    op.alter_column("applications", "critique", server_default=None)


def downgrade() -> None:
    op.drop_column("applications", "critique")
