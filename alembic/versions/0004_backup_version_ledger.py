"""0004_backup_version_ledger — immutable B2-version receipts and deletion
tombstones for the operator-only restore/retention tooling.

Revision ID: 0004_backup_version_ledger
Revises: 0003_backup_journal
Create Date: 2026-08-10

The earlier ``backup_journal`` table stores only the current path-level
payload identity.  It intentionally cannot identify which B2 *version* was
accepted, so an old journal row is valuable evidence but must never become a
retention candidate.  This revision adds a nullable current ``remote_file_id``
and a separate immutable receipt ledger keyed by B2 file id.  Old rows keep
NULL and remain non-prunable by construction.

The tombstone table is separate from ``backup_versions`` so remote acceptance
facts never change after insert.  It records a successful exact B2 deletion,
or an idempotent retry that reports the same version already absent, and makes
a delete-success/DB-crash retry converge without reclassifying an unexplained
missing remote version as safe.

Dual-surface contract: the same DDL appears in ``core.db._SCHEMA_SQL`` for a
fresh 0001 baseline replay.  Existing stamped installations get it here.  All
DDL is idempotent because a fresh boot runs 0001's current schema first and
then upgrades through this forward revision.
"""
from alembic import op

revision = "0004_backup_version_ledger"
down_revision = "0003_backup_journal"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE backup_journal
            ADD COLUMN IF NOT EXISTS remote_file_id text;

        CREATE TABLE IF NOT EXISTS backup_versions (
            file_id       text PRIMARY KEY,
            remote_name   text NOT NULL,
            sha256        text NOT NULL,
            sha1          text NOT NULL,
            size          bigint NOT NULL,
            journal_path  text NOT NULL,
            accepted_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS backup_versions_path_idx
            ON backup_versions (journal_path, accepted_at DESC);

        CREATE TABLE IF NOT EXISTS backup_version_tombstones (
            file_id       text PRIMARY KEY,
            remote_name   text NOT NULL,
            deleted_at    timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "Rollback) — restore from a backup if an upgrade goes wrong."
    )
