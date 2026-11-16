"""msd_data: match de turno propio en todo el dump + fallback al snapshot commiteado."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.connectors.msd import msd_data


DUMP = {
    "generado": "2026-07-09T17:11:30",
    "centros": [
        {"id": 928, "nombre": "CAPS DE PINAMAR", "especialidades": [
            {"nombre": "Medicina general y/o medicina de familia", "medicos": [
                {"medico": "BUSTOS, DIEGO",
                 "proximo": {"dia": "2026-08-03", "hora": "08:40"},
                 "turnos": [{"dia": "2026-08-03"}]},
                {"medico": "OLMEDO, RAUL",
                 "proximo": {"dia": "2026-07-15", "hora": "08:00"},
                 "turnos": [{"dia": "2026-07-15"}]},
            ]},
        ]},
        {"id": 927, "nombre": "CAPS VALERIA DEL MAR", "especialidades": [
            {"nombre": "Medicina general y/o medicina de familia", "medicos": [
                {"medico": "MONTES, Hugo",
                 "proximo": {"dia": "2026-07-28", "hora": "18:20"},
                 "turnos": [{"dia": "2026-07-28"}]},
            ]},
        ]},
    ],
}


class TestOwnAnywhere(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "msd_turnos.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DUMP, f)
        self._dumps, self._cache = msd_data._DUMPS, msd_data._CACHE
        msd_data._DUMPS = [path]
        msd_data._CACHE = None

    def tearDown(self):
        msd_data._DUMPS, msd_data._CACHE = self._dumps, self._cache
        self.tmp.cleanup()

    def test_agenda_bajo_otra_especialidad(self):
        # La pediatra Bustos atiende bajo "Medicina general" en MSD: la búsqueda
        # global la encuentra igual, con match de nombre completo.
        px = msd_data.proximo_own_anywhere("Diego Bustos", "caps-pinamar")
        self.assertEqual(px, ("2026-08-03", "08:40"))

    def test_apellido_solo_no_alcanza(self):
        # "OLMEDO, RAUL" no puede atribuirse a Ramiro Olmedo: el nombre de pila
        # tiene que coincidir, no sólo el apellido.
        self.assertIsNone(msd_data.proximo_own_anywhere("Ramiro Olmedo", "caps-ostende"))

    def test_prefiere_su_centro(self):
        px = msd_data.proximo_own_anywhere("Hugo Montes", "caps-valeria")
        self.assertEqual(px, ("2026-07-28", "18:20"))

    def test_future_acceptor_keeps_a_doctors_later_valid_slot(self):
        dump = msd_data._load()
        bustos = dump["centros"][0]["especialidades"][0]["medicos"][0]
        bustos["proximo"] = {"dia": "2026-07-17", "hora": "08:40"}
        bustos["turnos"] = [
            {"dia": "2026-07-17", "hora": "08:40"},
            {"dia": "2026-08-03", "hora": "08:40"},
        ]
        accept = lambda dia, hora: (dia, hora) > ("2026-08-01", "00:00")
        self.assertEqual(
            msd_data.proximo_own_anywhere("Diego Bustos", "caps-pinamar", accept=accept),
            ("2026-08-03", "08:40"),
        )
        self.assertEqual(
            msd_data.proximo_for_medico(
                {"nombre": "Medicina general", "centro": "caps-pinamar"},
                "Diego Bustos", strict=True, accept=accept),
            ("2026-08-03", "08:40"),
        )

    def test_sin_dump_devuelve_none(self):
        msd_data._DUMPS = [os.path.join(self.tmp.name, "no-existe.json")]
        msd_data._CACHE = None
        self.assertIsNone(msd_data.proximo_own_anywhere("Diego Bustos"))


class TestSnapshotFallback(unittest.TestCase):
    def test_cae_al_segundo_path(self):
        # data/ ausente (deploy en la nube) → se usa el snapshot de webdata/.
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, "snapshot.json")
            with open(snap, "w", encoding="utf-8") as f:
                json.dump(DUMP, f)
            dumps, cache = msd_data._DUMPS, msd_data._CACHE
            msd_data._DUMPS = [os.path.join(tmp, "no-existe.json"), snap]
            msd_data._CACHE = None
            try:
                self.assertIsNotNone(msd_data._load())
                px = msd_data.proximo_own_anywhere("Diego Bustos", "caps-pinamar")
                self.assertEqual(px, ("2026-08-03", "08:40"))
            finally:
                msd_data._DUMPS, msd_data._CACHE = dumps, cache


if __name__ == "__main__":
    unittest.main()
