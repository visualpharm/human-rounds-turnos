"""test_admin_appointments_router.py — native ASGI contract for
`admin_panel/admin_appointments_router.py` (native-migration campaign,
family F3, docs/roadmap/native-migration-campaign-20260826.md).

Offline only: no Postgres, no network — same fixture strategy as
`tests/test_admin_users_router.py`.
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
from app.admin_panel import admin_appointments_router  # noqa: E402
from app.core import config  # noqa: E402
from app.patient_web import catalog  # noqa: E402

config.ENV_PATH = ""
config._reset_cache_para_tests()


def tearDownModule():
    # Pop ENABLED_MODULES back to unset — NOT "restore whatever
    # os.environ.get('ENABLED_MODULES') returned before this module's
    # import-time os.environ[...] = 'turnos'". A capture-then-restore
    # pattern is unsound here: both unittest's discover() and pytest import
    # every test module up front in a collection phase before running any
    # of them, so a SIBLING file's own import-time mutation (e.g.
    # test_admin_patients_router.py doing the identical thing) can already
    # be sitting in os.environ at the moment this module's top-level code
    # runs, and "restore to captured original" would then just put back
    # that sibling's leaked value instead of true-unset. Unconditionally
    # popping converges on the one value the rest of the suite actually
    # relies on — "unset ENABLED_MODULES means every module enabled" (see
    # core/app_modules.py's enabled_ids()) — regardless of import order.
    #
    # A bare os.environ[...] = "turnos" with no restoration at all used to
    # leak into every test module that ran AFTER this one in the same
    # process (env vars are real process-global state, not per-module).
    # That leak is what made test_w2b_intake_native_routers.py see "intake"
    # as disabled only during the full backend suite (passing fine in
    # isolation): this module runs early alphabetically and pinned
    # ENABLED_MODULES=turnos for the rest of the run, so any later module
    # reading config.get("ENABLED_MODULES") with no DB override (a fresh
    # throwaway test db has none) fell through to this stale env value.
    _env.process_set(ENABLED_MODULES=None)
    config._reset_cache_para_tests()

_FIXTURE_TOKEN = "fixture-admin"
_FIXTURE_IDENTITY = {"usuario": "admin", "rol": "admin"}
_SERVICIO = {"id": "svc-1", "nombre": "Clinica general", "centro": "sede-1", "cluster": "clinica"}


def _verify_token_fixture(token):
    if token == _FIXTURE_TOKEN:
        return dict(_FIXTURE_IDENTITY)
    return None


class TestNativeAdminAppointmentsRouter(unittest.TestCase):
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
        self._centros_patch = patch.object(admin_auth, "centros_permitidos", return_value=None)
        self._centros_patch.start()
        self.addCleanup(self._centros_patch.stop)

        app = FastAPI()
        app.include_router(admin_appointments_router.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.cookies = {"st_admin": _FIXTURE_TOKEN}

    def test_slots_invalid_service(self):
        with patch.object(catalog, "services", return_value=[]):
            native = self.client.get(
                "/api/admin/appointment-slots?servicio_id=missing", cookies=self.cookies,
            )
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"error": "servicio_id inválido"})

    def test_slots_no_appointments_offered(self):
        from app.patient_web import site_availability
        with patch.object(catalog, "services", return_value=[_SERVICIO]), \
             patch.object(site_availability, "_offers_appointments", return_value=False):
            native = self.client.get(
                "/api/admin/appointment-slots?servicio_id=svc-1", cookies=self.cookies,
            )
        self.assertEqual(native.json(), {"libres": [], "sobreturnos": []})

    def test_create_invalid_service(self):
        body = {"dni": "20123456", "servicio_id": "missing", "dia": "2026-09-01", "hora": "10:00"}
        from app.core import db_accounts
        with patch.object(catalog, "services", return_value=[]), \
             patch.object(db_accounts, "get_user", return_value={
                 "dni": "20123456", "activo": True, "nombre": "Ana", "apellido": "Gomez",
             }):
            native = self.client.post("/api/admin/appointments", json=body, cookies=self.cookies)
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"error": "servicio_id inválido"})

    def test_create_patient_not_found(self):
        body = {"dni": "20123456", "servicio_id": "svc-1", "dia": "2026-09-01", "hora": "10:00"}
        from app.core import db_accounts
        with patch.object(db_accounts, "get_user", return_value=None):
            native = self.client.post("/api/admin/appointments", json=body, cookies=self.cookies)
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "paciente_no_encontrado"})

    def test_module_disabled_is_404(self):
        from app.core import app_modules
        with patch.object(app_modules, "is_enabled", return_value=False):
            native = self.client.get("/api/admin/appointment-slots", cookies=self.cookies)
        self.assertEqual(native.status_code, 404)


if __name__ == "__main__":
    unittest.main()
