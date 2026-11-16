"""test_refeps_retry.py — buscar_matricula() reintenta ante un blip transitorio
de REFEPS antes de propagar RefepsError.

Contexto (2026-07-15): dos consultas reales en /api/admin/refeps devolvieron 502
(RefepsError) y funcionaron segundos/horas después sin ningún cambio de código —
blip de red contra argentina.gob.ar, no un bug de parseo. Antes del fix, un solo
intento fallido tiraba la consulta entera; ahora reintenta _RETRIES veces con un
delay corto. Sin red: mockea `_buscar_matricula_once` y `time.sleep`.

Correr: python3 -m unittest discover -s tests -p "test_refeps_retry.py" -v
"""
import unittest
from unittest.mock import patch

from app.connectors.refeps import refeps


class TestBuscarMatriculaRetry(unittest.TestCase):
    def test_exito_al_primer_intento_no_reintenta(self):
        with patch("app.connectors.refeps.refeps._buscar_matricula_once", return_value=[{"apellido": "X"}]) as m, \
             patch("app.connectors.refeps.refeps.time.sleep") as sl:
            out = refeps.buscar_matricula("123")
        self.assertEqual(out, [{"apellido": "X"}])
        self.assertEqual(m.call_count, 1)
        sl.assert_not_called()

    def test_falla_una_vez_y_se_recupera_en_el_reintento(self):
        with patch("app.connectors.refeps.refeps._buscar_matricula_once",
                   side_effect=[refeps.RefepsError("blip transitorio"), [{"apellido": "Y"}]]) as m, \
             patch("app.connectors.refeps.refeps.time.sleep") as sl:
            out = refeps.buscar_matricula("456")
        self.assertEqual(out, [{"apellido": "Y"}])
        self.assertEqual(m.call_count, 2)
        sl.assert_called_once()

    def test_falla_persistente_propaga_refepserror_tras_agotar_reintentos(self):
        with patch("app.connectors.refeps.refeps._buscar_matricula_once",
                   side_effect=refeps.RefepsError("sitio caído")) as m, \
             patch("app.connectors.refeps.refeps.time.sleep"):
            with self.assertRaises(refeps.RefepsError):
                refeps.buscar_matricula("789")
        self.assertEqual(m.call_count, refeps._RETRIES + 1)


if __name__ == "__main__":
    unittest.main()
