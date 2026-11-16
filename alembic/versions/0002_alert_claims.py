"""0002_alert_claims — the at-most-once daily-alert claims table as a
real forward revision.

Revision ID: 0002_alert_claims
Revises: 0001_baseline
Create Date: 2026-08-08

Why this exists as a revision and not only inside `_SCHEMA_SQL`
(independent gate, 2026-08-08): `_SCHEMA_SQL` reaches a database ONLY
through 0001_baseline's replay on a genuinely FRESH install. Every
existing installation was stamped at head during the slice-4 cutover
(or upgraded past it) and never runs the baseline body again — a table
living only in `_SCHEMA_SQL` would simply never be created on prod or
demo. This confirms the design doc's own supersession note ("every
schema change becomes a real Alembic revision" once slice 4 landed):
new DDL needs BOTH the `_SCHEMA_SQL` entry (so a fresh install's
baseline replay stays complete and the fresh-path schema-diff proof
stays byte-equal) AND a forward revision like this one (so existing
stamped databases actually receive it on their next boot's
`upgrade(head)`).

`IF NOT EXISTS` is load-bearing, not defensive fluff: on a fresh
install the upgrade chain runs 0001 (which already created this table
via the `_SCHEMA_SQL` replay) and then this revision — which must be a
clean no-op there. The DDL below must stay byte-equivalent to the
`alert_claims` block in `db._SCHEMA_SQL`; drift is caught by
test_alembic_revisions.py's forward-revision test, which compares the
schema of an UPGRADED pre-existing database with a fresh one.

Forward-only, like every revision in this repo (docs/UPGRADING.md,
"Rollback"): `downgrade()` refuses.
"""
from alembic import op

revision = "0002_alert_claims"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS alert_claims (
            alert_key   text NOT NULL,
            day         date NOT NULL,
            claimed_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (alert_key, day)
        );
        """
    )


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "\"Rollback\") — restore from a backup if an upgrade goes wrong."
    )
