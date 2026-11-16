"""Contracts for the platform-core ``/admin/legal`` direct screen
(admin_panel/admin_legal_screen.py + templates/admin/legal.html).

The direct twin of the legacy ``/admin#legal`` section ("Términos y
privacidad"): same controller (admin-legal.js), same ``/api/admin/legal*``
API, admin-only exactly like that API (``solo_admin=True``), no module gate,
no document read during the render, and NOT an expired-2FA repair surface.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_legal_screen.py"
"""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-legal-screen-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.patient_web import legal as legal_api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ALL_ROLES = ("admin", "operador", "medico", "admisionista", "reportes", "marca")
DENIED_SESSION_ROLES = ("medico", "admisionista", "reportes")

# The real dependency cohort, in order: i18n, the shared formatter, then the
# legal controller. Nothing from the legacy shell (admin-app.js), the hash
# router/sections, the table engine or any other admin controller.
ASSETS = ("platform/i18n.mjs", "admin-shell.js", "platform/admin-legal.mjs")
FORBIDDEN_ASSETS = (
    "admin-app.", "admin-router.", "admin-sections.", "admin-table.",
    "admin-queue.", "admin-content.", "admin-settings.", "admin-system.",
    "admin-users.", "admin-account.", "admin-prompts.", "passkeys.",
)

EXPIRED = {"tiene_2fa": False, "2fa_deadline": "2026-01-01T00:00:00+00:00", "2fa_vencido": True}


def _cookies(role="admin", user="legal-screen-test"):
    return {"st_admin": admin_auth._mint_token(user, role)}


class TestAdminLegalScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_admin_get_is_html_no_store_and_loads_only_the_legal_cohort_in_order(self):
        self.assertEqual(app_modules.platform_admin_screen_path("legal"), "/admin/legal")
        response = self.client.get("/admin/legal", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        html = response.text
        self.assertIn('<h1 id="adm-h1" class="adm-h1">', html)
        self.assertIn('id="sec-legal"', html)
        self.assertIn('<p id="legal-status" class="adm-empty" role="status" aria-live="polite" hidden></p>', html)
        self.assertIn('window.SANA_ADMIN_SCREEN = "legal"', html)
        for asset in ASSETS:
            self.assertIn(static_fingerprint.url(asset), html)
        # Manifest key order is not script execution order. The classic shell
        # executes before the direct loader; ESM awaits its own dependencies.
        loader = 'var url = window.SANA_STATIC && window.SANA_STATIC["platform/admin-legal.mjs"]'
        shell = '<script src="' + static_fingerprint.url("admin-shell.js") + '"></script>'
        self.assertLess(html.index("window.SANA_STATIC"), html.index(loader))
        self.assertLess(html.index(shell), html.index(loader))
        self.assertLess(html.index(loader), html.index("import(url)"))
        self.assertNotRegex(html, r'src="[^\"]*/admin-legal(?:\.[0-9a-f]{8})?\.js')
        loaded_scripts = html.split("</head>", 1)[1]
        for asset in FORBIDDEN_ASSETS:
            self.assertNotIn("/static/" + asset, loaded_scripts, asset)
        self.assertIn('href="/admin/legal" data-nav="legal" class="on"', html)
        # The shared sidebar links the hash twin nowhere any more.
        self.assertNotIn('href="/admin#legal"', html)

    def test_english_shell_exposes_the_language_used_by_the_controller(self):
        cookies = {**_cookies(), "lang": "en"}
        with patch.object(i18n, "enabled_languages", return_value=["es", "en"]):
            response = self.client.get("/admin/legal", cookies=cookies)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIn('"active": "en"', response.text)
        self.assertIn("Terms and privacy", response.text)

    def test_role_matrix_is_admin_only_like_the_legacy_section(self):
        self.assertEqual(self.client.get("/admin/legal", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/admin/legal", cookies=_cookies()).status_code, 200)
        for role in DENIED_SESSION_ROLES:
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/legal", cookies=_cookies(role)).status_code, 403)
        # operador/marca tokens never verify: no session, so login.
        for role in ("operador", "marca"):
            with self.subTest(role=role):
                r = self.client.get("/admin/legal", cookies=_cookies(role), follow_redirects=False)
                self.assertEqual(r.status_code, 303)
                self.assertEqual(r.headers["location"], "/admin/login?next=%2Fadmin%2Flegal")

    def test_screen_gate_is_the_data_api_gate_for_every_role(self):
        # The screen must admit exactly what GET /api/admin/legal admits, and
        # deny with the same status: the gate is copied, never inferred. The
        # one difference: an API 401 (no valid session) is a login redirect.
        for role in ALL_ROLES:
            with self.subTest(role=role):
                api = self.client.get("/api/admin/legal", cookies=_cookies(role))
                screen = self.client.get("/admin/legal", cookies=_cookies(role), follow_redirects=False)
                if api.status_code == 401:
                    self.assertEqual(screen.status_code, 303)
                else:
                    self.assertEqual(screen.status_code, api.status_code)
        self.assertEqual(self.client.get("/api/admin/legal", follow_redirects=False).status_code, 401)
        self.assertEqual(self.client.get("/admin/legal", follow_redirects=False).status_code, 303)

    def test_render_never_reads_a_document(self):
        with patch.object(legal_api, "item_payload",
                          side_effect=AssertionError("legal API ran")) as item_payload:
            response = self.client.get("/admin/legal?doc=terminos", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        item_payload.assert_not_called()
        self.assertIn('id="sec-legal"', response.text)
        # The query state is client-owned; nothing document-shaped is echoed.
        self.assertNotIn('"cuerpo"', response.text)
        self.assertNotIn("lg-cuerpo", response.text)

    def test_platform_screen_is_independent_of_module_enablement(self):
        for enabled in ("", "intake", "chat"):
            with self.subTest(enabled=enabled):
                with patch.object(config, "_db_overrides", return_value={"ENABLED_MODULES": enabled}):
                    config._reset_cache_para_tests()
                    response = self.client.get("/admin/legal", cookies=_cookies())
                self.assertEqual(response.status_code, 200)
                self.assertIn('href="/admin/legal" data-nav="legal" class="on"', response.text)

    def test_shared_nav_is_the_only_sidebar_source(self):
        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/legal", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="legal"', response.text)

    def test_expired_two_factor_session_is_denied_here_and_the_repair_surface_is_unchanged(self):
        # /admin/legal is NOT a repair surface: the locked session keeps the
        # same 2fa_requerido 403 every other /admin/* screen answers, while
        # the one exact-path repair screen (/admin/account) still opens.
        with patch.object(admin_auth, "account_security", return_value=dict(EXPIRED)):
            denied = self.client.get("/admin/legal", cookies=_cookies(), follow_redirects=False)
            # Desde 2026-09-15 el gate de pantallas redirectea el plazo vencido
            # directo a la única superficie de reparación (mismo target que el
            # contrato cliente); el fail-closed ES el redirect, no un 403 literal.
            self.assertEqual(denied.status_code, 303)
            self.assertEqual(denied.headers.get("location"), "/admin/account?setup=2fa")
            self.assertEqual(self.client.get("/admin/account", cookies=_cookies()).status_code, 200)


if __name__ == "__main__":
    unittest.main()
