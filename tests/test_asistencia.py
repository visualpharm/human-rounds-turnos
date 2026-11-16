"""Regression coverage for the no-show policy and attendance audit trail."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="", TURNOS_NO_SHOW_LIMIT="3")

from app.auth import admin_auth  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.modules.turnos import hsi_journal  # noqa: E402
from app.modules.turnos import booking_create  # noqa: E402

import _dbtest  # noqa: E402

_ART = timezone(timedelta(hours=-3))
_db = _dbtest.PerModuleDb("sana_turnos_test_asistencia", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB = _db.have_db
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset():
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE attendance_events, booking_requests RESTART IDENTITY CASCADE")


def _past_request(dni="30111222"):
    sid = db_booking.create_booking_request(dni, "Ana", "Prueba", "1980-01-01", "wa:1", "cardio", "centro")
    db_booking.approve(sid, datetime.now(_ART) - timedelta(days=1), {"estado": "skip"})
    return sid


class AttendanceDbTests(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest("Postgres unavailable")
        _reset()

    def test_absences_block_at_three_and_correction_unblocks_without_erasing_history(self):
        ids = [_past_request("30111222") for _ in range(3)]
        for sid in ids:
            db_booking.mark_attendance(sid, "ausente", "operadora")
        summary = db_booking.attendance_summary_dni("30111222", 3)
        self.assertEqual(summary["visitas"], 3)
        self.assertEqual(summary["presentes"], 0)
        self.assertEqual(summary["faltas"], 3)
        self.assertTrue(summary["bloqueado_web"])

        db_booking.mark_attendance(ids[0], "presente", "operadora")
        summary = db_booking.attendance_summary_dni("30111222", 3)
        self.assertEqual(summary["visitas"], 3)
        self.assertEqual(summary["presentes"], 1)
        self.assertEqual(summary["faltas"], 2)
        self.assertFalse(summary["bloqueado_web"])
        self.assertEqual(len(summary["historial"]), 4)
        self.assertEqual(summary["historial"][0]["de_asistencia"], "ausente")
        self.assertEqual(summary["historial"][0]["a_asistencia"], "presente")
        self.assertIsInstance(summary["historial"][0]["created"], str)

    def test_unlinked_hsi_absence_counts_once_and_is_visible_in_history(self):
        db_hsi.hsi_mirror_patient("501", "30111225", {"id": "501", "document_number": "30111225"})
        db_hsi.hsi_mirror_appointment("601", "11", "501", {
            "id": "601", "schedule_id": "11", "patient_id": "501",
            "scheduled_at": "2026-07-10T09:00:00-03:00", "state": "no_show",
            "patient": {"id": "501", "document_number": "30111225"},
        })
        summary = db_booking.attendance_summary_dni("30111225", 3)
        self.assertEqual(summary["visitas"], 1)
        self.assertEqual(summary["faltas"], 1)
        self.assertEqual(summary["faltas_hsi"], 1)
        self.assertEqual(summary["historial"][0]["operador"], "HSI")

        # Once linked, the same HSI record is represented by the local mark
        # and must not be counted a second time.
        sid = _past_request("30111225")
        db_booking.update_hsi(sid, {"estado": "ok", "appointment_id": "601", "patient_id": "501"})
        db_booking.mark_attendance(sid, "ausente", "HSI", origen="hsi", exigir_no_futuro=False)
        summary = db_booking.attendance_summary_dni("30111225", 3)
        self.assertEqual(summary["faltas"], 1)
        self.assertEqual(summary["faltas_hsi"], 0)

    def test_only_a_non_future_day_approved_turn_can_be_marked(self):
        sid = db_booking.create_booking_request("30111223", "Ana", "Prueba", "1980-01-01", "wa:1", "cardio", "centro")
        db_booking.approve(sid, datetime.now(_ART) + timedelta(days=1), {"estado": "skip"})
        with self.assertRaisesRegex(ValueError, "asistencia_no_elegible"):
            db_booking.mark_attendance(sid, "ausente", "operadora")

    def test_a_same_day_turn_is_markable_even_if_the_hour_has_not_happened_yet(self):
        # Regla relajada (responsable del producto, 2026-07-24): el médico marca a medida que
        # atiende, no recién cuando pasó la hora exacta del turno — un turno
        # de HOY, más tarde que ahora, ya es elegible; sólo el día siguiente
        # en adelante sigue bloqueado (ver test anterior). `replace(hour=23,
        # minute=59)` en vez de `+timedelta(hours=1)` para no depender de la
        # hora real en que corre el test (evita cruzar medianoche ART).
        ahora = datetime.now(_ART)
        turno = ahora.replace(hour=23, minute=59, second=0, microsecond=0)
        if turno <= ahora:
            turno = ahora
        sid = db_booking.create_booking_request("30111226", "Ana", "Prueba", "1980-01-01", "wa:1", "cardio", "centro")
        db_booking.approve(sid, turno, {"estado": "skip"})
        row = db_booking.mark_attendance(sid, "presente", "operadora")
        self.assertEqual(row["asistencia"], "presente")

    def test_admin_endpoint_records_operator_and_returns_visible_summary(self):
        from fastapi.testclient import TestClient
        from app import main as app_module
        sid = _past_request()
        token = admin_auth._mint_token(admin_auth._admin_user())
        client = TestClient(app_module.app)
        r = client.post("/api/admin/requests/%s/attendance" % sid,
                        json={"asistencia": "ausente"},
                        cookies={"st_admin": token})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["solicitud"]["asistencia"], "ausente")
        self.assertEqual(body["solicitud"]["asistencia_resumen"]["faltas"], 1)
        self.assertEqual(body["solicitud"]["asistencia_resumen"]["historial"][0]["operador"], "admin")
        self.assertEqual(body["hsi_sync"], "sin_turno_hsi_vinculado")

    def test_web_create_is_denied_for_final_patient_dni_after_limit(self):
        for _ in range(3):
            db_booking.mark_attendance(_past_request("30111224"), "ausente", "operadora")
        usuario = {"dni": "30111224", "nombre": "Ana", "apellido": "Prueba",
                   "nacimiento": "1980-01-01", "sexo": "F"}
        status, payload = booking_create.create_request(
            usuario, {"servicio_id": "cardio", "centro_slug": "centro"})
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "bloqueado_por_ausencias")


class AttendanceOutboxTests(unittest.TestCase):
    def test_attendance_is_a_deterministic_outbox_intent(self):
        row = {"id": 7, "hsi_appointment_id": "44"}
        queued = []
        with patch("app.core.db_hsi.hsi_checkpoint_get", return_value={"appointment_id": "44", "schedule_id": "2",
                                                                 "scheduled_at": "2026-07-20T09:00:00-03:00",
                                                                 "state": "scheduled"}), \
             patch("app.core.db_hsi.hsi_outbox_enqueue", side_effect=lambda *args: queued.append(args) or {"id": 1}):
            hsi_journal.enqueue_attendance(row, "ausente")
        self.assertEqual(queued[0][2], "attendance")
        self.assertEqual(queued[0][3]["asistencia"], "ausente")
        self.assertTrue(queued[0][4].startswith("appointment-attendance:"))


if __name__ == "__main__":
    unittest.main()
