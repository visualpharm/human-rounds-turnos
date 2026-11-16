"""test_hsi_busy.py — an appointment created straight in HSI takes its hour here too.

The mirror (`hsi_appointments` + `hsi_doctor_schedules`) is read through
`db_hsi.hsi_future_unlinked_appointments` (patched here; its SQL is pinned in
test_db_hsi_store). What is pinned: which mirrored rows count, and that the one reader
reaches every place that counts free hours.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_hsi_busy.py
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_hsi, tz  # noqa: E402
from app.modules.turnos import booking_capacity, doctor_appointments, hsi_busy, hsi_journal, slot_holds  # noqa: E402
from app.patient_web import catalog  # noqa: E402

_AGENDA = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "08:00", "hasta": "12:00", "cada_min": 20}
_DOC = {"id": "dr-perez", "nombre": "Pérez Juan", "centro": "centro-a",
        "agenda": {"franjas": [dict(_AGENDA, servicio="cardiologia")]}}
_OTHER = {"id": "dr-gomez", "nombre": "Gómez Ana", "centro": "centro-a", "agenda": {"franjas": [_AGENDA]}}
_SERVICE = {"id": "cardiologia", "nombre": "Cardiología", "centro": "centro-a", "modo": "solicitud"}
_DIA = (datetime.now(tz.tzinfo()) + timedelta(days=9)).strftime("%Y-%m-%d")


def _row(hora, name="Pérez Juan", dia=None, state="scheduled", aid="a1"):
    return {"hsi_appointment_id": aid, "hsi_diary_id": "d1",
            "appointment": {"id": aid, "state": state, "scheduled_at": f"{dia or _DIA}T{hora}:00-03:00"},
            "schedule": {"professional_name": name} if name else None}


class TestHsiBusy(unittest.TestCase):
    rows = []

    def setUp(self):
        hsi_busy.invalidate()
        self.addCleanup(hsi_busy.invalidate)
        pairs = ((catalog, "doctors", lambda: [_DOC, _OTHER]),
                 (catalog, "doctor_doc", lambda i, **k: next((d for d in (_DOC, _OTHER) if d["id"] == i), None)),
                 (catalog, "services", lambda: [_SERVICE]),
                 (slot_holds.db_slots, "holds_active", lambda: []),
                 (slot_holds, "_approved_future_slots", lambda: []),
                 (slot_holds, "_live_offer_slots", lambda: []),
                 (booking_capacity.store, "approved_in_range", lambda *a, **k: []),
                 (hsi_journal, "_emr_selected", lambda: True),
                 (db_hsi, "hsi_future_unlinked_appointments", lambda day: list(self.rows)))
        for target, name, fn in pairs:
            patcher = mock.patch.object(target, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        slot_holds.invalidate()
        self.addCleanup(slot_holds.invalidate)

    def test_a_scheduled_appointment_takes_the_doctors_hour_everywhere(self):
        self.rows = [_row("09:00")]
        day = datetime.fromisoformat(_DIA).date()
        self.assertIn(("cardiologia", _DIA, "09:00"), slot_holds.taken())
        self.assertFalse(slot_holds.try_take("cardiologia", _DIA, "09:00", "tok"))
        self.assertIn((_DIA, "09:00"), booking_capacity.doctor_busy_minutes("Pérez Juan", day, day))
        libres = doctor_appointments._free_of_day(_DOC, _DIA, set())
        self.assertNotIn("09:00", libres)
        self.assertIn("08:40", libres)

    def test_counts_as_one_used_place_for_an_overbooking(self):
        self.rows = [_row("09:00")]
        turno = datetime.fromisoformat(f"{_DIA}T09:00:00-03:00")
        self.assertEqual(len(booking_capacity._doctor_appointments_at_time("Pérez Juan", turno)), 1)
        self.assertEqual(len(booking_capacity._doctor_appointments_at_time("Pérez Juan", turno, center_slug="centro-a")), 0)

    def test_nothing_mirrored_takes_nothing(self):
        self.rows = []
        self.assertFalse({k for k in slot_holds.taken() if k[1] == _DIA})

    def test_a_doctor_that_does_not_resolve_takes_nothing_and_does_not_fail(self):
        self.rows = [_row("09:00", name="Nadie Conocido"), _row("09:20", name=None, aid="a2")]
        self.assertFalse({k for k in slot_holds.taken() if k[1] == _DIA})

    def test_a_minute_outside_the_agenda_takes_nothing(self):
        self.rows = [_row("15:00")]
        self.assertFalse({k for k in slot_holds.taken() if k[1] == _DIA})

    def test_a_past_appointment_takes_nothing(self):
        past = (datetime.now(tz.tzinfo()) - timedelta(days=2)).strftime("%Y-%m-%d")
        self.rows = [_row("09:00", dia=past)]
        self.assertEqual(hsi_busy.busy_by_service(), set())

    def test_garbage_in_the_mirror_never_raises(self):
        bad = _row("09:00")
        bad["appointment"]["scheduled_at"] = "mañana a la tarde"
        self.rows = [bad, {"hsi_appointment_id": "x", "appointment": None, "schedule": None}]
        self.assertEqual(hsi_busy.busy_by_service(), set())

    def test_sync_off_keeps_the_old_behaviour(self):
        self.rows = [_row("09:00")]
        with _env.patched_env(TURNOS_HSI_SYNC_OFF="1"):
            hsi_busy.invalidate()
            self.assertEqual(hsi_busy.busy_by_service(), set())
            self.assertEqual(hsi_busy.busy_of_doctor("dr-perez"), set())

    def test_no_emr_routed_takes_nothing(self):
        self.rows = [_row("09:00")]
        with mock.patch.object(hsi_journal, "_emr_selected", lambda: False):
            hsi_busy.invalidate()
            self.assertEqual(hsi_busy.busy_by_service(), set())

    def test_a_reader_failure_degrades_to_nothing(self):
        def boom(day):
            raise RuntimeError("mirror down")
        with mock.patch.object(db_hsi, "hsi_future_unlinked_appointments", boom):
            hsi_busy.invalidate()
            self.assertEqual(hsi_busy.busy_by_service(), set())


if __name__ == "__main__":
    unittest.main()
