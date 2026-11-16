"""0005_demo_refresh_completion — durable, non-sensitive proof that a demo
generation completed rather than merely that its process started.

The public demo refreshes fictional data and copied pack assets on boot and
at the local-day boundary.  A log line or live daemon thread cannot prove
that a complete refresh actually finished, and both disappear on redeploy.
This singleton row records the latest attempt plus the last fully-complete
generation.  ``partial``/``failed`` overwrite the active outcome, preventing
an admin status surface from showing an earlier success as current.

Dual-surface contract: the exact DDL also lives in ``core.db._SCHEMA_SQL``
for a fresh 0001 baseline replay.  Existing stamped installations receive
this table here.  The record is bounded and deliberately stores no config,
credentials, sample data, or patient information.
"""
from alembic import op


revision = "0005_demo_refresh_completion"
down_revision = "0004_backup_version_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS demo_refresh_completion (
            job             text PRIMARY KEY CHECK (job = 'demo_refresh'),
            attempted_for   date NOT NULL,
            outcome         text NOT NULL CHECK (outcome IN ('success', 'partial', 'failed')),
            last_attempt_at timestamptz NOT NULL DEFAULT now(),
            success_for     date,
            last_success_at timestamptz
        );
        """
    )


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "Rollback) — restore from a backup if an upgrade goes wrong."
    )
