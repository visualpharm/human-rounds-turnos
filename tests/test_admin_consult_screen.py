"""Contracts for the intake-owned ``/admin/consult`` direct screen."""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-consult-screen-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.auth import admin_auth  # noqa: E402
from app import main as app_module  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.core import config  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.modules.intake import consult_admin  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.core import db_clinical  # noqa: E402

config.ENV_PATH = ""


def _cookies(role="admin"):
    return {"st_admin": admin_auth._mint_token("consult-screen-test", role)}


class TestAdminConsultScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_shell_is_no_store_html_with_the_exact_controller_dependencies(self):
        response = self.client.get("/admin/consult", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('id="sec-consulta"', response.text)
        self.assertIn('id="consult-status"', response.text)
        self.assertIn('window.SANA_ADMIN_SCREEN = "consulta"', response.text)
        native_url = static_fingerprint.module_url("modules/intake/admin-consult.mjs")
        self.assertTrue(native_url, "intake must register its own native entry")
        offsets = [response.text.index(url) for url in (
            static_fingerprint.url("platform/i18n.mjs"), native_url,
        )]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn("admin-consulta", response.text)
        self.assertIn('id="consult-load-recovery"', response.text)
        self.assertNotIn(
            f'<script src="{static_fingerprint.url("platform/admin-patient-summary.mjs")}"></script>',
            response.text,
        )
        self.assertIn("platform/admin-patient-summary.mjs", static_fingerprint.manifest())
        self.assertNotIn('data-nav="consulta"', response.text)
        self.assertIn('href="/admin/cola"', response.text)

    def test_native_entry_is_a_real_intake_asset_not_a_platform_manifest_alias(self):
        name = "modules/intake/admin-consult.mjs"
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        url = static_fingerprint.module_url(name)
        self.assertTrue(url)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("export function mount(host, options)", response.text)
        self.assertNotIn("SanaAdminRouter", response.text)

    def test_retired_classic_urls_are_no_store_404_without_a_compatibility_route(self):
        self.assertNotIn("admin-consulta.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "admin-consulta.js")))
        for url in ("/admin-consulta.js", "/static/admin-consulta.deadbeef.js"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_contextual_screen_stays_routed_without_becoming_a_menu_item(self):
        sections = app_module.app_modules.sections_payload_for_role("admin")
        consultation = next(
            section for section in sections
            if isinstance(section, dict) and section["id"] == "consulta"
        )
        self.assertEqual(consultation["screen"]["path"], "/admin/consult")
        self.assertNotIn("consulta", {item["id"] for item in screen_nav.nav_items("admin")})

    def test_role_matrix_matches_the_consult_api_gate(self):
        self.assertEqual(self.client.get("/admin/consult", follow_redirects=False).status_code, 303)
        for role in ("admin", "medico"):
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/consult", cookies=_cookies(role)).status_code, 200)
        for role in ("admisionista", "reportes"):
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/consult", cookies=_cookies(role)).status_code, 403)

    def test_shell_render_never_reads_or_mutates_an_interview(self):
        with patch.object(db_clinical, "interview_get", side_effect=AssertionError("consult API ran")) as get:
            response = self.client.get("/admin/consult?interview=123", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        get.assert_not_called()

    def test_query_state_does_not_change_the_server_render(self):
        response = self.client.get("/admin/consult?interview=123", cookies=_cookies("medico"))
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="sec-consulta"', response.text)
        self.assertNotIn("interview=123", response.text)

    def test_module_gate_flips_live_and_nav_uses_the_shared_source(self):
        state = {"ENABLED_MODULES": "turnos,intake"}
        with patch.object(config, "_db_overrides", side_effect=lambda: dict(state)):
            config._reset_cache_para_tests()
            self.assertEqual(self.client.get("/admin/consult", cookies=_cookies()).status_code, 200)
            state["ENABLED_MODULES"] = "turnos"
            config._reset_cache_para_tests()
            response = self.client.get("/admin/consult", cookies=_cookies())
            self.assertEqual(response.status_code, 404)
            state["ENABLED_MODULES"] = "turnos,intake"
            config._reset_cache_para_tests()
            self.assertEqual(self.client.get("/admin/consult", cookies=_cookies()).status_code, 200)

        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/consult", cookies=_cookies())
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="cuenta"', response.text)


if __name__ == "__main__":
    unittest.main()
