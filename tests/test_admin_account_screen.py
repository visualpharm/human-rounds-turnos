"""Contracts for the platform-core ``/admin/account`` direct screen
(admin_panel/admin_account_screen.py + templates/admin/account.html).

The direct twin of the legacy ``/admin#cuenta`` self-service surface: same
controller (admin-account.js), same APIs, universal to authenticated staff,
no module gate, no
account read during the render, and the ONE expired-2FA repair surface the
gate admits by exact path equality.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_account_screen.py"
"""

import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-account-screen-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.admin_panel import admin_account  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.auth import passkeys  # noqa: E402
from app.core import app_modules  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ALL_ROLES = ("admin", "operador", "medico", "admisionista", "reportes", "marca")
# The roles a signed session can carry today (admin_auth._validar_payload);
# operador/marca tokens never verify, so for them the screen answers exactly
# what the account API answers (401), proven by the parity test below.
SESSION_ROLES = ("admin", "medico", "admisionista", "reportes")

# The base owns the shared runtime once; the child adds only passkeys/account.
ASSETS = ("/static/platform/i18n.", '<script src="/static/admin-shell.',
          "/static/platform/admin-alerts.",
          "/static/platform/admin-account.")
FORBIDDEN_ASSETS = (
    "admin-app.", "admin-users.", "admin-router.", "admin-sections.",
    "admin-table.", "admin-queue.", "admin-content.", "admin-settings.",
    "admin-system.",
)

EXPIRED = {"tiene_2fa": False, "2fa_deadline": "2026-01-01T00:00:00+00:00", "2fa_vencido": True}
FRESH = {"tiene_2fa": True, "2fa_deadline": None, "2fa_vencido": False}


def _cookies(role="admin", user="account-screen-test"):
    return {"st_admin": admin_auth._mint_token(user, role)}


class TestAdminAccountScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_admin_get_is_html_no_store_and_loads_dependencies_in_order(self):
        self.assertEqual(app_modules.platform_admin_screen_path("cuenta"), "/admin/account")
        response = self.client.get("/admin/account", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        html = response.text
        self.assertIn('id="sec-cuenta"', html)
        self.assertIn('window.SANA_ADMIN_SCREEN = "cuenta"', html)
        self.assertIn('window.CURRENT_USER = "account-screen-test"', html)
        # The controller's required legacy anchors, same markup/classes.
        self.assertIn('<h1 id="adm-h1" class="adm-h1">', html)
        self.assertIn('<p class="adm-2fa-note" id="adm-2fa-note" hidden></p>', html)
        self.assertIn('<button class="adm-logout btn btn-quiet" id="adm-logout" type="button"', html)
        offsets = [html.index(asset) for asset in ASSETS]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn("/static/i18n.", html)
        self.assertIn("/static/platform/format.", html)
        self.assertIn("/static/platform/passkeys.", html)
        self.assertNotIn("/static/passkeys.", html)
        loaded_scripts = html.split("</head>", 1)[1]
        for asset in FORBIDDEN_ASSETS:
            self.assertNotIn("/static/" + asset, loaded_scripts, asset)
        self.assertIn('href="/admin/account" data-nav="cuenta" class="on"', html)

    def test_role_matrix_admits_every_authenticated_role_and_denies_no_session(self):
        # No session: screen redirects (bug B9), API returns 401 JSON
        resp = self.client.get("/admin/account", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertIn("Location", resp.headers)
        for role in SESSION_ROLES:
            with self.subTest(role=role):
                response = self.client.get("/admin/account", cookies=_cookies(role))
                self.assertEqual(response.status_code, 200)
                # A doctor's nav is a row of tabs with Cuenta as an icon (8c2d5a0dc):
                # the same link, marked current, with the icon tab's class beside «on».
                active = ('class="on adm5-tab-account"' if role == "medico" else 'class="on"')
                self.assertIn('href="/admin/account" data-nav="cuenta" ' + active
                              + ' aria-current="page"', response.text)

    def test_screen_gate_uses_the_same_flags_as_the_account_api(self):
        # The screen must admit the same authenticated-role cohort as the API.
        # Difference: no-session denial — screen redirects (303), API returns 401 JSON.
        for role in ALL_ROLES:
            with self.subTest(role=role):
                api = self.client.get("/api/admin/account", cookies=_cookies(role))
                screen = self.client.get("/admin/account", cookies=_cookies(role), follow_redirects=False)
                # For roles that get 401 from API, screen redirects (303)
                if api.status_code == 401:
                    self.assertEqual(screen.status_code, 303)
                else:
                    # Authenticated roles: both 200
                    self.assertEqual(screen.status_code, 200)

    def test_render_never_reads_the_account_or_the_passkeys(self):
        # Tripwires on the native account and passkey-router domain entries:
        # the screen render must never call either API family.
        passkey_entries = (
            "list_result", "register_options_result", "register_result",
            "delete_result", "reset_result", "login_options_result", "login_result",
        )
        with patch.object(admin_account, "account_status", side_effect=AssertionError("account API ran")) as status, \
                ExitStack() as stack:
            passkey_mocks = [
                stack.enter_context(patch.object(
                    passkeys, entry, side_effect=AssertionError("passkeys API ran")))
                for entry in passkey_entries
            ]
            response = self.client.get("/admin/account", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        status.assert_not_called()
        for mock in passkey_mocks:
            mock.assert_not_called()
        # Nothing account-shaped is echoed into the shell either.
        self.assertNotIn("otpauth", response.text)
        self.assertNotIn("qr_svg", response.text)

    def test_current_username_is_script_safe(self):
        response = self.client.get(
            "/admin/account",
            cookies={"st_admin": admin_auth._mint_token("</script><img>", "admin")},
        )
        self.assertEqual(response.status_code, 200)
        # Jinja's tojson escapes the angle brackets as \u003c / \u003e, so the
        # username can never close the <script> that carries it.
        escaped = "\\u003c/script\\u003e\\u003cimg\\u003e"
        self.assertIn('window.CURRENT_USER = "' + escaped + '"', response.text)
        self.assertNotIn("</script><img>", response.text)

    def test_platform_screen_is_independent_of_module_enablement(self):
        with patch.object(config, "_db_overrides", return_value={"ENABLED_MODULES": ""}):
            config._reset_cache_para_tests()
            response = self.client.get("/admin/account", cookies=_cookies())
        self.assertEqual(response.status_code, 200)

    def test_shared_nav_is_the_only_sidebar_source(self):
        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/account", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="cuenta"', response.text)


class TestExpiredTwoFactorRepairSurface(unittest.TestCase):
    """An expired 2FA deadline locks the whole panel except the account
    repair surfaces. The direct screen joins them by EXACT path only."""

    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def _gate(self, path):
        cookie = "st_admin=" + admin_auth._mint_token("repair-test", "admin")
        with patch.object(admin_auth, "account_security", return_value=dict(EXPIRED)):
            gate = admin_auth.evaluate_admin_gate(
                admin_auth._parse_cookie_token(cookie),
                "GET",
                path,
            )
        return gate.identity, gate.status

    def test_direct_screen_is_admitted_by_equality_and_lookalikes_fail_closed(self):
        identity, _status = self._gate("/admin/account")
        self.assertEqual((identity or {}).get("usuario"), "repair-test")
        identity, _status = self._gate("/admin/account?setup=2fa")
        self.assertEqual((identity or {}).get("usuario"), "repair-test")
        for path in ("/admin/accounts", "/admin/account/", "/admin/account/x",
                     "/admin/account-reset", "/admin/acc", "/admin/users", "/admin"):
            with self.subTest(path=path):
                identity, status = self._gate(path)
                self.assertIsNone(identity)
                self.assertEqual(status, 403)

    def test_existing_api_repair_prefixes_are_unchanged(self):
        for path in ("/api/admin/account", "/api/admin/account/2fa/start",
                     "/api/admin/whoami", "/api/admin/logout"):
            with self.subTest(path=path):
                identity, _status = self._gate(path)
                self.assertEqual((identity or {}).get("usuario"), "repair-test")
        # Passkey endpoints stay OUTSIDE the expired-state exception.
        for path in ("/api/admin/passkeys", "/api/admin/passkeys/register-options",
                     "/api/admin/passkeys/delete"):
            with self.subTest(path=path):
                identity, status = self._gate(path)
                self.assertIsNone(identity)
                self.assertEqual(status, 403)

    def test_http_expired_session_reaches_the_screen_and_nothing_nearby(self):
        with patch.object(admin_auth, "account_security", return_value=dict(EXPIRED)):
            ok = self.client.get("/admin/account", cookies=_cookies())
            self.assertEqual(ok.status_code, 200)
            self.assertIn('id="sec-cuenta"', ok.text)
            for path in ("/admin/users", "/admin/system", "/admin/settings"):
                with self.subTest(path=path):
                    # Since 2026-09-15 the SCREEN gate redirects an expired
                    # enrolment straight to the only repair surface (same
                    # /admin/account?setup=2fa target the client contract
                    # uses) instead of raw JSON — the fail-closed property is
                    # the redirect, not a literal 403: no other screen's
                    # content is ever rendered.
                    denied = self.client.get(path, cookies=_cookies(),
                                             follow_redirects=False)
                    self.assertEqual(denied.status_code, 303)
                    self.assertEqual(denied.headers.get("location"),
                                     "/admin/account?setup=2fa")
            for path in ("/admin/accounts", "/admin/account/"):
                with self.subTest(path=path):
                    self.assertNotEqual(self.client.get(path, cookies=_cookies()).status_code, 200)
            passkey_list = self.client.get("/api/admin/passkeys", cookies=_cookies())
            self.assertEqual(passkey_list.status_code, 403)

    def test_fresh_session_is_unaffected(self):
        with patch.object(admin_auth, "account_security", return_value=dict(FRESH)):
            self.assertEqual(self.client.get("/admin/account", cookies=_cookies()).status_code, 200)
            self.assertEqual(self.client.get("/admin/users", cookies=_cookies()).status_code, 200)


if __name__ == "__main__":
    unittest.main()
