"""security hardening

Revision ID: 0002_security_hardening
Revises: 0001_initial

Three fixes that need DDL:

1. `documents.extracted_text` and `documents.parsed` hold the full text and the
   parsed contact block of an uploaded resume -- name, phone, address, employment
   history. They were the only large personal-data columns left in the clear.
   They now use the `EncryptedString` / `EncryptedJSON` types, which are TEXT at
   rest. `parsed` moves from JSONB to TEXT for that reason. Existing rows stay
   readable: `crypto.decrypt_str` passes an unprefixed value straight through, so
   legacy plaintext keeps decoding until it is rewritten.

2. The append-only trigger on `audit_logs` was FOR EACH ROW on UPDATE and DELETE,
   which TRUNCATE does not fire. A statement-level BEFORE TRUNCATE trigger closes
   that.

3. `submission_attempts` gains UNIQUE(application_id, attempt_number) so two
   racing assistants cannot both open a live attempt against one application.
"""

from __future__ import annotations

from alembic import op

revision = "0002_security_hardening"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

AUDIT_TRUNCATE_TRIGGER = """
CREATE OR REPLACE FUNCTION jobagent_audit_no_truncate()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: TRUNCATE is not permitted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_no_truncate
BEFORE TRUNCATE ON audit_logs
FOR EACH STATEMENT EXECUTE FUNCTION jobagent_audit_no_truncate();
"""


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("ALTER TABLE documents ALTER COLUMN parsed TYPE TEXT USING parsed::text")
        op.execute("ALTER TABLE documents ALTER COLUMN parsed DROP NOT NULL")
        op.execute("ALTER TABLE documents ALTER COLUMN extracted_text DROP NOT NULL")
        op.execute(AUDIT_TRUNCATE_TRIGGER)

    op.create_unique_constraint(
        "uq_attempt_number", "submission_attempts", ["application_id", "attempt_number"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_constraint("uq_attempt_number", "submission_attempts", type_="unique")

    if is_postgres:
        op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
        op.execute("DROP FUNCTION IF EXISTS jobagent_audit_no_truncate()")
        op.execute("ALTER TABLE documents ALTER COLUMN parsed TYPE JSONB USING parsed::jsonb")
