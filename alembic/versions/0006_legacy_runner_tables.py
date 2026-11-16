"""0006_legacy_runner_tables — the three tables only the retired legacy
runner used to create, now owned by the Alembic lineage.

Revision ID: 0006_legacy_runner_tables
Revises: 0005_demo_refresh_completion
Create Date: 2026-08-16

`translations` (002_translations), `legal_docs` (003_legal_docs) and
`chat_compactions` (004_chat_compactions) historically reached every
database through `migrations.apply_pending()`, which ran on every boot —
they were never part of `_SCHEMA_SQL`, so the Alembic baseline never
created them. With the legacy runner retired to a one-time bridge (it now
executes only against a genuinely PARTIAL historic schema), the DDL moved
verbatim into `_SCHEMA_SQL`, which covers fresh installs via the baseline.

This revision covers the remaining shape: an already-Alembic database whose
whole history went through Alembic only — e.g. one built by a manual
`alembic upgrade head` under the pre-retirement baseline — which never met
the runner and therefore lacks the three tables. On every other database
(bridged legacy, complete pre-Alembic, fresh under the new baseline) the
tables already exist and each statement is an `IF NOT EXISTS` no-op; this
is a compatibility backstop, not a replay. Same raw-SQL style as every
other revision (no ORM, `exec_driver_sql`).

Forward-only, like the whole lineage (docs/UPGRADING.md, "Rollback"):
`downgrade()` refuses.
"""
from alembic import op

revision = "0006_legacy_runner_tables"
down_revision = "0005_demo_refresh_completion"
branch_labels = None
depends_on = None

# Verbatim the DDL the legacy migrations 002/003/004 ran (and `_SCHEMA_SQL`
# now contains) — kept byte-identical so every path to these tables produces
# the same pg_dump output.
_SQL = """
CREATE TABLE IF NOT EXISTS translations (
    source_key   text NOT NULL,
    lang         text NOT NULL,
    source_hash  text NOT NULL,
    content      text NOT NULL,
    model        text,
    override     boolean NOT NULL DEFAULT false,
    created      timestamptz NOT NULL DEFAULT now(),
    updated      timestamptz NOT NULL DEFAULT now(),
    updated_by   text,
    PRIMARY KEY (source_key, lang)
);

CREATE TABLE IF NOT EXISTS legal_docs (
    doc         text PRIMARY KEY,
    body        text NOT NULL,
    created     timestamptz NOT NULL DEFAULT now(),
    updated     timestamptz NOT NULL DEFAULT now(),
    updated_by  text
);

CREATE TABLE IF NOT EXISTS chat_compactions (
    patient_key    text PRIMARY KEY,
    summary        text NOT NULL,
    message_count  integer NOT NULL DEFAULT 0,
    covers_until   timestamptz NOT NULL,
    created        timestamptz NOT NULL DEFAULT now(),
    updated        timestamptz NOT NULL DEFAULT now()
);
"""


def upgrade():
    op.get_bind().exec_driver_sql(_SQL)


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "\"Rollback\") — restore from a backup if an upgrade goes wrong."
    )
