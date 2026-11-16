"""What the last remote purge pass of the B2 sweep did (one row, for the «Sistema» panel).

Revision ID: 0034_backup_purge_state
Revises: 0033_reset_token_hash
Create Date: 2026-10-26

The B2 sweep deletes the remote copies of the files the retention deleted
(`purged_files`). When the application key has no `deleteFiles` capability every
deletion is refused (pinamar, 2026-10-26) and the paths simply stay pending;
the web process that renders «Sistema» cannot see what the worker's sweep
found, so the sweep leaves it here: when the last pass ran, when the last
pass without a refusal or failure ended, and how many paths it closed, found
refused (`denied`) or failed. A single row (`key = 'remote_purge'`), rewritten by
each pass. No person's data. Additive only: one new empty table, nothing read
or rewritten in existing tables. Forward-only.
"""
from alembic import op

revision = "0034_backup_purge_state"
down_revision = "0033_reset_token_hash"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS backup_purge_state ("
        " key text PRIMARY KEY,"
        " last_pass_at timestamptz NOT NULL DEFAULT now(),"
        " last_success_at timestamptz,"
        " closed integer NOT NULL DEFAULT 0,"
        " denied integer NOT NULL DEFAULT 0,"
        " failed integer NOT NULL DEFAULT 0)")


def downgrade():
    raise NotImplementedError("backup_purge_state is forward-only; the next sweep rewrites its row")
