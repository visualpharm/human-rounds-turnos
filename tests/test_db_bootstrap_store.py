"""test_db_bootstrap_store.py — the locks and the boot-time helpers of `core/db.py`
against a real Postgres.

Stage 3 of the SQLAlchemy Core move leaves no hand-written SQL in its slice: the
advisory locks (`prompt_save`, `prompt_delete`, `catalog_upsert`,
`catalog_set_boolean_many`, `catalog_schedule_lock`), the schema-owner probe
(`_owner_conn_same_db`), the Alembic bookkeeping reads (`_alembic_version_present`,
`migrations_status`), the optional trigram indexes (`_apply_trigrams`) and the factory
roles seed (`seed_builtin_roles`). This file pins what each does — above all WHICH
lock key each takes, since a lock that silently stopped covering its key would pass
every functional test — written and green on the hand-written SQL first, unedited on
Core.
"""
import io
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_ai  # noqa: E402
from app.core import db_schema  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_bootstrap")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE ai_prompts, ai_prompts_history, ai_register_changes, catalog")

    def bare_database(self, suffix):
        """An empty database next to the test one; dropped on cleanup."""
        name = _dbtest.run_scoped("sana_turnos_test_boot_" + suffix)
        with psycopg.connect(_db.maint_url, autocommit=True, connect_timeout=5) as c:
            c.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
            c.execute("CREATE DATABASE %s" % name)

        def drop():
            with psycopg.connect(_db.maint_url, autocommit=True, connect_timeout=5) as c:
                c.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)

        self.addCleanup(drop)
        p = urlsplit(_db.test_url)
        return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


# ---- which advisory lock does each function take -----------------------------------

def _hold(sql, params):
    """Context manager: a transaction on its own connection holding an advisory lock."""
    class Holder:
        def __enter__(self):
            self.cm = db._connect()
            self.conn = self.cm.__enter__()
            self.conn.cursor().execute(sql, params)
            return self

        def __exit__(self, *exc):
            return self.cm.__exit__(*exc)  # commits: the lock goes

    return Holder()


class TestLockKeys(DbCase):
    def blocks_while_held(self, sql, params, call):
        """True if `call()` waits for the advisory lock `sql` takes, and finishes after it goes."""
        result = {}

        def worker():
            try:
                call()
                result["done"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        with _hold(sql, params):
            thread.start()
            thread.join(0.7)
            blocked = thread.is_alive()
        thread.join(15)
        self.assertFalse(thread.is_alive(), "the call never finished after the lock went")
        self.assertEqual(result, {"done": True})
        return blocked

    PROMPT_LOCK = "SELECT pg_advisory_xact_lock(hashtext('ai_prompt:' || %s))"
    CATALOG_LOCK = "SELECT pg_advisory_xact_lock(hashtext('ai_catalog:' || %s || ':' || %s))"

    def test_prompt_save_takes_the_lock_of_its_key_only(self):
        self.assertTrue(self.blocks_while_held(
            self.PROMPT_LOCK, ("k1",), lambda: db_ai.prompt_save("k1", "en", "es", True)))
        self.assertFalse(self.blocks_while_held(
            self.PROMPT_LOCK, ("other",), lambda: db_ai.prompt_save("k1", "en", "es", True)))

    def test_prompt_delete_takes_the_lock_of_its_key_only(self):
        db_ai.prompt_save("k1", "en", "es", True)
        self.assertTrue(self.blocks_while_held(
            self.PROMPT_LOCK, ("k1",), lambda: db_ai.prompt_delete("k1")))
        self.assertFalse(self.blocks_while_held(
            self.PROMPT_LOCK, ("other",), lambda: db_ai.prompt_delete("k1")))

    def test_catalog_upsert_locks_a_doctor_and_the_voice_config_but_nothing_else(self):
        self.assertTrue(self.blocks_while_held(
            self.CATALOG_LOCK, ("medicos", "7"),
            lambda: db_catalog.catalog_upsert("medicos", "7", {"nombre": "A"})))
        self.assertFalse(self.blocks_while_held(
            self.CATALOG_LOCK, ("medicos", "8"),
            lambda: db_catalog.catalog_upsert("medicos", "7", {"nombre": "A"})))
        self.assertTrue(self.blocks_while_held(
            self.CATALOG_LOCK, ("config", "chat_voice"),
            lambda: db_catalog.catalog_upsert("config", "chat_voice", {"model": "m"})))
        self.assertFalse(self.blocks_while_held(
            self.CATALOG_LOCK, ("config", "settings"),
            lambda: db_catalog.catalog_upsert("config", "settings", {"k": "v"})))
        self.assertFalse(self.blocks_while_held(
            self.CATALOG_LOCK, ("servicios", "7"),
            lambda: db_catalog.catalog_upsert("servicios", "7", {"nombre": "S"})))

    def test_catalog_set_boolean_many_locks_each_doctor_for_the_interview_flag_only(self):
        entries = [("7", {"nombre": "A"})]
        self.assertTrue(self.blocks_while_held(
            self.CATALOG_LOCK, ("medicos", "7"),
            lambda: db_catalog.catalog_set_boolean_many("medicos", entries, "entrevista_habilitada", True)))
        self.assertFalse(self.blocks_while_held(
            self.CATALOG_LOCK, ("medicos", "7"),
            lambda: db_catalog.catalog_set_boolean_many("medicos", entries, "other_flag", True)))
        self.assertFalse(self.blocks_while_held(
            self.CATALOG_LOCK, ("medicos", "8"),
            lambda: db_catalog.catalog_set_boolean_many("medicos", entries, "entrevista_habilitada", True)))

    def try_schedule_lock(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('catalog-schedule-ownership')) AS got")
            return cur.fetchone()["got"]

    def test_the_schedule_lock_is_held_for_the_whole_with_block(self):
        self.assertTrue(self.try_schedule_lock())
        with db_catalog.catalog_schedule_lock() as conn:
            self.assertTrue(hasattr(conn, "cursor"))
            self.assertFalse(self.try_schedule_lock())
            db_catalog.catalog_upsert("centros", "a", {"n": 1}, connection=conn)  # same transaction
        self.assertTrue(self.try_schedule_lock())
        self.assertEqual(db_catalog.catalog_get("centros", "a")["doc"], {"n": 1})

    def test_the_schedule_lock_is_released_when_the_block_raises(self):
        with self.assertRaises(RuntimeError):
            with db_catalog.catalog_schedule_lock():
                raise RuntimeError("boom")
        self.assertTrue(self.try_schedule_lock())


# ---- Alembic bookkeeping reads ---------------------------------------------------------

def _head():
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(db_schema._alembic_config(db._db_url())).get_current_head()


class TestAlembicBookkeeping(DbCase):
    def conn_to(self, url):
        conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=5)
        self.addCleanup(conn.close)
        return conn

    def test_a_booted_database_reports_the_head(self):
        conn = self.conn_to(_db.test_url)
        self.assertIs(db_schema._alembic_version_present(conn), True)
        self.assertEqual(db.migrations_status(conn), (True, _head(), _head()))

    def test_a_database_without_the_version_table_reports_none(self):
        conn = self.conn_to(self.bare_database("noav"))
        self.assertIs(db_schema._alembic_version_present(conn), False)
        self.assertEqual(db.migrations_status(conn), (False, None, _head()))

    def test_a_version_table_without_a_row_is_not_present(self):
        conn = self.conn_to(self.bare_database("emptyav"))
        conn.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        conn.commit()
        self.assertIs(db_schema._alembic_version_present(conn), False)
        self.assertEqual(db.migrations_status(conn), (False, None, _head()))

    def test_a_stale_version_is_not_up_to_date(self):
        conn = self.conn_to(self.bare_database("stale"))
        conn.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('0001_baseline')")
        conn.commit()
        self.assertIs(db_schema._alembic_version_present(conn), True)
        self.assertEqual(db.migrations_status(conn), (False, "0001_baseline", _head()))


# ---- schema-owner probe ---------------------------------------------------------------------

class TestOwnerProbe(DbCase):
    def test_the_same_database_returns_a_usable_owner_connection(self):
        with psycopg.connect(_db.test_url, row_factory=dict_row) as conn:
            owner = db_schema._owner_conn_same_db(conn, _db.test_url)
            try:
                self.assertEqual(owner.execute("SELECT 41 + 1 AS n").fetchone()["n"], 42)
            finally:
                owner.close()

    def test_another_database_is_refused_and_the_owner_connection_closed(self):
        other = self.bare_database("owner")
        with psycopg.connect(_db.test_url, row_factory=dict_row) as conn:
            with self.assertRaises(RuntimeError) as caught:
                db_schema._owner_conn_same_db(conn, other)
        self.assertIn("points at another database", str(caught.exception))


# ---- optional trigram indexes ----------------------------------------------------------------

TRIGRAM_INDEXES = ("users_first_name_trgm_idx", "users_last_name_trgm_idx", "users_dni_trgm_idx")


class TestTrigrams(DbCase):
    def indexes(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'users' "
                        "AND indexname LIKE '%%trgm%%' ORDER BY indexname")
            return {r["indexname"]: r["indexdef"] for r in cur.fetchall()}

    def setUp(self):
        super().setUp()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok FROM pg_available_extensions WHERE name = 'pg_trgm'")
            if not cur.fetchone():
                self.skipTest("pg_trgm is not installed on this server")
            for name in TRIGRAM_INDEXES:
                cur.execute("DROP INDEX IF EXISTS %s" % name)

    def test_creates_the_extension_and_the_three_gin_indexes_idempotently(self):
        self.assertEqual(self.indexes(), {})
        with psycopg.connect(_db.test_url, row_factory=dict_row) as conn:
            db_schema._apply_trigrams(conn)
            db_schema._apply_trigrams(conn)
        found = self.indexes()
        self.assertEqual(sorted(found), sorted(TRIGRAM_INDEXES))
        self.assertIn("USING gin (first_name gin_trgm_ops)", found["users_first_name_trgm_idx"])
        self.assertIn("USING gin (last_name gin_trgm_ops)", found["users_last_name_trgm_idx"])
        self.assertIn("USING gin (dni gin_trgm_ops)", found["users_dni_trgm_idx"])

    def test_a_failure_prints_and_never_raises(self):
        conn = psycopg.connect(_db.test_url, row_factory=dict_row)
        conn.close()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertIsNone(db_schema._apply_trigrams(conn))
        self.assertIn("pg_trgm no disponible", out.getvalue())
        self.assertEqual(self.indexes(), {})


# ---- factory roles seed -------------------------------------------------------------------------

class TestRolesSeed(DbCase):
    def roles(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT slug, name, description, builtin, permissions FROM roles ORDER BY slug")
            return cur.fetchall()

    def test_seed_inserts_the_factory_roles_and_never_overwrites(self):
        from app.core import permissions
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM roles")
        db_operators.seed_builtin_roles()
        expected = [(slug, d["name"], d.get("description", ""), True, list(d.get("permissions", ())))
                    for slug, d in sorted(permissions.BUILTIN_ROLES.items())]
        self.assertEqual([(r["slug"], r["name"], r["description"], r["builtin"], r["permissions"])
                          for r in self.roles()], expected)
        slug = expected[0][0]
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE roles SET description = 'edited by the owner' WHERE slug = %s", (slug,))
        db_operators.seed_builtin_roles()
        db_operators.seed_builtin_roles()
        roles = self.roles()
        self.assertEqual(len(roles), len(expected))
        self.assertEqual([r["description"] for r in roles if r["slug"] == slug], ["edited by the owner"])


if __name__ == "__main__":
    unittest.main()
