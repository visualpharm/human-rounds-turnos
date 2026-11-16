"""test_appointment_offer_router_native.py — native-migration campaign
(docs/roadmap/native-migration-campaign-20260826.md), wave 3 family W3a:
transport parity for `appointment_offer_router.py`'s three ASGI endpoints
against `appointment_offer.py`'s extracted `build_offer_page`/`accept_offer`/
`reject_offer_action` domain functions. Same TestClient(app) pattern as
test_appointment_confirm_router_native.py; same monkeypatch style (patch
module attributes directly, so each native endpoint sees the stub at call
time. No real Postgres needed: `waitlist.claim`/
`waitlist.reject_offer`/`waitlist_store.offer_by_token`/etc. are stubbed."""

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
from app.modules.turnos import appointment_offer  # noqa: E402


class TestAppointmentOfferRouterNative(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

        self._orig_offer_by_token = appointment_offer.store.offer_by_token
        self._orig_waitlist_row = appointment_offer.store.waitlist_row
        self._orig_claim = appointment_offer.waitlist.claim
        self._orig_reject = appointment_offer.waitlist.reject_offer
        self._orig_get_booking = appointment_offer.booking_store.get_booking_request
        self._orig_reassign = appointment_offer.booking_store.reassign_appointment

    def tearDown(self):
        appointment_offer.store.offer_by_token = self._orig_offer_by_token
        appointment_offer.store.waitlist_row = self._orig_waitlist_row
        appointment_offer.waitlist.claim = self._orig_claim
        appointment_offer.waitlist.reject_offer = self._orig_reject
        appointment_offer.booking_store.get_booking_request = self._orig_get_booking
        appointment_offer.booking_store.reassign_appointment = self._orig_reassign

    def _offer(self, **over):
        offer = {
            "servicio_id": 9001, "centro_slug": "no-existe-centro",
            "estado": "ofrecida",
            "slot_dt": datetime.now(timezone.utc) + timedelta(days=1),
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        offer.update(over)
        return offer

    # ---- GET /appointment/offer/{token} ------------------------------------

    def test_offer_page_unknown_token_is_not_a_dead_end(self):
        appointment_offer.store.offer_by_token = lambda t: None
        resp = self.client.get("/appointment/offer/no-existe-este-token")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("Este turno ya no está disponible", resp.text)
        self.assertIn("Mis turnos", resp.text)

    def test_offer_page_invalid_token_shape_is_not_a_dead_end(self):
        # Never reaches the store: both native POST endpoints share this guard.
        appointment_offer.store.offer_by_token = lambda t: (_ for _ in ()).throw(
            AssertionError("a malformed token must not reach the store"))
        resp = self.client.get("/appointment/offer/" + ("x" * 100))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Este turno ya no está disponible", resp.text)

    def test_offer_page_valid_open_offer_asks_the_question(self):
        offer = self._offer()
        appointment_offer.store.offer_by_token = lambda t: offer if t == "tok-bueno" else None
        resp = self.client.get("/appointment/offer/tok-bueno")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Tomar este turno", resp.text)
        self.assertIn("No lo quiero", resp.text)
        self.assertIn("tok-bueno", resp.text)

    def test_offer_page_expired_offer_is_not_a_dead_end(self):
        offer = self._offer(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        appointment_offer.store.offer_by_token = lambda t: offer
        resp = self.client.get("/appointment/offer/tok-vencido")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Este turno ya no está disponible", resp.text)

    def test_offer_page_already_accepted_shows_confirmation(self):
        offer = self._offer(estado="aceptada")
        appointment_offer.store.offer_by_token = lambda t: offer
        resp = self.client.get("/appointment/offer/tok-aceptado")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("quedó confirmado", resp.text)

    # ---- POST /api/offer/{token}/accept ------------------------------------

    def test_accept_invalid_token_400(self):
        def _explota(t):
            raise AssertionError("a malformed token must not reach waitlist.claim")
        appointment_offer.waitlist.claim = _explota
        resp = self.client.post("/api/offer/" + ("x" * 100) + "/accept")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "token_invalido"})

    def test_accept_no_existe_is_404(self):
        appointment_offer.waitlist.claim = lambda t: (False, "no_existe")
        resp = self.client.post("/api/offer/no-existe/accept")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_existe"})

    def test_accept_other_decline_reason_is_410(self):
        appointment_offer.waitlist.claim = lambda t: (False, "vencida")
        resp = self.client.post("/api/offer/tok-vencido/accept")
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json(), {"error": "vencida"})

    def test_accept_success_without_a_reassignable_row(self):
        oferta = {"lista_espera_id": 1, "slot_dt": datetime.now(timezone.utc)}
        appointment_offer.waitlist.claim = lambda t: (True, oferta)
        appointment_offer.store.waitlist_row = lambda i: {"solicitud_id": 42}
        appointment_offer.booking_store.get_booking_request = lambda sid: None
        resp = self.client.post("/api/offer/tok-bueno/accept")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "solicitud_id": None})

    def test_accept_success_reassigns_and_notifies(self):
        slot_dt = datetime.now(timezone.utc)
        oferta = {"lista_espera_id": 1, "slot_dt": slot_dt}
        appointment_offer.waitlist.claim = lambda t: (True, oferta)
        appointment_offer.store.waitlist_row = lambda i: {"solicitud_id": 42}
        appointment_offer.booking_store.get_booking_request = lambda sid: {"id": sid, "estado": "aprobada"}
        appointment_offer.booking_store.reassign_appointment = (
            lambda sid, dt, hsi, actor=None, slot_capacity=None: {"id": sid, "turno_dt": dt})

        from app.notifications import send as notifications
        orig_notify = notifications.notify_approved
        notified = []
        notifications.notify_approved = lambda row, handler=None: notified.append(row)
        try:
            resp = self.client.post("/api/offer/tok-bueno/accept")
        finally:
            notifications.notify_approved = orig_notify

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "solicitud_id": 42})
        self.assertEqual(notified, [{"id": 42, "turno_dt": slot_dt}])


    def test_accept_cancelada_row_returns_410(self):
        """Accept on a cancelada row returns 410."""
        slot_dt = datetime.now(timezone.utc)
        oferta = {"lista_espera_id": 1, "slot_dt": slot_dt}
        appointment_offer.waitlist.claim = lambda t: (True, oferta)
        appointment_offer.store.waitlist_row = lambda i: {"solicitud_id": 42}
        appointment_offer.booking_store.get_booking_request = lambda sid: {"id": 42, "estado": "cancelada"}

        def fail_on_reassign(sid, dt, hsi):
            raise AssertionError("should not reassign a cancelada row")
        appointment_offer.booking_store.reassign_appointment = fail_on_reassign

        resp = self.client.post("/api/offer/tok-cancelada/accept")
        self.assertEqual(resp.status_code, 410)

    def test_accept_aprobada_with_hsi_failure_skips_with_200(self):
        """Accept on aprobada with hsi_appointment_id and EMR sync off returns skip+200."""
        slot_dt = datetime.now(timezone.utc)
        oferta = {"lista_espera_id": 1, "slot_dt": slot_dt}
        appointment_offer.waitlist.claim = lambda t: (True, oferta)
        appointment_offer.store.waitlist_row = lambda i: {"solicitud_id": 42}
        appointment_offer.booking_store.get_booking_request = (
            lambda sid: {"id": 42, "estado": "aprobada", "hsi_appointment_id": "77"})

        reassigned = []
        appointment_offer.booking_store.reassign_appointment = (
            lambda sid, dt, hsi, actor=None, slot_capacity=None: reassigned.append({"sid": sid, "dt": dt, "hsi": hsi}) or
            {"id": sid, "turno_dt": dt})

        from app.modules.turnos import booking_requests
        orig_emr_enabled = booking_requests._emr_sync_enabled
        booking_requests._emr_sync_enabled = lambda: False

        from app.notifications import send as notifications
        orig_notify = notifications.notify_approved
        notified = []
        notifications.notify_approved = lambda row, handler=None: notified.append(row)

        try:
            resp = self.client.post("/api/offer/tok-hsi-fail/accept")
        finally:
            booking_requests._emr_sync_enabled = orig_emr_enabled
            notifications.notify_approved = orig_notify

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "solicitud_id": 42})
        # Verify that reassign was called with skip
        self.assertEqual(len(reassigned), 1)
        self.assertEqual(reassigned[0]["hsi"], {"estado": "skip"})

    # ---- POST /api/offer/{token}/reject -------------------------------------

    def test_reject_invalid_token_400(self):
        def _explota(t, handler=None):
            raise AssertionError("a malformed token must not reach waitlist.reject_offer")
        appointment_offer.waitlist.reject_offer = _explota
        resp = self.client.post("/api/offer/" + ("x" * 100) + "/reject")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "token_invalido"})

    def test_reject_success_passes_through_ok_true(self):
        appointment_offer.waitlist.reject_offer = lambda t, handler=None: (True, {"servicio_id": 1})
        resp = self.client.post("/api/offer/tok-bueno/reject")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_reject_false_is_still_200(self):
        # A declined offer is still a successful request with ok=false.
        appointment_offer.waitlist.reject_offer = lambda t, handler=None: (False, "no_existe")
        resp = self.client.post("/api/offer/tok-desconocido/reject")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": False})


if __name__ == "__main__":
    unittest.main()
