"""test_legacy_urls_router_native.py — direct native route contract for
`core/legacy_urls_router.py` (native-migration campaign, family W3b; the
legacy-vs-native comparison this file used to run was retired with the
strict core/ops handle sweep, 2026-08-30 — `core.legacy_urls.handle()` is
deleted, so the router is the only transport and is pinned directly).

What this file pins, all through the composed app (`TestClient`, redirects
NOT followed):

  - the literal old-Spanish-page redirect table: each old path 301s to its
    new English page, preserving the query string (absorbed verbatim from
    the retired `test_legacy_urls.py`, which drove the deleted `handle()`
    through a fake handler);
  - the legacy JS-asset alias family: an old `<script src>` 301s to the
    CURRENT fingerprinted URL with `no-store`, and a structurally-broken
    alias is a no-store miss, never raw module source;
  - the former census representative probe (`GET /servicio/refactor-census`,
    the old dark-route check) is owned by this router, not the terminal;
  - the page family is GET-only — a POST to an old page path never 301s;
  - a matched-but-unrecognized prefix path (bare `/medico/`) falls back to
    the terminal's OWN 404 HTML body, not a generic FastAPI 404.

The API/feed rewrite family is exercised through the composed app's native
ASGI middleware as well as the canonical pure mapping function. Aliases keep
their method, query, headers, and body and reach the canonical endpoint
without redirecting.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_legacy_urls_router_native.py"
"""
import os
import sys
import unittest

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TEST_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(DATABASE_URL="")

from app.core import legacy_urls  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.patient_web import error_pages  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402


class LegacyUrlsRouterTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)


class TestCensusRepresentative(LegacyUrlsRouterTestCase):
    """The exact probe the retired architecture census used for
    `core.legacy_urls`."""

    def test_servicio_refactor_census_redirects_to_service(self):
        resp = self.client.get("/servicio/refactor-census", follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers["location"], "/service/refactor-census")

    def test_route_owner_is_not_the_terminal(self):
        resp = self.client.get("/servicio/refactor-census", follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        # A terminal-owned miss would be a 404 HTML page, never a redirect.
        self.assertNotEqual(resp.headers.get("content-type", ""), "text/html; charset=utf-8")


class TestPageFamilyRedirects(LegacyUrlsRouterTestCase):
    """Página vieja (GET) -> 301 a la nueva, preservando el query string."""

    def test_mis_solicitudes_redirects_preserving_query(self):
        resp = self.client.get("/mis-solicitudes?foo=bar", follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers["location"], "/my-requests?foo=bar")

    def test_medicos_redirects_to_doctors(self):
        resp = self.client.get("/medicos", follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers["location"], "/doctors")

    def test_trailing_slash_twin_redirects_the_same(self):
        # The legacy dispatcher reached the table for ANY path and
        # `_new_page_path()` rstrips before lookup, so `GET /medicos/`
        # 301'd exactly like `/medicos`; the router registers the slashed
        # twin explicitly to keep that true.
        resp = self.client.get("/medicos/", follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers["location"], "/doctors")

    def test_servicio_turno_redirects_to_service_book(self):
        resp = self.client.get(
            "/servicio/cardiologia-hospital/turno?slot=2026-07-20T10:00",
            follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers["location"],
                         "/service/cardiologia-hospital/book?slot=2026-07-20T10:00")

    def test_stz_ui_alias_stays_closed(self):
        for path, expected_status in (
            ("/stz-ui.js", 404),
            ("/stz-ui.js?v=1", 404),
            ("/stz-ui.js/", 404),
        ):
            with self.subTest(path=path):
                resp = self.client.get(path, follow_redirects=False)
                self.assertEqual(resp.status_code, expected_status)
                self.assertEqual(resp.headers.get("cache-control"), "no-store")
                self.assertNotIn("location", resp.headers)
                if expected_status == 404 and not path.endswith("/"):
                    self.assertEqual(resp.content, b'{"error": "not found"}')

    def test_retired_formato_alias_stays_closed(self):
        resp = self.client.get("/formato.js", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)

    def test_rubricador_alias_stays_closed(self):
        for path, expected_status in (
            ("/rubricador.js", 404),
            ("/rubricador.js?v=1", 404),
            ("/rubricador.js/", 404),
        ):
            with self.subTest(path=path):
                resp = self.client.get(path, follow_redirects=False)
                self.assertEqual(resp.status_code, expected_status)
                self.assertEqual(resp.headers.get("cache-control"), "no-store")
                self.assertNotIn("location", resp.headers)
                if expected_status == 404 and not path.endswith("/"):
                    self.assertEqual(resp.content, b'{"error": "not found"}')

    def test_post_to_a_page_path_does_not_redirect(self):
        # The page family is GET-only (the router registers methods=["GET"]
        # only); a POST to an old page path is not a 301.
        resp = self.client.post("/cuenta", follow_redirects=False)
        self.assertNotEqual(resp.status_code, 301)

    def test_current_page_is_not_redirected(self):
        resp = self.client.get("/doctors", follow_redirects=False)
        self.assertNotEqual(resp.status_code, 301)


class TestJsAssetRedirects(LegacyUrlsRouterTestCase):
    """JS renombrados a inglés (2026-07-29) — un <script src> viejo, servido
    desde un admin.html cacheado, sigue el 301 al hash ACTUAL en vez de dar
    404 hasta que el cache expira; la respuesta nunca se cachea."""

    def test_retired_classic_queue_urls_are_no_store_misses(self):
        for path in ("/admin-queue.js", "/admin-cola.js", "/admin-cola.js?v=31",
                     "/admin-cola.js/", "/static/admin-queue.deadbeef.js"):
            with self.subTest(path=path):
                resp = self.client.get(path, follow_redirects=False)
                self.assertEqual(resp.status_code, 404)
                self.assertNotIn("location", resp.headers)
                self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_admin_tabla_redirects_to_current_platform_fingerprint_no_store(self):
        resp = self.client.get("/admin-tabla.js", follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers["location"],
                         static_fingerprint.url("platform/admin-table.mjs"))
        self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_intake_raw_aliases_redirect_to_declared_fingerprints(self):
        for path, name in (
            ("/entrevista.js", "modules/intake/entrevista.js"),
            ("/entrevista.css", "modules/intake/entrevista.css"),
            ("/modules/intake/entrevista.js", "modules/intake/entrevista.js"),
            ("/modules/intake/entrevista.css", "modules/intake/entrevista.css"),
            ("/modules/intake/admin-entrevistas.mjs", "modules/intake/admin-entrevistas.mjs"),
            ("/static/modules/turnos/wz-upload.mjs", "modules/turnos/wz-upload.mjs"),
            ("/static/modules/turnos/service.mjs", "modules/turnos/service.mjs"),
            ("/static/modules/turnos/upload-order.mjs", "modules/turnos/upload-order.mjs"),
            ("/modules/turnos/wz-upload.mjs", "modules/turnos/wz-upload.mjs"),
            ("/modules/turnos/service.mjs", "modules/turnos/service.mjs"),
            ("/modules/turnos/upload-order.mjs", "modules/turnos/upload-order.mjs"),
        ):
            with self.subTest(path=path):
                resp = self.client.get(path + "?stale=1", follow_redirects=False)
                self.assertEqual(resp.status_code, 301)
                self.assertEqual(resp.headers["location"],
                                 static_fingerprint.module_url(name))
                self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_native_upload_asset_slash_twins_never_serve_source(self):
        # The literal router registers slash twins, but asset aliases are
        # exact: only the six unslashed URLs above redirect to immutable assets.
        for prefix in ("/modules/turnos/", "/static/modules/turnos/"):
            for name in ("service", "upload-order", "wz-upload"):
                with self.subTest(prefix=prefix, name=name):
                    response = self.client.get(
                        prefix + name + ".mjs/", follow_redirects=False)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertNotIn("location", response.headers)

    def test_retired_entrevistas_classic_aliases_are_closed(self):
        for path in ("/admin-entrevistas.js", "/modules/intake/admin-entrevistas.js"):
            # A basename is absent; a slash-shaped script path is denied
            # by the static policy. Neither may redirect to a classic twin
            # or expose source, even to a client holding a stale URL.
            for suffix, expected_status in (("", 404), ("/", 404)):
                with self.subTest(path=path + suffix):
                    resp = self.client.get(path + suffix, follow_redirects=False)
                    self.assertEqual(resp.status_code, expected_status)
                    self.assertEqual(resp.headers.get("cache-control"), "no-store")
                    self.assertNotIn("location", resp.headers)

    def test_retired_create_appointment_classic_aliases_are_closed(self):
        # The native overlay is reachable solely through the shell manifest.
        # A cached shell cannot quietly acquire the replacement controller
        # through either historical root URL.
        for path in ("/admin-crear-turno.js", "/admin-create-appointment.js"):
            for suffix, expected_status in (("", 404), ("/", 404)):
                with self.subTest(path=path + suffix):
                    resp = self.client.get(path + suffix, follow_redirects=False)
                    self.assertEqual(resp.status_code, expected_status)
                    self.assertEqual(resp.headers.get("cache-control"), "no-store")
                    self.assertNotIn("location", resp.headers)

    def test_retired_editorial_classic_aliases_are_closed(self):
        # The Spanish Knowledge alias was the only explicitly registered
        # editorial route pair. All old English roots are retired too.
        for path in ("/admin-conocimiento.js", "/admin-knowledge.js",
                     "/admin-prompts.js", "/admin-legal.js"):
            for suffix, expected_status in (("", 404), ("?v=1", 404), ("/", 404)):
                with self.subTest(path=path + suffix):
                    resp = self.client.get(path + suffix, follow_redirects=False)
                    self.assertEqual(resp.status_code, expected_status)
                    self.assertEqual(resp.headers.get("cache-control"), "no-store")
                    self.assertNotIn("location", resp.headers)

    def test_broken_intake_alias_is_a_no_store_miss_not_raw_module_source(self):
        # A structurally-invalid intake manifest cannot turn a reserved raw
        # alias into generic static source; compatibility owns the miss.
        from unittest.mock import patch
        with patch.object(static_fingerprint, "_MODULE_ASSETS_CACHE", frozenset()):
            resp = self.client.get("/modules/intake/entrevista.js",
                                   follow_redirects=False)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers.get("cache-control"), "no-store")
        self.assertEqual(resp.headers.get("content-length"), "0")


class TestPrefixFallback(LegacyUrlsRouterTestCase):
    def test_bare_medico_prefix_falls_back_to_terminal_body_not_a_generic_404(self):
        # Unlike /servicio/ (its own rewriter accepts an empty id and still
        # produces "/service/"), a bare /medico/ has no id for
        # `_PAGES_PREFIX` to match against `_new_page_path()` returns None
        # here — a matched-but-unrecognized old path gets the site's real
        # error page, byte-identical to the terminal's own 404.
        self.assertIsNone(legacy_urls._new_page_path("/medico/"))
        resp = self.client.get("/medico/", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)
        try:
            expected = error_pages.not_found_body()
        except Exception:  # noqa: BLE001
            expected = b"404 - pagina no encontrada"
        self.assertEqual(resp.content, expected)
        self.assertEqual(resp.headers.get("cache-control"), "no-store")


class TestLegacyApiAndFeedMapping(unittest.TestCase):
    """Coverage absorbed from the deleted handler-driven test file.

    These are pure mapping contracts, not a fabricated request dispatcher;
    the middleware transport assertions below prove the same mappings reach
    the canonical native endpoints.
    """

    def test_post_solicitudes_maps_to_requests(self):
        self.assertEqual(
            legacy_urls._new_api_path("/api/solicitudes"),
            "/api/requests",
        )

    def test_admin_solicitudes_aprobar_maps_the_verb(self):
        self.assertEqual(
            legacy_urls._new_api_path("/api/admin/solicitudes/5/aprobar"),
            "/api/admin/requests/5/approve",
        )

    def test_cuenta_dependientes_eliminar_maps_the_compound_path(self):
        self.assertEqual(
            legacy_urls._new_api_path("/api/cuenta/dependientes/eliminar"),
            "/api/account/dependents/delete",
        )

    def test_admin_contenido_translates_the_entity_type(self):
        self.assertEqual(
            legacy_urls._new_api_path("/api/admin/contenido/medicos/5"),
            "/api/admin/content/doctors/5",
        )

    def test_admin_my_appointments_mapping_leaves_query_to_transport(self):
        self.assertEqual(
            legacy_urls._new_api_path("/api/admin/mis-turnos"),
            "/api/admin/my-appointments",
        )

    def test_current_and_unrelated_paths_are_not_mapped(self):
        self.assertIsNone(legacy_urls._new_api_path("/api/requests"))
        self.assertIsNone(legacy_urls._new_api_path("/api/puco"))

    def test_appointment_feed_mapping(self):
        self.assertEqual(
            legacy_urls._new_api_path("/feed/turnos/abc123.ics"),
            "/feed/appointments/abc123.ics",
        )

    def test_doctor_feed_mapping(self):
        self.assertEqual(
            legacy_urls._new_api_path("/feed/medico/tok.ics"),
            "/feed/doctor/tok.ics",
        )


class TestLegacyApiAndFeedNativeTransport(LegacyUrlsRouterTestCase):
    def test_rewrite_preserves_method_query_headers_and_body(self):
        from app.core.legacy_urls_middleware import LegacyUrlRewriteMiddleware
        from fastapi import FastAPI, Request

        probe = FastAPI()
        probe.add_middleware(LegacyUrlRewriteMiddleware)

        @probe.api_route("/{path:path}", methods=["POST"])
        async def echo(path: str, request: Request):
            return {
                "path": "/" + path,
                "method": request.method,
                "query": request.url.query,
                "body": (await request.body()).decode("ascii"),
                "marker": request.headers.get("X-Legacy-Marker"),
            }

        with TestClient(probe) as client:
            resp = client.post(
                "/api/admin/solicitudes/5/aprobar?dias=14",
                content=b"payload",
                headers={"X-Legacy-Marker": "kept"},
            )
        self.assertEqual(
            resp.json(),
            {
                "path": "/api/admin/requests/5/approve",
                "method": "POST",
                "query": "dias=14",
                "body": "payload",
                "marker": "kept",
            },
        )

    def test_api_alias_reaches_canonical_endpoint_without_redirect(self):
        from app.modules.turnos import booking_requests as bkr
        from unittest.mock import patch

        with patch.object(bkr, "_get_user", return_value=None):
            resp = self.client.post(
                "/api/solicitudes?source=legacy",
                json={"anything": True},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "auth_requerida"})
        self.assertNotIn("location", resp.headers)

    def test_feed_alias_reaches_canonical_endpoint_without_redirect(self):
        from app.modules.turnos import calendar_feed
        from unittest.mock import patch

        with patch.object(
            calendar_feed,
            "resolve_feed_ics",
            return_value=("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "turnos.ics"),
        ) as resolve:
            resp = self.client.get("/feed/turnos/abc123.ics", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/calendar", resp.headers["content-type"])
        self.assertNotIn("location", resp.headers)
        resolve.assert_called_once_with("abc123")


if __name__ == "__main__":
    unittest.main()
