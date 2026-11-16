"""Display-only history classification; no patient records or outbound calls."""
import unittest
from datetime import datetime
from unittest.mock import patch

from app.modules.turnos import booking_requests as requests


class TestPatientAppointmentHistory(unittest.TestCase):
    def setUp(self):
        self.now = datetime.fromisoformat("2026-10-21T12:00:00-03:00")

    def elapsed(self, row, now=None):
        return requests._appointment_has_elapsed(row, now or self.now)

    def test_past_is_independent_of_approval_status_and_does_not_mutate(self):
        for status in ("pendiente", "aprobada", "cancelada", "rechazada"):
            row = {"estado": status, "turno_dt": "2026-10-20T15:00:00-03:00"}
            before = dict(row)
            self.assertTrue(self.elapsed(row))
            self.assertEqual(row, before)

    def test_today_future_past_and_exact_boundary(self):
        self.assertFalse(self.elapsed({"turno_dt": "2026-10-21T12:01:00-03:00"}))
        self.assertTrue(self.elapsed({"turno_dt": "2026-10-21T11:59:00-03:00"}))
        self.assertTrue(self.elapsed({"turno_dt": "2026-10-21T12:00:00-03:00"}))

    def test_utc_midnight_is_still_previous_day_in_hospital(self):
        now = datetime.fromisoformat("2026-10-22T00:30:00+00:00")
        self.assertFalse(self.elapsed({"turno_dt": "2026-10-21T22:00:00-03:00"}, now))
        self.assertTrue(self.elapsed({"turno_dt": "2026-10-21T23:00:00+00:00"}, now))

    def test_hold_and_naive_datetime_use_installation_timezone(self):
        self.assertTrue(self.elapsed({"estado": "pendiente", "hold": {"dia": "2026-10-21", "hora": "11:00"}}))
        self.assertFalse(self.elapsed({"hold": {"dia": "2026-10-21", "hora": "13:00"}}))
        self.assertFalse(self.elapsed({"turno_dt": "2026-10-21T13:00:00"}))

    def test_missing_time_only_moves_a_known_previous_day(self):
        self.assertTrue(self.elapsed({"turno_dt": "2026-10-20"}))
        self.assertFalse(self.elapsed({"turno_dt": "2026-10-21"}))
        for row in ({}, {"turno_dt": "invalid"}, {"created": "2020-01-01"},
                    {"hold": {"dia": "2026-10-21"}}, {"hold": {"dia": "invalid", "hora": "25:00"}}):
            self.assertFalse(self.elapsed(row))

    def test_public_projection_exposes_display_flag_without_changing_status(self):
        row = {"id": 7, "estado": "pendiente", "turno_dt": None}
        with patch.object(requests.store, "hold_of_booking_request", return_value={"dia": "2001-01-01", "hora": "09:00"}):
            public = requests._public_row(row)
        self.assertTrue(public["turno_pasado"])
        self.assertEqual(public["estado"], "pendiente")
        self.assertEqual(row["estado"], "pendiente")
