"""0003_backup_journal — the durable data-volume backup journal as a
real forward revision.

Revision ID: 0003_backup_journal
Revises: 0002_alert_claims
Create Date: 2026-08-09

Same dual-surface contract 0002 established: `_SCHEMA_SQL` reaches a
database ONLY through 0001_baseline's replay on a genuinely FRESH
install, so new DDL needs BOTH the `_SCHEMA_SQL` entry (fresh-path
schema-diff proof stays byte-equal) AND this forward revision (existing
stamped databases receive it on their next boot's `upgrade(head)`).

`IF NOT EXISTS` is load-bearing: on a fresh install the chain runs 0001
(which already created this table via the `_SCHEMA_SQL` replay) and then
this revision — which must be a clean no-op there. The DDL below must
stay byte-equivalent to the `backup_journal` block in `db._SCHEMA_SQL`;
drift is caught by test_alembic_revisions.py's forward-revision test.

Forward-only, like every revision in this repo (docs/UPGRADING.md,
"Rollback"): `downgrade()` refuses.
"""
from alembic import op

revision = "0003_backup_journal"
down_revision = "0002_alert_claims"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS backup_journal (
            path        text PRIMARY KEY,
            sha256      text NOT NULL,
            size        bigint NOT NULL,
            mtime_ns    bigint NOT NULL,
            remote_name text NOT NULL,
            origin      text NOT NULL DEFAULT 'upload',
            uploaded_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "\"Rollback\") — restore from a backup if an upgrade goes wrong."
    )
