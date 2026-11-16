"""tests/routes/test_admin_modules_role_matrix.py — routes-tier coverage: GET
/api/admin/modules (`app_modules._get`). Per the P1 fix documented right
above `_API`/`_NAV_API` in app_modules.py (Codex monitor review,
2026-08-05): this used to be `solo_admin=True` (blocked every non-admin
role outright); the fix relaxed the GATE to `permitir_medico=True` while
keeping the RESPONSE role-filtered -- a `medico` session gets `sections`/
`nav_entries` but never the `modules` key (enable/disable-relevant data,
admin-only), `admisionista`/`reportes` still get the flat 403 no other
`_request_staff`-style endpoint in this file opts them into. See
docs/roadmap/tier3-harness-design.md, slice 4.
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


class TestAdminModulesRoleMatrix(RouteTestCase):

    def test_no_cookie_401(self):
        status, _ = self._get_json("/api/admin/modules")
        self.assertEqual(status, 401)

    def test_admin_sees_the_modules_key(self):
        status, body = self._get_json(
            "/api/admin/modules", headers=admin_cookie(rol="admin"))
        self.assertEqual(status, 200)
        self.assertIn("modules", body)
        self.assertIn("sections", body)
        self.assertIn("nav_entries", body)

    def test_medico_gets_sections_but_never_the_modules_key(self):
        status, body = self._get_json(
            "/api/admin/modules", headers=admin_cookie(rol="medico"))
        self.assertEqual(status, 200)
        self.assertIn("sections", body)
        self.assertNotIn("modules", body)

    def test_admisionista_403(self):
        status, body = self._get_json(
            "/api/admin/modules", headers=admin_cookie(rol="admisionista"))
        self.assertEqual(status, 403)
        self.assertEqual(body.get("error"), "rol_no_autorizado")

    def test_reportes_403(self):
        status, body = self._get_json(
            "/api/admin/modules", headers=admin_cookie(rol="reportes"))
        self.assertEqual(status, 403)
        self.assertEqual(body.get("error"), "rol_no_autorizado")


if __name__ == "__main__":
    unittest.main()
