"""Contracts for the platform-core ``/admin/login`` direct screen
(admin_panel/admin_login_screen.py + templates/admin/login.html).

The direct twin of the legacy ``/admin`` hash-routed gate: SAME gate markup,
SAME stylesheet (static/admin-gate.css — the one copy), SAME native
controller (static/platform/admin-login.mjs). This file pins the RENDER:
everything here asserts on HTML produced by the real composition through
TestClient, never on template/controller source text.

What lives where (the acceptance split):
  - render contracts (this file): public 200, the shared fingerprinted
    stylesheet on BOTH surfaces, no copied inline gate CSS on the rendered
    /admin, the standard credential fields, the server-validated data-next;
  - controller contracts (tests/admin-login-direct.test.mjs): the REAL
    native module imported without stripping import/export syntax —
    username→usuario payload mapping, the password and passkey role-aware
    landing (validated data-next first, else the authenticated /admin
    resolver), delayed-boot early submit, no SanaAdminLogin function API;
  - browser path (tests/e2e/admin.spec.mjs): a real Chromium logs in through
    /admin/login and lands on the target screen.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_login_screen.py"
"""
import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-login-screen-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.admin_panel import admin_login_screen  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.core import theme  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _without_theme_fonts(html):
    """The page minus the active theme's typeface block ({{fuentes}}).

    theme.fonts() inlines the self-hosted @font-face rules in a <style> of
    its own (both themes since 2026-09-17). That block is the one sanctioned
    inline style on every page; it must appear exactly once, and nothing else
    may add a <style>."""
    fonts = theme.fonts()
    assert html.count(fonts) == 1, "theme typeface block rendered exactly once"
    return html.replace(fonts, "")

# The gate's CSS family, asserted ABSENT from any rendered <style> block —
# the "no copied inline CSS" half of the sharing contract. These exact rule
# opener shapes only ever appear inside a stylesheet body, so their absence
# from served HTML proves the rules live in the linked admin-gate.css alone.
_GATE_RULE_OPENERS = (
    ".gate-wrap{", ".gate{", ".gate-row{", ".gate-btns{",
    ".gate-side{", ".gp-tabs{", ".gp-img{",
)


class _ClientCase(unittest.TestCase):
    def setUp(self):
        # This file renders the gate WITHOUT a database (see
        # TestDatabaseOutageGate). The module-level setdefault only holds when
        # this file runs alone: in the full discover run an earlier module has
        # already exported a DATABASE_URL (or a TURNOS_ENV_FILE carrying one),
        # the outage probe finds it unreachable and every render assertion
        # below would be judging the outage card instead of the form.
        _env.set_env(self, DATABASE_URL="", TURNOS_ENV_FILE=os.devnull)
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)


class TestDatabaseOutageGate(_ClientCase):
    """Con la base CONFIGURADA y caída, el gate NO ofrece formulario (responsable del producto,
    2026-09-15): credenciales perfectas sólo podían terminar en "usuario o
    contraseña incorrectos". La tarjeta de corte reemplaza inputs, passkey y
    ayuda; el reintento preserva el ?next= validado. Sin base configurada el
    formulario sigue (login del dueño env = camino de emergencia) — ese lado
    lo cubre el resto de este archivo, que corre sin DATABASE_URL."""

    def test_outage_card_replaces_the_form(self):
        with patch.object(admin_login_screen, "_db_is_down", return_value=True):
            r = self.client.get("/admin/login", follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("El sistema no está disponible", r.text)
        self.assertIn("No es un problema de tu usuario ni de tu contraseña", r.text)
        self.assertIn("Reintentar", r.text)
        # Cero superficies de credenciales: ni inputs, ni passkey, ni ayuda.
        self.assertNotIn('id="gate-user"', r.text)
        self.assertNotIn('id="gate-pass"', r.text)
        self.assertNotIn('id="gate-passkey"', r.text)
        self.assertNotIn("Olvidaste tu contrase", r.text)
        self.assertIn('<meta name="robots" content="noindex,nofollow">', r.text)

    def test_outage_retry_preserves_validated_next(self):
        with patch.object(admin_login_screen, "_db_is_down", return_value=True):
            r = self.client.get("/admin/login?next=/admin/cola",
                                follow_redirects=False)
        self.assertIn('href="/admin/login?next=%2Fadmin%2Fcola"', r.text)

    def test_probe_unconfigured_database_keeps_the_form(self):
        # Sin DATABASE_URL la sonda es False y el formulario vive: el resto
        # del archivo ya lo ejercita sin base; acá se fija explícito.
        self.assertFalse(admin_login_screen._db_is_down())
        html = self.client.get("/admin/login", follow_redirects=False).text
        self.assertIn('id="gate-user"', html)


class TestAdminLoginScreen(_ClientCase):
    """Render contracts for GET /admin/login."""

    def test_platform_screen_path_is_registered(self):
        self.assertEqual(app_modules.platform_admin_screen_path("login"), "/admin/login")

    def test_get_login_renders_200_without_authentication(self):
        response = self.client.get("/admin/login", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", response.headers)

    def test_login_loads_the_shared_gate_stylesheet_fingerprinted(self):
        """The direct screen resolves admin-gate.css to its immutable URL —
        the same URL /admin resolves (one shared stylesheet, zero copies)."""
        html = self.client.get("/admin/login", follow_redirects=False).text
        self.assertIn(static_fingerprint.url("admin-gate.css"), html)
        # The token itself must never survive into the rendered page: an
        # unregistered asset renders as a literal {{static.…}} href (the
        # failure mode this replaces) — pin that it resolved for real.
        self.assertNotIn("{{static.", html)
        self.assertNotIn("{{ static(", html)

    def test_login_has_no_inline_style_block(self):
        """The direct screen ships no <style> at all: every gate rule comes
        from the linked shared stylesheet, so nothing can drift or copy."""
        html = self.client.get("/admin/login", follow_redirects=False).text
        self.assertNotIn("<style", _without_theme_fonts(html))

    def test_login_renders_the_gate_markup_with_standard_credential_semantics(self):
        html = self.client.get("/admin/login", follow_redirects=False).text
        self.assertIn('id="gate-form"', html)
        self.assertIn('id="gate-user" name="username"', html)
        self.assertIn('autocomplete="username"', html)
        self.assertIn('id="gate-pass" name="password"', html)
        self.assertIn('autocomplete="current-password"', html)
        self.assertIn('id="gate-code"', html)
        self.assertIn('autocomplete="one-time-code"', html)
        self.assertIn('id="gate-turnstile"', html)
        self.assertIn('id="gate-btn"', html)
        self.assertIn('id="gate-passkey"', html)
        self.assertIn('id="gate-side"', html)
        self.assertIn('data-tab="humor"', html)
        self.assertIn('data-tab="consejos"', html)

    def test_login_boots_the_shared_controller_as_a_direct_screen(self):
        """Native controller (platform/admin-login.mjs) imported from the
        emitted fingerprint manifest, the direct-screen flag, a hold-submit
        until boot, and none of the logged-in panel's controllers."""
        html = self.client.get("/admin/login", follow_redirects=False).text
        i18n_js = static_fingerprint.url("platform/i18n.mjs")
        admin_login_js = static_fingerprint.url("platform/admin-login.mjs")
        for url in (i18n_js, admin_login_js):
            self.assertIn(url, html)
        self.assertLess(html.index("window.SANA_STATIC="),
                        html.index('window.SANA_STATIC["platform/admin-login.mjs"]'))
        self.assertNotIn("/passkeys.js", html)
        self.assertIn(static_fingerprint.url("platform/passkeys.mjs"), html)
        self.assertIn('window.SANA_ADMIN_SCREEN = "login"', html)
        self.assertIn('window.SANA_STATIC["platform/admin-login.mjs"]', html)
        self.assertIn("m.boot({ pendingSubmit: pending })", html)
        self.assertIn("e.preventDefault()", html)
        self.assertNotIn("SanaAdminLogin", html)
        self.assertNotIn("admin-login.js", html)
        for forbidden in ("admin-app.js", "admin-router.js", "admin-sections.js",
                          "admin-account.js", "admin-queue.js"):
            self.assertNotIn(f"/static/{forbidden.rsplit('.', 1)[0]}.", html)

    def test_login_carries_no_panel_chrome(self):
        """Standalone gate: no #app shell, no sidebar, no topbar."""
        html = self.client.get("/admin/login", follow_redirects=False).text
        self.assertNotIn('id="app"', html)
        self.assertNotIn("adm-side", html)
        self.assertNotIn("adm-topbar", html)


class TestAdminRootUsesTheStandaloneGate(_ClientCase):
    """The retired shell root redirects to the direct login surface."""

    def test_admin_redirects_instead_of_rendering_the_legacy_shell(self):
        response = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/admin/login")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_admin_links_the_shared_gate_stylesheet(self):
        body = self.client.get("/admin").text
        self.assertIn(static_fingerprint.url("admin-gate.css"), body)
        self.assertNotIn("{{static.admin-gate.css}}", body,
                         "unresolved token: the asset left the fingerprint registry")

    def test_admin_gate_markup_still_renders(self):
        body = self.client.get("/admin").text
        self.assertIn('id="gate-form"', body)
        self.assertIn('id="gate-user" name="username"', body)
        self.assertIn('id="gate-pass"', body)
        self.assertIn('id="gate-passkey"', body)

    def test_admin_login_has_no_legacy_panel_styles(self):
        body = self.client.get("/admin").text
        for opener in _GATE_RULE_OPENERS:
            self.assertNotIn(opener, body, f"{opener!r} must live only in admin-gate.css")
        self.assertNotIn("<style>", _without_theme_fonts(body))
        self.assertNotIn(".adm-shell{", body)

    def test_admin_login_does_not_load_panel_toast_chrome(self):
        body = self.client.get("/admin").text
        self.assertNotIn(static_fingerprint.url("admin-shell.css"), body)
        self.assertNotIn(".toast{position:fixed;", body)
        self.assertNotIn(".toast.on{opacity:1;}", body)


class TestNextTargetValidation(_ClientCase):
    """?next= is validated server-side BEFORE render; only same-origin admin
    paths and bounded direct-screen query state survive; everything else
    silently falls back to /admin."""

    def test_default_next_target_is_admin(self):
        self.assertIn('data-next="/admin"', self.client.get("/admin/login", follow_redirects=False).text)

    def test_valid_next_targets_render_into_the_form(self):
        for path in ("/admin", "/admin/account", "/admin/users",
                     "/admin/system", "/admin/my-appointments",
                     "/admin/solicitudes", "/admin/cola", "/admin/reportes",
                     "/admin/legal?doc=privacidad",
                     "/admin/consult?interview=123",
                     "/admin/account?setup=2fa"):
            with self.subTest(path=path):
                response = self.client.get(f"/admin/login?next={path}")
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'data-next="{path}"', response.text)

    def test_invalid_next_targets_fallback_to_admin_and_are_not_echoed(self):
        invalid_targets = [
            "//evil.com",              # protocol-relative
            "https://evil.com",        # scheme
            "\\evil.com",              # backslash
            "/admin/../etc/passwd",    # traversal
            "/admin/%2e%2e/passwd",    # encoded traversal
            "/admin#evil",             # hash fragment
            "/admin?evil=true",        # query on the legacy root
            "/evil",                   # non-admin path
            "/api/admin/login",        # API path, not a screen
            "/admin@evil.com",         # userinfo
            "javascript:alert(1)",     # scheme
            "/admin/'; DROP TABLE users;--",
            "/admin/<script>alert(1)</script>",
            "/admin/`rm -rf`",
        ]
        for target in invalid_targets:
            with self.subTest(target=target):
                response = self.client.get(f"/admin/login?next={target}")
                self.assertEqual(response.status_code, 200)
                self.assertIn('data-next="/admin"', response.text)
                self.assertNotIn(target.replace('"', '\\"'), response.text)

    def test_direct_query_state_survives_but_hash_state_is_rejected(self):
        self.assertIn('data-next="/admin/users?new=1"',
                      self.client.get("/admin/login?next=/admin/users?new=1", follow_redirects=False).text)
        # A raw # never reaches the server, so this uses the encoded form that
        # actually arrives in the next value.
        self.assertIn('data-next="/admin"',
                      self.client.get("/admin/login?next=/admin/users%23sec", follow_redirects=False).text)

    def test_duplicate_or_unknown_outer_login_query_is_not_collapsed_or_echoed(self):
        for query in (
            "?next=/admin/legal&next=/admin/account",
            "?next=/admin/legal&other=1",
            "?other=1",
        ):
            with self.subTest(query=query):
                self.assertIn('data-next="/admin"', self.client.get(
                    "/admin/login" + query,
                ).text)


class TestIsValidAdminNext(unittest.TestCase):
    """Unit contracts for the validator itself (pure function)."""

    def test_admin_root_and_screens_are_valid(self):
        for path in ("/admin", "/admin/account", "/admin/users", "/admin/system",
                     "/admin/settings", "/admin/solicitudes", "/admin/my-appointments",
                     "/admin/a-b_c9", "/admin/legal?doc=privacidad",
                     "/admin/consult?interview=123", "/admin/account?setup=2fa"):
            with self.subTest(path=path):
                self.assertTrue(admin_login_screen._is_valid_admin_next(path))

    def test_non_admin_paths_are_invalid(self):
        for path in ("/", "/account", "/evil", "/api/admin/login",
                     "/static/logo.png", "/patient", "/administer"):
            with self.subTest(path=path):
                self.assertFalse(admin_login_screen._is_valid_admin_next(path))

    def test_hostile_shapes_are_invalid(self):
        for path in ("", "//evil.com", "//admin@evil.com", "\\evil.com",
                     "/admin\\evil", "/admin/../etc/passwd", "/admin/./users",
                     "../admin", "/admin/%2e%2e/passwd", "/admin/%2E%2E/x",
                     "user:pass@/admin", "/admin@evil.com", "https://evil.com",
                     "javascript:alert(1)", "data:text/html,", "/admin#section",
                     "#admin", "/admin?evil=true", "/admin/login?next=/evil",
                     "/admin/../", "/admin/", "/admin/%20",
                     "/admin/users?next=/evil", "/admin/users?x=<script>",
                     "/admin/users?x=" + "a" * 257):
            with self.subTest(path=path):
                self.assertFalse(admin_login_screen._is_valid_admin_next(path))

    def test_control_characters_are_invalid(self):
        for path in ("/admin\x00evil", "/admin\r\nx", "/admin\tx", "/admin\n"):
            with self.subTest(repr(path)):
                self.assertFalse(admin_login_screen._is_valid_admin_next(path))


class TestRoleAwareLandingTargets(_ClientCase):
    """The role-aware landing contract's server half: every destination the
    controller picks (/admin/solicitudes, /admin/cola, /admin/reportes) is
    a real, RENDERED direct screen that role may actually enter — so a
    successful login can never strand a person on a 403 page.

    WHICH role lands where (and that retired/unknown roles land nowhere) is
    the controller's contract, pinned executably against the real native
    module in tests/admin-login-direct.test.mjs; this class pins the
    authorization matrix of the three destinations on the real composition,
    with sessions minted the same way admin_auth._mint_token() mints a real
    one (no DB, no secrets). No 'marca' case here — same reason
    test_admin_screen_contract.py has none: 'marca' is not a mintable
    fixture role, and its fail-closed behavior belongs to the controller
    suite that CAN stub it."""

    def setUp(self):
        super().setUp()
        # Same config._db_overrides monkeypatch test_admin_screen_contract.py
        # uses: ENABLED_MODULES pinned to turnos with no reachable Postgres.
        store = {"ENABLED_MODULES": "turnos"}
        patcher = patch.object(config, "_db_overrides",
                               side_effect=lambda: dict(store))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _get(self, path, rol=None):
        cookies = ({"st_admin": admin_auth._mint_token(f"landing-{rol}", rol)}
                   if rol else None)
        return self.client.get(path, cookies=cookies)

    def test_every_role_landing_renders_for_that_role(self):
        cases = [
            ("admin", "/admin/solicitudes", "solicitudes"),
            ("admisionista", "/admin/solicitudes", "solicitudes"),
            ("medico", "/admin/cola", "cola"),
            ("reportes", "/admin/reportes", "reportes"),
        ]
        for rol, path, screen in cases:
            with self.subTest(rol=rol, path=path):
                response = self._get(path, rol)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'window.SANA_ADMIN_SCREEN = "{screen}"',
                              response.text,
                              "the landing is the real direct screen, not a shell")

    def test_admin_may_enter_all_three_landing_screens(self):
        # admin's landing is solicitudes, but the other two landings must not
        # be role-traps either — admin is the superset everywhere.
        for path in ("/admin/solicitudes", "/admin/cola", "/admin/reportes"):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "admin").status_code, 200)

    def test_cross_role_landings_are_denied_by_the_server(self):
        # The fail-safe backstop: even a tampered client that picked a wrong
        # target gets a flat denial, never a rendered unauthorized page.
        cases = [
            ("reportes", "/admin/solicitudes"),
            ("reportes", "/admin/cola"),
            ("medico", "/admin/reportes"),
            ("admisionista", "/admin/reportes"),
        ]
        for rol, path in cases:
            with self.subTest(rol=rol, path=path):
                self.assertEqual(self._get(path, rol).status_code, 403)

    def test_anonymous_visitors_are_sent_to_login_not_a_landing(self):
        for path in ("/admin/solicitudes", "/admin/cola", "/admin/reportes"):
            with self.subTest(path=path):
                r = self.client.get(path, follow_redirects=False)
                self.assertEqual(r.status_code, 303)
                self.assertEqual(r.headers["location"], "/admin/login?next=" + path.replace("/", "%2F"))


class TestLoginAPI(_ClientCase):
    """The authentication POST the rendered form's controller drives. The
    REDIRECT after success is client-side behavior — pinned executably in
    tests/admin-login-direct.test.mjs (controller) and tests/e2e/admin.spec.mjs
    (real browser), never claimed here."""

    def test_owner_password_login_succeeds_and_mints_a_session(self):
        response = self.client.post(
            "/api/admin/login",
            json={"usuario": "admin",
                  "password": os.environ["TURNOS_ADMIN_PASS"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("ok"))
        self.assertIn("set-cookie", response.headers)

    def test_success_replaces_a_previous_users_cookie(self):
        old = admin_auth._mint_token("previous-staff", "medico")
        response = self.client.post("/api/admin/login", headers={"Cookie": "st_admin=" + old},
                                    json={"usuario": "admin", "password": os.environ["TURNOS_ADMIN_PASS"]})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        token = admin_auth._parse_cookie_token(cookie)
        self.assertNotEqual(token, old)
        self.assertEqual(admin_auth._verify_token(token)["usuario"], "admin")
        self.assertIn("Path=/", cookie)

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            "/api/admin/login",
            json={"usuario": "admin", "password": "definitely-not-it"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json().get("ok"))

    def test_authenticated_visitor_still_gets_the_login_screen(self):
        cookies = {"st_admin": admin_auth._mint_token("admin", "admin")}
        response = self.client.get("/admin/login", cookies=cookies)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class TestLoginCookieRecovery(_ClientCase):
    def test_explicit_login_clears_previous_identity_including_stale_and_demo(self):
        for token in ("expired-session", admin_auth._mint_token("demo", "admin"), admin_auth._mint_token("other-staff", "medico")):
            with self.subTest(token=token):
                response = self.client.get("/admin/login?next=/admin/doctors", headers={"Cookie": "st_admin=" + token})
                self.assertIn("st_admin=;", response.headers.get("set-cookie", ""))
                self.assertIn("Max-Age=0", response.headers.get("set-cookie", ""))
                self.assertIn("Path=/", response.headers.get("set-cookie", ""))

    def test_cookie_cleared_on_outage_and_secure_proxy(self):
        with patch.object(admin_login_screen, "_db_is_down", return_value=True):
            response = self.client.get("/admin/login", headers={"Cookie": "st_admin=stale", "X-Forwarded-Proto": "https"})
        self.assertIn("Max-Age=0", response.headers.get("set-cookie", ""))
        self.assertIn("Secure", response.headers.get("set-cookie", ""))

    def test_clean_login_does_not_set_a_cookie(self):
        response = self.client.get("/admin/login")
        self.assertNotIn("set-cookie", response.headers)
