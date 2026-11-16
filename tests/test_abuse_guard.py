"""abuse_guard.py — rate limit en memoria del endpoint de IA (/api/analyze-order).

Hasta ahora sólo cubierto de refilón por los 429 de otros módulos. Acá se aíslan
los bordes: el corte cae EXACTO en el límite por hora y por día, cada IP lleva su
propia cuenta, y el sweep poda las IPs muertas sin tocar las vivas.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
from app.ai import abuse_guard  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        abuse_guard._HITS.clear()
        abuse_guard._HITS.calls = 0

    def tearDown(self):
        abuse_guard._HITS.clear()

    def _limites(self, por_hora, por_dia):
        _env.set_env(self, TURNOS_AI_PER_HOUR=str(por_hora), TURNOS_AI_PER_DAY=str(por_dia))


class TestPermitido(_Base):
    def test_corta_exacto_en_el_limite_por_hora(self):
        self._limites(3, 100)
        ok = [abuse_guard.allowed("1.1.1.1") for _ in range(5)]
        # los primeros 3 pasan, del 4to en adelante corta
        self.assertEqual(ok, [True, True, True, False, False])

    def test_corta_por_dia_aunque_sobre_cupo_horario(self):
        self._limites(100, 2)
        ok = [abuse_guard.allowed("2.2.2.2") for _ in range(4)]
        self.assertEqual(ok, [True, True, False, False])

    def test_cada_ip_lleva_su_cuenta(self):
        self._limites(1, 100)
        self.assertTrue(abuse_guard.allowed("a"))
        self.assertFalse(abuse_guard.allowed("a"))   # a agotó su cupo
        self.assertTrue(abuse_guard.allowed("b"))    # b no se ve afectada

    def test_ip_vacia_no_revienta(self):
        self._limites(1, 100)
        self.assertTrue(abuse_guard.allowed(None))
        self.assertFalse(abuse_guard.allowed(None))  # None colapsa a "?"


class TestSweep(_Base):
    def test_poda_ips_muertas_y_conserva_las_vivas(self):
        now = time.time()
        abuse_guard._HITS["vieja"] = [now - abuse_guard._DAY - 5]      # sin hits en 24h
        abuse_guard._HITS["fresca"] = [now - 10]                 # hit reciente
        abuse_guard._sweep(now)
        self.assertNotIn("vieja", abuse_guard._HITS)
        self.assertIn("fresca", abuse_guard._HITS)


if __name__ == "__main__":
    unittest.main()
