"""test_waitlist.py — re-oferta de turnos liberados (C1).

El motor `next_action` es puro (sin base) y se testea siempre. Las pruebas
de integración con Postgres (optar/liberar/tick/reclamar) se saltean si la base
no está, igual que test_booking_requests.py.

Correr:  python3 -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _env  # noqa: E402
_env.process_defaults(TURNOS_SKIP_HSI="1")

from app.modules.turnos import waitlist as le  # noqa: E402

UTC = timezone.utc


def _dt(h, m=0):
    return datetime(2026, 7, 20, h, m, tzinfo=UTC)


def _cand(cid):
    return {"id": cid, "dni": f"dni{cid}", "email": f"p{cid}@x.com"}


def _oferta(expira):
    return {"expires_at": expira}


class TestMotor(unittest.TestCase):
    """next_action: motor puro de decisión."""

    def test_slot_lejos_y_cola_ofrece_al_primero(self):
        slot = _dt(15)                     # turno a las 15:00
        ahora = _dt(9)                     # faltan 6 h (>30 min)
        cola = [_cand(1), _cand(2)]
        accion, cand = le.next_action(slot, ahora, oferta_vigente=None, cola=cola)
        self.assertEqual(accion, "ofrecer")
        self.assertEqual(cand["id"], 1)    # FIFO: el primero

    def test_oferta_viva_en_ventana_espera(self):
        slot = _dt(15)
        ahora = _dt(9)
        viva = _oferta(_dt(9, 4))          # vence 9:04, ahora 9:00 -> viva
        accion, cand = le.next_action(slot, ahora, oferta_vigente=viva,
                                         cola=[_cand(2)])
        self.assertEqual(accion, "esperar")
        self.assertIsNone(cand)

    def test_ventana_vencida_pasa_al_siguiente(self):
        slot = _dt(15)
        ahora = _dt(9, 6)                  # 9:06, la ventana venció 9:05
        viva = _oferta(_dt(9, 5))
        accion, cand = le.next_action(slot, ahora, oferta_vigente=viva,
                                         cola=[_cand(2), _cand(3)])
        self.assertEqual(accion, "ofrecer")
        self.assertEqual(cand["id"], 2)

    def test_a_minutos_del_turno_igual_se_ofrece(self):
        # El responsable del producto, 2026-07-18: el paciente puede estar YA en el hospital — no hay
        # corte de "faltan menos de X min" (revoca el CORTE_MIN=30 original).
        slot = _dt(15)
        ahora = _dt(14, 55)               # faltan 5 min -> igual se ofrece
        accion, cand = le.next_action(slot, ahora, oferta_vigente=None,
                                         cola=[_cand(1), _cand(2)])
        self.assertEqual(accion, "ofrecer")
        self.assertEqual(cand, _cand(1))

    def test_turno_ya_pasado_queda_vacante(self):
        # El único corte que queda: un turno cuya hora ya pasó no se ofrece,
        # ni siquiera con una oferta viva colgada.
        slot = _dt(15)
        ahora = _dt(15, 1)
        viva = _oferta(_dt(15, 4))
        accion, _ = le.next_action(slot, ahora, oferta_vigente=viva, cola=[])
        self.assertEqual(accion, "vacante")

    def test_cola_vacia_deja_vacante(self):
        slot = _dt(15)
        ahora = _dt(9)
        accion, cand = le.next_action(slot, ahora, oferta_vigente=None, cola=[])
        self.assertEqual(accion, "vacante")
        self.assertIsNone(cand)

    def test_un_minuto_antes_todavia_ofrece(self):
        slot = _dt(15)
        ahora = _dt(14, 59)               # falta 1 minuto -> igual se ofrece
        accion, _ = le.next_action(slot, ahora, oferta_vigente=None,
                                      cola=[_cand(1)])
        self.assertEqual(accion, "ofrecer")

    def test_constantes(self):
        self.assertEqual(le.WINDOW_MIN, 5)
        self.assertEqual(le.STAGGER_MIN, 5)
        self.assertFalse(hasattr(le, "CORTE_MIN"))  # revocado 2026-07-18


if __name__ == "__main__":
    unittest.main()
