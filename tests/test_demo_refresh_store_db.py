"""ops/demo_refresh_store against a real Postgres: the seeded-day marker query and the
session advisory lock of the demo refresh.

`test_demo_refresh_completion` pins the lock frame's exception order with fakes and the
completion ledger with the database; the marker read (`day_already_seeded`) and the
try-lock answer were only reached indirectly. Here each is checked on its own.
"""
import datetime
import unittest

import _dbtest
from app.core import db
from app.ops import demo_refresh_store

_database = _dbtest.PerModuleDb("sana_turnos_test_demo_refresh_store")


def setUpModule():
    _database.set_up()


def tearDownModule():
    _database.tear_down()


MARCA = "@demo.humanrounds.org"
DAY = datetime.date(2026, 11, 4)


class TestDayAlreadySeeded(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:
            _dbtest.delete_booking_requests(cur)

    def request(self, contact, at, status="aprobada", dni="70010000"):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO booking_requests (dni, contact, status, appointment_at) VALUES (%s, %s, %s, %s)",
                        (dni, contact, status, at))

    def test_no_requests_means_not_seeded(self):
        self.assertFalse(demo_refresh_store.day_already_seeded(DAY))

    def test_a_marked_request_on_that_day_means_seeded(self):
        self.request("ana.perez" + MARCA, "2026-11-04 15:00:00+00")
        self.assertTrue(demo_refresh_store.day_already_seeded(DAY))

    def test_the_day_is_the_database_date_of_the_appointment_instant(self):
        self.request("ana.perez" + MARCA, "2026-11-04 15:00:00+00")
        self.assertFalse(demo_refresh_store.day_already_seeded(datetime.date(2026, 11, 3)))
        self.assertFalse(demo_refresh_store.day_already_seeded(datetime.date(2026, 11, 5)))

    def test_a_request_without_the_demo_marker_does_not_count(self):
        self.request("someone@example.org", "2026-11-04 15:00:00+00")
        self.assertFalse(demo_refresh_store.day_already_seeded(DAY))

    def test_the_marker_must_end_the_contact(self):
        self.request("ana" + MARCA + ".other", "2026-11-04 15:00:00+00")
        self.assertFalse(demo_refresh_store.day_already_seeded(DAY))

    def test_the_featured_patients_appointment_does_not_count(self):
        # 09.11: the Sunday seed puts her next visit on Monday; that one row
        # made Monday read as seeded and the Monday queue stayed empty.
        from scripts import seed_demo_all
        self.request(seed_demo_all.DEMO_PATIENT["contacto"], "2026-11-04 15:00:00+00",
                     dni=seed_demo_all.DEMO_PATIENT["dni"])
        self.assertFalse(demo_refresh_store.day_already_seeded(DAY))
        self.assertEqual(demo_refresh_store.day_queue_size(DAY), 0)

    def test_only_approved_appointments_are_the_days_queue(self):
        for status in ("cancelada", "pendiente", "rechazada"):
            self.request("ana" + MARCA, "2026-11-04 15:00:00+00", status=status)
        self.assertFalse(demo_refresh_store.day_already_seeded(DAY))
        self.request("ana" + MARCA, "2026-11-04 15:00:00+00")
        self.request("bea" + MARCA, "2026-11-04 16:00:00+00")
        self.assertEqual(demo_refresh_store.day_queue_size(DAY), 2)

    def test_a_request_with_no_appointment_does_not_count(self):
        self.request("ana" + MARCA, None)
        self.assertFalse(demo_refresh_store.day_already_seeded(DAY))


class TestExclusiveRefreshLock(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)

    def test_the_second_session_gets_none_and_the_lock_is_free_again_after_exit(self):
        with demo_refresh_store.exclusive_refresh() as first:
            self.assertIsNotNone(first)
            with demo_refresh_store.exclusive_refresh() as second:
                self.assertIsNone(second)
        with demo_refresh_store.exclusive_refresh() as again:
            self.assertIsNotNone(again)

    def test_the_lock_is_released_when_the_body_fails(self):
        with self.assertRaises(RuntimeError):
            with demo_refresh_store.exclusive_refresh():
                raise RuntimeError("boom")
        with demo_refresh_store.exclusive_refresh() as again:
            self.assertIsNotNone(again)


if __name__ == "__main__":
    unittest.main()
