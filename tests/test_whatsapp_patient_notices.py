"""WhatsApp notices the hospital starts: the confirmation with every detail
of the appointment and the day-before reminder (the product owner, 2026-10-21, q708829).

What is pinned here:
- off unless the installation turns them on (`TURNOS_WA_NOTIFY`), which is
  how Pinamar runs until it has its own number and the municipality's
  consent — and then zero network, not just "no message";
- `TURNOS_WA_TEST_RECIPIENTS` restricts them to those phones, and in the
  demo the list is mandatory (its patients are fictional; an invented number
  can be a real person's) and cannot be widened from the public admin;
- by Meta they go as the approved template, its eight parameters in order,
  never empty and never with a line break (Meta rejects both);
- the confirmation carries who, when, where, what to bring and the link to
  cancel or change it, and goes out even when the account has no email;
- a quick-reply tap on the reminder arrives as an answer to it.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app.core import db_booking  # noqa: E402
from app.notifications import otp_send  # noqa: E402
from app.notifications import send as notifications  # noqa: E402
from app.notifications import confirmations  # noqa: E402
from app.ops import demo_installation  # noqa: E402
from app.modules.turnos import prep_model  # noqa: E402,F401 — registers itself in send.py

_ENV_KEYS = ("TURNOS_WA_NOTIFY", "TURNOS_WA_TEST_RECIPIENTS", "TURNOS_DEMO_MODE",
             "TURNOS_WA_PROVIDER", "META_WA_TOKEN", "META_WA_PHONE_NUMBER_ID",
             "TURNOS_PUBLIC_ORIGIN", "TURNOS_REMINDERS_OFF")
_TEL = "+54 2254 40-1234"
_TEL_WA = "5492254401234"


class _Env(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, dict.fromkeys(_ENV_KEYS))
        p = mock.patch.object(config, "_db_overrides", lambda: {})
        p.start()
        self.addCleanup(p.stop)


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"messages":[{"id":"wamid.TEST"}]}'


class TestGate(_Env):
    def test_off_by_default(self):
        self.assertFalse(otp_send.notificacion_permitida(_TEL))

    def test_on_reaches_any_phone_outside_the_demo(self):
        _env.set_env(self, TURNOS_WA_NOTIFY="1")
        self.assertTrue(otp_send.notificacion_permitida(_TEL))

    def test_test_recipients_restrict_by_normalized_phone(self):
        _env.set_env(self, TURNOS_WA_NOTIFY="1", TURNOS_WA_TEST_RECIPIENTS="2254 40-1234, +1 555 000 1111")
        self.assertTrue(otp_send.notificacion_permitida(_TEL))
        self.assertFalse(otp_send.notificacion_permitida("+54 2254 40-9999"))

    def test_demo_without_list_sends_nothing(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1", TURNOS_WA_NOTIFY="1")
        self.assertFalse(otp_send.notificacion_permitida(_TEL))

    def test_demo_with_list_reaches_only_the_list(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1", TURNOS_WA_NOTIFY="1",
                     TURNOS_WA_TEST_RECIPIENTS=_TEL)
        self.assertTrue(otp_send.notificacion_permitida(_TEL))
        self.assertFalse(otp_send.notificacion_permitida("+54 9 11 5555 8888"))

    def test_demo_list_cannot_be_widened_from_settings(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1", TURNOS_WA_NOTIFY="1",
                     TURNOS_WA_TEST_RECIPIENTS=_TEL)
        with mock.patch.object(config, "_db_overrides", lambda: {
                "TURNOS_WA_TEST_RECIPIENTS": "+54 9 11 5555 8888",
                "TURNOS_WA_NOTIFY": "1"}):
            self.assertFalse(otp_send.notificacion_permitida("+54 9 11 5555 8888"))
            self.assertTrue(otp_send.notificacion_permitida(_TEL))

    def test_demo_runs_reminders_only_with_notices_and_list(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        self.assertEqual(demo_installation.locked_value("TURNOS_REMINDERS_OFF"), "1")
        _env.set_env(self, TURNOS_WA_NOTIFY="1")
        self.assertEqual(demo_installation.locked_value("TURNOS_REMINDERS_OFF"), "1")
        _env.set_env(self, TURNOS_WA_TEST_RECIPIENTS=_TEL)
        self.assertEqual(demo_installation.locked_value("TURNOS_REMINDERS_OFF"), "0")
        # Its email half stays silent.
        self.assertEqual(demo_installation.locked_value("MAIL_ENABLED"), "0")


class TestTemplateTransport(_Env):
    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_WA_NOTIFY="1", TURNOS_WA_PROVIDER="meta",
                     META_WA_TOKEN="tok-test", META_WA_PHONE_NUMBER_ID="123")

    def _send(self, *args):
        bodies = []

        def fake(req, timeout=None):
            bodies.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            ok = otp_send.enviar_notificacion(*args)
        return ok, bodies

    def test_meta_sends_the_template_with_clean_parameters(self):
        ok, bodies = self._send(_TEL, "texto libre", "turnos_confirmacion",
                                ["Juana Pérez", "Cardiología", "", "Hospital\n(Av. Shaw 255)"])
        self.assertTrue(ok)
        self.assertEqual(len(bodies), 1)
        b = bodies[0]
        self.assertEqual(b["type"], "template")
        self.assertEqual(b["to"], _TEL_WA)
        self.assertEqual(b["template"]["name"], "turnos_confirmacion")
        self.assertEqual(b["template"]["language"]["code"], "es_AR")
        params = [p["text"] for p in b["template"]["components"][0]["parameters"]]
        self.assertEqual(params, ["Juana Pérez", "Cardiología", "-", "Hospital (Av. Shaw 255)"])

    def test_off_means_zero_network(self):
        _env.set_env(self, TURNOS_WA_NOTIFY=None)
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("transporte prohibido")):
            self.assertFalse(otp_send.enviar_notificacion(_TEL, "x", "turnos_confirmacion", ["a"]))

    def test_demo_sends_to_the_test_phone(self):
        # enviar_texto stays blocked in the demo; the notice path is allowed
        # by the test list alone.
        _env.set_env(self, TURNOS_DEMO_MODE="1", TURNOS_WA_TEST_RECIPIENTS=_TEL)
        ok, bodies = self._send(_TEL, "x", "turnos_recordatorio", ["a"])
        self.assertTrue(ok)
        self.assertEqual(bodies[0]["template"]["name"], "turnos_recordatorio")
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("transporte prohibido")):
            self.assertFalse(otp_send.enviar_texto(_TEL, "hola"))


class TestConfirmation(_Env):
    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_WA_NOTIFY="1", TURNOS_PUBLIC_ORIGIN="https://demo.humanrounds.org")
        self.calls = []
        for name, value in (
                ("enviar_notificacion",
                 lambda tel, txt, plantilla=None, params=None:
                 self.calls.append((tel, txt, plantilla, params)) or True),):
            p = mock.patch.object(otp_send, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (
                ("_service_name", lambda sid: "Laboratorio"),
                ("_center", lambda slug: {"name": "Hospital Comunitario de Pinamar",
                                          "address": "Av. Shaw 255"}),
                ("_destination_email", lambda row: None),
                ("_destination_lang", lambda row: "es"),
                ("_entrevista_cta", lambda row, handler=None: None)):
            p = mock.patch.object(notifications, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(db_booking, "booking_request_confirm_token", lambda sid: "tok")
        p.start()
        self.addCleanup(p.stop)

    def _row(self, **extra):
        row = {
            "id": 41, "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "contacto": _TEL,
            "servicio_id": "laboratorio", "centro_slug": "hospital",
            "turno_dt": datetime(2026, 10, 28, 12, 30, tzinfo=timezone.utc),
            "medico_asignado": "Dra. Ana González", "orden_path": "/x/41.jpg",
            "ai_json": {"preparacion": [{
                "nombre": "Análisis de sangre",
                "ayuno": {"required": True, "horas": 8, "agua_permitida": True},
                "traer": ["Orden médica", "DNI", "Estudios anteriores"],
            }]},
        }
        row.update(extra)
        return row

    def test_confirmation_carries_the_whole_appointment(self):
        notifications.notify_approved(self._row())
        self.assertEqual(len(self.calls), 1)
        tel, txt, plantilla, params = self.calls[0]
        self.assertEqual(tel, "542254401234")
        self.assertEqual(plantilla, "turnos_confirmacion")
        self.assertEqual(params, [
            "Juana Pérez",
            "Laboratorio",
            "Miércoles 28 de octubre, 09:30",
            "Hospital Comunitario de Pinamar (Av. Shaw 255)",
            "Dra. Ana González",
            "Tu DNI, la orden médica y estudios anteriores",
            "Ayuno de 8 horas. Podés tomar agua.",
            "https://demo.humanrounds.org/my-requests",
        ])
        for value in params:
            self.assertIn(value, txt)
        self.assertIn("quedó confirmado", txt)

    def test_doctor_by_id_when_the_booking_carries_only_the_id(self):
        from app.patient_web import catalog
        with mock.patch.object(catalog, "doctors",
                               lambda: [{"id": "m7", "nombre": "Carlos Wu"}]):
            notifications.notify_approved(self._row(medico_asignado=None,
                                                    medico_asignado_id="m7"))
        self.assertEqual(self.calls[0][3][4], "Carlos Wu")

    def test_no_value_is_ever_empty(self):
        notifications.notify_approved(self._row(
            nombre=None, apellido=None, medico_asignado=None, orden_path=None,
            ai_json={}))
        params = self.calls[0][3]
        self.assertTrue(all(str(v).strip() for v in params))
        self.assertEqual(params[4], "el equipo de Laboratorio")
        self.assertEqual(params[6], "No necesita preparación especial.")

    def test_no_appointment_time_no_confirmation(self):
        notifications.notify_approved(self._row(turno_dt=None))
        self.assertEqual(self.calls, [])

    def test_off_sends_nothing_and_the_email_still_goes(self):
        _env.set_env(self, TURNOS_WA_NOTIFY=None)
        sent = []
        with mock.patch.object(notifications, "_destination_email", lambda row: "p@example.com"), \
                mock.patch.object(notifications.mail, "send",
                                  lambda *a, **k: sent.append(a) or True):
            notifications.notify_approved(self._row())
        self.assertEqual(self.calls, [])
        self.assertEqual(len(sent), 1)


class TestQuickReply(unittest.TestCase):
    def test_button_tap_arrives_as_its_text(self):
        from app.modules.chat import whatsapp_chat
        body = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "messages": [{"from": _TEL_WA, "type": "button",
                          "button": {"text": "No puedo ir", "payload": "No puedo ir"}}]}}]}]}
        msgs = whatsapp_chat._normalizar_meta(body)
        self.assertEqual(msgs[0]["body"], "No puedo ir")

    def test_both_buttons_are_unambiguous_answers(self):
        self.assertEqual(confirmations.interpret("Sí, voy"), "si")
        self.assertEqual(confirmations.interpret("No puedo ir"), "no")


if __name__ == "__main__":
    unittest.main()
