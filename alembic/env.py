"""alembic/env.py — Tier 4 slice 4. Pure schema-migration runner, no ORM.

`target_metadata` is `core.schema_model.metadata`: the SQLAlchemy Core
description of every table (no mapped classes — this repo never adopts the
ORM). It makes `alembic revision --autogenerate -m "..."` PROPOSE the revision
for a change made in `core/schema_model.py`. The proposal is a HINT, never the
revision: it is read and edited by hand, and the SQL that runs is the hand-
checked body (plain SQL via `op.execute(...)` / `op.get_bind().exec_driver_sql(...)`,
the style every revision since the baseline uses). Autogenerate knows nothing of
triggers, functions or `$$` bodies, and it writes `ALTER`s with no data backfill —
so a revision that needs either is written by hand either way.
`alembic check` reports the drift between a booted database and the model; the
guard that actually fails a build is `tests/test_schema_model.py`.
After a revision, regenerate `schema-head.txt` with
`scripts/schema_snapshot.py --write`.

Two ways this runs:
  - **App boot** (`db.py:_run_alembic_upgrade`): builds its own `Config`
    with `sqlalchemy.url` set from the live `DATABASE_URL`, then drives
    `alembic.command.stamp`/`command.upgrade` as a library call — never
    shells out to this file directly, but `command.*` loads and executes
    this `env.py` internally, same as the CLI would.
  - **Developer CLI** (`alembic revision -m "..."`, `alembic history`):
    reads `alembic.ini`'s `sqlalchemy.url` default (local dev DB).

Both paths only ever need "online" mode — this repo has no use for
`alembic upgrade --sql` (offline script generation), so that branch is not
implemented.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.schema_model import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata

# Objects a database has that the model deliberately does not describe: the
# retired runner's table, the queue library's own schema, and the optional
# `pg_trgm` search indexes (best-effort at boot, `db_schema._apply_trigrams`).
_NOT_IN_THE_MODEL_TABLES = ("alembic_version", "schema_migrations")
_NOT_IN_THE_MODEL_PREFIX = "procrastinate_"


def include_object(obj, name, type_, reflected, compare_to):
    """Keeps autogenerate (and `alembic check`) from proposing to DROP what the
    model leaves out on purpose."""
    if type_ == "table" and reflected and compare_to is None:
        return not (name in _NOT_IN_THE_MODEL_TABLES or name.startswith(_NOT_IN_THE_MODEL_PREFIX))
    if type_ == "index" and reflected and compare_to is None:
        return not name.endswith("_trgm_idx")
    return True


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


run_migrations_online()
