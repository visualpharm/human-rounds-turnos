"""The web process's database limits (hospital TASK-40, 2026-10-28).

`statement_timeout` and `lock_timeout` are set on the connections of the pool the WEB process
opens, and on no other connection: a stuck lock or a runaway query fails fast with a 503 instead
of holding every pooled connection (docs/architecture/capacity.md). Migrations, the schema
bootstrap, backups, the queue worker and scripts take no limit.

Measured on 98k requests / 28k accounts: the longest statement of the panel took 0.1 s and the
longest request 0.7 s; the defaults are 30 s and 5 s.
"""
import ast
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import _dbtest
import _env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db  # noqa: E402
from app.core import db_schema  # noqa: E402

_database = _dbtest.PerModuleDb("sana_turnos_test_web_timeouts")


def setUpModule():
    _database.set_up()


def tearDownModule():
    db.disable_web_timeouts()
    _database.tear_down()


class TestSettings(unittest.TestCase):
    def tearDown(self):
        db.disable_web_timeouts()

    def test_defaults_are_30_seconds_for_a_statement_and_5_for_a_lock(self):
        with _env.patched_env(TURNOS_DB_STATEMENT_TIMEOUT=None, TURNOS_DB_LOCK_TIMEOUT=None):
            self.assertEqual(db.enable_web_timeouts(), (30000, 5000))
        self.assertEqual(db.web_timeouts(), (30000, 5000))

    def test_the_environment_gives_seconds_and_zero_means_no_limit(self):
        with _env.patched_env(TURNOS_DB_STATEMENT_TIMEOUT=" 45 ", TURNOS_DB_LOCK_TIMEOUT="0.5"):
            self.assertEqual(db.enable_web_timeouts(), (45000, 500))
        with _env.patched_env(TURNOS_DB_STATEMENT_TIMEOUT="0", TURNOS_DB_LOCK_TIMEOUT="0"):
            self.assertEqual(db.enable_web_timeouts(), (0, 0))

    def test_unusable_values_fall_back_and_a_huge_one_is_capped(self):
        for raw in ("", "abc", "-1", "nan", "inf", "1e999"):
            with self.subTest(raw=raw), _env.patched_env(TURNOS_DB_STATEMENT_TIMEOUT=raw,
                                                         TURNOS_DB_LOCK_TIMEOUT=raw):
                self.assertEqual(db.enable_web_timeouts(), (30000, 5000))
        with _env.patched_env(TURNOS_DB_STATEMENT_TIMEOUT="9999", TURNOS_DB_LOCK_TIMEOUT="601"):
            self.assertEqual(db.enable_web_timeouts(), (600000, 600000))

    def test_a_process_that_never_enables_them_has_none(self):
        db.disable_web_timeouts()
        self.assertIsNone(db.web_timeouts())


class TestPoolWiring(unittest.TestCase):
    def tearDown(self):
        db.disable_web_timeouts()

    def _built_kwargs(self):
        built = {}

        class _Pool:
            check_connection = staticmethod(lambda conn: None)

            def __init__(self, url, **kwargs):
                built.update(kwargs)

        import psycopg_pool
        url = "postgresql://fake/web-timeouts"
        with mock.patch.object(psycopg_pool, "ConnectionPool", _Pool):
            db._pools.pop(url, None)
            try:
                db._get_pool(url)
            finally:
                db._pools.pop(url, None)
        return built

    def test_the_web_pool_configures_every_connection_and_another_pool_does_not(self):
        db.disable_web_timeouts()
        self.assertNotIn("configure", self._built_kwargs())
        db.enable_web_timeouts()
        self.assertIs(self._built_kwargs()["configure"], db._configure_web_connection)


class TestTimeoutIsADbUnavailable(unittest.TestCase):
    def _exit(self, exc):
        class _Conn:
            def __exit__(self, *args):
                return False

        class _Pool:
            def putconn(self, conn):
                pass

        pooled = db._PooledConn(_Pool(), _Conn())
        return pooled.__exit__(type(exc), exc, None)

    def test_a_cut_statement_and_a_cut_lock_wait_raise_db_timeout_naming_the_limit(self):
        from psycopg import errors
        with self.assertRaisesRegex(db.DbTimeout, "TURNOS_DB_STATEMENT_TIMEOUT") as cut:
            self._exit(errors.QueryCanceled("canceling statement due to statement timeout"))
        self.assertIsInstance(cut.exception, db.DbUnavailable)
        self.assertIsInstance(cut.exception.__cause__, errors.QueryCanceled)
        with self.assertRaisesRegex(db.DbTimeout, "TURNOS_DB_LOCK_TIMEOUT"):
            self._exit(errors.LockNotAvailable("canceling statement due to lock timeout"))

    def test_any_other_error_passes_through_untouched(self):
        from psycopg import errors
        self.assertFalse(self._exit(errors.UniqueViolation("duplicate key")))
        self.assertFalse(self._exit(ValueError("not a database error")))


class TestAnswer503(unittest.TestCase):
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from psycopg import errors
        from app.core import deps

        app = FastAPI()
        deps.install_db_unavailable_handler(app)

        @app.get("/wrapped")
        def wrapped():
            raise db.DbTimeout("consulta cortada por TURNOS_DB_STATEMENT_TIMEOUT")

        @app.get("/raw-statement")
        def raw_statement():
            raise errors.QueryCanceled("canceling statement due to statement timeout")

        @app.get("/raw-lock")
        def raw_lock():
            raise errors.LockNotAvailable("canceling statement due to lock timeout")

        return TestClient(app, raise_server_exceptions=False)

    def test_every_form_of_the_cut_answers_the_503_json_with_retry_after(self):
        client = self._client()
        for path in ("/wrapped", "/raw-statement", "/raw-lock"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": "base de datos no disponible"})
                self.assertEqual(response.headers["Retry-After"], "3")


class TestOnlyTheWebEntrypointEnablesThem(unittest.TestCase):
    def _sources(self):
        skip = {".git", "node_modules", "tests", "packs", "docs", "static", "locales", "extensions"}
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(folder, name)

    def test_nothing_but_asgi_main_calls_enable_web_timeouts(self):
        callers = set()
        for path in self._sources():
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if "enable_web_timeouts" not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "enable_web_timeouts":
                    callers.add(os.path.relpath(path, ROOT))
        self.assertEqual(callers, {os.path.join("app", "asgi_main.py")})

    def test_asgi_main_enables_them_only_for_the_web_process_and_after_the_pack_check(self):
        with open(os.path.join(ROOT, "app", "asgi_main.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        self.assertEqual([a.arg for a in main.args.args], ["web_process"])
        first = main.body[0]
        self.assertEqual(first.value.func.id, "_verify_required_packs")
        gated = [n for n in main.body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Name) and n.test.id == "web_process"]
        self.assertEqual(len(gated), 1)
        self.assertGreater(main.body.index(gated[0]), 0)
        self.assertIn("_enable_web_db_limits", ast.dump(gated[0]))
        entry = next(n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.dump(n.test))
        self.assertIn("web_process", ast.dump(entry))

    def test_the_enabler_turns_them_on_with_a_boot_line(self):
        from app import asgi_main
        try:
            with mock.patch("builtins.print") as printed, \
                    _env.patched_env(TURNOS_DB_STATEMENT_TIMEOUT=None, TURNOS_DB_LOCK_TIMEOUT="0"):
                asgi_main._enable_web_db_limits()
            self.assertEqual(db.web_timeouts(), (30000, 0))
            self.assertIn("consulta 30 s, espera de bloqueo sin límite", printed.call_args.args[0])
        finally:
            db.disable_web_timeouts()


class _DbCase(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        db.disable_web_timeouts()
        db.close_pools()
        self.addCleanup(self._back_to_default)

    def _back_to_default(self):
        db.disable_web_timeouts()
        db.close_pools()

    def _enable(self, statement="0.5", lock="0.3"):
        with _env.patched_env(TURNOS_DB_STATEMENT_TIMEOUT=statement, TURNOS_DB_LOCK_TIMEOUT=lock):
            db.enable_web_timeouts()
        db.close_pools()

    def _show(self, conn):
        with conn.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            statement = cur.fetchone()["statement_timeout"]
            cur.execute("SHOW lock_timeout")
            return statement, cur.fetchone()["lock_timeout"]

    def _plain_defaults(self):
        import psycopg
        with psycopg.connect(_database.test_url, connect_timeout=5) as conn:
            return (conn.execute("SHOW statement_timeout").fetchone()[0],
                    conn.execute("SHOW lock_timeout").fetchone()[0])


class TestTheWebPoolCarriesTheLimits(_DbCase):
    def test_every_pooled_connection_has_both_limits(self):
        self._enable()
        with db._connect() as conn:
            self.assertEqual(self._show(conn), ("500ms", "300ms"))
        # still there on the next checkout of the same connection
        with db._connect() as conn:
            self.assertEqual(self._show(conn), ("500ms", "300ms"))

    def test_a_statement_over_the_limit_is_cut_with_db_timeout_and_the_pool_keeps_working(self):
        self._enable()
        started = time.monotonic()
        with self.assertRaisesRegex(db.DbTimeout, "TURNOS_DB_STATEMENT_TIMEOUT"):
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_sleep(5)")
        self.assertLess(time.monotonic() - started, 3)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS uno")
            self.assertEqual(cur.fetchone()["uno"], 1)

    def test_a_lock_wait_over_the_limit_is_cut_with_db_timeout(self):
        self._enable()
        with db._connect() as setup, setup.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS web_timeouts_probe (id int)")
        import psycopg
        holder = psycopg.connect(_database.test_url, autocommit=False, connect_timeout=5)
        # If the limit were missing the query would wait for ever; the holder lets go after 4 s
        # so the failure is an assertion, not a hung suite.
        release = threading.Timer(4.0, holder.rollback)
        release.start()
        try:
            holder.execute("LOCK TABLE web_timeouts_probe IN ACCESS EXCLUSIVE MODE")
            started = time.monotonic()
            with self.assertRaisesRegex(db.DbTimeout, "TURNOS_DB_LOCK_TIMEOUT"):
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("SELECT count(1) FROM web_timeouts_probe")
            self.assertLess(time.monotonic() - started, 3)
        finally:
            release.cancel()
            holder.rollback()
            holder.close()
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS web_timeouts_probe")

    def test_zero_switches_a_limit_off(self):
        self._enable(statement="0", lock="0")
        with db._connect() as conn:
            self.assertEqual(self._show(conn), ("0", "0"))


class TestOtherProcessesCarryNone(_DbCase):
    def test_a_pool_opened_without_enabling_has_the_server_defaults(self):
        with db._connect() as conn:
            self.assertEqual(self._show(conn), self._plain_defaults())

    def test_the_environment_alone_does_not_limit_a_process_that_did_not_enable_them(self):
        # The worker is a child of the web process and inherits its environment.
        code = ("from app.core import db\n"
                "with db._connect() as conn:\n"
                "    print(conn.execute('SHOW statement_timeout').fetchone()['statement_timeout'],\n"
                "          conn.execute('SHOW lock_timeout').fetchone()['lock_timeout'])\n")
        env = dict(os.environ, DATABASE_URL=_database.test_url, TURNOS_DB_STATEMENT_TIMEOUT="1",
                   TURNOS_DB_LOCK_TIMEOUT="1", PYTHONPATH=ROOT)
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                             text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.split(), list(self._plain_defaults()))


class TestWorkNotUnderTheLimits(_DbCase):
    def test_without_timeouts_lifts_both_for_the_block_and_restores_them(self):
        self._enable()
        with db._connect() as conn:
            with db.without_timeouts(conn):
                self.assertEqual(self._show(conn), ("0", "0"))
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_sleep(0.8)")  # longer than the 0.5 s statement limit
                conn.commit()
            self.assertEqual(self._show(conn), ("500ms", "300ms"))

    def test_a_failing_block_still_gets_the_limits_back_and_the_error_through(self):
        self._enable()
        with db._connect() as conn:
            with self.assertRaises(ZeroDivisionError):
                with db.without_timeouts(conn):
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                    raise ZeroDivisionError
            self.assertEqual(self._show(conn), ("500ms", "300ms"))

    def test_a_process_without_limits_is_untouched_by_it(self):
        with db._connect() as conn:
            before = self._show(conn)
            with db.without_timeouts(conn) as same:
                self.assertIs(same, conn)
                self.assertEqual(self._show(conn), before)

    def test_the_schema_bootstrap_runs_without_limits_and_later_connections_have_them(self):
        self._enable()
        seen = []

        def fake_ensure(conn, url):
            seen.append(self._show(conn))

        db_schema._ensured.discard(db._db_url())
        try:
            with mock.patch.object(db_schema, "_ensure_schema", fake_ensure):
                with db._connect() as conn:
                    self.assertEqual(self._show(conn), ("500ms", "300ms"))
        finally:
            db_schema._ensured.add(db._db_url())
        self.assertEqual(seen, [("0", "0")])

    def test_the_real_bootstrap_of_an_empty_database_succeeds_under_short_limits(self):
        # Not mocked: Alembic's whole upgrade, the migration lock and the seeds, on the same
        # connection that carries the limits afterwards. The limits are 100 ms, not a hair above zero:
        # a loaded CI runner stalls an ordinary one-row SELECT for more than 1 ms, and the test then
        # measured the runner. "The bootstrap is not cut" is proved by a step that is five times the
        # limit (a 0.5 s sleep) running first, on the bootstrap's own connection, so the proof does
        # not depend on how fast the machine runs the migrations.
        import psycopg
        from urllib.parse import urlsplit, urlunsplit
        name = "sana_turnos_test_web_timeouts_boot_%d" % os.getpid()
        base = urlsplit(_database.test_url)
        url = urlunsplit((base.scheme, base.netloc, "/" + name, base.query, base.fragment))
        with psycopg.connect(_database.maint_url, autocommit=True, connect_timeout=5) as admin:
            admin.execute('DROP DATABASE IF EXISTS "%s"' % name)
            admin.execute('CREATE DATABASE "%s"' % name)
        self.addCleanup(self._drop_database, name)
        real_ensure = db_schema._ensure_schema
        slept = []

        def ensure_after_a_long_step(conn, ensure_url):
            with conn.cursor() as cur:
                cur.execute("SELECT pg_sleep(0.5)")  # five times the statement limit
            slept.append(self._show(conn))
            return real_ensure(conn, ensure_url)

        with _env.patched_env(DATABASE_URL=url, TURNOS_DB_STATEMENT_TIMEOUT="0.1",
                              TURNOS_DB_LOCK_TIMEOUT="0.1"):
            db.enable_web_timeouts()
            db.close_pools()
            self.addCleanup(db_schema._ensured.discard, url)
            self.addCleanup(db.close_pools)
            with mock.patch.object(db_schema, "_ensure_schema", ensure_after_a_long_step):
                with db._connect() as conn:
                    self.assertEqual(self._show(conn), ("100ms", "100ms"))
            self.assertEqual(slept, [("0", "0")])  # the bootstrap ran, and ran without limits
            # The seeds are read on a plain connection: only the SHOW above runs under the 100 ms limit.
            with psycopg.connect(url, connect_timeout=5) as plain:
                self.assertGreater(plain.execute("SELECT count(1) FROM roles").fetchone()[0], 0)  # the seeds ran
            with self.assertRaises(db.DbTimeout):
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("SELECT pg_sleep(0.5)")

    def _drop_database(self, name):
        import psycopg
        db.close_pools()
        with psycopg.connect(_database.maint_url, autocommit=True, connect_timeout=5) as admin:
            admin.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name)

    def test_the_backup_lock_waits_for_another_backup_longer_than_the_lock_limit(self):
        from app.core.backup_journal import Store
        self._enable()
        import psycopg
        other = psycopg.connect(_database.test_url, autocommit=True, connect_timeout=5)
        other.execute("SELECT pg_advisory_lock(hashtext('turnos:backup-full:v1')::bigint)")
        release = threading.Timer(1.0, lambda: other.execute(
            "SELECT pg_advisory_unlock(hashtext('turnos:backup-full:v1')::bigint)"))
        release.start()
        try:
            started = time.monotonic()
            with Store().lock():
                self.assertGreaterEqual(time.monotonic() - started, 0.8)  # waited past the 0.3 s limit
            with db._connect() as conn:
                self.assertEqual(self._show(conn), ("500ms", "300ms"))
        finally:
            release.join()
            other.close()


if __name__ == "__main__":
    unittest.main()
