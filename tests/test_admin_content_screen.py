"""Contracts for the turnos-owned ``/admin/services``, ``/admin/centers`` and
``/admin/doctors`` direct screens (admin_panel/admin_content_screen.py).

One shared module and template, THREE distinct one-route routers (the loader
mounts whatever object a manifest ``screen.router_attr`` names, without
deduplicating), admin-only under the turnos gate, no-store, pure shell —
the render never reads or echoes catalog data.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_content_screen.py"
"""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults({"TURNOS_ADMIN_SECRET": "admin-content-screen-test-secret"})

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.auth import admin_auth  # noqa: E402
from app import main as app_module  # noqa: E402
from app.admin_panel import admin_content_screen  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import config  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.admin_panel import admin_content  # noqa: E402

config.ENV_PATH = ""

SCREENS = (
    # (path, manifest id, section container)
    ("/admin/services", "servicios"),
    ("/admin/centers", "centros"),
    ("/admin/doctors", "medicos"),
)

BASE_COHORT = ("platform/i18n.mjs", "admin-shell.js", "platform/admin-alerts.mjs")
SHARED_COHORT = BASE_COHORT + ("platform/admin-control-fit.mjs", "platform/admin-content.mjs")
DOCTOR_ONLY = ("islands/MedCal.mjs",
               "vendor/preact.module.js")
DOCTOR_ORDER = BASE_COHORT + ("platform/admin-control-fit.mjs",
                "islands/MedCal.mjs",
                "platform/admin-content.mjs")
SERVICE_ORDER = BASE_COHORT + ("platform/admin-control-fit.mjs",
                "platform/admin-content.mjs")


def _cookies(role="admin"):
    return {"st_admin": admin_auth._mint_token("content-screen-test", role)}


class TestAdminContentScreens(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_three_distinct_one_route_routers_share_one_module(self):
        routers = [admin_content_screen.services_screen_router,
                   admin_content_screen.centers_screen_router,
                   admin_content_screen.doctors_screen_router]
        self.assertEqual(len({id(r) for r in routers}), 3)
        for router, (path, _sid) in zip(routers, SCREENS):
            with self.subTest(path=path):
                self.assertIsInstance(router, APIRouter)
                self.assertEqual([r.path for r in router.routes], [path])
                self.assertEqual(router.routes[0].methods, {"GET"})
        manifest = next(m for m in app_modules.list_modules() if m["id"] == "turnos")
        declared = {s["id"]: s.get("screen") for s in manifest["admin_sections"] if isinstance(s, dict)}
        for path, sid in SCREENS:
            with self.subTest(sid=sid):
                self.assertEqual(declared[sid]["path"], path)
                self.assertEqual(declared[sid]["module"], "app.admin_panel.admin_content_screen")
        ids = [s["id"] for s in manifest["admin_sections"]]
        for alias in ("services", "centers", "doctors"):
            self.assertNotIn(alias, ids, "English alias must not duplicate the sidebar entry")
        self.assertEqual(ids.count("servicios") + ids.count("centros") + ids.count("medicos"), 3)

    def test_shell_is_no_store_html_with_the_shared_cohort_and_canonical_hooks(self):
        for path, sid in SCREENS:
            with self.subTest(path=path):
                response = self.client.get(path, cookies=_cookies())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertIn('id="sec-%s"' % sid, response.text)
                self.assertIn('id="content-status"', response.text)
                self.assertNotIn('/static/admin-content.', response.text)
                self.assertIn('mount: (Content) => Content.mount(document)', response.text)
                self.assertIn(static_fingerprint.url("platform/screen-boot.mjs"), response.text)
                self.assertNotIn('id="toast"', response.text)
                self.assertIn(static_fingerprint.url("admin-shell.css"), response.text)
                self.assertIn('window.SANA_ADMIN_SCREEN = "%s"' % sid, response.text)
                self.assertIn('window.CURRENT_ROLE = "admin"', response.text)
                self.assertIn(static_fingerprint.url("platform/hours.mjs"), response.text)
                self.assertIn('href="%s" data-nav="%s" class="on"' % (path, sid), response.text)
                self.assertNotIn(static_fingerprint.url("admin-app.js"), response.text)
                self.assertNotIn("location.hash", response.text.split('id="adm-nav"')[1].split("<script")[0])
                offsets = [response.text.index(static_fingerprint.url(a)) for a in SHARED_COHORT]
                self.assertEqual(offsets, sorted(offsets), "shared cohort must keep admin.html's order")

    def test_doctor_only_payload_ships_on_doctors_alone_in_dependency_order(self):
        doctors = self.client.get("/admin/doctors", cookies=_cookies()).text
        offsets = [doctors.index(static_fingerprint.url(a)) for a in DOCTOR_ORDER]
        self.assertEqual(offsets, sorted(offsets), "doctors cohort must keep admin.html's order")
        self.assertIn('<script type="importmap">', doctors)
        self.assertIn('"sana/calendar-grid": "%s"' % static_fingerprint.url("platform/calendar-grid.mjs"), doctors)
        self.assertIn('window.SANA_ISLAND_URLS={"MedCal":"%s"}' % static_fingerprint.url("islands/MedCal.mjs"), doctors)
        self.assertIn(static_fingerprint.url("platform/islands.mjs"), doctors)
        self.assertNotIn("SanaIslands", doctors)
        for path in ("/admin/services", "/admin/centers"):
            body = self.client.get(path, cookies=_cookies()).text
            for asset in DOCTOR_ONLY:
                with self.subTest(path=path, asset=asset):
                    self.assertNotIn(static_fingerprint.url(asset), body)
            self.assertNotIn("importmap", body)
            self.assertNotIn("SANA_ISLAND_URLS", body)

    def test_capacity_editor_is_a_manifested_native_dependency_without_a_classic_tag(self):
        for path, cohort in (("/admin/services", SERVICE_ORDER),
                             ("/admin/doctors", DOCTOR_ORDER)):
            with self.subTest(path=path):
                body = self.client.get(path, cookies=_cookies()).text
                offsets = [body.index(static_fingerprint.url(a)) for a in cohort]
                self.assertEqual(offsets, sorted(offsets))
                self.assertIn(static_fingerprint.url("platform/admin-hours-editor.mjs"), body)
                self.assertNotIn("admin-hours-editor.js", body)
                self.assertIn(static_fingerprint.url("platform/admin-users.mjs"), body)
                self.assertNotIn("admin-users.js", body)
                self.assertIn(static_fingerprint.url("platform/admin-create-appointment.mjs"), body)
                self.assertNotIn("admin-create-appointment.js", body)

    def test_every_served_asset_is_content_hash_fingerprinted(self):
        for path, _sid in SCREENS:
            body = self.client.get(path, cookies=_cookies()).text
            cohort = DOCTOR_ORDER if path == "/admin/doctors" else SERVICE_ORDER if path == "/admin/services" else SHARED_COHORT
            for asset in cohort:
                with self.subTest(path=path, asset=asset):
                    self.assertIn(static_fingerprint.url(asset), body)
                    self.assertNotIn('src="/%s"' % asset, body)
            # first-party JS/CSS never carries the manual `?v=` ritual (the
            # favicon PNGs are platform-core and stay out of this contract)
            self.assertNotIn(".js?v=", body)
            self.assertNotIn(".css?v=", body)

    def test_role_matrix_doctor_editor_allows_self_service_only(self):
        for path, _sid in SCREENS:
            with self.subTest(path=path):
                r = self.client.get(path, follow_redirects=False)
                self.assertEqual(r.status_code, 303)
                self.assertEqual(r.headers["location"], "/admin/login?next=" + path.replace("/", "%2F"))
                self.assertEqual(self.client.get(path, cookies=_cookies()).status_code, 200)
                for role in ("medico", "admisionista", "reportes"):
                    self.assertEqual(self.client.get(path, cookies=_cookies(role)).status_code,
                                     200 if path == "/admin/doctors" and role == "medico" else 403, role)

    def test_item_query_state_never_reads_or_echoes_catalog_data(self):
        with patch.object(
            admin_content,
            "list_payload",
            side_effect=AssertionError("shell must not read catalog"),
        ):
            for path, sid in SCREENS:
                for item in ("new", "some-item-id-123"):
                    with self.subTest(path=path, item=item):
                        response = self.client.get(path + "?item=" + item, cookies=_cookies())
                        self.assertEqual(response.status_code, 200)
                        self.assertIn('id="sec-%s"' % sid, response.text)
                        self.assertNotIn("some-item-id-123", response.text)
                        self.assertNotIn("/api/admin/content", response.text)

    def test_english_shell_carries_the_translated_title_and_lang(self):
        cookies = {**_cookies(), "lang": "en"}
        with patch.object(i18n, "enabled_languages", return_value=["es", "en"]):
            for path, title in (("/admin/services", "Services"), ("/admin/centers", "Centers"),
                                ("/admin/doctors", "Doctors")):
                with self.subTest(path=path):
                    response = self.client.get(path, cookies=cookies)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('<html lang="en"', response.text)
                    self.assertIn('"active": "en"', response.text)
                    self.assertIn('id="adm-h1" class="adm-h1">%s</h1>' % title, response.text)
        es = self.client.get("/admin/doctors", cookies=_cookies())
        self.assertIn('id="adm-h1" class="adm-h1">Médicos</h1>', es.text)

    def test_module_gate_flips_live_and_nav_uses_the_shared_source(self):
        state = {"ENABLED_MODULES": "turnos"}
        with patch.object(config, "_db_overrides", side_effect=lambda: dict(state)):
            for path, _sid in SCREENS:
                with self.subTest(path=path):
                    state["ENABLED_MODULES"] = "turnos"
                    config._reset_cache_para_tests()
                    self.assertEqual(self.client.get(path, cookies=_cookies()).status_code, 200)
                    state["ENABLED_MODULES"] = "intake"
                    config._reset_cache_para_tests()
                    self.assertEqual(self.client.get(path, cookies=_cookies()).status_code, 404)
                    state["ENABLED_MODULES"] = "turnos"
                    config._reset_cache_para_tests()
                    self.assertEqual(self.client.get(path, cookies=_cookies()).status_code, 200)

        sentinel = [{"id": "proof", "href": "/admin/proof", "label": "Proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            for path, _sid in SCREENS:
                response = self.client.get(path, cookies=_cookies())
                self.assertIn('data-nav="proof"', response.text)
                self.assertNotIn('data-nav="cuenta"', response.text)

    def test_real_sidebar_lists_each_editor_once_with_its_direct_path(self):
        body = self.client.get("/admin/services", cookies=_cookies()).text
        nav = body.split('id="adm-nav"')[1].split("</nav>")[0]
        for path, sid in SCREENS:
            with self.subTest(path=path):
                self.assertEqual(nav.count('data-nav="%s"' % sid), 1)
                self.assertIn('href="%s" data-nav="%s"' % (path, sid), nav)
        for alias in ("services", "centers", "doctors"):
            self.assertNotIn('data-nav="%s"' % alias, nav)
        self.assertNotIn("/admin#servicios", nav)


if __name__ == "__main__":
    unittest.main()
