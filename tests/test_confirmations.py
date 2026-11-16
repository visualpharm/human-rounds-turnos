"""test_confirmations.py — la respuesta SÍ/NO al recordatorio del día previo
(reunión hospital 2026-07-24).

Correr: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_confirmations.py"
(OJO: `python3 -m unittest test.test_x` NO resuelve — ver CONTRIBUTING.md.)

Dos partes: `interpret()` es una función pura (sin base), y `respond()`
se prueba contra una base dedicada `sana_turnos_test_conf` (mismo patrón que
test_cola_dia.py) para no pisar las otras suites.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
_env.process_set(MAIL_ENABLED="0")  # cancelar dispara el mail al paciente

from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.notifications import confirmations   # noqa: E402

_ART = timezone(timedelta(hours=-3))

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_conf", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


class InterpretarTestCase(unittest.TestCase):
    """Función pura: qué mensajes son una respuesta al recordatorio."""

    def test_si_en_todas_sus_formas(self):
        for t in ("sí", "Si", "SÍ!", "dale", "ok", "listo", "confirmo",
                  "si gracias", "ahí voy", "si, dale"):
            self.assertEqual(confirmations.interpret(t), "si", t)

    def test_no_en_todas_sus_formas(self):
        for t in ("no", "No.", "no puedo", "no voy a poder", "cancelar",
                  "no gracias", "no voy a ir"):
            self.assertEqual(confirmations.interpret(t), "no", t)

    def test_una_conversacion_no_es_una_respuesta(self):
        # Estas las tiene que atender el motor de chat, no este atajo: hay una
        # pregunta adentro que un "sí/no" registrado dejaría sin contestar.
        for t in ("no sé si voy a llegar, ¿puedo ir más tarde?",
                  "hola, quería saber si tengo turno para mañana",
                  "sí, pero ¿puedo cambiarlo para la tarde?",
                  "no me acuerdo a qué hora era el turno",
                  ""):
            self.assertIsNone(confirmations.interpret(t), t)

    def test_no_confunde_palabras_que_empiezan_igual(self):
        for t in ("siempre", "nosotros", "nomas"):
            self.assertIsNone(confirmations.interpret(t), t)


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS", "1") == "1", "DB tests off")
class ResponderTestCase(unittest.TestCase):

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")

    def _turno(self, dni="30111222", dias=1):
        sid = db_booking.create_booking_request(dni, "Rosa", "Vázquez", "1970-05-05",
                                 "2254551222", "serv-cardio", "centro-a")
        db_booking.approve(sid, datetime.now(_ART) + timedelta(days=dias))
        return sid

    def test_un_si_queda_guardado_en_el_turno(self):
        sid = self._turno()
        msg = confirmations.respond({"dni": "30111222"}, "sí")
        self.assertIsNotNone(msg)
        self.assertIn("confirmado", msg.lower())
        self.assertEqual(db_booking.get_booking_request(sid)["confirmacion"], "si")

    def test_un_no_libera_el_turno(self):
        # El recordatorio promete literalmente "NO para liberar el turno":
        # queda registrado Y cancelado, no sólo anotado.
        sid = self._turno()
        msg = confirmations.respond({"dni": "30111222"}, "no puedo")
        self.assertIsNotNone(msg)
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["confirmacion"], "no")
        self.assertEqual(row["estado"], "cancelada")

    def test_sin_turno_pendiente_contesta_el_motor(self):
        # Nadie tiene turno: un "sí" suelto NO lo resuelve este atajo.
        self.assertIsNone(confirmations.respond({"dni": "30111222"}, "sí"))

    def test_un_turno_pasado_no_se_confirma(self):
        sid = self._turno(dias=-2)
        self.assertIsNone(confirmations.respond({"dni": "30111222"}, "sí"))
        self.assertIsNone(db_booking.get_booking_request(sid)["confirmacion"])

    def test_no_se_confirma_dos_veces(self):
        sid = self._turno()
        self.assertIsNotNone(confirmations.respond({"dni": "30111222"}, "sí"))
        # el segundo "sí" ya no tiene qué contestar: sigue hacia el motor
        self.assertIsNone(confirmations.respond({"dni": "30111222"}, "sí"))
        self.assertEqual(db_booking.get_booking_request(sid)["confirmacion"], "si")

    def test_el_adulto_confirma_el_turno_del_chico(self):
        # La madre contesta desde SU teléfono por el turno del hijo.
        sid = db_booking.create_booking_request("55111222", "Juan", "Vázquez", "2015-03-14",
                                 "2254551222", "serv-cardio", "centro-a",
                                 solicitante_dni="30111222")
        db_booking.approve(sid, datetime.now(_ART) + timedelta(days=1))
        self.assertIsNotNone(confirmations.respond({"dni": "30111222"}, "dale"))
        self.assertEqual(db_booking.get_booking_request(sid)["confirmacion"], "si")


if __name__ == "__main__":
    unittest.main()
