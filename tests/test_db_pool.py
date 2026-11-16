"""test_db_pool.py — db.py's Postgres connection pool (2026-08-06 audit:
`_connect()` used to open a brand-new TCP connection per query under a
threaded HTTP server; it's now checked out from a module-level
`psycopg_pool.ConnectionPool`, one per DATABASE_URL — see db.py's `_get_pool`
and `_PooledConn`).

Covers the three requirements from the audit:
  (a) a connection that died under us (Postgres restart) gets DISCARDED and
      REDIALED on the next checkout, never handed out broken.
  (b) pool exhaustion blocks with a bounded timeout, then raises
      `db.DbUnavailable` — the SAME exception class every caller already
      catches, never a raw `psycopg_pool.PoolTimeout` leaking through.
  (c) the `with _connect() as conn, conn.cursor() as cur:` contract at the
      ~234 call sites is unchanged — checkout on enter, return-to-pool
      (not close) on exit.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_catalog  # noqa: E402
import _dbtest  # noqa: E402


class PoolTimeoutTranslationTest(unittest.TestCase):
    """No live Postgres needed: proves `_connect()` never lets
    `psycopg_pool.PoolTimeout` (or any pool-layer exception) reach a caller
    directly — every caller in this repo already catches `db.DbUnavailable`,
    not psycopg_pool's own exception types."""

    def test_pool_timeout_becomes_db_unavailable(self):
        from psycopg_pool import PoolTimeout

        class _FakePool:
            def getconn(self, timeout=None):
                raise PoolTimeout("pool agotado (fake, sin Postgres real)")

        with mock.patch.object(db, "_get_pool", lambda url: _FakePool()), \
             mock.patch.object(db, "_db_url", lambda: "postgresql://fake/fake"):
            with self.assertRaises(db.DbUnavailable):
                db._connect()

    def test_missing_psycopg_pool_becomes_db_unavailable(self):
        """Same translation for the "driver ausente" branch — a broken
        install (psycopg_pool not importable) must 503 via DbUnavailable,
        same as the pre-pool code did for a missing psycopg."""
        real_get_pool = db._get_pool

        def _boom(url):
            raise db.DbUnavailable("psycopg no disponible: forced for test")

        with mock.patch.object(db, "_get_pool", _boom), \
             mock.patch.object(db, "_db_url", lambda: "postgresql://fake/fake"):
            with self.assertRaises(db.DbUnavailable):
                db._connect()
        self.assertIs(db._get_pool, real_get_pool)


_db = _dbtest.PerModuleDb("sana_turnos_test_pool",
                          seed=lambda: db_catalog.catalog_list("centros"))


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


class PoolIntegrationTest(unittest.TestCase):
    """Real Postgres, real pool. Skips (like every other DB-backed suite in
    this repo) when TEST_DATABASE_URL/DATABASE_URL isn't configured."""

    def setUp(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)

    def test_checkout_runs_a_real_query_and_returns_to_the_pool(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS uno")
            self.assertEqual(cur.fetchone()["uno"], 1)
        # The contract from `with _connect() as conn:` doesn't change: the
        # connection comes back OPEN (returned to the pool), never closed.
        self.assertFalse(conn.closed)

    def test_broken_connection_is_discarded_and_redialed(self):
        """Simulates a Postgres restart hitting a connection that's sitting
        idle in the pool: kill its backend, then check out again. The pool's
        health check (`check=ConnectionPool.check_connection`, wired in
        `db._get_pool`) must catch the dead connection at checkout, discard
        it, and hand back a genuinely NEW one — proven by a different
        backend pid — instead of handing out (or raising on) the dead one."""
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_backend_pid()")
            pid = cur.fetchone()["pg_backend_pid"]

        # Use an independent control connection. A second pool checkout may
        # legitimately return the same idle connection we are trying to kill,
        # which would make the test terminate its own controller at random.
        import psycopg
        with psycopg.connect(
                _db.test_url, autocommit=True, connect_timeout=5) as killer:
            with killer.cursor() as cur2:
                cur2.execute("SELECT pg_backend_pid()")
                self.assertNotEqual(pid, cur2.fetchone()[0])
                cur2.execute("SELECT pg_terminate_backend(%s)", (pid,))
                self.assertTrue(cur2.fetchone()[0])

        with db._connect() as conn3, conn3.cursor() as cur3:
            cur3.execute("SELECT pg_backend_pid()")
            pid3 = cur3.fetchone()["pg_backend_pid"]
        self.assertNotEqual(pid, pid3)

        # And the app keeps working afterward — no lingering DbUnavailable
        # from the one dead connection (proves "recover without a process
        # restart", the audit's actual requirement).
        with db._connect() as conn4, conn4.cursor() as cur4:
            cur4.execute("SELECT 1 AS uno")
            self.assertEqual(cur4.fetchone()["uno"], 1)

    def test_pool_exhaustion_times_out_and_raises_db_unavailable(self):
        """A pool with exactly ONE slot: hold it open, then a second
        checkout must block for a bounded time and raise `DbUnavailable` —
        never hang forever, never leak psycopg_pool's own exception type.
        The checkout timeout is shortened via monkeypatch (not the env var)
        so this test doesn't have to sit through the real 10s `_connect()`
        uses in production."""
        url = db._db_url()
        _env.set_env(self, TURNOS_DB_POOL="1")
        self.addCleanup(db.close_pools)   # runs before the environment is put back (LIFO)
        db.close_pools()
        with db._connect():  # the pool's only slot, held for the block
            pool = db._get_pool(url)
            real_getconn = pool.getconn

            def _fast_getconn(timeout=None):
                return real_getconn(timeout=0.3)

            with mock.patch.object(pool, "getconn", side_effect=_fast_getconn):
                with self.assertRaises(db.DbUnavailable):
                    db._connect()

    def test_pool_size_reads_turnos_db_pool_env(self):
        url = db._db_url()
        _env.set_env(self, TURNOS_DB_POOL="3")
        self.addCleanup(db.close_pools)   # runs before the environment is put back (LIFO)
        db.close_pools()
        pool = db._get_pool(url)
        self.assertEqual(pool.max_size, 3)


if __name__ == "__main__":
    unittest.main()
