"""test_ai_cooldown.py — enfriamiento escalonado por identidad para
lecturas de IA sobre una imagen/PDF (ai_cooldown.py).

Correr:  python3 -m unittest discover -s tests -p "test_ai_cooldown.py"

Todo con un reloj INYECTADO (`now=`) — nunca duerme de verdad.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.ai import ai_cooldown  # noqa: E402


class TestEnfriamiento(unittest.TestCase):
    def setUp(self):
        ai_cooldown._reset_para_tests()
        _env.set_env(self, TURNOS_IMG_BURST="2", TURNOS_IMG_BASE_SECONDS="60",
                     TURNOS_IMG_CAP_MIN="64")

    def tearDown(self):
        ai_cooldown._reset_para_tests()

    def test_identidad_vacia_siempre_permitida(self):
        ok, espera = ai_cooldown.attempt(None, now=0)
        self.assertTrue(ok)
        self.assertEqual(espera, 0)
        ok, espera = ai_cooldown.attempt("", now=1)
        self.assertTrue(ok)

    def test_rafaga_de_2_sin_espera(self):
        t = 1000.0
        ok1, _ = ai_cooldown.attempt("dni:1", now=t)
        ok2, _ = ai_cooldown.attempt("dni:1", now=t + 0.1)  # instantáneo
        self.assertTrue(ok1)
        self.assertTrue(ok2)

    def test_tercera_llamada_instantanea_bloquea_un_minuto(self):
        t = 1000.0
        ai_cooldown.attempt("dni:1", now=t)
        ai_cooldown.attempt("dni:1", now=t)
        ok3, espera3 = ai_cooldown.attempt("dni:1", now=t)  # 3ra, sin esperar
        self.assertFalse(ok3)
        self.assertEqual(espera3, 60)  # 1 min

    def test_esperar_lo_pedido_desbloquea(self):
        t = 1000.0
        ai_cooldown.attempt("dni:1", now=t)
        ai_cooldown.attempt("dni:1", now=t)
        ok3, espera3 = ai_cooldown.attempt("dni:1", now=t)
        self.assertFalse(ok3)
        ok4, _ = ai_cooldown.attempt("dni:1", now=t + espera3)  # justo a tiempo
        self.assertTrue(ok4)

    def test_duplica_1_2_4_8_16_con_tope(self):
        t = 0.0
        # ráfaga de 2, libre
        ai_cooldown.attempt("dni:2", now=t)
        ai_cooldown.attempt("dni:2", now=t)
        esperas = []
        for _ in range(6):
            ok, espera = ai_cooldown.attempt("dni:2", now=t)
            self.assertFalse(ok)
            esperas.append(espera)
            t += espera  # avanzamos EXACTO lo pedido y volvemos a consumir
            ok2, _ = ai_cooldown.attempt("dni:2", now=t)
            self.assertTrue(ok2)
        self.assertEqual(esperas, [60, 120, 240, 480, 960, 1920])

    def test_tope_64_minutos(self):
        _env.set_env(self, TURNOS_IMG_BURST="0")
        t = 0.0
        # ya con burst=0, la primera llamada de una identidad NUEVA sigue
        # siendo gratis (arranque de estado) — desde la segunda ya escala.
        ai_cooldown.attempt("dni:3", now=t)
        for _ in range(12):
            ok, espera = ai_cooldown.attempt("dni:3", now=t)
            self.assertFalse(ok)
            t += espera
            ai_cooldown.attempt("dni:3", now=t)
        ok, espera = ai_cooldown.attempt("dni:3", now=t)
        self.assertFalse(ok)
        self.assertLessEqual(espera, 64 * 60)
        self.assertEqual(espera, 64 * 60)

    def test_decae_tras_24h_de_silencio(self):
        t = 1000.0
        ai_cooldown.attempt("dni:4", now=t)
        ai_cooldown.attempt("dni:4", now=t)
        ok, espera = ai_cooldown.attempt("dni:4", now=t)
        self.assertFalse(ok)
        # 25 horas después: decay -> ráfaga nueva, sin arrastrar el contador
        mas_tarde = t + 25 * 3600
        ok1, _ = ai_cooldown.attempt("dni:4", now=mas_tarde)
        ok2, _ = ai_cooldown.attempt("dni:4", now=mas_tarde + 0.1)
        self.assertTrue(ok1)
        self.assertTrue(ok2)

    def test_identidades_aisladas_entre_si(self):
        t = 1000.0
        ai_cooldown.attempt("dni:5", now=t)
        ai_cooldown.attempt("dni:5", now=t)
        ok5, _ = ai_cooldown.attempt("dni:5", now=t)
        self.assertFalse(ok5)  # dni:5 ya está en cooldown
        ok6, _ = ai_cooldown.attempt("dni:6", now=t)  # otra identidad, intacta
        self.assertTrue(ok6)

    def test_mensaje_redondea_a_minutos(self):
        self.assertEqual(ai_cooldown.message(1), "Podés volver a subir una orden en 1 minuto.")
        self.assertEqual(ai_cooldown.message(90), "Podés volver a subir una orden en 2 minutos.")
        self.assertEqual(ai_cooldown.message(0), "Podés volver a subir una orden en 1 minuto.")


if __name__ == "__main__":
    unittest.main()
