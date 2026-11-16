"""State of the schema's foreign keys, and the retry of `VALIDATE CONSTRAINT`.

Alembic 0032_foreign_keys adds the 18 links of `core.db_accounts.FOREIGN_KEYS` as
`NOT VALID` (new rows are checked at once, old rows are not judged) and then
validates them; a constraint whose table already holds an orphan row (a child
whose parent is gone) is logged and stays `NOT VALID` — the site starts anyway.
This script says which constraints are valid, which are not and how many
orphan rows each has (counts only: no ids, no DNIs, nothing personal), and with
`--validate` retries the validation once the data has been fixed. It changes
no data; the only write is the constraint's own `validated` flag.

Run from the app directory (the container's /app):
    python3 scripts/fixes/validate_foreign_keys.py              # report, read-only
    python3 scripts/fixes/validate_foreign_keys.py --validate   # retry VALIDATE on the NOT VALID ones
    python3 scripts/fixes/validate_foreign_keys.py --json
Exit status: 0 when every foreign key exists and is valid, 1 otherwise.

What to do with an orphan (the owner decides each time; medical rows are never
deleted by a script): restore the parent, set an optional link to NULL, move
the row to a quarantine table with a verified copy, or leave the constraint NOT
VALID — new rows stay protected meanwhile.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects.postgresql import REGCLASS  # noqa: E402

from app.core import db, schema_model, sqlcore  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core.schema_model import pg_constraint  # noqa: E402


def _orphans(cur, child, column, parent, pcolumn):
    tables = schema_model.metadata.tables
    c, p = tables[child].alias("c"), tables[parent].alias("p")
    has_parent = sa.exists(sa.literal_column("1")).select_from(p).where(p.c[pcolumn] == c.c[column])
    return sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(c).where(
        c.c[column].is_not(None), ~has_parent)).fetchone()["n"]


def status(conn):
    """One dict per registered foreign key: name, child, column, parent,
    state ('valid' | 'not_valid' | 'missing') and, unless valid, the orphan
    count. Read-only."""
    out = []
    with conn.cursor() as cur:
        for name, child, column, parent, pcolumn, _ in db_accounts.FOREIGN_KEYS:
            row = sqlcore.run(cur, sa.select(pg_constraint.c.convalidated).where(
                pg_constraint.c.contype == sa.literal_column("'f'"), pg_constraint.c.conname == name,
                pg_constraint.c.conrelid == sa.cast(sa.literal_column("'public.'").op("||")(
                    sa.literal(child, sa.Text)), REGCLASS))).fetchone()
            state = "missing" if row is None else ("valid" if row["convalidated"] else "not_valid")
            item = {"name": name, "child": child, "column": column, "parent": parent,
                    "state": state, "orphans": None}
            if state != "valid":
                item["orphans"] = _orphans(cur, child, column, parent, pcolumn)
            out.append(item)
    return out


def validate(conn):
    """Retries `VALIDATE CONSTRAINT` on every NOT VALID key (`conn` is an
    autocommit psycopg connection with dict rows, see `connect`); returns the
    status afterwards. A violation leaves the constraint NOT VALID."""
    from psycopg.errors import ForeignKeyViolation
    for item in status(conn):
        if item["state"] != "not_valid":
            continue
        try:
            with conn.cursor() as cur:
                sqlcore.run_ddl(cur, sa.DDL("ALTER TABLE %s VALIDATE CONSTRAINT %s" % (
                    sqlcore.quote_identifier(item["child"]), sqlcore.quote_identifier(item["name"]))))
        except ForeignKeyViolation:
            pass
    return status(conn)


def connect():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(db._db_url(), autocommit=True, row_factory=dict_row)  # noqa: SLF001


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    with connect() as conn:
        result = validate(conn) if "--validate" in argv else status(conn)
    if "--json" in argv:
        print(json.dumps(result, indent=2))
    else:
        for item in result:
            extra = "" if item["orphans"] is None else "  orphan rows: %s" % item["orphans"]
            print("%-45s %-10s %s.%s -> %s%s" % (
                item["name"], item["state"], item["child"], item["column"], item["parent"], extra))
    return 0 if all(i["state"] == "valid" for i in result) else 1


if __name__ == "__main__":
    sys.exit(main())
