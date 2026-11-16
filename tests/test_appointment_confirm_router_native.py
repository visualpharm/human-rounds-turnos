"""test_appointment_confirm_router_native.py — native-migration campaign
(docs/roadmap/native-migration-campaign-20260826.md), family F7: transport
parity for `appointment_confirm_router.py`'s two ASGI endpoints against
`appointment_confirm.py`'s extracted `build_confirm_page`/`resolve_answer`
domain functions. Same TestClient(app) pattern as
tests/test_app_modules_router.py; same monkeypatch style as
tests/test_appointment_confirm.py (patch `core.db.*` module attributes
directly so the native endpoint sees deterministic state). No real Postgres
needed: `db_booking.booking_request_by_confirm_token`/
`db_booking.mark_confirmation` are stubbed."""

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


def _servicio_centro():
    """(servicio_id, centro_slug) de un servicio real — mismo helper que
    test_calendar_feed_router_native.py/test_calendar_feed.py: hace falta
    para que `_service_name`/`_center_name` resuelvan nombres reales."""
    from app.patient_web import site_catalog, site_centers
    slugs = {c.get("slug") for c in site_centers._centers()}
    for s in site_catalog._services():
        if s.get("centro") in slugs:
            return s["id"], s["centro"]
    return None, None


class TestAppointmentConfirmRouterNative(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

        self.sid, self.slug = _servicio_centro()
        if not self.sid:
            self.skipTest("sin servicio+centro resoluble en webdata")

        self._orig_by_token = db_booking.booking_request_by_confirm_token
        self._orig_mark = db_booking.mark_confirmation

    def tearDown(self):
        db_booking.booking_request_by_confirm_token = self._orig_by_token
        db_booking.mark_confirmation = self._orig_mark

    def _row(self, **over):
        row = {
            "id": 601, "dni": "90000001", "solicitante_dni": "90000001",
            "nombre": "Test", "apellido": "Usuario",
            "servicio_id": self.sid, "centro_slug": self.slug,
            "estado": "aprobada",
            "turno_dt": datetime.now(timezone.utc) + timedelta(days=1),
            "confirmacion": None,
        }
        row.update(over)
        return row

    # ---- GET /appointment/confirm/{token} ----------------------------------

    def test_confirm_page_unknown_token_is_not_a_dead_end(self):
        db_booking.booking_request_by_confirm_token = lambda t: None
        resp = self.client.get("/appointment/confirm/no-existe-este-token")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("Este link ya no sirve", resp.text)
        self.assertIn("Todos mis turnos", resp.text)

    def test_confirm_page_valid_token_asks_the_question(self):
        row = self._row()
        db_booking.booking_request_by_confirm_token = lambda t: row if t == "tok-bueno" else None
        resp = self.client.get("/appointment/confirm/tok-bueno")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("¿Vas a poder venir?", resp.text)
        self.assertIn('data-confirm-action="si"', resp.text)
        self.assertIn('data-confirm-action="no"', resp.text)
        self.assertIn("tok-bueno", resp.text)

    def test_confirm_page_r_si_resolves_only_client_side(self):
        row = self._row()
        db_booking.booking_request_by_confirm_token = lambda t: row
        resp = self.client.get("/appointment/confirm/tok-bueno", params={"r": "si"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('data-auto="si"', resp.text)

    # ---- POST /api/confirm/{token}/{answer} --------------------------------

    def test_confirm_answer_invalid_value_is_the_shared_terminal_404(self):
        def _explota(t):
            raise AssertionError("un answer inválido no debe tocar la DB")
        db_booking.booking_request_by_confirm_token = _explota
        resp = self.client.post("/api/confirm/tok-bueno/quizas")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.content, b'{"error": "not found"}')
        self.assertEqual(resp.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_confirm_answer_unknown_token_404(self):
        db_booking.booking_request_by_confirm_token = lambda t: None
        resp = self.client.post("/api/confirm/no-existe/si")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "token_invalido"})

    def test_confirm_answer_past_appointment_410(self):
        row = self._row(turno_dt=datetime.now(timezone.utc) - timedelta(days=2))
        db_booking.booking_request_by_confirm_token = lambda t: row
        resp = self.client.post("/api/confirm/tok-bueno/si")
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json(), {"error": "turno_pasado"})

    def test_confirm_answer_si_is_saved_with_email_channel(self):
        row = self._row()
        db_booking.booking_request_by_confirm_token = lambda t: row
        marks = []
        db_booking.mark_confirmation = lambda sid, valor, canal: marks.append((sid, valor, canal))
        resp = self.client.post("/api/confirm/tok-bueno/si")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["confirmacion"], "si")
        self.assertIn("confirmado", body["mensaje"].lower())
        self.assertEqual(marks, [(601, "si", "email")])


if __name__ == "__main__":
    unittest.main()
