"""relocation preference becomes tri-state

Revision ID: 0003_relocation_tristate
Revises: 0002_security_hardening

`candidate_profiles.willing_to_relocate` was NOT NULL with a Python-side default
of False, so every profile that had never answered the question looked exactly
like a profile that had answered "no". services/answers.py read that column
straight into a screening answer, which meant the agent told employers "this
candidate will not relocate" on behalf of people who had never said so -- a
fabricated answer, and one that quietly loses interviews.

The column becomes nullable and existing rows are left alone: a stored `false`
was either a real answer or an unanswered default, and we cannot tell which, so
we keep it rather than silently re-opening settled preferences. New profiles
start NULL, and NULL routes the question to the human.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_relocation_tristate"
down_revision = "0002_security_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "candidate_profiles",
        "willing_to_relocate",
        existing_type=sa.Boolean(),
        nullable=True,
        server_default=None,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE candidate_profiles SET willing_to_relocate = false "
        "WHERE willing_to_relocate IS NULL"
    )
    op.alter_column(
        "candidate_profiles",
        "willing_to_relocate",
        existing_type=sa.Boolean(),
        nullable=False,
    )
