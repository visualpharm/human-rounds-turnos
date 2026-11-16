"""POST /api/requests checks every catalog reference it is given (hospital
schema review №10, 2026-10-24): the service, the center and the doctor of the
chosen slot must exist in the catalog — a refusal, never «accept anything»
when the catalog cannot be read. No database: every case is refused (or
stopped by a stubbed limit check) before the first write."""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.modules.turnos import booking_create  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
from app.patient_web import catalog  # noqa: E402

_USER = {"dni": "27888999", "nombre": "Ana", "apellido": "López",
         "nacimiento": "1979-06-01", "sexo": "F", "contacto": "wa:11-5555"}

SERVICE = {"id": "cardiologia-hp", "nombre": "Cardiología", "cluster": "cardio",
           "centro": "hospital-pinamar"}
DOCTORS = [
    {"id": "clara", "nombre": "Clara Latido", "especialidad": "Cardióloga",
     "cluster": "cardio", "centro": "hospital-pinamar"},
    {"id": "tomas", "nombre": "Tomás Hueso", "especialidad": "Traumatólogo",
     "cluster": "trauma", "centro": "hospital-pinamar"},
]
CENTERS = [{"slug": "hospital-pinamar", "name": "Hospital de Pinamar"},
           {"slug": "caps-norte", "name": "CAPS Norte"}]


class TestCreateRequestCatalogRefs(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext
        stack(patch.object(booking_requests.store, "attendance_summary_dni",
                           return_value={"bloqueado_web": False}))
        stack(patch.object(booking_requests, "_services",
                           return_value={SERVICE["id"]: SERVICE["nombre"]}))
        stack(patch.object(booking_requests, "_service_full",
                           side_effect=lambda sid: dict(SERVICE) if sid == SERVICE["id"] else {}))
        stack(patch.object(catalog, "centers", return_value=CENTERS))
        stack(patch.object(catalog, "doctors", return_value=DOCTORS))
        stack(patch.object(catalog, "doctor_by_id",
                           side_effect=lambda mid: next((m for m in DOCTORS if m["id"] == mid), None)))
        # a request that passes the new checks stops at the booking limit, before any write
        self.limit = stack(patch("app.modules.turnos.booking_limits.check", return_value=(
            False, {"limite": 1, "abiertas": 1, "servicio_nombre": "Cardiología"})))

    def _post(self, **extra):
        body = {"servicio_id": SERVICE["id"], "centro_slug": "hospital-pinamar"}
        body.update(extra)
        return booking_create.create_request(dict(_USER), body)

    def test_valid_references_reach_the_booking_limit(self):
        status, _ = self._post(slot_medico_id="clara")
        self.assertEqual(status, 409)
        self.limit.assert_called_once()

    def test_without_a_slot_doctor_it_still_passes(self):
        self.assertEqual(self._post()[0], 409)

    def test_catalog_unreadable_refuses_instead_of_accepting_any_service(self):
        with patch.object(booking_requests, "_services", return_value=None):
            status, payload = self._post()
        self.assertEqual(status, 503)
        self.assertEqual(payload["codigo"], "catalogo_no_disponible")
        self.limit.assert_not_called()

    def test_unknown_center_is_refused(self):
        status, payload = self._post(centro_slug="no-existe")
        self.assertEqual(status, 400)
        self.assertEqual(payload["codigo"], "centro_inexistente")
        self.limit.assert_not_called()

    def test_center_of_another_service_is_refused(self):
        status, payload = self._post(centro_slug="caps-norte")
        self.assertEqual(status, 400)
        self.assertEqual(payload["codigo"], "centro_no_corresponde")

    def test_center_catalog_unreadable_refuses(self):
        with patch.object(catalog, "centers", side_effect=RuntimeError("boom")):
            status, payload = self._post()
        self.assertEqual(status, 503)
        self.assertEqual(payload["codigo"], "catalogo_no_disponible")

    def test_unknown_slot_doctor_is_refused(self):
        status, payload = self._post(slot_medico_id="inventado")
        self.assertEqual(status, 400)
        self.assertEqual(payload["codigo"], "medico_inexistente")
        self.limit.assert_not_called()

    def test_slot_doctor_outside_the_service_pool_is_refused(self):
        status, payload = self._post(slot_medico_id="tomas")
        self.assertEqual(status, 400)
        self.assertEqual(payload["codigo"], "medico_no_atiende_servicio")
        self.limit.assert_not_called()

    def test_a_test_doctor_in_the_pool_is_refused_like_a_missing_one(self):
        # pinamar review 2026-11-15 item 1: «Laura Prueba (test)» was bookable
        doctors = DOCTORS + [{"id": "laura-prueba-test", "nombre": "Laura Prueba (test)",
                              "especialidad": "Cardiología", "cluster": "cardio",
                              "centro": "hospital-pinamar"}]
        with patch.object(catalog, "doctors", return_value=doctors):
            status, payload = self._post(slot_medico_id="laura-prueba-test")
        self.assertEqual(status, 400)
        self.assertEqual(payload["codigo"], "medico_inexistente")
        self.limit.assert_not_called()

    def test_slot_doctor_check_is_skipped_for_a_lab(self):
        # a lab has no doctor: a stray id is not a reason to refuse
        with patch.object(booking_requests, "_is_lab", return_value=True):
            status, _ = self._post(slot_medico_id="tomas")
        self.assertEqual(status, 409)


    def test_doctor_list_unreadable_is_a_retry_not_a_missing_doctor(self):
        with patch.object(catalog, "doctors", side_effect=RuntimeError("boom")):
            status, payload = self._post(slot_medico_id="clara")
        self.assertEqual(status, 503)
        self.assertEqual(payload["codigo"], "catalogo_no_disponible")

    def test_without_a_center_the_center_list_is_not_needed(self):
        with patch.object(catalog, "centers", side_effect=RuntimeError("boom")):
            status, _ = self._post(centro_slug=None)
        self.assertEqual(status, 409)


class TestApproveWithoutCatalog(unittest.TestCase):
    """The desk always sends the service it shows; an unreadable catalog must
    not block approving the request's own service (it did not before)."""
    ROW = {"id": 7, "servicio_id": "cardiologia", "medico_asignado": ""}

    def _approve(self, servicio_id):
        # `sobreturno` of the wrong type stops the approval right after the
        # service check, before any write.
        body = {"turno_dt": "2026-11-02T09:00:00-03:00", "servicio_id": servicio_id,
                "sobreturno": "x"}
        with patch.object(booking_requests, "_services", return_value=None):
            return booking_decisions._approve_core({}, dict(self.ROW), 7, body)

    def test_service_valid_is_false_when_the_catalog_is_unreadable(self):
        with patch.object(booking_requests, "_services", return_value=None):
            self.assertFalse(booking_requests._service_valid("cardiologia"))

    def test_own_service_still_approves_with_the_catalog_unreadable(self):
        status, payload = self._approve("cardiologia")
        self.assertEqual((status, payload["error"]), (400, "sobreturno_invalido"))

    def test_derivar_to_another_service_waits_for_the_catalog(self):
        status, payload = self._approve("traumatologia")
        self.assertEqual(status, 503)
        self.assertIn(" ", payload["error"])  # a phrase the desk shows as is


if __name__ == "__main__":
    unittest.main()
