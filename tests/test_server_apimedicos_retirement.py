"""test_server_apimedicos_retirement.py — pins the server.py retirement slice
that deleted the last dispatch-dead block and its orphaned helper:

  - all_especialidad_slugs
  - the GET /api/medicos route block in Handler.do_GET

That handler was dead code whenever the turnos module is enabled (every
shipped configuration): `core.legacy_urls` — platform-core, FIRST real
handler in routing's GET registry — rewrites `/api/medicos` in place to
`/api/doctors` and returns False; `patient_web.patient_site` then claims
the rewritten path with `{"medicos": _doctors_enriched()}` and returns
True, so server.py's own do_GET route code never runs. The rewrite itself
STAYS — it is the live cached-Spanish-URL contract. The live
`/api/specialties` block and the `public_medicos()` data path now live in
`patient_web.availability_directory`, not in the legacy server.

Honesty about the one non-shipped configuration: if turnos is disabled,
legacy_urls still rewrites `self.path`, nobody claims `/api/doctors`,
dispatch() returns False, and do_GET then matches on the STALE
pre-dispatch local `path` (derived before dispatch()). Before this slice
that stale local served the block's tally JSON; after it, the same local
still starts with `/api/` so the generic `/api/` JSON 404 answers —
not the HTML 404 page. Same accepted trade as the cobertura and clinics
slices, in the API-appropriate shape.

Same shape as test_server_directorio_retirement.py: (1) STATIC — the
removed constructs and extracted live symbols stay out of server.py's
source and the live symbols stay in the owning patient-web module; (2)
RUNTIME — the names are gone from the legacy server module; (3)
UNREACHABLE — the real dispatch registry rewrites then claims the path
before any server.py route code; (4) DATA PATH — public_medicos() /
medicos_by_esp() / medico_by_url() / especialidades_tree() still work on
deterministic synthetic input.

Ported (native-migration final wave, docs/roadmap/
native-migration-campaign-20260826.md): `server.py` and the whole dark
dispatch chain (`core/routing.py`, `core/module_router.py`) are DELETED —
there is no `server` module left to import or grep, and no
`core.routing.dispatch()` chain to run a `_FakeHandler` through. What used
to be class (3) UNREACHABLE now asks a different, real question: since
`core/legacy_urls_router.py` only ported the PAGE-redirect family of
`core.legacy_urls.handle()` natively (its own docstring, and the campaign
doc's "Lifecycle decisions (W3b)" section, scope the API/feed rewrite
family — `_new_api_path()` — OUT of the native router; dark mode was that
family's only path), `/api/medicos` is no longer rewritten to
`/api/doctors` at all in the native-only topology — it just falls to the
terminal's generic `/api/` JSON 404, same as any other unclaimed API path.
This is an already-documented, deliberate scope trade-off from BEFORE the
final wave (not something this porting pass introduced or is papering
over) — verified empirically against the live composed app, not assumed.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Hermetic: never pull the install's local .env into the test process — set
# BEFORE any import so config loading sees no file (TURNOS_ENV_FILE=/dev/null).
import _env  # noqa: E402
_env.process_set(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")  # import/run without requiring Postgres
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
# the pre-launch gate (site_auth.py) only fires with BOTH set — off here so
# this file never depends on the local .env.
_env.process_set(TURNOS_BASIC_AUTH_USER=None, TURNOS_BASIC_AUTH_PASS=None)

from app.patient_web import availability_directory  # noqa: E402


class TestLiveApiMedicosSurfaceOwnedNatively(unittest.TestCase):
    """`server.py` itself is gone (test_asgi_main_dark_mode_retirement.py's
    `TestLegacyEntrypointRetired` pins that at the topology level), so the
    "absent from server.py's source" / `hasattr(server, ...)` checks this
    slice used to run are now moot by construction — there is no server
    module to check. What remains provable and worth pinning: the live
    symbols this slice extracted are owned by patient_web, importable, and
    reachable through a real route."""

    def test_live_symbols_owned_by_availability_directory(self):
        for name in (
            "public_medicos",
            "especialidades_tree",
            "medicos_by_esp",
            "medico_by_url",
        ):
            self.assertTrue(
                hasattr(availability_directory, name),
                msg=f"patient_web.availability_directory.{name} must own the live symbol",
            )

    def test_specialties_route_is_native(self):
        from app.patient_web import availability_directory_router
        self.assertTrue(hasattr(availability_directory_router, "specialties"))


class TestApiMedicosNativeBehavior(unittest.TestCase):
    """The explicit API alias rewrites into the sole native implementation."""

    @classmethod
    def setUpClass(cls):
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_module.app)

    def test_get_api_medicos_matches_the_native_api(self):
        alias = self.client.get("/api/medicos", follow_redirects=False)
        canonical = self.client.get("/api/doctors", follow_redirects=False)
        self.assertEqual(alias.status_code, 200)
        self.assertEqual(alias.json(), canonical.json())

    def test_api_doctors_is_the_live_native_route(self):
        from app.patient_web import site_listing
        synthetic = [{"nombre": "Dra. Ana Pérez"}]
        with patch.object(site_listing, "_doctors_enriched", return_value=synthetic):
            r = self.client.get("/api/doctors")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"medicos": synthetic})


class TestLiveDoctorsDataPath(unittest.TestCase):
    """The live doctor-listing data path — public_medicos() +
    medicos_by_esp() + medico_by_url() + especialidades_tree(), i.e. what
    /api/specialties, /datos and the /<esp-slug>/<name-slug> profile route
    consume — is intact. Synthetic _load_db, same pattern as the directorio
    test: no live server, no pack dependency."""

    _SYNTHETIC_DB = {
        "medicos": [
            {"nombre": "Dra. Ana Pérez", "especialidad": "Cardiología",
             "clinica": "Clínica X", "online": True,
             "obras_sociales": ["OSDE"]},
            {"nombre": "Dr. Beto López", "especialidad": "Clínica Médica",
             "clinica": "Clínica Y", "online": False,
             "obras_sociales": ["OSDE"]},
        ]
    }

    def setUp(self):
        patcher = patch.object(
            availability_directory,
            "_load_db",
            return_value=self._SYNTHETIC_DB,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_public_medicos_returns_two_rows(self):
        meds = availability_directory.public_medicos()
        self.assertEqual(len(meds), 2)

    def test_medicos_by_esp_finds_the_cardiologist(self):
        found = availability_directory.medicos_by_esp("cardiologia")
        self.assertTrue(found)
        self.assertTrue(any("Pérez" in m["nombre"] for m in found))

    def test_medico_by_url_resolves_each_row(self):
        for row in availability_directory.public_medicos():
            self.assertEqual(
                availability_directory.medico_by_url(
                    row["esp_slug"], row["slug"]
                ),
                row,
            )

    def test_especialidades_tree_builds(self):
        tree = availability_directory.especialidades_tree()
        self.assertIsInstance(tree, dict)
        self.assertIn("groups", tree)
        self.assertGreaterEqual(tree.get("total_medicos", 0), 2)


if __name__ == "__main__":
    unittest.main()
