"""test_calendar_feed_router_native.py — native-migration campaign
(docs/roadmap/native-migration-campaign-20260826.md), family F7: transport
coverage for `calendar_feed_router.py`'s five ASGI endpoints and
`calendar_feed.py`'s extracted `resolve_*` domain functions. Same
TestClient(app) pattern as tests/test_app_modules_router.py; patch
`calendar_feed._current_user`/`core.db.*` module attributes directly so
each native endpoint remains deterministic without a live Postgres."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.modules.turnos import calendar_feed  # noqa: E402


def _servicio_centro():
    """(servicio_id, centro_slug) de un servicio real, mismo helper que
    test_calendar_feed.py — necesario para que `_event()` arme un VEVENT en
    vez de devolver None (nowhere/nowhen to show -> 404)."""
    from app.patient_web import site_catalog, site_centers
    slugs = {c.get("slug") for c in site_centers._centers()}
    for s in site_catalog._services():
        if s.get("centro") in slugs:
            return s["id"], s["centro"]
    return None, None


class TestCalendarFeedRouterNative(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

        self.sid, self.slug = _servicio_centro()
        if not self.sid:
            self.skipTest("sin servicio+centro resoluble en webdata")

        self._orig_current_user = calendar_feed._current_user
        self._orig_get_booking = db_booking.get_booking_request
        self._orig_hold = db_slots.hold_of_booking_request
        self._orig_feed_token_issue = db_accounts.feed_token_issue
        self._orig_feed_token_regenerate = db_accounts.feed_token_regenerate
        self._orig_user_by_feed_token = db_accounts.user_by_feed_token
        self._orig_list_by_dni = db_booking.list_by_dni
        self._orig_operator_by_feed_token = db_operators.operator_by_feed_token
        self._orig_by_confirm_token = db_booking.booking_request_by_confirm_token

    def tearDown(self):
        calendar_feed._current_user = self._orig_current_user
        db_booking.get_booking_request = self._orig_get_booking
        db_slots.hold_of_booking_request = self._orig_hold
        db_accounts.feed_token_issue = self._orig_feed_token_issue
        db_accounts.feed_token_regenerate = self._orig_feed_token_regenerate
        db_accounts.user_by_feed_token = self._orig_user_by_feed_token
        db_booking.list_by_dni = self._orig_list_by_dni
        db_operators.operator_by_feed_token = self._orig_operator_by_feed_token
        db_booking.booking_request_by_confirm_token = self._orig_by_confirm_token

    def _row(self, **over):
        row = {
            "id": 501, "dni": "90000001", "solicitante_dni": "90000001",
            "nombre": "Test", "apellido": "Usuario",
            "servicio_id": self.sid, "centro_slug": self.slug,
            "estado": "aprobada",
            "turno_dt": datetime.now(timezone.utc) + timedelta(days=3),
        }
        row.update(over)
        return row

    # ---- GET /api/appointment/{sid}.ics ------------------------------------

    def test_appointment_ics_requires_session(self):
        resp = self.client.get("/api/appointment/501.ics")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "auth_requerida"})
        self.assertEqual(resp.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_appointment_ics_invalid_id(self):
        calendar_feed._current_user = lambda request: {"dni": "90000001"}
        resp = self.client.get("/api/appointment/not-a-number.ics")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "id_invalido"})

    def test_appointment_ics_not_mine_404(self):
        calendar_feed._current_user = lambda request: {"dni": "11111111"}
        db_booking.get_booking_request = lambda sid: self._row()
        resp = self.client.get("/api/appointment/501.ics")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_encontrado"})

    def test_appointment_ics_valid_serves_ics(self):
        calendar_feed._current_user = lambda request: {"dni": "90000001", "nombre": "Test", "apellido": "Usuario"}
        db_booking.get_booking_request = lambda sid: self._row()
        resp = self.client.get("/api/appointment/501.ics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "text/calendar; charset=utf-8")
        self.assertIn('filename="turno.ics"', resp.headers["content-disposition"])
        self.assertEqual(resp.headers["cache-control"], "no-cache, max-age=0")
        self.assertIn("BEGIN:VEVENT", resp.text)
        self.assertIn("UID:turno-501@sanaturnos", resp.text)

    # ---- GET /api/calendar/feed ---------------------------------------------

    def test_calendar_feed_url_requires_session(self):
        resp = self.client.get("/api/calendar/feed")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "auth_requerida"})

    def test_calendar_feed_url_shown_when_just_created(self):
        calendar_feed._current_user = lambda request: {"dni": "90000001"}
        db_accounts.feed_token_issue = lambda dni: {"token": "feed-tok-1234", "hint": "1234"}
        resp = self.client.get("/api/calendar/feed")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["url"].endswith("/feed/appointments/feed-tok-1234.ics"))
        self.assertEqual(resp.json()["hint"], "1234")
        self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_calendar_feed_url_hidden_once_it_exists(self):
        # The database keeps only the hash: a second visit gets the last 4 characters.
        calendar_feed._current_user = lambda request: {"dni": "90000001"}
        db_accounts.feed_token_issue = lambda dni: {"token": None, "hint": "1234"}
        resp = self.client.get("/api/calendar/feed")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"url": None, "hint": "1234"})
        self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_calendar_feed_url_vanished_account_401(self):
        calendar_feed._current_user = lambda request: {"dni": "90000001"}
        db_accounts.feed_token_issue = lambda dni: None
        resp = self.client.get("/api/calendar/feed")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "auth_requerida"})

    # ---- POST /api/calendar/feed/regenerate ---------------------------------

    def test_calendar_feed_regenerate_requires_session(self):
        called = []
        db_accounts.feed_token_regenerate = lambda dni: called.append(dni) or "x"
        resp = self.client.post("/api/calendar/feed/regenerate")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "auth_requerida"})
        self.assertEqual(called, [])

    def test_calendar_feed_regenerate_shows_the_new_address_once(self):
        calendar_feed._current_user = lambda request: {"dni": "90000001"}
        seen = []
        db_accounts.feed_token_regenerate = lambda dni: seen.append(dni) or "nuevo-tok-9xYz"
        resp = self.client.post("/api/calendar/feed/regenerate")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen, ["90000001"])
        self.assertTrue(resp.json()["url"].endswith("/feed/appointments/nuevo-tok-9xYz.ics"))
        self.assertEqual(resp.json()["hint"], "9xYz")
        self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_calendar_feed_regenerate_vanished_account_401(self):
        calendar_feed._current_user = lambda request: {"dni": "90000001"}
        db_accounts.feed_token_regenerate = lambda dni: None
        resp = self.client.post("/api/calendar/feed/regenerate")
        self.assertEqual(resp.status_code, 401)

    # ---- GET /feed/appointments/{token}.ics ---------------------------------

    def test_feed_ics_unknown_token_404(self):
        db_accounts.user_by_feed_token = lambda t: None
        resp = self.client.get("/feed/appointments/tok-desconocido.ics")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_encontrado"})

    def test_feed_ics_valid_token_serves_ics(self):
        usuario = {"dni": "90000001", "nombre": "Test", "apellido": "Usuario"}
        db_accounts.user_by_feed_token = lambda t: usuario
        db_booking.list_by_dni = lambda dni: [self._row(id=502)]
        resp = self.client.get("/feed/appointments/tok-bueno.ics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "text/calendar; charset=utf-8")
        self.assertIn('filename="mis-turnos.ics"', resp.headers["content-disposition"])
        self.assertIn("UID:turno-502@sanaturnos", resp.text)

    # ---- GET /feed/doctor/{token}.ics ---------------------------------------

    def test_doctor_feed_ics_unknown_token_404(self):
        db_operators.operator_by_feed_token = lambda t: None
        resp = self.client.get("/feed/doctor/tok-desconocido.ics")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_encontrado"})

    def test_doctor_feed_ics_non_medico_role_404(self):
        db_operators.operator_by_feed_token = lambda t: {"rol": "admin", "medico_id": None}
        resp = self.client.get("/feed/doctor/tok-admin.ics")
        self.assertEqual(resp.status_code, 404)

    def test_doctor_feed_ics_deactivated_account_404(self):
        # The token alone must not keep serving patients to a doctor whose account the admin deactivated
        # (the patient's own feed already dies with the archived account).
        from app.modules.turnos import doctor_appointments
        original = doctor_appointments.appointments_of
        self.addCleanup(setattr, doctor_appointments, "appointments_of", original)
        doctor_appointments.appointments_of = lambda *a, **k: [self._row(id=504)]
        db_operators.operator_by_feed_token = lambda t: {"rol": "medico", "medico_id": "dr-x", "activo": True}
        resp = self.client.get("/feed/doctor/tok-medico.ics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("UID:turno-504@sanaturnos", resp.text)

        db_operators.operator_by_feed_token = lambda t: {"rol": "medico", "medico_id": "dr-x", "activo": False}
        resp = self.client.get("/feed/doctor/tok-medico.ics")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_encontrado"})

    # ---- GET /appointment/{token}.ics ---------------------------------------

    def test_token_ics_malformed_token_404_without_touching_db(self):
        def _explota(t):
            raise AssertionError("no debe consultar la DB con un token con formato inválido")
        db_booking.booking_request_by_confirm_token = _explota
        # Espacios (URL-encoded, sin barra: la barra partiría el segmento de
        # ruta y nunca llegaría a este endpoint) no matchean _ICS_TOKEN_RE.
        resp = self.client.get("/appointment/no%20es%20un%20token%20valido.ics")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_encontrado"})

    def test_token_ics_valid_serves_ics_in_local_time(self):
        row = self._row(id=503, turno_dt=datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc))
        db_booking.booking_request_by_confirm_token = lambda t: row if t == "tok-bueno" else None
        resp = self.client.get("/appointment/tok-bueno.ics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "text/calendar; charset=utf-8")
        # 13:00 UTC == 10:00 ART (-03:00) — nunca el offset crudo.
        self.assertIn("DTSTART;TZID=America/Argentina/Buenos_Aires:20260810T100000", resp.text)

    def test_token_ics_unknown_token_404(self):
        db_booking.booking_request_by_confirm_token = lambda t: None
        resp = self.client.get("/appointment/tok-inexistente.ics")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_encontrado"})


if __name__ == "__main__":
    unittest.main()
