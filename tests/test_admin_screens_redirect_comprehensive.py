"""Comprehensive test for B9: admin HTML screens redirect on 401.

This test verifies that server-routed admin HTML screens redirect to /admin/login
when there's no session, never return JSON. Test covers core screens and all
recently-updated module screens.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_screens_redirect_comprehensive.py"
"""

import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="test-admin-screen-redirect-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class TestAdminScreensRedirectOnNoSession(unittest.TestCase):
    """Admin HTML screens redirect to login on 401, never return JSON."""

    def setUp(self):
        self.client = TestClient(app_module.app)

    # Platform-core screens
    def test_platform_screen_users_redirects(self):
        """GET /admin/users with no session → 303 redirect"""
        resp = self.client.get("/admin/users", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_platform_screen_doctors_redirects(self):
        """GET /admin/doctors with no session → 303 redirect"""
        resp = self.client.get("/admin/doctors", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_platform_screen_services_redirects(self):
        """GET /admin/services with no session → 303 redirect"""
        resp = self.client.get("/admin/services", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_platform_screen_centers_redirects(self):
        """GET /admin/centers with no session → 303 redirect"""
        resp = self.client.get("/admin/centers", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_platform_screen_account_redirects(self):
        """GET /admin/account with no session → 303 redirect"""
        resp = self.client.get("/admin/account", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    # Module screens (chat, intake)
    def test_module_screen_chats_redirects(self):
        """GET /admin/chats with no session → 303 redirect"""
        resp = self.client.get("/admin/chats", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_module_screen_consult_redirects(self):
        """GET /admin/consult with no session → 303 redirect"""
        resp = self.client.get("/admin/consult", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_module_screen_entrevistas_redirects(self):
        """GET /admin/entrevistas with no session → 303 redirect"""
        resp = self.client.get("/admin/entrevistas", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_api_endpoint_returns_401_json(self):
        """Verify API endpoints (/api/admin/*) still return 401 JSON, not redirects."""
        resp = self.client.get("/api/admin/whoami")
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertIn("error", body)
        self.assertEqual(body["error"], "no_autenticado")

    def test_screens_redirect_with_canonical_next(self):
        """Verify redirects include canonical next parameter."""
        paths = [
            "/admin/users",
            "/admin/doctors",
            "/admin/account",
            "/admin/chats",
        ]
        for path in paths:
            with self.subTest(path=path):
                resp = self.client.get(path, follow_redirects=False)
                self.assertEqual(resp.status_code, 303)
                location = resp.headers.get("Location", "")
                # Should have /admin/login and a next parameter
                self.assertIn("/admin/login", location)
                self.assertIn("next=", location)
                # Path should be URL-encoded in the next parameter
                self.assertTrue(location.startswith("/admin/login?next="))


if __name__ == "__main__":
    unittest.main()
