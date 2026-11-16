"""test_admin_screen_contract.py — Tier 5 slice 4 (docs/roadmap/
tier5-frontend-design.md, "admin.html decomposition into /admin/<screen>"):
the module-contract acceptance property for the NEW `screen` shape,
mirroring test_module_contract_asgi.py's own discipline for `router_attr`
route entries — "disable a module -> its screens/routes 404, its nav
entries disappear, zero central edits" now proven for a THIRD contract
shape (JSON routers, screen routers).

Same `fastapi.testclient.TestClient` against the real `app.app` instance
and the same `config._db_overrides` monkeypatch pattern
test_module_contract_asgi.py already established, so `ENABLED_MODULES`
can flip mid-test with no reachable Postgres.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_screen_contract.py"
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-screen-contract-fixture-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no depender del .env real del checkout corriendo esto

from app.auth import admin_auth  # noqa: E402
from app import main as app_module  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.modules.turnos import doctor_appointments  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _enabled_setup(test_case):
    """Same config._db_overrides monkeypatch test_module_contract_asgi.py
    uses — lets ENABLED_MODULES flip mid-test with no Postgres reachable."""
    config._reset_cache_para_tests()
    store = {}

    def fake_overrides():
        return dict(store)

    patcher = patch.object(config, "_db_overrides", side_effect=fake_overrides)
    patcher.start()
    test_case.addCleanup(patcher.stop)
    test_case.addCleanup(config._reset_cache_para_tests)

    def _set_enabled(raw):
        store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    return _set_enabled


def _admin_cookies(usuario="screen-contract-test", rol="admin"):
    """{"st_admin": <token>} minted the same way admin_auth._mint_token()
    is used elsewhere in this suite (tests/_routes_fixture.py's own
    admin_cookie() helper, ported here for TestClient instead of a real
    socket) — no DB round trip, no real login flow."""
    return {"st_admin": admin_auth._mint_token(usuario, rol)}


class TestEntrevistasScreenGating(unittest.TestCase):
    """The 'entrevistas' pilot screen (modules/intake/
    admin_entrevistas_screen.py): disable/enable drives 404 through the
    SAME require_module_enabled() dependency every other router_attr
    route already uses -- app.app mounts intake's screen router
    UNCONDITIONALLY at import time, so every scenario below is about the
    PER-REQUEST gate, never about whether the router got mounted."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_disabled_module_404s_the_screen_and_never_renders(self):
        self._set_enabled("turnos")  # intake NOT listed -> disabled
        r = self.client.get("/admin/entrevistas", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 404)
        # Indistinguishable from a path that never existed (the product owner,
        # 2026-08-05, docs/modules.md) -- same doctrine every other
        # migrated route already proves.
        self.assertEqual(r.json(), {"detail": {"error": "no_encontrado"}})

    def test_enabled_module_renders_the_real_content(self):
        self._set_enabled("turnos,intake")
        r = self.client.get("/admin/entrevistas", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
        body = r.text
        # The container the canonical module paints into, AND the real
        # renderer import itself -- boot() keeps painting the content.
        self.assertIn('id="sec-entrevistas"', body)
        self.assertIn(
            static_fingerprint.module_url("modules/intake/admin-entrevistas.mjs"),
            body,
        )
        self.assertIn("import(url)", body)
        self.assertNotIn("admin-entrevistas.js", body)
        # El contrato sin-hash (auditoría Tier 5, 2026-08-06): base.html
        # declara la pantalla y el route() del renderer la lee cuando no
        # hay hash — la plantilla ya NO fuerza location.hash (eso lo
        # prohíbe TestScreenTemplatesNeverForceTheHash, abajo).
        self.assertIn('window.SANA_ADMIN_SCREEN = "entrevistas"', body)
        # The server-rendered sidebar shows the entry, marked active.
        self.assertIn('data-nav="entrevistas"', body)
        self.assertIn('href="/admin/entrevistas"', body)

    def test_toggling_intake_live_flips_the_screen_gate_without_remount(self):
        self._set_enabled("turnos")
        self.assertEqual(
            self.client.get("/admin/entrevistas", cookies=_admin_cookies()).status_code, 404)
        self._set_enabled("turnos,intake")
        self.assertEqual(
            self.client.get("/admin/entrevistas", cookies=_admin_cookies()).status_code, 200)
        self._set_enabled("turnos")
        self.assertEqual(
            self.client.get("/admin/entrevistas", cookies=_admin_cookies()).status_code, 404)

    def test_disabling_intake_also_drops_the_nav_entry_zero_central_edits(self):
        # "nav entries disappear" half of the acceptance property, proven
        # directly against the SAME function the server-rendered sidebar
        # (core/screen_nav.nav_items) and the legacy client
        # sidebar (GET /api/admin/modules) both call.
        self._set_enabled("turnos,intake")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertTrue(any(
            isinstance(s, dict) and s.get("id") == "entrevistas" for s in sections))
        self._set_enabled("turnos")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertFalse(any(
            isinstance(s, dict) and s.get("id") == "entrevistas" for s in sections))


class TestEntrevistasScreenRoleGate(unittest.TestCase):
    """Byte-compatible role gating: the manifest's `entrevistas` section
    declares no `roles` key, which app_modules._section_allowed_for_role()
    already resolves to admin-only for the sidebar -- this class proves
    the NEW server-routed screen enforces the exact same policy, not a
    more permissive one invented for the pilot."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos,intake")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/entrevistas", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_medico_session_403s_same_as_the_legacy_sidebar_denies_it(self):
        r = self.client.get("/admin/entrevistas", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 403)

    def test_admisionista_session_403s(self):
        r = self.client.get("/admin/entrevistas", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 403)

    def test_admin_session_renders(self):
        r = self.client.get("/admin/entrevistas", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)


class TestColaScreenRoleGate(unittest.TestCase):
    """Tier 5 slice 5 — 'cola' ("Pacientes de hoy"), second server-routed
    screen after intake's pilot. Byte-compatible role gating: the
    manifest declares `roles: ["admin", "medico", "admisionista"]`, the
    SAME three roles `doctor_appointments._day_queue()` itself admits —
    this proves the screen enforces that exact policy (admin_cola_screen.py's
    own docstring names the underlying handler this mirrors)."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/cola", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_reportes_session_403s(self):
        r = self.client.get("/admin/cola", cookies=_admin_cookies(rol="reportes"))
        self.assertEqual(r.status_code, 403)

    # No 'marca' case here: admin_auth._mint_token()/`_verify_token()` only
    # accept rol in ("admin", "medico", "admisionista", "reportes") — 'marca'
    # is a real admin_auth.require_admin() branch but isn't a mintable test
    # fixture role today (same reason TestEntrevistasScreenRoleGate never
    # covers it either).

    def test_admin_session_renders(self):
        r = self.client.get("/admin/cola", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sec-cola"', r.text)
        self.assertIn('data-queue-server-rendered="true"', r.text)
        self.assertIn('id="queue-initial-data"', r.text)
        self.assertIn(static_fingerprint.url("platform/admin-table.mjs"), r.text)
        self.assertIn(static_fingerprint.url("platform/admin-queue.mjs"), r.text)
        self.assertIn(static_fingerprint.url("platform/admin-queue.css"), r.text)
        self.assertIn('window.CURRENT_ROLE = "admin"', r.text)
        self.assertNotIn('id="app"', r.text)

    def test_first_document_contains_a_jinja_rendered_queue_row(self):
        initial = {
            "dia": "2026-09-03", "hoy": True, "total": 1,
            "grupos": [{"medico": "MARIA DEL CARMEN", "turnos": [{
                "hora": "09:00", "paciente": "ANA LOPEZ",
                "servicio": "Cardiología", "asistencia": None,
            }]}],
        }
        with patch("app.admin_panel.admin_cola_screen.doctor_appointments.day_queue_data",
                   return_value=initial):
            r = self.client.get("/admin/cola", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-queue-server-rendered="true"', r.text)
        self.assertIn("Maria del Carmen", r.text)
        self.assertIn("Ana Lopez", r.text)
        self.assertIn("Cardiología", r.text)
        self.assertIn('"dia": "2026-09-03"', r.text)

    def test_medico_session_renders(self):
        r = self.client.get("/admin/cola", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 200)

    def test_unlinked_medico_account_renders_instead_of_500ing(self):
        error = doctor_appointments.DomainError(
            400, {"error": "cuenta_sin_medico"})
        with patch("app.admin_panel.admin_cola_screen.doctor_appointments.day_queue_data",
                   side_effect=error):
            r = self.client.get("/admin/cola", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('"account_unlinked": true', r.text)
        self.assertIn('data-queue-server-rendered="true"', r.text)

    def test_admisionista_session_renders(self):
        r = self.client.get("/admin/cola", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 200)


class TestPacientesScreenRoleGate(unittest.TestCase):
    """'pacientes' — third server-routed screen. `roles: ["admin",
    "admisionista"]`, matching admin_patients.py's own
    `permitir_admisionista=True` (no `permitir_medico`)."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/pacientes", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_medico_session_403s_same_as_the_legacy_sidebar_denies_it(self):
        r = self.client.get("/admin/pacientes", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 403)

    def test_admin_session_renders(self):
        r = self.client.get("/admin/pacientes", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sec-pacientes"', r.text)
        self.assertIn(static_fingerprint.url("platform/admin-table.mjs"), r.text)
        self.assertIn(static_fingerprint.url("platform/admin-patients.mjs"), r.text)

    def test_admisionista_session_renders(self):
        r = self.client.get("/admin/pacientes", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 200)


class TestMisTurnosScreenRoleGate(unittest.TestCase):
    """'mis-turnos' — fourth server-routed screen, the FIRST real case of
    a `roles` list that excludes 'admin' (`roles: ["medico"]`). Proves
    admin_mis_turnos_screen.py's explicit `rol == "medico"` check actually
    rejects an admin session, not just the roles `require_admin`'s generic
    flags already reject — the exact gap `modules/intake/
    admin_entrevistas_screen.py`'s docstring named as an open follow-up."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/mis-turnos", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_admin_session_lands_on_its_start_page_not_a_json_error(self):
        # Review 03/11: an admin has no agenda of his own; the screen sends him
        # to his role's start page instead of printing {"error": "solo_medico"}.
        from app.admin_panel import admin_navigation
        r = self.client.get("/admin/mis-turnos", cookies=_admin_cookies(rol="admin"), follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], admin_navigation.default_path_for_role("admin") or "/admin/account")
        self.assertNotEqual(r.headers["location"], "/admin/mis-turnos")

    def test_admisionista_session_403s(self):
        r = self.client.get("/admin/mis-turnos", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 403)

    def test_medico_session_renders(self):
        r = self.client.get("/admin/mis-turnos", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sec-mis-turnos"', r.text)
        self.assertIn(static_fingerprint.url("platform/admin-table.mjs"), r.text)
        self.assertIn(static_fingerprint.url("platform/admin-my-appointments.mjs"), r.text)


class TestMyPatientsScreenRoleGate(unittest.TestCase):
    """The direct longitudinal roster preserves its API's admin+medico gate."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        self.assertEqual(self.client.get("/admin/my-patients", follow_redirects=False).status_code, 303)

    def test_admisionista_and_reportes_403(self):
        for rol in ("admisionista", "reportes"):
            with self.subTest(rol=rol):
                self.assertEqual(
                    self.client.get("/admin/my-patients", cookies=_admin_cookies(rol=rol)).status_code,
                    403,
                )

    def test_admin_and_medico_render_the_real_direct_shell(self):
        for rol in ("admin", "medico"):
            with self.subTest(rol=rol):
                r = self.client.get("/admin/my-patients", cookies=_admin_cookies(rol=rol))
                self.assertEqual(r.status_code, 200)
                self.assertIn('id="sec-mis-pacientes"', r.text)
                self.assertIn('window.SANA_ADMIN_SCREEN = "mis-pacientes"', r.text)
                # El rol ya no es un global: viaja al mount nativo del template.
                self.assertIn(f'mountDoctorPatients({{ root: host, role: "{rol}" }})', r.text)
                self.assertEqual(r.headers["cache-control"], "no-store")
                for name in ("platform/i18n.mjs", "platform/format.mjs", "platform/admin-preferences.mjs",
                             "platform/admin-table.mjs", "platform/admin-control-fit.mjs",
                             "platform/admin-doctor-patients.mjs"):
                    self.assertIn(static_fingerprint.url(name), r.text)


class TestMyPatientsScreenModuleGating(unittest.TestCase):
    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_disabled_turnos_is_indistinguishable_404_and_drops_nav(self):
        self._set_enabled("intake")
        r = self.client.get("/admin/my-patients", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"detail": {"error": "no_encontrado"}})
        self.assertNotIn(
            "mis-pacientes",
            [item.get("id") for item in app_modules.sections_payload_for_role("admin")
             if isinstance(item, dict)],
        )


class TestReportesScreenRoleGate(unittest.TestCase):
    """'reportes' — fifth server-routed screen, the THIRD role-gate shape.
    The manifest declares `roles: ["admin", "reportes"]`, the SAME two roles
    every handler in reports.py/reports_router.py itself admits via
    `admin_auth.require_admin(handler, solo_admin=True, permitir_reportes=True)`.
    This proves admin_reportes_screen.py reproduces that exact gate verbatim
    (`deps.require_admin(solo_admin=True, permitir_reportes=True)` — no extra
    post-check, unlike mis-turnos): reports ARE solo_admin, and 'reportes' is
    the ONE non-admin role solo_admin exempts. The distinctive case vs. cola/
    pacientes/mis-turnos is the 'reportes' session RENDERING here — it 403s on
    every sibling screen above."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/reportes", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_currency_is_passed_from_the_pack_without_an_executable_global(self):
        from app.core import format as formatting
        currency = {"code": "USD", "symbol": "</script>", "locale": "en-US", "iso2": "US", "_note": "private metadata"}
        with patch.object(formatting, "CURRENCY", currency):
            response = self.client.get("/admin/reportes", cookies=_admin_cookies(rol="admin"))
            self.assertEqual(response.status_code, 200)
            self.assertIn("currency: " + formatting.country_currency_json(), response.text)
        self.assertNotIn('"symbol": "</script>"', response.text)
        self.assertNotIn("private metadata", response.text)
        self.assertNotIn("window.SANA_COUNTRY_CURRENCY", response.text)

    def test_medico_session_403s(self):
        r = self.client.get("/admin/reportes", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 403)

    def test_admisionista_session_403s(self):
        r = self.client.get("/admin/reportes", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 403)

    def test_admin_session_renders(self):
        r = self.client.get("/admin/reportes", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        # The fixed native controller owns this host; the shared shell keeps
        # navigation and title. Its five dependencies come from the manifest.
        self.assertIn('id="sec-reportes"', r.text)
        self.assertIn('import("' + static_fingerprint.url("platform/admin-reports.mjs") + '")', r.text)
        self.assertNotIn('admin-reports.js', r.text)
        manifest = json.loads(r.text.split("window.SANA_STATIC=", 1)[1].split("</script>", 1)[0].rstrip(";"))
        for name in ("i18n", "dates", "admin-charts", "search", "admin-session"):
            asset = "platform/" + name + ".mjs"
            self.assertEqual(manifest[asset], static_fingerprint.url(asset))
        # The shared loader owns readiness, the late-load copy, pagehide and
        # the persisted-pageshow remount (static/platform/screen-boot.mjs).
        self.assertIn(static_fingerprint.url("platform/screen-boot.mjs"), r.text)
        self.assertIn('recovery: "reports-load-recovery"', r.text)
        self.assertIn('id="reports-load-recovery"', r.text)
        self.assertNotRegex(r.text, r'src="/static/admin-charts\.')
        self.assertIn(static_fingerprint.url("platform/admin-charts.mjs"), r.text)
        self.assertNotRegex(r.text, r'src="/static/search\.')
        self.assertIn(static_fingerprint.url("platform/search.mjs"), r.text)
        self.assertIn('window.SANA_ADMIN_SCREEN = "reportes"', r.text)
        # The server-rendered sidebar shows the item, marked active and
        # pointing at the direct screen (not the legacy hash).
        self.assertIn('data-nav="reportes"', r.text)
        self.assertIn('href="/admin/reportes"', r.text)
        # Search is a CLIENT_LOADED native module: the served page publishes
        # its fingerprinted URL in the client manifest and never a classic
        # <script src> for search.js.
        self.assertIn(static_fingerprint.url("platform/search.mjs"), r.text)
        self.assertNotIn("SanaSearch", r.text)

    def test_reportes_session_renders_the_distinctive_case(self):
        # The case that separates this screen from its siblings: the 'reportes'
        # session (read-only, no cola/pacientes/mis-turnos perms) PASSES the
        # gate exactly as it passes the underlying data endpoints' gate.
        r = self.client.get("/admin/reportes", cookies=_admin_cookies(rol="reportes"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sec-reportes"', r.text)


class TestReportesScreenModuleGating(unittest.TestCase):
    """Tier 5 slice 5 — reportes is a section of the TURNOS module (not a
    separately disableable leaf module like intake/entrevistas). The screen
    router declares `Depends(require_module_enabled("turnos"))` at the
    ROUTER level, so disabling turnos 404s `/admin/reportes` WITHOUT
    unmounting the route (Starlette cannot remove routes at runtime — the
    router is mounted once, the gate is per-request). This is the mirror of
    TestEntrevistasScreenGating for the case where the section lives in the
    CORE module: it proves that (a) the 404 is indistinguishable from a path
    that never existed (`{"detail": {"error": "no_encontrado"}}`, the
    docs/modules.md doctrine), (b) it toggles live without remount, and
    (c) the nav entry drops on its own — no central edits, via the SAME
    sections_payload_for_role() that the server-rendered sidebar and the
    client side both consume.

    enabled_ids() is pure config membership: an ENABLED_MODULES that does
    NOT list turnos disables it (empty/None = ALL, which is why an explicit
    value that excludes it is set here instead of "")."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_disabled_turnos_404s_the_screen_indistinguishably(self):
        self._set_enabled("intake")  # turnos NOT listed -> disabled
        r = self.client.get("/admin/reportes", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 404)
        # Indistinguishable from a path that never existed (the product owner, 2026-08-05,
        # docs/modules.md) — same JSON the interviews pilot asserts.
        self.assertEqual(r.json(), {"detail": {"error": "no_encontrado"}})

    def test_enabled_turnos_renders(self):
        self._set_enabled("turnos")
        r = self.client.get("/admin/reportes", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sec-reportes"', r.text)

    def test_toggling_turnos_flips_the_gate_without_remount(self):
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/reportes", cookies=_admin_cookies()).status_code, 404)
        self._set_enabled("turnos")
        self.assertEqual(
            self.client.get("/admin/reportes", cookies=_admin_cookies()).status_code, 200)
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/reportes", cookies=_admin_cookies()).status_code, 404)

    def test_disabling_turnos_drops_the_reportes_nav_entry_zero_central_edits(self):
        # The entry disappears from the payload BOTH sidebars read
        # (server-rendered by screen_nav.nav_items, client-side by GET
        # /api/admin/modules) when the owning module is disabled —
        # "zero central edits" is the other half of the acceptance property.
        self._set_enabled("turnos")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertTrue(any(
            isinstance(s, dict) and s.get("id") == "reportes" for s in sections))
        self._set_enabled("intake")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertFalse(any(
            isinstance(s, dict) and s.get("id") == "reportes" for s in sections))


class TestSolicitudesScreenRoleGate(unittest.TestCase):
    """'solicitudes' — sixth server-routed screen, and the FIRST whose
    controller was never a hash-section-isolated renderer the way
    cola/pacientes/mis-turnos/reportes were: admin-app.js IS the Solicitudes
    controller, so its boot grew a bootDirectScreen() branch instead of a
    route() fallback. The manifest declares `roles: ["admin", "medico",
    "admisionista"]` — the SAME three roles cola declares — so this proves
    admin_solicitudes_screen.py's `require_admin(permitir_medico=True,
    permitir_admisionista=True)` reproduces cola's gate byte-for-byte (the
    'reportes' session is the distinctive case: it passes cola/solicitudes
    siblings' generic flags but 403s here)."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/solicitudes", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_reportes_session_403s(self):
        r = self.client.get("/admin/solicitudes", cookies=_admin_cookies(rol="reportes"))
        self.assertEqual(r.status_code, 403)

    def test_medico_session_renders(self):
        r = self.client.get("/admin/solicitudes", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 200)

    def test_admisionista_session_renders(self):
        r = self.client.get("/admin/solicitudes", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 200)

    def test_admin_session_renders_the_full_contract(self):
        r = self.client.get("/admin/solicitudes", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        body = r.text
        # The container admin-app.js paints the queue into, the primary
        # "Crear turno" action hook, and the no-hash contract (Tier 5 audit
        # fix) base.html declares — same trio as the interviews pilot.
        self.assertIn('id="sec-solicitudes"', body)
        self.assertIn('id="adm-crear-turno"', body)
        self.assertIn('window.SANA_ADMIN_SCREEN = "solicitudes"', body)
        self.assertIn('window.CURRENT_ROLE = "admin"', body)
        self.assertNotIn('id="app"', body)
        # Legacy header/content wrappers (Terra finding #1): the template
        # MUST restore admin.html's .adm-topbar/.adm-h1row/.adm-content-wrap,
        # because base.html ships that <style> verbatim — the sticky title
        # bar and the 1240px `.adm-content-wrap:has(> #sec-solicitudes…)`
        # measure only apply when those exact wrappers carry the section.
        self.assertIn('<header class="adm-topbar">', body)
        self.assertIn('class="adm-h1row"', body)
        self.assertIn('<div class="adm-content-wrap">', body)
        # The hidden #adm-sub subtitle (Terra finding #3): admin.html carries
        # it inside .adm-content-wrap and the controller reaches #adm-sub
        # (routeHsi hides it); the direct template restores it for DOM parity.
        self.assertIn('id="adm-sub"', body)
        # Dependency order (Terra finding #1): the closed admin cohort loads
        # FINGERPRINTED via {{ static('...') }} (static_fingerprint.MIGRATED),
        # NOT the legacy /<name>?v= alias — asserting the RESOLVED hashed URL
        # in the served HTML, not just that the template rendered, is what
        # makes the fingerprint contract real (Terra review: pin it).
        # platform/photo-zoom.mjs + admin-control-fit.mjs join the cohort (Terra finding
        # #3): the order-image viewer ([data-photozoom]) and the .adm-sel
        # filter-width measurer are real dependencies of this screen.
        # admin-app.js imports the canonical typeable combo ES module through
        # the fingerprint manifest before rendering its controls.
        for name in ("platform/admin-preferences.mjs", "platform/admin-table.mjs",
                     "platform/admin-solicitudes.mjs", "platform/admin-create-appointment.mjs",
                     "admin-app.js", "platform/i18n.mjs", "platform/search.mjs",
                     "platform/photo-zoom.mjs", "platform/admin-control-fit.mjs"):
            self.assertIn(static_fingerprint.url(name), body,
                          f"{name} must load via its fingerprinted URL")
        self.assertIn(static_fingerprint.url("platform/admin-combo.mjs"), body)
        self.assertNotIn("admin-combo.js", body)
        # Pin the two restored dependencies by their resolved hashed URL shape
        # too, so a regression to the ?v= alias is caught on the served page.
        self.assertRegex(body, r'/static/platform/photo-zoom\.[0-9a-f]{8}\.mjs')
        self.assertRegex(body, r'/static/platform/admin-control-fit\.[0-9a-f]{8}\.mjs')
        # admin-preferences.mjs is the dependency the direct boot's
        # prefsReady gate loads before hydrateRequestPrefs(); pinning
        # it here is what makes the 'load preferences before request
        # hydration' finding testable on the served page.
        self.assertRegex(body, r'/static/platform/admin-preferences\.[0-9a-f]{8}\.mjs')
        # format.mjs is published through the client manifest and imported by
        # the requests controller before its first render.
        self.assertIn(static_fingerprint.url("platform/format.mjs"), body)
        self.assertNotIn('/format.js?v=', body)
        # Tier 4 slice 6, batch 3: platform/admin-create-appointment.mjs import()s
        # dni-scan.mjs / dni-barcode.mjs at runtime through window.SANA_STATIC,
        # so the direct screen (via templates/admin/base.html's
        # `{{ static_manifest() }}`) publishes the client manifest exactly
        # once, in <head>, with the CLIENT_LOADED cohort at its CURRENT
        # fingerprinted URLs — and no `?v=` alias for any of them.
        self.assertEqual(body.count("window.SANA_STATIC="), 1)
        self.assertIn(static_fingerprint.manifest_script(), body)
        self.assertLess(body.index("window.SANA_STATIC="), body.index("</head>"))
        for name in static_fingerprint.CLIENT_LOADED:
            self.assertIn(f'"{name}":"{static_fingerprint.url(name)}"', body)
            self.assertNotIn(f"/{name}?v=", body)
        # The server-rendered sidebar shows the entry, marked active and
        # pointing at the direct screen (not the legacy hash).
        self.assertIn('data-nav="solicitudes"', body)
        self.assertIn('href="/admin/solicitudes"', body)


class TestSolicitudesScreenModuleGating(unittest.TestCase):
    """solicitudes is a section of the TURNOS module (not a separately
    disableable leaf like intake/entrevistas). The screen router declares
    `Depends(require_module_enabled("turnos"))` at the ROUTER level, so
    disabling turnos 404s `/admin/solicitudes` WITHOUT unmounting the route
    — the mirror of TestReportesScreenModuleGating for this screen: the 404
    is indistinguishable from a path that never existed, it toggles live
    without remount, and the nav entry drops on its own."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_disabled_turnos_404s_the_screen_indistinguishably(self):
        self._set_enabled("intake")  # turnos NOT listed -> disabled
        r = self.client.get("/admin/solicitudes", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"detail": {"error": "no_encontrado"}})

    def test_toggling_turnos_flips_the_gate_without_remount(self):
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/solicitudes", cookies=_admin_cookies()).status_code, 404)
        self._set_enabled("turnos")
        self.assertEqual(
            self.client.get("/admin/solicitudes", cookies=_admin_cookies()).status_code, 200)
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/solicitudes", cookies=_admin_cookies()).status_code, 404)


class TestPreparacionesScreenRoleGate(unittest.TestCase):
    """'preparaciones' — seventh landed server-routed screen, entrevistas
    role-gate shape: no `roles` key → admin-only. Distinctive vs. cola
    (medico+admisionista render) and reportes (the `reportes` role
    renders): every live non-admin role 403s. The retired `marca` token
    is 401, not 403 — `_validar_payload` rejects it before any role
    branch (`test_admin_screen_contract` previously documented why no
    sibling had a marca case; this class pins that 401)."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/preparations", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_medico_session_403s(self):
        r = self.client.get("/admin/preparations", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 403)

    def test_admisionista_session_403s(self):
        r = self.client.get("/admin/preparations", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 403)

    def test_reportes_session_403s(self):
        r = self.client.get("/admin/preparations", cookies=_admin_cookies(rol="reportes"))
        self.assertEqual(r.status_code, 403)

    def test_retired_marca_token_redirects_like_no_session(self):
        r = self.client.get("/admin/preparations", cookies=_admin_cookies(rol="marca"), follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_admin_session_renders_the_full_contract(self):
        r = self.client.get("/admin/preparations", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        body = r.text
        self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn('id="sec-preparaciones"', body)
        self.assertIn('window.SANA_ADMIN_SCREEN = "preparaciones"', body)
        self.assertRegex(body, r'<html lang="[a-z-]+"')
        self.assertEqual(body.count('id="adm-h1"'), 1)
        self.assertIn('<h1 id="adm-h1"', body)
        self.assertIn("Cómo prepararse", body)
        cohort = ("platform/i18n.mjs", "platform/admin-preparations.mjs")
        urls = [static_fingerprint.url(name) for name in cohort]
        positions = []
        for name, url in zip(cohort, urls):
            self.assertIn(url, body, f"{name} must load via its fingerprinted URL")
            positions.append(body.index(url))
        self.assertEqual(positions, sorted(positions),
                         "cohort must load in platform/i18n.mjs → admin-preparations order")
        self.assertIn(static_fingerprint.url("platform/admin-table.mjs"), body)
        self.assertIn(static_fingerprint.url("platform/prep.mjs"), body)
        # Classic retirement: the root controller is deregistered, so
        # url() raises on it — assert the retirement independently instead.
        self.assertNotIn("admin-preparations.js",
                         static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        self.assertNotIn("admin-preparations.js", body)
        self.assertNotIn("admin-preparaciones", body)
        for retired in ("/admin-preparations.js", "/static/admin-preparations.js",
                        "/admin-preparaciones.js"):
            response = self.client.get(retired)
            self.assertEqual(response.status_code, 404, retired)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertIn('id="preparations-load-recovery"', body)
        self.assertIn(static_fingerprint.url("platform/screen-boot.mjs"), body)
        self.assertIn('bootScreen({', body)
        self.assertNotIn('setTimeout(failed, 8000)', body)
        self.assertNotIn("/prep.js", body)
        self.assertIn('data-nav="preparaciones"', body)
        self.assertIn('href="/admin/preparations"', body)
        self.assertNotIn("/format.js", body)


class TestPreparacionesScreenModuleGating(unittest.TestCase):
    """preparaciones is a section of the TURNOS module. The screen router
    declares `Depends(require_module_enabled("turnos"))` at the ROUTER
    level, so disabling turnos 404s `/admin/preparations` without
    unmounting the route. The nav entry is admin-only: the admin payload
    keeps it (now with its `screen` dict); medico's payload drops it."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_disabled_turnos_404s_the_screen_indistinguishably(self):
        self._set_enabled("intake")  # turnos NOT listed -> disabled
        r = self.client.get("/admin/preparations", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"detail": {"error": "no_encontrado"}})

    def test_enabled_turnos_renders(self):
        self._set_enabled("turnos")
        r = self.client.get("/admin/preparations", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sec-preparaciones"', r.text)

    def test_toggling_turnos_flips_the_gate_without_remount(self):
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/preparations", cookies=_admin_cookies()).status_code, 404)
        self._set_enabled("turnos")
        self.assertEqual(
            self.client.get("/admin/preparations", cookies=_admin_cookies()).status_code, 200)
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/preparations", cookies=_admin_cookies()).status_code, 404)

    def test_payload_keeps_the_screen_dict_for_admin_and_drops_it_for_medico(self):
        self._set_enabled("turnos")
        admin_sections = app_modules.sections_payload_for_role("admin")
        entry = next(
            (s for s in admin_sections
             if isinstance(s, dict) and s.get("id") == "preparaciones"),
            None)
        self.assertIsNotNone(entry)
        self.assertIsInstance(entry.get("screen"), dict)
        self.assertEqual(entry["screen"]["path"], "/admin/preparations")
        medico_sections = app_modules.sections_payload_for_role("medico")
        self.assertFalse(any(
            isinstance(s, dict) and s.get("id") == "preparaciones"
            for s in medico_sections))

    def test_disabling_turnos_drops_the_nav_entry_zero_central_edits(self):
        self._set_enabled("turnos")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertTrue(any(
            isinstance(s, dict) and s.get("id") == "preparaciones" for s in sections))
        self._set_enabled("intake")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertFalse(any(
            isinstance(s, dict) and s.get("id") == "preparaciones" for s in sections))


class TestKnowledgeScreenRoleGate(unittest.TestCase):
    """'knowledge' — eighth landed turnos-owned server-routed screen
    (ninth overall with intake's entrevistas): entrevistas role-gate
    shape, no `roles` key → admin-only. Distinctive vs. cola
    (medico+admisionista render) and reportes (the `reportes` role
    renders): every live non-admin role 403s. The retired `marca` token
    is 401, not 403 — `_validar_payload` rejects it before any role
    branch. English path `/admin/knowledge`; the legacy hash id stays
    `knowledge`."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos")
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        r = self.client.get("/admin/knowledge", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_medico_session_403s(self):
        r = self.client.get("/admin/knowledge", cookies=_admin_cookies(rol="medico"))
        self.assertEqual(r.status_code, 403)

    def test_admisionista_session_403s(self):
        r = self.client.get("/admin/knowledge", cookies=_admin_cookies(rol="admisionista"))
        self.assertEqual(r.status_code, 403)

    def test_reportes_session_403s(self):
        r = self.client.get("/admin/knowledge", cookies=_admin_cookies(rol="reportes"))
        self.assertEqual(r.status_code, 403)

    def test_retired_marca_token_redirects_like_no_session(self):
        r = self.client.get("/admin/knowledge", cookies=_admin_cookies(rol="marca"), follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_admin_session_renders_the_full_contract(self):
        r = self.client.get("/admin/knowledge", cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        body = r.text
        self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn('id="sec-knowledge"', body)
        self.assertIn('window.SANA_ADMIN_SCREEN = "knowledge"', body)
        self.assertRegex(body, r'<html lang="[a-z-]+"')
        self.assertEqual(body.count('id="adm-h1"'), 1)
        self.assertIn('<h1 id="adm-h1"', body)
        self.assertIn("Wiki", body)
        cohort = ("platform/i18n.mjs", "platform/admin-knowledge.mjs")
        urls = [static_fingerprint.url(name) for name in cohort]
        for name, url in zip(cohort, urls):
            self.assertIn(url, body, f"{name} must load via its fingerprinted URL")
        # Manifest key sorting is not execution order. The native module awaits
        # i18n itself; the actual loader must resolve the emitted manifest.
        manifest_position = body.index("window.SANA_STATIC=")
        loader_position = body.index('var url = window.SANA_STATIC && window.SANA_STATIC["platform/admin-knowledge.mjs"]')
        self.assertLess(manifest_position, loader_position)
        self.assertIn("import(url)", body[loader_position:])
        self.assertNotIn('src="/admin-knowledge.js"', body)
        self.assertIn('data-nav="knowledge"', body)
        self.assertIn('href="/admin/knowledge"', body)
        self.assertNotIn("/format.js", body)
        self.assertNotIn("location.hash =", body)

    def test_item_query_is_client_state_the_shell_still_renders(self):
        r = self.client.get("/admin/knowledge?item=demo-article",
                            cookies=_admin_cookies(rol="admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('window.SANA_ADMIN_SCREEN = "knowledge"', r.text)
        self.assertIn('id="sec-knowledge"', r.text)


class TestKnowledgeScreenModuleGating(unittest.TestCase):
    """knowledge is a section of the TURNOS module. The screen router
    declares `Depends(require_module_enabled("turnos"))` at the ROUTER
    level, so disabling turnos 404s `/admin/knowledge` without
    unmounting the route. The nav entry is admin-only: the admin payload
    keeps it (now with its `screen` dict); medico's payload drops it."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_disabled_turnos_404s_the_screen_indistinguishably(self):
        self._set_enabled("intake")  # turnos NOT listed -> disabled
        r = self.client.get("/admin/knowledge", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"detail": {"error": "no_encontrado"}})

    def test_enabled_turnos_renders(self):
        self._set_enabled("turnos")
        r = self.client.get("/admin/knowledge", cookies=_admin_cookies())
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sec-knowledge"', r.text)

    def test_toggling_turnos_flips_the_gate_without_remount(self):
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/knowledge", cookies=_admin_cookies()).status_code, 404)
        self._set_enabled("turnos")
        self.assertEqual(
            self.client.get("/admin/knowledge", cookies=_admin_cookies()).status_code, 200)
        self._set_enabled("intake")
        self.assertEqual(
            self.client.get("/admin/knowledge", cookies=_admin_cookies()).status_code, 404)

    def test_payload_keeps_the_screen_dict_for_admin_and_drops_it_for_medico(self):
        self._set_enabled("turnos")
        admin_sections = app_modules.sections_payload_for_role("admin")
        entry = next(
            (s for s in admin_sections
             if isinstance(s, dict) and s.get("id") == "knowledge"),
            None)
        self.assertIsNotNone(entry)
        self.assertIsInstance(entry.get("screen"), dict)
        self.assertEqual(entry["screen"]["path"], "/admin/knowledge")
        medico_sections = app_modules.sections_payload_for_role("medico")
        self.assertFalse(any(
            isinstance(s, dict) and s.get("id") == "knowledge"
            for s in medico_sections))

    def test_disabling_turnos_drops_the_nav_entry_zero_central_edits(self):
        self._set_enabled("turnos")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertTrue(any(
            isinstance(s, dict) and s.get("id") == "knowledge" for s in sections))
        self._set_enabled("intake")
        sections = app_modules.sections_payload_for_role("admin")
        self.assertFalse(any(
            isinstance(s, dict) and s.get("id") == "knowledge" for s in sections))


if __name__ == "__main__":
    unittest.main()


class TestScreenTemplatesNeverForceTheHash(unittest.TestCase):
    """Auditoría Tier 5 (2026-08-06): el piloto original hacía pasar el gate
    del renderer forzando `location.hash` desde la plantilla — un screen
    server-routed que aún DEPENDE del contrato de hash no es una migración,
    es un disfraz. El contrato ahora es al revés: base.html declara
    `window.SANA_ADMIN_SCREEN` y cada route() legacy lo lee sólo cuando no
    hay hash. Este test impide que el shim vuelva en la próxima pantalla
    copiada."""

    def _screen_templates(self):
        import glob
        pats = [
            os.path.join(ROOT, "templates", "admin", "*.html"),
            os.path.join(ROOT, "app", "modules", "*", "admin_*.html"),
        ]
        files = [f for pat in pats for f in glob.glob(pat)]
        self.assertTrue(files, "no encontré plantillas de screens — ¿se movieron?")
        return files

    def test_no_screen_template_assigns_location_hash(self):
        for f in self._screen_templates():
            with open(f, encoding="utf-8") as fh:
                body = fh.read()
            self.assertNotRegex(
                body, r"location\.hash\s*=",
                f"{os.path.relpath(f, ROOT)} fuerza location.hash — el shim "
                "del piloto está prohibido: usá window.SANA_ADMIN_SCREEN "
                "(base.html) y el fallback sin-hash del route() del renderer",
            )

    def test_base_declares_the_screen_global(self):
        with open(os.path.join(ROOT, "templates", "admin", "base.html"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("window.SANA_ADMIN_SCREEN", body)
