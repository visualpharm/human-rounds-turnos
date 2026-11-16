"""Memory of the files the retention deleted on purpose (schema review №11).

Revision ID: 0029_purged_files
Revises: 0028_data_subject_requests
Create Date: 2026-10-25

The referral-file retention only removed the file from the data volume: the
copies the B2 sweep had already uploaded stayed forever, and restoring an old
volume brought the file back. `purged_files` is the journal the retention
writes BEFORE it deletes (path relative to data/, the reason, when), so the
B2 sweep can delete every remote version of that path, never upload it again,
and `restore_version` can refuse it. Additive only: one new table, nothing
read or rewritten in existing tables; the same DDL sits in
`core.db._SCHEMA_SQL` for fresh installs. Forward-only.
"""
from alembic import op

revision = "0029_purged_files"
down_revision = "0028_data_subject_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS purged_files ("
        " path text PRIMARY KEY,"
        " reason text NOT NULL,"
        " purged_at timestamptz NOT NULL DEFAULT now(),"
        " remote_done timestamptz)")


def downgrade():
    raise NotImplementedError("purged_files is forward-only; restore a verified backup")
