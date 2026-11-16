"""The booking window per doctor AT a center: doctor-at-center -> doctor -> service -> site."""
import unittest
from datetime import datetime
from unittest.mock import patch

from app.admin_panel import admin_content
from app.modules.turnos import booking_horizon as horizon
from app.patient_web import catalog

NOW = datetime(2026, 10, 13, 8)

# «Ecografía infantil» at the Hospital: the hospital asks for 14 days, the
# service says 31, the doctor 7 on their agenda, the site 21.
SERVICE = {"id": "eco-infantil", "centro": "hospital", "dias_anticipacion": 31}
DOCTOR = {
    "id": "doctor",
    "agenda": {"dias_anticipacion": 7},
    "dias_anticipacion": 9,
    "centros": [
        {"centro": "hospital", "dias_anticipacion": 14},
        {"centro": "caps-norte"},
    ],
}


class CenterWindowResolution(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(horizon.config, "get", return_value="21")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pediatric_ultrasound_at_the_hospital_is_14_days(self):
        self.assertEqual(horizon.days_for(SERVICE, DOCTOR), 14)

    def test_center_window_wins_over_every_other_level(self):
        self.assertEqual(horizon.days_for({"centro": "hospital", "dias_anticipacion": 31},
                                          {"centros": [{"centro": "hospital", "dias_anticipacion": 3}],
                                           "dias_anticipacion": 9}), 3)

    def test_a_center_without_a_window_falls_back_to_the_doctor(self):
        service = dict(SERVICE, centro="caps-norte")
        self.assertEqual(horizon.days_for(service, DOCTOR), 7)

    def test_a_center_the_doctor_does_not_attend_falls_back(self):
        service = dict(SERVICE, centro="elsewhere")
        self.assertEqual(horizon.days_for(service, DOCTOR), 7)

    def test_then_the_service_then_the_site(self):
        self.assertEqual(horizon.days_for(SERVICE, {"centros": [{"centro": "hospital"}]}), 31)
        self.assertEqual(horizon.days_for({"centro": "hospital"}, {"centros": [{"centro": "hospital"}]}), 21)

    def test_no_doctor_or_no_service_center_changes_nothing(self):
        self.assertEqual(horizon.days_for(SERVICE, None), 31)
        self.assertEqual(horizon.days_for({"dias_anticipacion": 31}, DOCTOR), 7)

    def test_invalid_center_window_is_ignored_and_big_ones_clamped(self):
        for bad in (0, -1, "", "no", None):
            doctor = {"centros": [{"centro": "hospital", "dias_anticipacion": bad}]}
            self.assertEqual(horizon.days_for(SERVICE, doctor), 31)
        doctor = {"centros": [{"centro": "hospital", "dias_anticipacion": 9999}]}
        self.assertEqual(horizon.days_for(SERVICE, doctor), 365)

    def test_allows_follows_the_center_window(self):
        # Opened the whole day: today + 13 is the last bookable day.
        self.assertTrue(horizon.allows("2026-10-26", SERVICE, DOCTOR, now=NOW))
        self.assertFalse(horizon.allows("2026-10-27", SERVICE, DOCTOR, now=NOW))
        self.assertTrue(horizon.allows("2026-10-27", SERVICE, None, now=NOW))

    def test_desk_still_sees_the_wide_window(self):
        with horizon.staff():
            self.assertEqual(horizon.available_days(SERVICE, DOCTOR, now=NOW), horizon.STAFF_DAYS)

    def test_check_slot_rejects_beyond_the_center_window(self):
        from app.modules.turnos import booking_limits
        from app.patient_web import catalog
        with patch.object(booking_limits, "_service_doc", return_value=SERVICE), \
             patch.object(catalog, "doctor_doc", return_value=DOCTOR):
            self.assertIsNone(horizon.check_slot("eco-infantil", "2026-10-26", "09:00", now=NOW, doctor_id="doctor"))
            rejected = horizon.check_slot("eco-infantil", "2026-10-27", "09:00", now=NOW, doctor_id="doctor")
        self.assertEqual(rejected["error"], "turno_fuera_de_anticipacion")


class CenterWindowValidation(unittest.TestCase):
    CENTERS = {"hospital", "caps-norte"}

    def check(self, **extra):
        return catalog._validate_doctor_center(dict({"centro": "hospital"}, **extra), self.CENTERS)

    def test_accepts_a_number_or_a_numeric_string(self):
        self.assertEqual(self.check(dias_anticipacion=14)[0]["dias_anticipacion"], 14)
        self.assertEqual(self.check(dias_anticipacion="14")[0]["dias_anticipacion"], 14)
        self.assertEqual(self.check(dias_anticipacion=365)[0]["dias_anticipacion"], 365)

    def test_empty_means_inherit_and_stores_nothing(self):
        for empty in (None, "", "  "):
            out, err = self.check(dias_anticipacion=empty)
            self.assertIsNone(err)
            self.assertNotIn("dias_anticipacion", out)
        self.assertNotIn("dias_anticipacion", self.check()[0])

    def test_rejects_outside_1_to_365(self):
        for bad in (0, -1, 366, 9999, "abc", True, 1.5):
            self.assertIsNotNone(self.check(dias_anticipacion=bad)[1], bad)


class DoctorSelfEditKeepsTheAdminWindow(unittest.TestCase):
    def test_stored_window_replaces_whatever_the_doctor_sends(self):
        current = {"centros": [{"centro": "hospital", "dias_anticipacion": 14}, {"centro": "caps-norte"}]}
        body = {"centros": [{"centro": "hospital", "dias_anticipacion": 365},
                            {"centro": "caps-norte", "dias_anticipacion": 2}]}
        admin_content._keep_center_windows(body, current)
        self.assertEqual(body["centros"][0]["dias_anticipacion"], 14)
        self.assertNotIn("dias_anticipacion", body["centros"][1])

    def test_window_is_not_part_of_the_schedule(self):
        a = {"centros": [{"centro": "hospital", "franjas": [], "dias_anticipacion": 14}]}
        b = {"centros": [{"centro": "hospital", "franjas": []}]}
        self.assertEqual(admin_content._centers_schedule(a), admin_content._centers_schedule(b))
        c = {"centros": [{"centro": "hospital", "franjas": [{"dias": [1]}]}]}
        self.assertNotEqual(admin_content._centers_schedule(a), admin_content._centers_schedule(c))


if __name__ == "__main__":
    unittest.main()
