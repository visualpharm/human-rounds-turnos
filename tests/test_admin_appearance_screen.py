"""Contracts for the platform-core ``/admin/appearance`` direct screen
(admin_panel/admin_appearance_screen.py + templates/admin/appearance.html).

The direct twin of the legacy ``/admin#configuracion`` section ("Apariencia y
acceso"): native owned controller, same five read/save APIs
(theme, auth-mode, turnstile — turnos; chat-voice — chat; branding —
platform), admin-only exactly like those APIs (``solo_admin=True``), no
module gate (the screen is 200 while a module-owned block's API is 404 —
the module-degradation premise), no API call during the render, and NOT an
expired-2FA repair surface.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_appearance_screen.py"
"""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-appearance-screen-test-pass")

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
from app.modules.chat import voice as voice_api  # noqa: E402
from app.modules.turnos import panel_settings  # noqa: E402
from app.patient_web import branding as branding_api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ALL_ROLES = ("admin", "operador", "medico", "admisionista", "reportes", "marca")
DENIED_SESSION_ROLES = ("medico", "admisionista", "reportes")

# Shared base runtime, then the child-owned theme controller.
ASSETS = ("platform/i18n.mjs", "admin-shell.js",
          "platform/admin-alerts.mjs", "platform/admin-appearance.mjs")
FORBIDDEN_ASSETS = (
    "admin-app.", "admin-router.", "admin-sections.", "admin-table.",
    "admin-queue.", "admin-content.", "admin-settings.", "admin-system.",
    "admin-users.", "admin-account.", "admin-prompts.", "admin-legal.",
    "passkeys.",
)

EXPIRED = {"tiene_2fa": False, "2fa_deadline": "2026-01-01T00:00:00+00:00", "2fa_vencido": True}


def _cookies(role="admin", user="appearance-screen-test"):
    return {"st_admin": admin_auth._mint_token(user, role)}


class TestAdminAppearanceScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def _enable(self, modules):
        # Same config._db_overrides monkeypatch test_module_contract_asgi.py
        # uses — flips ENABLED_MODULES with no Postgres reachable. An empty
        # value means "default", i.e. every module on; list only what stays on.
        patcher = patch.object(config, "_db_overrides", return_value={"ENABLED_MODULES": modules})
        patcher.start()
        self.addCleanup(patcher.stop)
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    def test_admin_get_is_html_no_store_and_loads_only_the_theme_cohort_in_order(self):
        self.assertEqual(app_modules.platform_admin_screen_path("configuracion"), "/admin/appearance")
        response = self.client.get("/admin/appearance", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        html = response.text
        self.assertIn('<h1 id="adm-h1" class="adm-h1">', html)
        self.assertIn('id="sec-configuracion"', html)
        self.assertIn('<p id="configuracion-status" class="adm-empty" role="status" aria-live="polite" hidden></p>', html)
        self.assertIn('window.SANA_ADMIN_SCREEN = "configuracion"', html)
        offsets = [html.index(static_fingerprint.url(asset)) for asset in ASSETS]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn('Appearance.mount(host, { status, onError })', html)
        self.assertIn(static_fingerprint.url("platform/screen-boot.mjs"), html)
        self.assertIn('id="appearance-load-recovery"', html)
        self.assertNotIn('admin-theme.', html)
        loaded_scripts = html.split("</head>", 1)[1]
        for asset in FORBIDDEN_ASSETS:
            self.assertNotIn("/static/" + asset, loaded_scripts, asset)
        self.assertIn('href="/admin/appearance" data-nav="configuracion" class="on"', html)
        # The shared sidebar links the hash twin nowhere any more.
        self.assertNotIn('href="/admin#configuracion"', html)
        # No sub-state and nothing invented server-side: no tabs, no dividers.
        self.assertNotIn('role="tab"', html)
        self.assertNotIn("<hr", html)

    def test_english_shell_exposes_the_language_used_by_the_controller(self):
        cookies = {**_cookies(), "lang": "en"}
        with patch.object(i18n, "enabled_languages", return_value=["es", "en"]):
            response = self.client.get("/admin/appearance", cookies=cookies)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIn('"active": "en"', response.text)
        self.assertIn("Appearance and access", response.text)
        # The direct session boundary strings ship in the bootstrap catalog.
        self.assertIn("admin.system.theme.sign_in", response.text)
        self.assertIn("admin.system.theme.load_error", response.text)

    def test_role_matrix_is_admin_only_like_the_legacy_section(self):
        # No session: screen redirects (303) to login, not 401 JSON
        self.assertEqual(self.client.get("/admin/appearance", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/admin/appearance", cookies=_cookies()).status_code, 200)
        for role in DENIED_SESSION_ROLES:
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/appearance", cookies=_cookies(role)).status_code, 403)
        # operador/marca tokens never verify: no session, so the screen sends
        # the visitor to the login screen (the APIs keep their 401 JSON).
        for role in ("operador", "marca"):
            with self.subTest(role=role):
                r = self.client.get("/admin/appearance", cookies=_cookies(role), follow_redirects=False)
                self.assertEqual(r.status_code, 303)
                self.assertEqual(r.headers["location"], "/admin/login?next=%2Fadmin%2Fappearance")

    def test_screen_gate_is_the_data_api_gate_for_every_role(self):
        # The screen admits and forbids exactly what the APIs do; where the
        # APIs answer 401 (no valid session) the screen redirects to login.
        self._enable("turnos")
        for role in ALL_ROLES:
            with self.subTest(role=role):
                branding = self.client.get("/api/admin/branding", cookies=_cookies(role))
                theme = self.client.get("/api/admin/theme", cookies=_cookies(role))
                screen = self.client.get("/admin/appearance", cookies=_cookies(role), follow_redirects=False)
                self.assertEqual(branding.status_code, theme.status_code)
                if branding.status_code == 401:
                    self.assertEqual(screen.status_code, 303)
                    self.assertEqual(screen.headers["location"], "/admin/login?next=%2Fadmin%2Fappearance")
                else:
                    self.assertEqual(screen.status_code, branding.status_code)

    def test_render_never_calls_any_of_the_five_apis(self):
        boom = AssertionError("appearance API ran during the render")
        with patch.object(panel_settings, "_theme_get_core", side_effect=boom) as theme_get, \
                patch.object(panel_settings, "_auth_mode_get_core", side_effect=boom) as auth_get, \
                patch.object(panel_settings, "_turnstile_get_core", side_effect=boom) as ts_get, \
                patch.object(branding_api, "state_payload", side_effect=boom) as branding_handle, \
                patch.object(voice_api, "admin_status_payload", side_effect=boom) as voice_handle:
            response = self.client.get("/admin/appearance?tema=pinamar#configuracion", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        for handler in (theme_get, auth_get, ts_get, branding_handle, voice_handle):
            handler.assert_not_called()
        self.assertIn('id="sec-configuracion"', response.text)
        # Nothing block-shaped is echoed by the server: the controller draws it.
        self.assertNotIn("cfg-h2", response.text)
        self.assertNotIn("cfg-voice-model", response.text)

    def test_platform_screen_is_independent_of_module_enablement(self):
        for enabled in ("", "intake", "chat"):
            with self.subTest(enabled=enabled):
                with patch.object(config, "_db_overrides", return_value={"ENABLED_MODULES": enabled}):
                    config._reset_cache_para_tests()
                    response = self.client.get("/admin/appearance", cookies=_cookies())
                self.assertEqual(response.status_code, 200)
                self.assertIn('href="/admin/appearance" data-nav="configuracion" class="on"', response.text)

    def test_screen_is_200_while_a_module_owned_block_api_is_404(self):
        # The degradation premise, pinned server-side: with turnos and chat
        # off (only intake listed) the theme and chat-voice APIs are gone (404)
        # but the platform screen still opens, and the platform branding API
        # still answers. The controller draws only the blocks whose GET
        # answered (proved in admin-appearance-direct.test.mjs and the e2e case).
        self._enable("intake")
        self.assertEqual(self.client.get("/api/admin/theme", cookies=_cookies()).status_code, 404)
        self.assertEqual(self.client.get("/api/admin/chat-voice", cookies=_cookies()).status_code, 404)
        self.assertEqual(self.client.get("/api/admin/branding", cookies=_cookies()).status_code, 200)
        self.assertEqual(self.client.get("/admin/appearance", cookies=_cookies()).status_code, 200)

    def test_shared_nav_is_the_only_sidebar_source(self):
        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/appearance", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="configuracion"', response.text)

    def test_expired_two_factor_session_is_denied_here_and_the_repair_surface_is_unchanged(self):
        # /admin/appearance is NOT a repair surface: the locked session keeps
        # the same 2fa_requerido 403 every other /admin/* screen answers, while
        # the one exact-path repair screen (/admin/account) still opens.
        with patch.object(admin_auth, "account_security", return_value=dict(EXPIRED)):
            denied = self.client.get("/admin/appearance", cookies=_cookies(), follow_redirects=False)
            # Desde 2026-09-15 el gate de pantallas redirectea el plazo vencido
            # directo a la única superficie de reparación (mismo target que el
            # contrato cliente); el fail-closed ES el redirect, no un 403 literal.
            self.assertEqual(denied.status_code, 303)
            self.assertEqual(denied.headers.get("location"), "/admin/account?setup=2fa")
            self.assertEqual(self.client.get("/admin/account", cookies=_cookies()).status_code, 200)

    def test_lookalike_paths_stay_closed(self):
        # Only the exact English path exists: no Spanish twin, no plural, no
        # settings/configuration synonym pair.
        for path in ("/admin/apariencia", "/admin/configuration", "/admin/configuracion", "/admin/appearances"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, cookies=_cookies()).status_code, 404)


if __name__ == "__main__":
    unittest.main()
