"""The four findings of the 2026-09-23 formal sweep that needed schema work
(closed 2026-09-24). Each class names the model property it pins:

- DniMove.lean `closed` — change_dni moves a person's whole clinical identity,
  and a new DNI-keyed column can't be added without deciding its fate.
- SlotHold.tla `OneApprovedPerSlot` — one approved regular appointment per
  (service, time) when no doctor is resolved.
- AppointmentHsi.tla `AuditReconstructable` — every status / time /
  attendance change leaves an `appointment_events` row in its own
  transaction, enough to replay the request's history.
- IntakeTriage.tla `OneInterviewPerRequest` — one interview bound per
  request, and the anonymous QR door never receives an existing session.
"""
import importlib.util
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_formal_inv")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _raw():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5,
                           row_factory=dict_row)


def _truncate(*tables):
    with _raw() as c, c.cursor() as cur:
        cur.execute("TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(tables))


def _future(days=3, hour=10):
    d = datetime.now(timezone.utc) + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


def _request(dni="30111222", servicio_id="lab", **kw):
    return db_booking.create_booking_request(
        dni=dni, nombre="Ana", apellido="Paz", nacimiento="1980-01-01",
        contacto="ana@example.com", servicio_id=servicio_id, centro_slug="c",
        **kw)


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)


# ---- 1. change_dni closure ----------------------------------------------

def _live_dni_columns():
    with _raw() as c, c.cursor() as cur:
        cur.execute("""SELECT c.table_name, c.column_name
                         FROM information_schema.columns c
                         JOIN information_schema.tables t
                           ON t.table_name=c.table_name AND t.table_schema=c.table_schema
                        WHERE c.table_schema='public' AND t.table_type='BASE TABLE'
                          AND c.column_name ILIKE %s""", ("%dni%",))
        # `dni_claims.claimant_key` holds a users.dni key (the claimant's account) without "dni" in its name.
        return {(r["table_name"], r["column_name"]) for r in cur.fetchall()} | {("dni_claims", "claimant_key")}


def _required_columns(table):
    with _raw() as c, c.cursor() as cur:
        cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=%s
                          AND is_nullable='NO' AND column_default IS NULL""", (table,))
        return [(r["column_name"], r["data_type"]) for r in cur.fetchall()]


_FILLER = {"date": datetime(2000, 1, 1).date(), "jsonb": {}, "integer": 1,
           "bigint": 1, "boolean": False, "bytea": b""}
_OVERRIDES = {("attendance_events", "attendance_to"): "presente"}


def _seed_row(table, values):
    """Insert one row into `table` with `values` plus a type-shaped filler
    for every other NOT NULL column without default — generic on purpose,
    so a DNI table added later is seedable without touching this test."""
    row = dict(values)
    for n, (col, typ) in enumerate(_required_columns(table)):
        if col in row:
            continue
        if (table, col) in _OVERRIDES:
            row[col] = _OVERRIDES[(table, col)]
        elif typ.startswith("timestamp"):
            row[col] = datetime.now(timezone.utc) + timedelta(days=1)
        elif typ in _FILLER:
            row[col] = _FILLER[typ]
        else:
            row[col] = f"{table}-{col}-{n}-{os.urandom(3).hex()}"
    from psycopg.types.json import Json
    cols = list(row)
    vals = [Json(v) if isinstance(v, dict) else v for v in row.values()]
    with _raw() as c, c.cursor() as cur:
        cur.execute("INSERT INTO %s (%s) VALUES (%s)" % (
            table, ", ".join(cols), ", ".join(["%s"] * len(cols))), vals)


def _count(table, column, value):
    with _raw() as c, c.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {column}=%s", (value,))
        return cur.fetchone()["n"]


class TestChangeDniClosure(DbCase):
    """DniMove.lean `closed`: every DNI-keyed column is moved or explicitly
    kept with a reason."""

    OLD, NEW = "20333444", "20333445"

    def setUp(self):
        super().setUp()
        tables = sorted({t for t, _ in _live_dni_columns()}
                        | {t for t, _ in db_accounts.DNI_PREFIXED_KEY_COLUMNS})
        _truncate(*tables)

    def test_every_dni_column_in_the_schema_is_classified(self):
        live = _live_dni_columns()
        classified = set(db_accounts.DNI_MOVED_COLUMNS) | set(db_accounts.DNI_KEPT_COLUMNS)
        self.assertEqual(
            live - classified, set(),
            "a column storing a DNI is neither moved by change_dni nor kept "
            "with a reason: add it to db_accounts.DNI_MOVED_COLUMNS or DNI_KEPT_COLUMNS")
        self.assertEqual(classified - live, set(),
                         "the registry names a column the schema doesn't have")
        self.assertEqual(set(db_accounts.DNI_MOVED_COLUMNS) & set(db_accounts.DNI_KEPT_COLUMNS), set())

    def _seed_everything_under_old(self):
        _seed_row("users", {"dni": self.OLD})
        for table, column in db_accounts.DNI_MOVED_COLUMNS:
            if table == "users":
                continue
            values = {column: self.OLD}
            for other_t, other_c in db_accounts.DNI_MOVED_COLUMNS:
                if other_t == table and other_c != column:
                    values[other_c] = self.OLD
            if table == "dependents":
                values["dni"] = "40999888"
            _seed_row(table, values)
        for table, column in db_accounts.DNI_PREFIXED_KEY_COLUMNS:
            _seed_row(table, {column: f"dni:{self.OLD}"})
            if table != "chat_compactions":  # patient_key is its primary key
                _seed_row(table, {column: f"dni:{self.OLD}:chat:abc"})
        # someone else's key that merely starts with the same digits
        _seed_row("chat_messages", {"key": f"dni:{self.OLD}9", "channel": "web"})
        # kept columns: another account's dependent with OLD as the child DNI,
        # and HSI's mirror of whoever HSI says holds OLD
        _seed_row("users", {"dni": "27000111"})   # the other adult: dependents.adult_dni is a foreign key
        _seed_row("dependents", {"adult_dni": "27000111", "dni": self.OLD})
        _seed_row("hsi_patients", {"hsi_patient_id": "h1", "dni": self.OLD})

    def test_moves_every_moved_column_and_chat_key(self):
        self._seed_everything_under_old()
        self.assertTrue(db_accounts.change_dni(self.OLD, self.NEW))
        for table, column in db_accounts.DNI_MOVED_COLUMNS:
            self.assertEqual(_count(table, column, self.OLD), 0, f"{table}.{column} left behind")
            self.assertGreaterEqual(_count(table, column, self.NEW), 1, f"{table}.{column} not moved")
        for table, column in db_accounts.DNI_PREFIXED_KEY_COLUMNS:
            with _raw() as c, c.cursor() as cur:
                cur.execute(f"SELECT {column} AS k FROM {table}")
                keys = {r["k"] for r in cur.fetchall()}
            self.assertNotIn(f"dni:{self.OLD}", keys, table)
            self.assertIn(f"dni:{self.NEW}", keys, table)
            if table != "chat_compactions":
                self.assertIn(f"dni:{self.NEW}:chat:abc", keys, table)
        self.assertEqual(_count("chat_messages", "key", f"dni:{self.OLD}9"), 1,
                         "a key that only shares a digit prefix must not move")
        # kept: the other account's dependent row and the HSI mirror
        self.assertEqual(_count("dependents", "dni", self.OLD), 1)
        self.assertEqual(_count("hsi_patients", "dni", self.OLD), 1)

    def test_old_dni_is_empty_for_the_next_registrant(self):
        self._seed_everything_under_old()
        db_accounts.change_dni(self.OLD, self.NEW)
        # the next person who registers with OLD inherits no clinical row
        for table, column in db_accounts.DNI_MOVED_COLUMNS:
            self.assertEqual(_count(table, column, self.OLD), 0, table)
        with _raw() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM chat_handoffs WHERE web_key LIKE %s",
                        (f"dni:{self.OLD}%",))
            self.assertEqual(cur.fetchone()["n"], 0, "WhatsApp grant stayed on the old DNI")

    def test_identical_content_is_deduplicated_not_duplicated(self):
        _seed_row("users", {"dni": self.OLD})
        for dni in (self.OLD, self.NEW):
            _seed_row("clinical_history_records",
                      {"dni": dni, "source_system": "hsi", "record_hash": "same"})
            _seed_row("lab_report_analysis",
                      {"dni": dni, "report_hash": "rep", "prompt_hash": "p"})
        _seed_row("clinical_history_records",
                  {"dni": self.OLD, "source_system": "hsi", "record_hash": "only-old"})
        self.assertTrue(db_accounts.change_dni(self.OLD, self.NEW))
        self.assertEqual(_count("clinical_history_records", "dni", self.NEW), 2)
        self.assertEqual(_count("lab_report_analysis", "dni", self.NEW), 1)

    def test_two_health_profiles_refuse_instead_of_mixing_people(self):
        _seed_row("users", {"dni": self.OLD})
        _seed_row("patient_health_profile", {"dni": self.OLD})
        _seed_row("patient_health_profile", {"dni": self.NEW})
        _seed_row("booking_requests", {"dni": self.OLD, "requester_dni": self.OLD})
        self.assertFalse(db_accounts.change_dni(self.OLD, self.NEW))
        self.assertEqual(_count("users", "dni", self.OLD), 1, "nothing moved on refusal")
        self.assertEqual(_count("booking_requests", "dni", self.OLD), 1)

    def test_existing_account_still_refuses(self):
        _seed_row("users", {"dni": self.OLD})
        _seed_row("users", {"dni": self.NEW})
        self.assertFalse(db_accounts.change_dni(self.OLD, self.NEW))

    def test_uploaded_photo_follows_the_change(self):
        import tempfile
        from app.cuentas import account_photo
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(account_photo, "PHOTOS_DIR", tmp):
            with open(os.path.join(tmp, f"{self.OLD}.jpg"), "wb") as fh:
                fh.write(b"x")
            self.assertTrue(account_photo.move(self.OLD, self.NEW))
            self.assertTrue(os.path.exists(os.path.join(tmp, f"{self.NEW}.jpg")))
            self.assertFalse(os.path.exists(os.path.join(tmp, f"{self.OLD}.jpg")))


# ---- 2. OneApprovedPerSlot for doctor-less services ----------------------

class TestDoctorlessSlotCapacity(DbCase):
    """SlotHold.tla `OneApprovedPerSlot`: B holds, auto-confirms, releases; A
    read 'no approval yet' before that and auto-confirms the same slot. The
    web paths now re-check capacity atomically, so the second loses."""

    WEB = {"slot_capacity": 1, "resuelta_por": "auto:web"}

    def setUp(self):
        super().setUp()
        _truncate("booking_requests", "appointment_events")

    def test_second_auto_confirmation_of_the_same_slot_conflicts(self):
        when = _future()
        a, b = _request("30000001"), _request("30000002")
        db_booking.approve(b, when, {"estado": "skip"}, **self.WEB)
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(a, when, {"estado": "skip"}, **self.WEB)
        self.assertEqual(db_booking.get_booking_request(a)["estado"], "pendiente",
                         "the losing request stays pending, never lost")

    def test_a_desk_approval_at_that_time_also_takes_the_web_slot(self):
        when = _future()
        a, b = _request("30000001"), _request("30000002")
        db_booking.approve(b, when, {"estado": "skip"}, medico_asignado="Dra. Paz",
                   resuelta_por="mesa:ana")
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(a, when, {"estado": "skip"}, **self.WEB)

    def test_the_desk_may_still_give_several_patients_the_same_time(self):
        """"A las 8, por orden de llegada": no capacity on staff approvals."""
        when = _future()
        for dni in ("30000001", "30000002", "30000003"):
            db_booking.approve(_request(dni), when, {"estado": "skip"}, resuelta_por="mesa:ana")

    def test_overbooking_other_times_and_other_services_do_not_collide(self):
        when = _future()
        db_booking.approve(_request("30000001"), when, {"estado": "skip"}, sobreturno=True)
        db_booking.approve(_request("30000002"), when, {"estado": "skip"}, **self.WEB)
        db_booking.approve(_request("30000003"), when + timedelta(minutes=20), {"estado": "skip"}, **self.WEB)
        db_booking.approve(_request("30000004", servicio_id="rx"), when, {"estado": "skip"}, **self.WEB)

    def test_patient_reschedule_onto_a_taken_slot_conflicts(self):
        when = _future()
        a, b = _request("30000001"), _request("30000002")
        db_booking.approve(a, when, {"estado": "skip"})
        db_booking.approve(b, when + timedelta(hours=1), {"estado": "skip"})
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.reassign_appointment(b, when, slot_capacity=1)
        self.assertEqual(db_booking.get_booking_request(b)["turno_dt"], when + timedelta(hours=1))

    def test_web_create_auto_confirms_with_capacity_one(self):
        from app.modules.turnos import booking_decisions
        from app.modules.turnos import booking_requests
        seen = {}

        def fake_approve(*args, **kwargs):
            seen.update(kwargs)
            raise db_booking.SlotConflict("taken")
        with mock.patch.object(booking_requests.store, "approve", side_effect=fake_approve), \
                mock.patch.object(booking_requests, "_emr_sync_enabled", return_value=False):
            with self.assertRaises(db_booking.SlotConflict):
                booking_decisions._persist_approval(_request(), _future(),
                                                   resuelta_por="auto:web", slot_capacity=1)
        self.assertEqual(seen.get("slot_capacity"), 1)

    def test_waitlist_accept_onto_a_taken_doctor_slot_expires_the_offer(self):
        from app.modules.turnos import appointment_offer
        when = _future()
        a, b = _request("30000001"), _request("30000002")
        db_booking.approve(a, when, {"estado": "skip"}, medico_asignado="Dr. Luis",
                   medico_asignado_id="d1")
        db_booking.approve(b, when + timedelta(hours=2), {"estado": "skip"},
                   medico_asignado="Dr. Luis", medico_asignado_id="d1")
        expired = []
        oferta = {"id": 5, "lista_espera_id": 8, "slot_dt": when}
        with mock.patch.object(appointment_offer.waitlist, "claim", return_value=(True, oferta)), \
                mock.patch.object(appointment_offer.store, "waitlist_row",
                                  return_value={"solicitud_id": b}), \
                mock.patch.object(appointment_offer.store, "expire_offer", side_effect=expired.append):
            with self.assertRaises(appointment_offer.OfferClaimFailed):
                appointment_offer.accept_offer(None, "tok-abc")
        self.assertEqual(expired, [5])
        self.assertEqual(db_booking.get_booking_request(b)["turno_dt"], when + timedelta(hours=2))

    def test_doctorless_waitlist_accept_onto_a_taken_slot_conflicts(self):
        from app.modules.turnos import appointment_offer
        when = _future()
        a, b = _request("30000001"), _request("30000002")
        db_booking.approve(a, when, {"estado": "skip"}, resuelta_por="mesa:ana")
        db_booking.approve(b, when + timedelta(hours=2), {"estado": "skip"})
        expired = []
        oferta = {"id": 6, "lista_espera_id": 9, "slot_dt": when}
        with mock.patch.object(appointment_offer.waitlist, "claim", return_value=(True, oferta)), \
                mock.patch.object(appointment_offer.store, "waitlist_row",
                                  return_value={"solicitud_id": b}), \
                mock.patch.object(appointment_offer.store, "expire_offer", side_effect=expired.append):
            with self.assertRaises(appointment_offer.OfferClaimFailed):
                appointment_offer.accept_offer(None, "tok-def")
        self.assertEqual(expired, [6])
        self.assertEqual(db_booking.get_booking_request(b)["turno_dt"], when + timedelta(hours=2))

    def test_concurrent_auto_confirms_admit_exactly_one(self):
        when = _future()
        sids = [_request(f"3100000{i}") for i in range(6)]
        results = []

        def go(sid):
            try:
                db_booking.approve(sid, when, {"estado": "skip"}, **self.WEB)
                results.append("ok")
            except db_booking.SlotConflict:
                results.append("conflict")
        threads = [threading.Thread(target=go, args=(s,)) for s in sids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1, results)


# ---- 3. AuditReconstructable ----------------------------------------------

def _replay(events):
    """Rebuild (status, appointment_at, attendance) from the events alone."""
    status = at = attendance = None
    for e in events:
        if e["status_from"] is not None or status is not None:
            assert e["status_from"] == status, (e, status)
        if e["kind"] != "creada":
            assert e["appointment_from"] == at, (e, at)
        status, at = e["status_to"], e["appointment_to"]
        if e["kind"] == "asistencia":
            assert e["attendance_from"] == attendance, (e, attendance)
            attendance = e["attendance_to"]
    return status, at, attendance


class TestAppointmentEvents(DbCase):
    """AppointmentHsi.tla `AuditReconstructable`."""

    def setUp(self):
        super().setUp()
        _truncate("booking_requests", "appointment_events", "attendance_events",
                  "day_queue_events")

    def test_full_lifecycle_is_reconstructable_with_actors(self):
        t1 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        t0 = _future(days=5)
        sid = _request(actor="paciente")
        db_booking.approve(sid, t0, {"estado": "skip"}, resuelta_por="mesa:ana")
        db_booking.reassign_appointment(sid, t1, actor="paciente:lista_espera")
        db_booking.reassign_appointment(sid, t1, {"estado": "ok"}, actor="hsi:journal")  # HSI ack, same time
        db_booking.mark_attendance(sid, "presente", "medico:luis")
        db_booking.mark_attendance(sid, "ausente", "mesa:ana")
        events = db_booking.appointment_events(sid)
        self.assertEqual([e["kind"] for e in events],
                         ["creada", "aprobada", "reasignada", "asistencia", "asistencia"],
                         "a same-time HSI acknowledgement is not a change")
        self.assertEqual([e["actor"] for e in events],
                         ["paciente", "mesa:ana", "paciente:lista_espera",
                          "medico:luis", "mesa:ana"])
        row = db_booking.get_booking_request(sid)
        self.assertEqual(_replay(events), (row["estado"] if "estado" in row else row["status"],
                                           row["turno_dt"], row["asistencia"]))
        self.assertEqual(events[2]["appointment_from"], t0)
        self.assertEqual(events[2]["appointment_to"], t1)

    def test_reject_and_cancel_record_status_transitions(self):
        a, b = _request("30000001"), _request("30000002")
        db_booking.reject(a, "orden ilegible", resuelta_por="mesa:ana")
        db_booking.approve(b, _future(), {"estado": "skip"}, resuelta_por="auto:web")
        db_booking.cancel(b, actor="paciente")
        self.assertEqual([(e["kind"], e["status_from"], e["status_to"], e["actor"])
                          for e in db_booking.appointment_events(a)],
                         [("creada", None, "pendiente", None),
                          ("rechazada", "pendiente", "rechazada", "mesa:ana")])
        self.assertEqual([(e["kind"], e["status_to"], e["actor"])
                          for e in db_booking.appointment_events(b)][-1],
                         ("cancelada", "cancelada", "paciente"))

    def test_a_write_that_did_not_happen_leaves_no_event(self):
        sid = _request()
        db_booking.reject(sid, "x", resuelta_por="mesa:ana")
        self.assertIsNone(db_booking.cancel(sid, actor="paciente"))          # terminal: no-op
        with self.assertRaises(db_booking.AlreadyProcessed):
            db_booking.approve(sid, _future(), {"estado": "skip"})
        self.assertEqual([e["kind"] for e in db_booking.appointment_events(sid)],
                         ["creada", "rechazada"])

    def test_a_rolled_back_approval_leaves_no_event(self):
        when = _future()
        a, b = _request("30000001"), _request("30000002")
        db_booking.approve(a, when, {"estado": "skip"})
        with self.assertRaises(db_booking.SlotConflict):
            db_booking.approve(b, when, {"estado": "skip"}, slot_capacity=1)
        self.assertEqual([e["kind"] for e in db_booking.appointment_events(b)], ["creada"])

    def test_limit_create_records_its_creation_once(self):
        fila = db_booking.create_booking_request_with_limit(
            "30000009", "Ana", "Paz", "1980-01-01", "a@x.com", "lab", "c", 3,
            idem_key="k-1", actor="paciente")
        again = db_booking.create_booking_request_with_limit(
            "30000009", "Ana", "Paz", "1980-01-01", "a@x.com", "lab", "c", 3,
            idem_key="k-1", actor="paciente")
        self.assertEqual(fila["id"], again["id"])
        self.assertEqual([e["kind"] for e in db_booking.appointment_events(fila["id"])], ["creada"])

    def test_patient_cancel_route_names_the_patient(self):
        from app.modules.turnos import booking_patient
        from app.modules.turnos import booking_requests
        sid = _request("30000001", solicitante_dni="27000111")
        with mock.patch.object(booking_requests, "_emr_sync_enabled", return_value=False):
            status, _ = booking_patient.cancel_request("27000111", sid)
        self.assertEqual(status, 200)
        self.assertEqual(db_booking.appointment_events(sid)[-1]["actor"], "solicitante")


class TestEveryStatusWriterIsAudited(unittest.TestCase):
    """The static half of AuditReconstructable: a FUTURE function that writes
    booking_requests.status or .appointment_at without an event can't land.
    Hermetic: reads source only."""

    _WRITE = __import__("re").compile(
        r"UPDATE\s+booking_requests\s+(?:\w+\s+)?SET\s(?:(?!\bWHERE\b)[^;])*?(?<![\w.])(status|appointment_at)\s*=",
        __import__("re").S | __import__("re").I)

    @staticmethod
    def _core_writes(source):
        """Names of the functions in `source` that build a Core `update(<booking_requests>)` whose
        `.values(...)` sets `status` or `appointment_at` (the Core spelling of the hand-written
        `UPDATE booking_requests SET status=...` that `_WRITE` finds)."""
        import ast
        tables = {"bk", "booking_requests", "requests"}

        def is_booking_table(node):
            return (isinstance(node, ast.Name) and node.id in tables) or \
                   (isinstance(node, ast.Attribute) and node.attr == "booking_requests")

        def chain(call):
            """The calls of a method chain `update(t).where(...).values(...)`, outermost first."""
            while isinstance(call, ast.Call):
                yield call
                call = call.func.value if isinstance(call.func, ast.Attribute) else None

        def writes(node):
            for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                calls = list(chain(call))
                updates_bookings = any(
                    (isinstance(c.func, ast.Attribute) and c.func.attr == "update"
                     or isinstance(c.func, ast.Name) and c.func.id == "update")
                    and c.args and is_booking_table(c.args[0]) for c in calls)
                sets = any(isinstance(c.func, ast.Attribute) and c.func.attr == "values"
                           and any(k.arg in ("status", "appointment_at") for k in c.keywords)
                           for c in calls)
                if updates_bookings and sets:
                    return True
            return False

        found = []
        tree = ast.parse(source)
        # statements built once at import (`_APPROVE = prepare(update(bk).values(status=...))`)
        # are written by the functions that name them
        built_once = {t.id for node in tree.body if isinstance(node, ast.Assign) and writes(node.value)
                      for t in node.targets if isinstance(t, ast.Name)}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if writes(node) or names & built_once:
                found.append(node.name)
        return found

    def test_db_functions_that_write_status_or_time_log_an_event(self):
        import ast
        writers = []
        for module in ("db.py", "db_booking.py"):
            with open(os.path.join(ROOT, "app", "core", module), encoding="utf-8") as fh:
                src = fh.read()
            sql_writers = set(self._core_writes(src))
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.FunctionDef):
                    body = ast.get_source_segment(src, node) or ""
                    if self._WRITE.search(body) or node.name in sql_writers:
                        writers.append(node.name)
                        self.assertIn("_log_appointment_event(", body,
                                      f"db.{node.name} changes status/time without an appointment_events row")
        self.assertEqual(sorted(writers),
                         ["approve", "cancel", "reassign_appointment", "reject"])

    def test_no_other_module_writes_booking_requests_status_directly(self):
        offenders = []
        for top in ("modules", "admin_panel", "connectors", "cuentas", "patient_web", "core"):
            for dirpath, _dirs, files in os.walk(os.path.join(ROOT, top)):
                for f in files:
                    path = os.path.join(dirpath, f)
                    if not f.endswith(".py") or path.endswith(
                            (os.path.join("core", "db.py"), os.path.join("core", "db_booking.py"))):
                        continue
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                    if self._WRITE.search(text) or self._core_writes(text):
                        offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [])


# ---- 4. OneInterviewPerRequest --------------------------------------------

def _bound(request_id):
    with _raw() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM interviews WHERE request_id=%s", (request_id,))
        return cur.fetchall()


class TestOneInterviewPerRequest(DbCase):
    """IntakeTriage.tla `OneInterviewPerRequest` (+ `DoctorSeesOwner`)."""

    def setUp(self):
        super().setUp()
        _truncate("booking_requests", "interviews")
        for rid in (7, 9):   # interviews.request_id is a foreign key (Alembic 0032)
            _seed_row("booking_requests", {"id": rid, "dni": "1"})

    def test_patient_open_is_idempotent(self):
        a = db_clinical.interview_bind(7, entry_mode="post_booking", dni="1")
        b = db_clinical.interview_bind(7, entry_mode="post_booking", dni="1")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(_bound(7)), 1)

    def test_anonymous_qr_never_receives_the_patients_session(self):
        own = db_clinical.interview_bind(7, entry_mode="post_booking", dni="1")
        qr = db_clinical.interview_create(doctor_id="d", entry_mode="qr", request_id=7,
                                 dni="1", matched=True)
        self.assertNotEqual(qr["id"], own["id"])
        self.assertNotEqual(qr["patient_token"], own["patient_token"])
        self.assertIsNone(qr["request_id"])
        self.assertFalse(qr["matched"])
        self.assertEqual([r["id"] for r in _bound(7)], [own["id"]])

    def test_second_qr_at_the_door_starts_unbound(self):
        first = db_clinical.interview_create(doctor_id="d", entry_mode="qr", request_id=7, dni="1",
                                    matched=True)
        second = db_clinical.interview_create(doctor_id="d", entry_mode="qr", request_id=7, dni="1",
                                     matched=True)
        self.assertEqual(first["request_id"], 7)
        self.assertIsNone(second["request_id"])
        self.assertNotEqual(first["patient_token"], second["patient_token"])

    def test_patient_takes_the_binding_from_a_door_interview_without_deleting_it(self):
        qr = db_clinical.interview_create(doctor_id="d", entry_mode="qr", request_id=7, dni="1",
                                 matched=True)
        own = db_clinical.interview_bind(7, entry_mode="post_booking", dni="1")
        self.assertEqual([r["id"] for r in _bound(7)], [own["id"]])
        kept = db_clinical.interview_get(qr["id"])
        self.assertIsNotNone(kept, "the door interview is kept")
        self.assertIsNone(kept["request_id"])
        self.assertEqual(kept["detached_request_id"], 7)
        self.assertEqual(db_clinical.interview_for_request(7)["id"], own["id"])

    def test_logged_in_door_scan_continues_the_patients_own_interview(self):
        own = db_clinical.interview_bind(7, entry_mode="post_booking", dni="1")
        scan = db_clinical.interview_bind(7, entry_mode="qr", dni="1", authenticated=True)
        self.assertEqual(scan["id"], own["id"])

    def test_find_or_create_never_hands_the_patient_a_door_interview(self):
        from app.modules.intake import interview_patient
        qr = db_clinical.interview_create(doctor_id="d", entry_mode="qr", request_id=7, dni="1",
                                 matched=True)
        got = interview_patient._find_or_create_interview({"dni": "1"}, 7)
        self.assertNotEqual(got["id"], qr["id"])
        self.assertEqual(got["entry_mode"], "post_booking")

    def test_concurrent_opens_bind_exactly_one(self):
        out = []

        def go(mode):
            out.append(db_clinical.interview_bind(9, entry_mode=mode, dni="1", matched=True)["id"])
        threads = [threading.Thread(target=go, args=("post_booking" if i % 2 else "qr",))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(_bound(9)), 1)

    def test_the_index_itself_refuses_a_second_bound_row(self):
        import psycopg
        db_clinical.interview_bind(7, entry_mode="post_booking", dni="1")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with _raw() as c, c.cursor() as cur:
                cur.execute("INSERT INTO interviews (request_id, entry_mode, session_key) "
                            "VALUES (7, 'qr', 'raw-dup')")


# ---- the 0015 backfills on pre-existing data ------------------------------

def _revision_sql():
    path = os.path.join(ROOT, "alembic", "versions", "0015_formal_invariants.py")
    spec = importlib.util.spec_from_file_location("rev0015", path)
    mod = importlib.util.module_from_spec(spec)
    captured = []

    class _Bind:
        def exec_driver_sql(self, sql):
            captured.append(sql)

    class _Op:
        def get_bind(self):
            return _Bind()
    with mock.patch.dict(sys.modules, {"alembic": mock.Mock(op=_Op())}):
        spec.loader.exec_module(mod)
        mod.upgrade()
    return captured[0]


class TestRevision0015Backfills(DbCase):
    """An existing install may already hold several interviews bound to one
    request: the revision resolves them without deleting a row."""

    def setUp(self):
        super().setUp()
        _truncate("booking_requests", "interviews", "appointment_events")
        _seed_row("booking_requests", {"id": 5, "dni": "1"})   # interviews.request_id is a foreign key
        with _raw() as c, c.cursor() as cur:
            cur.execute("DROP INDEX IF EXISTS interviews_request_uniq")

    def tearDown(self):
        with _raw() as c, c.cursor() as cur:
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")
            cur.execute(_revision_sql())

    def test_duplicates_are_resolved_and_nothing_is_deleted(self):
        with _raw() as c, c.cursor() as cur:
            for mode in ("post_booking", "qr", "post_booking", "qr"):
                cur.execute("INSERT INTO interviews (request_id, entry_mode, session_key) "
                            "VALUES (5, %s, %s)", (mode, os.urandom(6).hex()))
        sql = _revision_sql()
        with _raw() as c, c.cursor() as cur:
            cur.execute(sql)
            cur.execute(sql)  # idempotent re-run
            cur.execute("SELECT id, entry_mode, request_id, detached_request_id "
                        "FROM interviews ORDER BY id")
            ivs = cur.fetchall()
        self.assertEqual(len(ivs), 4, "no interview deleted")
        bound = [r for r in ivs if r["request_id"] == 5]
        self.assertEqual([(r["id"], r["entry_mode"]) for r in bound], [(3, "post_booking")],
                         "winner = what interview_for_request already showed")
        self.assertTrue(all(r["detached_request_id"] == 5 for r in ivs if r["id"] != 3))


if __name__ == "__main__":
    unittest.main()
