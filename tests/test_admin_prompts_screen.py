"""Contracts for the turnos-owned ``/admin/prompts`` direct screen."""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.auth import admin_auth  # noqa: E402
from app import main as app_module  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.core import config  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

config.ENV_PATH = ""


def _cookies(role="admin"):
    return {"st_admin": admin_auth._mint_token("prompts-screen-test", role)}


class TestAdminPromptsScreen(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_ADMIN_PASS="test-prompts")
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_shell_is_no_store_html_with_only_the_prompt_controller_cohort(self):
        response = self.client.get("/admin/prompts", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('id="sec-prompts"', response.text)
        self.assertIn('id="prompts-status"', response.text)
        self.assertIn('window.SANA_ADMIN_SCREEN = "prompts"', response.text)
        assets = ("platform/i18n.mjs", "admin-shell.js", "platform/admin-prompts.mjs")
        for asset in assets:
            self.assertIn(static_fingerprint.url(asset), response.text)
        # URLs occur first in a sorted manifest, not in execution order.
        loader = 'var url = window.SANA_STATIC && window.SANA_STATIC["platform/admin-prompts.mjs"]'
        shell = '<script src="' + static_fingerprint.url("admin-shell.js") + '"></script>'
        self.assertLess(response.text.index("window.SANA_STATIC"), response.text.index(loader))
        self.assertLess(response.text.index(shell), response.text.index(loader))
        self.assertLess(response.text.index(loader), response.text.index("import(url)"))
        self.assertNotRegex(response.text, r'src="[^\"]*/admin-prompts(?:\.[0-9a-f]{8})?\.js')
        self.assertIn('href="/admin/prompts" data-nav="prompts" class="on"', response.text)
        self.assertNotIn(static_fingerprint.url("admin-app.js"), response.text)

    def test_english_shell_exposes_the_language_used_by_the_prompt_controller(self):
        cookies = {**_cookies(), "lang": "en"}
        with patch.object(i18n, "enabled_languages", return_value=["es", "en"]):
            response = self.client.get("/admin/prompts", cookies=cookies)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIn('"active": "en"', response.text)
        self.assertIn("AI instructions", response.text)

    def test_role_matrix_is_admin_only_like_the_legacy_section(self):
        self.assertEqual(self.client.get("/admin/prompts", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/admin/prompts", cookies=_cookies()).status_code, 200)
        for role in ("medico", "admisionista", "reportes"):
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/prompts", cookies=_cookies(role)).status_code, 403)

    def test_query_state_does_not_read_or_mutate_prompt_data_while_rendering(self):
        response = self.client.get("/admin/prompts?prompt=chat_anonimo", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="sec-prompts"', response.text)
        self.assertNotIn("chat_anonimo", response.text)

    def test_module_gate_flips_live_and_nav_uses_the_shared_source(self):
        state = {"ENABLED_MODULES": "turnos"}
        with patch.object(config, "_db_overrides", side_effect=lambda: dict(state)):
            config._reset_cache_para_tests()
            self.assertEqual(self.client.get("/admin/prompts", cookies=_cookies()).status_code, 200)
            state["ENABLED_MODULES"] = "intake"
            config._reset_cache_para_tests()
            response = self.client.get("/admin/prompts", cookies=_cookies())
            self.assertEqual(response.status_code, 404)
            state["ENABLED_MODULES"] = "turnos"
            config._reset_cache_para_tests()
            self.assertEqual(self.client.get("/admin/prompts", cookies=_cookies()).status_code, 200)

        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/prompts", cookies=_cookies())
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="cuenta"', response.text)


if __name__ == "__main__":
    unittest.main()
