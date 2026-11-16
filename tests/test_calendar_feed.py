"""test_calendar_feed.py — el .ics de un turno es del PACIENTE, no de la cuenta.

Regresión de dos bugs del turno de un hijo (2026-07-12):
  1. `_turno_ics` sólo aceptaba `row.dni == usuario.dni`, así que el padre que
     sacó el turno para su hijo recibía 404 al bajar el .ics.
  2. `_evento` escribía en la DESCRIPTION el nombre de la CUENTA, así que el
     turno del hijo entraba al calendario a nombre del padre.
La fila de solicitudes ya trae nombre/apellido/dni del paciente y
`solicitante_dni` del adulto que lo pidió — la identidad sale de ahí.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.modules.turnos import calendar_feed  # noqa: E402
from app.modules.turnos import calendar_feed_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_TEST_APP = FastAPI()
_TEST_APP.include_router(calendar_feed_router.router)
_CLIENT = TestClient(_TEST_APP)


def _row(**over):
    row = {
        "id": 7,
        "dni": "55111222",          # el hijo
        "nombre": "Mateo",
        "apellido": "Belov",
        "solicitante_dni": "20456781",  # el padre, que fue quien lo pidió
        "servicio_id": None,
        "centro_slug": None,
        "estado": "aprobada",
        "turno_dt": datetime(2026, 7, 14, 8, 0, 0),
    }
    row.update(over)
    return row


class TestEventoNombraAlPaciente(unittest.TestCase):
    """El VEVENT lleva el nombre del paciente (la fila), no el de la cuenta."""

    def _primer_par(self):
        """(servicio_id, centro_slug) de un servicio real: `servicio.centro` YA es
        el slug del centro en webdata/servicios.json."""
        from app.patient_web import site_catalog, site_centers
        slugs = {c.get("slug") for c in site_centers._centers()}
        for s in site_catalog._services():
            if s.get("centro") in slugs:
                return s["id"], s["centro"]
        self.skipTest("sin servicio+centro resoluble en webdata")

    def test_description_usa_el_nombre_del_paciente(self):
        sid, slug = self._primer_par()
        row = _row(servicio_id=sid, centro_slug=slug)
        cuenta = {"dni": "20456781", "nombre": "Ilya", "apellido": "Belov"}
        ev = calendar_feed._event(row, cuenta)
        self.assertIsNotNone(ev)
        self.assertIn("Mateo Belov", ev)
        self.assertNotIn("Turno de Ilya Belov", ev)

    def test_sin_nombre_en_la_fila_cae_a_la_cuenta(self):
        sid, slug = self._primer_par()
        row = _row(servicio_id=sid, centro_slug=slug, nombre=None, apellido=None)
        cuenta = {"dni": "20456781", "nombre": "Ilya", "apellido": "Belov"}
        ev = calendar_feed._event(row, cuenta)
        self.assertIn("Ilya Belov", ev)


class TestDuenoDelIcs(unittest.TestCase):
    """El dueño es el paciente O el adulto solicitante (mismo criterio que
    db_booking.list_by_dni, que ya listaba el turno del hijo en /mis-solicitudes)."""

    def _es_mio(self, dni_usuario, row):
        return dni_usuario in (row.get("dni"), row.get("solicitante_dni"))

    def test_el_padre_es_dueno_del_turno_del_hijo(self):
        self.assertTrue(self._es_mio("20456781", _row()))

    def test_el_paciente_es_dueno_de_su_turno(self):
        self.assertTrue(self._es_mio("55111222", _row()))

    def test_un_tercero_no(self):
        self.assertFalse(self._es_mio("30999888", _row()))


class TestPendienteIcs(unittest.TestCase):
    """2026-07-18: una solicitud PENDIENTE con horario ya elegido (hold en
    turno_holds) también sirve un .ics — con " (pendiente)" en el SUMMARY,
    para que la persona pueda agendar el turno propuesto antes de que el
    hospital lo confirme. Sin hold (franja tipo "A la mañana", o sin elegir
    nada todavía) sigue sin haber fecha que ofrecer -> 404, mismo contrato de
    siempre para "no hay turno concreto"."""

    def _primer_par(self):
        from app.patient_web import site_catalog, site_centers
        slugs = {c.get("slug") for c in site_centers._centers()}
        for s in site_catalog._services():
            if s.get("centro") in slugs:
                return s["id"], s["centro"]
        self.skipTest("sin servicio+centro resoluble en webdata")

    def setUp(self):
        self.usuario = {"dni": "90000001", "nombre": "Test", "apellido": "Usuario"}
        self._orig_current_user = calendar_feed._current_user
        self._orig_get_solicitud = db_booking.get_booking_request
        self._orig_hold = db_slots.hold_of_booking_request
        calendar_feed._current_user = lambda request: self.usuario

    def tearDown(self):
        calendar_feed._current_user = self._orig_current_user
        db_booking.get_booking_request = self._orig_get_solicitud
        db_slots.hold_of_booking_request = self._orig_hold

    def test_pendiente_con_hold_sirve_ics_con_sufijo(self):
        sid, slug = self._primer_par()
        row = {"id": 66, "dni": "90000001", "solicitante_dni": "90000001",
               "nombre": "Test", "apellido": "Usuario",
               "servicio_id": sid, "centro_slug": slug,
               "estado": "pendiente", "turno_dt": None}
        db_booking.get_booking_request = lambda i: row
        db_slots.hold_of_booking_request = lambda i: {"dia": "2026-07-20", "hora": "07:30"}
        response = _CLIENT.get("/api/appointment/66.ics")
        self.assertEqual(response.status_code, 200)
        body = response.text
        summary_line = next(l for l in body.split("\r\n") if l.startswith("SUMMARY:"))
        self.assertTrue(summary_line.endswith("(pendiente)"))
        self.assertIn("DTSTART;TZID=America/Argentina/Buenos_Aires:20260720T073000", body)

    def test_pendiente_sin_hold_404(self):
        sid, slug = self._primer_par()
        row = {"id": 68, "dni": "90000001", "solicitante_dni": "90000001",
               "nombre": "Test", "apellido": "Usuario",
               "servicio_id": sid, "centro_slug": slug,
               "estado": "pendiente", "turno_dt": None}
        db_booking.get_booking_request = lambda i: row
        db_slots.hold_of_booking_request = lambda i: None
        response = _CLIENT.get("/api/appointment/68.ics")
        self.assertEqual(response.status_code, 404)

    def test_ajena_sigue_404_aunque_tenga_hold(self):
        sid, slug = self._primer_par()
        row = {"id": 66, "dni": "11111111", "solicitante_dni": "11111111",
               "nombre": "Otra", "apellido": "Persona",
               "servicio_id": sid, "centro_slug": slug,
               "estado": "pendiente", "turno_dt": None}
        db_booking.get_booking_request = lambda i: row
        db_slots.hold_of_booking_request = lambda i: {"dia": "2026-07-20", "hora": "07:30"}
        response = _CLIENT.get("/api/appointment/66.ics")
        self.assertEqual(response.status_code, 404)


class TestFeedIncluyePendientes(unittest.TestCase):
    """El feed de calendario suma pendientes con hold futuro (sufijo en el
    SUMMARY) además de las aprobadas futuras de siempre; el UID sigue siendo
    turno-<id>@sanaturnos en ambos casos, así que al aprobarse el evento se
    actualiza en la app de calendario en vez de duplicarse."""

    def _primer_par(self):
        from app.patient_web import site_catalog, site_centers
        slugs = {c.get("slug") for c in site_centers._centers()}
        for s in site_catalog._services():
            if s.get("centro") in slugs:
                return s["id"], s["centro"]
        self.skipTest("sin servicio+centro resoluble en webdata")

    def setUp(self):
        self.usuario = {"dni": "90000001", "nombre": "Test", "apellido": "Usuario"}
        self._orig_token = db_accounts.user_by_feed_token
        self._orig_listar = db_booking.list_by_dni
        self._orig_hold = db_slots.hold_of_booking_request
        db_accounts.user_by_feed_token = lambda t: self.usuario

    def tearDown(self):
        db_accounts.user_by_feed_token = self._orig_token
        db_booking.list_by_dni = self._orig_listar
        db_slots.hold_of_booking_request = self._orig_hold

    def test_feed_incluye_aprobada_y_pendiente_futura(self):
        sid, slug = self._primer_par()
        futuro_aprobada = datetime.now(timezone.utc) + timedelta(days=5)
        rows = [
            {"id": 69, "dni": "90000001", "solicitante_dni": "90000001",
             "nombre": "Test", "apellido": "Usuario",
             "servicio_id": sid, "centro_slug": slug,
             "estado": "aprobada", "turno_dt": futuro_aprobada},
            {"id": 66, "dni": "90000001", "solicitante_dni": "90000001",
             "nombre": "Test", "apellido": "Usuario",
             "servicio_id": sid, "centro_slug": slug,
             "estado": "pendiente", "turno_dt": None},
            {"id": 68, "dni": "90000001", "solicitante_dni": "90000001",
             "nombre": "Test", "apellido": "Usuario",
             "servicio_id": sid, "centro_slug": slug,
             "estado": "pendiente", "turno_dt": None},  # sin hold, franja suelta
        ]
        db_booking.list_by_dni = lambda dni: rows
        holds = {66: {"dia": "2099-01-15", "hora": "09:00"}}
        db_slots.hold_of_booking_request = lambda i: holds.get(i)
        response = _CLIENT.get("/feed/appointments/tok.ics")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertEqual(body.count("BEGIN:VEVENT"), 2)
        self.assertIn("UID:turno-69@sanaturnos", body)
        self.assertIn("UID:turno-66@sanaturnos", body)
        self.assertNotIn("UID:turno-68@sanaturnos", body)
        summaries = [l for l in body.split("\r\n") if l.startswith("SUMMARY:")]
        self.assertTrue(any(s.endswith("(pendiente)") for s in summaries))
        self.assertTrue(any(not s.endswith("(pendiente)") for s in summaries))

    def test_pendiente_con_hold_pasado_no_entra(self):
        sid, slug = self._primer_par()
        rows = [
            {"id": 66, "dni": "90000001", "solicitante_dni": "90000001",
             "nombre": "Test", "apellido": "Usuario",
             "servicio_id": sid, "centro_slug": slug,
             "estado": "pendiente", "turno_dt": None},
        ]
        db_booking.list_by_dni = lambda dni: rows
        db_slots.hold_of_booking_request = lambda i: {"dia": "2020-01-01", "hora": "09:00"}
        response = _CLIENT.get("/feed/appointments/tok.ics")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertEqual(body.count("BEGIN:VEVENT"), 0)


class TestTokenIcs(unittest.TestCase):
    """GET /appointment/<confirm_token>.ics (2026-08-03) — el link "Al
    calendario" de los emails de aprobación/recordatorio, sin sesión: el
    token ES la autenticación (booking_requests.confirm_token, mismo que
    appointment_confirm.py)."""

    def _primer_par(self):
        from app.patient_web import site_catalog, site_centers
        slugs = {c.get("slug") for c in site_centers._centers()}
        for s in site_catalog._services():
            if s.get("centro") in slugs:
                return s["id"], s["centro"]
        self.skipTest("sin servicio+centro resoluble en webdata")

    def setUp(self):
        self._orig_by_token = db_booking.booking_request_by_confirm_token
        self._orig_mint = db_booking.booking_request_confirm_token

    def tearDown(self):
        db_booking.booking_request_by_confirm_token = self._orig_by_token
        db_booking.booking_request_confirm_token = self._orig_mint

    def test_token_valido_sirve_ics_en_hora_local(self):
        sid, slug = self._primer_par()
        row = {"id": 71, "dni": "90000001", "solicitante_dni": "90000001",
               "nombre": "Test", "apellido": "Usuario",
               "servicio_id": sid, "centro_slug": slug,
               "estado": "aprobada",
               "turno_dt": datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)}
        db_booking.booking_request_by_confirm_token = lambda t: row if t == "tok-bueno" else None
        response = _CLIENT.get("/appointment/tok-bueno.ics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "text/calendar; charset=utf-8")
        self.assertIn('filename="turno.ics"', response.headers.get("Content-Disposition", ""))
        body = response.text
        # 13:00 UTC == 10:00 ART (-03:00) — nunca el offset crudo.
        self.assertIn("DTSTART;TZID=America/Argentina/Buenos_Aires:20260810T100000", body)
        self.assertIn("BEGIN:VALARM", body)
        self.assertIn("TRIGGER:-PT30M", body)

    def test_token_con_formato_invalido_404_sin_tocar_la_db(self):
        def _explota(t):
            raise AssertionError("no debe consultar la DB con un token con formato inválido")
        db_booking.booking_request_by_confirm_token = _explota
        response = _CLIENT.get("/appointment/no/es-un-token%20valido.ics")
        self.assertEqual(response.status_code, 404)

    def test_token_desconocido_404(self):
        db_booking.booking_request_by_confirm_token = lambda t: None
        response = _CLIENT.get("/appointment/tok-inexistente.ics")
        self.assertEqual(response.status_code, 404)

    def test_estado_no_aprobada_404(self):
        sid, slug = self._primer_par()
        row = {"id": 72, "dni": "90000001", "solicitante_dni": "90000001",
               "nombre": "Test", "apellido": "Usuario",
               "servicio_id": sid, "centro_slug": slug,
               "estado": "pendiente", "turno_dt": None}
        db_booking.booking_request_by_confirm_token = lambda t: row
        response = _CLIENT.get("/appointment/tok-pendiente.ics")
        self.assertEqual(response.status_code, 404)

    def test_get_no_muta_nada(self):
        """El GET nunca mintea ni actualiza el token — sólo lo lee. La
        aprobación/el recordatorio ya lo mintearon antes, al armar el email
        (notifications._calendar_ics_url)."""
        sid, slug = self._primer_par()
        row = {"id": 73, "dni": "90000001", "solicitante_dni": "90000001",
               "nombre": "Test", "apellido": "Usuario",
               "servicio_id": sid, "centro_slug": slug,
               "estado": "aprobada",
               "turno_dt": datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)}
        db_booking.booking_request_by_confirm_token = lambda t: row

        def _no_debe_mintear(sid):
            raise AssertionError("el GET no debe mintear/tocar confirm_token")
        db_booking.booking_request_confirm_token = _no_debe_mintear
        response = _CLIENT.get("/appointment/tok-bueno.ics")
        self.assertEqual(response.status_code, 200)

    def test_ruteo_nativo_del_token_ics(self):
        sid, slug = self._primer_par()
        row = {"id": 74, "dni": "90000001", "solicitante_dni": "90000001",
               "nombre": "Test", "apellido": "Usuario",
               "servicio_id": sid, "centro_slug": slug,
               "estado": "aprobada",
               "turno_dt": datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)}
        db_booking.booking_request_by_confirm_token = lambda token: row

        response = _CLIENT.get("/appointment/tok-bueno.ics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/calendar; charset=utf-8")



if __name__ == "__main__":
    unittest.main()
