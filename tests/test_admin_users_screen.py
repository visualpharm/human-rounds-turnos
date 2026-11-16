"""Contracts for the platform-core ``/admin/users`` direct screen."""

import os
import re
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-users-screen-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.admin_panel import admin_users  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import app_modules  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _cookies(role="admin"):
    return {"st_admin": admin_auth._mint_token("users-screen-test", role)}


class TestAdminUsersScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_admin_get_is_html_no_store_and_loads_dependencies_in_order(self):
        self.assertEqual(app_modules.platform_admin_screen_path("usuarios"), "/admin/users")
        response = self.client.get("/admin/users", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('id="sec-usuarios"', response.text)
        self.assertIn('window.SANA_ADMIN_SCREEN = "usuarios"', response.text)
        self.assertIn('window.CURRENT_USER = "users-screen-test"', response.text)
        scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', response.text)
        assets = ("admin-shell",
                  "platform/admin-control-fit")
        offsets = [next(i for i, src in enumerate(scripts)
                        if src.startswith(f"/static/{asset}.")) for asset in assets]
        self.assertEqual(offsets, sorted(offsets))
        # Imported modules belong in the hashed manifest, not in the classic
        # script-order assertion. A manifest mention is not script execution.
        for module in ("platform/admin-alerts", "platform/i18n", "platform/admin-preferences", "platform/admin-session",
                       "platform/admin-table", "platform/format", "platform/admin-users"):
            self.assertRegex(response.text, rf'/static/{module}\.[0-9a-f]{{8}}\.mjs')
            self.assertFalse(any(src.startswith(f"/static/{module}.") for src in scripts))
        self.assertNotIn("admin-users.js", response.text)
        self.assertIn('SANA_STATIC["platform/admin-users.mjs"]', response.text)
        self.assertIn("m.boot()", response.text)
        self.assertIn('href="/admin/users" data-nav="usuarios" class="on"', response.text)

    def test_role_matrix_matches_the_users_api(self):
        r = self.client.get("/admin/users", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/admin/login?next=%2Fadmin%2Fusers")
        for role in ("medico", "admisionista", "reportes"):
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/users", cookies=_cookies(role)).status_code, 403)
        # the retired marca token never verifies: no session, so login
        self.assertEqual(self.client.get("/admin/users", cookies=_cookies("marca"),
                                         follow_redirects=False).status_code, 303)

    def test_render_never_reads_or_mutates_users(self):
        with patch.object(
            admin_users,
            "list_users",
            side_effect=AssertionError("users API ran"),
        ) as list_users:
            response = self.client.get("/admin/users", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        list_users.assert_not_called()

    def test_current_username_is_script_safe(self):
        response = self.client.get(
            "/admin/users",
            cookies={"st_admin": admin_auth._mint_token("</script><img>", "admin")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(r'window.CURRENT_USER = "\u003c/script\u003e\u003cimg\u003e"', response.text)
        self.assertNotIn("</script><img>", response.text)

    def test_platform_screen_is_independent_of_module_enablement(self):
        with patch.object(config, "_db_overrides", return_value={"ENABLED_MODULES": ""}):
            config._reset_cache_para_tests()
            response = self.client.get("/admin/users", cookies=_cookies())
        self.assertEqual(response.status_code, 200)

    def test_shared_nav_is_the_only_sidebar_source(self):
        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/users", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="cuenta"', response.text)


if __name__ == "__main__":
    unittest.main()
