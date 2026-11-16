"""test_list_approved_future_for_doctor.py — db_booking.list_approved_future_for_doctor
(2026-08-06 audit: db_booking.list_approved_future was a hospital-wide unbounded
SELECT * backing the doctor ICS feed, "Mis turnos" and per-doctor stats —
this pushes the doctor filter into SQL instead of pulling every OTHER
doctor's rows just to throw them away in Python).

Covers per-doctor filter correctness: a doctor only sees their OWN approved
future rows, rows resolved to a DIFFERENT doctor's id are excluded, rows with
no assigned_doctor_id resolved yet (legacy / name-only) are still included
(so the name-fallback matching in doctor_appointments._belongs_to keeps
working), and date bounds are pushed into SQL. Also exercises
doctor_appointments.appointments_of end-to-end, since that's the real caller
(ICS feed + "Mis turnos" + stats)."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_catalog  # noqa: E402
import _dbtest  # noqa: E402

_ART = timezone(timedelta(hours=-3))

_db = _dbtest.PerModuleDb("sana_turnos_test_future_for_doctor",
                          seed=lambda: db_catalog.catalog_list("centros"))


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


def _crear_aprobada(dni, servicio_id, turno_dt, medico_asignado=None, medico_asignado_id=None):
    sid = db_booking.create_booking_request(dni, "Ana", "Prueba", "1980-01-01", "wa:1",
                                     servicio_id, "centro")
    db_booking.approve(sid, turno_dt, {"estado": "skip"}, medico_asignado=medico_asignado,
               medico_asignado_id=medico_asignado_id)
    return sid


class ListApprovedFutureForDoctorTest(unittest.TestCase):
    def setUp(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            conn.commit()

    def test_returns_empty_without_medico_id(self):
        self.assertEqual(db_booking.list_approved_future_for_doctor(None), [])
        self.assertEqual(db_booking.list_approved_future_for_doctor(""), [])

    def test_only_this_doctors_rows_come_back(self):
        ahora = datetime.now(_ART)
        sid_a = _crear_aprobada("30111001", "cardio", ahora + timedelta(days=1),
                                 medico_asignado="Doctora A", medico_asignado_id="doc-a")
        _crear_aprobada("30111002", "cardio", ahora + timedelta(days=2),
                         medico_asignado="Doctor B", medico_asignado_id="doc-b")

        rows = db_booking.list_approved_future_for_doctor("doc-a", desde=ahora)
        ids = [r["id"] for r in rows]
        self.assertIn(sid_a, ids)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["medico_asignado_id"], "doc-a")

    def test_legacy_rows_without_id_are_still_included(self):
        """A row whose assigned_doctor_id was never resolved (legacy, or the
        operator only picked a free-text name) has to keep showing up for
        EVERY doctor's query — the SQL can't do the tolerant name match, so
        it hands the row over and lets the caller's own name-matching decide
        (see doctor_appointments._belongs_to)."""
        ahora = datetime.now(_ART)
        sid_legacy = _crear_aprobada("30111003", "cardio", ahora + timedelta(days=1),
                                      medico_asignado="Doctora Legacy", medico_asignado_id=None)
        _crear_aprobada("30111004", "cardio", ahora + timedelta(days=1),
                         medico_asignado="Doctor B", medico_asignado_id="doc-b")

        rows = db_booking.list_approved_future_for_doctor("doc-a", desde=ahora)
        ids = [r["id"] for r in rows]
        self.assertIn(sid_legacy, ids)
        self.assertNotIn(sid_legacy + 1, ids)  # doc-b's row must be excluded

    def test_desde_hasta_bounds_are_enforced(self):
        ahora = datetime.now(_ART)
        sid_soon = _crear_aprobada("30111005", "cardio", ahora + timedelta(days=1),
                                    medico_asignado_id="doc-a")
        sid_far = _crear_aprobada("30111006", "cardio", ahora + timedelta(days=30),
                                   medico_asignado_id="doc-a")

        rows = db_booking.list_approved_future_for_doctor(
            "doc-a", desde=ahora, hasta=ahora + timedelta(days=7))
        ids = [r["id"] for r in rows]
        self.assertIn(sid_soon, ids)
        self.assertNotIn(sid_far, ids)

    def test_appointments_of_end_to_end(self):
        """The real caller (doctor_appointments.appointments_of): pushes the
        SQL-side filter via list_approved_future_for_doctor AND still
        applies the tolerant name match on top — proving the two-layer
        contract stays correct after the audit fix."""
        from app.modules.turnos import doctor_appointments

        ahora = datetime.now(_ART)
        _crear_aprobada("30111007", "cardio", ahora + timedelta(days=1),
                         medico_asignado="Ana Gomez", medico_asignado_id="doc-a")
        _crear_aprobada("30111008", "cardio", ahora + timedelta(days=1),
                         medico_asignado="Otro Medico", medico_asignado_id="doc-b")
        # Legacy row: no id, only a name that fuzzy-matches "doc-a"'s doctor.
        _crear_aprobada("30111009", "cardio", ahora + timedelta(days=1),
                         medico_asignado="Ana Gomez", medico_asignado_id=None)

        rows = doctor_appointments.appointments_of("doc-a", "Ana Gomez", desde=ahora)
        dnis = sorted(r["dni"] for r in rows)
        self.assertEqual(dnis, ["30111007", "30111009"])


if __name__ == "__main__":
    unittest.main()
