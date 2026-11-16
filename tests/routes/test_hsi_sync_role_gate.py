"""tests/routes/test_hsi_sync_role_gate.py — routes-tier pilot #1: the
default `admin_auth.require_admin(handler)` role matrix (no
`permitir_medico`/`permitir_admisionista`/`permitir_reportes`/`permitir_marca`
opt-in), driven end to end through a REAL socket against
GET /api/admin/hsi-sync (`booking_requests._hsi_sync_status`, the plainest
default-flags caller). See docs/roadmap/tier3-harness-design.md, Area 2's
"Pilot scope" — the design's own example (a handful of
`admin_auth.require_admin` role-gating checks).
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _env  # noqa: E402


class TestHsiSyncRoleGate(RouteTestCase):

    def setUp(self):
        # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) —
        # booking_requests_router._hsi_selected() 404s the whole
        # /api/admin/hsi-sync surface without it (2026-09-02, "feat: gate
        # HSI behind explicit connector setting"). This class exercises the
        # role matrix BEHIND that gate, same opt-in as test_queue_worker.py
        # and harness.mjs's `hsi: true`, so the 401/403 cases (which 403
        # before ever reaching the gate) are unaffected and the 200 case
        # actually reaches the handler.
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        super().setUp()

    def test_no_cookie_401(self):
        status, body = self._get_json("/api/admin/hsi-sync")
        self.assertEqual(status, 401)
        self.assertEqual((body or {}).get("error"), "no_autenticado")

    def test_bad_cookie_401(self):
        status, _ = self._get_json(
            "/api/admin/hsi-sync", headers={"Cookie": "st_admin=basura.firmafalsa"})
        self.assertEqual(status, 401)

    def test_admin_role_200(self):
        status, body = self._get_json(
            "/api/admin/hsi-sync", headers=admin_cookie(rol="admin"))
        self.assertEqual(status, 200)
        self.assertIn("counts", body)

    def test_medico_role_403(self):
        # _hsi_sync_status uses the DEFAULT require_admin flags -- unlike
        # /api/admin/requests (which opts medico in), this endpoint never
        # does, so a doctor's session must be rejected here.
        status, body = self._get_json(
            "/api/admin/hsi-sync", headers=admin_cookie(rol="medico"))
        self.assertEqual(status, 403)
        self.assertEqual((body or {}).get("error"), "rol_no_autorizado")

    def test_admisionista_role_403(self):
        status, body = self._get_json(
            "/api/admin/hsi-sync", headers=admin_cookie(rol="admisionista"))
        self.assertEqual(status, 403)
        self.assertEqual((body or {}).get("error"), "rol_no_autorizado")

    def test_reportes_role_403(self):
        # 'reportes' is the one role require_admin() special-cases with its
        # OWN opt-in (permitir_reportes) rather than folding into
        # solo_admin -- still 403 here since this endpoint doesn't opt in.
        status, body = self._get_json(
            "/api/admin/hsi-sync", headers=admin_cookie(rol="reportes"))
        self.assertEqual(status, 403)
        self.assertEqual((body or {}).get("error"), "rol_no_autorizado")


if __name__ == "__main__":
    unittest.main()
