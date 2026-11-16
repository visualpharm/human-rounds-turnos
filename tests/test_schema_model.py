"""test_schema_model.py — `core/schema_model.py` (the SQLAlchemy Core description
of every table) says exactly what a fresh install's database is.

Two proofs, each failing with the line that differs:

  1. DB-less: the tables and the NOT NULL columns the model declares are the
     ones `alembic/schema-head.txt` lists (the committed catalog snapshot of a
     real fresh boot). Runs everywhere, in milliseconds.
  2. Real Postgres: a database built from `schema_model.metadata` ALONE
     (`create_all`, no Alembic, no SQL text from this repo) is read back with the
     very same `scripts/schema_snapshot.py` and compared with the committed
     snapshot — column types, order, NOT NULL, defaults, primary keys, UNIQUE and
     CHECK constraints, indexes (with their predicates and sort order) and
     sequences. A table, column, constraint or index present on one side only, or
     spelled differently, is a red test naming it.

Triggers and functions are not part of the model (a revision owns them;
`test_alembic_revisions.TestSchemaSnapshot` guards them), so they are cut out of the
snapshot before the comparison. The snapshot itself leaves out the `pg_trgm`
indexes, the queue library's tables and the Alembic bookkeeping.

A schema change therefore needs the revision AND the matching edit of
`core/schema_model.py` in the same commit: with only one of them this file is red.
"""
import difflib
import os
import re
import sys
import unittest
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

import sqlalchemy as sa  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.core import schema_model  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_schema_model")

SNAPSHOT_FILE = os.path.join(ROOT, "alembic", "schema-head.txt")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _committed_snapshot():
    with open(SNAPSHOT_FILE, encoding="utf-8", newline="") as f:
        return f.read()


def _model_scope(text):
    """The part of a snapshot the model describes: the TABLE blocks (columns,
    constraints, indexes) and the SEQUENCE lines. The header comment, the
    triggers and the function bodies are not part of the model."""
    out = []
    for line in text.splitlines():
        if line.startswith("--") or line.startswith("  CREATE TRIGGER "):
            continue
        if line.startswith("CREATE OR REPLACE FUNCTION "):
            break
        out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def _tables(text):
    """{table: [its lines]} of a snapshot, in file order."""
    blocks, current = {}, None
    for line in text.splitlines():
        if line.startswith("TABLE "):
            current = line[len("TABLE "):]
            blocks[current] = []
        elif line.startswith("SEQUENCE ") or not line.strip():
            current = None
        elif current is not None:
            blocks[current].append(line)
    return blocks


def _describe_difference(committed, built):
    """A readable account of where the model and the database disagree."""
    want, got = _tables(committed), _tables(built)
    lines = []
    for name in sorted(set(want) - set(got)):
        lines.append("table %s is in the database but not in core/schema_model.py" % name)
    for name in sorted(set(got) - set(want)):
        lines.append("table %s is in core/schema_model.py but not in the database "
                     "(no revision creates it)" % name)
    for name in sorted(set(want) & set(got)):
        if want[name] == got[name]:
            continue
        lines.append("table %s differs (- database / + core/schema_model.py):" % name)
        lines.extend("    " + d for d in difflib.unified_diff(
            want[name], got[name], lineterm="", n=0) if not d.startswith(("---", "+++", "@@")))
    seq_want = [l for l in committed.splitlines() if l.startswith("SEQUENCE ")]
    seq_got = [l for l in built.splitlines() if l.startswith("SEQUENCE ")]
    if seq_want != seq_got:
        lines.append("sequences differ (- database / + core/schema_model.py):")
        lines.extend("    " + d for d in difflib.unified_diff(
            seq_want, seq_got, lineterm="", n=0) if not d.startswith(("---", "+++", "@@")))
    return "\n".join(lines)


class TestModelShape(unittest.TestCase):
    """DB-less: the model lists the snapshot's tables and NOT NULL columns."""

    def test_the_model_has_the_snapshot_s_tables(self):
        committed = _tables(_committed_snapshot())
        self.assertEqual(
            sorted(committed), sorted(schema_model.metadata.tables),
            "core/schema_model.py and alembic/schema-head.txt list different tables")

    def test_columns_and_nullability_match_the_snapshot(self):
        problems = []
        for name, lines in _tables(_committed_snapshot()).items():
            snap_cols = []
            for line in lines:
                m = re.match(r"^  (\w+) (.+)$", line)
                if m and not line.startswith(("  CONSTRAINT ", "  CREATE ")):
                    snap_cols.append((m.group(1), " NOT NULL" in m.group(2)))
            table = schema_model.metadata.tables.get(name)
            if table is None:
                continue
            model_cols = [(c.name, not c.nullable) for c in table.columns]
            if snap_cols != model_cols:
                problems.append("%s: database %r vs model %r" % (name, snap_cols, model_cols))
        self.assertEqual([], problems)

    def test_every_table_has_a_named_primary_key(self):
        unnamed = [t.name for t in schema_model.metadata.tables.values()
                   if t.primary_key is None or not t.primary_key.columns
                   or not t.primary_key.name]
        self.assertEqual([], unnamed)

    def test_constraints_and_indexes_carry_explicit_names(self):
        """A name left to a convention would drift from the one the database has."""
        unnamed = []
        for t in schema_model.metadata.tables.values():
            for c in t.constraints:
                if c.name is None:
                    unnamed.append("%s: %r" % (t.name, c))
            for i in t.indexes:
                if i.name is None:
                    unnamed.append("%s: index %r" % (t.name, i))
        self.assertEqual([], unnamed)


class TestDifferenceReport(unittest.TestCase):
    """The comparison names what differs (a green test proves nothing if the
    diff can't see a change)."""

    BASE = ("TABLE a\n  id integer NOT NULL\n  note text\n"
            "  CONSTRAINT a_pkey PRIMARY KEY (id)\n\n"
            "TABLE b\n  id integer NOT NULL\n\n"
            "SEQUENCE a_id_seq integer start 1 inc 1 owned-by a.id\n")

    def test_identical_text_reports_nothing(self):
        self.assertEqual("", _describe_difference(self.BASE, self.BASE))

    def test_a_missing_table_a_changed_column_and_a_sequence_are_named(self):
        built = (self.BASE.replace("note text", "note text NOT NULL")
                 .replace("TABLE b\n  id integer NOT NULL\n\n", "TABLE c\n  id integer NOT NULL\n\n")
                 .replace("start 1", "start 5"))
        report = _describe_difference(self.BASE, built)
        self.assertIn("table b is in the database but not in core/schema_model.py", report)
        self.assertIn("table c is in core/schema_model.py but not in the database", report)
        self.assertIn("table a differs", report)
        self.assertIn("-  note text", report)
        self.assertIn("+  note text NOT NULL", report)
        self.assertIn("sequences differ", report)

    def test_model_scope_drops_header_triggers_and_functions(self):
        text = ("-- header\n\nTABLE a\n  id integer NOT NULL\n  CREATE TRIGGER t BEFORE ...\n\n"
                "CREATE OR REPLACE FUNCTION public.f()\nBEGIN\nEND\n")
        self.assertEqual("\nTABLE a\n  id integer NOT NULL\n", _model_scope(text))


class TestModelEqualsFreshInstall(unittest.TestCase):
    """A database built from the model alone reads back as the committed snapshot."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def _build_from_model(self):
        import psycopg
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "schema_snapshot", os.path.join(ROOT, "scripts", "schema_snapshot.py"))
        snap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(snap)

        name = _dbtest.run_scoped("sana_turnos_test_schema_model_built")
        with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c:
            c.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
            c.execute("CREATE DATABASE %s" % name)
        self.addCleanup(self._drop, name)
        p = urlsplit(_TEST_URL)
        url = urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))
        engine = sa.create_engine(db_schema._sqlalchemy_url(url), poolclass=sa.pool.NullPool)
        try:
            schema_model.metadata.create_all(engine)
        finally:
            engine.dispose()
        with psycopg.connect(url, connect_timeout=5) as conn:
            return snap.snapshot(conn)

    @staticmethod
    def _drop(name):
        import psycopg
        with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c:
            c.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)

    def test_database_built_from_the_model_matches_alembic_head(self):
        committed = _model_scope(_committed_snapshot())
        built = _model_scope(self._build_from_model())
        if committed != built:
            self.fail(
                "core/schema_model.py no longer describes the schema of a fresh install "
                "(alembic/schema-head.txt). A schema change is a revision AND the matching "
                "edit of the model in the same commit:\n" + _describe_difference(committed, built))


class TestAlembicTargetsTheModel(unittest.TestCase):
    """`alembic/env.py` wires `target_metadata` to the model, so autogenerate
    (`alembic revision --autogenerate`, `alembic check`) works as the hint it is
    meant to be: silent on a booted fresh install, loud when the database and the
    model part ways."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        self.name = _dbtest.run_scoped("sana_turnos_test_schema_model_alembic")
        with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c:
            c.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % self.name)
            c.execute("CREATE DATABASE %s" % self.name)
        self.addCleanup(TestModelEqualsFreshInstall._drop, self.name)
        p = urlsplit(_TEST_URL)
        self.url = urlunsplit((p.scheme, p.netloc, "/" + self.name, p.query, p.fragment))
        with _env.patched_env(DATABASE_URL=self.url):
            db_schema._ensured.clear()
            try:
                db._connect().close()
            finally:
                db.close_pools()
                db_schema._ensured.clear()

    def _check(self):
        """`alembic check` against the test database. Alembic's environment
        (`alembic.context`) is one set of module globals per process, so two
        Alembic runs at the same time corrupt each other — a daemon thread left by
        an earlier test in a full run can boot a database (an upgrade) while this
        runs. `db_schema._lock` is the lock the app's own boot holds around its upgrade."""
        from alembic import command
        with db_schema._lock:
            command.check(db_schema._alembic_config(self.url))

    def test_check_is_silent_on_a_fresh_install(self):
        self._check()

    def test_check_names_a_column_the_model_does_not_have(self):
        import psycopg
        from alembic.util import CommandError
        with psycopg.connect(self.url, autocommit=True, connect_timeout=5) as c:
            c.execute("ALTER TABLE searches ADD COLUMN drift_probe text")
        with self.assertRaises(CommandError) as ctx:
            self._check()
        self.assertIn("New upgrade operations detected", str(ctx.exception))
        self.assertIn("drift_probe", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
