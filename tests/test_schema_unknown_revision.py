"""A database stamped with an Alembic revision this checkout does not know.

That is what a rollback to an older release looks like: the newer release migrated the
database forward, the older code has no script for the stamp it finds. Before 2026-11-06
every `db._connect()` took the migrations lock again, ran Alembic, failed and left the
URL un-ensured, so the next connection did it all over (0.1-2.6 s per connection).

The contract pinned here: the boot decides once per process, refuses before Alembic with
a message that names the stored revision, the newest one the code knows and what to do;
`/ready`, the panel login card and «Sistema» show that message; the refusal is logged
once; and a database put back on a known revision (a restore, the newer release rolled
back) is served again without a restart.
"""
import logging
import unittest
from unittest.mock import patch

import _dbtest
from app.admin_panel import admin_login_screen, system_status
from app.core import db, db_locks, db_schema, health

_database = _dbtest.PerModuleDb("sana_turnos_test_schema_unknown_revision")

_FUTURE = "9999_from_a_newer_release"

# The refusal's ERROR line is asserted once (assertLogs); elsewhere it would only print.
_quiet = logging.NullHandler()


def setUpModule():
    logging.getLogger("turnos.schema").addHandler(_quiet)
    _database.set_up()


def tearDownModule():
    _database.tear_down()
    logging.getLogger("turnos.schema").removeHandler(_quiet)


def _stamp(revision):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE alembic_version SET version_num = %s", (revision,))


def _forget_process_state():
    """A fresh process against the same database: nothing ensured, nothing refused."""
    db_schema._ensured.clear()
    db_schema._refused.clear()
    db.close_pools()


class TestUnknownRevision(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        with db._connect() as conn:
            _al_dia, self.head, _h = db.migrations_status(conn)
        _stamp(_FUTURE)
        _forget_process_state()
        self.addCleanup(self._restore)

    def _restore(self):
        import psycopg
        with psycopg.connect(db._db_url(), autocommit=True) as c, c.cursor() as cur:
            cur.execute("UPDATE alembic_version SET version_num = %s", (self.head,))
        _forget_process_state()

    def _connect_refused(self):
        with self.assertRaises(db.SchemaRefused) as caught:
            with db._connect():
                pass
        return caught.exception

    def test_every_connection_is_refused_with_the_revisions_and_the_way_out(self):
        for _ in range(3):
            refused = self._connect_refused()
        text = str(refused)
        self.assertIn(_FUTURE, text)
        self.assertIn(self.head, text)
        self.assertIn("versión más nueva", text)
        self.assertIsInstance(refused, db.DbUnavailable)

    def test_the_decision_is_taken_once_and_alembic_never_runs(self):
        lock_keys = []
        real_key = db_locks._lock_key

        def counting_key(name):
            lock_keys.append(name)
            return real_key(name)

        with patch.object(db_locks, "_lock_key", side_effect=counting_key), \
                patch.object(db_schema, "_run_alembic_upgrade") as upgrade:
            for _ in range(5):
                self._connect_refused()
        self.assertEqual(upgrade.call_count, 0)
        # The first connection takes the lock (and releases it) to decide; the four
        # after it reuse the decision without touching the lock.
        self.assertEqual(lock_keys.count("turnos:migrations"), 2)

    def test_the_refusal_is_logged_once(self):
        with self.assertLogs("turnos.schema", level="ERROR") as logs:
            for _ in range(4):
                self._connect_refused()
        self.assertEqual(len(logs.records), 1)
        self.assertIn(_FUTURE, logs.output[0])
        self.assertIn(self.head, logs.output[0])

    def test_ready_and_sistema_name_the_revision(self):
        up, table, at_head, detail = system_status.probe_db()
        self.assertEqual((up, table, at_head), (False, False, False))
        self.assertIn(_FUTURE, detail)
        self.assertIn(self.head, detail)
        ok, body = health.readiness()
        self.assertFalse(ok)
        self.assertIn(_FUTURE, body["detail"])

    def test_the_panel_login_card_says_what_happened(self):
        self._connect_refused()
        html = admin_login_screen._outage_html("/admin").decode()
        self.assertIn("versión más nueva", html)
        self.assertIn(_FUTURE, html)
        self.assertIn("No es un problema de tu usuario ni de tu contraseña", html)

    def test_a_database_back_on_a_known_revision_is_served_without_a_restart(self):
        self._connect_refused()
        import psycopg
        with psycopg.connect(db._db_url(), autocommit=True) as c, c.cursor() as cur:
            cur.execute("UPDATE alembic_version SET version_num = %s", (self.head,))
        with db._connect() as conn:
            al_dia, actual, head = db.migrations_status(conn)
        self.assertEqual((al_dia, actual), (True, self.head))
        self.assertIsNone(db.schema_refusal())


if __name__ == "__main__":
    unittest.main()
