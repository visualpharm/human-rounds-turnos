"""test_db_app_role.py — la aplicación corre con un rol que no puede cambiar
el esquema (K10, 2026-10-23; scripts/db_app_role.py, TURNOS_DB_MIGRATION_URL).

Cubre: el dry-run no escribe nada; el rol creado lee y escribe datos pero no
crea ni borra tablas, no hace TRUNCATE y no tiene UPDATE sobre el registro de
accesos; el arranque con DATABASE_URL = rol de la app y TURNOS_DB_MIGRATION_URL
= dueño lleva el esquema a head y sirve; lo que el dueño crea después le llega
al rol; una URL de migración que apunta a OTRA base frena el arranque.

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
         -m unittest discover -s tests -p "test_db_app_role.py"
"""

import io
import os
import sys
import unittest
from unittest import mock
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_schema  # noqa: E402
import db_app_role  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_app_role", seed=lambda: db_operators.operators_list())
_PASS = "app-role-throwaway-pass"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL, _ROLE
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url
    # Los roles son de todo el cluster: uno por corrida, borrado al final.
    _ROLE = _dbtest.run_scoped("app_role")


def tearDownModule():
    if HAVE_DB:
        import psycopg
        for url in (_TEST_URL,):
            try:
                with psycopg.connect(url, autocommit=True) as c, c.cursor() as cur:
                    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (_ROLE,))
                    if cur.fetchone():
                        cur.execute('DROP OWNED BY "%s"' % _ROLE)
            except Exception:  # noqa: BLE001
                pass
    _db.tear_down()
    if HAVE_DB:
        import psycopg
        with psycopg.connect(_MAINT_URL, autocommit=True) as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (_ROLE,))
            if cur.fetchone():
                cur.execute('DROP OWNED BY "%s"' % _ROLE)
                cur.execute('DROP ROLE "%s"' % _ROLE)


def _as_app(url):
    p = urlsplit(url)
    host = p.netloc.rsplit("@", 1)[-1]
    return urlunsplit((p.scheme, "%s:%s@%s" % (_ROLE, _PASS, host), p.path, p.query, p.fragment))


def _role_exists():
    import psycopg
    with psycopg.connect(_TEST_URL) as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (_ROLE,))
        return cur.fetchone() is not None


class AppRoleCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def _apply(self, dry_run=False):
        out = io.StringIO()
        db_app_role.apply(_TEST_URL, _ROLE, _PASS, dry_run=dry_run, out=out)
        return out.getvalue()

    def _app_execute(self, sql, params=None):
        import psycopg
        with psycopg.connect(_as_app(_TEST_URL), autocommit=True) as c, c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None


class TestScript(AppRoleCase):
    def test_1_dry_run_prints_and_writes_nothing_never_the_password(self):
        if _role_exists():
            self.skipTest("rol ya creado por otro caso")
        texto = self._apply(dry_run=True)
        self.assertIn("CREATE ROLE", texto)
        self.assertIn("dry-run", texto)
        self.assertNotIn(_PASS, texto)
        self.assertFalse(_role_exists())

    def test_2_app_role_works_data_but_not_the_schema(self):
        texto = self._apply()
        self.assertNotIn(_PASS, texto)
        import psycopg
        self.assertEqual(self._app_execute("SELECT count(*) FROM users")[0][0] >= 0, True)
        self._app_execute(
            "INSERT INTO clinical_access_log (actor, action, dni_hash) VALUES ('x', 'ficha', 'h')")
        for sql in ("CREATE TABLE intruso (id int)",
                    "DROP TABLE users",
                    "ALTER TABLE users ADD COLUMN intrusa text",
                    "TRUNCATE clinical_access_log",
                    "UPDATE clinical_access_log SET actor = 'otro'"):
            with self.subTest(sql=sql):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    self._app_execute(sql)
        # Re-correrlo es idempotente.
        self._apply()

    def _pending_0026(self):
        """The database as an install one release behind: 0026 not applied."""
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS clinical_access_log")
            cur.execute("UPDATE alembic_version SET version_num = '0025_patient_portal_user_id'")

    def _boot(self, env):
        app_url = env["DATABASE_URL"]
        db_schema._ensured.clear()
        try:
            with _env.patched_env({"TURNOS_DB_MIGRATION_URL": None, **env}):
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("SELECT current_user AS u, "
                                "(SELECT version_num FROM alembic_version) AS v, "
                                "to_regclass('clinical_access_log') IS NOT NULL AS t")
                    return cur.fetchone()
        finally:
            db_schema._ensured.discard(app_url)
            db.close_pools()

    def test_3_boot_as_app_role_migrates_through_the_owner(self):
        self._apply()
        app_url = _as_app(_TEST_URL)
        self._pending_0026()
        # Sin el dueño, el rol de la app no puede crear la tabla nueva.
        with self.assertRaises(Exception):
            self._boot({"DATABASE_URL": app_url})
        fila = self._boot({"DATABASE_URL": app_url, "TURNOS_DB_MIGRATION_URL": _TEST_URL})
        from alembic.script import ScriptDirectory
        head = ScriptDirectory.from_config(db_schema._alembic_config(_TEST_URL)).get_current_head()
        self.assertEqual((fila["u"], fila["v"], fila["t"]), (_ROLE, head, True))
        self._apply()   # el rol vuelve a quedar sin UPDATE sobre la tabla recreada
        # Una tabla que el dueño crea después (una revisión nueva) le llega al rol.
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True) as c, c.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS revision_futura (id serial PRIMARY KEY, v text)")
        self._app_execute("INSERT INTO revision_futura (v) VALUES ('ok')")

    def test_4_a_migration_url_on_another_database_stops_the_boot(self):
        self._apply()
        app_url = _as_app(_TEST_URL)
        env = {"DATABASE_URL": app_url, "TURNOS_DB_MIGRATION_URL": _MAINT_URL}
        db_schema._ensured.clear()
        try:
            with _env.patched_env(env):
                with self.assertRaises(Exception) as ctx:
                    with db._connect():
                        pass
            self.assertIn("TURNOS_DB_MIGRATION_URL", str(ctx.exception))
        finally:
            db_schema._ensured.discard(app_url)
            db.close_pools()


class TestScriptPassword(AppRoleCase):
    """The password is a SQL literal inside `ALTER ROLE ... PASSWORD`, which cannot take a bind
    parameter: every character that a SQL string, a `%` format or a `:name` placeholder treats
    specially has to arrive in the role unchanged."""

    _AWKWARD = "pa'ss%s:w\"o\\rd{}%%:x"

    def _login(self, password):
        import psycopg
        p = urlsplit(_TEST_URL)
        with psycopg.connect(host=p.hostname, port=p.port, dbname=p.path.lstrip("/"),
                             user=_ROLE, password=password, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT current_user")
            return cur.fetchone()[0]

    def test_5_a_password_with_awkward_characters_arrives_unchanged(self):
        out = io.StringIO()
        db_app_role.apply(_TEST_URL, _ROLE, self._AWKWARD, dry_run=False, out=out)
        self.assertNotIn(self._AWKWARD, out.getvalue())
        self.assertEqual(self._login(self._AWKWARD), _ROLE)
        import psycopg
        with self.assertRaises(psycopg.OperationalError):
            self._login(self._AWKWARD.replace("%%", "%"))
        # Back to the suite's password: later cases connect with it.
        self._apply()
        self.assertEqual(self._login(_PASS), _ROLE)


class TestStatementText(unittest.TestCase):
    """The statements of `statements()` go out through `sa.text`, which reads `:name` as a bind
    parameter and `%` as a format character: a database name with either must reach the server
    unchanged."""

    def test_colon_and_percent_survive_text(self):
        sent = []

        class Cur:
            def execute(self, sql, params=None):
                sent.append((sql, params))

        stmt = 'GRANT CONNECT ON DATABASE "a:b\\:c%d:" TO "r"'
        db_app_role._run_statement(Cur(), stmt)
        sql, params = sent[0]
        self.assertEqual(params, {})
        self.assertEqual(sql % params, stmt)


if __name__ == "__main__":
    unittest.main()
