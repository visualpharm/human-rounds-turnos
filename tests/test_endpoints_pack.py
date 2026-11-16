"""test_endpoints_pack.py — PUCO/RENAPER/REFEPS endpoint URLs sourced from
packs/ar/endpoints.json instead of hardcoded literals (Phase 2c, 2026-07-30).

Env override (TURNOS_SISA_URL / TURNOS_REFEPS_URL / TURNOS_RENAPER_*) still
wins over BOTH the pack and the literal fallback — same precedence chain as
format.py's PHONE / tz.py.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_endpoints_pack.py"
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.connectors.puco import puco       # noqa: E402
from app.connectors.refeps import refeps     # noqa: E402
from app.cuentas import auth_renaper  # noqa: E402


class TestPucoSisaUrl(unittest.TestCase):
    def test_default_matches_packs_ar_endpoints_json(self):
        self.assertEqual(puco.SISA_URL,
                          "https://sisa.msal.gov.ar/sisa/services/rest/puco/")

    def test_env_override_wins(self):
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs after the env is put back
        _env.set_env(self, TURNOS_SISA_URL="https://example.test/puco/")
        config._reset_cache_para_tests()
        self.assertEqual(puco._load_sisa_url(), "https://example.test/puco/")


class TestRefepsUrl(unittest.TestCase):
    def test_default_matches_packs_ar_endpoints_json(self):
        self.assertEqual(
            refeps.URL,
            "https://www.argentina.gob.ar/salud/buscador-nacional-de-profesionales-de-la-salud")

    def test_env_override_wins(self):
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs after the env is put back
        _env.set_env(self, TURNOS_REFEPS_URL="https://example.test/refeps/")
        config._reset_cache_para_tests()
        self.assertEqual(refeps._load_url(), "https://example.test/refeps/")


class TestRenaperDefaults(unittest.TestCase):
    def test_defaults_match_packs_ar_endpoints_json(self):
        self.assertEqual(auth_renaper._DEFAULTS["TURNOS_RENAPER_AUTHORIZE_URL"],
                          "https://id.argentina.gob.ar/authorize/")
        self.assertEqual(auth_renaper._DEFAULTS["TURNOS_RENAPER_TOKEN_URL"],
                          "https://id.argentina.gob.ar/token/")
        self.assertEqual(auth_renaper._DEFAULTS["TURNOS_RENAPER_USERINFO_URL"],
                          "https://id.argentina.gob.ar/userinfo/")

    def test_env_still_overrides_the_pack_default(self):
        # _env() checks config.get(key) BEFORE _DEFAULTS -- proves the pack
        # value only ever supplies the default, never shadows an explicit
        # TURNOS_RENAPER_* override.
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs after the env is put back
        _env.set_env(self, TURNOS_RENAPER_AUTHORIZE_URL="https://example.test/authorize/")
        config._reset_cache_para_tests()
        self.assertEqual(auth_renaper._env("TURNOS_RENAPER_AUTHORIZE_URL"),
                          "https://example.test/authorize/")


if __name__ == "__main__":
    unittest.main()
