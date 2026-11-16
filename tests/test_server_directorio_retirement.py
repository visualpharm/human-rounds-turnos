"""test_server_directorio_retirement.py — pins the server.py retirement slice
that deleted the dead private-directory dispatch surface:

  - medico_by_slug, _render_directorio, _render_dir_rows, _dir_cov_cell,
    _copago_chip, _turno_pill, _BILLING_SUFFIX_RE
  - the /medicos and /medico/<slug> route blocks in Handler.do_GET

Those handlers were dead code: `core.legacy_urls` — platform-core, FIRST real
handler in routing's GET registry and therefore never gated by module
enablement — answers every GET /medicos with a 301 to /doctors and every GET
/medico/<slug> with a 301 to /doctor/<slug> (the 2026-07-17 rename compat),
returning True ("already answered") long before server.py's late fallback
runs. The doctors directory itself is owned by patient_web/site_*.py (`/doctors`,
`/doctor/<id>`). `patient_web.availability_directory` owns the read-only data
projection used by `/api/specialties`; `patient_web.availability_pages` owns
the late live `/<specialty>[/<doctor>]` pages.

Same shape as test_server_cobertura_retirement.py (the round-1 slice):
(1) STATIC — the removed constructs stay out of server.py's source and the
    live symbols stay in; (2) RUNTIME — the names are gone from the module;
(3) UNREACHABLE — the real dispatch registry claims both paths before any
    server.py route code; (4) DATA PATH — public_medicos()/medico_by_url()
    still work on deterministic synthetic input.

Ported (native-migration final wave, docs/roadmap/
native-migration-campaign-20260826.md): `server.py` and the dark dispatch
chain (`core/routing.py`, `core/module_router.py`) are DELETED — there is
no `server` module to grep/import and no `routing.dispatch()` chain to run
a `_FakeHandler` through. `/medicos` and `/medico/<slug>` are the
PAGE-redirect family (`core.legacy_urls._PAGES_EXACT` /
`_PAGES_PREFIX`), which — unlike the API/feed family — DID get a native
router (`core/legacy_urls_router.py`, mounted unconditionally in
`routers/platform.py`'s `ORDERED` tuple). Verified empirically against the
live composed app: `GET /medicos` -> 301 /doctors and
`GET /medico/ana-perez` -> 301 /doctor/ana-perez both reproduce byte-for-
byte, same as dark mode.

Trailing-slash note: dark mode's `_new_page_path()` strips a trailing
slash before matching `_PAGES_EXACT` (`path.rstrip("/") or "/"`), so
`GET /medicos/` was ALSO a 301. The W3b native router originally
registered only the bare literals and `/medicos/` fell to the terminal's
static policy (403) — found during the final-wave test migration and
FIXED in `core/legacy_urls_router.py` by registering a trailing-slash twin
per literal (same `_resolve()` chain). `test_get_medicos_trailing_slash_
redirects_too` below pins the restored parity.
"""

import os
import pathlib
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

_DIRECTORY_SOURCE = pathlib.Path(ROOT, "app", "patient_web", "availability_directory.py").read_text(encoding="utf-8")
_PAGES_SOURCE = pathlib.Path(ROOT, "app", "patient_web", "availability_pages.py").read_text(encoding="utf-8")


class TestLiveDirectorioSurfaceOwnedNatively(unittest.TestCase):
    """`server.py` is gone entirely (test_asgi_main_dark_mode_retirement.py's
    `TestLegacyEntrypointRetired` pins the topology-level deletion), so the
    "removed constructs absent from server.py's source" / `hasattr(server,
    ...)` checks this slice used to run are now moot by construction. What
    remains provable: the live directory symbols this slice extracted stay
    owned by patient_web."""

    def test_live_directory_symbols_owned_by_patient_web(self):
        for needle in ("def public_medicos(", "def medico_by_url(",
                       "def _turno_friendly("):
            self.assertIn(needle, _DIRECTORY_SOURCE)
        self.assertIn("def _insurer_logo_tag(", _PAGES_SOURCE)

    def test_live_directory_symbols_importable(self):
        for name in ("public_medicos", "medico_by_url"):
            self.assertTrue(hasattr(availability_directory, name))


class TestDeadRoutesUnreachableNative(unittest.TestCase):
    """`core/legacy_urls_router.py` — mounted unconditionally, platform-core,
    in `routers/platform.py`'s `ORDERED` tuple — claims both exact paths
    with the SAME 301 targets the deleted dispatch-dead blocks would have
    raced against, including the trailing-slash twins (see the module
    docstring's trailing-slash note)."""

    @classmethod
    def setUpClass(cls):
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_module.app)

    def test_get_medicos_redirects_to_doctors(self):
        r = self.client.get("/medicos", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/doctors")

    def test_get_medicos_trailing_slash_redirects_too(self):
        # Dark mode rstripped before the _PAGES_EXACT lookup; the native
        # router's explicit trailing-slash twin restores that parity.
        r = self.client.get("/medicos/", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/doctors")

    def test_get_medico_slug_redirects_to_doctor_slug(self):
        r = self.client.get("/medico/ana-perez", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/doctor/ana-perez")

    def test_get_medico_slug_trailing_slash_redirects_too(self):
        # Unlike the bare /medicos/ case, a PREFIX route (/medico/{rest:path})
        # IS registered, so a trailing slash on the slug still resolves
        # through _new_page_path()'s own rstrip — no gap here.
        r = self.client.get("/medico/ana-perez/", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/doctor/ana-perez")

    def test_legacy_urls_router_mounted_platform_core_ungated(self):
        from app.core import legacy_urls_router
        from app.routers import platform
        self.assertIn(legacy_urls_router.router, platform.ORDERED)


class TestLiveDirectorioDataPath(unittest.TestCase):
    """The live doctor-listing data path — public_medicos() + medico_by_url(),
    i.e. what /api/specialties and the /<esp-slug>/<name-slug> profile route
    consume — is intact. Synthetic _load_db, same pattern as the round-1
    cobertura test: no live server, no pack dependency."""

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
        patcher = patch.object(availability_directory, "_load_db", return_value=self._SYNTHETIC_DB)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_public_medicos_enriches_synthetic_rows(self):
        meds = availability_directory.public_medicos()
        self.assertEqual(len(meds), 2)
        self.assertIn("doc_url", meds[0])
        for m in meds:
            # every row public_medicos() emits must resolve back through the
            # LIVE lookup of the /<esp-slug>/<name-slug> route (fresh dicts
            # per call — value equality, not identity)
            self.assertEqual(availability_directory.medico_by_url(m["esp_slug"], m["slug"]), m)

    def test_medico_by_url_rejects_unknown(self):
        self.assertIsNone(availability_directory.medico_by_url("cardiologia", "nadie"))


if __name__ == "__main__":
    unittest.main()
