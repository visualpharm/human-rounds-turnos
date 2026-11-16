"""tests/routes/test_cancel_waitlist_trigger.py — routes-tier coverage: POST
/api/requests/<id>/cancel (`booking_requests._cancel`) as the WIRING point
for waitlist.py's re-offer engine (see waitlist.py's own module docstring,
"the trigger is the patient's Cancel button"). Seeds an approved
appointment plus a waitlisted patient whose OWN appointment is later
(`waitlist.opt_in`, the real function the opt-in route calls, never a
hand-built row), drives the real HTTP cancel, and asserts the durable
side effect: a fresh `appointment_offers` row for the waitlisted
candidate's slot -- the thing `waitlist.release_slot` is FOR, not just
that cancel returned 200. See docs/roadmap/tier3-harness-design.md, slice
4 ("cancel + waitlist trigger" is one of the named coverage targets).
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.modules.turnos import waitlist  # noqa: E402

_SERVICIO = "cardiologia-hospital"
_CENTRO = "hospital-pinamar"


def _crear_cuenta(dni):
    db_accounts.create_user(dni, "Paciente", "DeCancela", "1990-01-01", "M", None,
                    "throwaway-pass-12345")
    token = db_accounts.create_session(dni)
    return {"Cookie": "st_ses=" + token}


class TestCancelWaitlistTrigger(RouteTestCase):

    def test_cancel_own_future_appointment_offers_the_freed_slot(self):
        ahora = datetime.now(timezone.utc)
        slot_liberado = ahora + timedelta(days=2)
        slot_mas_lejano = ahora + timedelta(days=5)

        # Patient A: the one who's about to cancel.
        dni_a = "30999331"
        sid_a = db_booking.create_booking_request(
            dni_a, "Ana", "Cancela", "1988-01-01", None, _SERVICIO, _CENTRO)
        db_booking.approve(sid_a, slot_liberado)
        cookie_a = _crear_cuenta(dni_a)

        # Patient B: already has a LATER appointment, opted in to the
        # waitlist for something sooner -- real function, not a hand-built
        # row.
        dni_b = "30999332"
        sid_b = db_booking.create_booking_request(
            dni_b, "Beto", "Espera", "1985-01-01", None, _SERVICIO, _CENTRO)
        db_booking.approve(sid_b, slot_mas_lejano)
        waitlist.opt_in(sid_b, dni_b, "beto@example.com", _SERVICIO, _CENTRO,
                        slot_mas_lejano)

        status, body = self._post_json(
            f"/api/requests/{sid_a}/cancel", {}, headers=cookie_a)
        self.assertEqual(status, 200)
        self.assertEqual(body["solicitud"]["estado"], "cancelada")

        # Durable effect: a live offer for the freed slot, aimed at B.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT waitlist_id, status FROM appointment_offers "
                "WHERE service_id=%s AND center_slug=%s AND slot_at=%s",
                (_SERVICIO, _CENTRO, slot_liberado))
            ofertas = cur.fetchall()
        self.assertEqual(len(ofertas), 1)
        self.assertEqual(ofertas[0]["status"], "ofrecida")

    def test_cancel_someone_elses_request_404(self):
        sid = db_booking.create_booking_request(
            "30999333", "Otro", "Titular", "1990-01-01", None, _SERVICIO, _CENTRO)
        cookie = _crear_cuenta("30999334")  # a different account entirely
        status, body = self._post_json(
            f"/api/requests/{sid}/cancel", {}, headers=cookie)
        self.assertEqual(status, 404)

    def test_cancel_without_session_401(self):
        sid = db_booking.create_booking_request(
            "30999335", "Solo", "Titular", "1990-01-01", None, _SERVICIO, _CENTRO)
        status, _ = self._post_json(f"/api/requests/{sid}/cancel", {})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
