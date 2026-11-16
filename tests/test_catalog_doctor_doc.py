"""catalog.doctor_doc — the one id lookup over the live doctors (no database needed)."""
import unittest
from unittest.mock import patch

from app.patient_web import catalog

LIVE = [{"id": "ana", "nombre": "Ana"}, {"id": "7", "nombre": "Siete"}]
WITH_INCOMPLETE = LIVE + [{"id": "nuevo", "incompleto": True}]


def _doctors(incluir_incompletos=False, incluir_deshabilitados=False):
    return WITH_INCOMPLETE if incluir_incompletos else LIVE


class DoctorDocTest(unittest.TestCase):
    def test_finds_by_id_compared_as_text(self):
        with patch.object(catalog, "doctors", side_effect=_doctors):
            self.assertEqual(catalog.doctor_doc("ana"), LIVE[0])
            self.assertEqual(catalog.doctor_doc(7), LIVE[1])

    def test_no_id_or_no_match_is_none(self):
        with patch.object(catalog, "doctors", side_effect=_doctors) as doctors:
            for missing in (None, "", "ghost"):
                self.assertIsNone(catalog.doctor_doc(missing))
            self.assertEqual(doctors.call_count, 1)  # an empty id never reads the catalog

    def test_incomplete_doctors_only_when_asked(self):
        with patch.object(catalog, "doctors", side_effect=_doctors):
            self.assertIsNone(catalog.doctor_doc("nuevo"))
            self.assertEqual(catalog.doctor_doc("nuevo", incluir_incompletos=True), WITH_INCOMPLETE[2])

    def test_unreadable_catalog_raises_unless_tolerant(self):
        with patch.object(catalog, "doctors", side_effect=RuntimeError("catalog down")):
            with self.assertRaises(RuntimeError):
                catalog.doctor_doc("ana")
            self.assertIsNone(catalog.doctor_doc("ana", tolerant=True))


class DoctorOfExactIdTest(unittest.TestCase):
    """doctor_appointments.doctor_of keeps comparing ids as they come (doctor_doc compares them as text)."""

    def test_int_id_does_not_match_text_id(self):
        from app.modules.turnos import doctor_appointments
        with patch.object(catalog, "doctors", side_effect=_doctors):
            self.assertEqual(doctor_appointments.doctor_of("7"), LIVE[1])
            self.assertIsNone(doctor_appointments.doctor_of(7))
            self.assertIsNone(doctor_appointments.doctor_of(None))
            self.assertIsNone(doctor_appointments.doctor_of("nuevo"))
            self.assertEqual(doctor_appointments.doctor_of("nuevo", incluir_incompletos=True), WITH_INCOMPLETE[2])
        with patch.object(catalog, "doctors", side_effect=RuntimeError("down")):
            self.assertIsNone(doctor_appointments.doctor_of("7"))


class DoctorSpecialtyTest(unittest.TestCase):
    """catalog.doctor_specialty — the best-effort specialty text (consult_admin and interview_agent used to
    each wrap the lookup in their own try/except and strip inside it)."""

    ROWS = [{"id": "a", "especialidad": "  Cardiología "}, {"id": "b", "especialidad": 123},
            {"id": "c", "especialidad": ""}, {"id": "d", "especialidad": "   "}, {"id": "e"},
            {"id": "f", "especialidad": 0}, {"id": "g", "especialidad": ["x"]}]

    def test_text_only_and_everything_else_is_none(self):
        with patch.object(catalog, "doctors", return_value=self.ROWS):
            self.assertEqual(catalog.doctor_specialty("a"), "Cardiología")
            for other in ("b", "c", "d", "e", "f", "g", "zzz", "", None):
                self.assertIsNone(catalog.doctor_specialty(other), other)

    def test_unreadable_catalog_is_none(self):
        with patch.object(catalog, "doctors", side_effect=RuntimeError("down")):
            self.assertIsNone(catalog.doctor_specialty("a"))

    def test_the_two_intake_readers_answer_the_same(self):
        from app.modules.intake import consult_admin, interview_agent
        with patch.object(catalog, "doctors", return_value=self.ROWS):
            for doctor_id, want in (("a", "Cardiología"), ("b", None), ("zzz", None), (None, None)):
                self.assertEqual(consult_admin._doctor_especialidad(doctor_id), want, doctor_id)
                self.assertEqual(interview_agent._especialidad_for({"doctor_id": doctor_id}), want, doctor_id)


if __name__ == "__main__":
    unittest.main()
