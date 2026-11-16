"""0014_users_updated_column — users.updated as a real forward revision.

Revision ID: 0014_users_updated_column
Revises: 0013_users_identity_kind
Create Date: 2026-09-15

`users.updated` (db._SCHEMA_SQL, 2026-08-27, "recientes" contacts feature —
`admin_panel/admin_patients.py:list_patients`, `core/db.py:users_admin_list`)
was added to `_SCHEMA_SQL` only, with no matching forward Alembic revision.
A fresh database (baseline replay) got the column; every database already
stamped to Alembic head before 2026-08-27 — the live demo included — never
did, and `GET /api/admin/patients` (no query string, the "recientes"
default view) has been failing on it ever since with `psycopg.errors.
UndefinedColumn: column u.updated does not exist` (confirmed via the demo
container's own logs, `docker logs <demo-app-uuid>-...`). The
search path (`?q=`) never selects `u.updated`, so it stayed green.

Same two-halves contract as every revision since 0002: the DDL below is
byte-equivalent to the `updated` ALTER + backfill in db._SCHEMA_SQL (fresh
installs get it through the baseline replay and this revision is then a
clean no-op there); existing stamped databases receive it here, on their
next boot's `upgrade(head)`. `IF NOT EXISTS` / `WHERE updated IS NULL` are
load-bearing, not defensive fluff — idempotent on a database that somehow
already has the column populated.

Forward-only, like every revision in this repo (docs/UPGRADING.md,
"Rollback"): `downgrade()` refuses.
"""
from alembic import op

revision = "0014_users_updated_column"
down_revision = "0013_users_identity_kind"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS updated timestamptz;
        UPDATE users SET updated = created WHERE updated IS NULL;
        """
    )


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "\"Rollback\") — restore from a backup if an upgrade goes wrong."
    )
