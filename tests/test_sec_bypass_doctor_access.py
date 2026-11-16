"""Security review 2026-11, bypasses of M-2: a doctor keeps no hold on a refused or cancelled request.

The access check (`db_clinical.doctor_has_patient`) already turned a rejected/cancelled request away;
the order file and the case chat read the same row through `staff_can_see` / `my_referral_data`,
which only asked whether the row was assigned to the doctor.
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.modules.turnos import booking_requests, doctor_appointments  # noqa: E402
from app.core.status_error import BodyStatusError  # noqa: E402,F401


def _row(estado):
    return {"id": 5, "dni": "30111222", "estado": estado, "medico_asignado_id": "7",
            "medico_asignado": "Dra. Paz", "centro_slug": "hospital"}


class DeadRequestIsClosedToTheDoctor(unittest.TestCase):
    identity = {"usuario": "dra", "rol": "medico"}

    def setUp(self):
        for target, value in (
                ("app.modules.turnos.booking_requests.store.operator_doctor_id", "7"),
                ("app.modules.turnos.doctor_appointments.doctor_of", {"id": "7", "nombre": "Dra. Paz"}),
                ("app.modules.turnos.booking_requests.admin_auth.centros_permitidos", None)):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_staff_can_see_an_assigned_live_request(self):
        for estado in ("pendiente", "aprobada"):
            self.assertTrue(booking_requests.staff_can_see(self.identity, _row(estado)), estado)

    def test_staff_can_see_refuses_a_rejected_or_cancelled_request_to_the_doctor(self):
        for estado in ("rechazada", "cancelada"):
            self.assertFalse(booking_requests.staff_can_see(self.identity, _row(estado)), estado)

    def test_the_desk_still_sees_them(self):
        admin = {"usuario": "adm", "rol": "admin"}
        for estado in ("rechazada", "cancelada"):
            self.assertTrue(booking_requests.staff_can_see(admin, _row(estado)), estado)

    def test_my_referral_data_is_a_404_for_a_rejected_or_cancelled_request(self):
        for estado in ("rechazada", "cancelada"):
            with patch("app.modules.turnos.doctor_appointments.db_operators.operator_doctor_id", return_value="7"), \
                    patch("app.modules.turnos.doctor_appointments.db_booking.get_booking_request",
                          return_value=_row(estado)), \
                    patch("app.core.access_log.record") as record, \
                    patch.object(doctor_appointments, "_referral_file") as file_:
                with self.assertRaises(doctor_appointments.DomainError) as cm:
                    doctor_appointments.my_referral_data(self.identity, 5)
            self.assertEqual(cm.exception.status, 404)
            file_.assert_not_called()
            self.assertFalse(record.call_args.kwargs["allowed"])


if __name__ == "__main__":
    unittest.main()
