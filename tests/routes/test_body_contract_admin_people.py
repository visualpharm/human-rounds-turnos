"""tests/routes/test_body_contract_admin_people.py — the panel's people JSON
routes: patients (`admin_panel/admin_patients_router.py`), staff accounts
(`admin_panel/admin_users_router.py`) and roles (`admin_panel/roles_router.py`):
every answer to the body battery of `_body_contract.py`, pinned against
`tests/golden/body_contract_admin_people.json` (TASK-24, Pydantic stage 6).

Each case starts from a truncated database with an admin, a doctor account,
one patient and one custom role.
"""

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
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402

_DNI = "30999411"
_ADMIN = "staff.personas"
_MEDICO = "medico.personas"
_GOLDEN = bc.Golden("admin_people")


class TestAdminPeopleBodyContract(RouteTestCase):

    @classmethod
    def tearDownClass(cls):
        if bc.WRITE:
            _GOLDEN.save()
        super().tearDownClass()

    def _staff(self):
        from app.core import permissions
        self.db.truncate_all()
        db_operators.operator_create(_ADMIN, password="clave-staff-12", rol="admin")
        db_operators.operator_create(_MEDICO, password="clave-medi-12", rol="medico")
        db_accounts.create_user(_DNI, "Ana", "Paz", "1990-01-02", "F", "+542254000411", "clave-pac-123")
        self._role = permissions.create_role("Mesa de tarde", "recepción", [])["slug"]
        return admin_cookie(usuario=_ADMIN, rol="admin")

    def _pin(self, path, valid, method="POST", docs=None, key=None):
        bc.pin_route(self, _GOLDEN, method, path, valid, reset=self._staff, auth=True,
                     docs=docs, key=key)

    def test_no_body_crashes_a_route(self):
        """Since stage 6 a body the panel never sends is a 400 of the family, not
        a 500: the golden file holds no 500 at all."""
        crashes = [(route, case) for route, cases in _GOLDEN.sections.items()
                   for case, got in cases.items() if got["status"] == 500]
        self.assertEqual(crashes, [])

    def test_patient_create(self):
        self._pin("/api/admin/patients",
                  {"dni": "30999412", "password": "clave-pac-123", "nombre": "Eva",
                   "apellido": "Ruiz", "contacto": "+542254000412", "email": "eva@example.com",
                   "sexo": "F", "nacimiento": "1991-02-03", "activo": True},
                  docs=(p.PatientIn, p.ItemOut))

    def test_patient_edit(self):
        self._pin(f"/api/admin/patients/{_DNI}",
                  {"nombre": "Ana", "apellido": "Paz", "contacto": "+542254000411",
                   "email": "ana@example.com", "sexo": "F", "nacimiento": "1990-01-02",
                   "activo": True},
                  method="PUT",
                  docs=(p.PatientIn, p.ItemOut))

    def test_patient_dependent(self):
        self._pin(f"/api/admin/patients/{_DNI}/dependents",
                  {"dni": "40999411", "nombre": "Leo", "apellido": "Paz",
                   "nacimiento": "2015-05-05", "sexo": "M", "vinculo": "hijo"},
                  docs=(p.PatientDependentIn, p.ItemOut))

    def test_patient_clinical_history(self):
        self._pin(f"/api/admin/patients/{_DNI}/clinical-history",
                  {"records": [], "source_system": "local_history"},
                  docs=(p.ClinicalHistoryIn, p.ClinicalHistoryOut))

    def test_user_create(self):
        self._pin("/api/admin/users",
                  {"usuario": "nuevo.medico", "password": "clave-nuevo-12", "rol": "medico",
                   "access_mode": "password", "totp": False, "forzar_2fa": False,
                   "medico_id": "gonzalez-juan", "email": "nuevo@example.com", "permisos_agregados": [],
                   "permisos_quitados": [], "centros": []},
                  docs=(p.StaffUserIn, p.StaffUserOut))

    def test_user_action(self):
        self._pin("/api/admin/users/actions/disable",
                  {"medico_ids": ["gonzalez-juan"], "emails": {}, "usuario": "", "email": ""},
                  docs=(p.StaffUsersActionIn, p.StaffUsersActionOut))

    def test_user_edit(self):
        self._pin(f"/api/admin/users/{_MEDICO}",
                  {"rol": "medico", "email": "medico@example.com", "medico_id": "gonzalez-juan",
                   "centros": [], "activo": True, "permisos_agregados": [],
                   "permisos_quitados": [], "forzar_2fa": False},
                  method="PUT",
                  docs=(p.StaffUserIn, p.StaffUserOut))

    def test_role_create(self):
        self._pin("/api/admin/roles",
                  {"name": "Recepción", "description": "mesa de entrada",
                   "permissions": ["pacientes"]},
                  docs=(p.RoleIn, p.RoleOut))

    def test_role_edit(self):
        self._pin(lambda: f"/api/admin/roles/{self._role}",
                  {"name": "Mesa de tarde", "description": "recepción",
                   "permissions": ["pacientes"], "enabled": True},
                  method="PUT", key="PUT /api/admin/roles/{slug}",
                  docs=(p.RoleIn, p.RoleOut))


if __name__ == "__main__":
    unittest.main()
