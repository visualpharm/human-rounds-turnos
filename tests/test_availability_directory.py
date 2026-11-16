"""Focused hermetic contracts for the extracted availability directory."""

import gzip
import json
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

from starlette.requests import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import gzip_bridge  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.patient_web import availability_directory  # noqa: E402
from app.patient_web import availability_directory_router  # noqa: E402


def _snapshot(count=24):
    return {"medicos": [
        {
            "nombre": f"Dra. Persona {index:02d}",
            "especialidad": "Cardiología" if index % 2 else "Pediatría",
            "clinica": "Clínica A" if index % 3 else "Clínica B",
            "online": bool(index % 2),
            "obras_sociales": ["OSDE", "Swiss Medical"] if index % 2 else ["OSDE"],
            "proximo_turno": "2026-09-02 10:00",
        }
        for index in range(count)
    ]}


def _request(accept_encoding="", query=b""):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/api/specialties",
        "raw_path": b"/api/specialties", "query_string": query,
        "headers": [(b"accept-encoding", accept_encoding.encode())],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    })


class TestAvailabilityDirectoryTransportParity(unittest.TestCase):
    def setUp(self):
        self.db = patch.object(availability_directory, "_load_db", return_value=_snapshot())
        self.clinics = patch.object(availability_directory, "_load_clinicas", return_value={})
        self.db.start()
        self.clinics.start()
        self.addCleanup(self.db.stop)
        self.addCleanup(self.clinics.stop)

    def test_native_response_keeps_json_cache_and_gzip_contract(self):
        # The retired legacy transport's own contract (no-store JSON,
        # gzip-if-accepted with Vary) — now pinned on the native endpoint
        # itself, the only transport left.
        with patch.object(gzip_bridge, "_MIN_SIZE", 1):
            native = availability_directory_router.specialties_response(_request("gzip"))

        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(native.headers["cache-control"], "no-store")
        self.assertEqual(native.headers["content-encoding"], "gzip")
        self.assertEqual(native.headers["vary"], "Accept-Encoding")
        self.assertEqual(
            json.loads(gzip.decompress(native.body)),
            availability_directory.especialidades_tree(),
        )

    def test_canonical_query_reaches_the_endpoint_directly(self):
        # `?cache=old` on the canonical path is ignored, never a mismatch.
        native = availability_directory_router.specialties_response(
            _request(query=b"cache=old"))
        self.assertEqual(native.status_code, 200)

    def test_explicit_spanish_compatibility_alias_reaches_the_native_endpoint(self):
        # API compatibility aliases are rewrite-and-continue middleware, not
        # parallel route implementations. Both paths must therefore return the
        # same native payload while only the English path owns the endpoint.
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module
        client = TestClient(app_module.app)
        alias = client.get("/api/especialidades", follow_redirects=False)
        canonical = client.get("/api/specialties", follow_redirects=False)
        self.assertEqual(alias.status_code, 200)
        self.assertEqual(alias.json(), canonical.json())

    def test_asgi_canonical_path_serves_the_tree_natively(self):
        # Was "not a legacy proxy" (a tripwire on the retired legacy
        # handler); with that transport deleted the meaningful contract is
        # that the composed app serves the canonical path straight from the
        # domain function.
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module

        with patch.object(availability_directory, "especialidades_tree", return_value={"groups": []}):
            response = TestClient(app_module.app).get("/api/specialties")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"groups": []})


class TestAvailabilityDirectoryComposition(unittest.TestCase):
    # test_legacy_route_is_unique_and_keeps_patient_site_precedence RETIRED
    # (native-migration final wave): `core/routing.py` (routing.GET) is
    # deleted, and the "precedence" concept it pinned doesn't transfer —
    # dark mode's linear claimant chain let one module's handle() shadow
    # another's on an OVERLAPPING/wildcard path, which is why relative
    # index order mattered; a real FastAPI route table matches by exact
    # path, so no route can shadow patient_web/site_*.py's distinct literal paths
    # the way a handle()-chain claimant could. The uniqueness half of this
    # test (patient_web.availability_directory claims /api/specialties
    # exactly once) is already pinned below by
    # test_native_route_is_unique_and_module_gated — nothing here is lost.

    def test_native_route_is_unique_and_module_gated(self):
        routers = app_modules.merge_app_routers()
        route_paths = [route.path for _, router in routers for route in router.routes]
        self.assertEqual(route_paths.count("/api/specialties"), 1)
        self.assertTrue(app_modules.route_enabled("app.patient_web.availability_directory"))

    def test_directory_does_not_take_the_editable_catalog_or_datos_page(self):
        # server_source dropped (native-migration final wave): server.py no
        # longer exists (test_asgi_main_dark_mode_retirement.py's
        # TestLegacyEntrypointRetired pins the deletion), so it trivially
        # cannot contain 'path == "/datos"' anymore — nothing left to check.
        source = (ROOT / "app" / "patient_web" / "availability_directory.py").read_text(encoding="utf-8")
        health_source = (ROOT / "app" / "patient_web" / "health_data.py").read_text(encoding="utf-8")
        self.assertNotIn("patient_web import catalog", source)
        self.assertNotIn('path == "/datos"', source)
        self.assertIn("def render_data_page(", health_source)


if __name__ == "__main__":
    unittest.main()
