"""The patient booking window does not bind the desk (the reference-deployment counterpart, 2026-10-19).

A doctor with a one-week window and a closure until Friday showed the desk
«no tiene horarios con lugar en el próximo mes» while his Thursdays were
bookable by staff. Desk slot lookups run inside `booking_horizon.staff()`.
"""
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.modules.turnos import booking_horizon  # noqa: E402

NOW = datetime(2026, 10, 19, 17, 0)
DOCTOR = {"agenda": {"dias_anticipacion": 7}}


class TestStaffWindow(unittest.TestCase):
    def test_patient_window_stays_one_week(self):
        self.assertEqual(booking_horizon.available_days(None, DOCTOR, NOW), 7)
        self.assertFalse(booking_horizon.allows("2026-10-29", None, DOCTOR, NOW))

    def test_desk_sees_at_least_the_site_default(self):
        with booking_horizon.staff():
            self.assertGreaterEqual(booking_horizon.available_days(None, DOCTOR, NOW),
                                    booking_horizon.site_default())
            self.assertTrue(booking_horizon.allows("2026-10-29", None, DOCTOR, NOW))
        self.assertFalse(booking_horizon.allows("2026-10-29", None, DOCTOR, NOW),
                         "the widening ends with the block")

    def test_a_longer_doctor_window_is_kept_for_the_desk(self):
        lejos = {"agenda": {"dias_anticipacion": 200}}
        with booking_horizon.staff():
            self.assertEqual(booking_horizon.available_days(None, lejos, NOW), 200)

    def test_desk_reaches_a_doctor_back_from_holidays_in_seven_weeks(self):
        # The reference-deployment counterpart, 2026-10-23: every hospital cardiologist was closed until late
        # November or mid-December, past the 31-day site window, so «Crear
        # turno» in Cardiología showed only the panel's test doctor and the
        # month arrows never reached the real ones.
        with booking_horizon.staff():
            self.assertTrue(booking_horizon.allows("2026-12-15", None, DOCTOR, NOW))
            self.assertGreaterEqual(booking_horizon.available_days(None, DOCTOR, NOW), 90)


if __name__ == "__main__":
    unittest.main()
