"""Shared-shell contracts for every direct ``/admin/<screen>`` render
(templates/admin/base.html) and the legacy ``/admin`` gate — the slice that
gave both shells ONE toast provider (admin-shell.js + admin-shell.css), ONE
DNI formatter (Format.dni in format.mjs — no server mirror: nothing
server-side renders a DNI) and the red-triage bell
(platform/admin-alerts.mjs) on 2026-08-31.

What is proven here, on the RENDERED response of the real composition (no
source scanning):

* the shared cohort (i18n, format, dates, admin-shell, admin-alerts) loads
  EXACTLY ONCE per direct screen, in order, and admin-shell.css is a linked
  stylesheet — never an inline copy, never a static toast node;
* the bell is initialized with the role the SERVER verified for the request
  (``sanaInitAlerts("<rol>")`` comes from ``admin_auth.active_role()``, a
  request-scoped ContextVar bound by ``deps.require_admin`` — not from a
  template variable a renderer could forget to pass): admin and medico get
  their own rol, a non-alert role (admisionista) still gets its exact rol
  string — the JS guard decides, the server does not editorialize — and an
  unauthenticated request renders no init at all;
* the title band extraction duplicated/removed/reordered nothing: exactly
  one ``adm-topbar`` and one ``#adm-h1`` per screen, and the screen-specific
  controls that used to sit in the child's own header are still there, in
  the same band position (solicitudes' create button, settings' subnav,
  my-patients' aria-labelledby target);
* the legacy shell links the very same assets and dropped its inline copy.

Behavior of the scripts themselves (toast attributes, bell polling/ack,
role teardown) lives in tests/admin-shell-shared.test.mjs; the real-browser
path (announcement semantics, visibility, responsive fit) in
tests/e2e/admin-shell.spec.mjs.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_shared_shell.py"
"""

import os
import sys
import unittest
from html.parser import HTMLParser

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-shared-shell-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# The shared runtime base.html loads for EVERY direct screen, before any
# child controller. i18n first (every controller's T() needs it), the two
# shared module loader/toast provider, session boundary, and bell. The
# formatter is published in window.SANA_STATIC and imported by consumers.
SHARED_COHORT = ("platform/i18n.mjs", "admin-shell.js", "platform/admin-alerts.mjs")

# Every direct screen an admin session can render under the generic test
# pack, each with the one id that must still be there EXACTLY once (the
# child's own mount point — the band must not have swallowed it).
# /admin/plan is Pinamar-only (pack-gated) and /admin/mis-turnos denies
# admin (medico's own agenda); both are covered below through the roles
# that DO own them.
ADMIN_SCREENS = (
    ("/admin/hsi", "sec-hsi"),
    ("/admin/cola", "sec-cola"),
    ("/admin/solicitudes", "sec-solicitudes"),
    ("/admin/settings", "sec-settings"),
    ("/admin/my-patients", "sec-mis-pacientes"),
    ("/admin/preparations", "sec-preparaciones"),
    ("/admin/knowledge", "sec-knowledge"),
    ("/admin/users", "sec-usuarios"),
    ("/admin/account", "sec-cuenta"),
    ("/admin/appearance", "sec-configuracion"),
    ("/admin/system", "sec-sistema"),
    ("/admin/prompts", "sec-prompts"),
    ("/admin/legal", "sec-legal"),
    ("/admin/pacientes", "sec-pacientes"),
    ("/admin/reportes", "sec-reportes"),
    ("/admin/services", "sec-servicios"),
    ("/admin/centers", "sec-centros"),
    ("/admin/doctors", "sec-medicos"),  # editor id is the Spanish one (admin_content_screen.py)
)


def _cookies(role="admin", user="shared-shell-test"):
    return {"st_admin": admin_auth._mint_token(user, role)}


def _fresh_2fa_deadline(test, *users):
    """These sessions have no real account, so the gate starts a 7-day 2FA
    enrolment deadline for them in whatever DB the run points at. A row left
    by a run more than 7 days ago is an EXPIRED deadline, and every screen
    then redirects to /admin/account?setup=2fa — the test failed when run
    alone and passed only inside a run on a fresh DB. Clear the deadline
    before and after, so the gate starts a new one. No DB: nothing to clear."""
    from app.core import db_auth

    def clear():
        for user in users:
            try:
                db_auth.admin_security_set_deadline(user, None)
            except Exception:  # noqa: BLE001 — no DB lane: no deadline either
                pass
    clear()
    test.addCleanup(clear)


def _elements_of_class(html, class_name):
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.matches = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if class_name in (attrs.get("class") or "").split():
                self.matches.append((tag, attrs))

    parser = Elements()
    parser.feed(html)
    parser.close()
    return parser.matches


class TestElementClassOracle(unittest.TestCase):
    def test_css_comments_and_attribute_text_are_not_extra_controls(self):
        html = ('<style>.set-subnav a{}.set-subnav{display:flex}</style>'
                '<!-- set-subnav --><div data-note="set-subnav"></div>'
                '<nav class="extra set-subnav"></nav>')
        self.assertEqual(_elements_of_class(html, "set-subnav"),
                         [("nav", {"class": "extra set-subnav"})])

    def test_duplicate_controls_are_still_detected(self):
        html = '<nav class="set-subnav"></nav>' * 2
        self.assertEqual(len(_elements_of_class(html, "set-subnav")), 2)


class TestDirectScreensSharedShell(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        _fresh_2fa_deadline(self, "shared-shell-test")
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def _get(self, path, role="admin"):
        response = self.client.get(path, cookies=_cookies(role))
        self.assertEqual(response.status_code, 200, path)
        return response.text

    def test_shared_cohort_loads_once_in_order_and_the_toast_is_never_inlined(self):
        for path, section_id in ADMIN_SCREENS:
            with self.subTest(screen=path):
                html = self._get(path)
                # The shared runtime: each asset exactly once, in cohort order.
                offsets = []
                for asset in SHARED_COHORT:
                    url = static_fingerprint.url(asset)
                    self.assertEqual(html.count(url), 1, (path, asset))
                    offsets.append(html.index(url))
                self.assertEqual(offsets, sorted(offsets), f"{path}: cohort out of order")
                self.assertEqual(html.count(static_fingerprint.url("platform/admin-session.mjs")), 1, path)
                # admin-shell.css is a LINKED stylesheet, exactly once.
                css_url = static_fingerprint.url("admin-shell.css")
                self.assertEqual(html.count(css_url), 1, path)
                self.assertIn(f'<link rel="stylesheet" href="{css_url}">', html)
                # admin-gate.css too — since the shared-utilities slice the
                # gate/.btn base rules live ONLY there, so a direct screen
                # that dropped the link would lose its buttons wholesale.
                gate_url = static_fingerprint.url("admin-gate.css")
                self.assertEqual(html.count(gate_url), 1, path)
                self.assertIn(f'<link rel="stylesheet" href="{gate_url}">', html)
                # No inline copy of the toast rules, no static toast node —
                # the node is admin-shell.js's to create, on first use.
                self.assertNotIn(".toast{", html, path)
                self.assertNotIn('id="toast"', html, path)
                # The role the server verified, exactly one init, no leak of
                # the jinja global that produced it.
                self.assertEqual(html.count('const role = "admin";'), 1, path)
                self.assertNotIn("screen_rol", html, path)
                # Title band: one band, one h1 — nothing duplicated, and the
                # child's own mount point survived the extraction.
                self.assertEqual(html.count('class="adm-topbar"'), 1, path)
                self.assertEqual(html.count('id="adm-h1"'), 1, path)
                self.assertEqual(html.count(f'id="{section_id}"'), 1, path)

    def test_band_extraction_kept_every_screen_specific_control(self):
        # solicitudes: the create button moved WITH the title into the band —
        # h1 first, then the button, both inside the one header.
        html = self._get("/admin/solicitudes")
        band = html.index('class="adm-h1row"')
        h1 = html.index('id="adm-h1"')
        boton = html.index('id="adm-crear-turno"')
        header_end = html.index("</header>")
        self.assertLess(band, h1)
        self.assertLess(h1, boton)
        self.assertLess(boton, header_end)
        self.assertEqual(html.count('id="adm-crear-turno"'), 1)
        # settings: its subnav is its own, still exactly once, still before
        # the content wrap.
        html = self._get("/admin/settings")
        subnavs = _elements_of_class(html, "set-subnav")
        self.assertEqual(len(subnavs), 1)
        self.assertEqual(subnavs[0][0], "nav")
        self.assertLess(html.index('<nav class="set-subnav"'),
                        html.index('id="sec-settings"'))
        self.assertIn('id="sec-settings"', html)
        # my-patients: labels the region by the shared h1 — target resolves.
        html = self._get("/admin/my-patients")
        self.assertEqual(html.count('aria-labelledby="adm-h1"'), 1)
        self.assertEqual(html.count('id="adm-h1"'), 1)
        # services/centers/doctors share ONE module template: the subnav and
        # the status line are per-render, not per-template.
        for path in ("/admin/services", "/admin/centers", "/admin/doctors"):
            html = self._get(path)
            self.assertEqual(html.count('id="adm-sub"'), 1, path)
            self.assertEqual(html.count('id="content-status"'), 1, path)
        # screens that seed the role for their controllers still do.
        html = self._get("/admin/cola")
        self.assertIn('window.CURRENT_ROLE = "admin"', html)

    def test_the_bell_is_initialized_with_the_role_the_server_verified(self):
        # medico: their own screens, their own role string.
        for path in ("/admin/cola", "/admin/my-patients", "/admin/mis-turnos"):
            with self.subTest(screen=path):
                html = self._get(path, role="medico")
                self.assertEqual(html.count('const role = "medico";'), 1, path)
        # admisionista: allowed on the queue, NOT an alert role — the render
        # still carries the exact verified rol; the widget's own guard
        # decides (proven in admin-shell-shared.test.mjs), the server does
        # not editorialize the string.
        html = self._get("/admin/cola", role="admisionista")
        self.assertEqual(html.count('const role = "admisionista";'), 1)
        self.assertNotIn('const role = "admin";', html)
        # reportes sees its own screen with its own role.
        html = self._get("/admin/reportes", role="reportes")
        self.assertEqual(html.count('const role = "reportes";'), 1)

    def test_a_request_the_gate_denies_renders_no_bell_init(self):
        # No session at all: the gate answers before any render — no init
        # string, no cohort, nothing to run.
        response = self.client.get("/admin/hsi", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("mountAlerts", response.text)
        # operador: a retired token is no session either — same rule.
        response = self.client.get("/admin/hsi", cookies=_cookies(role="operador"),
                                   follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("mountAlerts", response.text)

    def test_medico_denied_where_admin_belong(self):
        response = self.client.get("/admin/hsi", cookies=_cookies(role="medico"))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("mountAlerts", response.text)


class TestAdminRootRetiresTheLegacyShell(unittest.TestCase):
    def setUp(self):
        _fresh_2fa_deadline(self, "legacy-shell-test")
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_authenticated_root_redirects_without_serving_shell_assets(self):
        response = self.client.get(
            "/admin",
            cookies=_cookies(user="legacy-shell-test"),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/admin/solicitudes")
        self.assertEqual(response.headers["cache-control"], "no-store")
        for asset in ("admin-shell.css", "admin-shell.js", "platform/admin-alerts.mjs",
                      "admin-app.js"):
            self.assertNotIn(static_fingerprint.url(asset), response.text, asset)


if __name__ == "__main__":
    unittest.main()
