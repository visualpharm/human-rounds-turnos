"""test_db_booking_flow_store.py — the booking state machine of `core/db.py` against a real Postgres.

Stage 6 of the SQLAlchemy Core move converts the booking group of `core/db.py`. This file pins the
transactions that decide who holds an appointment: `approve` (the status compare-and-set, the doctor
pair, the per-slot capacity under its advisory lock, the percentage-overbooking budget under its
lock, the HSI checkpoint, the audit event), `reject`, `cancel`, `reassign_appointment`,
`reschedule_preference`, `mark_attendance` (row lock, the three writes, the queue invalidation and its
NOTIFY), `appointment_events`, and `create_booking_request_with_limit` (the count-and-create under
the (patient, service) advisory lock, the idempotency key). The row locks and the advisory-lock keys
are held from a second connection: the function must wait for them. It was written and run green on
the hand-written SQL first, and passes unedited on Core.
"""
import os
import sys
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Json  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_values  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_booking_flow")

UTC = timezone.utc


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _at(days=0, hours=0, minutes=0):
    return (datetime.now(UTC) + timedelta(days=days, hours=hours, minutes=minutes)).replace(
        microsecond=0)


def _future(days=3, hour=10, minute=0):
    d = datetime.now(UTC) + timedelta(days=days)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _ins(**cols):
    cols.setdefault("dni", "30000001")
    cols.setdefault("first_name", "Ana")
    cols.setdefault("last_name", "Paz")
    cols.setdefault("service_id", "lab")
    cols.setdefault("center_slug", "c1")
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return _sql(f"INSERT INTO booking_requests ({names}) VALUES ({marks}) RETURNING id",
                tuple(Json(v) if isinstance(v, (dict, list)) else v for v in cols.values()))[0]["id"]


def _row(sid):
    rows = _sql("SELECT * FROM booking_requests WHERE id=%s", (sid,))
    return rows[0] if rows else None


def _request(dni="30000001", servicio_id="lab", **kw):
    return db_booking.create_booking_request(
        dni=dni, nombre="Ana", apellido="Paz", nacimiento="1980-01-01",
        contacto="ana@example.com", servicio_id=servicio_id, centro_slug="c1", **kw)


def _kinds(sid):
    return [e["kind"] for e in db_booking.appointment_events(sid)]


def _raw(autocommit=False):
    return psycopg.connect(_db.test_url, autocommit=autocommit, connect_timeout=5, row_factory=dict_row)


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, day_queue_events, hsi_checkpoints, hsi_appointments, "
                        "hsi_patients RESTART IDENTITY CASCADE")

    def assertWaitsFor(self, fn, hold_sql, params=()):
        """Runs `fn` on a thread while a second connection holds `hold_sql` (a lock) in an open
        transaction: `fn` must still be waiting half a second later and finish once it is released."""
        holder = _raw()
        with holder.cursor() as cur:
            cur.execute(hold_sql, params)
        out = {}

        def run():
            try:
                out["result"] = fn()
            except BaseException as exc:    # noqa: BLE001 — handed back to the test
                out["error"] = exc
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(0.6)
        blocked = thread.is_alive()
        holder.commit()
        holder.close()
        thread.join(10)
        self.assertTrue(blocked, "the function did not wait for the lock")
        self.assertFalse(thread.is_alive())
        return out


# ---- approve ---------------------------------------------------------------------------------

class TestApprove(DbCase):
    def test_sets_the_status_the_time_the_hsi_result_and_the_audit(self):
        sid = _request()
        when = _future()
        row = db_booking.approve(sid, when, {"estado": "ok", "patient_id": "P1", "appointment_id": "A1"},
                         resuelta_por="mesa:ana")
        self.assertEqual((row["id"], row["estado"], row["turno_dt"]), (sid, "aprobada", when))
        self.assertEqual((row["hsi_paciente_id"] if "hsi_paciente_id" in row else row["hsi_patient_id"],
                          row["hsi_appointment_id"], row["hsi_estado"]), ("P1", "A1", "ok"))
        self.assertEqual(row["resuelta_por"], "mesa:ana")
        self.assertIsNotNone(row["resuelta_en"])
        self.assertIs(row["sobreturno"], False)
        self.assertEqual(row, db_booking.get_booking_request(sid))
        events = db_booking.appointment_events(sid)
        self.assertEqual([(e["kind"], e["actor"], e["status_from"], e["status_to"],
                           e["appointment_from"], e["appointment_to"]) for e in events][1:],
                         [("aprobada", "mesa:ana", "pendiente", "aprobada", None, when)])

    def test_a_text_time_is_coerced(self):
        sid = _request()
        when = _future()
        row = db_booking.approve(sid, when.isoformat().replace("+00:00", "Z"))
        self.assertEqual(row["turno_dt"], when)

    def test_no_hsi_result_leaves_the_hsi_columns_null(self):
        sid = _request()
        row = db_booking.approve(sid, _future())
        self.assertEqual((row["hsi_patient_id"], row["hsi_appointment_id"], row["hsi_estado"]),
                         (None, None, None))

    def test_service_reassignment_is_optional(self):
        a, b = _request("1", "lab"), _request("2", "lab")
        self.assertEqual(db_booking.approve(a, _future())["servicio_id"], "lab")
        self.assertEqual(db_booking.approve(b, _future(), servicio_id="rx")["servicio_id"], "rx")

    def test_the_doctor_is_one_pair(self):
        def fresh():
            return _ins(assigned_doctor="Dr. Viejo", assigned_doctor_id="d0")
        row = db_booking.approve(fresh(), _future(days=3))
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Dr. Viejo", "d0"),
                         "neither given: the pair stays")
        row = db_booking.approve(fresh(), _future(days=4), medico_asignado="  Dra. Paz  ", medico_asignado_id=" d9 ")
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Dra. Paz", "d9"))
        row = db_booking.approve(fresh(), _future(days=5), medico_asignado="Dra. Paz")
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Dra. Paz", None),
                         "a name alone replaces the id as well")
        row = db_booking.approve(fresh(), _future(days=6), medico_asignado_id=7)
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), (None, "7"),
                         "an id alone replaces the name as well")
        row = db_booking.approve(fresh(), _future(days=7), medico_asignado="   ", medico_asignado_id="")
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Dr. Viejo", "d0"),
                         "blank values are not a doctor")

    def test_only_a_pending_request_can_be_approved(self):
        for status in ("aprobada", "rechazada", "cancelada"):
            sid = _ins(status=status)
            with self.assertRaises(db_booking.AlreadyProcessed):
                db_booking.approve(sid, _future())
            self.assertEqual(_row(sid)["status"], status)
        with self.assertRaises(db_booking.AlreadyProcessed):
            db_booking.approve(404, _future())
        self.assertEqual(_sql("SELECT count(*) AS n FROM appointment_events"), [{"n": 0}])

    def test_the_same_doctor_and_time_conflicts_unless_marked_overbooking(self):
        when = _future()
        a, b, c = _request("1"), _request("2"), _request("3")
        db_booking.approve(a, when, medico_asignado="Dr. Luis", medico_asignado_id="d1")
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(b, when, medico_asignado="Dr. Luis", medico_asignado_id="d1")
        self.assertEqual(_row(b)["status"], "pendiente")
        self.assertEqual(_kinds(b), ["creada"], "the rolled-back approval left no event")
        row = db_booking.approve(c, when, medico_asignado="Dr. Luis", medico_asignado_id="d1", sobreturno=True)
        self.assertIs(row["sobreturno"], True)
        db_booking.approve(_request("4"), when, medico_asignado="Dr. Luis", medico_asignado_id="d2")

    def test_the_hsi_checkpoint_is_stored_with_the_approval(self):
        sid = _request()
        db_booking.approve(sid, _future(), {"estado": "ok", "appointment_id": "A7",
                                    "checkpoint": {"v": 1, "at": datetime(2030, 5, 6, 7, 8, tzinfo=UTC)}})
        self.assertEqual([(r["entity_type"], r["entity_id"], r["remote"])
                          for r in _sql("SELECT * FROM hsi_checkpoints")],
                         [("appointment", "A7", {"v": 1, "at": "2030-05-06T07:08:00+00:00"})])
        again = _request("2")
        db_booking.approve(again, _future(days=4), {"estado": "ok", "appointment_id": "A7", "checkpoint": {"v": 2}})
        self.assertEqual([r["remote"] for r in _sql("SELECT * FROM hsi_checkpoints")], [{"v": 2}])

    def test_no_checkpoint_without_both_parts_and_none_after_a_conflict(self):
        when = _future()
        a, b, c = _request("1"), _request("2"), _request("3")
        db_booking.approve(a, when, {"estado": "ok", "appointment_id": "A1"}, medico_asignado_id="d1")
        db_booking.approve(c, when + timedelta(hours=1), {"estado": "ok", "checkpoint": {"v": 1}})
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(b, when, {"estado": "ok", "appointment_id": "A2", "checkpoint": {"v": 1}},
                       medico_asignado_id="d1")
        self.assertEqual(_sql("SELECT * FROM hsi_checkpoints"), [])

    def test_waits_for_the_row_lock(self):
        sid = _request()
        out = self.assertWaitsFor(lambda: db_booking.approve(sid, _future()),
                                  "SELECT 1 FROM booking_requests WHERE id=%s FOR UPDATE", (sid,))
        self.assertEqual(out["result"]["estado"], "aprobada")


class TestApproveSlotCapacity(DbCase):
    def test_a_full_slot_refuses_and_the_loser_stays_pending(self):
        when = _future()
        a, b = _request("1"), _request("2")
        db_booking.approve(a, when, slot_capacity=1)
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(b, when, slot_capacity=1)
        self.assertEqual(_row(b)["status"], "pendiente")
        self.assertEqual(_kinds(b), ["creada"])

    def test_capacity_n_admits_n(self):
        when = _future()
        ids = [_request(str(i)) for i in range(4)]
        db_booking.approve(ids[0], when, slot_capacity=2)
        db_booking.approve(ids[1], when, slot_capacity=2)
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(ids[2], when, slot_capacity=2)

    def test_what_counts(self):
        when = _future()
        db_booking.approve(_request("1"), when, sobreturno=True)                      # overbooking: not counted
        db_booking.approve(_request("2", "rx"), when)                                  # another service
        db_booking.approve(_request("3"), when + timedelta(minutes=20))                # another time
        cancelled = _request("4")
        db_booking.approve(cancelled, when)
        db_booking.cancel(cancelled)                                                   # not approved any more
        db_booking.approve(_request("5"), when, slot_capacity=1)
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(_request("6"), when, slot_capacity=1)

    def test_an_overbooked_approval_ignores_the_capacity(self):
        when = _future()
        db_booking.approve(_request("1"), when, slot_capacity=1)
        db_booking.approve(_request("2"), when, sobreturno=True, slot_capacity=1)

    def test_no_capacity_no_check(self):
        when = _future()
        for dni in ("1", "2", "3"):
            db_booking.approve(_request(dni), when)

    def test_the_slot_check_uses_the_requests_own_service(self):
        when = _future()
        db_booking.approve(_request("1", "rx"), when, slot_capacity=1)
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(_request("2", "rx"), when, slot_capacity=1)
        db_booking.approve(_request("3", "lab"), when, slot_capacity=1)
        reassigned = _request("4", "lab")
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(reassigned, when, servicio_id="rx", slot_capacity=1)

    def test_waits_for_the_slot_lock_under_its_key(self):
        when = _future()
        sid = _request()
        key = "slot:lab:" + when.astimezone(UTC).isoformat()
        out = self.assertWaitsFor(lambda: db_booking.approve(sid, when, slot_capacity=1),
                                  "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (key,))
        self.assertEqual(out["result"]["estado"], "aprobada")

    def test_does_not_wait_for_another_slots_lock(self):
        when = _future()
        sid = _request()
        holder = _raw()
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", ("slot:lab:otra-hora",))
        try:
            self.assertEqual(db_booking.approve(sid, when, slot_capacity=1)["estado"], "aprobada")
        finally:
            holder.commit()
            holder.close()


class TestApproveOverbookingBudget(DbCase):
    WINDOW = (_future(days=5, hour=8), _future(days=5, hour=12))

    def _extra(self, **kw):
        defaults = dict(status="aprobada", overbooking=True, appointment_at=_future(days=5, hour=9),
                        assigned_doctor="Dr. Luis", assigned_doctor_id="d1", center_slug="c1")
        defaults.update(kw)
        return _ins(**defaults)

    def _approve(self, sid, **kw):
        args = dict(sobreturno=True, medico_asignado="Dr. Luis", medico_asignado_id="d1",
                    overbooking_capacity=2, overbooking_window=self.WINDOW,
                    overbooking_lock_key="ob:d1:c1", overbooking_center_slug="c1")
        args.update(kw)
        return db_booking.approve(sid, _future(days=5, hour=10), **args)

    def test_counts_the_extras_of_that_doctor_band_and_center(self):
        self._extra()
        self._extra(appointment_at=_future(days=5, hour=11))
        self._extra(appointment_at=_future(days=6, hour=9))                  # another day
        self._extra(assigned_doctor_id="d2", assigned_doctor="Dra. Paz")     # another doctor
        self._extra(center_slug="c2")                                         # another center
        self._extra(status="cancelada")
        self._extra(overbooking=False)
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dr. Luis", "d1", "c1"), 2)
        sid = _request()
        with self.assertRaises(db_booking.OverbookingCapacityReached):
            self._approve(sid)
        self.assertEqual(_row(sid)["status"], "pendiente")
        self.assertEqual(_kinds(sid), ["creada"])

    def test_below_the_budget_the_approval_goes_through(self):
        self._extra()
        row = self._approve(_request())
        self.assertEqual((row["estado"], row["sobreturno"]), ("aprobada", True))

    def test_legacy_rows_without_an_id_match_by_the_normalized_name(self):
        self._extra(assigned_doctor_id=None, assigned_doctor="Dr. Luís")
        self._extra(assigned_doctor_id=None, assigned_doctor="dr. luis")
        self._extra(assigned_doctor_id=None, assigned_doctor="Dra. Otra")
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "DR. LUIS", None, "c1"), 2)
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dra. Otra", None, "c1"), 1)
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dr. Luis", "d1", "c1"), 2,
                         "the id match and the legacy name match both count")

    def test_a_missing_center_matches_only_rows_without_a_center(self):
        self._extra(center_slug=None)
        self._extra()
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dr. Luis", "d1", None), 1)
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dr. Luis", "d1", "c1"), 1)
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dr. Luis", "d1", "zz"), 0)

    def test_the_request_itself_is_not_counted(self):
        sid = self._extra(status="aprobada")
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dr. Luis", "d1", "c1"), 1)
        self.assertEqual(db_booking.overbooking_count(*self.WINDOW, "Dr. Luis", "d1", "c1", exclude_sid=sid), 0)

    def test_window_is_half_open_and_accepts_text_times(self):
        start, end = self.WINDOW
        self._extra(appointment_at=start)
        self._extra(appointment_at=end)
        self.assertEqual(db_booking.overbooking_count(start, end, "Dr. Luis", "d1", "c1"), 1)
        self.assertEqual(db_booking.overbooking_count(start.isoformat(), end.isoformat(), "Dr. Luis", "d1", "c1"), 1)

    def test_a_budget_needs_an_overbooked_approval_with_a_window_and_a_key(self):
        sid = _request()
        for kw in ({"sobreturno": False}, {"overbooking_window": None}, {"overbooking_lock_key": None},
                   {"overbooking_lock_key": ""}):
            with self.assertRaises(ValueError):
                self._approve(sid, **kw)
        self.assertEqual(_row(sid)["status"], "pendiente")

    def test_waits_for_the_budget_lock_under_the_given_key(self):
        sid = _request()
        out = self.assertWaitsFor(lambda: self._approve(sid, overbooking_lock_key="ob:espera"),
                                  "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", ("ob:espera",))
        self.assertEqual(out["result"]["estado"], "aprobada")


# ---- reject / cancel -----------------------------------------------------------------------------

class TestReject(DbCase):
    def test_rejects_a_pending_request(self):
        sid = _request()
        row = db_booking.reject(sid, "orden ilegible", resuelta_por="mesa:ana")
        self.assertEqual((row["estado"], row["motivo_rechazo"], row["resuelta_por"]),
                         ("rechazada", "orden ilegible", "mesa:ana"))
        self.assertIsNotNone(row["resuelta_en"])
        self.assertEqual(row, db_booking.get_booking_request(sid))
        self.assertEqual([(e["kind"], e["actor"], e["status_from"], e["status_to"])
                          for e in db_booking.appointment_events(sid)][1:],
                         [("rechazada", "mesa:ana", "pendiente", "rechazada")])

    def test_only_the_given_states_can_be_rejected(self):
        approved = _ins(status="aprobada", appointment_at=_at(2))
        with self.assertRaises(db_booking.AlreadyProcessed):
            db_booking.reject(approved, "x")
        row = db_booking.reject(approved, "conflicto HSI", resuelta_por="hsi", desde=("pendiente", "aprobada"))
        self.assertEqual(row["estado"], "rechazada")
        event = db_booking.appointment_events(approved)[-1]
        self.assertEqual((event["status_from"], event["status_to"]), ("aprobada", "rechazada"))
        self.assertIsNotNone(event["appointment_from"])
        for status in ("rechazada", "cancelada"):
            sid = _ins(status=status)
            with self.assertRaises(db_booking.AlreadyProcessed):
                db_booking.reject(sid, "x", desde=("pendiente", "aprobada"))
        with self.assertRaises(db_booking.AlreadyProcessed):
            db_booking.reject(_request("9"), "x", desde=())
        with self.assertRaises(db_booking.AlreadyProcessed):
            db_booking.reject(404, "x")

    def test_waits_for_the_row_lock(self):
        sid = _request()
        out = self.assertWaitsFor(lambda: db_booking.reject(sid, "x"),
                                  "SELECT 1 FROM booking_requests WHERE id=%s FOR UPDATE", (sid,))
        self.assertEqual(out["result"]["estado"], "rechazada")


class TestCancel(DbCase):
    def test_cancels_a_pending_or_an_approved_request(self):
        pending, approved = _request("1"), _request("2")
        db_booking.approve(approved, _future(), resuelta_por="mesa:ana")
        row = db_booking.cancel(pending, actor="paciente")
        self.assertEqual(row["estado"], "cancelada")
        self.assertIsNotNone(row["cancelada_en"])
        self.assertIsNone(row["resuelta_por"], "the hospital's decision fields are untouched")
        row = db_booking.cancel(approved, actor="solicitante")
        self.assertEqual((row["estado"], row["resuelta_por"]), ("cancelada", "mesa:ana"))
        self.assertEqual(row, db_booking.get_booking_request(approved))
        event = db_booking.appointment_events(approved)[-1]
        self.assertEqual((event["kind"], event["actor"], event["status_from"], event["status_to"]),
                         ("cancelada", "solicitante", "aprobada", "cancelada"))
        self.assertIsNotNone(event["appointment_from"])

    def test_a_resolved_or_unknown_request_returns_none_and_writes_nothing(self):
        for status in ("rechazada", "cancelada"):
            sid = _ins(status=status)
            self.assertIsNone(db_booking.cancel(sid, actor="paciente"))
            self.assertEqual(_row(sid)["status"], status)
            self.assertEqual(_kinds(sid), [])
        self.assertIsNone(db_booking.cancel(404))
        self.assertIsNone(db_booking.cancel(None))

    def test_waits_for_the_row_lock(self):
        sid = _request()
        out = self.assertWaitsFor(lambda: db_booking.cancel(sid),
                                  "SELECT 1 FROM booking_requests WHERE id=%s FOR UPDATE", (sid,))
        self.assertEqual(out["result"]["estado"], "cancelada")


class TestReschedulePreference(DbCase):
    def test_changes_only_the_preference_of_that_request(self):
        a, b = _request("1", preferencia_horario="mañana"), _request("2", preferencia_horario="tarde")
        row = db_booking.reschedule_preference(a, "a la tarde")
        self.assertEqual(row["preferencia_horario"], "a la tarde")
        self.assertEqual(row["estado"], "pendiente")
        self.assertEqual(_row(b)["time_preference"], "tarde")
        self.assertIsNone(db_booking.reschedule_preference(a, None)["preferencia_horario"])
        self.assertIsNone(db_booking.reschedule_preference(404, "x"))


class TestReassignAppointment(DbCase):
    def test_moves_the_time_keeps_the_status_and_logs_the_move(self):
        sid = _request()
        t0, t1 = _future(), _future(days=1)
        db_booking.approve(sid, t0, {"estado": "ok", "patient_id": "P1", "appointment_id": "A1"})
        row = db_booking.reassign_appointment(sid, t1, {"estado": "ok2", "appointment_id": "A2"}, actor="paciente:lista")
        self.assertEqual((row["estado"], row["turno_dt"]), ("aprobada", t1))
        self.assertEqual((row["hsi_patient_id"], row["hsi_appointment_id"], row["hsi_estado"]),
                         ("P1", "A2", "ok2"), "the values not given keep the previous ones")
        event = db_booking.appointment_events(sid)[-1]
        self.assertEqual((event["kind"], event["actor"], event["appointment_from"], event["appointment_to"]),
                         ("reasignada", "paciente:lista", t0, t1))

    def test_the_same_time_is_not_a_change(self):
        sid = _request()
        t0 = _future()
        db_booking.approve(sid, t0)
        db_booking.reassign_appointment(sid, t0, {"estado": "ok"}, actor="hsi")
        self.assertEqual(_kinds(sid), ["creada", "aprobada"])
        self.assertEqual(_row(sid)["hsi_status"], "ok")

    def test_text_time_and_no_hsi_result(self):
        sid = _request()
        db_booking.approve(sid, _future(), {"estado": "ok"})
        t1 = _future(days=2)
        row = db_booking.reassign_appointment(sid, t1.isoformat())
        self.assertEqual((row["turno_dt"], row["hsi_estado"]), (t1, "ok"))

    def test_moving_onto_a_taken_doctor_slot_conflicts_and_changes_nothing(self):
        t0, t1 = _future(), _future(days=1)
        a, b = _request("1"), _request("2")
        db_booking.approve(a, t0, medico_asignado_id="d1")
        db_booking.approve(b, t1, medico_asignado_id="d1")
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.reassign_appointment(b, t0)
        self.assertEqual(_row(b)["appointment_at"], t1)
        self.assertEqual(_kinds(b), ["creada", "aprobada"])

    def test_capacity_applies_when_given(self):
        t0, t1 = _future(), _future(days=1)
        a, b = _request("1"), _request("2")
        db_booking.approve(a, t0)
        db_booking.approve(b, t1)
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.reassign_appointment(b, t0, slot_capacity=1)
        self.assertEqual(db_booking.reassign_appointment(b, t0)["turno_dt"], t0, "without capacity the desk may double up")

    def test_unknown_id_is_none(self):
        self.assertIsNone(db_booking.reassign_appointment(404, _future()))
        self.assertIsNone(db_booking.reassign_appointment(404, _future(), slot_capacity=1))


# ---- attendance --------------------------------------------------------------------------------------

class TestMarkAttendance(DbCase):
    def _today_approved(self, **kw):
        when = datetime.now(db_values._ART).replace(hour=3, minute=0, second=0, microsecond=0)
        defaults = dict(status="aprobada", appointment_at=when, center_slug="c9",
                        assigned_doctor="Dr. Luis", assigned_doctor_id="d1")
        defaults.update(kw)
        return _ins(**defaults)

    def test_validates_its_arguments(self):
        sid = self._today_approved()
        with self.assertRaises(ValueError) as cm:
            db_booking.mark_attendance(sid, "tarde", "op")
        self.assertEqual(str(cm.exception), "asistencia_invalida")
        with self.assertRaises(ValueError) as cm:
            db_booking.mark_attendance(sid, "presente", "op", origen="otro")
        self.assertEqual(str(cm.exception), "origen_invalido")
        self.assertIsNone(_row(sid)["attendance"])

    def test_unknown_request_is_none(self):
        self.assertIsNone(db_booking.mark_attendance(404, "presente", "op"))
        self.assertIsNone(db_booking.mark_attendance(None, "presente", "op"))

    def test_writes_the_state_the_audit_the_event_and_the_queue_invalidation(self):
        sid = self._today_approved()
        row = db_booking.mark_attendance(sid, "presente", "medico:luis")
        self.assertEqual((row["estado"], row["asistencia"], row["asistencia_actualizada_por"]),
                         ("aprobada", "presente", "medico:luis"))
        self.assertIsNotNone(row["asistencia_actualizada_en"])
        self.assertEqual(row, db_booking.get_booking_request(sid))
        audit = _sql("SELECT * FROM attendance_events WHERE request_id=%s", (sid,))
        self.assertEqual([(a["dni"], a["attendance_from"], a["attendance_to"], a["operator"], a["source"])
                          for a in audit], [("30000001", None, "presente", "medico:luis", "sana")])
        event = db_booking.appointment_events(sid)[-1]
        self.assertEqual((event["kind"], event["actor"], event["attendance_from"], event["attendance_to"],
                          event["status_from"], event["status_to"]),
                         ("asistencia", "medico:luis", None, "presente", "aprobada", "aprobada"))
        queue = _sql("SELECT * FROM day_queue_events")
        self.assertEqual(len(queue), 1)
        self.assertEqual((queue[0]["appointment_day"], queue[0]["center_slug"],
                          queue[0]["assigned_doctor_id"], queue[0]["assigned_doctor"]),
                         (row["turno_dt"].astimezone(db_values._ART).date(), "c9", "d1", "Dr. Luis"))

    def test_a_correction_is_audited_even_when_it_repeats_the_value(self):
        sid = self._today_approved()
        db_booking.mark_attendance(sid, "presente", "mesa:ana")
        db_booking.mark_attendance(sid, "presente", "mesa:luis")
        db_booking.mark_attendance(sid, "ausente", "mesa:luis")
        audit = _sql("SELECT attendance_from, attendance_to FROM attendance_events WHERE request_id=%s "
                     "ORDER BY id", (sid,))
        self.assertEqual([(a["attendance_from"], a["attendance_to"]) for a in audit],
                         [(None, "presente"), ("presente", "presente"), ("presente", "ausente")])
        self.assertEqual(_sql("SELECT count(*) AS n FROM day_queue_events"), [{"n": 3}])

    def test_a_repeated_hsi_value_is_not_an_event(self):
        sid = self._today_approved(attendance="ausente")
        row = db_booking.mark_attendance(sid, "ausente", "poll", origen="hsi")
        self.assertEqual(row["asistencia"], "ausente")
        self.assertEqual(_sql("SELECT count(*) AS n FROM attendance_events"), [{"n": 0}])
        self.assertEqual(_sql("SELECT count(*) AS n FROM day_queue_events"), [{"n": 0}])
        self.assertEqual(_kinds(sid), [])
        db_booking.mark_attendance(sid, "presente", "poll", origen="hsi")
        event = db_booking.appointment_events(sid)[-1]
        self.assertEqual((event["actor"], event["attendance_from"], event["attendance_to"]),
                         ("hsi:poll", "ausente", "presente"))
        audit = _sql("SELECT source, operator FROM attendance_events")
        self.assertEqual(audit, [{"source": "hsi", "operator": "poll"}])

    def test_only_an_approved_request_of_today_or_before_is_eligible(self):
        tomorrow = datetime.now(db_values._ART).replace(hour=12) + timedelta(days=1)
        for sid in (_ins(status="pendiente"), _ins(status="rechazada"), _ins(status="cancelada"),
                    _ins(status="aprobada"),
                    _ins(status="aprobada", appointment_at=tomorrow)):
            with self.assertRaises(ValueError) as cm:
                db_booking.mark_attendance(sid, "presente", "op")
            self.assertEqual(str(cm.exception), "asistencia_no_elegible")
            self.assertIsNone(_row(sid)["attendance"])
        yesterday = _ins(status="aprobada", appointment_at=_at(-1))
        self.assertEqual(db_booking.mark_attendance(yesterday, "ausente", "op")["asistencia"], "ausente")
        future = _ins(status="aprobada", appointment_at=tomorrow)
        self.assertEqual(db_booking.mark_attendance(future, "presente", "hsi-import", origen="hsi",
                                            exigir_no_futuro=False)["asistencia"], "presente")
        self.assertEqual(db_booking.mark_attendance(_ins(status="pendiente"), "presente", "op",
                                            exigir_no_futuro=False)["asistencia"], "presente")

    def test_a_request_without_a_time_writes_no_queue_event(self):
        sid = _ins(status="pendiente")
        db_booking.mark_attendance(sid, "presente", "op", exigir_no_futuro=False)
        self.assertEqual(_sql("SELECT count(*) AS n FROM day_queue_events"), [{"n": 0}])
        self.assertEqual(_sql("SELECT count(*) AS n FROM attendance_events"), [{"n": 1}])

    def test_the_queue_event_is_announced_after_commit(self):
        sid = self._today_approved()
        listener = _raw(autocommit=True)
        try:
            listener.execute("LISTEN turnos_day_queue")
            db_booking.mark_attendance(sid, "presente", "op")
            notes = list(listener.notifies(timeout=2, stop_after=1))
        finally:
            listener.close()
        event_id = _sql("SELECT id FROM day_queue_events")[0]["id"]
        self.assertEqual([n.payload for n in notes], [str(event_id)])

    def test_waits_for_the_row_lock(self):
        sid = self._today_approved()
        out = self.assertWaitsFor(lambda: db_booking.mark_attendance(sid, "presente", "op"),
                                  "SELECT 1 FROM booking_requests WHERE id=%s FOR UPDATE", (sid,))
        self.assertEqual(out["result"]["asistencia"], "presente")

    def test_queue_events_are_replayable_by_id(self):
        sid = self._today_approved()
        self.assertEqual(db_slots.day_queue_event_high_water(), 0)
        db_booking.mark_attendance(sid, "presente", "op")
        db_booking.mark_attendance(sid, "ausente", "op")
        self.assertEqual(db_slots.day_queue_event_high_water(), 2)
        self.assertEqual([e["id"] for e in db_slots.day_queue_events_after(1)], [2])


# ---- audit trail ----------------------------------------------------------------------------------------

class TestAppointmentEvents(DbCase):
    def test_oldest_first_plain_dicts_of_every_column(self):
        sid = _request(actor="paciente")
        db_booking.approve(sid, _future(), resuelta_por="mesa:ana")
        db_booking.cancel(sid, actor="paciente")
        events = db_booking.appointment_events(sid)
        self.assertEqual([e["kind"] for e in events], ["creada", "aprobada", "cancelada"])
        self.assertEqual([e["id"] for e in events], sorted(e["id"] for e in events))
        self.assertTrue(all(type(e) is dict for e in events))
        self.assertTrue(all(e["request_id"] == sid for e in events))
        self.assertIsInstance(events[0]["created"], datetime)

    def test_other_requests_and_unknown_ids(self):
        a, b = _request("1"), _request("2")
        self.assertEqual(len(db_booking.appointment_events(a)), 1)
        self.assertEqual(db_booking.appointment_events(404), [])
        self.assertEqual(db_booking.appointment_events(None), [])

    def test_log_appointment_event_writes_on_the_callers_transaction(self):
        sid = _request()
        with db._connect() as conn, conn.cursor() as cur:
            db_booking._log_appointment_event(cur, sid, "reasignada", "x", "aprobada", "aprobada",
                                      _at(1), _at(2), "presente", "ausente")
            conn.rollback()
        self.assertEqual(_kinds(sid), ["creada"])
        with db._connect() as conn, conn.cursor() as cur:
            db_booking._log_appointment_event(cur, sid, "asistencia", "x", None, None, None, None)
        event = db_booking.appointment_events(sid)[-1]
        self.assertEqual((event["kind"], event["actor"], event["attendance_from"], event["attendance_to"]),
                         ("asistencia", "x", None, None))


# ---- create with the limit ---------------------------------------------------------------------------------

class TestCreateWithLimit(DbCase):
    ARGS = ("30000009", "Ana", "Paz", "1980-01-01", "a@x.test", "lab", "c1")

    def test_the_appointment_asked_for_outlives_rejection_in_the_account_list(self):
        # The product owner 26.10: a rejected request in «Anteriores» said no date — the slot
        # lived only in its hold, which rejection deletes (Alembic 0037).
        pedido = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
        fila = db_booking.create_booking_request_with_limit(*self.ARGS, 3, turno_pedido=pedido)
        sin_slot = db_booking.create_booking_request_with_limit(*self.ARGS, 3)
        db_booking.reject(fila["id"], "Testing")
        filas = {r["id"]: r for r in db_booking.list_by_dni("30000009")}
        self.assertEqual(filas[fila["id"]]["requested_at"], pedido)
        self.assertIsNone(filas[sin_slot["id"]]["requested_at"])
        from app.modules.turnos import booking_requests
        self.assertEqual(booking_requests._public_row(filas[fila["id"]])["turno_pedido"],
                         "2026-10-27T12:00:00-03:00")
        self.assertIsNone(booking_requests._public_row(filas[sin_slot["id"]])["turno_pedido"])

    def test_creates_and_returns_the_wired_row_with_its_event(self):
        fila = db_booking.create_booking_request_with_limit(
            *self.ARGS, 3, orden_mime="image/png", sexo="F", preferencia_horario="mañana",
            solicitante_dni="27000111", slot_medico_id="d4", actor="paciente", nota_paciente="vengo en silla")
        self.assertEqual((fila["estado"], fila["dni"], fila["nombre"], fila["servicio_id"]),
                         ("pendiente", "30000009", "Ana", "lab"))
        self.assertEqual((fila["solicitante_dni"], fila["slot_medico_id"], fila["slot_medico_id"],
                          fila["nota_paciente"], fila["orden_mime"]),
                         ("27000111", "d4", "d4", "vengo en silla", "image/png"))
        self.assertEqual(fila["nacimiento"], date(1980, 1, 1))
        self.assertNotIn("_ya_existia", fila, "no idempotency key, no flag")
        self.assertEqual([(e["kind"], e["actor"]) for e in db_booking.appointment_events(fila["id"])],
                         [("creada", "paciente")])

    def test_the_limit_counts_pending_and_future_approved_of_the_same_patient_and_service(self):
        db_booking.create_booking_request_with_limit(*self.ARGS, 2)
        approved = db_booking.create_booking_request_with_limit(*self.ARGS, 2)
        db_booking.approve(approved["id"], _future())
        with self.assertRaises(db_booking.LimitReached) as cm:
            db_booking.create_booking_request_with_limit(*self.ARGS, 2)
        self.assertEqual((cm.exception.limite, cm.exception.abiertas), (2, 2))
        self.assertEqual(_sql("SELECT count(*) AS n FROM booking_requests"), [{"n": 2}])
        other_service = ("30000009", "Ana", "Paz", "1980-01-01", "a@x.test", "rx", "c1")
        db_booking.create_booking_request_with_limit(*other_service, 2)
        other_patient = ("30000010", "Ana", "Paz", "1980-01-01", "a@x.test", "lab", "c1")
        db_booking.create_booking_request_with_limit(*other_patient, 2)

    def test_rejected_cancelled_and_past_requests_do_not_count(self):
        _ins(dni="30000009", status="rechazada")
        _ins(dni="30000009", status="cancelada")
        _ins(dni="30000009", status="aprobada", appointment_at=_at(-2))
        db_booking.create_booking_request_with_limit(*self.ARGS, 1)
        with self.assertRaises(db_booking.LimitReached):
            db_booking.create_booking_request_with_limit(*self.ARGS, 1)

    def test_a_zero_limit_refuses_and_estados_activos_changes_what_counts(self):
        with self.assertRaises(db_booking.LimitReached) as cm:
            db_booking.create_booking_request_with_limit(*self.ARGS, 0)
        self.assertEqual((cm.exception.limite, cm.exception.abiertas), (0, 0))
        _ins(dni="30000009", status="pendiente")
        db_booking.create_booking_request_with_limit(*self.ARGS, 1, estados_activos=("aprobada",))
        db_booking.create_booking_request_with_limit(*self.ARGS, 1, estados_activos=())

    def test_the_idempotency_key_returns_the_same_row_even_at_the_limit(self):
        first = db_booking.create_booking_request_with_limit(*self.ARGS, 1, idem_key=" k-1 ")
        self.assertIs(first["_ya_existia"], False)
        again = db_booking.create_booking_request_with_limit(*self.ARGS, 1, idem_key="k-1")
        self.assertEqual((again["id"], again["_ya_existia"]), (first["id"], True))
        self.assertEqual(_row(first["id"])["idem_key"], "k-1")
        with self.assertRaises(db_booking.LimitReached):
            db_booking.create_booking_request_with_limit(*self.ARGS, 1, idem_key="k-2")
        self.assertEqual(_sql("SELECT count(*) AS n FROM booking_requests"), [{"n": 1}])
        self.assertEqual(len(db_booking.appointment_events(first["id"])), 1)

    def test_a_blank_key_is_no_key(self):
        a = db_booking.create_booking_request_with_limit(*self.ARGS, 5, idem_key="   ")
        b = db_booking.create_booking_request_with_limit(*self.ARGS, 5, idem_key="")
        c = db_booking.create_booking_request_with_limit(*self.ARGS, 5, idem_key=None)
        self.assertEqual(len({a["id"], b["id"], c["id"]}), 3)
        self.assertTrue(all("_ya_existia" not in x for x in (a, b, c)))

    def test_get_by_idem_key(self):
        sid = _ins(idem_key="abc")
        _ins()
        self.assertEqual(db_slots.get_booking_request_by_idem_key(" abc ")["id"], sid)
        for value in (None, "", "  ", "nope"):
            self.assertIsNone(db_slots.get_booking_request_by_idem_key(value), repr(value))

    def test_a_row_that_appears_between_the_count_and_the_insert_is_returned_not_duplicated(self):
        """Another (patient, service) with the SAME key commits after our re-check: the insert hits the
        unique key, does nothing, and the caller gets the winner's row flagged as pre-existing."""
        winner = {}

        def count_then_lose_the_race(cur, dni, servicio_id, estados):
            winner["id"] = _ins(dni="otro", idem_key="race-2")
            return 0
        with mock.patch.object(db_slots, "_count_active_requests_cur", side_effect=count_then_lose_the_race):
            fila = db_booking.create_booking_request_with_limit(*self.ARGS, 5, idem_key="race-2")
        self.assertEqual((fila["id"], fila["_ya_existia"], fila["dni"]), (winner["id"], True, "otro"))
        self.assertEqual(_sql("SELECT count(*) AS n FROM booking_requests"), [{"n": 1}])
        self.assertEqual(_kinds(winner["id"]), [], "no event of ours: we created nothing")

    def test_concurrent_creates_with_one_key_make_one_row(self):
        results = []

        def go():
            results.append(db_booking.create_booking_request_with_limit(*self.ARGS, 1, idem_key="race"))
        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertEqual(sorted(r["_ya_existia"] for r in results), [False] + [True] * 5)

    def test_concurrent_creates_without_a_key_stop_at_the_limit(self):
        outcomes = []

        def go():
            try:
                db_booking.create_booking_request_with_limit(*self.ARGS, 2)
                outcomes.append("ok")
            except db_booking.LimitReached:
                outcomes.append("limit")
        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(outcomes), ["limit"] * 4 + ["ok"] * 2)

    def test_waits_for_the_patient_and_service_lock(self):
        out = self.assertWaitsFor(lambda: db_booking.create_booking_request_with_limit(*self.ARGS, 1),
                                  "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                                  ("booklimit:30000009:lab",))
        self.assertEqual(out["result"]["estado"], "pendiente")

    def test_does_not_wait_for_another_patients_lock(self):
        holder = _raw()
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", ("booklimit:otro:lab",))
        try:
            self.assertEqual(db_booking.create_booking_request_with_limit(*self.ARGS, 1)["estado"], "pendiente")
        finally:
            holder.commit()
            holder.close()

    def test_count_helpers_agree_with_the_limit(self):
        _ins(dni="30000009", status="pendiente")
        _ins(dni="30000009", status="aprobada", appointment_at=_at(2))
        _ins(dni="30000009", status="aprobada", appointment_at=_at(-2))
        _ins(dni="30000009", status="aprobada")
        _ins(dni="30000009", status="cancelada")
        self.assertEqual(db_slots.count_active_requests("30000009", "lab"), 3)
        self.assertEqual(db_slots.count_active_requests("30000009", "lab", ("pendiente",)), 1)
        self.assertEqual(db_slots.count_active_requests("30000009", "lab", ()), 0)
        self.assertEqual(db_slots.count_active_requests("30000009", "rx"), 0)
        self.assertEqual(db_slots.count_active_requests(None, "lab"), 0)


if __name__ == "__main__":
    unittest.main()
