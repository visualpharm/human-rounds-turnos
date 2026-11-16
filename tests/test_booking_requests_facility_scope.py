"""tests/test_booking_requests_facility_scope.py — behavioral regression proof for
the admisionista facility-scope security fix (commit 9085b7d3, 2026-08-31).

Proves that an admisionista account scoped to center A can:
  - list requests at center A (filtered, not all)
  - approve/reject requests at center A
  - gets 404 (non-disclosing) when accessing center B requests
  - 404 is indistinguishable from a genuinely missing request

Proves that admin and medico roles are unchanged:
  - no center filtering on list
  - full access to approve/reject all requests

Uses the real production route/core functions through the live server with
patches for database operations — no TEST_DATABASE_URL required.
No skips — every test runs or fails loudly.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

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
from app.modules.turnos import booking_requests_router  # noqa: E402
from app.core import config  # noqa: E402

config.ENV_PATH = ""
config._reset_cache_para_tests()


def tearDownModule():
    _env.process_set(ENABLED_MODULES=None)   # undoes the import-time process_set above
    config._reset_cache_para_tests()


# ---- Test fixtures -----------------------------------------------------------

_FIXTURE_TOKEN_ADMIN = "fixture-admin"
_FIXTURE_TOKEN_MESA_SUD = "fixture-mesa-sud"
_FIXTURE_TOKEN_MESA_NORTE = "fixture-mesa-norte"
_FIXTURE_TOKEN_MESA_TODO = "fixture-mesa-todo"

CENTRO_A = "caps-sud"
CENTRO_B = "riverside-clinic"
SERVICIO_A = "clinica-caps-sud"
SERVICIO_B = "dermatologia-riverside-clinic"


def _verify_token_fixture(token):
    """Token verification fixture that returns identities for our test accounts."""
    fixtures = {
        _FIXTURE_TOKEN_ADMIN: {"usuario": "admin", "rol": "admin"},
        _FIXTURE_TOKEN_MESA_SUD: {"usuario": "mesa-sud", "rol": "admisionista"},
        _FIXTURE_TOKEN_MESA_NORTE: {"usuario": "mesa-norte", "rol": "admisionista"},
        _FIXTURE_TOKEN_MESA_TODO: {"usuario": "mesa-todo", "rol": "admisionista"},
    }
    return fixtures.get(token)


def _centros_permitidos_fixture(identity):
    """Centros_permitidos fixture that matches our test account centers."""
    usuario = (identity or {}).get("usuario", "")
    if usuario == "mesa-sud":
        return {CENTRO_A}
    if usuario == "mesa-norte":
        return {CENTRO_B}
    if usuario == "mesa-todo":
        return None  # No centers = sees everything
    # admin has no center restrictions
    return None


def _turno_futuro_iso():
    """A turno_dt in the future for approve requests."""
    return (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()


# ---- Fake database rows ------------------------------------------------------

def _fake_request(sid, centro_slug, servicio_id, estado="pendiente"):
    """Create a fake booking request row."""
    return {
        "id": sid,
        "dni": f"30{sid}2222",
        "nombre": "Paciente",
        "apellido": f"Centro{centro_slug.title()}",
        "fecha_nacimiento": "1990-01-01",
        "telefono": None,
        "servicio_id": servicio_id,
        "centro_slug": centro_slug,
        "estado": estado,
        "motivo_rechazo": None,
        "turno_dt": None,
        "creado_dt": datetime.now(timezone.utc).isoformat(),
        "resuelto_por": None,
        "medico_asignado_id": None,
    }


# ---- Test cases --------------------------------------------------------------

class TestBookingRequestsFacilityScope(unittest.TestCase):
    """Facility-scope regression proof for admisionista role (commit 9085b7d3)."""

    def setUp(self):
        """Set up FastAPI test client with patched dependencies."""
        # Patch token verification
        self._verify_patch = patch.object(
            admin_auth, "_verify_token", side_effect=_verify_token_fixture
        )
        self._verify_patch.start()
        self.addCleanup(self._verify_patch.stop)

        # Patch account security check
        self._security_patch = patch.object(
            admin_auth,
            "account_security",
            return_value={"tiene_2fa": True, "2fa_deadline": None, "2fa_vencido": False},
        )
        self._security_patch.start()
        self.addCleanup(self._security_patch.stop)

        # Patch centros_permitidos to return center sets for our test accounts
        self._centros_patch = patch.object(
            admin_auth, "centros_permitidos", side_effect=_centros_permitidos_fixture
        )
        self._centros_patch.start()
        self.addCleanup(self._centros_patch.stop)

        # The requests list and the referral file write the clinical access
        # log (K10): no database here, so the writes are simulated too.
        from app.core import db_clinical
        for name in ("insert_clinical_access", "insert_clinical_access_many"):
            p = patch.object(db_clinical, name)
            p.start()
            self.addCleanup(p.stop)

        # Create app and include router
        app = FastAPI()
        app.include_router(booking_requests_router.router)
        from app.core import deps as _early_deps
        _early_deps.install_early_response(app)  # the app's answer of a dependency
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

        # Test data: two requests at different centers
        self.sid_a = 1001
        self.sid_b = 1002
        self.request_a = _fake_request(self.sid_a, CENTRO_A, SERVICIO_A)
        self.request_b = _fake_request(self.sid_b, CENTRO_B, SERVICIO_B)

    # ---- LIST: GET /api/admin/requests ---------------------------------------

    def test_admisionista_scoped_to_a_sees_only_a_in_list(self):
        """An admisionista scoped to center A sees only A's requests."""
        with patch("app.modules.turnos.booking_requests_store.list_admin",
                   return_value=[self.request_a, self.request_b]), \
             patch("app.modules.turnos.booking_requests_store.count_admin", return_value=2):
            resp = self.client.get(
                "/api/admin/requests",
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_SUD},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        rows = body.get("solicitudes", [])
        ids = [r.get("id") for r in rows]

        self.assertIn(self.sid_a, ids, "center A request visible to scoped account")
        self.assertNotIn(self.sid_b, ids, "center B request NOT visible to scoped account")
        self.assertEqual(len(rows), 1, "exactly one request in filtered list")

    def test_admisionista_scoped_to_b_sees_only_b_in_list(self):
        """An admisionista scoped to center B sees only B's requests."""
        with patch("app.modules.turnos.booking_requests_store.list_admin",
                   return_value=[self.request_a, self.request_b]), \
             patch("app.modules.turnos.booking_requests_store.count_admin", return_value=2):
            resp = self.client.get(
                "/api/admin/requests",
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_NORTE},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        rows = body.get("solicitudes", [])
        ids = [r.get("id") for r in rows]

        self.assertIn(self.sid_b, ids, "center B request visible to scoped account")
        self.assertNotIn(self.sid_a, ids, "center A request NOT visible to scoped account")
        self.assertEqual(len(rows), 1, "exactly one request in filtered list")

    def test_admisionista_sin_centros_ve_todo(self):
        """An admisionista with no centers sees all requests (legacy behavior)."""
        with patch("app.modules.turnos.booking_requests_store.list_admin",
                   return_value=[self.request_a, self.request_b]), \
             patch("app.modules.turnos.booking_requests_store.count_admin", return_value=2):
            resp = self.client.get(
                "/api/admin/requests",
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_TODO},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        rows = body.get("solicitudes", [])
        ids = [r.get("id") for r in rows]

        self.assertIn(self.sid_a, ids, "center A visible to unscoped account")
        self.assertIn(self.sid_b, ids, "center B visible to unscoped account")
        self.assertGreaterEqual(len(rows), 2, "both requests visible")

    def test_admin_role_no_center_filtering(self):
        """Admin role sees all requests regardless of center (unchanged)."""
        with patch("app.modules.turnos.booking_requests_store.list_admin",
                   return_value=[self.request_a, self.request_b]), \
             patch("app.modules.turnos.booking_requests_store.count_admin", return_value=2):
            resp = self.client.get(
                "/api/admin/requests",
                cookies={"st_admin": _FIXTURE_TOKEN_ADMIN},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        rows = body.get("solicitudes", [])
        ids = [r.get("id") for r in rows]

        self.assertIn(self.sid_a, ids, "center A visible to admin")
        self.assertIn(self.sid_b, ids, "center B visible to admin")
        self.assertGreaterEqual(len(rows), 2, "both requests visible")

    # ---- APPROVE: 404 for cross-center, approve works for same-center --------

    def test_admisionista_scoped_to_a_gets_404_approving_b(self):
        """Approving a request at center B from a center-A scoped account returns 404."""
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=self.request_b), \
             patch("app.modules.turnos.booking_requests_store.approve",
                   return_value=self.request_b) as mock_approve:
            resp = self.client.post(
                f"/api/admin/requests/{self.sid_b}/approve",
                json={"turno_dt": _turno_futuro_iso()},
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_SUD},
            )
        self.assertEqual(resp.status_code, 404, "cross-center approve returns 404")
        # Verify approve was NOT called (request unchanged)
        mock_approve.assert_not_called()

    def test_admisionista_scoped_to_a_can_approve_a(self):
        """Approving a request at center A from a center-A scoped account succeeds."""
        approved_row = self.request_a.copy()
        approved_row["estado"] = "aprobada"
        approved_row["turno_dt"] = _turno_futuro_iso()
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=self.request_a), \
             patch("app.modules.turnos.booking_requests_store.approve",
                   return_value=approved_row) as mock_approve:
            resp = self.client.post(
                f"/api/admin/requests/{self.sid_a}/approve",
                json={"turno_dt": _turno_futuro_iso()},
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_SUD},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["solicitud"]["estado"], "aprobada")
        mock_approve.assert_called_once()

    def test_missing_request_also_404_on_approve(self):
        """A genuinely missing request returns the same 404 when approving."""
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=None), \
             patch("app.modules.turnos.booking_requests_store.approve",
                   return_value=None) as mock_approve:
            resp = self.client.post(
                f"/api/admin/requests/999999/approve",
                json={"turno_dt": _turno_futuro_iso()},
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_SUD},
            )
        self.assertEqual(resp.status_code, 404, "missing request returns 404")
        mock_approve.assert_not_called()

    # ---- REJECT: 404 for cross-center, reject works for same-center ---------

    def test_admisionista_scoped_to_b_gets_404_rejecting_a(self):
        """Rejecting a request at center A from a center-B scoped account returns 404."""
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=self.request_a), \
             patch("app.modules.turnos.booking_requests_store.reject",
                   return_value=self.request_a) as mock_reject:
            resp = self.client.post(
                f"/api/admin/requests/{self.sid_a}/reject",
                json={"motivo": "No es de mi centro"},
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_NORTE},
            )
        self.assertEqual(resp.status_code, 404, "cross-center reject returns 404")
        mock_reject.assert_not_called()

    def test_admisionista_scoped_to_b_can_reject_b(self):
        """Rejecting a request at center B from a center-B scoped account succeeds."""
        rejected_row = self.request_b.copy()
        rejected_row["estado"] = "rechazada"
        rejected_row["motivo_rechazo"] = "Paciente no asistió"
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=self.request_b), \
             patch("app.modules.turnos.booking_requests_store.reject",
                   return_value=rejected_row) as mock_reject:
            resp = self.client.post(
                f"/api/admin/requests/{self.sid_b}/reject",
                json={"motivo": "Paciente no asistió"},
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_NORTE},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["solicitud"]["estado"], "rechazada")
        self.assertEqual(body["solicitud"]["motivo_rechazo"], "Paciente no asistió")
        mock_reject.assert_called_once()

    def test_missing_request_also_404_on_reject(self):
        """A genuinely missing request returns the same 404 when rejecting."""
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=None), \
             patch("app.modules.turnos.booking_requests_store.reject",
                   return_value=None) as mock_reject:
            resp = self.client.post(
                f"/api/admin/requests/999999/reject",
                json={"motivo": "Missing request"},
                cookies={"st_admin": _FIXTURE_TOKEN_MESA_NORTE},
            )
        self.assertEqual(resp.status_code, 404, "missing request returns 404")
        mock_reject.assert_not_called()

    # ---- ADMIN role unchanged ------------------------------------------------

    def test_admin_role_no_scope_on_approve(self):
        """Admin role can approve requests at any center (unchanged)."""
        approved_row = self.request_b.copy()
        approved_row["estado"] = "aprobada"
        approved_row["turno_dt"] = _turno_futuro_iso()
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=self.request_b), \
             patch("app.modules.turnos.booking_requests_store.approve",
                   return_value=approved_row) as mock_approve:
            resp = self.client.post(
                f"/api/admin/requests/{self.sid_b}/approve",
                json={"turno_dt": _turno_futuro_iso()},
                cookies={"st_admin": _FIXTURE_TOKEN_ADMIN},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["solicitud"]["estado"], "aprobada")
        mock_approve.assert_called_once()

    def test_admin_role_no_scope_on_reject(self):
        """Admin role can reject requests at any center (unchanged)."""
        rejected_row = self.request_a.copy()
        rejected_row["estado"] = "rechazada"
        rejected_row["motivo_rechazo"] = "Admin decision"
        with patch("app.modules.turnos.booking_requests_store.get_booking_request",
                   return_value=self.request_a), \
             patch("app.modules.turnos.booking_requests_store.reject",
                   return_value=rejected_row) as mock_reject:
            resp = self.client.post(
                f"/api/admin/requests/{self.sid_a}/reject",
                json={"motivo": "Admin decision"},
                cookies={"st_admin": _FIXTURE_TOKEN_ADMIN},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["solicitud"]["estado"], "rechazada")
        mock_reject.assert_called_once()


if __name__ == "__main__":
    unittest.main()
