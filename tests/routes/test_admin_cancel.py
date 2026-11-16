"""tests/routes/test_admin_cancel.py — routes-tier coverage: POST
/api/admin/requests/<id>/cancel (`booking_decisions._desk_cancel_core`).
La contraparte técnica (hospital, 2026-10-19): «Hay que tener la opción de cancelar/eliminar
turno» — the desk takes an appointment out of «Pacientes de hoy». Covers the
durable transition (status -> 'cancelada', the row and its history kept,
the desk user as the event's actor), the freed slot reaching the waitlist
exactly as a patient's own cancel does, the email to the patient, the
past-appointment and already-cancelled guards, and the doctor's narrower
scope (a request not assigned to them is a 404).
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.modules.turnos import waitlist  # noqa: E402
from app.notifications import send as notifications  # noqa: E402

_SERVICIO = "cardiologia-hospital"
_CENTRO = "hospital-pinamar"


def _aprobada(dni, cuando):
    sid = db_booking.create_booking_request(
        dni, "Nora", "Domínguez", "1958-01-01", None, _SERVICIO, _CENTRO)
    db_booking.approve(sid, cuando)
    return sid


class TestAdminCancel(RouteTestCase):

    def test_desk_cancels_a_future_appointment_and_the_slot_is_offered(self):
        ahora = datetime.now(timezone.utc)
        slot = ahora + timedelta(days=3)
        sid = _aprobada("30999661", slot)

        # Someone with a later appointment waits for an earlier one.
        sid_b = db_booking.create_booking_request(
            "30999662", "Beto", "Espera", "1985-01-01", None, _SERVICIO, _CENTRO)
        db_booking.approve(sid_b, ahora + timedelta(days=9))
        waitlist.opt_in(sid_b, "30999662", "beto@example.com", _SERVICIO, _CENTRO,
                        ahora + timedelta(days=9))

        with mock.patch.object(notifications, "notify_cancelled") as aviso:
            status, body = self._post_json(
                f"/api/admin/requests/{sid}/cancel", {},
                headers=admin_cookie(usuario="mesa.prueba", rol="admisionista"))
        self.assertEqual(status, 200)
        self.assertEqual(body["solicitud"]["estado"], "cancelada")

        row = db_booking.get_booking_request(sid)
        self.assertEqual(row.get("estado"), "cancelada")  # kept, never deleted
        ultimo = db_booking.appointment_events(sid)[-1]
        self.assertEqual((ultimo["kind"], ultimo["actor"]), ("cancelada", "mesa.prueba"))

        aviso.assert_called_once()
        self.assertEqual(aviso.call_args.args[0]["estado"], "cancelada")

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM appointment_offers "
                "WHERE service_id=%s AND center_slug=%s AND slot_at=%s",
                (_SERVICIO, _CENTRO, slot))
            ofertas = cur.fetchall()
        self.assertEqual([o["status"] for o in ofertas], ["ofrecida"])

    def test_a_past_appointment_is_not_cancelled_409(self):
        sid = _aprobada("30999663", datetime.now(timezone.utc) - timedelta(hours=2))
        status, _ = self._post_json(
            f"/api/admin/requests/{sid}/cancel", {}, headers=admin_cookie())
        self.assertEqual(status, 409)
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "aprobada")

    def test_cancelling_twice_409(self):
        sid = _aprobada("30999664", datetime.now(timezone.utc) + timedelta(days=2))
        with mock.patch.object(notifications, "notify_cancelled"):
            first, _ = self._post_json(
                f"/api/admin/requests/{sid}/cancel", {}, headers=admin_cookie())
            second, _ = self._post_json(
                f"/api/admin/requests/{sid}/cancel", {}, headers=admin_cookie())
        self.assertEqual((first, second), (200, 409))

    def test_medico_cannot_cancel_a_request_not_assigned_to_them_404(self):
        sid = _aprobada("30999665", datetime.now(timezone.utc) + timedelta(days=2))
        status, _ = self._post_json(
            f"/api/admin/requests/{sid}/cancel", {}, headers=admin_cookie(rol="medico"))
        self.assertEqual(status, 404)
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "aprobada")

    def test_without_session_401(self):
        sid = _aprobada("30999666", datetime.now(timezone.utc) + timedelta(days=2))
        status, _ = self._post_json(f"/api/admin/requests/{sid}/cancel", {})
        self.assertEqual(status, 401)


class TestCancelEmail(unittest.TestCase):

    def test_says_it_is_cancelled_and_offers_another(self):
        row = {"estado": "cancelada", "servicio_id": _SERVICIO, "dni": "30999667",
               "turno_dt": datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)}
        with mock.patch.object(notifications, "_destination_email", return_value="nora@example.com"), \
                mock.patch.object(notifications, "_destination_lang", return_value="es"), \
                mock.patch.object(notifications, "_patient_name", return_value="Nora"), \
                mock.patch.object(notifications.mail, "send") as send:
            notifications.notify_cancelled(row)
        send.assert_called_once()
        to, asunto, texto = send.call_args.args[:3]
        self.assertEqual(to, "nora@example.com")
        self.assertTrue(asunto.startswith("Se canceló tu turno de "))
        self.assertIn("fue cancelado por el centro", texto)
        self.assertIn("Pedir otro turno: ", texto)
        self.assertIn("/service/" + _SERVICIO, texto)

    def test_no_email_on_the_account_sends_nothing(self):
        with mock.patch.object(notifications, "_destination_email", return_value=None), \
                mock.patch.object(notifications.mail, "send") as send:
            notifications.notify_cancelled({"servicio_id": _SERVICIO, "dni": "1"})
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
