"""0001_baseline — the schema as it stood when the baseline was frozen
(2026-10-25), replayed from `alembic/baseline/0001_schema.sql`.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-06

Tier 4 slice 4 (docs/roadmap/tier4-replatform-design.md, "Alembic (slice
4)"). The ONE-TIME cutover revision: until 2026-10-25 its body was the live
`db._SCHEMA_SQL` string, edited together with a forward revision for every
schema change. The text is now a file next to the revisions, byte-for-byte
what the string was, and FROZEN (`test_alembic_revisions.TestBaselineFrozen`
pins its hash): from here on a schema change is a new revision under
`alembic/versions/` and nothing else — foreign keys, triggers and
dollar-quoted bodies included. Revisions 0002–0027 are idempotent
(`IF NOT EXISTS`), so on a fresh install they are no-ops over this
baseline; every revision after 0027 does real work there.

The file is read lazily inside `upgrade()` and by path relative to this
module, so a bundle carrying only `alembic/` (the rollback package) still
loads this module on a database that never runs it.

It never re-runs against an existing schema: a database with pre-Alembic
tables but no `alembic_version` row is refused at boot
(`db_schema._refuse_pre_alembic`); the installations that predate Alembic
were brought under it by the one-time bridge, removed 2026-11-05 (release
tag `bridge-last`). The
text goes through `op.get_bind().exec_driver_sql(...)`, SQLAlchemy Core's
raw-driver-SQL primitive (bypasses bind-param scanning — load-bearing, since
the text contains a literal `%s` inside a SQL comment).

Forward-only (docs/UPGRADING.md, "Rollback"): `downgrade()` deliberately
refuses rather than pretending an undo exists for DDL already run against
live data.
"""
import os

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_BASELINE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "baseline", "0001_schema.sql")


def upgrade():
    with open(_BASELINE_FILE, encoding="utf-8", newline="") as f:
        op.get_bind().exec_driver_sql(f.read())


def downgrade():
    raise NotImplementedError(
        "Las migraciones de este repo son forward-only (ver docs/UPGRADING.md, "
        "\"Rollback\") — no hay downgrade() para 0001_baseline ni para ninguna "
        "revisión futura. Restaurar desde un backup si un upgrade sale mal."
    )
