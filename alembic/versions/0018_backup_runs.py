"""Encrypted installation backup evidence; no change to the legacy B2 ledger."""
from alembic import op

revision = "0018_backup_runs"
down_revision = "0017_doctor_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS backup_runs (
        id text PRIMARY KEY, started_at timestamptz NOT NULL, finished_at timestamptz,
        status text NOT NULL, archive_key text, size bigint NOT NULL DEFAULT 0, sha256 text,
        uploaded boolean NOT NULL DEFAULT false, integrity_verified boolean NOT NULL DEFAULT false,
        restore_tested boolean NOT NULL DEFAULT false, error text,
        evidence jsonb NOT NULL DEFAULT '{}'::jsonb
    )""")


def downgrade():
    raise NotImplementedError("Forward-only; recover using an independently verified backup")
