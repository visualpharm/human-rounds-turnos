"""tests/routes/test_cancel_label.py — routes-tier coverage of WHO cancelled
(the product owner, 2026-10-26): the patient's list carries `cancelada_por` — "paciente"
when the patient (or the adult acting for them) cancelled, "centro" when a
desk user did, absent of both when the data does not say (a cancellation
from before `appointment_events`, never guessed) — and the account page, the
assistant and the desk email read it with ONE text:
«Cancelada por vos» / «Cancelada por el centro» / «Cancelada».
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

from app.core import db, i18n  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.modules.chat import chat_tools  # noqa: E402
from app.notifications import send as notifications  # noqa: E402

_SERVICIO = "cardiologia-hospital"
_CENTRO = "hospital-pinamar"


def _cookie(dni):
    db_accounts.create_user(dni, "Paciente", "DeCancela", "1990-01-01", "M", None, "throwaway-pass-12345")
    return {"Cookie": "st_ses=" + db_accounts.create_session(dni)}


def _aprobada(dni, dias=3):
    sid = db_booking.create_booking_request(dni, "Nora", "Domínguez", "1958-01-01", None, _SERVICIO, _CENTRO)
    db_booking.approve(sid, datetime.now(timezone.utc) + timedelta(days=dias))
    return sid


class TestCancelledBy(RouteTestCase):

    def _fila(self, cookie, sid):
        status, body = self._get_json("/api/requests", headers=cookie)
        self.assertEqual(status, 200)
        fila = next(r for r in body["solicitudes"] if str(r["id"]) == str(sid))
        self.assertEqual(fila["estado"], "cancelada")
        return fila

    def test_the_patient_cancelled(self):
        dni = "30999441"
        sid = _aprobada(dni)
        cookie = _cookie(dni)
        status, body = self._post_json(f"/api/requests/{sid}/cancel", {}, headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body["solicitud"]["cancelada_por"], "paciente")
        self.assertEqual(self._fila(cookie, sid)["cancelada_por"], "paciente")

    def test_the_adult_acting_for_a_dependent_counts_as_the_patient(self):
        dni_hijo, dni_madre = "30999442", "30999443"
        sid = db_booking.create_booking_request(dni_hijo, "Hijo", "DeCancela", "2015-01-01", None, _SERVICIO, _CENTRO,
                                        solicitante_dni=dni_madre)
        db_booking.approve(sid, datetime.now(timezone.utc) + timedelta(days=3))
        cookie = _cookie(dni_madre)
        status, _ = self._post_json(f"/api/requests/{sid}/cancel", {}, headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self._fila(cookie, sid)["cancelada_por"], "paciente")

    def test_the_desk_cancelled(self):
        dni = "30999444"
        sid = _aprobada(dni)
        cookie = _cookie(dni)
        with mock.patch.object(notifications, "notify_cancelled"):
            status, _ = self._post_json(
                f"/api/admin/requests/{sid}/cancel", {},
                headers=admin_cookie(usuario="mesa.prueba", rol="admisionista"))
        self.assertEqual(status, 200)
        self.assertEqual(self._fila(cookie, sid)["cancelada_por"], "centro")

    def test_no_recorded_actor_is_not_guessed(self):
        dni = "30999445"
        sid = _aprobada(dni)
        cookie = _cookie(dni)
        db_booking.cancel(sid, actor="paciente")
        # A cancellation from before `appointment_events` (2026-09-24): the row says «cancelada», no event.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM appointment_events WHERE request_id=%s AND kind='cancelada'", (sid,))
        self.assertIsNone(self._fila(cookie, sid)["cancelada_por"])

    def test_the_assistant_reads_the_same_text(self):
        dni = "30999446"
        por_paciente, por_centro, sin_dato = _aprobada(dni, 3), _aprobada(dni, 4), _aprobada(dni, 5)
        db_booking.cancel(por_paciente, actor="paciente")
        db_booking.cancel(por_centro, actor="mesa.prueba")
        db_booking.cancel(sin_dato, actor="paciente")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM appointment_events WHERE request_id=%s AND kind='cancelada'", (sin_dato,))
        _, token = i18n.bind_lang("es")
        try:
            out = chat_tools._tool_mine({"dni": dni}, {}, None)
        finally:
            i18n.unbind(token)
        textos = {r["id"]: r["estado_texto"] for r in out["solicitudes"]}
        self.assertEqual(textos[por_paciente], "Cancelada por vos")
        self.assertEqual(textos[por_centro], "Cancelada por el centro")
        self.assertEqual(textos[sin_dato], "Cancelada")


class TestAdminQueueCancelledBy(RouteTestCase):
    """The desk's own queue (GET /api/admin/requests) carries `cancelada_por` too, from the same event,
    so its row says «por el paciente» / «por el centro» / «Cancelada» — not «por el paciente» for all."""

    def _cola(self, estado="cancelada"):
        status, body = self._get_json(f"/api/admin/requests?status={estado}&limit=500", headers=admin_cookie())
        self.assertEqual(status, 200)
        return {str(r["id"]): r for r in body["solicitudes"]}

    def test_the_queue_row_says_who_cancelled(self):
        dni = "30999451"
        por_paciente, por_centro, sin_dato = _aprobada(dni, 3), _aprobada(dni, 4), _aprobada(dni, 5)
        db_booking.cancel(por_paciente, actor="paciente")
        db_booking.cancel(por_centro, actor="mesa.prueba")
        db_booking.cancel(sin_dato, actor="paciente")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM appointment_events WHERE request_id=%s AND kind='cancelada'", (sin_dato,))
        cola = self._cola()
        self.assertEqual(cola[str(por_paciente)]["cancelada_por"], "paciente")
        self.assertEqual(cola[str(por_centro)]["cancelada_por"], "centro")
        self.assertIsNone(cola[str(sin_dato)]["cancelada_por"])
        # The query's helper column never reaches the wire.
        self.assertNotIn("cancelled_actor", cola[str(por_centro)])

    def test_the_default_view_carries_it_and_only_cancelled_rows(self):
        dni = "30999452"
        cancelada, pendiente = _aprobada(dni, 3), db_booking.create_booking_request(
            "30999453", "Pía", "Pendiente", "1990-01-01", None, _SERVICIO, _CENTRO)
        db_booking.cancel(cancelada, actor="mesa.prueba")
        status, body = self._get_json("/api/admin/requests?limit=500", headers=admin_cookie())
        self.assertEqual(status, 200)
        filas = {str(r["id"]): r for r in body["solicitudes"]}
        self.assertEqual(filas[str(cancelada)]["cancelada_por"], "centro")
        self.assertNotIn("cancelada_por", filas[str(pendiente)])

    def test_the_row_handed_back_after_the_desk_cancel_says_centro(self):
        sid = _aprobada("30999454")
        with mock.patch.object(notifications, "notify_cancelled"):
            status, body = self._post_json(
                f"/api/admin/requests/{sid}/cancel", {},
                headers=admin_cookie(usuario="mesa.prueba", rol="admisionista"))
        self.assertEqual(status, 200)
        self.assertEqual(body["solicitud"]["cancelada_por"], "centro")


if __name__ == "__main__":
    unittest.main()
