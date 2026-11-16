"""Contracts for the Pinamar-only ``/admin/plan`` direct screen."""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-plan-screen-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.core import theme  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _cookies(role="admin"):
    return {"st_admin": admin_auth._mint_token("plan-screen-test", role)}


class TestAdminPlanScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_pinamar_admin_get_is_html_no_store_and_loads_the_roadmap_cohort(self):
        with patch.object(theme, "current", return_value="pinamar"):
            response = self.client.get("/admin/plan?view=technical", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        html = response.text
        self.assertEqual(app_modules.platform_admin_screen_path("plan"), "/admin/plan")
        self.assertIn('id="sec-plan"', html)
        self.assertIn('window.SANA_ADMIN_SCREEN = "plan"', html)
        self.assertIn('window.SANA_EMR_SYNC_ENABLED = false', html)
        self.assertNotIn('data-nav="hsi"', html)
        self.assertIn(static_fingerprint.url("platform/i18n.mjs"), html)
        self.assertIn(static_fingerprint.url("platform/admin-roadmap.mjs"), html)
        self.assertIn('href="/admin/plan" data-nav="plan" class="on"', html)
        for asset in ("admin-app.", "admin-router.", "admin-sections.", "admin-system."):
            self.assertNotIn("/static/" + asset, html, asset)

    def test_route_and_shared_nav_are_absent_outside_pinamar(self):
        with patch.object(theme, "current", return_value="humanrounds"):
            self.assertEqual(self.client.get("/admin/plan", cookies=_cookies()).status_code, 404)
            self.assertNotIn("plan", app_modules.sections_payload_for_role("admin"))
        with patch.object(theme, "current", return_value="pinamar"):
            self.assertIn("plan", app_modules.sections_payload_for_role("admin"))

    def test_admin_only_gate_runs_before_the_pinamar_route_check(self):
        with patch.object(theme, "current", return_value="pinamar"):
            self.assertEqual(self.client.get("/admin/plan", follow_redirects=False).status_code, 303)
            for role in ("medico", "admisionista", "reportes"):
                with self.subTest(role=role):
                    self.assertEqual(self.client.get("/admin/plan", cookies=_cookies(role)).status_code, 403)

    def test_render_uses_the_shared_sidebar_builder(self):
        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(theme, "current", return_value="pinamar"), \
                patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/plan", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="plan"', response.text)

    def test_explicit_hsi_selection_reaches_the_roadmap_and_shared_nav(self):
        with _env.patched_env({"CONNECTOR_EMR_SYNC": "hsi"}), \
                patch.object(theme, "current", return_value="pinamar"):
            config._reset_cache_para_tests()
            response = self.client.get("/admin/plan", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('window.SANA_EMR_SYNC_ENABLED = true', response.text)
        self.assertIn('data-nav="hsi"', response.text)


if __name__ == "__main__":
    unittest.main()
