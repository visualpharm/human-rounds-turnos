"""Contracts for the platform-core ``/admin/settings`` direct screen."""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-settings-screen-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.core import screen_nav, static_fingerprint  # noqa: E402
from app.admin_panel import settings_admin  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import app_modules  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _cookies(role="admin"):
    return {"st_admin": admin_auth._mint_token("settings-screen-test", role)}


class TestAdminSettingsScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_admin_get_is_html_no_store_and_loads_controller_in_order(self):
        self.assertEqual(app_modules.platform_admin_screen_path("settings"), "/admin/settings")
        response = self.client.get("/admin/settings", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('id="sec-settings"', response.text)
        self.assertIn('window.SANA_ADMIN_SCREEN = "settings"', response.text)
        i18n = response.text.index("/static/platform/i18n.")
        controller = response.text.index("/static/platform/admin-settings.")
        self.assertLess(i18n, controller)
        self.assertNotIn("/static/i18n.", response.text)
        self.assertIn('href="/admin/settings" data-nav="settings" class="on"', response.text)
        self.assertIn('href="/admin/settings?view=integrations"', response.text)

    def test_native_asset_is_canonical_and_retired_shapes_are_no_store_misses(self):
        name = "platform/admin-settings.mjs"
        self.assertIn(name, static_fingerprint.MIGRATED)
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("admin-settings.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        asset = self.client.get(static_fingerprint.url(name))
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.headers["cache-control"], "public, max-age=31536000, immutable")
        with open(os.path.join(ROOT, "static", name), "rb") as source:
            self.assertEqual(asset.content, source.read())
        for url in ("/admin-settings.js", "/static/admin-settings.js",
                    "/static/admin-settings.deadbeef.js", "/static/platform/admin-settings.deadbeef.mjs"):
            with self.subTest(url=url):
                retired = self.client.get(url, follow_redirects=False)
                self.assertEqual(retired.status_code, 404)
                self.assertEqual(retired.headers["cache-control"], "no-store")
                self.assertNotIn("location", retired.headers)

    def test_role_matrix_matches_the_settings_api(self):
        r = self.client.get("/admin/settings", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/admin/login?next=%2Fadmin%2Fsettings")
        for role in ("medico", "admisionista", "reportes"):
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/settings", cookies=_cookies(role)).status_code, 403)
        # the retired marca token never verifies: no session, so login
        self.assertEqual(self.client.get("/admin/settings", cookies=_cookies("marca"),
                                         follow_redirects=False).status_code, 303)

    def test_render_never_reads_settings_or_runs_probes(self):
        blocked = {name: lambda: (_ for _ in ()).throw(AssertionError("probe ran"))
                   for name in settings_admin._PROBES}
        with patch.object(settings_admin, "_payload", side_effect=AssertionError("payload read")) as payload, \
             patch.object(settings_admin, "_PROBES", blocked):
            response = self.client.get("/admin/settings", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        payload.assert_not_called()

    def test_platform_screen_is_independent_of_module_enablement(self):
        with patch.object(config, "_db_overrides", return_value={"ENABLED_MODULES": ""}):
            config._reset_cache_para_tests()
            response = self.client.get("/admin/settings", cookies=_cookies())
        self.assertEqual(response.status_code, 200)

    def test_shared_nav_is_the_only_sidebar_source(self):
        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/settings", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="cuenta"', response.text)


if __name__ == "__main__":
    unittest.main()
