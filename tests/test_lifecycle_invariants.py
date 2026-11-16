"""Lifecycle invariants for booking requests: cancel/reject state guards."""
import os
import sys
import unittest
from unittest import mock
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import db_booking  # noqa: E402
from app.core import db_clinical  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_lifecycle")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestCancelOnRechazada(unittest.TestCase):
    """Cancel on a rechazada row returns None, row stays rechazada."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")

    def test_cancel_on_rechazada_returns_none(self):
        sid = self._setup_booking()
        db_booking.reject(sid, "test motivo")

        result = db_booking.cancel(sid)
        self.assertIsNone(result)

        row = db_booking.get_booking_request(sid)
        self.assertEqual(row.get("estado"), "rechazada")

    def _setup_booking(self):
        """Create a booking in pendiente status."""
        return db_booking.create_booking_request(
            dni="11111111",
            nombre="Test",
            apellido="Patient",
            nacimiento="1990-01-01",
            contacto="test@example.com",
            servicio_id=1,
            centro_slug="test"
        )


class TestRejectWithDesde(unittest.TestCase):
    """Reject respects the desde parameter."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")

    def test_reject_on_aprobada_without_desde_raises(self):
        sid = self._setup_approved_booking()

        with self.assertRaises(db_booking.AlreadyProcessed):
            db_booking.reject(sid, "test motivo")

    def test_reject_on_aprobada_with_desde_succeeds(self):
        sid = self._setup_approved_booking()

        result = db_booking.reject(sid, "test motivo", desde=("aprobada",))
        self.assertIsNotNone(result)
        self.assertEqual(result.get("estado"), "rechazada")
        self.assertEqual(result.get("motivo_rechazo"), "test motivo")

    def test_reject_on_pendiente_always_works(self):
        sid = self._setup_booking()

        result = db_booking.reject(sid, "test motivo")
        self.assertIsNotNone(result)
        self.assertEqual(result.get("estado"), "rechazada")

    def _setup_booking(self):
        sid = db_booking.create_booking_request(
            dni="22222222",
            nombre="Test2",
            apellido="Patient2",
            nacimiento="1990-01-01",
            contacto="test2@example.com",
            servicio_id=1,
            centro_slug="test"
        )
        return sid

    def _setup_approved_booking(self):
        sid = self._setup_booking()
        dt = datetime.now(timezone.utc) + timedelta(days=1)
        db_booking.approve(sid, dt)
        return sid


class TestInterviewQRVsPostBooking(unittest.TestCase):
    """interview_for_request returns post-booking interview over QR."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")

    def test_post_booking_beats_qr(self):
        sid = self._setup_booking()

        qr_int = db_clinical.interview_create(
            request_id=sid,
            dni="33333333",
            doctor_id=None,
            entry_mode="qr"
        )

        pb_int = db_clinical.interview_create(
            request_id=sid,
            dni="33333333",
            doctor_id=1,
            entry_mode="post_booking"
        )

        result = db_clinical.interview_for_request(sid)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("id"), pb_int.get("id"))
        self.assertEqual(result.get("entry_mode"), "post_booking")

    def _setup_booking(self):
        return db_booking.create_booking_request(
            dni="33333333",
            nombre="Test3",
            apellido="Patient3",
            nacimiento="1990-01-01",
            contacto="test3@example.com",
            servicio_id=1,
            centro_slug="test"
        )


class TestHealthProfileMergeQRGuard(unittest.TestCase):
    """QR interviews must never overwrite health profile."""

    def test_qr_interview_returns_empty(self):
        from app.modules.intake import health_profile

        interview = {"dni": "1", "entry_mode": "qr"}
        qa_lines = ["q: a", "q: b"]

        result = health_profile.merge_from_interview(interview, qa_lines)
        self.assertEqual(result, {})

    def test_qr_interview_never_calls_db_save(self):
        """Pure test: QR interview blocked before any DB call."""
        from app.modules.intake import health_profile

        interview = {"dni": "1", "entry_mode": "qr"}
        qa_lines = ["q: a"]

        with mock.patch.object(db_clinical, "health_profile_save",
                               side_effect=AssertionError("DB save should not be called")):
            result = health_profile.merge_from_interview(interview, qa_lines)
            self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
