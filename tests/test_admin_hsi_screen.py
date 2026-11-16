"""Contracts for the turnos-owned ``/admin/hsi`` direct screen
(admin_panel/admin_hsi_screen.py + templates/admin/hsi.html).

The direct twin of the legacy ``/admin#hsi`` section ("Sincronización con
HSI"): native lifecycle-owned controller (``static/platform/admin-hsi.mjs``,
the classic root ``admin-hsi.js`` physically retired), same
``/api/admin/hsi-sync*`` APIs, admin-only exactly like those APIs
(``_check_admin(handler, {})`` / ``solo_admin``), module-gated exactly like
them (turnos off → 404), no HSI/resolver/DB/socket work during the render,
no configuration value echoed, and NOT an expired-2FA repair surface.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_hsi_screen.py"
"""

import os
import socket
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-hsi-screen-test-pass")
_PREVIOUS_CONNECTOR = os.environ.get("CONNECTOR_EMR_SYNC")
_env.process_set(CONNECTOR_EMR_SYNC="hsi")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.connectors.hsi import hsi_resolver  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.modules.turnos import booking_emr  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_env.process_set(CONNECTOR_EMR_SYNC=_PREVIOUS_CONNECTOR)


def setUpModule():
    _env.set_env_for_module(CONNECTOR_EMR_SYNC="hsi")   # put back after the module's tests

ALL_ROLES = ("admin", "operador", "medico", "admisionista", "reportes", "marca")
DENIED_SESSION_ROLES = ("medico", "admisionista", "reportes")

# Shared base runtime, then the child-owned native HSI controller.
ASSETS = ("platform/i18n.mjs", "admin-shell.js",
          "platform/admin-alerts.mjs", "platform/admin-hsi.mjs")
FORBIDDEN_ASSETS = (
    "admin-app.", "admin-router.", "admin-sections.", "admin-table.",
    "admin-queue.", "admin-content.", "admin-settings.", "admin-system.",
    "admin-users.", "admin-account.", "admin-prompts.", "admin-legal.",
    "admin-theme.", "admin-solicitudes.", "admin-hsi.", "passkeys.",
)

EXPIRED = {"tiene_2fa": False, "2fa_deadline": "2026-01-01T00:00:00+00:00", "2fa_vencido": True}

# What store.hsi_sync_status() hands the API when a database IS there — a
# fake so the API gate can be compared status-for-status with the screen
# without any database (the panel comes from the resolver, patched to None).
FAKE_STATUS = {"counts": {}, "outbox": [], "conflicts": []}

# Lookalikes that must stay closed: the route is the exact bare path only.
LOOKALIKES = ("/admin/hsi/", "/admin/hsi-sync", "/admin/hsis", "/admin/HSI",
              "/admin/hsi/x", "/admin/hsi/conflicts", "/admin/hsi//")


def _cookies(role="admin", user="hsi-screen-test"):
    return {"st_admin": admin_auth._mint_token(user, role)}


def _boom(*_args, **_kwargs):
    raise AssertionError("the render touched HSI, the resolver, the database or a socket")


class TestAdminHsiScreen(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    # ---- shell -----------------------------------------------------------

    def test_admin_get_is_html_no_store_and_loads_only_the_hsi_cohort_in_order(self):
        response = self.client.get("/admin/hsi", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        html = response.text
        self.assertIn('<h1 id="adm-h1" class="adm-h1">', html)
        self.assertIn("Sincronización con HSI", html)
        self.assertIn('<div id="sec-hsi"></div>', html)
        self.assertIn('<p id="hsi-status" class="adm-empty" role="status" aria-live="polite" hidden></p>', html)
        self.assertIn('window.SANA_ADMIN_SCREEN = "hsi"', html)
        offsets = [html.index(static_fingerprint.url(asset)) for asset in ASSETS]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn(static_fingerprint.url("platform/admin-session.mjs"), html)
        self.assertIn('Hsi.mount(host, { status, onError })', html)
        self.assertIn(static_fingerprint.url("platform/screen-boot.mjs"), html)
        self.assertIn('id="hsi-load-recovery"', html)
        self.assertNotIn("admin-hsi.js", html)
        loaded_scripts = html.split("</head>", 1)[1]
        for asset in FORBIDDEN_ASSETS:
            self.assertNotIn("/static/" + asset, loaded_scripts, asset)
        # Only the shared shell is classic; Alerts and HSI import their
        # fingerprinted native controllers from inline module bootstraps.
        self.assertEqual(html.count("<script src="), 1)
        self.assertEqual(html.count("mod.mountAlerts({ root: document, role })"), 1)
        self.assertIn('href="/admin/hsi" data-nav="hsi" class="on"', html)
        # The shared sidebar links the hash twin nowhere any more.
        self.assertNotIn('href="/admin#hsi"', html)
        # every first-party script is fingerprinted, never a manual `?v=`
        self.assertNotIn(".js?v=", html)

    def test_english_shell_exposes_the_language_used_by_the_controller(self):
        cookies = {**_cookies(), "lang": "en"}
        with patch.object(i18n, "enabled_languages", return_value=["es", "en"]):
            response = self.client.get("/admin/hsi", cookies=cookies)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIn('"active": "en"', response.text)
        self.assertIn("HSI synchronization", response.text)
        # the client catalog carries the section's own namespace for T()
        self.assertIn('"admin.hsi.rescan": "Check again"', response.text)

    def test_shared_nav_is_the_only_sidebar_source(self):
        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            response = self.client.get("/admin/hsi", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-nav="proof"', response.text)
        self.assertNotIn('data-nav="hsi"', response.text)
        self.assertNotIn('data-nav="cuenta"', response.text)

    # ---- gate ------------------------------------------------------------

    def test_role_matrix_is_admin_only_like_the_legacy_section(self):
        self.assertEqual(self.client.get("/admin/hsi", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/admin/hsi", cookies=_cookies()).status_code, 200)
        for role in DENIED_SESSION_ROLES:
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/admin/hsi", cookies=_cookies(role)).status_code, 403)
        # operador/marca tokens never verify and an invalid cookie is no
        # session: all three are sent to the login screen.
        for label, cookies in (("operador", _cookies("operador")), ("marca", _cookies("marca")),
                               ("invalid", {"st_admin": "not-a-token"})):
            with self.subTest(role=label):
                r = self.client.get("/admin/hsi", cookies=cookies, follow_redirects=False)
                self.assertEqual(r.status_code, 303)
                self.assertEqual(r.headers["location"], "/admin/login?next=%2Fadmin%2Fhsi")

    def test_screen_gate_is_the_hsi_api_gate_for_every_role_in_both_module_states(self):
        # The screen must admit exactly what GET /api/admin/hsi-sync admits,
        # and deny with the same status: the gate is copied, never inferred.
        # The one difference: where the API answers 401 (no valid session)
        # the screen redirects to login.
        # The API's admin branch reads the store, so that read is faked (the
        # database is absent here) and the resolver's panel is None.
        with patch.object(booking_requests.store, "hsi_sync_status", return_value=dict(FAKE_STATUS)), \
                patch.object(hsi_resolver, "estado_panel", return_value=None):
            for role in ALL_ROLES:
                with self.subTest(role=role, turnos="on"):
                    api = self.client.get("/api/admin/hsi-sync", cookies=_cookies(role))
                    screen = self.client.get("/admin/hsi", cookies=_cookies(role), follow_redirects=False)
                    if api.status_code == 401:
                        self.assertEqual(screen.status_code, 303)
                    else:
                        self.assertEqual(screen.status_code, api.status_code)
            self.assertEqual(self.client.get("/api/admin/hsi-sync", follow_redirects=False).status_code, 401)
            self.assertEqual(self.client.get("/admin/hsi", follow_redirects=False).status_code, 303)
            self.assertEqual(self.client.get("/api/admin/hsi-sync", cookies=_cookies()).status_code, 200)
            # turnos off: the APIs and the screen are the same 404, every role
            with patch.object(config, "_db_overrides", return_value={"ENABLED_MODULES": "intake"}):
                config._reset_cache_para_tests()
                for role in ALL_ROLES:
                    with self.subTest(role=role, turnos="off"):
                        api = self.client.get("/api/admin/hsi-sync", cookies=_cookies(role))
                        screen = self.client.get("/admin/hsi", cookies=_cookies(role))
                        self.assertEqual((screen.status_code, api.status_code), (404, 404))
                self.assertEqual(self.client.get("/admin/hsi", follow_redirects=False).status_code, 404)
                self.assertEqual(self.client.get("/api/admin/hsi-sync", follow_redirects=False).status_code, 404)

    def test_module_gate_flips_live(self):
        state = {"ENABLED_MODULES": "turnos"}
        with patch.object(config, "_db_overrides", side_effect=lambda: dict(state)):
            config._reset_cache_para_tests()
            self.assertEqual(self.client.get("/admin/hsi", cookies=_cookies()).status_code, 200)
            state["ENABLED_MODULES"] = "intake"
            config._reset_cache_para_tests()
            response = self.client.get("/admin/hsi", cookies=_cookies())
            self.assertEqual(response.status_code, 404)
            state["ENABLED_MODULES"] = "turnos"
            config._reset_cache_para_tests()
            response = self.client.get("/admin/hsi", cookies=_cookies())
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/admin/hsi" data-nav="hsi" class="on"', response.text)

    def test_connector_off_keeps_the_screen_and_drops_only_the_menu_entry(self):
        with _env.patched_env({"CONNECTOR_EMR_SYNC": "off"}):
            config._reset_cache_para_tests()
            # Slice 7 (2026-11-10): no redirect — the same shell, and the
            # controller draws the honest state («el servidor no envía…»).
            response = self.client.get("/admin/hsi", cookies=_cookies(), follow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertIn('<div id="sec-hsi"></div>', response.text)
            self.assertNotIn('data-nav="hsi"', response.text)
            # «Sistema» links here; it is the menu entry lit.
            self.assertIn('href="/admin/system" data-nav="sistema" class="on"', response.text)
            self.assertNotIn("hsi", {item["id"] for item in screen_nav.nav_items("admin")})

    def test_state_api_is_read_only_and_names_the_mode_without_the_connector(self):
        from app.core import hsi_extension
        for connector, install, modo in (
                ("off", None, "apagado"),
                ("off", {"tienda": "https://store.example/x", "zip": None, "guia": None}, "extension")):
            with self.subTest(modo=modo), _env.patched_env({"CONNECTOR_EMR_SYNC": connector}), \
                    patch.object(hsi_extension, "install", return_value=install), \
                    patch.object(hsi_extension, "store_url", return_value=(install or {}).get("tienda")), \
                    patch.object(booking_requests.store, "hsi_sync_status", side_effect=_boom), \
                    patch.object(hsi_resolver, "resolver_pass", side_effect=_boom):
                config._reset_cache_para_tests()
                response = self.client.get("/api/admin/system/hsi", cookies=_cookies())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                body = response.json()
                self.assertEqual((body["modo"], body["motivo"], body["conector"]), (modo, "sin_conector", None))
                self.assertTrue(body["modo_texto"] and body["motivo_texto"])
                self.assertEqual(body["total"], len(body["centros"]))
                self.assertNotIn("diario", body)

    def test_state_api_is_admin_only(self):
        self.assertEqual(self.client.get("/api/admin/system/hsi", follow_redirects=False).status_code, 401)
        for role in ("medico", "admisionista", "reportes"):
            with self.subTest(role=role):
                response = self.client.get("/api/admin/system/hsi", cookies=_cookies(role), follow_redirects=False)
                self.assertEqual(response.status_code, 403)

    def test_connector_off_closes_every_hsi_api_before_store_or_resolver_work(self):
        with _env.patched_env({"CONNECTOR_EMR_SYNC": "off"}), \
                patch.object(booking_requests.store, "hsi_sync_status", side_effect=_boom), \
                patch.object(hsi_resolver, "resolver_pass", side_effect=_boom):
            config._reset_cache_para_tests()
            cases = (
                ("GET", "/api/admin/hsi-sync", None),
                ("POST", "/api/admin/hsi-sync/resolver", {}),
                ("POST", "/api/admin/hsi-sync/conflicts", {}),
                ("POST", "/api/admin/requests/1/retry-hsi", {}),
            )
            for method, path, body in cases:
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, cookies=_cookies(), json=body)
                    self.assertEqual(response.status_code, 404)

    def test_manifest_declares_the_screen_as_a_v2_admin_section(self):
        manifest = next(m for m in app_modules.list_modules() if m["id"] == "turnos")
        section = next(s for s in manifest["admin_sections"] if s["id"] == "hsi")
        self.assertEqual(section["label_key"], "admin.shell.nav.hsi")
        self.assertEqual(section["label"], "HSI")
        self.assertIsNone(section.get("roles"))  # admin-only, like the APIs
        self.assertEqual(section["screen"]["path"], "/admin/hsi")
        self.assertEqual(section["screen"]["module"], "app.admin_panel.admin_hsi_screen")
        self.assertEqual(section["screen"]["router_attr"], "screen_router")
        self.assertEqual(section["screen"]["requires_capability"], "emr_sync")
        self.assertEqual(section["screen"]["requires_connector"], "hsi")
        # the shared sidebar reads that path for every admin screen
        nav = next(item for item in screen_nav.nav_items("admin") if item["id"] == "hsi")
        self.assertEqual({key: nav[key] for key in ("id", "href", "label")},
                         {"id": "hsi", "href": "/admin/hsi", "label": "HSI"})

    def test_lookalikes_and_trailing_slash_stay_closed(self):
        for path in LOOKALIKES:
            with self.subTest(path=path):
                response = self.client.get(path, cookies=_cookies(), follow_redirects=False)
                self.assertNotEqual(response.status_code, 200, path)
                self.assertNotIn(response.status_code, (301, 302, 307, 308), path)
                self.assertNotIn('id="sec-hsi"', response.text, path)
        # the trailing-slash closure is the same one every direct screen has
        self.assertEqual(self.client.get("/admin/hsi/", cookies=_cookies(), follow_redirects=False).status_code,
                         self.client.get("/admin/prompts/", cookies=_cookies(), follow_redirects=False).status_code)

    def test_expired_two_factor_session_is_denied_here_and_the_repair_surface_is_unchanged(self):
        # /admin/hsi is NOT a repair surface: the locked session keeps the
        # same 2fa_requerido 403 every other /admin/* screen answers, while
        # the one exact-path repair screen (/admin/account) still opens.
        with patch.object(admin_auth, "account_security", return_value=dict(EXPIRED)):
            denied = self.client.get("/admin/hsi", cookies=_cookies(), follow_redirects=False)
            # Desde 2026-09-15 el gate de pantallas redirectea el plazo vencido
            # directo a la única superficie de reparación (mismo target que el
            # contrato cliente); el fail-closed ES el redirect, no un 403 literal.
            self.assertEqual(denied.status_code, 303)
            self.assertEqual(denied.headers.get("location"), "/admin/account?setup=2fa")
            self.assertEqual(self.client.get("/admin/account", cookies=_cookies()).status_code, 200)

    # ---- render purity -----------------------------------------------------

    def test_render_calls_no_hsi_api_resolver_connector_db_or_socket(self):
        # El portón resuelve los permisos de la cuenta contra la base ANTES de
        # renderizar (slice de roles, 2026-09-12): esa lectura es del portón,
        # no del render, así que se sirve desde acá para que el resto del
        # cerrojo —HSI, resolver, base y sockets DENTRO del render— siga
        # siendo exactamente igual de estricto que antes.
        # La revisión de «Cerrar sesión» (una lectura por pedido, TASK-55) también
        # es del portón y del middleware de navegación, no del render: se sirve igual.
        from app.core import permissions as _perm
        with patch.object(_perm, "access_for",
                          return_value={"slug": "admin", "state": "ok",
                                        "permissions": _perm.ALL}), \
                patch.object(db_auth, "admin_session_is_revoked", return_value=False), \
                patch.object(booking_emr, "_hsi_sync_status_core", side_effect=_boom), \
                patch.object(booking_emr, "_hsi_resolver_run_core", side_effect=_boom), \
                patch.object(booking_emr, "_hsi_conflicts_apply_core", side_effect=_boom), \
                patch.object(booking_requests.store, "hsi_sync_status", side_effect=_boom), \
                patch.object(hsi_resolver, "estado_panel", side_effect=_boom), \
                patch.object(hsi_resolver, "resolver_pass", side_effect=_boom), \
                patch.object(db, "_connect", side_effect=_boom), \
                patch.object(socket.socket, "connect", _boom), \
                patch.object(socket.socket, "connect_ex", _boom), \
                patch.object(socket, "create_connection", _boom):
            for path in ("/admin/hsi", "/admin/hsi?conflicto=zz-query-sentinel-7&accion=rechazar#hsi"):
                with self.subTest(path=path):
                    response = self.client.get(path, cookies=_cookies())
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('<div id="sec-hsi"></div>', response.text)
                    # nothing panel-shaped is server-rendered (the controller
                    # draws it client-side) and the query state is never echoed
                    for needle in ("pct_futuras", "conflicto_id", "hsi-rescan-btn",
                                   "Resueltos automáticamente", "zz-query-sentinel"):
                        self.assertNotIn(needle, response.text, needle)

    def test_render_leaks_no_configuration_or_environment_value(self):
        sentinels = {
            "HSI_URL": "https://hsi-leak-sentinel.invalid/api",
            "HSI_USER": "hsi-user-leak-sentinel",
            "HSI_PASSWORD": "hsi-password-leak-sentinel",
            "HSI_INSTITUTION_ID": "hsi-institution-leak-sentinel",
        }
        with _env.patched_env(sentinels):
            config._reset_cache_para_tests()
            response = self.client.get("/admin/hsi", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        for value in sentinels.values():
            self.assertNotIn(value, response.text, value)
        self.assertNotIn("TURNOS_SKIP_HSI", response.text)
        self.assertNotIn("HSI_URL", response.text)

    def test_skip_hsi_changes_the_api_content_not_the_route(self):
        bodies = {}
        with patch.object(booking_requests.store, "hsi_sync_status", return_value=dict(FAKE_STATUS)), \
                patch.object(hsi_resolver, "estado_panel", return_value=None):
            for value in ("1", "0"):
                with self.subTest(TURNOS_SKIP_HSI=value):
                    # The emergency switch only applies to an explicitly
                    # selected connector. An unconfigured Core is inactive
                    # regardless of TURNOS_SKIP_HSI, so select the legacy HSI
                    # adapter here to isolate the switch this test names.
                    with _env.patched_env({
                        "TURNOS_SKIP_HSI": value,
                        "CONNECTOR_EMR_SYNC": "hsi",
                    }):
                        config._reset_cache_para_tests()
                        screen = self.client.get("/admin/hsi", cookies=_cookies())
                        api = self.client.get("/api/admin/hsi-sync", cookies=_cookies())
                    self.assertEqual(screen.status_code, 200)
                    self.assertEqual(api.status_code, 200)
                    self.assertEqual(api.json()["hsi_activo"], value != "1")
                    bodies[value] = screen.text
        # the screen is byte-identical either way: the flag never reaches it
        self.assertEqual(bodies["1"], bodies["0"])


if __name__ == "__main__":
    unittest.main()
