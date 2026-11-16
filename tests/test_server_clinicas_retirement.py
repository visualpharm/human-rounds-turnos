"""test_server_clinicas_retirement.py — pins the server.py retirement slice
that deleted the dead private-clinics page surface:

  - medicos_by_clinica, _render_clinica_page, _load_known_cities,
    _sentence_case, _clean_dir, _gen_clinica_desc, _render_clinicas_index,
    _FALLBACK_KNOWN_CITIES, _KNOWN_CITIES, and the clinics-index-only
    category constants (CLINICA_MIN_MEDICOS, CLINICA_EXCLUDE,
    CLINICA_FORCE_CHICA, CLINICA_CAT_LAB, CLINICA_CAT_OPTICA,
    CLINICA_CAT_DENTAL, CLINICA_CAT_PSY) — all of them fed only the two
    deleted renderers, with zero other consumers anywhere in the repo.
  - the /clinicas and /clinica/<slug> route blocks in Handler.do_GET

Those handlers were dead code in both transports: `patient_web.patient_site`
de-links `/clinicas`, `/clinicas/` and every `/clinica/<x>` to `/` at the very
top of handle()'s GET branch (legacy transport) and
`patient_web.patient_site_router` registers the same paths natively on the
ASGI router (native transport) — both answer 302 to `/` before any of
server.py's own do_GET route code runs. The live clinics DATA path
(`especialidades_tree()` -> /api/specialties, /datos; `all_clinicas()` -> the
offline Brila writer) is owned by functions that survive the slice.

Same shape as test_server_directorio_retirement.py (the round-3 slice):
(1) STATIC — the removed constructs stay out of server.py's source and the
    live symbols stay in; (2) RUNTIME — the names are gone from the module;
(3) DISPATCH, legacy transport — explicit 302 + Location; (4) DISPATCH,
native ASGI — explicit 302 + Location (the parity gap the ASGI-vs-legacy
route-parity script deliberately does not pin); (5) DATA PATH — the current
alias/count asymmetry between all_clinicas() (merges under the canonical
slug) and especialidades_tree()["clinicas"] (keyed by raw name, no merge) is
pinned as it behaves today, not "fixed".
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
_HEALTH_DATA_SOURCE = pathlib.Path(ROOT, "app", "patient_web", "health_data.py").read_text(encoding="utf-8")


class TestLiveClinicasSurfaceOwnedNatively(unittest.TestCase):
    """`server.py` is gone entirely (test_asgi_main_dark_mode_retirement.py's
    `TestLegacyEntrypointRetired` pins the topology-level deletion), so the
    "removed constructs absent from server.py's source" / `hasattr(server,
    ...)` checks this slice used to run are now moot by construction —
    there is no server module left to grep or import. What remains
    provable: the live data-path symbols this slice extracted stay owned
    by patient_web."""

    def test_live_data_path_symbols_owned_by_patient_web(self):
        for needle in (
            "def _load_clinicas(",
            "def _canonical_clinic_slug(",
            "def all_clinicas(",
            "def medicos_by_esp(",
            "def especialidades_tree(",
        ):
            self.assertIn(needle, _DIRECTORY_SOURCE)
        self.assertIn("def render_data_page(", _HEALTH_DATA_SOURCE)
        # (The `handler.path.split("?", 1)[0]` source pin for health_data.py
        # retired with its `handle()` — the native /datos path contract is
        # pinned by test_health_data.py's router tests.)

    def test_live_symbols_importable_from_availability_directory(self):
        for name in (
            "_load_clinicas", "_canonical_clinic_slug", "all_clinicas",
            "especialidades_tree",
        ):
            self.assertTrue(hasattr(availability_directory, name))


class TestDeadRoutesUnreachableAsgi(unittest.TestCase):
    """Native ASGI transport: patient_site_router registers /clinicas and
    /clinica/{rest} directly, so the same 302 answers them and the catch-all
    proxy never forwards to legacy server.py code. This is the standing
    native-ASGI pin that scripts/route_parity_check.py deliberately is not
    (it's a same-response diff auditor, not a status/Location pin)."""

    @classmethod
    def setUpClass(cls):
        from app.core import config
        config.ENV_PATH = ""  # never depend on the running checkout's real .env
        from app import main as app_module
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_module.app)

    def test_asgi_clinicas_redirects_home(self):
        r = self.client.get("/clinicas", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/")

    def test_asgi_clinicas_trailing_slash_redirects_home(self):
        r = self.client.get("/clinicas/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/")

    def test_asgi_clinica_slug_redirects_home(self):
        r = self.client.get("/clinica/cempi-centro-medico-pinamar", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/")


class TestLiveClinicasDataPath(unittest.TestCase):
    """The live clinics data path — all_clinicas() (the offline Brila writer)
    and especialidades_tree()["clinicas"] (/api/specialties, /datos) — is
    intact, and its current alias/count asymmetry is pinned exactly as it
    behaves today: all_clinicas() merges alias partners under the canonical
    slug; especialidades_tree() keys by the raw clinica name and does NOT
    merge. Deterministic synthetic input, no pack dependency (same pattern
    as test_server_directorio_retirement.py's TestLiveDirectorioDataPath)."""

    _SYNTHETIC_DB = {
        "medicos": [
            {"nombre": "Dra. Ana Pérez", "especialidad": "Cardiología",
             "clinica": "Cempi Centro Medico Pinamar", "online": True,
             "obras_sociales": ["OSDE"]},
            {"nombre": "Dr. Beto López", "especialidad": "Clínica Médica",
             "clinica": "CEMPI", "online": False,
             "obras_sociales": ["OSDE"]},
        ]
    }

    # "cempi" is an alias of the canonical "cempi-centro-medico-pinamar" slug.
    _SYNTHETIC_CLINICS = {
        "cempi-centro-medico-pinamar": {"name": "Cempi Centro Medico Pinamar",
                                          "google_rating": 4.5},
        "cempi": {"alias_of": "cempi-centro-medico-pinamar"},
    }

    def setUp(self):
        db_patcher = patch.object(availability_directory, "_load_db", return_value=self._SYNTHETIC_DB)
        db_patcher.start()
        self.addCleanup(db_patcher.stop)
        clin_patcher = patch.object(availability_directory, "_load_clinicas",
                                     return_value=self._SYNTHETIC_CLINICS)
        clin_patcher.start()
        self.addCleanup(clin_patcher.stop)

    def test_all_clinicas_merges_alias_partners_under_canonical_slug(self):
        # Doctors whose raw clinica name canonicalizes to the alias partner
        # DO merge into the canonical slug's count (the public_medicos() loop
        # calls _canonical_clinic_slug() before grouping).
        rows = availability_directory.all_clinicas()
        by_slug = {r["slug"]: r for r in rows}
        self.assertIn("cempi-centro-medico-pinamar", by_slug)
        self.assertEqual(by_slug["cempi-centro-medico-pinamar"]["count"], 2)
        # BUT the second loop (which back-fills clinics_db entries with no
        # doctors of their own) checks info.get("alias_de") to skip aliases —
        # a key that's never populated in real data (only "alias_of" is), so
        # every alias slug ALSO surfaces as its own zero-count row. Pinned as
        # current behavior, not "fixed" — see docs/roadmap/
        # server-retirement-slice4-clinicas.md §6.5.
        self.assertIn("cempi", by_slug)
        self.assertEqual(by_slug["cempi"]["count"], 0)

    def test_especialidades_tree_clinicas_keeps_alias_partners_separate(self):
        tree = availability_directory.especialidades_tree()
        clinicas = {c["name"]: c for c in tree["clinicas"]}
        self.assertIn("Cempi Centro Medico Pinamar", clinicas)
        self.assertIn("CEMPI", clinicas)
        self.assertEqual(clinicas["Cempi Centro Medico Pinamar"]["count"], 1)
        self.assertEqual(clinicas["CEMPI"]["count"], 1)
        self.assertNotIn("slug", clinicas["Cempi Centro Medico Pinamar"])


if __name__ == "__main__":
    unittest.main()
