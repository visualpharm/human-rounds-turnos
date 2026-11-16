"""Characterization of `db_schema._ensure_schema` (the boot-time schema bootstrap) before it was cut into steps.

It pins what a restructure must not move: the order of the side effects (take the cross-process
advisory lock, refuse a pre-Alembic database, commit, the Alembic upgrade, release the lock, then
trigrams and the built-in roles), the schema a
fresh and an already-initialised database end up with, and the failure paths (the lock is released
and the schema is not marked as ensured). Each scenario runs on its own scratch database, named after
the lane's own database, and drops it afterwards.
"""

import os
import pathlib
import sys
import unittest
from unittest import mock
from urllib.parse import urlsplit, urlunsplit

import _env

_env.process_defaults(TURNOS_ENV_FILE=os.devnull, TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

import _dbtest  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.core import permissions  # noqa: E402


def _lane_url():
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def _url_for(lane_url, name):
    p = urlsplit(lane_url)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def _script_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def catalog_dump(url):
    """Everything the bootstrap leaves behind, read from the catalog: tables with their columns,
    indexes, constraints, triggers, functions, sequences, extensions, the Alembic row and the
    built-in roles. Plain lists, so two dumps compare with ==."""
    queries = {
        "columns": "SELECT table_name, column_name, data_type, is_nullable, column_default "
                   "FROM information_schema.columns WHERE table_schema = 'public' "
                   "ORDER BY table_name, ordinal_position",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes "
                   "WHERE schemaname = 'public' ORDER BY tablename, indexname",
        "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                       "FROM pg_constraint WHERE connamespace = 'public'::regnamespace "
                       "ORDER BY 1, 2",
        "triggers": "SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) "
                    "FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1, 2",
        "functions": "SELECT proname, pg_get_functiondef(oid) FROM pg_proc "
                     "WHERE pronamespace = 'public'::regnamespace ORDER BY 1",
        "sequences": "SELECT sequencename, data_type::text, start_value, increment_by "
                     "FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1",
        "extensions": "SELECT extname FROM pg_extension ORDER BY 1",
        "alembic": "SELECT version_num FROM alembic_version ORDER BY 1",
        "roles": "SELECT slug, name, description, builtin, enabled, permissions FROM roles ORDER BY slug",
    }
    out = {}
    with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
        for key, sql in queries.items():
            cur.execute(sql)
            out[key] = [[str(v) for v in row] for row in cur.fetchall()]
        cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND "
                    "database = (SELECT oid FROM pg_database WHERE datname = current_database())")
        out["advisory_locks_in_this_database"] = cur.fetchone()[0]
    return out


class _Cursor:
    def __init__(self, real, events):
        self._real, self._events = real, events

    def execute(self, sql, *args, **kwargs):
        text = str(sql)
        if "pg_advisory_unlock" in text:
            self._events.append("unlock")
        elif "pg_advisory_lock" in text:
            self._events.append("lock")
        return self._real.execute(sql, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Conn:
    """The caller's connection, seen by the bootstrap; logs lock statements and commits."""

    def __init__(self, real, events):
        self._real, self.events = real, events

    def cursor(self, *args, **kwargs):
        return _Cursor(self._real.cursor(*args, **kwargs), self.events)

    def commit(self):
        self.events.append("commit")
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Base(unittest.TestCase):
    def setUp(self):
        self.lane = _lane_url()
        if not self.lane:
            self.skipTest("TEST_DATABASE_URL not configured")
        _env.set_env(self, TURNOS_ENV_FILE=os.devnull, TURNOS_DB_MIGRATION_URL=None)
        # The lane database name already carries a run token; a short fixed base keeps the name under 63.
        self.name = _dbtest.run_scoped("sana_turnos_test_ens")
        try:
            with psycopg.connect(self.lane, autocommit=True, connect_timeout=5) as c:
                c.execute('CREATE DATABASE "%s"' % self.name)
        except psycopg.Error as e:
            self.skipTest("cannot create a scratch database: %s" % e)
        self.url = _url_for(self.lane, self.name)
        self.addCleanup(self._drop)
        db_schema._ensured.discard(self.url)
        self.addCleanup(db_schema._ensured.discard, self.url)

    def _drop(self):
        with psycopg.connect(self.lane, autocommit=True, connect_timeout=5) as c:
            c.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % self.name)

    def _spy(self, owner, name, label, fake=None, fmt=None):
        """Replace `owner.name` with a wrapper that logs `label` and then calls the real function
        (or `fake`)."""
        real = getattr(owner, name)
        events = self.events

        def wrapper(*args, **kwargs):
            events.append(fmt(*args, **kwargs) if fmt else label)
            return (fake or real)(*args, **kwargs)

        patcher = mock.patch.object(owner, name, new=wrapper)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _instrument(self, owner_url=None, upgrade_error=None):
        self.events = []
        events = self.events
        self._spy(db_schema, "_refuse_pre_alembic", "refuse")
        self._spy(db_schema, "_alembic_version_present", "av_present")
        self._spy(db_schema, "_migration_url", "migration_url",
                  fake=(lambda: owner_url or ""))
        self._spy(db_schema, "_owner_conn_same_db", "owner_conn")
        real_upgrade = db_schema._run_alembic_upgrade

        def upgrade(conn, url):
            # Alembic works on its own connection: an open read transaction here deadlocks the boot
            status = conn.info.transaction_status
            if status != psycopg.pq.TransactionStatus.IDLE:
                raise AssertionError("the caller's read transaction is still open: %s" % status.name)
            if upgrade_error:
                raise upgrade_error
            return real_upgrade(conn, url)

        self._spy(db_schema, "_run_alembic_upgrade", None, fake=upgrade,
                  fmt=lambda conn, url: "upgrade(owner_url=%s)" % (url == owner_url))
        self._spy(db_schema, "_apply_trigrams", None,
                  fmt=lambda conn: "trigrams(%s)" % ("app" if isinstance(conn, _Conn) else "owner"))
        self._spy(db_schema, "_seed_builtin_roles", "seed_roles")
        return events

    def _boot(self, **kw):
        events = self._instrument(**kw)
        with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as real:
            conn = _Conn(real, events)
            try:
                db_schema._ensure_schema(conn, self.url)
            finally:
                # an open session must not be left holding the migrations key
                with psycopg.connect(self.url, connect_timeout=5) as other:
                    self.key_free = other.execute(
                        "SELECT pg_try_advisory_lock(hashtext('turnos:migrations')::bigint)").fetchone()[0]
        return list(events)

    def _build_pre_alembic(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("CREATE TABLE schema_migrations (id text PRIMARY KEY)")


# The trailing "commit" pairs are the ones `_apply_trigrams` and `_seed_builtin_roles` issue themselves.
_FRESH = [
    "lock", "refuse", "av_present", "commit", "migration_url",
    "upgrade(owner_url=False)", "unlock", "commit", "migration_url",
    "trigrams(app)", "commit", "seed_roles", "commit",
]


class TestFreshDatabase(_Base):
    def test_order_of_side_effects_and_resulting_schema(self):
        events = self._boot()
        self.assertEqual(events, _FRESH)
        self.assertTrue(self.key_free, "the bootstrap left its advisory lock held")
        self.assertIn(self.url, db_schema._ensured)
        dump = catalog_dump(self.url)
        self.assertEqual(dump["alembic"], [[_script_head()]])
        tables = {row[0] for row in dump["columns"]}
        self.assertTrue({"users", "booking_requests", "roles", "alembic_version"} <= tables)
        self.assertNotIn("schema_migrations", tables)
        self.assertEqual({row[0] for row in dump["roles"]}, set(permissions.BUILTIN_ROLES))
        self.assertEqual(dump["advisory_locks_in_this_database"], 0)

    def test_matches_the_committed_snapshot(self):
        self._boot()
        import importlib.util
        spec = importlib.util.spec_from_file_location("schema_snapshot", ROOT / "scripts" / "schema_snapshot.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with psycopg.connect(self.url, connect_timeout=5) as conn:
            text = mod.snapshot(conn)
        self.assertEqual(text, (ROOT / "alembic" / "schema-head.txt").read_text(encoding="utf-8"))


class TestAlreadyInitialisedDatabase(_Base):
    def test_second_boot_changes_nothing(self):
        self._boot()
        before = catalog_dump(self.url)
        db_schema._ensured.discard(self.url)
        events = self._boot()
        self.assertEqual(events, [
            "lock", "refuse", "av_present", "commit", "migration_url",
            "upgrade(owner_url=False)", "unlock", "commit", "migration_url",
            "trigrams(app)", "commit", "seed_roles", "commit"])
        self.assertEqual(catalog_dump(self.url), before)
        self.assertTrue(self.key_free)

    def test_a_url_already_ensured_does_nothing(self):
        self._boot()
        events = self._boot()
        self.assertEqual(events, [])

    def test_seed_does_not_overwrite_an_edited_builtin_role(self):
        self._boot()
        with psycopg.connect(self.url, autocommit=True) as c:
            c.execute("UPDATE roles SET name = 'Edited name' WHERE slug = %s",
                      (sorted(permissions.BUILTIN_ROLES)[0],))
        db_schema._ensured.discard(self.url)
        self._boot()
        with psycopg.connect(self.url, connect_timeout=5) as c:
            names = [r[0] for r in c.execute("SELECT name FROM roles WHERE slug = %s",
                                             (sorted(permissions.BUILTIN_ROLES)[0],))]
        self.assertEqual(names, ["Edited name"])


class TestPreAlembicDatabase(_Base):
    def test_is_refused_before_the_upgrade_and_releases_the_lock(self):
        self._build_pre_alembic()
        with self.assertRaisesRegex(RuntimeError, "predates Alembic"):
            self._boot()
        self.assertEqual(self.events, ["lock", "refuse", "av_present", "unlock", "commit"])
        self.assertNotIn(self.url, db_schema._ensured)
        self.assertTrue(self.key_free)
        with psycopg.connect(self.url, connect_timeout=5) as c:
            tables = [r[0] for r in c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")]
        self.assertEqual(tables, ["schema_migrations"], "the refusal runs no DDL")


class TestOwnerRole(_Base):
    def test_owner_connection_is_probed_then_runs_alembic_and_trigrams(self):
        # same database, but a URL that differs from the app's, so the one handed to Alembic is visible
        owner_url = self.url + ("&" if "?" in self.url else "?") + "application_name=schema_owner"
        events = self._boot(owner_url=owner_url)
        self.assertEqual(events, [
            "lock", "refuse", "av_present", "commit", "migration_url", "owner_conn", "commit",
            "upgrade(owner_url=True)", "unlock", "commit", "migration_url", "owner_conn",
            "trigrams(owner)", "seed_roles", "commit"])
        self.assertTrue(self.key_free)


class TestFailurePaths(_Base):
    def test_a_failed_upgrade_releases_the_lock_and_leaves_the_schema_unensured(self):
        boom = RuntimeError("upgrade exploded")
        with self.assertRaises(RuntimeError) as ctx:
            self._boot(upgrade_error=boom)
        self.assertIs(ctx.exception, boom)
        self.assertEqual(self.events[-2:], ["unlock", "commit"])
        self.assertNotIn("trigrams(app)", self.events)
        self.assertNotIn("seed_roles", self.events)
        self.assertNotIn(self.url, db_schema._ensured)
        self.assertTrue(self.key_free)

    def test_a_failed_refusal_check_releases_the_lock(self):
        self.events = []
        with mock.patch.object(db_schema, "_alembic_version_present", side_effect=ValueError("no check")):
            with self.assertRaises(ValueError):
                with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as real:
                    db_schema._ensure_schema(real, self.url)
        with psycopg.connect(self.url, connect_timeout=5) as other:
            self.assertTrue(other.execute(
                "SELECT pg_try_advisory_lock(hashtext('turnos:migrations')::bigint)").fetchone()[0])
        self.assertNotIn(self.url, db_schema._ensured)


if __name__ == "__main__":
    unittest.main()
