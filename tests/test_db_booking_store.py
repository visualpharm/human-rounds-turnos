"""test_db_booking_store.py — the booking-request statements of `core/db.py` against a real Postgres.

Stage 6 of the SQLAlchemy Core move converts the booking group of `core/db.py`. This file pins the
part of it that reads and writes `booking_requests` outside the approve / reject / cancel / limit
transactions (those are `test_db_booking_flow_store.py`): create and read, the patient's own data,
the referral file bookkeeping and its retention sweep, the staff lists and their counts, the
approved-appointment windows, the confirmation link and answer, the HSI re-push list, the two
reminder sweeps with their claims, the attendance summaries and the ranking history.

Every function is called directly and its rows, key names (the Spanish wire format of `db_wire._wire`),
order and boundaries are asserted; every lookup that takes a value which can be `None` is also called
with `None` over rows whose column is NULL (the hand-written `col = NULL` finds nothing, Core's
`col == None` is `IS NULL`). It was written and run green on the hand-written SQL first, and passes
unedited on Core.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from psycopg.types.json import Json  # noqa: E402

from app.core import clock, db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_values  # noqa: E402
from app.core import db_wire  # noqa: E402

import _clock  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_booking")

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


def _now():
    return _sql("SELECT now() AS n")[0]["n"]


def _at(days=0, hours=0, minutes=0):
    """A whole second counted from the test's frozen clock (`DbCase.setUp` freezes it): taken from the
    wall clock per call, a row inserted at `_at(2)` and a window opened at `_at(2)` a few statements later
    straddled a second boundary now and then - the window began one second after the row and the row fell
    out of it."""
    return (clock.utcnow() + timedelta(days=days, hours=hours, minutes=minutes)).replace(microsecond=0)


def _ins(**cols):
    """A booking row written with plain SQL, so a test controls every column."""
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


def _columns():
    return [r["column_name"] for r in _sql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='booking_requests' ORDER BY ordinal_position")]


def _wire_keys():
    mapping = db_wire._WIRE_BACK["booking_requests"]
    hidden = db_wire._WIRE_HIDDEN["booking_requests"]   # the case chat: a logged read of its own
    return [mapping.get(c, c) for c in _columns() if c not in hidden]


class DbCase(unittest.TestCase):
    def setUp(self):
        _clock.freeze(self)
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, day_queue_events, hsi_checkpoints, hsi_appointments, "
                        "hsi_patients RESTART IDENTITY CASCADE")


class TestAtHelper(unittest.TestCase):
    """`_at` is a pure function of the frozen clock: the wall clock ticking between two calls moves nothing."""

    def test_every_call_of_a_test_counts_from_the_same_second_until_the_clock_is_advanced(self):
        frozen = _clock.freeze(self, "2026-10-27T01:30:00.600000Z")
        first = [_at(2) for _ in range(5)]
        self.assertEqual({datetime(2026, 10, 29, 1, 30, tzinfo=UTC)}, set(first))
        self.assertEqual(_at(-1), _at(0) - timedelta(days=1))
        self.assertEqual(_at(0).microsecond, 0)
        frozen.advance(seconds=1)
        self.assertEqual(datetime(2026, 10, 29, 1, 30, 1, tzinfo=UTC), _at(2))

    def test_a_default_freeze_is_the_wall_clock_in_whole_seconds(self):
        _clock.freeze(self)
        self.assertEqual(_at(0), _at(0).replace(microsecond=0))


# ---- create / read -----------------------------------------------------------------

class TestCreateBookingRequest(DbCase):
    def test_returns_the_new_id_and_stores_every_field(self):
        sid = db_booking.create_booking_request(
            "30111222", "Ana", "Paz", "1980-02-03", "ana@x.test", "lab", "c1",
            orden_mime="application/pdf", sexo="F", preferencia_horario="mañana",
            solicitante_dni="27000999", slot_medico_id="d7", actor="paciente")
        self.assertIsInstance(sid, int)
        row = _row(sid)
        self.assertEqual((row["dni"], row["first_name"], row["last_name"]), ("30111222", "Ana", "Paz"))
        self.assertEqual(row["birth_date"], date(1980, 2, 3))
        self.assertEqual((row["sex"], row["contact"], row["service_id"], row["center_slug"]),
                         ("F", "ana@x.test", "lab", "c1"))
        self.assertEqual((row["referral_mime"], row["time_preference"]), ("application/pdf", "mañana"))
        self.assertEqual((row["requester_dni"], row["slot_doctor_id"]), ("27000999", "d7"))
        self.assertEqual(row["status"], "pendiente")
        self.assertIsNone(row["appointment_at"])
        self.assertIs(row["overbooking"], False)
        self.assertIsNone(row["idem_key"])
        self.assertIsNotNone(row["created"])

    def test_requester_defaults_to_the_patient(self):
        sid = _request("30111222")
        self.assertEqual(_row(sid)["requester_dni"], "30111222")
        sid = _request("30111223", solicitante_dni="")
        self.assertEqual(_row(sid)["requester_dni"], "30111223")

    def test_birth_date_accepts_a_date_a_string_and_none(self):
        a = db_booking.create_booking_request("1", "A", "B", date(1990, 5, 6), "c", "lab", "c1")
        b = db_booking.create_booking_request("2", "A", "B", "1990-05-06T10:00:00", "c", "lab", "c1")
        c = db_booking.create_booking_request("3", "A", "B", None, "c", "lab", "c1")
        self.assertEqual(_row(a)["birth_date"], date(1990, 5, 6))
        self.assertEqual(_row(b)["birth_date"], date(1990, 5, 6))
        self.assertIsNone(_row(c)["birth_date"])

    def test_logs_the_creation_event_in_the_same_transaction(self):
        sid = _request(actor="paciente:30000001")
        events = db_booking.appointment_events(sid)
        self.assertEqual([(e["kind"], e["actor"], e["status_from"], e["status_to"]) for e in events],
                         [("creada", "paciente:30000001", None, "pendiente")])
        self.assertEqual(sorted(events[0]),
                         sorted(["id", "request_id", "kind", "actor", "status_from", "status_to",
                                 "appointment_from", "appointment_to", "attendance_from",
                                 "attendance_to", "created"]))

    def test_ids_are_sequential(self):
        a, b = _request("1"), _request("2")
        self.assertEqual(b, a + 1)


class TestGetBookingRequest(DbCase):
    def test_row_has_every_column_under_its_wire_key_in_table_order(self):
        sid = _ins(status="aprobada", appointment_at=_at(1), assigned_doctor="Dr. Luis")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(list(row), _wire_keys())
        self.assertEqual(row["estado"], "aprobada")
        self.assertEqual(row["medico_asignado"], "Dr. Luis")
        self.assertEqual(row["nombre"], "Ana")
        self.assertEqual(row["servicio_id"], "lab")
        self.assertEqual(row["centro_slug"], "c1")

    def test_unknown_or_none_id_is_none(self):
        _ins()
        self.assertIsNone(db_booking.get_booking_request(999))
        self.assertIsNone(db_booking.get_booking_request(None))


class TestSetReferral(DbCase):
    def test_path_alone_keeps_the_mime(self):
        sid = _ins(referral_mime="image/png")
        db_booking.set_referral(sid, "ordenes/1.png")
        row = _row(sid)
        self.assertEqual((row["referral_path"], row["referral_mime"]), ("ordenes/1.png", "image/png"))

    def test_path_and_mime(self):
        sid = _ins()
        db_booking.set_referral(sid, "ordenes/1.pdf", "application/pdf")
        row = _row(sid)
        self.assertEqual((row["referral_path"], row["referral_mime"]),
                         ("ordenes/1.pdf", "application/pdf"))

    def test_touches_only_that_row_and_an_unknown_id_is_a_no_op(self):
        a, b = _ins(), _ins()
        db_booking.set_referral(a, "x.png")
        db_booking.set_referral(12345, "y.png")
        self.assertIsNone(_row(b)["referral_path"])
        self.assertEqual(_row(a)["referral_path"], "x.png")


class TestSaveAi(DbCase):
    def test_stores_the_document_and_returns_the_wired_row(self):
        sid = _ins()
        doc = {"verdict": "ok", "preparacion": ["ayuno"], "n": 3}
        row = db_booking.save_ai(sid, doc)
        self.assertEqual(row["id"], sid)
        self.assertEqual(row["ai_json"], doc)
        self.assertEqual(list(row), _wire_keys())
        self.assertEqual(_row(sid)["ai_json"], doc)

    def test_replaces_the_previous_document(self):
        sid = _ins(ai_json={"verdict": "pendiente_ia"})
        db_booking.save_ai(sid, {"verdict": "ok"})
        self.assertEqual(_row(sid)["ai_json"], {"verdict": "ok"})

    def test_unknown_id_is_none(self):
        self.assertIsNone(db_booking.save_ai(404, {"a": 1}))

    def test_none_is_stored_as_the_json_null_not_sql_null(self):
        sid = _ins()
        db_booking.save_ai(sid, None)
        self.assertEqual(_sql("SELECT ai_json IS NULL AS n, ai_json::text AS t "
                              "FROM booking_requests WHERE id=%s", (sid,)),
                         [{"n": False, "t": "null"}])


class TestUpdateBookingPatientData(DbCase):
    def test_writes_only_the_keys_present(self):
        sid = _ins(first_name="Ana", last_name="Paz", sex="F", birth_date=date(1980, 1, 1))
        row = db_booking.update_booking_patient_data(sid, {"nombre": "Anita"})
        self.assertEqual(row["nombre"], "Anita")
        stored = _row(sid)
        self.assertEqual((stored["first_name"], stored["last_name"], stored["sex"], stored["birth_date"]),
                         ("Anita", "Paz", "F", date(1980, 1, 1)))

    def test_all_four_keys_and_the_date_coercion(self):
        sid = _ins()
        row = db_booking.update_booking_patient_data(
            sid, {"nombre": "N", "apellido": "A", "nacimiento": "1975-06-07", "sexo": "M"})
        stored = _row(sid)
        self.assertEqual((stored["first_name"], stored["last_name"], stored["birth_date"], stored["sex"]),
                         ("N", "A", date(1975, 6, 7), "M"))
        self.assertEqual(list(row), _wire_keys())
        self.assertEqual(row["nacimiento"], date(1975, 6, 7))

    def test_a_none_value_clears_the_column(self):
        sid = _ins(sex="F", birth_date=date(1980, 1, 1))
        db_booking.update_booking_patient_data(sid, {"sexo": None, "nacimiento": None})
        stored = _row(sid)
        self.assertIsNone(stored["sex"])
        self.assertIsNone(stored["birth_date"])

    def test_unknown_keys_are_ignored_and_nothing_to_write_returns_the_row(self):
        sid = _ins(first_name="Ana")
        row = db_booking.update_booking_patient_data(sid, {"estado": "aprobada", "dni": "9"})
        self.assertEqual(row, db_booking.get_booking_request(sid))
        stored = _row(sid)
        self.assertEqual((stored["status"], stored["dni"]), ("pendiente", "30000001"))
        self.assertEqual(db_booking.update_booking_patient_data(sid, {}), db_booking.get_booking_request(sid))

    def test_unknown_id_is_none(self):
        self.assertIsNone(db_booking.update_booking_patient_data(404, {"nombre": "X"}))
        self.assertIsNone(db_booking.update_booking_patient_data(404, {}))


class TestAssignPending(DbCase):
    def test_assigns_a_pending_request(self):
        sid = _ins()
        row = db_booking.assign_pending_booking_request(sid, "Dr. Luis", "d1")
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Dr. Luis", "d1"))
        self.assertEqual(row["estado"], "pendiente")
        self.assertEqual(list(row), _wire_keys())

    def test_reassigns_and_can_clear_the_id(self):
        sid = _ins(assigned_doctor="Dr. Luis", assigned_doctor_id="d1")
        row = db_booking.assign_pending_booking_request(sid, "Dra. Paz", None)
        stored = _row(sid)
        self.assertEqual((stored["assigned_doctor"], stored["assigned_doctor_id"]), ("Dra. Paz", None))
        self.assertEqual(row["medico_asignado"], "Dra. Paz")

    def test_a_resolved_request_is_untouched_and_returns_none(self):
        for status in ("aprobada", "rechazada", "cancelada"):
            sid = _ins(status=status, assigned_doctor="Dr. Luis", assigned_doctor_id="d1")
            self.assertIsNone(db_booking.assign_pending_booking_request(sid, "Otra", "d2"), status)
            self.assertEqual(_row(sid)["assigned_doctor_id"], "d1")

    def test_unknown_id_is_none(self):
        self.assertIsNone(db_booking.assign_pending_booking_request(404, "X", "d1"))
        self.assertIsNone(db_booking.assign_pending_booking_request(None, "X", "d1"))


class TestUnassignPendingOfDoctor(DbCase):
    def test_releases_only_the_pending_requests_of_that_doctor(self):
        mine = _ins(assigned_doctor="Dr. Luis", assigned_doctor_id="d1")
        mine2 = _ins(assigned_doctor="Dr. Luis", assigned_doctor_id="d1")
        approved = _ins(status="aprobada", assigned_doctor="Dr. Luis", assigned_doctor_id="d1",
                        appointment_at=_at(2))
        other = _ins(assigned_doctor="Dra. Paz", assigned_doctor_id="d2")
        self.assertEqual(db_booking.unassign_pending_of_doctor("d1"), 2)
        for sid in (mine, mine2):
            row = _row(sid)
            self.assertEqual((row["assigned_doctor"], row["assigned_doctor_id"]), (None, None))
        self.assertEqual(_row(approved)["assigned_doctor_id"], "d1")
        self.assertEqual(_row(other)["assigned_doctor_id"], "d2")

    def test_nobody_to_release_returns_zero(self):
        _ins(assigned_doctor="Dr. Luis", assigned_doctor_id="d1")
        self.assertEqual(db_booking.unassign_pending_of_doctor("zz"), 0)

    def test_none_releases_nothing(self):
        """`assigned_doctor_id = NULL` never matches: requests with no doctor stay as they are."""
        sin = _ins(assigned_doctor="Texto libre")
        _ins(assigned_doctor_id="d1")
        self.assertEqual(db_booking.unassign_pending_of_doctor(None), 0)
        self.assertEqual(_row(sin)["assigned_doctor"], "Texto libre")


# ---- AI backlog ----------------------------------------------------------------------

class TestPendingAi(DbCase):
    def test_lists_pending_requests_with_a_file_and_a_pending_verdict(self):
        a = _ins(first_name="A", referral_path="o/a.png", referral_mime="image/png",
                 ai_json={"verdict": "pendiente_ia"}, created=_at(-3))
        b = _ins(first_name="B", referral_path="o/b.pdf", referral_mime="application/pdf",
                 ai_json={"verdict": "pendiente_ia"}, created=_at(-2))
        _ins(referral_path="o/c.png", ai_json={"verdict": "ok"})
        _ins(ai_json={"verdict": "pendiente_ia"})                                 # no file
        _ins(status="aprobada", referral_path="o/d.png", ai_json={"verdict": "pendiente_ia"})
        _ins(referral_path="o/e.png")                                              # no document
        rows = db_booking.booking_requests_pending_ai()
        self.assertEqual([r["id"] for r in rows], [a, b])
        self.assertEqual(sorted(rows[0]), sorted(
            ["id", "dni", "nombre", "apellido", "servicio_id", "orden_path", "orden_mime"]))
        self.assertEqual((rows[0]["nombre"], rows[0]["orden_path"], rows[0]["orden_mime"]),
                         ("A", "o/a.png", "image/png"))

    def test_oldest_first_and_limited(self):
        ids = [_ins(referral_path=f"o/{i}.png", ai_json={"verdict": "pendiente_ia"},
                    created=_at(-10 + i)) for i in range(5)]
        self.assertEqual([r["id"] for r in db_booking.booking_requests_pending_ai(limit=2)], ids[:2])
        self.assertEqual(len(db_booking.booking_requests_pending_ai()), 5)

    def test_count_counts_every_status_with_the_pending_verdict(self):
        _ins(ai_json={"verdict": "pendiente_ia"})
        _ins(status="aprobada", ai_json={"verdict": "pendiente_ia"})
        _ins(ai_json={"verdict": "ok"})
        _ins()
        self.assertEqual(db_booking.count_pending_ai(), 2)

    def test_count_is_zero_when_the_database_fails(self):
        with mock.patch.object(db, "_connect", side_effect=db.DbUnavailable("down")):
            self.assertEqual(db_booking.count_pending_ai(), 0)


# ---- referral retention ----------------------------------------------------------------

class TestReferralRetention(DbCase):
    def test_terminal_requests_past_the_retention_are_candidates(self):
        old = _at(-100)
        approved = _ins(status="aprobada", appointment_at=_at(-60), referral_path="o/1")
        rejected = _ins(status="rechazada", resolved_at=_at(-50), referral_path="o/2", created=old)
        rejected_no_resolution = _ins(status="rechazada", referral_path="o/3", created=_at(-70))
        cancelled = _ins(status="cancelada", cancelled_at=_at(-40), referral_path="o/4", created=old)
        cancelled_by_resolution = _ins(status="cancelada", resolved_at=_at(-45), referral_path="o/5",
                                       created=old)
        cancelled_by_creation = _ins(status="cancelada", referral_path="o/6", created=_at(-80))
        rows = db_booking.referrals_to_purge(30)
        self.assertEqual(sorted(r["id"] for r in rows),
                         sorted([approved, rejected, rejected_no_resolution, cancelled,
                                 cancelled_by_resolution, cancelled_by_creation]))
        self.assertEqual(sorted(rows[0]), ["id", "orden_path"])
        self.assertEqual({r["id"]: r["orden_path"] for r in rows}[approved], "o/1")

    def test_never_candidates(self):
        _ins(status="aprobada", appointment_at=_at(5), referral_path="o/1", created=_at(-100))   # future
        _ins(status="aprobada", referral_path="o/2", created=_at(-100))                           # no time
        _ins(status="pendiente", referral_path="o/3", created=_at(-100))
        _ins(status="rechazada", resolved_at=_at(-100), created=_at(-100))                        # no file
        _ins(status="rechazada", resolved_at=_at(-10), referral_path="o/4", created=_at(-100))    # recent
        self.assertEqual(db_booking.referrals_to_purge(30), [])

    def test_oldest_terminal_first_and_limited_and_retention_is_a_parameter(self):
        a = _ins(status="rechazada", resolved_at=_at(-90), referral_path="o/a")
        b = _ins(status="rechazada", resolved_at=_at(-50), referral_path="o/b")
        c = _ins(status="rechazada", resolved_at=_at(-70), referral_path="o/c")
        self.assertEqual([r["id"] for r in db_booking.referrals_to_purge(30)], [a, c, b])
        self.assertEqual([r["id"] for r in db_booking.referrals_to_purge(30, limit=2)], [a, c])
        self.assertEqual([r["id"] for r in db_booking.referrals_to_purge(60)], [a, c])
        self.assertEqual([r["id"] for r in db_booking.referrals_to_purge("60", "1")], [a])

    def test_clear_referral_path_clears_path_and_mime_of_that_row_only(self):
        a = _ins(referral_path="o/a", referral_mime="image/png")
        b = _ins(referral_path="o/b", referral_mime="image/png")
        db_booking.clear_referral_path(a)
        db_booking.clear_referral_path(404)
        self.assertEqual((_row(a)["referral_path"], _row(a)["referral_mime"]), (None, None))
        self.assertEqual(_row(b)["referral_path"], "o/b")


# ---- the patient's account view ----------------------------------------------------------

LIST_BY_DNI_KEYS = ["id", "dni", "nombre", "apellido", "solicitante_dni", "servicio_id", "centro_slug",
                    "estado", "turno_dt", "preferencia_horario", "medico_asignado", "medico_asignado_id",
                    "motivo_rechazo", "created", "ai_json", "requested_at", "cancelled_actor"]


class TestListByDni(DbCase):
    def test_own_and_requested_rows_newest_first_with_the_wire_keys(self):
        own = _ins(dni="100", created=_at(-3))
        asked = _ins(dni="200", requester_dni="100", created=_at(-2), ai_json={"preparacion": ["x"]})
        both = _ins(dni="100", requester_dni="100", created=_at(-1))
        _ins(dni="300", requester_dni="300")
        rows = db_booking.list_by_dni("100")
        self.assertEqual([r["id"] for r in rows], [both, asked, own])
        self.assertEqual(list(rows[0]), LIST_BY_DNI_KEYS)
        self.assertEqual(rows[1]["ai_json"], {"preparacion": ["x"]})
        self.assertEqual(rows[1]["solicitante_dni"], "100")

    def test_cancelled_actor_is_the_actor_of_the_cancellation_event(self):
        by_patient = _ins(dni="100", status="aprobada", appointment_at=_at(3))
        by_desk = _ins(dni="100", status="aprobada", appointment_at=_at(4))
        before_events = _ins(dni="100", status="cancelada")   # no event: cancelled before appointment_events
        live = _ins(dni="100", status="pendiente")
        db_booking.cancel(by_patient, actor="paciente")
        db_booking.cancel(by_desk, actor="mesa.prueba")
        actors = {r["id"]: r["cancelled_actor"] for r in db_booking.list_by_dni("100")}
        self.assertEqual(actors, {by_patient: "paciente", by_desk: "mesa.prueba",
                                  before_events: None, live: None})
        self.assertEqual([db_booking.cancel_actor(i) for i in (by_patient, by_desk, before_events, live)],
                         ["paciente", "mesa.prueba", None, None])

    def test_none_and_unknown_find_nothing_even_over_null_columns(self):
        _ins(dni="100")                       # requester_dni is NULL
        _ins(dni=None, requester_dni="100")   # dni is NULL
        self.assertEqual(db_booking.list_by_dni(None), [])
        self.assertEqual(db_booking.list_by_dni("999"), [])


# ---- staff lists --------------------------------------------------------------------------

class TestListAndCountAdmin(DbCase):
    def _seed(self):
        ids = {}
        ids["pend_old"] = _ins(status="pendiente", created=_at(-90))
        ids["pend_new"] = _ins(status="pendiente", created=_at(-1))
        ids["appr_new"] = _ins(status="aprobada", created=_at(-2), appointment_at=_at(3))
        ids["appr_old"] = _ins(status="aprobada", created=_at(-40), appointment_at=_at(-39))
        ids["rej_new"] = _ins(status="rechazada", created=_at(-5))
        ids["canc_old"] = _ins(status="cancelada", created=_at(-60))
        return ids

    def test_default_view_is_every_pending_plus_the_recent_resolved(self):
        ids = self._seed()
        rows = db_booking.list_admin()
        self.assertEqual([r["id"] for r in rows],
                         [ids["pend_new"], ids["appr_new"], ids["rej_new"], ids["pend_old"]])
        # cancelled_actor rides along for the queue's «Cancelada por …» and `_admin_row` drops it.
        self.assertEqual(list(rows[0]), _wire_keys() + ["cancelled_actor"])
        self.assertEqual(db_booking.count_admin(), 4)

    def test_state_filter_returns_only_that_state_newest_first(self):
        ids = self._seed()
        self.assertEqual([r["id"] for r in db_booking.list_admin("aprobada")], [ids["appr_new"], ids["appr_old"]])
        self.assertEqual([r["id"] for r in db_booking.list_admin("pendiente")], [ids["pend_new"], ids["pend_old"]])
        self.assertEqual(db_booking.list_admin("inexistente"), [])
        self.assertEqual(db_booking.count_admin("aprobada"), 2)
        self.assertEqual(db_booking.count_admin("cancelada"), 1)
        self.assertEqual(db_booking.count_admin("inexistente"), 0)

    def test_empty_state_means_the_default_view(self):
        self._seed()
        self.assertEqual(len(db_booking.list_admin("")), 4)
        self.assertEqual(db_booking.count_admin(""), 4)

    def test_limit_and_offset(self):
        ids = self._seed()
        self.assertEqual([r["id"] for r in db_booking.list_admin(limit=2)], [ids["pend_new"], ids["appr_new"]])
        self.assertEqual([r["id"] for r in db_booking.list_admin(limit=2, offset=2)],
                         [ids["rej_new"], ids["pend_old"]])
        self.assertEqual([r["id"] for r in db_booking.list_admin("aprobada", limit="1", offset="1")],
                         [ids["appr_old"]])
        self.assertEqual(db_booking.list_admin(limit=2, offset=10), [])
        self.assertEqual(db_booking.count_admin(), 4, "the count ignores the page")

    def test_default_limit_is_three_hundred(self):
        with mock.patch.object(db_booking, "_ADMIN_LIST_DEFAULT_LIMIT", 2):
            self._seed()
            self.assertEqual(len(db_booking.list_admin()), 2)
            self.assertEqual(len(db_booking.list_admin(limit=0)), 2)


class TestApprovedInRange(DbCase):
    def test_half_open_range_ordered_by_time_and_only_approved(self):
        base = _at(5).replace(minute=0, second=0)
        a = _ins(status="aprobada", appointment_at=base)
        b = _ins(status="aprobada", appointment_at=base + timedelta(minutes=30))
        _ins(status="aprobada", appointment_at=base + timedelta(hours=1))        # `hasta` is exclusive
        _ins(status="aprobada", appointment_at=base - timedelta(minutes=1))
        _ins(status="pendiente", appointment_at=base + timedelta(minutes=10))
        _ins(status="cancelada", appointment_at=base + timedelta(minutes=10))
        _ins(status="aprobada")
        rows = db_booking.approved_in_range(base, base + timedelta(hours=1))
        self.assertEqual([r["id"] for r in rows], [a, b])
        self.assertEqual(list(rows[0]), _wire_keys())
        self.assertEqual(db_booking.approved_in_range(base + timedelta(days=1), base + timedelta(days=2)), [])


class TestApprovedFutureWindows(DbCase):
    def test_future_approved_in_time_order_with_open_ended_defaults(self):
        late = _ins(status="aprobada", appointment_at=_at(9))
        soon = _ins(status="aprobada", appointment_at=_at(1))
        _ins(status="aprobada", appointment_at=_at(-1))
        _ins(status="pendiente", appointment_at=_at(2))
        _ins(status="aprobada")
        rows = db_booking.list_approved_future()
        self.assertEqual([r["id"] for r in rows], [soon, late])
        self.assertEqual(list(rows[0]), _wire_keys())

    def test_explicit_bounds_are_inclusive(self):
        t1, t2, t3 = _at(1), _at(2), _at(3)
        a, b, c = (_ins(status="aprobada", appointment_at=t) for t in (t1, t2, t3))
        self.assertEqual([r["id"] for r in db_booking.list_approved_future(desde=t2)], [b, c])
        self.assertEqual([r["id"] for r in db_booking.list_approved_future(hasta=t2)], [a, b])
        self.assertEqual([r["id"] for r in db_booking.list_approved_future(t2, t2)], [b])
        past = _ins(status="aprobada", appointment_at=_at(-5))
        self.assertEqual([r["id"] for r in db_booking.list_approved_future(desde=_at(-6), hasta=_at(-4))], [past])

    def test_for_one_doctor_also_returns_the_rows_with_no_resolved_doctor(self):
        mine = _ins(status="aprobada", appointment_at=_at(1), assigned_doctor_id="d1")
        legacy = _ins(status="aprobada", appointment_at=_at(2), assigned_doctor="Dr. Luis")
        _ins(status="aprobada", appointment_at=_at(3), assigned_doctor_id="d2")
        _ins(status="aprobada", appointment_at=_at(-3), assigned_doctor_id="d1")
        _ins(status="pendiente", appointment_at=_at(4), assigned_doctor_id="d1")
        rows = db_booking.list_approved_future_for_doctor("d1")
        self.assertEqual([r["id"] for r in rows], [mine, legacy])
        self.assertEqual(list(rows[0]), _wire_keys())
        self.assertEqual([r["id"] for r in db_booking.list_approved_future_for_doctor("d1", hasta=_at(1))], [mine])
        self.assertEqual([r["id"] for r in db_booking.list_approved_future_for_doctor("d1", desde=_at(2))], [legacy])

    def test_for_one_doctor_coerces_the_id_to_text_and_empty_means_nobody(self):
        mine = _ins(status="aprobada", appointment_at=_at(1), assigned_doctor_id="17")
        self.assertEqual([r["id"] for r in db_booking.list_approved_future_for_doctor(17)], [mine])
        self.assertEqual(db_booking.list_approved_future_for_doctor(None), [])
        self.assertEqual(db_booking.list_approved_future_for_doctor(""), [])


# ---- confirmation ---------------------------------------------------------------------------

class TestBookingRequestToConfirm(DbCase):
    def test_nearest_future_unconfirmed_approved_of_the_patient_or_the_requester(self):
        far = _ins(dni="500", status="aprobada", appointment_at=_at(5))
        near = _ins(dni="500", status="aprobada", appointment_at=_at(2))
        asked = _ins(dni="600", requester_dni="500", status="aprobada", appointment_at=_at(1))
        _ins(dni="500", status="aprobada", appointment_at=_at(-1))                       # past
        _ins(dni="500", status="aprobada", appointment_at=_at(1), confirmation="si")    # answered
        _ins(dni="500", status="pendiente", appointment_at=_at(1))
        _ins(dni="700", status="aprobada", appointment_at=_at(1))
        row = db_booking.booking_request_to_confirm("500")
        self.assertEqual(row["id"], asked)
        self.assertEqual(list(row), _wire_keys())
        db_booking.mark_confirmation(asked, "no")
        self.assertEqual(db_booking.booking_request_to_confirm("500")["id"], near)
        db_booking.mark_confirmation(near, "si")
        self.assertEqual(db_booking.booking_request_to_confirm("500")["id"], far)

    def test_the_number_is_reduced_to_its_digits(self):
        sid = _ins(dni="30111222", status="aprobada", appointment_at=_at(1))
        self.assertEqual(db_booking.booking_request_to_confirm("30.111.222")["id"], sid)
        self.assertEqual(db_booking.booking_request_to_confirm(30111222)["id"], sid)

    def test_now_can_be_given(self):
        sid = _ins(dni="500", status="aprobada", appointment_at=_at(1))
        self.assertEqual(db_booking.booking_request_to_confirm("500", ahora=_at(0))["id"], sid)
        self.assertIsNone(db_booking.booking_request_to_confirm("500", ahora=_at(2)))

    def test_empty_none_and_unknown_find_nothing_over_null_columns(self):
        _ins(dni="500", status="aprobada", appointment_at=_at(1))     # requester_dni NULL
        for value in (None, "", "abc", "999"):
            self.assertIsNone(db_booking.booking_request_to_confirm(value), repr(value))


class TestMarkConfirmation(DbCase):
    def test_stores_the_answer_the_time_and_the_channel(self):
        sid = _ins(status="aprobada", appointment_at=_at(1))
        row = db_booking.mark_confirmation(sid, "si", "whatsapp")
        self.assertEqual((row["confirmacion"], row["confirmacion_canal"]), ("si", "whatsapp"))
        self.assertIsNotNone(row["confirmacion_en"])
        self.assertEqual(list(row), _wire_keys())
        row = db_booking.mark_confirmation(sid, "no")
        self.assertEqual((row["confirmacion"], row["confirmacion_canal"]), ("no", None))

    def test_invalid_answer_raises_and_unknown_id_is_none(self):
        sid = _ins()
        with self.assertRaises(ValueError):
            db_booking.mark_confirmation(sid, "tal vez")
        self.assertIsNone(_row(sid)["confirmation"])
        self.assertIsNone(db_booking.mark_confirmation(404, "si"))


class TestConfirmToken(DbCase):
    def test_created_once_and_idempotent(self):
        sid = _ins()
        token = db_booking.booking_request_confirm_token(sid)
        self.assertTrue(token and len(token) >= 24)
        self.assertEqual(db_booking.booking_request_confirm_token(sid), token)
        self.assertEqual(_row(sid)["confirm_token"], token)
        other = _ins()
        self.assertNotEqual(db_booking.booking_request_confirm_token(other), token)

    def test_an_existing_token_is_returned_stripped_and_a_blank_one_is_replaced(self):
        kept = _ins(confirm_token="  tok-viejo  ")
        self.assertEqual(db_booking.booking_request_confirm_token(kept), "tok-viejo")
        blank = _ins(confirm_token="   ")
        fresh = db_booking.booking_request_confirm_token(blank)
        self.assertTrue(fresh.strip())
        self.assertEqual(_row(blank)["confirm_token"], fresh)

    def test_unknown_request_is_none(self):
        self.assertIsNone(db_booking.booking_request_confirm_token(404))
        self.assertIsNone(db_booking.booking_request_confirm_token(None))

    def test_lookup_by_token(self):
        sid = _ins(confirm_token="tok-abc")
        _ins(confirm_token="tok-def")
        row = db_booking.booking_request_by_confirm_token(" tok-abc ")
        self.assertEqual(row["id"], sid)
        self.assertEqual(list(row), _wire_keys())

    def test_lookup_by_blank_or_unknown_token_is_none_even_with_null_tokens(self):
        _ins()      # confirm_token IS NULL
        for value in (None, "", "   ", "nope"):
            self.assertIsNone(db_booking.booking_request_by_confirm_token(value), repr(value))


# ---- HSI re-push list ---------------------------------------------------------------------------

class TestHsiPending(DbCase):
    def test_future_approved_or_cancelled_whose_push_failed(self):
        a = _ins(status="aprobada", appointment_at=_at(2), hsi_status="error")
        b = _ins(status="cancelada", appointment_at=_at(1), hsi_status="pendiente")
        _ins(status="aprobada", appointment_at=_at(1), hsi_status="ok")
        _ins(status="aprobada", appointment_at=_at(1))                       # hsi_status NULL
        _ins(status="aprobada", appointment_at=_at(-1), hsi_status="error")  # already past
        _ins(status="pendiente", appointment_at=_at(1), hsi_status="error")
        _ins(status="aprobada", hsi_status="error")                          # no time
        rows = db_booking.booking_requests_hsi_pending()
        self.assertEqual([r["id"] for r in rows], [b, a])
        self.assertEqual(list(rows[0]), _wire_keys())

    def test_since_can_be_given(self):
        old = _ins(status="aprobada", appointment_at=_at(-1), hsi_status="error")
        new = _ins(status="aprobada", appointment_at=_at(1), hsi_status="error")
        self.assertEqual([r["id"] for r in db_booking.booking_requests_hsi_pending(_at(-2))], [old, new])
        self.assertEqual([r["id"] for r in db_booking.booking_requests_hsi_pending(_at(0))], [new])


class TestUpdateHsi(DbCase):
    def test_writes_the_result_and_keeps_the_previous_ids_when_none(self):
        sid = _ins(status="aprobada", hsi_patient_id="p1", hsi_appointment_id="a1", hsi_status="error")
        row = db_booking.update_hsi(sid, {"estado": "ok"})
        self.assertEqual((row["hsi_patient_id"], row["hsi_appointment_id"], row["hsi_estado"]),
                         ("p1", "a1", "ok"))
        row = db_booking.update_hsi(sid, {"estado": "ok", "patient_id": "p2", "appointment_id": "a2"})
        self.assertEqual((row["hsi_patient_id"], row["hsi_appointment_id"]), ("p2", "a2"))
        self.assertEqual(list(row), _wire_keys())
        self.assertEqual(_row(sid)["status"], "aprobada", "status and time are not touched")

    def test_the_status_is_always_overwritten(self):
        sid = _ins(hsi_status="error")
        self.assertIsNone(db_booking.update_hsi(sid, None)["hsi_estado"])

    def test_checkpoint_is_stored_and_replaced_for_an_appointment(self):
        sid = _ins()
        db_booking.update_hsi(sid, {"estado": "ok", "appointment_id": "A9",
                            "checkpoint": {"v": 1, "when": datetime(2030, 1, 2, 3, 4, tzinfo=UTC)}})
        rows = _sql("SELECT * FROM hsi_checkpoints")
        self.assertEqual([(r["entity_type"], r["entity_id"]) for r in rows], [("appointment", "A9")])
        self.assertEqual(rows[0]["remote"], {"v": 1, "when": "2030-01-02T03:04:00+00:00"})
        db_booking.update_hsi(sid, {"estado": "ok", "appointment_id": "A9", "checkpoint": {"v": 2}})
        rows = _sql("SELECT * FROM hsi_checkpoints")
        self.assertEqual([r["remote"] for r in rows], [{"v": 2}])

    def test_no_checkpoint_without_an_appointment_id_or_a_checkpoint(self):
        sid = _ins()
        db_booking.update_hsi(sid, {"estado": "ok", "checkpoint": {"v": 1}})
        db_booking.update_hsi(sid, {"estado": "ok", "appointment_id": "A9"})
        self.assertEqual(_sql("SELECT * FROM hsi_checkpoints"), [])

    def test_unknown_id_returns_none(self):
        self.assertIsNone(db_booking.update_hsi(404, {"estado": "ok"}))


# ---- reminders -------------------------------------------------------------------------------------

REMIND_KEYS = ["id", "nombre", "apellido", "dni", "solicitante_dni", "servicio_id", "centro_slug",
               "contacto", "turno_dt", "medico_asignado", "medico_asignado_id", "ai_json"]


class TestReminderSweeps(DbCase):
    def _art_day(self, y, m, d, hour=10):
        return datetime(y, m, d, hour, 0, tzinfo=db_values._ART)

    def test_candidates_are_the_approved_unreminded_of_that_art_day(self):
        day = self._art_day(2031, 3, 10)
        late = _ins(status="aprobada", appointment_at=day.replace(hour=15),
                    confirm_token="tk", contact="ana@x.test", ai_json={"a": 1})
        early = _ins(status="aprobada", appointment_at=day.replace(hour=8))
        _ins(status="aprobada", appointment_at=day.replace(hour=9), reminder_at=_at(-1))
        _ins(status="pendiente", appointment_at=day.replace(hour=9))
        _ins(status="aprobada", appointment_at=day + timedelta(days=1))
        rows = db_booking.booking_requests_to_remind(date(2031, 3, 10))
        self.assertEqual([r["id"] for r in rows], [early, late])
        self.assertEqual(list(rows[0]), REMIND_KEYS + ["confirm_token"])
        self.assertEqual(rows[1]["confirm_token"], "tk")
        self.assertEqual(rows[1]["contacto"], "ana@x.test")

    def test_the_day_is_the_art_day_not_the_utc_day(self):
        # 01:30 UTC on the 11th is 22:30 on the 10th in Buenos Aires
        sid = _ins(status="aprobada", appointment_at=datetime(2031, 3, 11, 1, 30, tzinfo=UTC))
        self.assertEqual([r["id"] for r in db_booking.booking_requests_to_remind(date(2031, 3, 10))], [sid])
        self.assertEqual(db_booking.booking_requests_to_remind(date(2031, 3, 11)), [])
        self.assertEqual([r["id"] for r in db_booking.booking_requests_to_remind("2031-03-10")], [sid])

    def test_interview_sweep_has_its_own_mark_and_no_token_column(self):
        day = self._art_day(2031, 3, 10)
        a = _ins(status="aprobada", appointment_at=day, reminder_at=_at(-1), assigned_doctor_id="d1")
        _ins(status="aprobada", appointment_at=day, interview_reminder_at=_at(-1))
        rows = db_booking.booking_requests_to_remind_interview(date(2031, 3, 10))
        self.assertEqual([r["id"] for r in rows], [a])
        self.assertEqual(list(rows[0]), REMIND_KEYS)
        self.assertEqual(rows[0]["medico_asignado_id"], "d1")

    def test_none_date_finds_nothing(self):
        _ins(status="aprobada", appointment_at=_at(1))
        _ins(status="aprobada")      # no time: its local date IS NULL, and `= NULL` must not find it
        self.assertEqual(db_booking.booking_requests_to_remind(None), [])
        self.assertEqual(db_booking.booking_requests_to_remind_interview(None), [])

    def test_mark_reminder_stamps_the_row_and_nothing_else(self):
        a, b = _ins(), _ins()
        self.assertIsNone(db_booking.mark_reminder(a))
        self.assertIsNotNone(_row(a)["reminder_at"])
        self.assertIsNone(_row(b)["reminder_at"])
        stamp = _row(a)["reminder_at"]
        db_booking.mark_reminder(a)
        self.assertGreaterEqual(_row(a)["reminder_at"], stamp, "mark_reminder does not check the mark")
        db_booking.mark_reminder(404)

    def test_claim_reminder_is_won_once(self):
        sid = _ins()
        self.assertTrue(db_booking.claim_reminder(sid))
        stamp = _row(sid)["reminder_at"]
        self.assertIsNotNone(stamp)
        self.assertFalse(db_booking.claim_reminder(sid))
        self.assertEqual(_row(sid)["reminder_at"], stamp, "the loser does not move the mark")
        self.assertFalse(db_booking.claim_reminder(404))
        self.assertIsNone(_row(sid)["interview_reminder_at"])

    def test_interview_marks(self):
        a, b = _ins(), _ins()
        db_booking.mark_interview_reminder(a)
        self.assertIsNotNone(_row(a)["interview_reminder_at"])
        self.assertIsNone(_row(a)["reminder_at"])
        self.assertTrue(db_booking.claim_interview_reminder(b))
        self.assertFalse(db_booking.claim_interview_reminder(b))
        self.assertFalse(db_booking.claim_interview_reminder(404))

    def test_a_marked_row_leaves_the_sweep(self):
        day = self._art_day(2031, 3, 10)
        sid = _ins(status="aprobada", appointment_at=day)
        self.assertTrue(db_booking.claim_reminder(sid))
        self.assertEqual(db_booking.booking_requests_to_remind(date(2031, 3, 10)), [])
        self.assertEqual([r["id"] for r in db_booking.booking_requests_to_remind_interview(date(2031, 3, 10))], [sid])

    def test_two_threads_claim_one_row_exactly_once(self):
        import threading
        sid = _ins()
        results = []
        threads = [threading.Thread(target=lambda: results.append(db_booking.claim_reminder(sid)))
                   for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [False] * 5 + [True])


# ---- attendance summaries ----------------------------------------------------------------------------

def _hsi_no_show(dni, appt_id, scheduled_at, patient_id=None):
    patient_id = patient_id or f"P-{dni}"
    _sql("INSERT INTO hsi_patients (hsi_patient_id, dni, payload) VALUES (%s, %s, %s) "
         "ON CONFLICT DO NOTHING", (patient_id, dni, Json({})))
    _sql("INSERT INTO hsi_appointments (hsi_appointment_id, hsi_patient_id, payload) VALUES (%s, %s, %s)",
         (appt_id, patient_id, Json({"state": "no_show", "scheduled_at": scheduled_at})))


class TestAttendanceSummary(DbCase):
    EMPTY = {"visitas": 0, "presentes": 0, "faltas": 0, "faltas_hsi": 0, "bloqueado_web": False,
             "historial": []}

    def test_empty_input_and_blank_dnis(self):
        self.assertEqual(db_booking.attendance_summary_by_dnis([]), {})
        self.assertEqual(db_booking.attendance_summary_by_dnis([None, "", "  "]), {})
        self.assertEqual(db_booking.attendance_summary_dni("404"), self.EMPTY)
        self.assertEqual(db_booking.attendance_summary_dni(None), self.EMPTY)

    def test_counts_per_patient_and_the_block_threshold(self):
        for att in ("presente", "ausente", "ausente", None):
            _ins(dni="100", attendance=att)
        _ins(dni="200", attendance="presente")
        _ins(dni="300")
        out = db_booking.attendance_summary_by_dnis([" 100 ", "200", "300", "400", "100"], limite=2)
        self.assertEqual(sorted(out), ["100", "200", "300", "400"])
        self.assertEqual({k: out["100"][k] for k in ("visitas", "presentes", "faltas", "faltas_hsi")},
                         {"visitas": 3, "presentes": 1, "faltas": 2, "faltas_hsi": 0})
        self.assertIs(out["100"]["bloqueado_web"], True)
        self.assertEqual((out["200"]["visitas"], out["200"]["faltas"], out["200"]["bloqueado_web"]),
                         (1, 0, False))
        self.assertEqual(out["300"], self.EMPTY)
        self.assertEqual(out["400"], self.EMPTY)
        self.assertIs(db_booking.attendance_summary_by_dnis(["100"], limite=3)["100"]["bloqueado_web"], False)
        self.assertIs(db_booking.attendance_summary_by_dnis(["100"], limite=1)["100"]["bloqueado_web"], True)
        self.assertIs(db_booking.attendance_summary_by_dnis(["100"], limite=0)["100"]["bloqueado_web"], False,
                      "a falsy limit means the default of three")
        self.assertIs(db_booking.attendance_summary_by_dnis(["100"], limite=None)["100"]["bloqueado_web"], False)

    def test_history_comes_from_the_attendance_events_newest_first(self):
        sid = _ins(dni="100", status="aprobada", appointment_at=_at(-1))
        base = _at(-5)
        for i, (de, a, op) in enumerate([(None, "presente", "mesa:ana"), ("presente", "ausente", "mesa:luis")]):
            _sql("INSERT INTO attendance_events (request_id, dni, attendance_from, attendance_to, operator, "
                 "source, created) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                 (sid, "100", de, a, op, "sana", base + timedelta(hours=i)))
        out = db_booking.attendance_summary_by_dnis(["100"])["100"]
        self.assertEqual([(e["de_asistencia"], e["a_asistencia"], e["operador"]) for e in out["historial"]],
                         [("presente", "ausente", "mesa:luis"), (None, "presente", "mesa:ana")])
        self.assertEqual(sorted(out["historial"][0]),
                         sorted(["solicitud_id", "dni", "de_asistencia", "a_asistencia", "operador",
                                 "origen", "created"]))
        self.assertIsInstance(out["historial"][0]["created"], str, "timestamps travel as ISO text")
        self.assertEqual(out["historial"][0]["solicitud_id"], sid)

    def test_history_is_capped_at_twelve(self):
        sid = _ins(dni="100")
        for i in range(15):
            _sql("INSERT INTO attendance_events (request_id, dni, attendance_to, operator, created) "
                 "VALUES (%s,'100','presente','op',%s)", (sid, _at(-20 + i)))
        self.assertEqual(len(db_booking.attendance_summary_by_dnis(["100"])["100"]["historial"]), 12)

    def test_hsi_no_shows_count_until_a_request_is_linked(self):
        _hsi_no_show("100", "A1", "2030-01-02T10:00:00+00:00")
        _hsi_no_show("100", "A2", "2030-01-03T10:00:00+00:00")
        _hsi_no_show("100", "A3", "2030-01-04T10:00:00+00:00")
        _ins(dni="100", hsi_appointment_id="A3", attendance="ausente")     # linked: counted once
        _sql("INSERT INTO hsi_appointments (hsi_appointment_id, hsi_patient_id, payload) "
             "VALUES ('A4', 'P-100', %s)", (Json({"state": "attended", "scheduled_at": "2030-01-05"}),))
        out = db_booking.attendance_summary_by_dnis(["100"])["100"]
        self.assertEqual((out["visitas"], out["faltas"], out["faltas_hsi"]), (3, 3, 2))
        hsi = [e for e in out["historial"] if e["origen"] == "hsi"]
        self.assertEqual([e["created"] for e in hsi],
                         ["2030-01-03T10:00:00+00:00", "2030-01-02T10:00:00+00:00"])
        self.assertEqual(hsi[0], {"solicitud_id": None, "dni": "100", "de_asistencia": None,
                                  "a_asistencia": "ausente", "operador": "HSI", "origen": "hsi",
                                  "created": "2030-01-03T10:00:00+00:00"})

    def test_a_history_entry_with_no_readable_time_sorts_last(self):
        sid = _ins(dni="100")
        _sql("INSERT INTO attendance_events (request_id, dni, attendance_to, operator, created) "
             "VALUES (%s,'100','presente','op',%s)", (sid, _at(-30)))
        _hsi_no_show("100", "A1", "no es una fecha")
        _hsi_no_show("100", "A2", "2030-01-02T10:00:00+00:00")
        history = db_booking.attendance_summary_by_dnis(["100"])["100"]["historial"]
        self.assertEqual([e["origen"] for e in history], ["hsi", "sana", "hsi"])
        self.assertEqual(history[-1]["created"], "no es una fecha")

    def test_attendance_summary_dni_is_the_single_patient_view(self):
        _ins(dni="100", attendance="ausente")
        out = db_booking.attendance_summary_dni(" 100 ", limite=1)
        self.assertEqual((out["faltas"], out["bloqueado_web"]), (1, True))


class TestRankingHistory(DbCase):
    def test_only_granted_requests_of_the_given_patients_inside_the_window(self):
        a = _ins(dni="100", status="aprobada", appointment_at=_at(-3), attendance="presente")
        b = _ins(dni="100", status="cancelada", appointment_at=None, created=_at(-10),
                 cancelled_at=_at(-9))
        c = _ins(dni="200", status="aprobada", appointment_at=_at(2))
        _ins(dni="100", status="pendiente", appointment_at=_at(1))
        _ins(dni="100", status="rechazada", appointment_at=_at(1))
        _ins(dni="300", status="aprobada", appointment_at=_at(1))
        _ins(dni="100", status="aprobada", appointment_at=_at(-5000))        # outside the window
        rows = db_booking.booking_requests_for_ranking(["100", "200", " "])
        self.assertEqual([r["id"] for r in rows], [a, c, b], "ordered by time, no time last")
        self.assertEqual(sorted(rows[0]), sorted(
            ["id", "dni", "servicio_id", "estado", "turno_dt", "asistencia", "confirmacion",
             "cancelada_en", "created"]))

    def test_window_is_a_parameter_with_a_floor_of_thirty_days(self):
        old = _ins(dni="100", status="aprobada", appointment_at=_at(-100))
        recent = _ins(dni="100", status="aprobada", appointment_at=_at(-10))
        self.assertEqual([r["id"] for r in db_booking.booking_requests_for_ranking(["100"], ventana_dias=200)],
                         [old, recent])
        self.assertEqual([r["id"] for r in db_booking.booking_requests_for_ranking(["100"], ventana_dias=50)],
                         [recent])
        self.assertEqual([r["id"] for r in db_booking.booking_requests_for_ranking(["100"], ventana_dias=1)],
                         [recent])

    def test_empty_input(self):
        _ins(dni="100", status="aprobada", appointment_at=_at(1))
        self.assertEqual(db_booking.booking_requests_for_ranking([]), [])
        self.assertEqual(db_booking.booking_requests_for_ranking([None, ""]), [])

    def test_hsi_no_shows_become_synthetic_approved_absences(self):
        when = (datetime.now(UTC) - timedelta(days=20)).replace(microsecond=0)
        _hsi_no_show("100", "A1", when.isoformat())
        _hsi_no_show("100", "A2", (datetime.now(UTC) - timedelta(days=4000)).isoformat())   # out of window
        _hsi_no_show("100", "A3", "no es una fecha")
        _hsi_no_show("100", "A4", (when - timedelta(days=1)).isoformat())
        _ins(dni="100", hsi_appointment_id="A4", status="aprobada", appointment_at=when - timedelta(days=1),
             attendance="ausente")
        _sql("INSERT INTO hsi_appointments (hsi_appointment_id, hsi_patient_id, payload) VALUES "
             "('A5', 'P-100', %s)", (Json({"state": "no_show"}),))                          # no time
        rows = db_booking.booking_requests_for_ranking(["100"])
        synthetic = [r for r in rows if r["id"] is None]
        self.assertEqual(len(synthetic), 1)
        self.assertEqual({k: synthetic[0][k] for k in ("dni", "servicio_id", "estado", "asistencia",
                                                       "confirmacion", "cancelada_en")},
                         {"dni": "100", "servicio_id": "hsi:A1", "estado": "aprobada",
                          "asistencia": "ausente", "confirmacion": None, "cancelada_en": None})
        self.assertEqual(synthetic[0]["turno_dt"], when)
        self.assertEqual(len(rows), 2, "the linked request counts once, as the request")


if __name__ == "__main__":
    unittest.main()
