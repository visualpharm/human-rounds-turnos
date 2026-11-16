"""test_alembic_revisions.py — the Alembic lineage: the frozen baseline, the
fresh-install schema snapshot, the boot path, and every forward revision's
upgrade from an existing installation.

Until 2026-11-05 this was `test_alembic_cutover.py`, which also proved the
one-time bridge from the pre-Alembic schema (`migrations/`). The bridge is
gone: a database without an `alembic_version` row that carries pre-Alembic
tables is refused at boot (`db_schema._refuse_pre_alembic`, TestBootPath) and
is converted by one boot of the release tagged `bridge-last`.

  1. Fresh empty database: bootstrapped through the real path
     (`db._connect()` -> upgrade to head) it reaches EXACTLY the schema
     committed in `alembic/schema-head.txt` (TestSchemaSnapshot); the first
     revision is a frozen file (TestBaselineFrozen).
  2. Boot path (TestBootPath): fresh, already at head, a pre-Alembic
     database refused before any DDL, and the early forward revisions
     0006-0008 on an existing installation.
  3. Every later forward revision: an existing installation stamped at the
     revision before it gains the change, keeps its data, and a second boot
     changes nothing (one class per revision).

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
      discover -s tests -p "test_alembic_revisions.py" -v
"""


import os
import sys
import unittest
from urllib.parse import urlsplit, urlunsplit
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.core import feed_tokens  # noqa: E402

import _dbtest      # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_alembic_revisions")


# The lineage tip, read from the script directory. Adding a revision no
# longer needs an edit here; the conscious step is regenerating
# `alembic/schema-head.txt` (TestSchemaSnapshot).
def _script_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


_HEAD = _script_head()


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _fresh_db_url(name):
    import psycopg
    with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
        cur.execute("CREATE DATABASE %s" % name)
    p = urlsplit(_TEST_URL)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def _drop_db(name):
    import psycopg
    with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)


def _build_existing_schema(url):
    """An existing installation's schema with no revision recorded yet: the
    frozen baseline body plus the trigram indexes every installation gets on
    its first boot. Each caller stamps the revision it starts from and then
    takes away what that revision adds."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(db_schema.BASELINE_SQL)
        conn.commit()
        db_schema._apply_trigrams(conn)


def _snapshot_module():
    """`scripts/schema_snapshot.py`, loaded by path (scripts/ is not a package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "schema_snapshot", os.path.join(ROOT, "scripts", "schema_snapshot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _snapshot_of(url):
    import psycopg
    with psycopg.connect(url, connect_timeout=5) as conn:
        return _snapshot_module().snapshot(conn)


def _connect_new_boot_path(url):
    """Runs `db._connect()` (the NEW, Alembic-driven boot path) against
    `url`, restoring `DATABASE_URL`/`db_schema._ensured` afterward so this doesn't
    leak into any other test in the same process."""
    db_schema._ensured.clear()
    try:
        with _env.patched_env(DATABASE_URL=url):
            conn = db._connect()
            conn.close()
    finally:
        db_schema._ensured.clear()


_BASELINE_SHA256 = "6a6064cd06d330d317c66394770b4a1290fab0e5aaaa66aa541c3cd8dc616edf"


class AlembicConfigurationTest(unittest.TestCase):
    def test_script_location_uses_repo_root_without_checkout_env_access(self):
        checkout_env = os.path.normcase(os.path.abspath(os.path.join(ROOT, ".env")))
        real_open = open

        def guarded_open(path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and (
                os.path.normcase(os.path.abspath(os.fspath(path))) == checkout_env
            ):
                raise AssertionError("Alembic config opened checkout .env")
            return real_open(path, *args, **kwargs)

        with _env.patched_env({"TURNOS_ENV_FILE": os.devnull}), \
             mock.patch("builtins.open", side_effect=guarded_open):
            cfg = db_schema._alembic_config("postgresql://fixture/fixture")
        self.assertEqual(cfg.get_main_option("script_location"), os.path.join(ROOT, "alembic"))


class TestBaselineFrozen(unittest.TestCase):
    """The first revision is a frozen file, never edited again."""

    def test_baseline_file_is_byte_for_byte_the_frozen_text(self):
        import hashlib
        with open(db_schema.BASELINE_FILE, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(
            digest, _BASELINE_SHA256,
            "alembic/baseline/0001_schema.sql is FROZEN: a schema change is a "
            "new revision under alembic/versions/ (plus "
            "`python3 scripts/schema_snapshot.py --write`), never an edit here")

    def test_revision_0001_replays_that_same_file(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rev0001", os.path.join(ROOT, "alembic", "versions", "0001_baseline.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(os.path.realpath(mod._BASELINE_FILE),
                         os.path.realpath(db_schema.BASELINE_FILE))

    def test_there_is_no_second_schema_source_in_db_py(self):
        self.assertFalse(hasattr(db, "_SCHEMA_SQL"),
                         "db._SCHEMA_SQL is gone: DDL lives in revisions")
        self.assertFalse(hasattr(db, "_ACCESS_LOG_GUARD_SQL"),
                         "the guard SQL lives in revision 0026")
        self.assertFalse(hasattr(db, "_SUBJECT_EVENTS_GUARD_SQL"),
                         "the guard SQL lives in revision 0028")


class TestSchemaSnapshot(unittest.TestCase):
    """A fresh install (Alembic from empty to head) ends up with exactly the
    schema committed in `alembic/schema-head.txt` — what a migration MR
    shows for review, regenerated with `scripts/schema_snapshot.py --write`."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def test_fresh_boot_matches_the_committed_snapshot(self):
        name = _dbtest.run_scoped("sana_turnos_test_alembic_snap")
        url = _fresh_db_url(name)
        try:
            _connect_new_boot_path(url)
            fresh = _snapshot_of(url)
        finally:
            _drop_db(name)
        with open(_snapshot_module().SNAPSHOT_FILE, encoding="utf-8", newline="") as f:
            committed = f.read()
        self.assertEqual(
            committed, fresh,
            "alembic/schema-head.txt is stale: a revision changed the schema "
            "(or a new one was added) — run `python3 scripts/schema_snapshot.py "
            "--write` and commit the file with the revision")
        self.assertIn("Alembic head %s." % _HEAD, committed)


class TestForwardRevisionAlertClaims(unittest.TestCase):
    """Upgrade-path gates for the forward revisions after 0001_baseline.

    A table living only in `BASELINE_SQL` never reaches an existing
    installation: it was stamped at 0001 when it moved to Alembic and the baseline
    body does not run again. The fixture therefore removes the post-baseline
    tables and proves a normal boot restores them, preserves data, and
    converges with a fresh install.
    """

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_fwd")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def _existing_0001_db(self):
        """Today's prod/demo shape: full schema, stamped at
        0001_baseline, WITHOUT its forward-revision tables, with real data on the
        books."""
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0001_baseline")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS alert_claims")
            cur.execute("DROP TABLE IF EXISTS backup_journal")
            cur.execute("DROP TABLE IF EXISTS demo_refresh_completion")
            cur.execute("INSERT INTO searches (q, results) VALUES ('fwd-proof', 3)")

    def test_existing_0001_db_gains_forward_tables_and_keeps_its_data(self):
        import psycopg
        self._existing_0001_db()
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass('public.alert_claims') IS NOT NULL")
            self.assertTrue(cur.fetchone()[0],
                            "0002 must create alert_claims on an existing database")
            cur.execute("SELECT to_regclass('public.backup_journal') IS NOT NULL")
            self.assertTrue(cur.fetchone()[0],
                            "0003 must create backup_journal on an existing database")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM searches WHERE q='fwd-proof'")
            self.assertEqual(cur.fetchone()[0], 1,
                             "existing data must survive the upgrade")

    def test_rerun_at_head_is_an_idempotent_noop_that_loses_nothing(self):
        import psycopg
        self._existing_0001_db()
        _connect_new_boot_path(self.db_url)   # reaches head (0005)
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("INSERT INTO alert_claims (alert_key, day) "
                        "VALUES ('fwd-proof', current_date)")
        _connect_new_boot_path(self.db_url)   # rerun at head
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM alert_claims WHERE alert_key='fwd-proof'")
            self.assertEqual(cur.fetchone()[0], 1,
                             "the rerun is idempotent and loses no rows")
            cur.execute("SELECT count(*) FROM searches WHERE q='fwd-proof'")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_upgraded_db_converges_to_a_fresh_install(self):
        self._existing_0001_db()
        _connect_new_boot_path(self.db_url)
        name_fresh = _dbtest.run_scoped("sana_turnos_test_alembic_fwd_fresh")
        url_fresh = _fresh_db_url(name_fresh)
        try:
            _connect_new_boot_path(url_fresh)
            self.assertEqual(_snapshot_of(url_fresh), _snapshot_of(self.db_url),
                             "the upgraded database must converge to a fresh "
                             "install's schema")
        finally:
            _drop_db(name_fresh)


class TestForwardRevisionUsersUpdatedColumn(unittest.TestCase):
    """0014_users_updated_column — regression for the live demo 500.

    `users.updated` (2026-08-27, the "recientes" contacts feature —
    `admin_panel/admin_patients.py:list_patients`, `core/db.py:
    users_admin_list(recientes=True)`) was added to `BASELINE_SQL` only, with
    no forward Alembic revision. Every database already stamped to Alembic
    head before that date — the live demo included, confirmed via its own
    container logs — never gained the column, and `GET /api/admin/patients`
    (no query string) has been a hard 500 (`psycopg.errors.UndefinedColumn:
    column u.updated does not exist`) ever since; `?q=` never selects it, so
    every other patients-list path stayed green and this shipped untested.

    This fixture reproduces exactly that shape: a database stamped at
    0013_users_identity_kind (everything through the revision BEFORE this
    one) with `users.updated` then dropped, matching the column's real
    absence on an existing install. A normal boot must restore it AND the
    domain query that failed on the live demo must actually succeed.
    """

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_users_updated")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def _existing_0013_db_missing_updated(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0013_users_identity_kind")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER TABLE users DROP COLUMN IF EXISTS updated")
            cur.execute(
                "INSERT INTO users (dni, first_name, last_name, pass_hash) "
                "VALUES ('30111230', 'Reciente', 'Paciente', 'x')"
            )

    def test_existing_0013_db_gains_the_column_and_keeps_its_data(self):
        import psycopg
        self._existing_0013_db_missing_updated()
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='users' AND column_name='updated'")
            self.assertIsNotNone(cur.fetchone(),
                                 "0014 must add users.updated on an existing database")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM users WHERE dni='30111230'")
            self.assertEqual(cur.fetchone()[0], 1,
                             "existing data must survive the upgrade")

    def test_recientes_listing_survives_the_exact_shape_that_500d_on_demo(self):
        # Same query `admin_panel/admin_patients.py:list_patients` runs for
        # GET /api/admin/patients with no ?q= — this is the live repro,
        # inline: before 0014 it raised UndefinedColumn on this fixture.
        self._existing_0013_db_missing_updated()
        db_schema._ensured.clear()
        try:
            with _env.patched_env(DATABASE_URL=self.db_url):
                items, total = db_accounts.users_admin_list(recientes=True)
        finally:
            db_schema._ensured.clear()
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["dni"], "30111230")


class TestBackupJournalRevision(unittest.TestCase):
    """The explicit 0002 -> 0003 path for installations already at 0002.

    A fresh 0001 replay uses today's `BASELINE_SQL`, which already contains
    ``backup_journal``. This fixture deliberately removes that table *after*
    reaching the historical 0002 version so the forward migration itself,
    rather than the fresh baseline, is what proves the existing-install path.
    """

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_backup_journal")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_0002_database_gains_backup_journal_at_0003(self):
        import psycopg
        from alembic import command

        cfg = db_schema._alembic_config(self.db_url)
        command.upgrade(cfg, "0002_alert_claims")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE backup_journal")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0002_alert_claims")

        command.upgrade(cfg, "0003_backup_journal")
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass('public.backup_journal') IS NOT NULL")
            self.assertTrue(cur.fetchone()[0])
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0003_backup_journal")


class TestBackupVersionLedgerRevision(unittest.TestCase):
    """The explicit 0003 -> 0004 path, including a rerun with receipt data.

    ``0001_baseline`` now contains the current fresh-install DDL, so this test
    deliberately removes 0004's objects after reaching the historical 0003
    revision.  That makes the forward migration, not an accidental new
    baseline replay, prove the existing-install upgrade path.
    """

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_backup_ledger")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_0003_database_gains_immutable_ledger_and_reruns_without_loss(self):
        import psycopg
        from alembic import command

        cfg = db_schema._alembic_config(self.db_url)
        command.upgrade(cfg, "0003_backup_journal")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE backup_version_tombstones")
            cur.execute("DROP TABLE backup_versions")
            cur.execute("ALTER TABLE backup_journal DROP COLUMN remote_file_id")
            cur.execute("INSERT INTO backup_journal "
                        "(path, sha256, size, mtime_ns, remote_name, origin) "
                        "VALUES ('legacy.bin', %s, 1, 1, 'data/legacy.bin', 'upload')",
                        ("c" * 64,))
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0003_backup_journal")

        command.upgrade(cfg, "0004_backup_version_ledger")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass('public.backup_versions') IS NOT NULL")
            self.assertTrue(cur.fetchone()[0])
            cur.execute("SELECT to_regclass('public.backup_version_tombstones') IS NOT NULL")
            self.assertTrue(cur.fetchone()[0])
            cur.execute("SELECT remote_file_id FROM backup_journal WHERE path='legacy.bin'")
            self.assertIsNone(cur.fetchone()[0], "pre-ledger journal rows stay legacy/non-prunable")
            cur.execute("INSERT INTO backup_versions "
                        "(file_id, remote_name, sha256, sha1, size, journal_path) "
                        "VALUES ('proof-id', 'data/proof.bin', %s, %s, 1, 'proof.bin')",
                        ("a" * 64, "b" * 40))

        command.upgrade(cfg, "0004_backup_version_ledger")
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0004_backup_version_ledger")
            cur.execute("SELECT count(*) FROM backup_versions WHERE file_id='proof-id'")
            self.assertEqual(cur.fetchone()[0], 1)


class TestDemoRefreshCompletionRevision(unittest.TestCase):
    """The explicit 0004 -> 0005 upgrade, including a safe rerun.

    The direct fresh-schema path now includes this table.  Removing it after
    reaching the historical 0004 state proves an existing installation gains
    it through the actual forward migration, rather than through baseline
    replay or lazy runtime DDL.
    """

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_demo_refresh")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_0004_gains_completion_ledger_and_reruns_without_losing_row(self):
        import psycopg
        from alembic import command

        cfg = db_schema._alembic_config(self.db_url)
        command.upgrade(cfg, "0004_backup_version_ledger")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE demo_refresh_completion")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0004_backup_version_ledger")

        command.upgrade(cfg, "0005_demo_refresh_completion")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass('public.demo_refresh_completion') IS NOT NULL")
            self.assertTrue(cur.fetchone()[0])
            cur.execute("""
                INSERT INTO demo_refresh_completion
                    (job, attempted_for, outcome, success_for, last_success_at)
                VALUES ('demo_refresh', current_date, 'success', current_date, now())
            """)

        command.upgrade(cfg, "0005_demo_refresh_completion")
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0005_demo_refresh_completion")
            cur.execute("SELECT count(*) FROM demo_refresh_completion WHERE job='demo_refresh'")
            self.assertEqual(cur.fetchone()[0], 1,
                             "a rerun must preserve a persisted completion record")


class TestBootPath(unittest.TestCase):
    """`db_schema._ensure_schema` on every database shape a boot can meet:
    fresh, already at head, pre-Alembic (refused before any DDL, the advisory
    lock released), and an existing installation below 0006, 0007 and 0008."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "no database")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_boot")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_fresh_database_boots_alembic_only_without_legacy_bookkeeping(self):
        import psycopg
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations') IS NULL")
            self.assertTrue(cur.fetchone()[0],
                            "a fresh install never has the old runner's bookkeeping table")
            for t in ("translations", "legal_docs", "chat_compactions"):
                cur.execute("SELECT to_regclass('public.' || %s) IS NOT NULL", (t,))
                self.assertTrue(cur.fetchone()[0], f"{t} must come from the baseline")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)

    def test_already_alembic_database_boots_at_head_and_keeps_its_rows(self):
        import psycopg
        _connect_new_boot_path(self.db_url)      # first boot: fresh install
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("INSERT INTO searches (q, results) VALUES ('boot-proof', 2)")
            # A fossil of the pre-Alembic era next to alembic_version changes nothing.
            cur.execute("CREATE TABLE schema_migrations (id text PRIMARY KEY)")
        _connect_new_boot_path(self.db_url)      # rerun at head
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM searches WHERE q='boot-proof'")
            self.assertEqual(cur.fetchone()[0], 1)

    def _tables(self):
        import psycopg
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' ORDER BY 1")
            return [r[0] for r in cur.fetchall()]

    def _assert_refused(self):
        import psycopg
        before = self._tables()
        with self.assertRaisesRegex(Exception, "predates Alembic.*bridge-last"):
            _connect_new_boot_path(self.db_url)
        self.assertEqual(self._tables(), before, "the refusal runs no DDL")
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('turnos:migrations')::bigint)")
            self.assertTrue(cur.fetchone()[0], "the refusal releases the boot lock")

    def test_partial_spanish_schema_is_refused_before_any_ddl(self):
        import psycopg
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("CREATE TABLE solicitudes (id serial PRIMARY KEY, dni text)")
            cur.execute("INSERT INTO solicitudes (dni) VALUES ('30111222')")
        self._assert_refused()
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM solicitudes")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_complete_pre_alembic_schema_is_refused_too(self):
        import psycopg
        _build_existing_schema(self.db_url)
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("CREATE TABLE schema_migrations (id text PRIMARY KEY)")
        self._assert_refused()

    def test_alembic_only_database_gains_legacy_runner_tables_via_0006(self):
        """A database whose whole life went through Alembic under the
        pre-retirement baseline (e.g. a manual `alembic upgrade head`) never
        met the runner and lacks its three tables — revision 0006 is the
        compatibility backstop that creates them. Reproduced by upgrading to
        0005 and dropping the tables today's baseline now includes."""
        import psycopg
        from alembic import command
        command.upgrade(db_schema._alembic_config(self.db_url), "0005_demo_refresh_completion")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            for t in ("translations", "legal_docs", "chat_compactions"):
                cur.execute(f"DROP TABLE IF EXISTS {t}")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            for t in ("translations", "legal_docs", "chat_compactions"):
                cur.execute("SELECT to_regclass('public.' || %s) IS NOT NULL", (t,))
                self.assertTrue(cur.fetchone()[0],
                                f"0006 must create {t} on an Alembic-only database")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)

    def test_existing_install_gains_post_baseline_shape_via_0007(self):
        """A stamped installation receives later baseline DDL through a
        forward revision, with existing rows and repeat boots preserved."""
        import psycopg
        from alembic import command

        command.upgrade(db_schema._alembic_config(self.db_url),
                        "0006_legacy_runner_tables")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS lab_report_analysis")
            cur.execute("DROP INDEX IF EXISTS booking_requests_doctor_appointment_idx")
            cur.execute("DROP INDEX IF EXISTS chat_messages_whatsapp_tel_idx")
            cur.execute(
                "ALTER TABLE operators ALTER COLUMN role SET DEFAULT 'operador'")
            cur.execute(
                "INSERT INTO operators (username, pass_hash, role) "
                "VALUES ('existing-install-proof', 'x', 'admin')")

        _connect_new_boot_path(self.db_url)

        with psycopg.connect(self.db_url, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT to_regclass('public.lab_report_analysis')")
            self.assertIsNotNone(cur.fetchone()[0])
            for index_name in (
                    "lab_report_analysis_dni_idx",
                    "booking_requests_doctor_appointment_idx",
                    "chat_messages_whatsapp_tel_idx"):
                cur.execute(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE schemaname='public' AND indexname=%s",
                    (index_name,))
                self.assertIsNotNone(cur.fetchone(), index_name)
            cur.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='operators' "
                "AND column_name='role'")
            self.assertEqual(cur.fetchone()[0], "'medico'::text")
            cur.execute(
                "SELECT count(*) FROM operators "
                "WHERE username='existing-install-proof' AND role='admin'")
            self.assertEqual(cur.fetchone()[0], 1)

        # The second boot is a true at-head no-op and keeps the sentinel row.
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as c, c.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM operators "
                "WHERE username='existing-install-proof' AND role='admin'")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_0008_projects_legacy_hsi_mirrors_to_normalized_emr_records(self):
        """The forward migration preserves legacy observations without
        leaving Core dependent on the vendor's wire field names."""
        import psycopg
        from alembic import command
        from psycopg.types.json import Json

        command.upgrade(db_schema._alembic_config(self.db_url),
                        "0007_existing_install_shape")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO hsi_patients (hsi_patient_id,dni,payload) "
                "VALUES (%s,%s,%s)",
                ("90001", "30111222", Json({
                    "person": {"identificationNumber": "30111222",
                               "firstName": "Ana", "lastName": "Paz"},
                })))
            cur.execute(
                "INSERT INTO hsi_doctor_schedules "
                "(hsi_diary_id,hsi_professional_id,payload) VALUES (%s,%s,%s)",
                ("31", "7", Json({
                    "diary": {"specialty": "Cardiology",
                              "professional": "Dr Probe"},
                    "detail": {"appointmentDuration": 30},
                })))
            legacy_appointment = {
                "id": 5001, "diaryId": 31, "patientId": 90001,
                "date": "2026-09-02", "hour": "09:30:00",
                "appointmentStateId": 3, "updatedOn": "revision-1",
            }
            cur.execute(
                "INSERT INTO hsi_appointments "
                "(hsi_appointment_id,hsi_diary_id,hsi_patient_id,payload) "
                "VALUES (%s,%s,%s,%s)",
                ("5001", "31", "90001", Json(legacy_appointment)))
            cur.execute(
                "INSERT INTO hsi_checkpoints (entity_type,entity_id,remote) "
                "VALUES ('appointment',%s,%s)",
                ("5001", Json(legacy_appointment)))

        command.upgrade(db_schema._alembic_config(self.db_url), "head")

        with psycopg.connect(self.db_url) as c, c.cursor() as cur:
            cur.execute("SELECT payload FROM hsi_patients WHERE hsi_patient_id='90001'")
            self.assertEqual(cur.fetchone()[0], {
                "id": "90001", "document_number": "30111222",
                "given_name": "Ana", "family_name": "Paz",
            })
            cur.execute("SELECT payload FROM hsi_doctor_schedules WHERE hsi_diary_id='31'")
            self.assertEqual(cur.fetchone()[0], {
                "id": "31", "professional_id": "7",
                "specialty": "Cardiology", "professional_name": "Dr Probe",
            })
            cur.execute("SELECT payload FROM hsi_appointments WHERE hsi_appointment_id='5001'")
            appointment = cur.fetchone()[0]
            self.assertEqual(appointment["state"], "no_show")
            self.assertEqual(appointment["schedule_id"], "31")
            self.assertEqual(appointment["patient_id"], "90001")
            self.assertEqual(appointment["scheduled_at"],
                             "2026-09-02T09:30:00-03:00")
            cur.execute(
                "SELECT remote FROM hsi_checkpoints "
                "WHERE entity_type='appointment' AND entity_id='5001'")
            checkpoint = cur.fetchone()[0]
            self.assertEqual(checkpoint["state"], "no_show")
            self.assertEqual(checkpoint["version"], "revision-1")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)

        # Replaying a boot at head cannot project the already-normalized row a
        # second time or resurrect the old wire shape.
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as c, c.cursor() as cur:
            cur.execute(
                "SELECT payload FROM hsi_appointments "
                "WHERE hsi_appointment_id='5001'")
            self.assertEqual(cur.fetchone()[0]["state"], "no_show")

    def test_0007_refuses_a_same_name_wrong_index_transactionally(self):
        """IF NOT EXISTS must not bless an impostor index and advance the
        revision marker past a schema the migration cannot prove."""
        import psycopg
        from alembic import command

        command.upgrade(db_schema._alembic_config(self.db_url),
                        "0006_legacy_runner_tables")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP INDEX booking_requests_doctor_appointment_idx")
            cur.execute(
                "CREATE INDEX booking_requests_doctor_appointment_idx "
                "ON booking_requests (dni)")

        with self.assertRaisesRegex(
                RuntimeError, "could not prove the existing-install schema repair"):
            command.upgrade(db_schema._alembic_config(self.db_url), "head")

        with psycopg.connect(self.db_url) as c, c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], "0006_legacy_runner_tables")
            cur.execute(
                "SELECT pg_get_indexdef(indexrelid) "
                "FROM pg_index WHERE indexrelid = "
                "'booking_requests_doctor_appointment_idx'::regclass")
            self.assertIn("(dni)", cur.fetchone()[0])


class TestDoctorOnboardingRevision(unittest.TestCase):
    """An existing 0016 installation gains completion without losing overrides."""
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or 'sin base de datos')
        self.db_name = _dbtest.run_scoped('sana_turnos_test_alembic_onboarding')
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_existing_0016_upgrade_and_idempotent_completion(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), '0016_operator_username_history')
        with psycopg.connect(self.db_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute('ALTER TABLE operators DROP COLUMN IF EXISTS onboarding_completed_at')
            cur.execute("INSERT INTO operators (username,pass_hash,role,doctor_id) VALUES ('synthetic-doctor','not-a-password','medico','doctor')")
            cur.execute("INSERT INTO roles(slug,name,builtin,permissions) VALUES ('medico','Médico',true,ARRAY['solicitudes','cola','atencion_medica']) ON CONFLICT(slug) DO UPDATE SET permissions=excluded.permissions")
            cur.execute("INSERT INTO operator_access(username,granted) VALUES ('synthetic-doctor',ARRAY['solicitudes'])")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT onboarding_completed_at FROM operators WHERE username='synthetic-doctor'")
            self.assertIsNone(cur.fetchone()[0])
            cur.execute("SELECT permissions FROM roles WHERE slug='medico'")
            self.assertEqual(set(cur.fetchone()[0]), {'solicitudes', 'cola', 'atencion_medica'})
            cur.execute("SELECT granted FROM operator_access WHERE username='synthetic-doctor'")
            self.assertIn('solicitudes',cur.fetchone()[0])
        with _env.patched_env({'DATABASE_URL':self.db_url}):
            self.assertTrue(db_operators.operator_complete_onboarding('synthetic-doctor'))
            first=db_operators.operator_get('synthetic-doctor')['onboarding_completed_at']
            self.assertTrue(db_operators.operator_complete_onboarding('synthetic-doctor'))
            self.assertEqual(db_operators.operator_get('synthetic-doctor')['onboarding_completed_at'],first)
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute('SELECT version_num FROM alembic_version')
            self.assertEqual(cur.fetchone()[0],_HEAD)


class TestConsultaReemplazadaRevision(unittest.TestCase):
    """0027_consulta_reemplazada (problems.md №1): an install stamped at 0025
    that already holds two consultations for one interview keeps the newest
    as current, marks the older one superseded by it, and gains the
    one-current-per-interview index."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_consulta")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_existing_0025_duplicates_are_backfilled_then_indexed(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0025_patient_portal_user_id")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP INDEX IF EXISTS consultations_vigente_uniq")
            cur.execute("ALTER TABLE consultations DROP COLUMN IF EXISTS superseded_at")
            cur.execute("ALTER TABLE consultations DROP COLUMN IF EXISTS superseded_by")
            for iid, tok in ((1, "a"), (1, "b"), (2, "c")):
                cur.execute("INSERT INTO consultations (interview_id, doctor_id, receta_token, orden_token, "
                            "receta_json, notes) VALUES (%s, 'med-1', %s, %s, %s::jsonb, %s)",
                            (iid, "r" + tok, "o" + tok, '{"lineas": ["texto ' + tok + '"]}', "notas " + tok))
        # the read-only dry run counts exactly what the backfill will void
        sys.path.insert(0, os.path.join(ROOT, "scripts", "fixes"))
        try:
            import report_consultas_duplicadas as dry_run
        finally:
            sys.path.pop(0)
        from psycopg.rows import dict_row
        with psycopg.connect(self.db_url, row_factory=dict_row) as c:
            antes = dry_run.report(c)
        self.assertEqual((antes["entrevistas"], antes["a_marcar"], antes["ya_migrada"]), (1, 1, False))
        self.assertEqual([(d["interview_id"], d["mismo_contenido"]) for d in antes["detalle"]], [(1, False)])
        # the dry run is read-only: nothing changed, the schema has no marks yet
        with psycopg.connect(self.db_url) as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name='consultations' AND column_name='superseded_at'")
            self.assertEqual(cur.fetchone()[0], 0)
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url, row_factory=dict_row) as c:
            despues = dry_run.report(c)
        self.assertEqual((despues["entrevistas"], despues["a_anular"], despues["ya_migrada"]), (0, 0, True))
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT receta_token, superseded_at IS NOT NULL, superseded_by FROM consultations ORDER BY id")
            rows = cur.fetchall()
            self.assertEqual([(r[0], r[1]) for r in rows], [("ra", True), ("rb", False), ("rc", False)])
            self.assertEqual(rows[0][2], 2)
            # the backfill only marks: every row and every issued text is still there, untouched
            cur.execute("SELECT receta_token, orden_token, receta_json->'lineas'->>0, notes "
                        "FROM consultations ORDER BY id")
            self.assertEqual(cur.fetchall(), [("ra", "oa", "texto a", "notas a"),
                                              ("rb", "ob", "texto b", "notas b"),
                                              ("rc", "oc", "texto c", "notas c")])
            with self.assertRaises(psycopg.errors.UniqueViolation):
                cur.execute("INSERT INTO consultations (interview_id, doctor_id) VALUES (2, 'med-1')")


class TestPurgedFilesRevision(unittest.TestCase):
    """0029_purged_files (problems.md №11): an install stamped at 0028 without
    the journal table gains it on the next boot — one new empty table, nothing
    else read or rewritten."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_purged")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_existing_0027_install_gains_the_empty_journal(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0028_data_subject_requests")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS purged_files")
            cur.execute("INSERT INTO backup_journal (path, sha256, size, mtime_ns, remote_name) "
                        "VALUES ('ordenes/1.png', 'h', 1, 1, 'data/ordenes/1.png')")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM purged_files")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT path FROM backup_journal")
            self.assertEqual(cur.fetchall(), [("ordenes/1.png",)])


class TestDateTimeColumnsRevision(unittest.TestCase):
    """0030_date_time_columns (problems.md №13): an install stamped at 0029
    whose `slot_holds.day/time` and `fecha` columns are still text gets real
    `date` / `time` columns without the boot ever failing on a bad value."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_datetime")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def _text_install(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0029_purged_files")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER TABLE slot_holds ALTER COLUMN day TYPE text USING to_char(day, 'YYYY-MM-DD')")
            cur.execute("ALTER TABLE slot_holds ALTER COLUMN time TYPE text USING to_char(time, 'HH24:MI')")
            cur.execute("ALTER TABLE lab_report_analysis ALTER COLUMN fecha TYPE text USING fecha::text")
            cur.execute("ALTER TABLE patient_lab_uploads ALTER COLUMN fecha TYPE text USING fecha::text")
        return psycopg

    def test_text_values_are_converted_and_bad_ones_never_fail_the_boot(self):
        psycopg = self._text_install()
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            for service, day, hour, token, request_id in (
                    ("cardiologia", "2026-11-02", "9:00", "old-unlinked", None),
                    ("cardiologia", "2026-11-02", "09:00", "linked", 5),
                    ("cardiologia", "2026-11-02", "09:20", "other-time", None),
                    ("cardiologia", "2026-13-45", "09:00", "bad-day", None),
                    ("cardiologia", "2026-11-03", "25:61", "bad-time", 6),
                    ("guardia", "2026-11-02", "09:00", "other-service", None)):
                cur.execute("INSERT INTO slot_holds (service_id, day, time, token, request_id, expires) "
                            "VALUES (%s, %s, %s, %s, %s, now() + interval '1 day')",
                            (service, day, hour, token, request_id))
            for rhash, fecha in (("a", "2024-02-15"), ("b", "2024-02-15T10:30:00"),
                                 ("c", "15/02/2024"), ("d", "2024-02-30"), ("e", None)):
                cur.execute("INSERT INTO lab_report_analysis (dni, report_hash, prompt_hash, fecha) "
                            "VALUES ('1', %s, 'p', %s)", (rhash, fecha))
            for fecha in ("2023-01-09", "2024-13-45", None):
                cur.execute("INSERT INTO patient_lab_uploads (dni, uploaded_by_dni, fecha) "
                            "VALUES ('1', '1', %s)", (fecha,))
        # the read-only dry run names exactly what the revision will change
        sys.path.insert(0, os.path.join(ROOT, "scripts", "fixes"))
        try:
            import report_fechas_texto as dry_run
        finally:
            sys.path.pop(0)
        from psycopg.rows import dict_row
        with psycopg.connect(self.db_url, row_factory=dict_row) as c:
            antes = dry_run.report(c)
        self.assertEqual([d["id"] for d in antes["holds"]["a_borrar_imposibles"]], [4, 5])
        self.assertEqual([d["se_borran"] for d in antes["holds"]["a_borrar_duplicados"]], [[1]])
        self.assertEqual([d["id"] for d in antes["lab_report_analysis"]["a_normalizar"]], [2])
        self.assertEqual([d["id"] for d in antes["lab_report_analysis"]["a_vaciar"]], [3, 4])
        self.assertEqual([d["id"] for d in antes["patient_lab_uploads"]["a_vaciar"]], [2])
        self.assertEqual(antes["total_afectado"], 7)
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url, row_factory=dict_row) as c:
            self.assertEqual(dry_run.report(c)["total_afectado"], 0)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                        "WHERE (table_name='slot_holds' AND column_name IN ('day','time')) "
                        "OR (column_name='fecha' AND table_name IN ('lab_report_analysis','patient_lab_uploads')) "
                        "ORDER BY 1, 2")
            self.assertEqual(cur.fetchall(), [
                ("lab_report_analysis", "fecha", "date"),
                ("patient_lab_uploads", "fecha", "date"),
                ("slot_holds", "day", "date"),
                ("slot_holds", "time", "time without time zone")])
            cur.execute("SELECT token, to_char(day, 'YYYY-MM-DD'), to_char(time, 'HH24:MI') "
                        "FROM slot_holds ORDER BY token")
            # «9:00» and «09:00» were one slot: the linked hold stays; both impossible rows are gone
            self.assertEqual(cur.fetchall(), [("linked", "2026-11-02", "09:00"),
                                              ("other-service", "2026-11-02", "09:00"),
                                              ("other-time", "2026-11-02", "09:20")])
            # the registry of personal data knows the table the revision may create
            from app.core import personal_data
            self.assertTrue(personal_data.is_no_personal("date_time_conversion_leftovers"))
            # ... but nothing is lost: each one that left, and each value that became NULL, is set aside whole
            cur.execute("SELECT source_table, row_id, original FROM date_time_conversion_leftovers "
                        "ORDER BY source_table, row_id")
            leftovers = cur.fetchall()
            self.assertEqual([(t, i) for t, i, _o in leftovers], [
                ("lab_report_analysis", 3), ("lab_report_analysis", 4), ("patient_lab_uploads", 2),
                ("slot_holds", 1), ("slot_holds", 4), ("slot_holds", 5)])
            by_row = {(t, i): o for t, i, o in leftovers}
            self.assertEqual(by_row[("lab_report_analysis", 3)], {"fecha": "15/02/2024"})
            self.assertEqual(by_row[("slot_holds", 4)]["day"], "2026-13-45")
            self.assertEqual((by_row[("slot_holds", 1)]["token"], by_row[("slot_holds", 1)]["time"]),
                             ("old-unlinked", "9:00"))
            self.assertEqual(by_row[("slot_holds", 5)]["request_id"], 6)
            cur.execute("SELECT report_hash, fecha::text FROM lab_report_analysis ORDER BY report_hash")
            self.assertEqual(cur.fetchall(), [("a", "2024-02-15"), ("b", "2024-02-15"),
                                              ("c", None), ("d", None), ("e", None)])
            cur.execute("SELECT fecha::text FROM patient_lab_uploads ORDER BY id")
            self.assertEqual([r[0] for r in cur.fetchall()], ["2023-01-09", None, None])
            with self.assertRaises(psycopg.errors.UniqueViolation):
                cur.execute("INSERT INTO slot_holds (service_id, day, time, token, expires) "
                            "VALUES ('cardiologia', '2026-11-02', '9:00', 'x', now())")

    def test_two_linked_holds_on_one_slot_keep_both_requests_data(self):
        psycopg = self._text_install()
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            for token, hour, request_id in (("req-a", "9:00", 11), ("req-b", "09:00", 12)):
                cur.execute("INSERT INTO slot_holds (service_id, day, time, token, request_id, expires) "
                            "VALUES ('cardiologia', '2026-11-02', %s, %s, %s, now() + interval '1 day')",
                            (hour, token, request_id))
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT token, request_id FROM slot_holds")
            self.assertEqual(cur.fetchall(), [("req-b", 12)])
            cur.execute("SELECT original->>'token', original->>'request_id' FROM date_time_conversion_leftovers")
            self.assertEqual(cur.fetchall(), [("req-a", "11")])

    def test_a_half_converted_table_does_not_lose_its_valid_rows(self):
        psycopg = self._text_install()
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("INSERT INTO slot_holds (service_id, day, time, token, expires) "
                        "VALUES ('cardiologia', '2026-11-02', '10:20', 'x', now() + interval '1 day')")
            cur.execute("ALTER TABLE slot_holds ALTER COLUMN day TYPE date USING day::date")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_char(day, 'YYYY-MM-DD'), to_char(time, 'HH24:MI') FROM slot_holds")
            self.assertEqual(cur.fetchall(), [("2026-11-02", "10:20")])
            cur.execute("SELECT count(*) FROM date_time_conversion_leftovers")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_a_table_without_rows_and_a_second_boot_change_nothing(self):
        psycopg = self._text_install()
        _connect_new_boot_path(self.db_url)
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT data_type FROM information_schema.columns "
                        "WHERE table_name='slot_holds' AND column_name='time'")
            self.assertEqual(cur.fetchone()[0], "time without time zone")


class TestPromptTextEnRevision(unittest.TestCase):
    """0031_ai_prompts_text_en (problems.md №14): `texto_en` becomes `text_en`
    in `ai_prompts` and `ai_prompts_history` next to `text_es`. A pure RENAME:
    every row and every instruction survives, and an install that already has
    the new name boots without touching anything."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_text_en")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def _columns(self, cur, table):
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s", (table,))
        return {r[0] for r in cur.fetchall()}

    def test_existing_0027_install_keeps_its_prompts_under_the_new_name(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0030_date_time_columns")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER TABLE ai_prompts RENAME COLUMN text_en TO texto_en")
            cur.execute("ALTER TABLE ai_prompts_history RENAME COLUMN text_en TO texto_en")
            cur.execute("INSERT INTO ai_prompts (key, texto_en, text_es, edited) "
                        "VALUES ('orden_analisis', 'Read the referral.', 'Leé la orden.', true)")
            cur.execute("INSERT INTO ai_prompts_history (key, texto_en, text_es) "
                        "VALUES ('orden_analisis', 'Older text.', 'Texto anterior.')")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            for table in ("ai_prompts", "ai_prompts_history"):
                cols = self._columns(cur, table)
                self.assertIn("text_en", cols, table)
                self.assertNotIn("texto_en", cols, table)
                self.assertIn("text_es", cols, table)
            cur.execute("SELECT text_en, text_es, edited FROM ai_prompts WHERE key='orden_analisis'")
            self.assertEqual(cur.fetchone(), ("Read the referral.", "Leé la orden.", True))
            cur.execute("SELECT text_en, text_es FROM ai_prompts_history WHERE key='orden_analisis'")
            self.assertEqual(cur.fetchone(), ("Older text.", "Texto anterior."))

    def test_install_already_on_the_new_name_boots_untouched(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0030_date_time_columns")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("INSERT INTO ai_prompts (key, text_en, text_es, edited) "
                        "VALUES ('orden_analisis', 'Read the referral.', 'Leé la orden.', true)")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT text_en FROM ai_prompts WHERE key='orden_analisis'")
            self.assertEqual(cur.fetchone()[0], "Read the referral.")


class TestResetTokenHashRevision(unittest.TestCase):
    """0033_reset_token_hash: an install stamped at 0032 whose `reset_tokens`
    still holds raw tokens keeps the links that are still open (hashed in
    place, so an emailed link keeps working) and drops used/expired rows; no
    raw token is left, and a rerun never hashes a hash."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_resettoken")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_open_raw_tokens_are_hashed_and_dead_ones_removed(self):
        import hashlib
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0032_foreign_keys")
        raw_open = "A" * 43
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("INSERT INTO reset_tokens (token, dni, expires, used) VALUES "
                        "(%s, '30111111', now() + interval '30 minutes', false), "
                        "('usado-' || repeat('b', 37), '30111111', now() + interval '30 minutes', true), "
                        "('vencido-' || repeat('c', 35), '30111111', now() - interval '5 minutes', false)",
                        (raw_open,))
        _connect_new_boot_path(self.db_url)
        digest = hashlib.sha256(raw_open.encode()).hexdigest()
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT token, dni, used FROM reset_tokens")
            self.assertEqual(cur.fetchall(), [(digest, "30111111", False)])
        # the stored value is exactly what the code looks the emailed link up by
        self.assertEqual(digest, db_accounts._reset_token_digest(raw_open))

    def test_rerun_does_not_hash_a_hash(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0032_foreign_keys")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("INSERT INTO reset_tokens (token, dni, expires) "
                        "VALUES (repeat('d', 43), '30111111', now() + interval '10 minutes')")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as c, c.cursor() as cur:
            cur.execute("SELECT token FROM reset_tokens")
            once = cur.fetchone()[0]
        # run the revision again for real against the already converted row
        command.stamp(db_schema._alembic_config(self.db_url), "0032_foreign_keys")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as c, c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT token FROM reset_tokens")
            self.assertEqual(cur.fetchall(), [(once,)])


class TestBackupPurgeStateRevision(unittest.TestCase):
    """0034_backup_purge_state: an install stamped at 0033 gains the empty
    one-row table the B2 sweep leaves for «Sistema»; nothing else is touched."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_purgestate")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_existing_0033_install_gains_the_empty_table(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0033_reset_token_hash")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS backup_purge_state")
            cur.execute("INSERT INTO purged_files (path, reason) VALUES ('ordenes/1.png', 'retention')")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM backup_purge_state")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT path FROM purged_files")
            self.assertEqual(cur.fetchall(), [("ordenes/1.png",)])


class TestAdminSessionRevocationsRevision(unittest.TestCase):
    """0035_admin_session_revocations: an install stamped at 0034 gains the empty table
    «Cerrar sesión» writes to; nothing else is touched."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_sessrev")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_existing_0034_install_gains_the_empty_table(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0034_backup_purge_state")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS admin_session_revocations")
            cur.execute("INSERT INTO purged_files (path, reason) VALUES ('ordenes/2.png', 'retention')")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM admin_session_revocations")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT path FROM purged_files")
            self.assertEqual(cur.fetchall(), [("ordenes/2.png",)])


class TestLaunchSignalCountersRevision(unittest.TestCase):
    """0036_launch_signal_counters: an install stamped at 0035 gains the empty counter table; nothing else
    is touched."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_launchsig")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_existing_0035_install_gains_the_empty_table(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0035_admin_session_revocations")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS launch_signal_counters")
            cur.execute("INSERT INTO purged_files (path, reason) VALUES ('ordenes/3.png', 'retention')")
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT count(*) FROM launch_signal_counters")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'launch_signal_counters' ORDER BY ordinal_position")
            self.assertEqual([r[0] for r in cur.fetchall()], ["bucket", "code", "n"])
            cur.execute("SELECT path FROM purged_files")
            self.assertEqual(cur.fetchall(), [("ordenes/3.png",)])


class TestBookingRequestedAtRevision(unittest.TestCase):
    """0037_booking_requested_at: a request keeps the appointment it asked
    for after its hold is gone (the product owner 26.10: a rejected request said no date).
    An install at 0036 gains the column; a pending request with its hold still
    linked takes the hold's wall-clock day and time; one without a hold keeps
    NULL — nothing is invented."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_requestedat")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def test_existing_0036_install_backfills_from_the_linked_hold(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0036_launch_signal_counters")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER TABLE booking_requests DROP COLUMN IF EXISTS requested_at")
            cur.execute("INSERT INTO booking_requests (service_id, status) VALUES ('s', 'pendiente') RETURNING id")
            con_hold = cur.fetchone()[0]
            cur.execute("INSERT INTO booking_requests (service_id, status) VALUES ('s', 'rechazada') RETURNING id")
            sin_hold = cur.fetchone()[0]
            cur.execute("INSERT INTO slot_holds (service_id, day, time, token, request_id, expires)"
                        " VALUES ('s', '2026-10-27', '12:00', 't', %s, now() + interval '1 day')", (con_hold,))
        _connect_new_boot_path(self.db_url)
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT id, to_char(requested_at AT TIME ZONE 'America/Argentina/Buenos_Aires',"
                        " 'YYYY-MM-DD HH24:MI') FROM booking_requests ORDER BY id")
            self.assertEqual(cur.fetchall(), [(con_hold, "2026-10-27 12:00"), (sin_hold, None)])


class TestMcpTokenHashRevision(unittest.TestCase):
    """0038_mcp_token_hash: an install at 0037 whose `users.mcp_token` holds
    raw tokens gets them hashed IN PLACE with their last 4 characters in the
    new `mcp_token_hint`, so an assistant already holding a token keeps working
    without the patient regenerating it; an empty token becomes NULL; no raw
    token is left, and a rerun never hashes a hash nor rewrites a hint."""

    _RAW = "Zx9_" + "k" * 24 + "-Q7w"  # the shape of secrets.token_urlsafe(24): 32 url-safe characters

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_mcptoken")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def _seed_0037(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0037_booking_requested_at")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER TABLE users DROP COLUMN IF EXISTS mcp_token_hint")
            cur.execute("INSERT INTO users (dni, mcp_token) VALUES ('30111111', %s), ('30222222', ''),"
                        " ('30333333', NULL)", (self._RAW,))

    def _users(self):
        import psycopg
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT dni, mcp_token, mcp_token_hint FROM users ORDER BY dni")
            return cur.fetchall()

    def test_raw_tokens_are_hashed_in_place_and_keep_their_hint(self):
        import hashlib
        self._seed_0037()
        _connect_new_boot_path(self.db_url)
        digest = hashlib.sha256(self._RAW.encode("utf-8")).hexdigest()
        self.assertEqual(self._users(), [
            ("30111111", digest, "-Q7w"),
            ("30222222", None, None),
            ("30333333", None, None),
        ])
        # the stored value is exactly what the code looks the Bearer token up by
        self.assertEqual(digest, db_accounts._mcp_token_digest(self._RAW))

    def test_rerun_does_not_hash_a_hash_nor_rewrite_the_hint(self):
        from alembic import command
        self._seed_0037()
        _connect_new_boot_path(self.db_url)
        once = self._users()
        command.stamp(db_schema._alembic_config(self.db_url), "0037_booking_requested_at")
        _connect_new_boot_path(self.db_url)
        self.assertEqual(self._users(), once)


class TestFeedTokenHashRevision(unittest.TestCase):
    """0040_feed_token_hash: an install at 0039 whose `users.feed_token` (a
    patient's calendar address) and `operators.feed_token` (a doctor's) hold
    raw tokens gets them hashed IN PLACE with their last 4 characters in the
    new `feed_token_hint` columns, so a calendar app already subscribed keeps
    updating without anyone re-subscribing; an empty token becomes NULL; no raw
    token is left, and a rerun never hashes a hash nor rewrites a hint."""

    _RAW = "Ab3_" + "k" * 14 + "-Fd9"  # the shape of secrets.token_urlsafe(16): 22 url-safe characters
    _RAW_DOC = "Dc7-" + "m" * 14 + "_Z2x"

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_feedtoken")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def _seed_0039(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0039_booking_case_chat")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER TABLE users DROP COLUMN IF EXISTS feed_token_hint")
            cur.execute("ALTER TABLE operators DROP COLUMN IF EXISTS feed_token_hint")
            cur.execute("INSERT INTO users (dni, feed_token) VALUES ('30111111', %s), ('30222222', ''),"
                        " ('30333333', NULL)", (self._RAW,))
            cur.execute("INSERT INTO operators (username, pass_hash, feed_token) VALUES ('ana', 'x', %s),"
                        " ('beto', 'x', ''), ('carla', 'x', NULL)", (self._RAW_DOC,))

    def _rows(self):
        import psycopg
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT dni, feed_token, feed_token_hint FROM users ORDER BY dni")
            users = cur.fetchall()
            cur.execute("SELECT username, feed_token, feed_token_hint FROM operators ORDER BY username")
            return users, cur.fetchall()

    def test_raw_tokens_are_hashed_in_place_and_keep_their_hint(self):
        import hashlib
        self._seed_0039()
        _connect_new_boot_path(self.db_url)
        digest = hashlib.sha256(self._RAW.encode("utf-8")).hexdigest()
        digest_doc = hashlib.sha256(self._RAW_DOC.encode("utf-8")).hexdigest()
        users, operators = self._rows()
        self.assertEqual(users, [
            ("30111111", digest, "-Fd9"),
            ("30222222", None, None),
            ("30333333", None, None),
        ])
        self.assertEqual(operators, [
            ("ana", digest_doc, "_Z2x"),
            ("beto", None, None),
            ("carla", None, None),
        ])
        # the stored value is exactly what the code looks the address up by
        self.assertEqual(digest, feed_tokens.digest(self._RAW))
        self.assertEqual(digest_doc, feed_tokens.digest(self._RAW_DOC))

    def test_rerun_does_not_hash_a_hash_nor_rewrite_the_hint(self):
        from alembic import command
        self._seed_0039()
        _connect_new_boot_path(self.db_url)
        once = self._rows()
        command.stamp(db_schema._alembic_config(self.db_url), "0039_booking_case_chat")
        _connect_new_boot_path(self.db_url)
        self.assertEqual(self._rows(), once)


class TestBookingReminderSkippedRevision(unittest.TestCase):
    """0041_booking_reminder_skipped: an install at 0040 gains
    `booking_requests.reminder_skipped`, false for every existing request (a
    claimed reminder of before cannot be told apart and stays counted as
    sent); a rerun changes nothing (the product owner's hub, 2026-11-10)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_alembic_reminderskipped")
        self.db_url = _fresh_db_url(self.db_name)
        self.addCleanup(_drop_db, self.db_name)

    def _rows(self):
        import psycopg
        with psycopg.connect(self.db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()[0], _HEAD)
            cur.execute("SELECT reminder_at IS NOT NULL, reminder_skipped FROM booking_requests ORDER BY id")
            return cur.fetchall()

    def test_existing_0040_install_gains_the_column_false(self):
        import psycopg
        from alembic import command
        _build_existing_schema(self.db_url)
        command.stamp(db_schema._alembic_config(self.db_url), "0040_feed_token_hash")
        with psycopg.connect(self.db_url, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER TABLE booking_requests DROP COLUMN IF EXISTS reminder_skipped")
            cur.execute("INSERT INTO booking_requests (service_id, status, reminder_at)"
                        " VALUES ('s', 'aprobada', now()), ('s', 'aprobada', NULL)")
        _connect_new_boot_path(self.db_url)
        self.assertEqual(self._rows(), [(True, False), (False, False)])
        command.stamp(db_schema._alembic_config(self.db_url), "0040_feed_token_hash")
        _connect_new_boot_path(self.db_url)
        self.assertEqual(self._rows(), [(True, False), (False, False)])
