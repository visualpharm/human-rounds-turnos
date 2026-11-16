"""Focused Tier 4 proof for the fingerprint cohorts the admin shells (and the
patient wizard, for the two shared utilities) render through: the root admin
JavaScript cohort and ADMIN_SHELL_UTILITIES.

Run: python3 -m unittest discover -s tests -p "test_admin_static_fingerprint_rendering.py"
"""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults({"TURNOS_ADMIN_SECRET": "admin-static-render-test-secret"})

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import paths, static_fingerprint  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import theme  # noqa: E402
from app.patient_web import site_catalog, site_page_service
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class TestAdminStaticFingerprintRendering(unittest.TestCase):
    _ASGI_TEMPLATES = {
        "cola.html": {"platform/admin-queue.mjs", "platform/admin-queue.css"},
        "pacientes.html": {"platform/admin-patients.mjs"},
        "mis-turnos.html": {"platform/admin-my-appointments.mjs"},
        "my-patients.html": {"platform/admin-control-fit.mjs",
                             "platform/admin-doctor-patients.mjs"},
        # reportes.html boots the native Reports controller from MIGRATED
        # (platform/admin-reports.mjs), which resolves its five runtime
        # dependencies (i18n, dates, charts, search, admin-session) from
        # CLIENT_LOADED via window.SANA_STATIC — the module URL is covered
        # here; the manifest keys' rendered URLs are pinned by
        # test_admin_screen_contract.
        "reportes.html": {"platform/admin-reports.mjs"},
    }

    @staticmethod
    def _admin_response(theme_name, path="/admin/solicitudes"):
        """Exercise a mounted direct admin screen with a real session."""
        with patch.object(theme, "current", return_value=theme_name):
            token = admin_auth._mint_token("static-render-admin", "admin")
            with patch.object(admin_auth, "account_security", return_value={
                "tiene_2fa": False, "2fa_deadline": None, "2fa_vencido": False,
            }):
                return TestClient(app_module.app).get(
                    path, cookies={admin_auth.COOKIE_NAME: token})

    def test_closed_cohort_matches_the_root_admin_assets(self):
        actual = {
            name for name in os.listdir(os.path.join(ROOT, paths.ROOT_WEB_ASSETS_DIR))
            if name.startswith("admin-") and name.endswith(".js")
        }
        self.assertEqual(static_fingerprint.ADMIN_ROOT_JAVASCRIPT, actual)
        self.assertTrue(actual <= static_fingerprint.MIGRATED)
        self.assertIn("platform/admin-control-fit.mjs", static_fingerprint.MIGRATED)
        self.assertNotIn("platform/admin-control-fit.mjs", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertNotIn("modules/intake/admin-entrevistas.mjs", static_fingerprint.MIGRATED)
        self.assertNotIn("modules/intake/admin-entrevistas.js", static_fingerprint.MIGRATED)

    def test_closed_utilities_cohort_is_empty(self):
        # Every former non-admin classic shell utility has moved to a canonical
        # module. A new direct script would have to enter this closed set.
        self.assertEqual(static_fingerprint.ADMIN_SHELL_UTILITIES, set())
        for name in static_fingerprint.ADMIN_SHELL_UTILITIES:
            with self.subTest(name=name):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, paths.ROOT_WEB_ASSETS_DIR, name)))
                self.assertNotIn(name, static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
                self.assertRegex(static_fingerprint.url(name),
                                 r"^/static/[a-z-]+\.[0-9a-f]{8}\.js$")

    def test_closed_client_loaded_cohort_matches_the_runtime_loaded_assets(self):
        # Tier 4 slice 6, batch 3: the assets a STATIC JS file loads by URL at
        # runtime — the cohort window.SANA_STATIC publishes. Nested
        # platform/ names resolve under static/; remaining root names stay
        # at repo root. None is an admin bundle or shell utility, all are
        # MIGRATED. A new runtime-loaded asset means adding it here AND
        # resolving its loader through window.SANA_STATIC in the same commit.
        self.assertEqual(static_fingerprint.CLIENT_LOADED,
                         {"platform/nav.mjs", "platform/directory.mjs", "platform/account-form.mjs", "platform/dependents.mjs",
                     "platform/account-requests.mjs", "platform/account-labs.mjs",
                     "platform/dni-barcode.mjs", "platform/dni-scan.mjs", "platform/age.mjs",
                     "platform/calendar-grid.mjs", "platform/hours.mjs", "platform/dates.mjs",
                     "platform/format.mjs", "platform/prep.mjs", "platform/islands.mjs",
                     "platform/turnstile.mjs", "platform/passkeys.mjs", "platform/admin-combo.mjs", "platform/admin-filter-controls.mjs",
                     "platform/admin-patient-summary.mjs", "platform/admin-preferences.mjs", "platform/admin-table.mjs",
                     "platform/admin-session.mjs", "platform/i18n.mjs", "platform/week.mjs",
                     "platform/admin-charts.mjs", "platform/search.mjs", "platform/admin-login.mjs",
                     "platform/listing.mjs", "platform/admin-solicitudes.mjs", "platform/admin-hours-editor.mjs",
                     "platform/admin-users.mjs",
                     "platform/admin-prompts.mjs", "platform/admin-legal.mjs", "platform/admin-knowledge.mjs",
                     "platform/chat-widget.mjs",
                     "platform/chat-ui.mjs",
                     "platform/admin-create-appointment.mjs", "platform/backup-key.mjs",
                     "platform/hsi-sync.mjs",
                     "platform/attendance-forecast.mjs",
                     "platform/intake-explainer.mjs",
                     "platform/print-head.mjs", "platform/analytics.mjs"})
        for name in static_fingerprint.CLIENT_LOADED:
            with self.subTest(name=name):
                self.assertTrue(os.path.isfile(static_fingerprint._physical_path(name)))
                self.assertNotIn(name, static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
                self.assertNotIn(name, static_fingerprint.ADMIN_SHELL_UTILITIES)
                self.assertIn(name, static_fingerprint.MIGRATED)
                self.assertEqual(static_fingerprint.manifest()[name],
                                 static_fingerprint.url(name))

    def test_admin_app_extraction_contract(self):
        # admin-app.js is the requests-screen controller.  Its direct screen
        # owns the sole script reference, fingerprinted through Jinja rather
        # than through the retired shell's placeholder syntax.
        self.assertIn("admin-app.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "root", "admin-app.js")))
        with open(os.path.join(ROOT, "templates", "admin", "solicitudes.html"), encoding="utf-8") as fh:
            screen = fh.read()
        self.assertEqual(screen.count("{{ static('admin-app.js') }}"), 1)
        self.assertNotIn("admin-app.js?v=", screen)
        self.assertNotIn("/admin-app.js", screen)
        # url() resolves for it like every other cohort member.
        self.assertRegex(static_fingerprint.url("admin-app.js"),
                         r"^/static/admin-app\.[0-9a-f]{8}\.js$")

    def test_admin_session_extraction_contract(self):
        self.assertNotIn("admin-login.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "admin-login.js")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "platform", "admin-login.mjs")))
        self.assertIn("platform/admin-login.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "admin-session.mjs")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "platform", "admin-session.mjs")))
        for name in ("admin-preferences.mjs", "admin-table.mjs", "admin-combo.mjs",
                     "admin-filter-controls.mjs", "admin-control-fit.mjs",
                     "admin-patient-summary.mjs"):
            self.assertFalse(os.path.isfile(os.path.join(ROOT, name)), name)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "platform", name)), name)
        self.assertNotIn("admin-session.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertIn("platform/admin-session.mjs", static_fingerprint.CLIENT_LOADED)
        with open(os.path.join(ROOT, "templates", "admin", "base.html"), encoding="utf-8") as fh:
            base = fh.read()
        with open(os.path.join(ROOT, "templates", "admin", "solicitudes.html"), encoding="utf-8") as fh:
            screen = fh.read()
        self.assertNotIn("{{ static('admin-session.js') }}", base)
        self.assertNotIn("admin-session.js", base)
        self.assertEqual(screen.count("{{ static('admin-app.js') }}"), 1)
        self.assertNotIn("admin-login.js", base)
        self.assertNotIn("{{ static('admin-login.js') }}", base)
        self.assertRegex(static_fingerprint.url("platform/admin-session.mjs"),
                         r"^/static/platform/admin-session\.[0-9a-f]{8}\.mjs$")
        self.assertRegex(static_fingerprint.url("platform/admin-login.mjs"),
                         r"^/static/platform/admin-login\.[0-9a-f]{8}\.mjs$")

    def test_admin_users_extraction_contract(self):
        self.assertNotIn("admin-users.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "admin-users.js")))
        self.assertTrue(os.path.isfile(os.path.join(
            ROOT, "static", "platform", "admin-users.mjs")))
        self.assertIn("platform/admin-users.mjs", static_fingerprint.CLIENT_LOADED)
        with open(os.path.join(ROOT, "templates", "admin", "users.html"), encoding="utf-8") as fh:
            users = fh.read()
        with open(os.path.join(ROOT, "templates", "admin", "content.html"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertNotIn("admin-users.js", users)
        self.assertNotIn("{{ static('admin-users.js') }}", users)
        self.assertIn('SANA_STATIC["platform/admin-users.mjs"]', users)
        self.assertIn("m.boot()", users)
        self.assertNotIn("admin-users.js", content)
        self.assertRegex(static_fingerprint.url("platform/admin-users.mjs"),
                         r"^/static/platform/admin-users\.[0-9a-f]{8}\.mjs$")

    def test_create_appointment_extraction_contract(self):
        self.assertNotIn("admin-create-appointment.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "admin-create-appointment.js")))
        self.assertTrue(os.path.isfile(os.path.join(
            ROOT, "static", "platform", "admin-create-appointment.mjs")))
        self.assertIn("platform/admin-create-appointment.mjs", static_fingerprint.CLIENT_LOADED)
        for rel in ("templates/admin/solicitudes.html",
                    "templates/admin/cola.html",
                    "templates/admin/content.html"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotIn("admin-create-appointment.js", src)
            self.assertNotIn("{{ static('admin-create-appointment.js') }}", src)
        self.assertRegex(static_fingerprint.url("platform/admin-create-appointment.mjs"),
                         r"^/static/platform/admin-create-appointment\.[0-9a-f]{8}\.mjs$")

    def test_native_admin_render_has_only_current_fingerprint_urls(self):
        response = self._admin_response("pinamar")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        expected = {
            "platform/admin-alerts.mjs", "admin-shell.js",
            "admin-app.js", "platform/admin-combo.mjs", "platform/admin-control-fit.mjs",
            "platform/photo-zoom.mjs",
            "platform/admin-preferences.mjs",
            "platform/admin-session.mjs", "platform/admin-solicitudes.mjs", "platform/admin-table.mjs",
        }
        for name in expected:
            with self.subTest(name=name):
                self.assertIn(static_fingerprint.url(name), body)
                self.assertNotIn(f'/{name}?v=', body)
        self.assertIn(static_fingerprint.url("platform/format.mjs"), body)
        self.assertNotIn("{{static.admin-", body)
        self.assertNotIn("{{static.calendar-grid" + ".js}}", body)
        self.assertNotIn("{{static.format.js}}", body)
        self.assertNotIn("{{static.islands.js}}", body)
        self.assertNotIn("{{static.passkeys.js}}", body)

    def test_usability_screen_loads_its_fingerprinted_controller(self):
        with patch("app.modules.turnos.study.enabled", return_value=True):
            response = self._admin_response("pinamar", "/admin/usability")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertIn(static_fingerprint.url("platform/admin-usability.mjs"),
                      response.text)
        self.assertNotIn("platform/admin-usability.mjs", static_fingerprint.CLIENT_LOADED)

    def test_direct_admin_render_publishes_the_client_manifest_once(self):
        # The direct requests screen dynamic-imports
        # platform/admin-create-appointment.mjs through the client manifest,
        # whose desk scanner import()s dni-scan.mjs / dni-barcode.mjs at runtime
        # — so the shell emits window.SANA_STATIC exactly once, through
        # {{ static_manifest() }}, with exactly the
        # CLIENT_LOADED keys resolved to their CURRENT fingerprinted URLs.
        import json
        import re as _re
        with open(os.path.join(ROOT, "templates", "admin", "base.html"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("{{ static_manifest() }}"), 1)
        body = self._admin_response("pinamar").text
        self.assertNotIn("{{static.manifest}}", body)
        self.assertEqual(body.count("window.SANA_STATIC="), 1)
        self.assertIn(static_fingerprint.manifest_script(), body)
        payload = _re.search(r"window\.SANA_STATIC=(\{.*?\});</script>", body).group(1)
        self.assertEqual(json.loads(payload), static_fingerprint.manifest())
        self.assertEqual(set(json.loads(payload)), static_fingerprint.CLIENT_LOADED)
        # The manifest precedes the loader that consumes it.
        self.assertLess(body.index("window.SANA_STATIC="),
                        body.index(static_fingerprint.url("admin-app.js")))
        self.assertIn(static_fingerprint.url("platform/admin-create-appointment.mjs"), body)
        for name in static_fingerprint.CLIENT_LOADED:
            self.assertNotIn(f"/{name}?v=", body)

    def test_generic_admin_does_not_load_reference_deployment_roadmap(self):
        body = self._admin_response("humanrounds").text
        self.assertNotIn('id="nav-plan"', body)
        self.assertNotIn('id="sec-plan"', body)
        self.assertNotIn(static_fingerprint.url("platform/admin-roadmap.mjs"), body)
        self.assertNotIn("admin.system.roadmap.", body)
        self.assertNotIn("turnos.pinamar.gob.ar", body)

    def test_all_admin_surfaces_have_no_legacy_root_admin_urls(self):
        # Every direct admin template carries no `?v=` alias for any
        # migrated root asset.  solicitudes.html used to load format.js at
        # /format.js?v=4; none of those aliases may come back.
        sources = sorted(
            os.path.join(ROOT, "templates", "admin", template)
            for template in os.listdir(os.path.join(ROOT, "templates", "admin"))
            if template.endswith(".html")
        )
        for path in sources:
            with self.subTest(path=os.path.relpath(path, ROOT)), open(path, encoding="utf-8") as fh:
                source = fh.read()
            for name in static_fingerprint.ADMIN_ROOT_JAVASCRIPT | static_fingerprint.ADMIN_SHELL_UTILITIES:
                self.assertNotIn(f'{name}?v=', source)
        # The bare-path check (no `?v=` needed to be a legacy alias) stays
        # scoped to the four screen templates it always covered — base.html
        # names admin-*.js files in prose comments ("admin-queue.js/
        # static/platform/admin-patients.mjs/…"), which is not a URL.
        for template in self._ASGI_TEMPLATES:
            path = os.path.join(ROOT, "templates", "admin", template)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            for name in static_fingerprint.ADMIN_ROOT_JAVASCRIPT:
                self.assertNotIn(f'/{name}', source)

        self.assertFalse(os.path.exists(os.path.join(ROOT, "admin.html")),
                         "the retired hash shell must not return as source")

        for template, names in self._ASGI_TEMPLATES.items():
            path = os.path.join(ROOT, "templates", "admin", template)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            for name in names:
                self.assertIn("{{ static('" + name + "') }}", source)

        # Shared direct-screen dependencies live once in base.html, not in
        # each child template. The rendered-response suite proves the final
        # inherited order; this source guard pins ownership.
        base = os.path.join(ROOT, "templates", "admin", "base.html")
        with open(base, encoding="utf-8") as fh:
            base_source = fh.read()
        for name in ("admin-shell.js", "platform/admin-alerts.mjs"):
            self.assertIn("{{ static('" + name + "') }}", base_source)
        self.assertIn("static_manifest()", base_source)
        self.assertIn("platform/i18n.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertIn("platform/admin-charts.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertIn("platform/search.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertIn("platform/admin-login.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertIn("platform/listing.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertIn("platform/chat-widget.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertIn("platform/chat-ui.mjs", static_fingerprint.CLIENT_LOADED)


class TestPatientShellStaticFingerprintRendering(unittest.TestCase):
    """The patient wizard loads the shared formatter directly and publishes
    the canonical calendar module through the fingerprinted client manifest.
    The deleted UMD script and its old query-string alias stay absent."""

    @staticmethod
    def _admin_response(theme_name):
        with patch.object(theme, "current", return_value=theme_name):
            token = admin_auth._mint_token("patient-shared-admin", "admin")
            with patch.object(admin_auth, "account_security", return_value={
                "tiene_2fa": False, "2fa_deadline": None, "2fa_vencido": False,
            }):
                return TestClient(app_module.app).get(
                    "/admin/doctors",
                    cookies={admin_auth.COOKIE_NAME: token},
                )

    @classmethod
    def setUpClass(cls):
        cls.service = next(
            (s for s in site_catalog._services()
             if str(s.get("modo", "")).startswith("solicitud")), None)
        assert cls.service is not None, "the demo pack has no solicitud-mode service"
        cls.html = site_page_service._page_service_appointment(
            cls.service["id"]).decode("utf-8")

    def test_formatter_and_calendar_modules_use_current_fingerprints(self):
        self.assertIn(static_fingerprint.url("platform/format.mjs"), self.html)
        self.assertIn(static_fingerprint.url("platform/calendar-grid.mjs"), self.html)
        self.assertNotIn("/format.js?v=", self.html)
        self.assertNotIn("calendar-grid" + ".js", self.html)

    def test_same_canonical_url_as_the_admin_shell(self):
        # One content hash, one URL: the patient page and the native admin
        # shell carry the byte-identical fingerprinted reference — the
        # coherence the whole cohort exists for.
        admin_body = self._admin_response("pinamar").text
        for name in ("platform/format.mjs", "platform/calendar-grid.mjs"):
            with self.subTest(name=name):
                url = static_fingerprint.url(name)
                self.assertIn(url, self.html)
                self.assertIn(url, admin_body)

    def test_wizard_load_order_does_not_change(self):
        # Dependency order mirrored from the wizard's classic-script block;
        # dates.mjs and age.mjs are loaded by service.js through the
        # client manifest. platform/account-form.mjs is loaded lazily at
        # mountAuth through that same manifest — never as a classic <script>.
        # platform/dependents.mjs is loaded lazily when the wizard decides
        # who the patient is — never as a classic <script>.
        # service.js closes the direct-script block; prep.mjs and format.mjs
        # are imported by service.js through the manifest before first render.
        # Pinned on the <script> TAGS, not the bare URLs: the
        # head's window.SANA_STATIC manifest also carries the Account
        # URL (as a JSON value, before any <script>), so the bare URL's first
        # occurrence is no longer its loading tag.
        service_url = static_fingerprint.module_url("modules/turnos/service.mjs")
        self.assertIn(service_url, self.html)
        self.assertIn('<script type="importmap">', self.html)
        self.assertIn("takePendingFile: takePendingFile", self.html)
        self.assertNotIn('src="' + service_url + '" defer', self.html)
        self.assertNotIn("dependents.js", self.html)
        self.assertIn(static_fingerprint.url("platform/dates.mjs"), self.html)
        self.assertIn(static_fingerprint.url("platform/prep.mjs"), self.html)
        self.assertIn(static_fingerprint.url("platform/account-form.mjs"), self.html)
        self.assertIn(static_fingerprint.url("platform/dependents.mjs"), self.html)
        self.assertNotIn("/prep.js", self.html)
        self.assertNotIn("/account-form.js", self.html)
        self.assertNotIn("account-form.js", self.html)

    def test_wizard_page_publishes_the_client_manifest_once_in_head(self):
        # The wizard loads platform/account-form.mjs through the manifest
        # (service.js importStatic at mountAuth), which import()s
        # dni-barcode.mjs on a DNI photo — so the `_shell()` head carries
        # window.SANA_STATIC exactly once, before </head> (hence before any
        # deferred loader runs), with the CLIENT_LOADED map resolved to
        # current URLs.
        import json
        import re as _re
        self.assertEqual(self.html.count("window.SANA_STATIC="), 1)
        self.assertIn(static_fingerprint.manifest_script(), self.html)
        self.assertLess(self.html.index("window.SANA_STATIC="), self.html.index("</head>"))
        payload = _re.search(r"window\.SANA_STATIC=(\{.*?\});</script>", self.html).group(1)
        self.assertEqual(json.loads(payload), static_fingerprint.manifest())
        self.assertIn("platform/age.mjs", json.loads(payload))


if __name__ == "__main__":
    unittest.main()
