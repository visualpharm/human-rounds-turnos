"""test_admin_patients_router.py — native ASGI contract for
`admin_panel/admin_patients_router.py` (native-migration campaign, family
F3, docs/roadmap/native-migration-campaign-20260826.md).

Offline only: no Postgres, no network — same fixture strategy as
`tests/test_admin_users_router.py` (patched `_verify_token`/
`account_security`, patched `core.db` calls the domain functions make).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_defaults(TURNOS_ADMIN_TOTP_SECRET="")
_env.process_set(ENABLED_MODULES="turnos")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import admin_auth  # noqa: E402
from app.admin_panel import admin_patients_router  # noqa: E402
from app.core import config, db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_slots  # noqa: E402

config.ENV_PATH = ""
config._reset_cache_para_tests()


def tearDownModule():
    # Same leak, same fix as test_admin_appointments_router.py's
    # tearDownModule (see its comment for the full "why unconditional pop,
    # not capture-and-restore" reasoning — collection-phase import order
    # between sibling test files makes a captured "original" unreliable).
    # This module hard-set ENABLED_MODULES=turnos at import time with no
    # restoration, which otherwise persists as real process-global state
    # into every test module that runs afterward in the same `unittest
    # discover` process — the root cause of test_w2b_intake_native_routers.py
    # seeing "intake" as disabled only during the full backend suite.
    _env.process_set(ENABLED_MODULES=None)
    config._reset_cache_para_tests()

_FIXTURE_TOKEN = "fixture-admin"
_FIXTURE_IDENTITY = {"usuario": "admin", "rol": "admin"}
_FIXTURE_USER = {
    "dni": "20123456", "nombre": "Ana", "apellido": "Gomez", "activo": True,
    "cobertura": None, "domicilio": None, "contacto": None, "email": None,
    "nacimiento": None, "sexo": "F",
}


def _verify_token_fixture(token):
    if token == _FIXTURE_TOKEN:
        return dict(_FIXTURE_IDENTITY)
    return None


class TestNativeAdminPatientsRouter(unittest.TestCase):
    def setUp(self):
        self._verify_patch = patch.object(admin_auth, "_verify_token", side_effect=_verify_token_fixture)
        self._verify_patch.start()
        self.addCleanup(self._verify_patch.stop)
        self._security_patch = patch.object(
            admin_auth, "account_security",
            return_value={"tiene_2fa": True, "2fa_deadline": None, "2fa_vencido": False},
        )
        self._security_patch.start()
        self.addCleanup(self._security_patch.stop)

        app = FastAPI()
        app.include_router(admin_patients_router.router)
        from app.core import deps as _early_deps
        _early_deps.install_early_response(app)  # the app's answer of a dependency
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.cookies = {"st_admin": _FIXTURE_TOKEN}

    def test_detail_not_found(self):
        with patch.object(db_accounts, "get_user", return_value=None):
            native = self.client.get("/api/admin/patients/20123456", cookies=self.cookies)
        self.assertEqual(native.status_code, 404)

    def test_detail_success(self):
        with patch.object(db_accounts, "get_user", return_value=dict(_FIXTURE_USER)), \
             patch.object(db_slots, "list_dependents", return_value=[]), \
             patch.object(db_slots, "adults_of_dependent", return_value=[]), \
             patch.object(admin_patients_router.admin_patients, "patient_turnos", return_value=[{"id": 1}]) as turnos, \
             patch.object(db_clinical, "insert_clinical_access") as acceso:
            native = self.client.get("/api/admin/patients/20123456", cookies=self.cookies)
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json()["item"]["dni"], "20123456")
        # Salud 30.10: la ficha trae los turnos del paciente, vistos con la identidad de quien abre.
        self.assertEqual(native.json()["turnos"], [{"id": 1}])
        self.assertEqual(turnos.call_args.args[0], "20123456")
        self.assertEqual(turnos.call_args.args[1]["usuario"], "admin")
        # K10: abrir la ficha deja su fila en el registro de accesos.
        self.assertEqual(acceso.call_args.args[2], "ficha")

    def test_detail_refused_when_the_access_log_cannot_be_written(self):
        with patch.object(db_accounts, "get_user", return_value=dict(_FIXTURE_USER)), \
             patch.object(db_slots, "list_dependents", return_value=[]), \
             patch.object(db_slots, "adults_of_dependent", return_value=[]), \
             patch.object(db_clinical, "insert_clinical_access", side_effect=db.DbUnavailable("x")):
            native = self.client.get("/api/admin/patients/20123456", cookies=self.cookies)
        self.assertEqual(native.status_code, 503)
        self.assertNotIn("item", native.json())

    def test_non_numeric_dni_is_terminal_404(self):
        # FastAPI's `{dni}` path template matches ANY segment; the module's
        # own `_ITEM_RE` shape check (7-9 digits) is what turns a non-numeric
        # value into the generic terminal 404 — not FastAPI's default shape.
        native = self.client.get("/api/admin/patients/not-a-dni", cookies=self.cookies)
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.content, b'{"error": "not found"}')
        self.assertEqual(native.headers.get("cache-control"), "no-store")

    def test_archive_not_found(self):
        with patch.object(db_accounts, "get_user", return_value=None):
            native = self.client.delete("/api/admin/patients/20123456", cookies=self.cookies)
        self.assertEqual(native.status_code, 404)

    def test_create_missing_fields(self):
        body = {"dni": "20123456"}
        with patch.object(db_accounts, "get_user", return_value=None):
            native = self.client.post("/api/admin/patients", json=body, cookies=self.cookies)
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"error": "Faltan nombre y apellido."})

    # TASK-50 (26.10): a patient without email who forgot the password had no
    # way back in. The desk gives a new temporary password from the record —
    # same hands as creating a patient, shown once, the action in the access
    # log, the old password and every open session gone.
    def test_temp_password_sets_a_new_one_shown_once_and_logged(self):
        with patch.object(db_accounts, "get_user", return_value=dict(_FIXTURE_USER)), \
             patch.object(db_accounts, "set_password") as fijar, \
             patch.object(db_clinical, "insert_clinical_access") as acceso:
            native = self.client.post("/api/admin/patients/20123456/temp-password", cookies=self.cookies)
        self.assertEqual(native.status_code, 200, native.text)
        temp = native.json()["password_temporal"]
        self.assertGreaterEqual(len(temp), 8)
        fijar.assert_called_once_with("20123456", temp)
        self.assertEqual(acceso.call_args.args[2], "contrasena")
        self.assertEqual(native.headers.get("cache-control"), "no-store")

    def test_temp_password_unknown_patient_is_404_and_changes_nothing(self):
        with patch.object(db_accounts, "get_user", return_value=None), \
             patch.object(db_accounts, "set_password") as fijar, \
             patch.object(db_clinical, "insert_clinical_access"):
            native = self.client.post("/api/admin/patients/20123456/temp-password", cookies=self.cookies)
        self.assertEqual(native.status_code, 404)
        fijar.assert_not_called()

    def test_temp_password_not_set_when_the_log_cannot_be_written(self):
        with patch.object(db_accounts, "get_user", return_value=dict(_FIXTURE_USER)), \
             patch.object(db_accounts, "set_password") as fijar, \
             patch.object(db_clinical, "insert_clinical_access", side_effect=db.DbUnavailable("x")):
            native = self.client.post("/api/admin/patients/20123456/temp-password", cookies=self.cookies)
        self.assertEqual(native.status_code, 503)
        fijar.assert_not_called()

    def test_temp_password_refused_to_a_doctor(self):
        with patch.object(admin_auth, "_verify_token", return_value={"usuario": "doc", "rol": "medico"}), \
             patch.object(db_accounts, "get_user", return_value=dict(_FIXTURE_USER)), \
             patch.object(db_accounts, "set_password") as fijar, \
             patch.object(db_clinical, "insert_clinical_access"):
            native = self.client.post("/api/admin/patients/20123456/temp-password", cookies=self.cookies)
        self.assertEqual(native.status_code, 403)
        fijar.assert_not_called()

    # 2026-11-02 (the hospital's technical contact: «posibilidad de editar esos pacientes que no
    # tienen nombre»): the desk can already fill in a name from the record; the
    # edit is now written to the access log BEFORE it happens, like a read.
    def test_edit_records_the_edit_in_the_access_log_before_saving(self):
        from app.admin_panel import admin_patients
        orden = []
        with patch.object(db_accounts, "get_user", return_value=dict(_FIXTURE_USER)), \
             patch.object(db_clinical, "insert_clinical_access",
                          side_effect=lambda *a, **k: orden.append("registro")) as acceso, \
             patch.object(admin_patients, "edit_patient",
                          side_effect=lambda *a, **k: orden.append("edicion") or {"ok": True}) as editar:
            native = self.client.put("/api/admin/patients/20123456",
                                     json={"nombre": "Ana", "apellido": "Gomez"}, cookies=self.cookies)
        self.assertEqual(native.status_code, 200, native.text)
        self.assertEqual(acceso.call_args.args[2], "edicion")
        editar.assert_called_once()
        self.assertEqual(orden, ["registro", "edicion"])

    def test_edit_is_refused_when_the_log_cannot_be_written(self):
        from app.admin_panel import admin_patients
        with patch.object(db_accounts, "get_user", return_value=dict(_FIXTURE_USER)), \
             patch.object(db_clinical, "insert_clinical_access", side_effect=db.DbUnavailable("x")), \
             patch.object(admin_patients, "edit_patient") as editar:
            native = self.client.put("/api/admin/patients/20123456",
                                     json={"nombre": "Ana", "apellido": "Gomez"}, cookies=self.cookies)
        self.assertEqual(native.status_code, 503)
        editar.assert_not_called()

    def test_edit_unknown_patient_is_404_without_a_log_row(self):
        from app.admin_panel import admin_patients
        with patch.object(db_accounts, "get_user", return_value=None), \
             patch.object(db_clinical, "insert_clinical_access") as acceso, \
             patch.object(admin_patients, "edit_patient") as editar:
            native = self.client.put("/api/admin/patients/20123456",
                                     json={"nombre": "Ana", "apellido": "Gomez"}, cookies=self.cookies)
        self.assertEqual(native.status_code, 404)
        acceso.assert_not_called()
        editar.assert_not_called()

    def test_module_disabled_is_404(self):
        from app.core import app_modules
        with patch.object(app_modules, "is_enabled", return_value=False):
            native = self.client.get("/api/admin/patients", cookies=self.cookies)
        self.assertEqual(native.status_code, 404)


if __name__ == "__main__":
    unittest.main()
