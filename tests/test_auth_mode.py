"""test_auth_mode.py — modo de ingreso de los pacientes (Configuración).

auth_mode.py sirve el modo configurado (default vía env TURNOS_AUTH_MODE, o el
guardado en la DB) y accounts._auth_mode() delega en él. Igual que test_theme.py,
la lógica de default se prueba con `_default()` (puro) y current() sembrando el
cache local — sin depender del estado de la DB.
"""

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.auth import mode as auth_mode  # noqa: E402
from app.core import config  # noqa: E402
from app.cuentas import accounts  # noqa: E402


class TestAuthModo(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_AUTH_MODE=None)
        # mismo seam que test_turnstile: el .env local del repo no debe
        # contaminar los defaults del test (auth_mode._env delega en config.get)
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""

    def tearDown(self):
        config.ENV_PATH = self._env_path
        auth_mode._cache["v"] = None
        auth_mode._cache["t"] = 0.0

    # ---- default (puro, sin DB) --------------------------------------------
    def test_default_es_password(self):
        self.assertEqual(auth_mode._default(), "password")

    def test_default_configurable_por_env(self):
        _env.set_env(self, TURNOS_AUTH_MODE="renaper")
        self.assertEqual(auth_mode._default(), "renaper")

    def test_env_invalido_cae_a_password(self):
        _env.set_env(self, TURNOS_AUTH_MODE="oauth-magico")
        self.assertEqual(auth_mode._default(), "password")

    # ---- guardar valida el id ----------------------------------------------
    def test_guardar_rechaza_modo_invalido(self):
        with self.assertRaises(ValueError):
            auth_mode.save("dni-a-secas")
        with self.assertRaises(ValueError):
            auth_mode.save("")

    # ---- actual() cachea (seam para no depender de la DB) ------------------
    def test_actual_usa_cache_fresco(self):
        auth_mode._cache["v"] = "renaper"
        auth_mode._cache["t"] = time.time()
        self.assertEqual(auth_mode.current(), "renaper")

    # ---- accounts._auth_mode delega en auth_mode -----------------------------
    def test_cuentas_auth_mode_delega(self):
        auth_mode._cache["v"] = "renaper"
        auth_mode._cache["t"] = time.time()
        self.assertEqual(accounts._auth_mode(), "renaper")
        auth_mode._cache["v"] = "password"
        self.assertEqual(accounts._auth_mode(), "password")

    # ---- catálogo para el selector del admin -------------------------------
    def test_modes_catalogo(self):
        self.assertIn("password", auth_mode.MODES)
        self.assertIn("renaper", auth_mode.MODES)
        for m in auth_mode.MODES.values():
            self.assertIn("id", m)
            self.assertIn("label", m)
            self.assertIn("desc", m)


if __name__ == "__main__":
    unittest.main()
