"""test_config.py — config.py (precedencia DB > env > .env > default, legacy).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_config.py"
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
import _env  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        # nunca pegarle a un .env real del checkout durante los tests
        self._env_path_patch = patch.object(config, "ENV_PATH", "/no/existe/.env")
        self._env_path_patch.start()

    def tearDown(self):
        self._env_path_patch.stop()
        config._reset_cache_para_tests()

    def test_env_gana_sobre_dotenv(self):
        with patch.object(config, "_dotenv", return_value={"X": "del_dotenv"}), \
             _env.patched_env({"X": "del_env"}):
            self.assertEqual(config.get("X"), "del_env")

    def test_dotenv_gana_sobre_default(self):
        with patch.object(config, "_dotenv", return_value={"X": "del_dotenv"}), \
             _env.patched_env(X=None):
            self.assertEqual(config.get("X", default="def"), "del_dotenv")

    def test_default_cuando_no_hay_nada(self):
        with patch.object(config, "_dotenv", return_value={}), \
             patch.object(config, "_db_overrides", return_value={}):
            _env.set_env(self, NOEXISTE_X=None)
            self.assertEqual(config.get("NOEXISTE_X", default="def"), "def")
            self.assertIsNone(config.get("NOEXISTE_X"))

    def test_db_gana_sobre_todo(self):
        with patch.object(config, "_db_overrides", return_value={"X": "de_la_db"}), \
             patch.object(config, "_dotenv", return_value={"X": "del_dotenv"}), \
             _env.patched_env({"X": "del_env"}):
            self.assertEqual(config.get("X"), "de_la_db")

    def test_legacy_fallback_en_env(self):
        _env.set_env(self, NUEVO=None)
        with patch.object(config, "_dotenv", return_value={}), \
             patch.object(config, "_db_overrides", return_value={}), \
             _env.patched_env({"VIEJO": "valor_viejo"}):
            self.assertEqual(config.get("NUEVO", legacy="VIEJO"), "valor_viejo")

    def test_legacy_fallback_en_dotenv(self):
        _env.set_env(self, NUEVO=None, VIEJO=None)
        with patch.object(config, "_dotenv", return_value={"VIEJO": "valor_viejo_dotenv"}), \
             patch.object(config, "_db_overrides", return_value={}):
            self.assertEqual(config.get("NUEVO", legacy="VIEJO"), "valor_viejo_dotenv")

    def test_legacy_fallback_en_db(self):
        with patch.object(config, "_db_overrides", return_value={"VIEJO": "valor_viejo_db"}), \
             patch.object(config, "_dotenv", return_value={}):
            _env.set_env(self, NUEVO=None, VIEJO=None)
            self.assertEqual(config.get("NUEVO", legacy="VIEJO"), "valor_viejo_db")

    def test_nuevo_gana_sobre_legacy_en_niveles_mas_altos(self):
        # nuevo en env vs viejo en DB: DB (aunque sea la clave legacy) le
        # gana a env — la DB es el nivel más alto sin importar el nombre.
        with patch.object(config, "_db_overrides", return_value={"VIEJO": "de_la_db"}), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env({"NUEVO": "del_env"}):
            self.assertEqual(config.get("NUEVO", legacy="VIEJO"), "de_la_db")

    def test_db_ausente_o_ok_cae_a_env(self):
        # el caso real: _db_overrides() no explota, sólo no encuentra fila.
        with patch("app.core.db_catalog.catalog_get", side_effect=RuntimeError("sin conexión")), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env({"X": "del_env"}):
            config._reset_cache_para_tests()
            self.assertEqual(config.get("X"), "del_env")

    def test_db_sin_fila_cae_a_default(self):
        with patch("app.core.db_catalog.catalog_get", return_value=None), \
             patch.object(config, "_dotenv", return_value={}):
            config._reset_cache_para_tests()
            _env.set_env(self, NOEXISTE_Y=None)
            self.assertEqual(config.get("NOEXISTE_Y", default="def"), "def")

    def test_bootstrap_keys_saltean_la_db(self):
        # DATABASE_URL/HOST/PORT nunca deben llamar a _db_overrides — si lo
        # hicieran, habría circularidad (para leer la config hace falta la
        # DB, para conectar a la DB hace falta la config).
        with patch.object(config, "_db_overrides", side_effect=AssertionError(
                "config.get() consultó la DB para una clave de arranque")), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env({"DATABASE_URL": "postgres://x"}):
            self.assertEqual(config.get("DATABASE_URL"), "postgres://x")
        with patch.object(config, "_db_overrides", side_effect=AssertionError(
                "config.get() consultó la DB para una clave de arranque")), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env(HOST=None, PORT=None):
            self.assertIsNone(config.get("HOST"))
            self.assertEqual(config.get("PORT", default="7700"), "7700")

    def test_bootstrap_key_ignora_override_de_db(self):
        # aunque _db_overrides() SÍ tenga la clave (p. ej. quedó un valor
        # viejo guardado), una clave de arranque nunca debe usarla.
        with patch.object(config, "_db_overrides", return_value={"DATABASE_URL": "de_la_db"}), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env({"DATABASE_URL": "del_env"}):
            self.assertEqual(config.get("DATABASE_URL"), "del_env")

    def test_lock_hace_ganar_env_sobre_db(self):
        # TURNOS_CONFIG_LOCK=1 (escape hatch de ops, fase 4): el override de
        # DB se ignora del todo, aunque exista.
        with patch.object(config, "_db_overrides", return_value={"X": "de_la_db"}), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env({"X": "del_env", "TURNOS_CONFIG_LOCK": "1"}):
            self.assertEqual(config.get("X"), "del_env")
            self.assertTrue(config.locked())

    def test_lock_hace_ganar_dotenv_sobre_db_sin_env(self):
        with patch.object(config, "_db_overrides", return_value={"X": "de_la_db"}), \
             patch.object(config, "_dotenv", return_value={"X": "del_dotenv"}), \
             _env.patched_env({"TURNOS_CONFIG_LOCK": "1", "X": None}):
            self.assertEqual(config.get("X"), "del_dotenv")

    def test_sin_lock_gana_db_como_siempre(self):
        with patch.object(config, "_db_overrides", return_value={"X": "de_la_db"}), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env({"X": "del_env", "TURNOS_CONFIG_LOCK": None}):
            self.assertEqual(config.get("X"), "de_la_db")
            self.assertFalse(config.locked())

    def test_lock_no_afecta_claves_de_arranque_igual(self):
        # ya saltean la DB de por sí — con o sin lock, mismo resultado.
        with patch.object(config, "_db_overrides", side_effect=AssertionError("no debería consultar la DB")), \
             patch.object(config, "_dotenv", return_value={}), \
             _env.patched_env({"DATABASE_URL": "del_env", "TURNOS_CONFIG_LOCK": "1"}):
            self.assertEqual(config.get("DATABASE_URL"), "del_env")

    def test_guardar_hace_merge_e_invalida_cache(self):
        estado = {"v": {"A": "1"}}

        def fake_catalogo_get(tipo, id_):
            return {"doc": estado["v"]}

        def fake_catalogo_upsert(tipo, id_, doc, borrado=False, connection=None):
            estado["v"] = doc
            return {"doc": doc}

        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = lambda: {"doc": estado["v"]}
        with patch("app.core.db._connect", return_value=conn), \
             patch("app.core.db_ai.ai_register_append") as audit, \
             patch("app.core.db_catalog.catalog_get", side_effect=fake_catalogo_get), \
             patch("app.core.db_catalog.catalog_upsert", side_effect=fake_catalogo_upsert):
            config._reset_cache_para_tests()
            resultado = config.guardar({"B": "2", "A": None})
            self.assertEqual(resultado, {"B": "2"})
            self.assertEqual(estado["v"], {"B": "2"})


class EnvFileOverrideTest(unittest.TestCase):
    """`TURNOS_ENV_FILE` reemplaza el `.env` real que consulta config.get()
    -- ver config._env_path()/_dotenv(). Prueba puntual del pedido de
    Codex (Tier 2, ítem 6): un checkout con un `.env` de desarrollo YA
    poblado (TOTP/Turnstile reales) no debe filtrarse al server bajo test
    del harness E2E; el harness nunca necesita blanquear ese archivo."""

    def setUp(self):
        config._reset_cache_para_tests()
        fd, self._real_env = tempfile.mkstemp(prefix="fake_real_dotenv_", suffix=".env")
        os.close(fd)
        with open(self._real_env, "w", encoding="utf-8") as f:
            f.write("TURNOS_ADMIN_TOTP_SECRET=REALSECRETFROMDEVDOTENV\n")
        # Simula "el .env real del checkout" sin tocar el de verdad.
        self._env_path_patch = patch.object(config, "ENV_PATH", self._real_env)
        self._env_path_patch.start()

    def tearDown(self):
        self._env_path_patch.stop()
        os.remove(self._real_env)
        config._reset_cache_para_tests()

    def test_sin_override_lee_el_dotenv_real(self):
        # sanity: sin TURNOS_ENV_FILE, config.get() SÍ lee el .env "real"
        # simulado -- confirma que el fixture de este test está bien armado
        # antes de probar que el override lo neutraliza.
        _env.set_env(self, TURNOS_ENV_FILE=None, TURNOS_ADMIN_TOTP_SECRET=None)
        with patch.object(config, "_db_overrides", return_value={}):
            config._reset_cache_para_tests()
            self.assertEqual(
                config.get("TURNOS_ADMIN_TOTP_SECRET"), "REALSECRETFROMDEVDOTENV"
            )

    def test_turnos_env_file_ignora_el_dotenv_real(self):
        # un TURNOS_ENV_FILE apuntando a un archivo mínimo (lo que escribe
        # el harness E2E) hace que el .env real NUNCA se abra -- el secreto
        # ahí adentro no se filtra, sin blanquear nada a mano.
        fd, minimal = tempfile.mkstemp(prefix="harness_minimal_", suffix=".env")
        os.close(fd)
        with open(minimal, "w", encoding="utf-8") as f:
            f.write("# vacío a propósito -- harness hermético\n")
        try:
            with patch.object(config, "_db_overrides", return_value={}), \
                 _env.patched_env({"TURNOS_ENV_FILE": minimal, "TURNOS_ADMIN_TOTP_SECRET": None}):
                config._reset_cache_para_tests()
                self.assertIsNone(config.get("TURNOS_ADMIN_TOTP_SECRET"))
        finally:
            os.remove(minimal)

    def test_turnos_env_file_a_ruta_inexistente_tambien_ignora_el_real(self):
        # el harness puede apuntar a una ruta que ni siquiera llega a crear
        # -- _dotenv() debe degradarse a {} en vez de caer al .env real.
        with patch.object(config, "_db_overrides", return_value={}), \
             _env.patched_env({"TURNOS_ENV_FILE": "/no/existe/e2e.env", "TURNOS_ADMIN_TOTP_SECRET": None}):
            config._reset_cache_para_tests()
            self.assertIsNone(config.get("TURNOS_ADMIN_TOTP_SECRET"))


if __name__ == "__main__":
    unittest.main()
