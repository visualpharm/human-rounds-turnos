"""test_server_cobertura_retirement.py — pins the narrow server.py retirement
slice that deleted the proven-dead coverage surface:

  - medicos_by_cobertura, _render_cobertura_page, _render_obras_index
  - the /cobertura/<slug> and /obras-sociales route blocks

Those handlers were dead code: `patient_web/site_*.py.handle()` de-links both paths
(302 -> /) before server.py's inherited routes ever run, so they were
unreachable on the live site (the dead-code finding is recorded in
docs/architecture/data-packs.md). This file has exactly two jobs and nothing
else — no live server, no HTTP:

  (1) STATIC: the three definitions and the two route dispatch lines are
      literally absent from server.py's source, while `all_coberturas` — the
      one LIVE coverage symbol, which still feeds `/api/specialties` through
      `especialidades_tree` — is still defined.
  (2) DATA PATH: `all_coberturas()` and `especialidades_tree()` still return
      the coberturas list, and `especialidades_tree` owns it
      (`tree["coberturas"] == all_coberturas()`) — so the live
      `/api/specialties` coverage behavior stays owned and reachable through
      the very function that serves it.
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


class TestLiveCoverageSurfaceOwnedNatively(unittest.TestCase):
    """`server.py` is gone entirely (test_asgi_main_dark_mode_retirement.py's
    `TestLegacyEntrypointRetired` pins the topology-level deletion), so the
    "removed constructs absent from server.py's source" / `hasattr(server,
    ...)` checks this slice used to run are now moot by construction —
    there is no server module left to grep or import. What remains
    provable: the one live coverage symbol this slice kept stays owned by
    patient_web."""

    def test_live_coverage_symbol_owned_by_patient_web(self):
        self.assertIn("def all_coberturas(", _DIRECTORY_SOURCE)

    def test_live_coverage_symbol_importable(self):
        self.assertTrue(hasattr(availability_directory, "all_coberturas"))


class TestLiveCoberturaDataPath(unittest.TestCase):
    """The live coberturas data path — all_coberturas + especialidades_tree,
    i.e. what /api/specialties serves — is intact and owned by the tree.

    `DATA_PATH` (data/availability.json) is a runtime WebTurno snapshot that
    isn't shipped with the repo, so in a bare test run `_load_db()` degrades
    and both functions return empty. To exercise the REAL functions on
    deterministic input we feed synthetic doctors through `_load_db` — a
    plain monkeypatch, no live server, no pack dependency."""

    # OSDE is accepted by two doctors, Swiss Medical by one, and "Particular"
    # is filtered out by all_coberturas (it is not a real obra social).
    _SYNTHETIC_DB = {
        "medicos": [
            {"nombre": "Dra. Ana Pérez", "especialidad": "Cardiología",
             "clinica": "Clínica X", "online": True,
             "obras_sociales": ["OSDE", "Swiss Medical"]},
            {"nombre": "Dr. Beto López", "especialidad": "Clínica Médica",
             "clinica": "Clínica Y", "online": False,
             "obras_sociales": ["OSDE"]},
            {"nombre": "Dr. Cara Soto", "especialidad": "Pediatría",
             "clinica": "Clínica X", "online": False,
             "obras_sociales": ["Particular"]},
        ]
    }

    def setUp(self):
        patcher = patch.object(availability_directory, "_load_db", return_value=self._SYNTHETIC_DB)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_coberturas_drops_particular_and_tallies(self):
        cob = availability_directory.all_coberturas()
        # "Particular" is excluded; OSDE (2) sorts above Swiss Medical (1).
        self.assertEqual(cob, [
            {"name": "OSDE", "slug": "osde", "count": 2},
            {"name": "Swiss Medical", "slug": "swiss-medical", "count": 1},
        ])

    def test_especialidades_tree_owns_the_coberturas_list(self):
        tree = availability_directory.especialidades_tree()
        # especialidades_tree() builds its `coberturas` FROM all_coberturas()
        # (`coberturas = all_coberturas()`), so the two must agree. That
        # ownership is exactly what makes /api/specialties the live route for
        # coverage data after the dead /cobertura + /obras-sociales went away.
        self.assertIn("coberturas", tree)
        self.assertEqual(tree["coberturas"], availability_directory.all_coberturas())
        self.assertEqual(tree["coberturas"], [
            {"name": "OSDE", "slug": "osde", "count": 2},
            {"name": "Swiss Medical", "slug": "swiss-medical", "count": 1},
        ])


if __name__ == "__main__":
    unittest.main()
