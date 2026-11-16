"""test_appointment_confirm.py — el "¿vas a poder venir?" de un clic, la puerta
por email (2026-08-02; copy + CTA de la entrevista previa, 2026-08-03).

Correr: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_appointment_confirm.py"
(OJO: `python3 -m unittest test.test_x` NO resuelve — ver CONTRIBUTING.md.)

Base dedicada (`sana_turnos_test_confirm`, mismo patrón que
test_confirmations.py) para no pisar las otras suites. Se conduce por el
contrato de ruta nativo (TestClient contra la composición real, mismo patrón
que test_appointment_confirm_router_native.py): lo que se prueba es el HTML
que se arma, no el transporte.
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
from app.core import db_clinical  # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_ART = timezone(timedelta(hours=-3))

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_confirm", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS", "1") == "1", "DB tests off")
class ConfirmarPorEmailTestCase(unittest.TestCase):

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
        self.client = TestClient(app_module.app)

    def _turno(self, dias=1):
        sid = db_booking.create_booking_request("30111222", "Rosa", "Vázquez", "1970-05-05",
                                        "2254551222", "serv-cardio", "centro-a")
        db_booking.approve(sid, datetime.now(_ART) + timedelta(days=dias))
        return sid, db_booking.booking_request_confirm_token(sid)

    def _get(self, token, query=""):
        r = self.client.get(f"/appointment/confirm/{token}{query}")
        self.assertEqual(r.status_code, 200)
        return r.text

    def _post(self, token, valor):
        return self.client.post(f"/api/confirm/{token}/{valor}")

    # ---- el token ---------------------------------------------------------

    def test_el_token_es_estable(self):
        # Reenviar el recordatorio no puede invalidar el link que el paciente
        # ya tiene en la bandeja.
        sid, token = self._turno()
        self.assertTrue(token)
        self.assertEqual(db_booking.booking_request_confirm_token(sid), token)
        self.assertEqual(db_booking.booking_request_by_confirm_token(token)["id"], sid)

    def test_token_desconocido_no_es_un_callejon(self):
        html = self._get("no-existe-este-token")
        self.assertIn("Este link ya no sirve", html)
        self.assertIn("Todos mis turnos", html)

    # ---- la pregunta ------------------------------------------------------

    def test_la_pagina_pregunta_y_ofrece_las_dos_respuestas(self):
        _sid, token = self._turno()
        html = self._get(token)
        self.assertIn("¿Vas a poder venir?", html)
        self.assertIn('data-confirm-action="si"', html)
        self.assertIn('data-confirm-action="no"', html)
        self.assertIn(token, html)

    def test_r_no_pregunta_antes_de_liberar(self):
        # El clic que regala el turno se pregunta una vez: no se auto-resuelve.
        _sid, token = self._turno()
        html = self._get(token, "?r=no")
        self.assertIn("¿Liberamos tu turno?", html)
        self.assertNotIn('data-auto="si"', html)

    def test_r_si_resuelve_solo_en_el_cliente(self):
        _sid, token = self._turno()
        html = self._get(token, "?r=si")
        self.assertIn('data-auto="si"', html)

    def test_abrir_el_link_no_confirma_nada(self):
        # Un escáner de links (el cliente de correo, un filtro corporativo) abre
        # todas las URLs del email. El GET NUNCA cambia el estado: si lo hiciera,
        # un escáner podría liberar un turno que nadie liberó.
        sid, token = self._turno()
        self._get(token, "?r=si")
        self._get(token, "?r=no")
        row = db_booking.get_booking_request(sid)
        self.assertIsNone(row["confirmacion"])
        self.assertEqual(row["estado"], "aprobada")

    # ---- la respuesta -----------------------------------------------------

    def test_un_si_queda_guardado_con_canal_email(self):
        sid, token = self._turno()
        h = self._post(token, "si")
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        self.assertIn("confirmado", h.json()["mensaje"].lower())
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["confirmacion"], "si")
        self.assertEqual(row["confirmacion_canal"], "email")
        self.assertEqual(row["estado"], "aprobada")

    def test_un_no_libera_el_turno(self):
        # Es literalmente lo que promete el email: el turno queda disponible.
        sid, token = self._turno()
        h = self._post(token, "no")
        self.assertEqual(h.status_code, 200)
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["confirmacion"], "no")
        self.assertEqual(row["estado"], "cancelada")

    def test_quien_ya_confirmo_puede_cambiar_de_idea(self):
        # Confirmó y después no puede venir: el mismo link tiene que servir,
        # que es todo el punto de preguntar.
        sid, token = self._turno()
        self._post(token, "si")
        html = self._get(token)
        self.assertIn("Confirmado: te esperamos", html)
        self.assertIn('data-confirm-action="no"', html)
        self._post(token, "no")
        self.assertEqual(db_booking.get_booking_request(sid)["estado"], "cancelada")

    def test_un_turno_pasado_no_se_confirma(self):
        sid, token = self._turno(dias=-2)
        h = self._post(token, "si")
        self.assertEqual(h.status_code, 410)
        self.assertIsNone(db_booking.get_booking_request(sid)["confirmacion"])
        self.assertIn("Este link ya no sirve", self._get(token))

    def test_un_turno_cancelado_no_pide_confirmacion(self):
        sid, token = self._turno()
        db_booking.cancel(sid)
        self.assertIn("está cancelado", self._get(token))
        self.assertEqual(self._post(token, "si").status_code, 410)

    def test_token_invalido_en_el_post(self):
        self.assertEqual(self._post("no-existe", "si").status_code, 404)

    def test_el_barrido_trae_lo_que_hace_falta_para_preguntar(self):
        # Regresión: booking_requests_to_remind no traía `contact`, así que
        # notifications._whatsapp_reminder leía un contacto vacío y el mensaje
        # del día previo NUNCA salía, ni con proveedor configurado. El token va
        # por el mismo motivo: el link del email se arma desde esta fila.
        sid, _token = self._turno()
        manana = (datetime.now(_ART) + timedelta(days=1)).date()
        filas = [f for f in db_booking.booking_requests_to_remind(manana) if f["id"] == sid]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["contacto"], "2254551222")
        self.assertIn("confirm_token", filas[0])


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS", "1") == "1", "DB tests off")
class ConfirmarInterviewCtaTestCase(ConfirmarPorEmailTestCase):
    """El "Contale al médico cómo te sentís" en la página ya-confirmada
    (2026-08-03): botón grande si la entrevista está pendiente, link
    discreto de actualizar si ya está 'done', nada si intake no aplica.
    Mismo patrón de monkeypatch que TestEntrevistaCtaEnEmails en
    test_notifications.py — no depende del estado real del módulo intake,
    así la prueba es determinística."""

    def setUp(self):
        super().setUp()
        from app.modules.turnos import booking_requests
        self._bookreq = booking_requests
        self._orig_campo = booking_requests._entrevista_field
        self._orig_token = booking_requests._entrevista_patient_token
        self._orig_interview = db_clinical.interview_for_request

    def tearDown(self):
        self._bookreq._entrevista_field = self._orig_campo
        self._bookreq._entrevista_patient_token = self._orig_token
        db_clinical.interview_for_request = self._orig_interview
        super().tearDown()

    def test_entrevista_pendiente_muestra_boton_primario_con_token(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        self._bookreq._entrevista_patient_token = lambda sid: "tok-abc"
        db_clinical.interview_for_request = lambda sid: None
        sid, token = self._turno()
        self._post(token, "si")
        html = self._get(token)
        self.assertIn(f"/entrevista/{sid}?t=tok-abc", html)
        self.assertIn("Contale al médico cómo te sentís", html)
        self.assertIn('class="mp-cta"', html)

    def test_entrevista_completada_muestra_link_de_actualizar(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        self._bookreq._entrevista_patient_token = lambda sid: "tok-abc"
        db_clinical.interview_for_request = lambda sid: {"status": "done"}
        sid, token = self._turno()
        self._post(token, "si")
        html = self._get(token)
        self.assertIn(f"/entrevista/{sid}?t=tok-abc", html)
        self.assertIn("Actualizá lo que le contaste al médico", html)
        self.assertNotIn("Contale al médico cómo te sentís", html)

    def test_intake_apagado_no_muestra_cta(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: None
        sid, token = self._turno()
        self._post(token, "si")
        html = self._get(token)
        self.assertNotIn("/entrevista/", html)
        self.assertNotIn("Contale al médico cómo te sentís", html)
        self.assertNotIn("Actualizá lo que le contaste al médico", html)


if __name__ == "__main__":
    unittest.main()
