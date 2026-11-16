"""Hermetic contracts for the extracted platform-core ``/datos`` page."""

import asyncio
import os
import pathlib
import sys
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env  # noqa: E402
_env.process_set(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.patient_web import health_data, health_data_router  # noqa: E402
from app.routers import platform as platform_routers  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402
from app import main as app_module  # noqa: E402
import snapshots  # noqa: E402


_CARTILLA = [
    {"esp": "Cardiología", "obsoc": [{"nombre": "IOMA"}, {"nombre": "Particulares"}]},
    {"esp": "Cardiología", "obsoc": [{"nombre": "IOMA"}, {"nombre": "OSDE"}]},
    {"esp": "Pediatría", "obsoc": [{"nombre": "OSDE"}, {"nombre": "--Todas--"}]},
]
_TREE = {
    "groups": [{"name": "Cardiología", "count": 3}, {"name": "Pediatría", "count": 1}],
    "con_online": 4,
    "total_medicos": 4,
}


def _request(path="/datos", query=b""):
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query,
        "headers": [],
        "client": ("127.0.0.1", 41000),
        "server": ("testserver", 80),
    })


def render_datos_page():
    """The synthetic /datos render both the test and tests/snapshots.py use."""
    with (
        _env.patched_env(
            {"TURNOS_PUBLIC_ORIGIN": "https://pinamar.humanrounds.org"},
        ),
        patch.object(health_data, "_load_cartilla", return_value=_CARTILLA),
        patch.object(health_data.availability_directory, "especialidades_tree", return_value=_TREE),
        patch.object(health_data.site_identity, "get", return_value="Pinamar"),
        patch.object(health_data.theme, "current", return_value="pinamar"),
        patch.object(health_data.theme, "brand_name", return_value="Pinamar Turnos"),
    ):
        return health_data.render_data_page()


class HealthDataTestCase(unittest.TestCase):
    def test_synthetic_render_matches_its_page_snapshot(self):
        # Markup and copy are pinned in tests/snapshots.json (python3
        # tests/snapshots.py --write after a deliberate change); the asset
        # fingerprints are checked against the files, not pinned.
        body = render_datos_page()
        snapshots.assert_page(self, "datos", body)
        native_nav = ("<script type='module' src='" +
                      health_data.static_fingerprint.url("platform/nav.mjs") +
                      "'></script>").encode()
        self.assertEqual(body.count(native_nav), 1)
        self.assertNotIn(b'<script src="/nav.js"></script>', body)
        self.assertEqual(body.count(b'<div class="datos-section">'), 3)

    def test_optional_cartilla_keeps_the_original_sibling_path_geometry(self):
        expected = os.path.normpath(
            os.path.join(ROOT, "..", "salud-pinamar", "cartilla-2026-05-14.json")
        )
        self.assertEqual(os.path.normpath(health_data._CARTILLA_PATH), expected)

    def test_native_router_is_platform_core_and_preserves_transport_headers(self):
        self.assertIn(health_data_router.router, platform_routers.ORDERED)
        paths = [route.path for route in health_data_router.router.routes]
        self.assertEqual(paths, ["/datos", "/datos/"])
        with (
            patch.object(health_data_router.i18n, "lang_switch_redirect", return_value=None),
            patch.object(health_data_router.setup, "is_fresh_install", return_value=False),
            patch.object(health_data, "render_data_page", return_value=b"<html>ok</html>"),
        ):
            response = asyncio.run(health_data_router.data_response(_request()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"<html>ok</html>")
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["content-length"], "15")
        self.assertNotIn("cache-control", response.headers)
        self.assertNotIn("content-encoding", response.headers)

    def test_native_route_preserves_the_fresh_install_redirect(self):
        with (
            patch.object(health_data_router.i18n, "lang_switch_redirect") as switch,
            patch.object(health_data_router.setup, "is_fresh_install", return_value=True),
        ):
            response = asyncio.run(health_data_router.data_response(_request()))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["location"], "/setup")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["content-length"], "0")
        switch.assert_not_called()

    def test_composed_app_claims_both_paths_without_cache_or_gzip(self):
        with (
            patch.object(health_data_router.setup, "is_fresh_install", return_value=False),
            patch.object(health_data, "render_data_page", return_value=b"<html>ok</html>"),
        ):
            client = TestClient(app_module.app)
            for path in ("/datos", "/datos/"):
                response = client.get(path, headers={"Accept-Encoding": "gzip"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"<html>ok</html>")
                self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
                self.assertEqual(response.headers["content-length"], "15")
                self.assertNotIn("cache-control", response.headers)
                self.assertNotIn("content-encoding", response.headers)

    def test_native_language_switch_redirects_after_setup_and_before_rendering(self):
        with (
            patch.object(
                health_data_router.i18n, "lang_switch_redirect",
                return_value=("/datos", "turnos_lang=en; Max-Age=1; Path=/; SameSite=Lax"),
            ),
            patch.object(health_data_router.setup, "is_fresh_install", return_value=False) as fresh,
            patch.object(health_data, "render_data_page") as render,
        ):
            response = asyncio.run(health_data_router.data_response(_request(query=b"lang=en")))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["location"], "/datos")
        self.assertEqual(
            response.headers["set-cookie"],
            "turnos_lang=en; Max-Age=1; Path=/; SameSite=Lax")
        self.assertEqual(response.headers["content-length"], "0")
        fresh.assert_called_once_with()
        render.assert_not_called()

    # test_server_no_longer_owns_the_page_or_dispatch_block retired with its
    # subject: server.py and routers/legacy_http_transport.py were deleted
    # outright in the native-migration final wave, which
    # test_asgi_main_dark_mode_retirement.TestLegacyEntrypointRetired pins.


if __name__ == "__main__":
    unittest.main()
