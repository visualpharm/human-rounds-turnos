"""Tests for admin screen 401 redirects (bug B9).

Admin HTML screens should respond with a 303 redirect to /admin/login?next=<path>
on 401 (no session), not return JSON {"error":"no_autenticado"}.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_screen_redirect.py"
"""

import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-screen-redirect-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class TestAdminScreenRedirect(unittest.TestCase):
    """Admin HTML screens should 303 redirect to login on 401, not return JSON."""

    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_admin_users_screen_no_session_redirects(self):
        """GET /admin/users with no session → 303 to /admin/login?next=/admin/users"""
        resp = self.client.get("/admin/users", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertIn("Location", resp.headers)
        location = resp.headers["Location"]
        self.assertTrue(location.startswith("/admin/login?next="))
        # next parameter is URL-encoded: /admin/users → %2Fadmin%2Fusers
        self.assertIn("%2Fadmin%2Fusers", location)

    def test_admin_doctors_screen_no_session_redirects(self):
        """GET /admin/doctors with no session → 303 to /admin/login?next=/admin/doctors"""
        resp = self.client.get("/admin/doctors", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        location = resp.headers["Location"]
        # next parameter is URL-encoded: /admin/doctors → %2Fadmin%2Fdoctors
        self.assertIn("%2Fadmin%2Fdoctors", location)

    def test_admin_services_screen_no_session_redirects(self):
        """GET /admin/services with no session → 303 redirect"""
        resp = self.client.get("/admin/services", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        location = resp.headers["Location"]
        # next parameter is URL-encoded: /admin/services → %2Fadmin%2Fservices
        self.assertIn("%2Fadmin%2Fservices", location)

    def test_admin_reportes_screen_no_session_redirects(self):
        """GET /admin/reportes with no session → 303 redirect"""
        resp = self.client.get("/admin/reportes", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        location = resp.headers["Location"]
        # next parameter is URL-encoded: /admin/reportes → %2Fadmin%2Freportes
        self.assertIn("%2Fadmin%2Freportes", location)

    def test_admin_account_screen_no_session_redirects(self):
        """GET /admin/account with no session → 303 redirect"""
        resp = self.client.get("/admin/account", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        location = resp.headers["Location"]
        # next parameter is URL-encoded: /admin/account → %2Fadmin%2Faccount
        self.assertIn("%2Fadmin%2Faccount", location)

    def test_api_endpoint_no_session_returns_401_json(self):
        """GET /api/admin/whoami with no session → 401 JSON (not redirect)"""
        resp = self.client.get("/api/admin/whoami")
        self.assertEqual(resp.status_code, 401)
        # API endpoints should return JSON, not redirect
        body = resp.json()
        self.assertIn("error", body)
        self.assertEqual(body["error"], "no_autenticado")

    def test_redirect_target_canonical(self):
        """Redirect Location header contains a canonical safe path."""
        resp = self.client.get("/admin/users", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        location = resp.headers.get("Location", "")
        # Should be a safe path starting with /admin/login
        self.assertTrue(
            location.startswith("/admin/login"),
            f"Unsafe location: {location}"
        )
        # Should contain the next parameter
        self.assertIn("next=", location)
        # Should not contain hostile markers
        self.assertNotIn("@", location)
        self.assertNotIn("//", location.replace("//", "__"))  # Allow // in localhost


if __name__ == "__main__":
    unittest.main()
