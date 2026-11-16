"""Data-package location contracts for TURNOS_WEBDATA_DIR and pack mode.

Without the legacy override, ``webdata_dir()`` still reports the compatibility
directory while ``path()`` resolves through the public default pack chain.
With TURNOS_WEBDATA_DIR, the explicit single-directory override wins.

catalog.merge_with_overlay se monkeypatchea a identidad (no perder nada):
en un entorno con Postgres levantado y filas de overlay reales (edits del
admin sobre servicios de Pinamar), esas filas "sólo-DB" se agregan SIEMPRE
al final de merge_with_overlay sin importar qué base se les pase — así que
podrían filtrar servicios de Pinamar al merge de Sampleton. Sin este
parche el test sería no-determinista según si hay Postgres corriendo a mano.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import sitedata  # noqa: E402
from app.patient_web import catalog   # noqa: E402
from app.patient_web import site_base, site_catalog, site_centers

EJEMPLO_DIR = os.path.join(ROOT, "packs", "example-town")


def _identidad(tipo, base, incluir_borrados=False, strict=False):
    meta = {it.get(catalog.id_key(tipo)): {"_origen": "json", "_borrado": False} for it in base}
    return list(base), meta


def _clear_caches():
    catalog._JSON_CACHE.clear()
    site_base._CACHE.clear()


class TestDatosDirDefault(unittest.TestCase):
    """No legacy override: public example-town is the active data source."""

    def setUp(self):
        self.addCleanup(_clear_caches)  # registered first: runs once the env is back
        _env.set_env(self, TURNOS_WEBDATA_DIR=None)
        _clear_caches()

    def test_webdata_dir_es_el_del_repo(self):
        self.assertTrue(sitedata.webdata_dir().endswith(os.sep + "webdata"))
        self.assertEqual(sitedata.webdata_dir(), os.path.join(ROOT, "webdata"))

    def test_default_pack_serves_example_town(self):
        nombres = [s.get("nombre") for s in site_catalog._services()]
        self.assertIn("Vacunación", nombres)
        centros = [c.get("slug") for c in site_centers._centers()]
        self.assertIn("sampleton-hospital", centros)
        self.assertNotIn("hospital-pinamar", centros)


class TestDatosDirEjemplo(unittest.TestCase):
    """(b) con TURNOS_WEBDATA_DIR=packs/example-town: sirve Sampleton, nunca Pinamar."""

    def setUp(self):
        self.addCleanup(_clear_caches)  # registered first: runs once the env is back
        _env.set_env(self, TURNOS_WEBDATA_DIR=EJEMPLO_DIR)
        _clear_caches()
        self._merge_bak = catalog.merge_with_overlay
        catalog.merge_with_overlay = _identidad

    def tearDown(self):
        catalog.merge_with_overlay = self._merge_bak

    def test_datos_path_respeta_la_env(self):
        self.assertEqual(sitedata.webdata_dir(), os.path.abspath(EJEMPLO_DIR))
        self.assertEqual(sitedata.path("servicios.json"),
                          os.path.join(os.path.abspath(EJEMPLO_DIR), "servicios.json"))

    def test_catalogo_servicios_sirve_ojo_manso(self):
        nombres = [s.get("nombre") for s in catalog.services()]
        self.assertTrue(any(n == "Vacunación" for n in nombres))

    def test_hospital_servicios_es_ojo_manso_sin_pinamar(self):
        servicios = site_catalog._services()
        ids = [s.get("id") for s in servicios]
        self.assertIn("vacunacion-riverside-clinic", ids)
        self.assertIn("cardiologia-sampleton-hospital", ids)
        # nada de Pinamar: ni por id ni por nombre de centro
        self.assertFalse(any("pinamar" in (i or "") for i in ids))
        centros = [c.get("slug") for c in site_centers._centers()]
        # Sampleton tiene el tamaño de un pueblo mediano de verdad
        # (scripts/generate_example_town_catalog.py). Lo que importa acá no es
        # cuántos centros hay — eso cambia cada vez que se regenera el pack —
        # sino que los dos históricos sigan estando y que no se cuele ninguno
        # de Pinamar.
        self.assertIn("sampleton-hospital", centros)
        self.assertIn("riverside-clinic", centros)
        self.assertNotIn("hospital-pinamar", centros)
        self.assertFalse([c for c in centros if "pinamar" in c])

    def test_todos_los_modos_estan_representados(self):
        modos = {s.get("modo") for s in site_catalog._services()}
        self.assertEqual(modos, {"walkin", "walkin_con_orden", "solicitud", "solicitud_con_orden"})


if __name__ == "__main__":
    unittest.main()
