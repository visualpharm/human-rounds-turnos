"""test_desk_restore.py — the desk undoes a mistaken mark from the patient's
file (the product owner, 2026-11-05: if the patient did not come, a button to change it,
in case someone pressed it by accident; if the appointment was cancelled,
reschedule it or bring it back).

Two operations, both through the real ASGI app:

- «No vino» marked by mistake goes back to «Vino» through the SAME route the
  day queue uses (`/attendance`), and both marks stay in the audit log.
- A cancelled appointment goes back as it was (`/restore`) only when it was
  an approved appointment, its time has not passed and its slot is still
  free; it is approved again by the ordinary approval path (audit event
  «restaurada», the approval email), and the cancellation no longer counts
  against the patient. Otherwise the request stays cancelled and the file
  offers only «Reprogramar»: an overbooking, a service that no longer gives
  turnos, a time a patient takes while the desk writes (the desk holds the
  minute from its last check until the write).
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="desk-restore-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

import sqlalchemy as sa  # noqa: E402

import _dbtest  # noqa: E402
from app.core import db_booking, db_slots, schema_model, sqlcore, tz  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import admin_patients  # noqa: E402
from app.modules.turnos import slot_holds  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.notifications import send as notifications  # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_desk_restore",
                          seed=lambda: db_booking.get_booking_request(-1))

_SERVICIO = "laboratorio-restore"
_CENTRO = "hospital-pinamar"

HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _cookies(rol="admin", usuario="mesa.restore"):
    return {"st_admin": admin_auth._mint_token(usuario, rol)}


def _aprobada(dni, cuando, apellido="Restaurar"):
    sid = db_booking.create_booking_request(
        dni, "Nora", apellido, "1958-01-01", None, _SERVICIO, _CENTRO)
    db_booking.approve(sid, cuando)
    return sid


class _Case(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin DB de test")
        self.client = TestClient(app_module.app)

    def _cancel(self, sid):
        with patch.object(notifications, "notify_cancelled"):
            r = self.client.post(f"/api/admin/requests/{sid}/cancel", json={},
                                 cookies=_cookies())
        self.assertEqual(r.status_code, 200, r.json())

    def _restore(self, sid, rol="admin"):
        with patch.object(notifications, "notify_approved") as aviso:
            r = self.client.post(f"/api/admin/requests/{sid}/restore", json={},
                                 cookies=_cookies(rol))
        return r, aviso

    def _ficha(self, dni, sid):
        turnos = admin_patients.patient_turnos(dni, {"rol": "admin", "usuario": "mesa.restore"})
        return next(t for t in turnos if t["id"] == sid)


class TestAbsenceCorrection(_Case):

    def test_a_mistaken_no_show_goes_back_to_came_and_both_marks_are_audited(self):
        sid = _aprobada("31555001", datetime.now(timezone.utc) - timedelta(minutes=30))
        for valor in ("ausente", "presente"):
            r = self.client.post(f"/api/admin/requests/{sid}/attendance",
                                 json={"asistencia": valor}, cookies=_cookies())
            self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(db_booking.get_booking_request(sid).get("asistencia"), "presente")
        marcas = [(e["attendance_from"], e["attendance_to"])
                  for e in db_booking.appointment_events(sid) if e["kind"] == "asistencia"]
        self.assertEqual(marcas, [(None, "ausente"), ("ausente", "presente")])
        self.assertEqual(self._ficha("31555001", sid)["asistencia"], "presente")


class TestRestoreCancelled(_Case):

    def test_a_cancelled_future_appointment_goes_back_as_it_was(self):
        cuando = (datetime.now(timezone.utc) + timedelta(days=4)).replace(second=0, microsecond=0)
        sid = _aprobada("31555002", cuando)
        self._cancel(sid)
        self.assertTrue(self._ficha("31555002", sid)["restaurable"])

        r, aviso = self._restore(sid)
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(r.json()["solicitud"]["estado"], "aprobada")

        row = db_booking.get_booking_request(sid)
        self.assertEqual(row.get("estado"), "aprobada")
        self.assertEqual(row.get("turno_dt"), cuando)
        self.assertIsNone(row.get("cancelada_en"))  # the cancel no longer counts against them
        ultimo = db_booking.appointment_events(sid)[-1]
        self.assertEqual((ultimo["kind"], ultimo["actor"], ultimo["status_from"], ultimo["status_to"]),
                         ("restaurada", "mesa.restore", "cancelada", "aprobada"))
        aviso.assert_called_once()  # the patient hears it as an ordinary approval
        self.assertFalse(self._ficha("31555002", sid)["restaurable"])

    def test_a_slot_taken_meanwhile_keeps_it_cancelled_and_offers_no_restore(self):
        cuando = (datetime.now(timezone.utc) + timedelta(days=5)).replace(second=0, microsecond=0)
        sid = _aprobada("31555003", cuando)
        self._cancel(sid)
        _aprobada("31555004", cuando, apellido="Ocupa")
        self.assertFalse(self._ficha("31555003", sid)["restaurable"])

        r, aviso = self._restore(sid)
        self.assertEqual(r.status_code, 409, r.json())
        self.assertEqual(r.json()["error"], "turno_tomado")
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "cancelada")
        aviso.assert_not_called()

    def test_a_past_time_is_not_restored(self):
        cuando = datetime.now(timezone.utc) + timedelta(days=2)
        sid = _aprobada("31555005", cuando)
        self._cancel(sid)
        bk = schema_model.booking_requests
        with db_booking.db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.update(bk).where(bk.c.id == sid).values(
                appointment_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        self.assertFalse(self._ficha("31555005", sid)["restaurable"])
        r, _ = self._restore(sid)
        self.assertEqual(r.status_code, 409, r.json())
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "cancelada")

    def test_an_overbooking_is_not_restored(self):
        sid = _aprobada("31555010", datetime.now(timezone.utc) + timedelta(days=3))
        self._cancel(sid)
        bk = schema_model.booking_requests
        with db_booking.db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.update(bk).where(bk.c.id == sid).values(overbooking=True))
        self.assertFalse(self._ficha("31555010", sid)["restaurable"])
        r, aviso = self._restore(sid)
        self.assertEqual(r.status_code, 409, r.json())
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "cancelada")
        aviso.assert_not_called()

    def test_a_patient_who_takes_the_time_during_the_restore_keeps_it(self):
        cuando = (datetime.now(timezone.utc) + timedelta(days=6)).replace(second=0, microsecond=0)
        sid = _aprobada("31555011", cuando)
        self._cancel(sid)
        local = cuando.astimezone(tz.tzinfo())
        dia, hora = local.strftime("%Y-%m-%d"), local.strftime("%H:%M")
        self.assertTrue(slot_holds.try_take(_SERVICIO, dia, hora, "paciente-restore"))
        try:
            # The desk read what was held a moment before the patient clicked.
            with patch.object(slot_holds, "held_or_offered", return_value=set()):
                r, aviso = self._restore(sid)
            self.assertEqual(r.status_code, 409, r.json())
            self.assertEqual(r.json()["error"], "turno_tomado")
            self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "cancelada")
            aviso.assert_not_called()
        finally:
            db_slots.hold_release("paciente-restore")
        r, _ = self._restore(sid)
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual([h for h in db_slots.holds_active() if str(h.get("token") or "").startswith("mesa:")], [],
                         "the desk's own hold is let go once the turno is written")

    def test_a_service_that_became_walk_in_is_not_restored(self):
        sid = _aprobada("31555012", datetime.now(timezone.utc) + timedelta(days=3))
        self._cancel(sid)
        sin_turnos = [{"id": _SERVICIO, "nombre": "Laboratorio", "modo": "walkin", "centro": _CENTRO}]
        with patch.object(catalog, "services", return_value=sin_turnos):
            self.assertFalse(self._ficha("31555012", sid)["restaurable"])
            r, aviso = self._restore(sid)
        self.assertEqual(r.status_code, 409, r.json())
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "cancelada")
        aviso.assert_not_called()

    def test_a_cancelled_request_that_was_never_approved_is_not_restored(self):
        sid = db_booking.create_booking_request(
            "31555006", "Nora", "Pedido", "1958-01-01", None, _SERVICIO, _CENTRO)
        self._cancel(sid)
        self.assertFalse(self._ficha("31555006", sid)["restaurable"])
        r, _ = self._restore(sid)
        self.assertEqual(r.status_code, 409, r.json())

    def test_an_active_appointment_is_not_restored(self):
        sid = _aprobada("31555007", datetime.now(timezone.utc) + timedelta(days=3))
        r, _ = self._restore(sid)
        self.assertEqual(r.status_code, 409, r.json())
        self.assertNotIn("restaurable", {k for k, v in self._ficha("31555007", sid).items() if v})

    def test_an_archived_patients_appointment_comes_back_only_after_the_patient(self):
        # Archiving cancels the turnos ahead; while the patient stays archived
        # the file offers one «Restaurar» — the patient's own — never a turno
        # for someone who cannot sign in (2026-11-06).
        from app.core import db_accounts
        clave = "archivo-" + "restore"
        db_accounts.create_user("31555013", "Nora", "Archivada", "1958-01-01", "F",
                                "+5492254155013", clave)
        sid = _aprobada("31555013", datetime.now(timezone.utc) + timedelta(days=3))
        self._cancel(sid)
        db_accounts.user_admin_update("31555013", {"activo": False})
        self.assertFalse(self._ficha("31555013", sid)["restaurable"])
        r, aviso = self._restore(sid)
        self.assertEqual(r.status_code, 409, r.json())
        self.assertEqual(r.json()["error"], "paciente_archivado")
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "cancelada")
        aviso.assert_not_called()

        db_accounts.user_admin_update("31555013", {"activo": True})
        self.assertTrue(self._ficha("31555013", sid)["restaurable"])
        r, _ = self._restore(sid)
        self.assertEqual(r.status_code, 200, r.json())

    def test_a_doctor_cannot_restore_a_request_not_assigned_to_them(self):
        sid = _aprobada("31555008", datetime.now(timezone.utc) + timedelta(days=3))
        self._cancel(sid)
        r, _ = self._restore(sid, rol="medico")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(db_booking.get_booking_request(sid).get("estado"), "cancelada")

    def test_without_session_401(self):
        sid = _aprobada("31555009", datetime.now(timezone.utc) + timedelta(days=3))
        r = self.client.post(f"/api/admin/requests/{sid}/restore", json={})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
