"""admin_panel/system_status.probe_db against a real Postgres.

`/ready` and the «Sistema» panel rely on it, and every other test replaces it with a fake.
Here it runs for real: the liveness ping, the base-table probe and the migrations check.
"""
import unittest
from unittest.mock import patch

import _dbtest
from app.admin_panel import system_status
from app.core import db

_database = _dbtest.PerModuleDb("sana_turnos_test_probe_db")


def setUpModule():
    _database.set_up()


def tearDownModule():
    _database.tear_down()


class TestProbeDb(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)

    def test_a_migrated_database_is_up_with_its_base_table_and_at_head(self):
        self.assertEqual(system_status.probe_db(), (True, True, True, None))

    def test_a_missing_base_table_is_reported_by_name(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE booking_requests RENAME TO booking_requests_synthetic")
        try:
            up, table, at_head, detail = system_status.probe_db()
        finally:
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("ALTER TABLE booking_requests_synthetic RENAME TO booking_requests")
        self.assertEqual((up, table), (True, False))
        self.assertIn("booking_requests", detail)

    def test_pending_migrations_are_reported_with_both_revisions(self):
        with patch.object(db, "migrations_status", return_value=(False, "0001_synthetic", "0099_synthetic")):
            up, table, at_head, detail = system_status.probe_db()
        self.assertEqual((up, table, at_head), (True, True, False))
        self.assertIn("0001_synthetic", detail)
        self.assertIn("0099_synthetic", detail)

    def test_an_unreachable_database_never_raises(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("synthetic")):
            up, table, at_head, detail = system_status.probe_db()
        self.assertEqual((up, table, at_head), (False, False, False))
        self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()
