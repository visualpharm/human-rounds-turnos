"""test_agenda_print.py — the shape of «Turnos programados» without a database.

`modules/turnos/agenda_print.py` (2026-11-07): the windows a doctor's agenda
opens on a day (a closed day prints none, an `extra` exception adds one), and
how the day's rows fall into them (by time, the row's own center first, the
rest in a last block without hours). The route, the access rows and the
refusal of another doctor's day are in test_access_log_routes.py.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _env  # noqa: E402,F401

from app.modules.turnos import agenda_print  # noqa: E402
from app.patient_web import catalog  # noqa: E402

# 2026-11-10 is a Tuesday: weekday 2 in the agenda's Sunday-first numbering.
_DIA = "2026-11-10"


def _medico(**extra):
    m = {"id": "med-1", "nombre": "Dra. Uno", "centros": [
        {"centro": "centro-a", "franjas": [{"dias": [2], "desde": "17:00", "hasta": "19:20"},
                                           {"dias": [4], "desde": "08:00", "hasta": "12:00"}]},
        {"centro": "centro-b", "franjas": [{"dias": [2], "desde": "08:00", "hasta": "10:00"}]},
    ]}
    m.update(extra)
    return m


def _fila(hora, centro="centro-a", apellido="Paz"):
    return {"hora": hora, "centro_slug": centro, "apellidos": apellido, "nombres": "Ana"}


class TestVentanas(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(catalog, "services", return_value=[])
        p.start()
        self.addCleanup(p.stop)

    def test_las_franjas_del_dia_en_orden(self):
        self.assertEqual(agenda_print.day_windows(_medico(), _DIA), [
            {"desde": "08:00", "hasta": "10:00", "centro": "centro-b"},
            {"desde": "17:00", "hasta": "19:20", "centro": "centro-a"},
        ])

    def test_un_dia_cerrado_no_tiene_franjas_y_un_extra_suma_una(self):
        m = _medico()
        m["centros"][0]["excepciones"] = [{"fecha": _DIA, "tipo": "extra", "desde": "20:00", "hasta": "21:00"}]
        m["centros"][1]["excepciones"] = [{"fecha": _DIA, "tipo": "cerrado"}]
        self.assertEqual(agenda_print.day_windows(m, _DIA), [
            {"desde": "17:00", "hasta": "19:20", "centro": "centro-a"},
            {"desde": "20:00", "hasta": "21:00", "centro": "centro-a"},
        ])

    def test_una_fecha_rota_no_tiene_franjas(self):
        self.assertEqual(agenda_print.day_windows(_medico(), "no-es-fecha"), [])
        self.assertEqual(agenda_print.parse_day("2026-13-01"), None)
        self.assertEqual(agenda_print.parse_day("10/11/2026"), None)
        self.assertEqual(agenda_print.parse_day("2026-11-10"), "2026-11-10")


class TestBloques(unittest.TestCase):
    def test_cada_fila_en_su_franja_y_lo_de_afuera_al_final(self):
        ventanas = [{"desde": "08:00", "hasta": "10:00", "centro": "centro-b"},
                    {"desde": "08:00", "hasta": "10:00", "centro": "centro-a"},
                    {"desde": "17:00", "hasta": "19:20", "centro": "centro-a"},
                    {"desde": "20:00", "hasta": "21:00", "centro": "centro-a"}]
        filas = [_fila("19:00", apellido="Zeta"), _fila("17:20", apellido="Alfa"),
                 _fila("08:40", centro="centro-a"), _fila("22:10"), _fila("19:20")]
        bloques = agenda_print.blocks(ventanas, filas)
        resumen = [(b["desde"], b["centro"], [f["hora"] for f in b["filas"]]) for b in bloques]
        self.assertEqual(resumen, [
            ("08:00", "centro-a", ["08:40"]),          # its own center's window, not the first one at that hour
            ("17:00", "centro-a", ["17:20", "19:00"]),
            (None, None, ["19:20", "22:10"]),         # the window's end is not inside it; the empty 20:00 window is not printed
        ])


if __name__ == "__main__":
    unittest.main()
