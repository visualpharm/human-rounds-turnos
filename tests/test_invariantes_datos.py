"""Invariantes de los paquetes de datos (webdata/ y cualquier otro).

catalog.py valida esto al ESCRIBIR (edición del admin). Este sweep lo chequea
al LEER, sobre los JSON autorados que se commitean — así un error de autoría o
del importador (scripts/import_detalle_turnos.py) se caza en el test, no en
producción. Invariante load-bearing y "no relitigar":

  Un servicio walkin / walkin_con_orden NUNCA lleva `agenda`
  (contradiría el cartel "No necesitás turno" — walkin no ofrece turnos).
"""
import glob
import json
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from app.patient_web import catalog  # noqa: E402


def _data_dirs():
    """Todo paquete de datos del repo con su propio servicios.json: webdata/ +
    webdata-*/ hermanos (legado), y cualquier pack bajo packs/ (Pinamar movió
    servicios.json a packs/ar-b-pinamar/ el 2026-07-30; example-town también
    trae el suyo) — ver docs/architecture/data-packs.md."""
    dirs = []
    candidatos = ([os.path.join(_ROOT, "webdata")]
                  + sorted(glob.glob(os.path.join(_ROOT, "webdata-*")))
                  + sorted(glob.glob(os.path.join(_ROOT, "packs", "*"))))
    for d in candidatos:
        if os.path.isfile(os.path.join(d, "servicios.json")):
            dirs.append(d)
    return dirs


def _servicios(data_dir):
    with open(os.path.join(data_dir, "servicios.json"), encoding="utf-8") as f:
        doc = json.load(f)
    return doc["servicios"] if isinstance(doc, dict) else doc


class TestWalkinSinAgenda(unittest.TestCase):
    def test_ningun_walkin_tiene_agenda(self):
        dirs = _data_dirs()
        self.assertTrue(dirs, "no se encontró ningún paquete de datos con servicios.json")
        for data_dir in dirs:
            for s in _servicios(data_dir):
                if s.get("modo") in catalog._NO_APPOINTMENT_MODES:
                    self.assertFalse(
                        s.get("agenda"),
                        f"{os.path.basename(data_dir)}: servicio walkin '{s.get('id')}' "
                        f"lleva agenda — un walkin no ofrece turnos")

    def test_modo_valido_en_cada_servicio(self):
        for data_dir in _data_dirs():
            for s in _servicios(data_dir):
                modo = s.get("modo")
                self.assertIn(
                    modo, catalog.SERVICE_MODES,
                    f"{os.path.basename(data_dir)}: servicio '{s.get('id')}' "
                    f"tiene modo inválido {modo!r}")


if __name__ == "__main__":
    unittest.main()
