"""test_notifications.py — pruebas de notifications.py y reminders.py.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"

Sin Postgres real: db_accounts.get_user / db_booking.booking_requests_to_remind /
db_booking.claim_reminder / db_booking.claim_interview_reminder se monkeypatchean.
email_mailgun.send se monkeypatchea para capturar (to, subject, body) en vez
de pegarle a la red.
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.notifications import mailgun as email_mailgun    # noqa: E402
from app.notifications import send as notifications    # noqa: E402
from app.notifications import reminders    # noqa: E402

_ART = timezone(timedelta(hours=-3))

# Mail carrying a loopback link is refused (notifications/mail.py), so the
# module runs as a configured installation; TestMailWithoutPublicOrigin
# overrides it where the point is the missing origin.
def setUpModule():
    _env.set_env_for_module(TURNOS_PUBLIC_ORIGIN="https://example.health.invalid")


class _CapturedSend:
    """Reemplaza email_mailgun.send: guarda cada llamada, no pega a la red.
    Captura también `html` (2026-08-01, HTML layer): cada llamada queda
    como (to, subject, text, html)."""

    def __init__(self):
        self.calls = []

    def __call__(self, to, subject, text, html=None):
        self.calls.append((to, subject, text, html))
        return True


class SendCaptureMixin:
    def setUp(self):
        super().setUp()
        self._orig_send = email_mailgun.send
        self.sent = _CapturedSend()
        email_mailgun.send = self.sent
        self._orig_get_usuario = db_accounts.get_user
        db_accounts.get_user = lambda dni: self._usuario(dni)
        self._usuarios = {}

    def tearDown(self):
        email_mailgun.send = self._orig_send
        db_accounts.get_user = self._orig_get_usuario
        super().tearDown()

    def _usuario(self, dni):
        return self._usuarios.get(dni)

    def con_email(self, dni, email):
        self._usuarios[dni] = {"dni": dni, "email": email}


class TestPrepLineas(unittest.TestCase):
    def test_vacio(self):
        self.assertEqual(notifications._prep_lines([]), [])
        self.assertEqual(notifications._prep_lines(None), [])

    def test_ayuno_y_traer(self):
        prep = [{
            "nombre": "Análisis de sangre / laboratorio",
            "ayuno": {"required": True, "horas": 8, "agua_permitida": True,
                      "nota": "Confirmá en el consultorio 5."},
            "preparacion": ["No fumes durante el ayuno."],
            "traer": ["Orden médica", "DNI"],
            "sin_preparacion": False,
        }]
        lineas = notifications._prep_lines(prep)
        texto = "\n".join(lineas)
        self.assertIn("Cómo prepararte:", texto)
        self.assertIn("Ayuno de 8 horas.", texto)
        self.assertIn("Podés tomar agua.", texto)
        self.assertIn("Confirmá en el consultorio 5.", texto)
        self.assertIn("No fumes durante el ayuno.", texto)
        # Desde 2026-07-29 el email tiene las MISMAS secciones que la web
        # (prep_model.py) y "qué llevar" es una lista de verdad, uno por
        # línea: "Llevá: Orden médica, DNI" en una sola línea era justo lo que
        # ui-rules prohíbe en pantalla y se leía igual de mal en un mail.
        self.assertIn("Qué llevar:", texto)
        self.assertIn("- Orden médica", texto)
        self.assertIn("- DNI", texto)
        self.assertIn("Antes de venir:", texto)
        # single estudio -> sin encabezado "Para <nombre>:"
        self.assertNotIn("Para Análisis", texto)

    def test_sin_preparacion(self):
        prep = [{"nombre": "Audiometría", "ayuno": {"required": False},
                 "preparacion": [], "traer": [], "sin_preparacion": True}]
        lineas = notifications._prep_lines(prep)
        self.assertIn("- No necesita preparación especial.", lineas)

    def test_multi_estudio_encabeza_cada_uno(self):
        prep = [
            {"nombre": "Glucemia", "ayuno": {"required": True, "horas": 8},
             "preparacion": [], "traer": [], "sin_preparacion": False},
            {"nombre": "Audiometría", "ayuno": {"required": False},
             "preparacion": ["Evitá ruidos fuertes."], "traer": [], "sin_preparacion": True},
        ]
        lineas = notifications._prep_lines(prep)
        texto = "\n".join(lineas)
        self.assertIn("Para Glucemia:", texto)
        self.assertIn("Para Audiometría:", texto)


class TestNotificationOrigin(unittest.TestCase):
    def test_background_links_use_the_configured_public_origin(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="https://example.health.invalid/")
        self.assertEqual(
            notifications._base_url(None),
            "https://example.health.invalid",
        )


class TestMailWithoutPublicOrigin(unittest.TestCase):
    """Without TURNOS_PUBLIC_ORIGIN every link falls back to the loopback
    origin; a message carrying one must not leave the machine."""

    class _Adapter:
        def __init__(self):
            self.calls = []

        def send(self, to, subject, text, html=None):
            self.calls.append((to, subject, text, html))
            return True

    def setUp(self):
        from app.notifications import mail
        from app.core import config
        self.mail = mail
        self.adapter = self._Adapter()
        self._orig_transport = mail.transport
        mail.transport = lambda: (self.adapter, "fake", "explicit")
        self._orig_get = config.get
        self.origin = None
        config.get = lambda key, default=None: (
            self.origin if key == "TURNOS_PUBLIC_ORIGIN" and self.origin is not None
            else self._orig_get(key, default) if key != "TURNOS_PUBLIC_ORIGIN" else default)
        self._config = config

    def tearDown(self):
        self.mail.transport = self._orig_transport
        self._config.get = self._orig_get

    def test_without_origin_a_mail_with_a_link_is_not_sent(self):
        from app.patient_web import seo
        link = seo.DEFAULT_ORIGIN + "/admin/reset-password?token=abc"
        self.assertFalse(self.mail.send("a@example.com", "Asunto", "Entrá en " + link))
        self.assertFalse(self.mail.send("a@example.com", "Asunto", "Hola", html=f'<a href="{link}">x</a>'))
        self.assertEqual(self.adapter.calls, [])

    def test_without_origin_a_mail_without_a_link_still_goes(self):
        self.assertTrue(self.mail.send("a@example.com", "Asunto", "Hola"))
        self.assertEqual(len(self.adapter.calls), 1)

    def test_with_a_public_origin_the_link_goes(self):
        self.origin = "https://example.health.invalid"
        text = "Entrá en https://example.health.invalid/admin/reset-password?token=abc"
        self.assertTrue(self.mail.send("a@example.com", "Asunto", text))
        self.assertEqual(len(self.adapter.calls), 1)

    def test_an_origin_set_to_loopback_on_purpose_still_goes(self):
        # a local receiver (the browser suites' sink) sets the loopback origin explicitly
        from app.patient_web import seo
        self.origin = seo.DEFAULT_ORIGIN
        link = seo.DEFAULT_ORIGIN + "/admin/reset-password?token=abc"
        self.assertTrue(self.mail.send("a@example.com", "Asunto", "Entrá en " + link))
        self.assertEqual(len(self.adapter.calls), 1)

    def test_the_whatsapp_registration_reply_has_no_link_without_origin(self):
        from app.modules.chat import whatsapp_chat
        msg = whatsapp_chat._mensaje_sin_cuenta()
        self.assertNotIn("127.0.0.1", msg)
        self.origin = "https://example.health.invalid"
        self.assertIn("https://example.health.invalid/account", whatsapp_chat._mensaje_sin_cuenta())


class TestNotificarRecibida(SendCaptureMixin, unittest.TestCase):
    def test_manda_con_email_en_la_cuenta(self):
        self.con_email("30111222", "paciente@example.com")
        row = {
            "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
            "centro_slug": "hospital-pinamar", "preferencia_horario": "Martes 21 de julio · 07:30",
            "ai_json": {"preparacion": []},
        }
        notifications.notify_received(row)
        self.assertEqual(len(self.sent.calls), 1)
        to, subject, body, html = self.sent.calls[0]
        self.assertEqual(to, "paciente@example.com")
        self.assertIn("Recibimos tu solicitud", subject)
        self.assertIn("Recibimos tu solicitud", body)
        self.assertIn("Martes 21 de julio", body)
        self.assertIn("revisa tu solicitud", body)
        # El HTML es la MISMA información en el layout compartido: un
        # documento completo, con el nombre del servicio adentro.
        self.assertIsNotNone(html)
        self.assertIn("<!doctype html>", html)
        self.assertIn("Recibimos tu solicitud", html)
        # El link de cuenta dice "Todos mis turnos" (2026-08-03), no
        # "Seguí el estado en Mis solicitudes".
        self.assertIn("Todos mis turnos", body)
        self.assertIn("Todos mis turnos", html)
        self.assertNotIn("Seguí el estado en Mis solicitudes", body)

    def test_skip_sin_email(self):
        self.con_email("30111222", None)  # cuenta sin email
        row = {"nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
               "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
               "centro_slug": "hospital-pinamar"}
        notifications.notify_received(row)
        self.assertEqual(self.sent.calls, [])

    def test_nunca_levanta_sin_row(self):
        notifications.notify_received(None)  # no debe lanzar
        self.assertEqual(self.sent.calls, [])


class TestNotificarAprobada(SendCaptureMixin, unittest.TestCase):
    def test_incluye_preparacion_inline(self):
        self.con_email("30111222", "paciente@example.com")
        row = {
            "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
            "centro_slug": "hospital-pinamar",
            "turno_dt": datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
            "ai_json": {"preparacion": [{
                "nombre": "Ecografía abdominal",
                "ayuno": {"required": True, "horas": 8, "agua_permitida": True, "nota": None},
                "preparacion": [], "traer": ["DNI"], "sin_preparacion": False,
            }]},
        }
        notifications.notify_approved(row)
        self.assertEqual(len(self.sent.calls), 1)
        to, subject, body, html = self.sent.calls[0]
        self.assertIn("confirmado", subject)
        self.assertIn("Cómo prepararte:", body)
        self.assertIn("Ayuno de 8 horas.", body)
        self.assertIn("Qué llevar:", body)
        self.assertIn("- DNI", body)
        self.assertIsNotNone(html)
        self.assertIn("Cómo prepararte", html)
        self.assertIn("<li", html)  # la preparación es una lista real, no '- ' de texto


class TestNotificarAprobadaAgregaCalendario(SendCaptureMixin, unittest.TestCase):
    """"Al calendario" al lado de la fecha/hora (2026-08-03) — el link de
    cuenta pasa a decir "Todos mis turnos"."""

    def setUp(self):
        super().setUp()
        self._orig_token = db_booking.booking_request_confirm_token
        db_booking.booking_request_confirm_token = lambda sid: "tok-cal"

    def tearDown(self):
        db_booking.booking_request_confirm_token = self._orig_token
        super().tearDown()

    def _row(self, **extra):
        row = {
            "id": 51, "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
            "centro_slug": "hospital-pinamar",
            "turno_dt": datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
            "ai_json": {"preparacion": []},
        }
        row.update(extra)
        return row

    def test_agrega_al_calendario_y_renombra_el_link_de_cuenta(self):
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_approved(self._row())
        _to, _asunto, texto, html = self.sent.calls[0]
        for parte in (texto, html):
            self.assertIn("/appointment/tok-cal.ics", parte)
            self.assertIn("Al calendario", parte)
            self.assertIn("Todos mis turnos", parte)
        self.assertNotIn("Detalle y calendario en Mis solicitudes", texto)

    def test_sin_token_no_rompe_el_email(self):
        db_booking.booking_request_confirm_token = lambda sid: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_approved(self._row())
        _to, _asunto, texto, _html = self.sent.calls[0]
        self.assertNotIn("/appointment/", texto)
        self.assertIn("confirmado", texto)


class TestNotificarAprobadaNombraAlMedico(SendCaptureMixin, unittest.TestCase):
    """El mail de «turno aprobado» dice quién atiende, con la misma línea que
    el recordatorio del día anterior (hallazgo de la repasada del 28.10)."""

    def _row(self, **extra):
        row = {
            "id": 52, "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
            "centro_slug": "hospital-pinamar",
            "turno_dt": datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
            "ai_json": {"preparacion": []},
        }
        row.update(extra)
        return row

    def test_aprobada_nombra_al_medico_en_texto_y_html(self):
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_approved(self._row(medico_asignado="Dra. Ana Gómez"))
        _to, _asunto, texto, html = self.sent.calls[0]
        for parte in (texto, html):
            self.assertIn("Te atiende: Dra. Ana Gómez.", parte)

    def test_aprobada_sin_medico_no_inventa_la_linea(self):
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_approved(self._row(medico_asignado=None))
        _to, _asunto, texto, html = self.sent.calls[0]
        self.assertNotIn("Te atiende", texto)
        self.assertNotIn("Te atiende", html)

    def test_aprobada_y_recordatorio_dicen_lo_mismo(self):
        self.con_email("30111222", "paciente@example.com")
        row = self._row(medico_asignado="Dra. Ana Gómez")
        notifications.notify_approved(row)
        notifications.notify_reminder(row)
        linea = "Te atiende: Dra. Ana Gómez."
        self.assertIn(linea, self.sent.calls[0][2])
        self.assertIn(linea, self.sent.calls[1][2])


class TestRecordatorioTick(unittest.TestCase):
    def setUp(self):
        self._orig_send = email_mailgun.send
        self.sent = _CapturedSend()
        email_mailgun.send = self.sent
        self._orig_get_usuario = db_accounts.get_user
        db_accounts.get_user = lambda dni: {"dni": dni, "email": "paciente@example.com"}
        self._orig_para_recordar = db_booking.booking_requests_to_remind
        self._orig_reclamar = db_booking.claim_reminder
        self._orig_sin_nada = db_booking.mark_reminder_skipped
        self.marcadas = []
        self.sin_nada = []

        def fake_para_recordar(fecha):
            self._fecha_pedida = fecha
            return self._filas

        def fake_reclamar(sid):
            # Reclamo siempre gana en estos tests (no hay carrera acá — la
            # carrera del claim-CAS real se prueba en test_reminders.py con
            # Postgres real); esto sólo simula "reminder_at estaba NULL".
            self.marcadas.append(sid)
            return True

        db_booking.booking_requests_to_remind = fake_para_recordar
        db_booking.claim_reminder = fake_reclamar
        db_booking.mark_reminder_skipped = self.sin_nada.append
        self._filas = []

    def tearDown(self):
        email_mailgun.send = self._orig_send
        db_accounts.get_user = self._orig_get_usuario
        db_booking.booking_requests_to_remind = self._orig_para_recordar
        db_booking.claim_reminder = self._orig_reclamar
        db_booking.mark_reminder_skipped = self._orig_sin_nada

    def _row(self, sid=1, con_email=True):
        return {
            "id": sid, "nombre": "Juana", "apellido": "Pérez",
            "dni": "30111222", "solicitante_dni": "30111222",
            "servicio_id": "eco-abdominal", "centro_slug": "hospital-pinamar",
            "turno_dt": datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc),
            "medico_asignado": None, "ai_json": {"preparacion": []},
        }

    def test_fuera_de_ventana_no_manda(self):
        self._filas = [self._row()]
        # 03:00 ART -> fuera de la ventana 09-21
        now = datetime(2026, 7, 16, 3, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick(now)
        self.assertEqual(n, 0)
        self.assertEqual(self.sent.calls, [])
        self.assertEqual(self.marcadas, [])

    def test_dentro_de_ventana_manda_y_marca(self):
        self._filas = [self._row(sid=42)]
        # 10:00 ART -> dentro de la ventana
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick(now)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.sent.calls), 1)
        self.assertEqual(self.marcadas, [42])
        # pidió el barrido de "mañana" en ART
        self.assertEqual(self._fecha_pedida, (now.astimezone(_ART) + timedelta(days=1)).date())

    def test_fila_sin_email_igual_se_marca(self):
        db_accounts.get_user = lambda dni: {"dni": dni, "email": None}
        self._filas = [self._row(sid=7)]
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick(now)
        # nothing went out (the product owner's hub, 2026-11-10): the claim stays, marked as nothing to send, not counted
        self.assertEqual(n, 0)
        self.assertEqual(self.sent.calls, [])
        self.assertEqual(self.marcadas, [7])
        self.assertEqual(self.sin_nada, [7])

    def test_reclamo_perdido_no_manda_ni_cuenta(self):
        # data-integrity guards (2026-08-05): si otro tick/proceso ya
        # reclamó la fila (reminder_at dejó de ser NULL), claim_reminder
        # devuelve False -> ni se manda el mail ni se suma a `procesadas`.
        db_booking.claim_reminder = lambda sid: False
        self._filas = [self._row(sid=13)]
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick(now)
        self.assertEqual(n, 0)
        self.assertEqual(self.sent.calls, [])
        self.assertEqual(self.marcadas, [])

    # --- bordes de la ventana horaria (la conversión a ART es el hotspot) ---

    def _corre(self, now_utc):
        self._filas = [self._row(sid=99)]
        return reminders.tick(now_utc)

    def test_ventana_usa_hora_art_no_utc_de_madrugada(self):
        # 10:00 UTC = 07:00 ART -> FUERA. Si mirara la hora UTC (10, dentro)
        # mandaría un recordatorio a las 7 de la mañana ART. Prueba que la
        # ventana se evalúa en ART, no en UTC.
        now = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(self._corre(now), 0)
        self.assertEqual(self.marcadas, [])

    def test_ventana_usa_hora_art_no_utc_de_noche(self):
        # 23:30 UTC = 20:30 ART -> DENTRO. Si mirara la hora UTC (23, fuera)
        # se saltaría un recordatorio válido.
        now = datetime(2026, 7, 16, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(self._corre(now), 1)
        self.assertEqual(self.marcadas, [99])

    def test_borde_inferior_0900_incluido(self):
        now = datetime(2026, 7, 16, 9, 0, tzinfo=_ART).astimezone(timezone.utc)
        self.assertEqual(self._corre(now), 1)

    def test_borde_inferior_0859_excluido(self):
        now = datetime(2026, 7, 16, 8, 59, tzinfo=_ART).astimezone(timezone.utc)
        self.assertEqual(self._corre(now), 0)

    def test_borde_superior_2100_excluido(self):
        # 21:00 exacto queda fuera (< 21, exclusivo)
        now = datetime(2026, 7, 16, 21, 0, tzinfo=_ART).astimezone(timezone.utc)
        self.assertEqual(self._corre(now), 0)

    def test_borde_superior_2059_incluido(self):
        now = datetime(2026, 7, 16, 20, 59, tzinfo=_ART).astimezone(timezone.utc)
        self.assertEqual(self._corre(now), 1)

    def test_manana_es_dia_siguiente_en_art(self):
        # 23:30 UTC del 16 = 20:30 ART del 16 -> "mañana" ART = 17, NO el 18
        # (que sería el error si computara mañana desde la fecha UTC del 17).
        now = datetime(2026, 7, 16, 23, 30, tzinfo=timezone.utc)
        self._corre(now)
        self.assertEqual(self._fecha_pedida, datetime(2026, 7, 17).date())


if __name__ == "__main__":
    unittest.main()


class TestRecordatorioWhatsApp(SendCaptureMixin, unittest.TestCase):
    """Recordatorio del día previo también por WhatsApp (reunión hospital
    2026-07-24): con los avisos encendidos (TURNOS_WA_NOTIFY) manda la
    plantilla con el turno entero y la pregunta SÍ/NO; apagados (el default,
    Pinamar hoy) es un no-op; un contacto que no es teléfono se saltea."""

    def setUp(self):
        super().setUp()
        from app.notifications import otp_send
        self._otp = otp_send
        self._orig_notif = otp_send.enviar_notificacion
        _env.set_env(self, TURNOS_WA_NOTIFY="1")
        self.wa_calls = []
        otp_send.enviar_notificacion = (
            lambda tel, txt, plantilla=None, params=None:
            self.wa_calls.append((tel, txt, plantilla, params)) or True)

    def tearDown(self):
        self._otp.enviar_notificacion = self._orig_notif
        super().tearDown()

    def _row(self):
        return {
            "id": 7, "nombre": "Juana", "apellido": "Pérez",
            "dni": "30111222", "solicitante_dni": "30111222",
            "contacto": "11-5555-8888",
            "servicio_id": "eco-abdominal", "centro_slug": "hospital-pinamar",
            "turno_dt": datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc),
            "medico_asignado": None, "ai_json": {"preparacion": []},
        }

    def test_manda_whatsapp_con_pregunta_si_no(self):
        notifications.notify_reminder(self._row())
        self.assertEqual(len(self.wa_calls), 1)
        tel, txt, plantilla, params = self.wa_calls[0]
        self.assertEqual(tel, "1155558888")
        self.assertEqual(plantilla, "turnos_recordatorio")
        self.assertEqual(len(params), 8)
        self.assertIn("mañana", txt)
        self.assertIn("Respondé SÍ", txt)
        self.assertIn("NO", txt)

    def test_avisos_apagados_no_manda(self):
        # Pinamar hoy: proveedor Meta configurado, avisos sin encender.
        _env.set_env(self, TURNOS_WA_NOTIFY=None)
        self._otp.enviar_notificacion = self._orig_notif
        from unittest import mock
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("no debe salir nada")), \
                mock.patch.object(self._otp, "wa_provider", lambda: "meta"):
            notifications.notify_reminder(self._row())

    def test_contacto_no_telefonico_se_saltea(self):
        row = self._row()
        row["contacto"] = "juana@example.com"
        notifications.notify_reminder(row)
        self.assertEqual(self.wa_calls, [])

    def test_email_sigue_saliendo_ademas_del_whatsapp(self):
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_reminder(self._row())
        self.assertEqual(len(self.sent.calls), 1)
        self.assertEqual(len(self.wa_calls), 1)

    def _account(self, **avisos):
        self._usuarios["30111222"] = {
            "dni": "30111222", "email": "paciente@example.com", "avisos": avisos or None}

    def test_whatsapp_switched_off_in_the_account_sends_no_reminder(self):
        # «Contacto y avisos»: the patient switched WhatsApp off. The email still goes.
        self._account(whatsapp=False, email=True)
        notifications.notify_reminder(self._row())
        self.assertEqual(self.wa_calls, [])
        self.assertEqual(len(self.sent.calls), 1)

    def test_whatsapp_switched_off_in_the_account_sends_no_confirmation(self):
        self._account(whatsapp=False, email=True)
        notifications.notify_approved(self._row())
        self.assertEqual(self.wa_calls, [])
        self.assertEqual(len(self.sent.calls), 1)

    def test_email_switched_off_keeps_the_whatsapp(self):
        self._account(whatsapp=True, email=False)
        notifications.notify_reminder(self._row())
        self.assertEqual(len(self.wa_calls), 1)
        self.assertEqual(self.sent.calls, [])

    def test_both_channels_switched_off_send_nothing(self):
        self._account(whatsapp=False, email=False)
        notifications.notify_reminder(self._row())
        notifications.notify_approved(self._row())
        self.assertEqual(self.wa_calls, [])
        self.assertEqual(self.sent.calls, [])

    def test_no_saved_preference_keeps_both_channels(self):
        # avisos null or without the key is not an opt-out: the confirmation goes by email AND WhatsApp.
        for avisos in ({}, {"email": True}, {"whatsapp": True}):
            self._account(**avisos)
            self.wa_calls.clear()
            self.sent.calls.clear()
            notifications.notify_approved(self._row())
            self.assertEqual(len(self.wa_calls), 1, avisos)
            self.assertEqual(len(self.sent.calls), 1, avisos)

    def test_an_unknown_account_still_gets_the_whatsapp(self):
        # A desk-created appointment for somebody with no account: nothing to opt out of.
        notifications.notify_reminder(self._row())
        self.assertEqual(len(self.wa_calls), 1)
        self.assertEqual(self.sent.calls, [])

    def test_a_failed_account_lookup_does_not_stop_the_whatsapp(self):
        def down(dni):
            raise RuntimeError("database down")
        db_accounts.get_user = down
        notifications.notify_reminder(self._row())
        self.assertEqual(len(self.wa_calls), 1)

    def test_a_dependents_appointment_follows_the_requesters_choice(self):
        # The child (dni) has no account of its own: the parent's switch decides, whoever the patient is.
        row = dict(self._row(), dni="50123456", solicitante_dni="30111222", nombre="Mateo")
        self._account(whatsapp=False, email=True)
        self._usuarios["50123456"] = {"dni": "50123456", "avisos": {"whatsapp": True}}
        notifications.notify_reminder(row)
        self.assertEqual(self.wa_calls, [])
        self.assertEqual(len(self.sent.calls), 1)

    def test_the_remedy_after_a_rejection_follows_the_whatsapp_switch(self):
        from types import SimpleNamespace
        original = notifications._turnos_remedies
        notifications._turnos_remedies = SimpleNamespace(whatsapp_text=lambda rem, paciente, servicio, lang=None: "another time: ...")
        try:
            self._account(whatsapp=False, email=True)
            notifications._whatsapp_rejection(self._row(), {"remedy": True}, "Juana", "Eco")
            self.assertEqual(self.wa_calls, [])
            self._account(whatsapp=True, email=True)
            notifications._whatsapp_rejection(self._row(), {"remedy": True}, "Juana", "Eco")
            self.assertEqual(len(self.wa_calls), 1)
        finally:
            notifications._turnos_remedies = original


class TestRecordatorioPideConfirmacion(SendCaptureMixin, unittest.TestCase):
    """El recordatorio por email PIDE confirmación (2026-08-02). Hasta hoy la
    pregunta salía sólo por WhatsApp y el número institucional de Meta sigue en
    trámite: sin estos dos links nadie contestaba nada."""

    def setUp(self):
        super().setUp()
        self._orig_token = db_booking.booking_request_confirm_token
        db_booking.booking_request_confirm_token = lambda sid: "tok-abc"

    def tearDown(self):
        db_booking.booking_request_confirm_token = self._orig_token
        super().tearDown()

    def _row(self, **extra):
        row = {
            "id": 7, "nombre": "Juana", "apellido": "Pérez",
            "dni": "30111222", "solicitante_dni": "30111222",
            "contacto": "juana@example.com",
            "servicio_id": "eco-abdominal", "centro_slug": "hospital-pinamar",
            "turno_dt": datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc),
            "medico_asignado": None, "ai_json": {"preparacion": []},
        }
        row.update(extra)
        return row

    def test_el_email_lleva_los_dos_links(self):
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_reminder(self._row())
        _to, _asunto, texto, html = self.sent.calls[0]
        for parte in (texto, html):
            self.assertIn("/appointment/confirm/tok-abc?r=si", parte)
            self.assertIn("/appointment/confirm/tok-abc?r=no", parte)
        self.assertIn("¿Vas a poder venir?", texto)
        # El sí es el botón primario y el no un link (ui-rules: un solo
        # primario por pantalla, y el que libera el turno no compite con él).
        self.assertIn("Sí, voy a ir", html)
        self.assertIn("No voy a poder ir", html)

    def test_el_email_lleva_al_calendario_y_todos_mis_turnos(self):
        # Mismo token (confirm_token) reusado para el .ics — ver
        # notifications._calendar_ics_url. El link de cuenta ya no dice
        # "Detalle en Mis solicitudes" (2026-08-03).
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_reminder(self._row())
        _to, _asunto, texto, html = self.sent.calls[0]
        for parte in (texto, html):
            self.assertIn("/appointment/tok-abc.ics", parte)
            self.assertIn("Al calendario", parte)
            self.assertIn("Todos mis turnos", parte)
        self.assertNotIn("Detalle en Mis solicitudes", texto)

    def test_si_ya_contesto_no_se_le_vuelve_a_preguntar(self):
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_reminder(self._row(confirmacion="si"))
        _to, _asunto, texto, _html = self.sent.calls[0]
        self.assertNotIn("/appointment/confirm/", texto)
        # …pero el recordatorio sale igual: sigue siendo el aviso del turno.
        self.assertIn("Mañana", texto)

    def test_sin_token_el_recordatorio_sale_igual(self):
        # El link es best-effort: si no se puede crear el token, el paciente
        # igual recibe el recordatorio (mismo contrato que el resto del módulo).
        db_booking.booking_request_confirm_token = lambda sid: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_reminder(self._row())
        _to, _asunto, texto, _html = self.sent.calls[0]
        self.assertNotIn("/appointment/confirm/", texto)
        self.assertIn("tenés tu turno", texto)


class TestSendMultipart(unittest.TestCase):
    """email_mailgun.send: `text` siempre va, `html` sólo cuando se pasa —
    Mailgun manda las dos partes en el mismo POST con un campo extra, así
    que no hace falta armar MIME acá. Se intercepta el opener HTTP para leer
    exactamente qué campos viajaron, sin pegarle a la red."""

    def setUp(self):
        self._orig_cfg = email_mailgun._cfg
        email_mailgun._cfg = lambda: ("testkey", "mg.test.local", "Test <a@b.com>", True)
        self._orig_build_opener = email_mailgun.urllib.request.build_opener
        self.captured = {}

        class _FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _FakeOpener:
            def open(_self, req, timeout=8):
                self.captured["req"] = req
                return _FakeResp()

        email_mailgun.urllib.request.build_opener = lambda *a, **k: _FakeOpener()

    def tearDown(self):
        email_mailgun._cfg = self._orig_cfg
        email_mailgun.urllib.request.build_opener = self._orig_build_opener

    def _campos(self):
        import urllib.parse
        data = self.captured["req"].data
        return {k: v[0] for k, v in urllib.parse.parse_qs(data.decode()).items()}

    def test_sin_html_manda_solo_texto(self):
        ok = email_mailgun.send("p@x.com", "Asunto", "Cuerpo plano")
        self.assertTrue(ok)
        campos = self._campos()
        self.assertEqual(campos["text"], "Cuerpo plano")
        self.assertNotIn("html", campos)

    def test_con_html_manda_las_dos_partes(self):
        ok = email_mailgun.send("p@x.com", "Asunto", "Cuerpo plano", html="<p>Cuerpo</p>")
        self.assertTrue(ok)
        campos = self._campos()
        self.assertEqual(campos["text"], "Cuerpo plano")
        self.assertEqual(campos["html"], "<p>Cuerpo</p>")


class TestEntrevistaCtaEnEmails(SendCaptureMixin, unittest.TestCase):
    """La sección "Contale al médico cómo te sentís" en received/approved:
    presente cuando booking_requests._entrevista_field la ofrece, ausente
    cuando no. La decisión de habilitación en sí (módulo apagado, toggle por
    médico) ya se prueba en test_intake_settings.py — acá sólo se prueba que
    notifications la refleja, en texto Y en html, en las dos emails. El
    nombre patient-facing dejó de ser la "entrevista previa" burocrática
    (the product owner, 2026-08-03)."""

    def setUp(self):
        super().setUp()
        from app.modules.turnos import booking_requests
        self._bookreq = booking_requests
        self._orig_campo = booking_requests._entrevista_field
        self._orig_token = booking_requests._entrevista_patient_token

    def tearDown(self):
        self._bookreq._entrevista_field = self._orig_campo
        self._bookreq._entrevista_patient_token = self._orig_token
        super().tearDown()

    def _row(self, sid=55):
        return {
            "id": sid, "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
            "centro_slug": "hospital-pinamar", "medico_asignado_id": "med-1",
            "ai_json": {"preparacion": []},
        }

    def test_recibida_incluye_la_cta_cuando_se_ofrece(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        self._bookreq._entrevista_patient_token = lambda sid: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_received(self._row(sid=55))
        to, subject, text, html = self.sent.calls[0]
        self.assertIn("/entrevista/55", text)
        self.assertIn("Contale al médico cómo te sentís", text)
        self.assertIn("/entrevista/55", html)
        self.assertIn("Contale al médico cómo te sentís", html)

    def test_recibida_omite_la_cta_cuando_no_se_ofrece(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_received(self._row(sid=56))
        to, subject, text, html = self.sent.calls[0]
        self.assertNotIn("/entrevista/", text)
        self.assertNotIn("/entrevista/", html)

    def test_aprobada_incluye_la_cta_cuando_se_ofrece(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        self._bookreq._entrevista_patient_token = lambda sid: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_approved(self._row(sid=57))
        to, subject, text, html = self.sent.calls[0]
        self.assertIn("/entrevista/57", text)
        self.assertIn("/entrevista/57", html)

    def test_aprobada_omite_la_cta_cuando_no_se_ofrece(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_approved(self._row(sid=58))
        to, subject, text, html = self.sent.calls[0]
        self.assertNotIn("/entrevista/", text)
        self.assertNotIn("/entrevista/", html)

    def test_el_link_lleva_el_patient_token_para_entrar_sin_login(self):
        # 2026-08-03: el link de mail tiene que entrar directo al chat, sin
        # loguearse — booking_requests._entrevista_patient_token es lo que
        # arma ese token; acá sólo se prueba que _entrevista_cta lo pega al
        # final de la url cuando existe.
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        self._bookreq._entrevista_patient_token = lambda sid: "tok-fake-123"
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_received(self._row(sid=59))
        to, subject, text, html = self.sent.calls[0]
        self.assertIn("/entrevista/59?t=tok-fake-123", text)
        self.assertIn("/entrevista/59?t=tok-fake-123", html)

    def test_sin_token_el_link_no_lleva_query_string(self):
        # Best-effort: si no se pudo armar el token (DB caída, etc.) el link
        # sigue funcionando como antes de 2026-08-03 — sesión-only.
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        self._bookreq._entrevista_patient_token = lambda sid: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_received(self._row(sid=60))
        to, subject, text, html = self.sent.calls[0]
        self.assertIn("/entrevista/60", text)
        self.assertNotIn("/entrevista/60?t=", text)


class TestNotifyInterviewReminder(SendCaptureMixin, unittest.TestCase):
    """notify_interview_reminder (deliverable 3 — el SEGUNDO mail del día
    antes) se autolimita: sólo manda cuando la entrevista está ofrecida Y
    todavía no está 'done' (db_clinical.interview_for_request)."""

    def setUp(self):
        super().setUp()
        from app.modules.turnos import booking_requests
        self._bookreq = booking_requests
        self._orig_campo = booking_requests._entrevista_field
        self._orig_token = booking_requests._entrevista_patient_token
        self._bookreq._entrevista_patient_token = lambda sid: None
        self._orig_interview_for_request = db_clinical.interview_for_request

    def tearDown(self):
        self._bookreq._entrevista_field = self._orig_campo
        self._bookreq._entrevista_patient_token = self._orig_token
        db_clinical.interview_for_request = self._orig_interview_for_request
        super().tearDown()

    def _row(self, sid):
        return {
            "id": sid, "nombre": "Juana", "apellido": "Pérez", "dni": "30111222",
            "solicitante_dni": "30111222", "servicio_id": "eco-abdominal",
            "centro_slug": "hospital-pinamar", "medico_asignado_id": "med-1",
            "turno_dt": datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc),
        }

    def test_manda_cuando_se_ofrece_y_no_esta_completada(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        db_clinical.interview_for_request = lambda sid: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_interview_reminder(self._row(sid=5))
        self.assertEqual(len(self.sent.calls), 1)
        to, subject, text, html = self.sent.calls[0]
        self.assertIn("/entrevista/5", text)
        self.assertIn("/entrevista/5", html)
        self.assertIn("Mañana", text)

    def test_no_manda_si_la_entrevista_ya_esta_completada(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        db_clinical.interview_for_request = lambda sid: {"status": "done"}
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_interview_reminder(self._row(sid=6))
        self.assertEqual(self.sent.calls, [])

    def test_no_manda_si_la_entrevista_no_se_ofrece(self):
        self._bookreq._entrevista_field = lambda sid, doc=None: None
        self.con_email("30111222", "paciente@example.com")
        notifications.notify_interview_reminder(self._row(sid=7))
        self.assertEqual(self.sent.calls, [])

    def test_nunca_levanta_sin_row(self):
        notifications.notify_interview_reminder(None)
        self.assertEqual(self.sent.calls, [])


class TestRecordatorioEntrevistaTick(unittest.TestCase):
    """reminders.tick_interview: SEGUNDO barrido del día antes, propia
    ventana horaria y propia columna (interview_reminder_at) — independiente
    de tick(). Recorre booking_requests_to_remind_interview y reclama vía
    db_booking.claim_interview_reminder (CAS sobre interview_reminder_at IS NULL)
    ANTES de notificar — mismo contrato claim-before-send que tick(),
    probado bajo Postgres real en test_reminders.py; acá sólo el barrido y
    el "se marca aunque notify explote"."""

    def setUp(self):
        self._orig_para_recordar = db_booking.booking_requests_to_remind_interview
        self._orig_reclamar = db_booking.claim_interview_reminder
        self._orig_notify = notifications.notify_interview_reminder
        self.marcadas = []
        self.notificadas = []
        self._filas = []

        def fake_para_recordar(fecha):
            self._fecha_pedida = fecha
            return self._filas

        def fake_reclamar(sid):
            # Reclamo siempre gana en estos tests (no hay carrera acá — la
            # carrera del claim-CAS real se prueba en test_reminders.py con
            # Postgres real); esto sólo simula "interview_reminder_at estaba NULL".
            self.marcadas.append(sid)
            return True

        def fake_notify(row, handler=None):
            self.notificadas.append(row["id"])

        db_booking.booking_requests_to_remind_interview = fake_para_recordar
        db_booking.claim_interview_reminder = fake_reclamar
        notifications.notify_interview_reminder = fake_notify

    def tearDown(self):
        db_booking.booking_requests_to_remind_interview = self._orig_para_recordar
        db_booking.claim_interview_reminder = self._orig_reclamar
        notifications.notify_interview_reminder = self._orig_notify

    def _row(self, sid):
        return {"id": sid, "servicio_id": "eco-abdominal", "centro_slug": "hospital-pinamar",
                "turno_dt": datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc),
                "medico_asignado_id": "med-1"}

    def test_dentro_de_ventana_notifica_y_marca(self):
        self._filas = [self._row(sid=77)]
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick_interview(now)
        self.assertEqual(n, 1)
        self.assertEqual(self.notificadas, [77])
        self.assertEqual(self.marcadas, [77])

    def test_fuera_de_ventana_no_hace_nada(self):
        self._filas = [self._row(sid=77)]
        now = datetime(2026, 7, 16, 3, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick_interview(now)
        self.assertEqual(n, 0)
        self.assertEqual(self.notificadas, [])
        self.assertEqual(self.marcadas, [])

    def test_se_marca_aunque_notify_explote(self):
        self._filas = [self._row(sid=88)]

        def boom(row, handler=None):
            raise RuntimeError("mailgun caído")
        notifications.notify_interview_reminder = boom
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick_interview(now)
        self.assertEqual(n, 1)
        self.assertEqual(self.marcadas, [88])

    def test_pide_el_barrido_de_manana_en_art(self):
        self._filas = [self._row(sid=5)]
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        reminders.tick_interview(now)
        self.assertEqual(self._fecha_pedida, (now.astimezone(_ART) + timedelta(days=1)).date())

    def test_reclamo_perdido_no_notifica_ni_cuenta(self):
        # Mismo contrato que TestRecordatorioTick.test_reclamo_perdido_no_manda_ni_cuenta:
        # si otro tick/proceso ya reclamó la fila (interview_reminder_at dejó
        # de ser NULL), claim_interview_reminder devuelve False -> ni se
        # notifica ni se suma a `procesadas`.
        db_booking.claim_interview_reminder = lambda sid: False
        self._filas = [self._row(sid=13)]
        now = datetime(2026, 7, 16, 10, 0, tzinfo=_ART).astimezone(timezone.utc)
        n = reminders.tick_interview(now)
        self.assertEqual(n, 0)
        self.assertEqual(self.notificadas, [])
        self.assertEqual(self.marcadas, [])
