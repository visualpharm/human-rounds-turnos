"""tests/routes/test_body_contract_admin_requests.py — the staff's actions on
a booking request (`modules/turnos/booking_requests_router.py`), a desk
appointment (`admin_panel/admin_appointments_router.py`) and the doctor's
confirmation of a pre-consult (`modules/intake/consult_admin_router.py`):
every answer to the body battery of `_body_contract.py`, pinned against
`tests/golden/body_contract_admin_requests.json` (TASK-24, Pydantic stage 5).

Each case starts from a truncated database with one pending request (id 1)
the patient booked through the API, and an admin operator.
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _body_contract as bc  # noqa: E402

from app.admin_panel import api_models as p  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.modules.intake import api_models as i  # noqa: E402
from app.modules.turnos import api_models as t  # noqa: E402

_DNI = "30999311"
_SERVICIO = "medicina-general-riverside-clinic"
_CENTRO = "riverside-clinic"
_USUARIO = "staff.acciones"
_GOLDEN = bc.Golden("admin_requests")


class TestAdminRequestsBodyContract(RouteTestCase):

    @classmethod
    def tearDownClass(cls):
        if bc.WRITE:
            _GOLDEN.save()
        super().tearDownClass()

    def _staff(self):
        from app.modules.turnos import slot_holds
        self.db.truncate_all()
        slot_holds.invalidate()
        db_accounts.create_user(_DNI, "Marta", "Gómez", "1980-05-10", "F", "+542254000311",
                       "throwaway-12345")
        patient = {"Cookie": "st_ses=" + db_accounts.create_session(_DNI)}
        status, _, raw = bc.request(
            self._base_url, "POST", "/api/requests",
            json.dumps({"servicio_id": _SERVICIO, "centro_slug": _CENTRO}).encode("utf-8"),
            patient)
        assert status == 200 and json.loads(raw)["id"] == 1, raw
        db_operators.operator_create(_USUARIO, password="clave-staff-12", rol="admin")
        return admin_cookie(usuario=_USUARIO, rol="admin")

    def _pin(self, path, valid, docs=None, refuses=True):
        bc.pin_route(self, _GOLDEN, "POST", path, valid, reset=self._staff, auth=True,
                     docs=docs, refuses=refuses)

    def test_no_body_crashes_a_route(self):
        """Since stage 5 a body the panel never sends is a 400 of the family, not
        a 500: the golden file holds no 500 at all."""
        crashes = [(route, case) for route, cases in _GOLDEN.sections.items()
                   for case, got in cases.items() if got["status"] == 500]
        self.assertEqual(crashes, [])

    def test_approve(self):
        self._pin("/api/admin/requests/1/approve",
                  {"turno_dt": "2030-01-07T10:00", "servicio_id": _SERVICIO,
                   "medico": "Juan González", "sobreturno": False},
                  docs=(t.ApproveIn, t.StaffRequestOut))

    def test_assign(self):
        self._pin("/api/admin/requests/1/assign", {"medico_id": "gonzalez-juan", "medico": ""},
                  docs=(t.AssignIn, t.StaffRequestOut))

    def test_reject(self):
        self._pin("/api/admin/requests/1/reject", {"motivo": "sin cupo este mes"},
                  docs=(t.RejectIn, t.StaffRequestOut))

    def test_edit_field(self):
        self._pin("/api/admin/requests/1/campos", {"campo": "diagnostico", "valor": "control anual"},
                  docs=(t.EditFieldIn, None))

    def test_patient_data(self):
        self._pin("/api/admin/requests/1/patient-data",
                  {"nombre": "Marta", "apellido": "Gómez", "nacimiento": "1980-05-10",
                   "sexo": "F"},
                  docs=(t.PatientDataIn, t.PatientDataOut))

    def test_attendance(self):
        self._pin("/api/admin/requests/1/attendance", {"asistencia": "presente"},
                  docs=(t.AttendanceIn, None))

    def test_hsi_conflicts(self):
        # No EMR connector is selected here: the route answers its 404 before it reads the body, every case.
        self._pin("/api/admin/hsi-sync/conflicts", {"accion": "aplicar", "conflictos": [1]},
                  docs=(t.HsiConflictsIn, None), refuses=False)

    def test_desk_appointment(self):
        self._pin("/api/admin/appointments",
                  {"dni": _DNI, "servicio_id": _SERVICIO, "dia": "2030-01-07", "hora": "10:00",
                   "medico": "Juan González", "nota": "control", "sobreturno": False},
                  docs=(p.DeskAppointmentIn, p.DeskAppointmentOut))

    def test_consult_confirm(self):
        self._pin("/api/admin/intake/consult/1/confirm",
                  {"diagnosticos": [], "estudios": [], "medicacion": [], "notas": "ok",
                   "profile_reviewed": False, "reemplaza_id": None},
                  docs=(i.ConsultConfirmIn, i.ConsultConfirmOut))


if __name__ == "__main__":
    unittest.main()
