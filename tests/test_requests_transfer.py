"""Live patient requests from one installation to another (scripts/fixes/requests_transfer.py).

pinamar.humanrounds.org → salud.pinamar.gob.ar (Salud, 2026-10-26: «Copiarlos a
salud.pinamar.gob.ar»): a handful of real appointments were given on the test
installation before the municipal one opened. The export carries each request
as the desk sees it — patient, service, center, doctor, time, state — and none
of the side material (order file, AI reading, HSI ids, confirmation link); the
import, run by the municipality, recreates it with its history. Proves: the
file holds only the allowlisted fields, a past or closed request does not
travel, the import is a dry run unless told otherwise, it never guesses a
service, center or doctor the target lacks or has switched off, the dry run
already shows a doctor's slot taken (on the target or inside the file, by the
rule of `booking_requests_doctor_slot_uniq`) and a request the target already
has, a database error on one request prints only its id and error class and
leaves nothing half-written, a damaged file is refused without quoting it, the
import refuses to run on the installation the file came from, a second run
changes nothing, and the patient sees the appointment in their account.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_requests_transfer.py
"""

import contextlib
import datetime
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
FIXES = os.path.join(ROOT, "scripts", "fixes")

from app.core import config  # noqa: E402
config.ENV_PATH = ""

import psycopg  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from app.core import db, sqlcore  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_values  # noqa: E402
from app.core import schema_model as sm  # noqa: E402
from app.patient_web import catalog  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_requests_transfer")
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


SOURCE, TARGET = "prueba.example", "destino.example"
_CATALOG = {"servicios": {"ginecologia", "cardiologia-hospital"},
            "centros": {"caps-valeria", "hospital-pinamar"},
            "medicos": {"ariel-zarate", "sebastian-frias"}}
# What the files of these tests hold about people: none of it may reach a
# printed line, a log record or stderr.
_PII = ("Marta", "Ríos", "30111222", "2254 111222", "1970-05-02", "Hugo", "30333444", "hugo@", "Otro", "30999000")


def _as(host):
    """Run as the installation named `host` (its TURNOS_PUBLIC_ORIGIN)."""
    return _env.patched_env(TURNOS_PUBLIC_ORIGIN="https://" + host)


def _script():
    if FIXES not in sys.path:
        sys.path.insert(0, FIXES)
    import requests_transfer
    return requests_transfer


def _wipe():
    with db._connect() as conn, conn.cursor() as cur:
        _dbtest.delete_booking_requests(cur)


def _future(days, hour=8, minute=40):
    tz = datetime.timezone(datetime.timedelta(hours=-3))
    d = datetime.datetime.now(tz) + datetime.timedelta(days=days)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _row(dni="30999000", service="ginecologia", status="aprobada", doctor="ariel-zarate", when=None,
         overbooking=False, created=None):
    """A request as it could already sit on the target (inserted directly)."""
    values = {"dni": dni, "first_name": "Otro", "last_name": "Vecino", "birth_date": datetime.date(1975, 1, 1),
              "contact": "2254 999000", "service_id": service, "center_slug": "caps-valeria", "status": status,
              "appointment_at": when, "assigned_doctor_id": doctor, "overbooking": overbooking}
    if created is not None:
        values["created"] = created
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.insert(sm.booking_requests).values(**values)
                           .returning(sm.booking_requests.c.id)).fetchone()["id"]


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de test")
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        _wipe()
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.file = os.path.join(self.tmp, "solicitudes.json")
        self.script = _script()
        self.when = _future(20)

    def source(self):
        """The test installation: an approved future appointment, a pending
        request, a cancelled one and a past one. Returns their ids."""
        ok = db_booking.create_booking_request("30111222", "Marta", "Ríos", "1970-05-02", "2254 111222",
                                       "ginecologia", "caps-valeria", sexo="F", slot_medico_id="ariel-zarate")
        db_booking.set_referral(ok, "/data/ordenes/x.jpg", "image/jpeg")
        db_booking.save_ai(ok, {"lectura": "orden"})
        db_booking.approve(ok, self.when, hsi_result={"estado": "ok", "patient_id": "77", "appointment_id": "88"},
                   medico_asignado="Ariel Zarate", medico_asignado_id="ariel-zarate", resuelta_por="ana.mesa")
        db_booking.booking_request_confirm_token(ok)
        pend = db_booking.create_booking_request("30333444", "Hugo", "Paz", "1958-01-09", "hugo@example.invalid",
                                         "cardiologia-hospital", "hospital-pinamar", preferencia_horario="mañana")
        db_booking.assign_pending_booking_request(pend, "Sebastian Frias", "sebastian-frias")
        gone = db_booking.create_booking_request("30555666", "Rita", "Gil", "1980-03-03", "2254 555666",
                                         "ginecologia", "caps-valeria")
        db_booking.cancel(gone, actor="paciente")
        past = db_booking.create_booking_request("30777888", "Juan", "Sol", "1990-07-07", "2254 777888",
                                         "ginecologia", "caps-valeria")
        db_booking.approve(past, _future(-3), medico_asignado="Ariel Zarate", medico_asignado_id="ariel-zarate",
                   resuelta_por="ana.mesa")
        return ok, pend, gone, past

    def export(self, ids, origin=None):
        lines = []
        with _as(SOURCE):
            code = self.script.export(self.file, ids, origin=origin, emit=lines.append)
        return code, lines

    def import_(self, apply=False, catalog=None, live=None, host=TARGET):
        lines = []
        known = catalog or _CATALOG
        live = live or known
        with _as(host), patch.object(self.script, "_known", lambda tipo: set(known[tipo])), \
                patch.object(self.script, "_live_ids", lambda tipo: set(live[tipo])):
            code = self.script.import_file(self.file, apply=apply, emit=lines.append)
        return code, lines

    def exported_then_target(self):
        ok, pend, gone, past = self.source()
        code, _ = self.export([ok, pend, gone, past])
        self.assertEqual(code, 0)
        _wipe()
        return ok, pend

    def entry(self, origen_id, **over):
        """A hand-made file entry (one request as the export writes it)."""
        e = {f: None for f in self.script.FIELDS}
        e.update(dni="31%06d" % origen_id, first_name="Marta", last_name="Ríos", birth_date="1970-05-02",
                 contact="2254 111222", service_id="ginecologia", center_slug="caps-valeria", status="aprobada",
                 appointment_at=self.when.isoformat(), assigned_doctor_id="ariel-zarate",
                 assigned_doctor="Ariel Zarate", resolved_by="ana.mesa", created=_future(-5).isoformat(),
                 overbooking=False, origen_id=origen_id)
        e.update(over)
        return e

    def write(self, entries, origin=SOURCE):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"formato": self.script.FORMAT, "version": self.script.VERSION, "origen": origin,
                       "exportado": "2026-10-27T00:00:00+00:00", "solicitudes": entries}, f, ensure_ascii=False)

    def run_main(self, *argv, host=TARGET, live=None, live_fn=None):
        """`main()` as the operator runs it: returns (code, stdout, stderr, log text).
        Nothing may escape as a traceback — an exception here fails the test."""
        out, err, logged = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logged)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        root = logging.getLogger()
        before = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            with _as(host), patch.object(self.script, "_known", lambda tipo: set(_CATALOG[tipo])), \
                    patch.object(self.script, "_live_ids", live_fn or (lambda tipo: set((live or _CATALOG)[tipo]))), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.script.main(list(argv))
        finally:
            root.removeHandler(handler)
            root.setLevel(before)
        return code, out.getvalue(), err.getvalue(), logged.getvalue()

    def assertNoPii(self, *texts):
        for text in texts:
            for pii in _PII:
                self.assertNotIn(pii, text)

    def count(self):
        return len(db_booking.list_admin())


class TestExport(_Base):
    def test_file_carries_the_request_never_its_side_material(self):
        ok, pend, gone, past = self.source()
        code, lines = self.export([ok, pend, gone, past])
        self.assertEqual(code, 0)
        with open(self.file, encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
        self.assertEqual(data["origen"], SOURCE)
        rows = {r["origen_id"]: r for r in data["solicitudes"]}
        # Cancelled and past ones stay home.
        self.assertEqual(set(rows), {ok, pend})
        self.assertEqual(rows[ok]["status"], "aprobada")
        self.assertEqual(rows[ok]["assigned_doctor_id"], "ariel-zarate")
        self.assertEqual(rows[ok]["resolved_by"], "ana.mesa")
        self.assertEqual(rows[pend]["status"], "pendiente")
        self.assertEqual(rows[pend]["assigned_doctor_id"], "sebastian-frias")
        for r in data["solicitudes"]:
            self.assertEqual(set(r), set(self.script.FIELDS) | {"origen_id"})
        for leak in ("ordenes", "lectura", "image/jpeg", '"77"', '"88"', "confirm_token", "ai_json", "hsi"):
            self.assertNotIn(leak, raw)
        # The report names ids and counts, never a patient.
        text = "\n".join(lines)
        for pii in ("Marta", "Ríos", "30111222", "2254", "hugo@"):
            self.assertNotIn(pii, text)
        self.assertIn("no_vigente", text)

    def test_refuses_to_overwrite_an_existing_file(self):
        ok, *_ = self.source()
        self.assertEqual(self.export([ok])[0], 0)
        self.assertEqual(self.export([ok])[0], 2)

    def test_the_origin_is_the_installation_itself_and_a_wrong_name_is_refused(self):
        ok, *_ = self.source()
        code, lines = self.export([ok], origin="https://%s/" % SOURCE.upper())   # same host, written another way
        self.assertEqual(code, 0, lines)
        os.remove(self.file)
        code, lines = self.export([ok], origin="otro.example")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.file))
        self.assertIn("--origin", "\n".join(lines))
        # An installation that does not know its own name cannot name a source.
        with _env.patched_env(TURNOS_PUBLIC_ORIGIN=None):
            lines = []
            self.assertEqual(self.script.export(self.file, [ok], emit=lines.append), 2)
        self.assertFalse(os.path.exists(self.file))


class TestImport(_Base):
    def test_dry_run_writes_nothing(self):
        self.exported_then_target()
        code, lines = self.import_()
        self.assertEqual(code, 0)
        self.assertIn('"modo": "simulacion"', lines[-1])
        self.assertEqual(db_booking.list_admin(), [])

    def test_apply_recreates_with_history_and_twice_changes_nothing(self):
        ok, pend = self.exported_then_target()
        code, lines = self.import_(apply=True)
        self.assertEqual(code, 0, lines)
        self.assertEqual(json.loads(lines[-1])["creadas"], 2)
        mine = {r["servicio_id"]: r for r in db_booking.list_by_dni("30111222")}
        appt = mine["ginecologia"]
        self.assertEqual(appt["estado"], "aprobada")
        self.assertEqual(db_values._coerce_dt(appt["turno_dt"]), self.when)
        self.assertEqual(appt["medico_asignado_id"], "ariel-zarate")
        self.assertEqual(appt["centro_slug"], "caps-valeria")
        self.assertIsNone(appt.get("orden_path"))
        self.assertIsNone(appt.get("hsi_appointment_id"))
        kinds = [e["kind"] for e in db_booking.appointment_events(appt["id"])]
        self.assertEqual(kinds, ["creada", "aprobada"])
        waiting = db_booking.list_by_dni("30333444")
        self.assertEqual([(r["estado"], r["medico_asignado_id"]) for r in waiting], [("pendiente", "sebastian-frias")])
        text = "\n".join(lines)
        for pii in ("Marta", "30111222", "hugo@"):
            self.assertNotIn(pii, text)
        _, again = self.import_(apply=True)
        report = json.loads(again[-1])
        self.assertEqual((report["creadas"], report["ya_existian"]), (0, 2))
        self.assertEqual(len(db_booking.list_admin()), 2)

    def test_never_guesses_a_service_center_or_doctor_the_target_lacks(self):
        self.exported_then_target()
        lacking = dict(_CATALOG, medicos={"sebastian-frias"})
        code, lines = self.import_(apply=True, catalog=lacking)
        self.assertEqual(code, 1)
        self.assertIn("medico_no_esta_en_el_catalogo", "\n".join(lines))
        self.assertEqual([r["estado"] for r in db_booking.list_admin()], ["pendiente"])


class TestSlotInThePlan(_Base):
    """(a) The dry run sees a doctor's slot already taken, by the rule of the
    unique index: approved, not an overbooking, same assigned doctor and time."""

    def test_a_slot_taken_on_the_target_is_shown_by_the_dry_run_and_nothing_is_half_written(self):
        self.exported_then_target()
        _row(when=self.when)
        code, lines = self.import_()
        self.assertEqual(code, 1)
        self.assertIn("CONFLICTO #", "\n".join(lines))
        self.assertIn("horario_ocupado", "\n".join(lines))
        self.assertEqual(json.loads(lines[-1])["conflictos"], 1)
        self.assertEqual(self.count(), 1)                        # the dry run wrote nothing
        code, lines = self.import_(apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(db_booking.list_by_dni("30111222"), [])
        report = json.loads(lines[-1])
        self.assertEqual((report["creadas"], report["conflictos"]), (1, 1))

    def test_what_the_index_does_not_cover_does_not_take_the_slot(self):
        self.exported_then_target()
        _row(dni="30999001", when=self.when, overbooking=True)                 # an overbooking is exempt
        _row(dni="30999002", when=self.when, status="cancelada")               # not approved
        _row(dni="30999003", when=self.when, doctor="sebastian-frias")           # another doctor
        _row(dni="30999004", when=self.when + datetime.timedelta(minutes=20))  # another time
        code, lines = self.import_()
        self.assertEqual(code, 0, lines)
        self.assertEqual(json.loads(lines[-1])["creadas"], 2)

    def test_the_same_instant_in_another_offset_is_the_same_slot(self):
        self.exported_then_target()
        _row(when=self.when.astimezone(datetime.timezone.utc))
        self.assertIn("horario_ocupado", "\n".join(self.import_()[1]))

    def test_two_requests_of_the_file_for_one_slot_keep_the_first(self):
        self.write([self.entry(1), self.entry(2)])
        code, lines = self.import_()
        text = "\n".join(lines)
        self.assertEqual(code, 1)
        self.assertIn("crear     #1", text)
        self.assertIn("CONFLICTO #2 — horario_repetido_en_el_archivo", text)
        code, lines = self.import_(apply=True)
        self.assertEqual(code, 1)
        self.assertEqual([r["dni"] for r in db_booking.list_admin()], ["31000001"])

    def test_an_overbooking_or_another_doctor_of_the_file_shares_the_time(self):
        self.write([self.entry(1), self.entry(2, overbooking=True), self.entry(3, assigned_doctor_id="sebastian-frias")])
        code, lines = self.import_(apply=True)
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.count(), 3)

    def test_an_empty_doctor_id_takes_the_slot_as_the_index_reads_it(self):
        self.write([self.entry(1, assigned_doctor_id="", slot_doctor_id="ariel-zarate"),
                    self.entry(2, assigned_doctor_id="", slot_doctor_id="ariel-zarate")])
        code, lines = self.import_(apply=True)
        self.assertIn("CONFLICTO #2 — horario_repetido_en_el_archivo", "\n".join(lines))
        self.assertEqual(self.count(), 1)
        _row(dni="30999005", doctor="", when=self.when + datetime.timedelta(days=1))
        self.write([self.entry(3, assigned_doctor_id="", slot_doctor_id="ariel-zarate",
                               appointment_at=(self.when + datetime.timedelta(days=1)).isoformat())])
        self.assertIn("horario_ocupado", "\n".join(self.import_()[1]))

    def test_the_plan_is_pure_and_checks_only_what_the_index_covers(self):
        rows = [self.entry(1), self.entry(2, status="pendiente", appointment_at=None, created=None, dni="a")]
        rows = [dict(r, appointment_at=db_values._coerce_dt(r["appointment_at"]), created=db_values._coerce_dt(r["created"]))
                for r in rows]
        taken = {"slots": {("ariel-zarate", rows[0]["appointment_at"])}, "requests": set()}
        known = lambda tipo: set(_CATALOG[tipo])   # noqa: E731
        out = self.script.plan(SOURCE, rows, set(), known, known, taken, datetime.datetime.now(datetime.timezone.utc))
        self.assertEqual([(a, why) for a, _, why in out], [("conflict", "horario_ocupado"), ("create", "")])


class TestApplyErrors(_Base):
    """(b) A database error on one request prints its id and the error class,
    never the driver's message (which quotes the failing row)."""

    def bad_then_good(self):
        # `confirmation` has a CHECK ('si', 'no'): the database refuses the row and
        # its message quotes the whole row, DNI, name and contact included.
        self.write([self.entry(1, status="pendiente", appointment_at=None, confirmation="quizas",
                               dni="30111222", contact="2254 111222"),
                    self.entry(2, status="pendiente", appointment_at=None, dni="30333444", first_name="Hugo",
                               contact="hugo@example.invalid", created=_future(-4).isoformat())])

    def test_the_premise_the_driver_message_quotes_the_row(self):
        self.bad_then_good()
        _, rows = self.script._read(self.file)
        with self.assertRaises(psycopg.errors.CheckViolation) as ctx:
            self.script._create(SOURCE, rows[0])
        self.assertIn("30111222", str(ctx.exception))

    def test_a_row_error_prints_id_and_class_only_and_the_run_goes_on(self):
        self.bad_then_good()
        code, out, err, logged = self.run_main("import", self.file, "--apply")
        self.assertEqual(code, 3)
        self.assertIn("ERROR     #1 — CheckViolation 23514 booking_requests_confirmation_check", out)
        self.assertIn("creada    #2", out)
        report = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((report["creadas"], report["errores"], report["sin_intentar"]), (1, 1, 0))
        self.assertEqual([r["dni"] for r in db_booking.list_admin()], ["30333444"])        # the bad one left nothing
        self.assertNoPii(out, err, logged)
        self.assertNotIn("Failing row", out + err + logged)
        self.assertNotIn("Traceback", out + err + logged)

    def test_any_other_database_failure_stops_the_run_and_says_so(self):
        for failure in (psycopg.OperationalError("connection to server at \"10.1.2.3\" failed: Marta Ríos 30111222"),
                        db.DbUnavailable("no se pudo conectar a la base: 30111222 Marta")):
            with self.subTest(type(failure).__name__):
                _wipe()
                self.write([self.entry(1, status="pendiente", appointment_at=None),
                            self.entry(2, status="pendiente", appointment_at=None, created=_future(-4).isoformat())])
                with patch.object(self.script, "_create", side_effect=failure) as create:
                    code, out, err, logged = self.run_main("import", self.file, "--apply")
                self.assertEqual(create.call_count, 1)
                self.assertEqual(code, 3)
                self.assertIn("sin intentar #2", out)
                report = json.loads(out.strip().splitlines()[-1])
                self.assertEqual((report["creadas"], report["errores"], report["sin_intentar"]), (0, 1, 1))
                self.assertNoPii(out, err, logged)
                self.assertNotIn("10.1.2.3", out + err + logged)

    def test_a_failure_in_the_history_rolls_the_request_back_and_a_second_run_finishes(self):
        self.write([self.entry(1), self.entry(2, assigned_doctor_id="sebastian-frias", dni="30333444")])
        real = db_booking._log_appointment_event

        def fails_on_approval(cur, sid, kind, *args):
            if kind == "aprobada":
                raise psycopg.errors.CheckViolation("Failing row contains (30111222, Marta, Ríos)")
            return real(cur, sid, kind, *args)

        with patch.object(db_booking, "_log_appointment_event", fails_on_approval):
            code, out, err, logged = self.run_main("import", self.file, "--apply")
        self.assertEqual(code, 3)
        self.assertEqual(self.count(), 0)                # the request rolled back with its «creada» event
        with db._connect() as conn, conn.cursor() as cur:
            events = sqlcore.run(cur, sa.text("SELECT count(*) AS n FROM appointment_events")).fetchone()["n"]
        self.assertEqual(events, 0)
        self.assertNoPii(out, err, logged)
        code, out, err, logged = self.run_main("import", self.file, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.count(), 2)
        code, out, *_ = self.run_main("import", self.file, "--apply")
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["ya_existian"], 2)


class TestDamagedFile(_Base):
    """(c) A file that cannot be read is refused by request id and field; the
    value that did not parse (a birth date is personal data) is never printed."""

    def test_an_unreadable_birth_date_is_not_quoted(self):
        self.write([self.entry(7, birth_date="1970-13-45")])
        code, out, err, logged = self.run_main("import", self.file, "--apply")
        self.assertEqual(code, 2)
        self.assertIn("#7", out)
        self.assertIn("birth_date", out)
        self.assertNotIn("1970-13-45", out + err + logged)
        self.assertNoPii(out, err, logged)
        self.assertEqual(self.count(), 0)

    def test_a_time_that_does_not_parse_or_has_no_zone_is_not_quoted_either(self):
        for field, value in (("appointment_at", "mañana a las 8"), ("created", "2026-10-30T08:40:00")):
            with self.subTest(field):
                self.write([self.entry(8, **{field: value})])
                code, out, err, logged = self.run_main("import", self.file)
                self.assertEqual(code, 2)
                self.assertIn(field, out)
                self.assertNotIn(value, out + err + logged)

    def test_other_damage_names_no_content(self):
        self.write([self.entry(1)])
        for label, text in (("not json", '{"dni": "30111222" Marta Ríos'),
                            ("a list", '[{"dni": "30111222"}]'),
                            ("bad id", json.dumps({"formato": self.script.FORMAT, "version": 1, "origen": SOURCE,
                                                   "solicitudes": [dict(self.entry(1), origen_id="30111222-Marta")]}))):
            with self.subTest(label):
                with open(self.file, "w", encoding="utf-8") as f:
                    f.write(text)
                code, out, err, logged = self.run_main("import", self.file)
                self.assertEqual(code, 2)
                self.assertNoPii(out, err, logged)
                self.assertNotIn("Traceback", out + err + logged)
        code, out, *_ = self.run_main("import", os.path.join(self.tmp, "no-existe.json"))
        self.assertEqual(code, 2)
        self.assertIn("FileNotFoundError", out)


    def test_a_status_the_file_invents_is_not_echoed(self):
        self.write([self.entry(9, status="Marta Ríos 30111222")])
        code, out, err, logged = self.run_main("import", self.file)
        self.assertEqual(code, 1)
        self.assertIn("estado_no_vigente", out)
        self.assertNoPii(out, err, logged)


class TestPlanningFailures(_Base):
    """A database or catalog failure while the plan is made — before anything is
    written — prints its class only, whatever message the driver put in it."""

    def test_every_read_of_the_plan_is_covered(self):
        self.write([self.entry(1)])
        driver_text = "connection to server failed: Marta Ríos 30111222 hugo@example.invalid"
        for name, failure in (("_is_source", psycopg.OperationalError(driver_text)),
                              ("_existing_keys", psycopg.errors.UndefinedTable(driver_text)),
                              ("_target_state", db.DbUnavailable(driver_text)),
                              ("_live_ids", RuntimeError(driver_text))):
            with self.subTest(name), contextlib.ExitStack() as stack:
                live_fn = None
                if name == "_live_ids":        # `run_main` fixes the catalog itself: hand it the failing one
                    def live_fn(tipo, failure=failure):
                        raise failure
                else:
                    stack.enter_context(patch.object(self.script, name, side_effect=failure))
                code, out, err, logged = self.run_main("import", self.file, "--apply", live_fn=live_fn)
                self.assertEqual(code, 3)
                self.assertIn(type(failure).__name__, out)
                self.assertNoPii(out, err, logged)
                self.assertEqual(self.count(), 0)


class TestLiveCatalog(_Base):
    """(d) Only what gives appointments today may receive a request: a service,
    center or doctor that is switched off or deleted is a plan error, told apart
    from one the catalog never had."""

    def test_a_switched_off_service_center_or_doctor_is_a_plan_error_told_from_an_unknown_one(self):
        self.exported_then_target()
        off = {"servicios": {"cardiologia-hospital"}, "centros": {"caps-valeria"}, "medicos": {"ariel-zarate"}}
        known = {"servicios": _CATALOG["servicios"], "centros": _CATALOG["centros"],
                 "medicos": _CATALOG["medicos"] | {"fuera-de-todo"}}
        code, lines = self.import_(apply=True, catalog=known, live=off)
        text = "\n".join(lines)
        self.assertEqual(code, 1)
        self.assertIn("servicio_deshabilitado", text)           # the approved one: ginecologia is off
        self.assertIn("centro_deshabilitado", text)              # the pending one: hospital-pinamar is off
        self.assertEqual(self.count(), 0)
        gone = {"servicios": set(), "centros": set(), "medicos": set()}
        code, lines = self.import_(catalog=gone)
        self.assertIn("servicio_no_esta_en_el_catalogo", "\n".join(lines))

    def test_a_switched_off_doctor_blocks_the_request(self):
        self.exported_then_target()
        live = dict(_CATALOG, medicos={"sebastian-frias"})
        code, lines = self.import_(apply=True, live=live)
        self.assertEqual(code, 1)
        self.assertIn("medico_deshabilitado", "\n".join(lines))
        self.assertEqual([r["estado"] for r in db_booking.list_admin()], ["pendiente"])

    def test_the_slot_doctor_stored_with_the_request_must_be_live_too(self):
        self.write([self.entry(1, slot_doctor_id="fuera-de-todo"), self.entry(2, slot_doctor_id="sebastian-frias",
                                                                              assigned_doctor_id="sebastian-frias")])
        known = dict(_CATALOG, medicos=_CATALOG["medicos"] | {"fuera-de-todo"})
        code, lines = self.import_(catalog=known, live=_CATALOG)
        text = "\n".join(lines)
        self.assertIn("CONFLICTO #1 — medico_deshabilitado", text)
        self.assertIn("crear     #2", text)
        code, lines = self.import_()                          # unknown to the catalog altogether
        self.assertIn("CONFLICTO #1 — medico_no_esta_en_el_catalogo", "\n".join(lines))

    def test_live_ids_is_what_the_site_gives_appointments_with(self):
        for tipo, iid, doc, off in (("servicios", "zz-vigente", {"nombre": "Vigente"}, False),
                                    ("servicios", "zz-baja", {"nombre": "De baja"}, True),
                                    ("medicos", "zz-doc-vigente", {"nombre": "Doc Vigente"}, False),
                                    ("medicos", "zz-doc-baja", {"nombre": "Doc de baja"}, True),
                                    ("medicos", "zz-doc-incompleto", {"nombre": "", "incompleto": True}, False)):
            db_catalog.catalog_upsert(tipo, iid, doc, borrado=off)
            self.addCleanup(db_catalog.catalog_delete_row, tipo, iid)
        self.addCleanup(catalog.invalidate)
        catalog.invalidate()
        services, doctors = catalog.live_ids("servicios"), catalog.live_ids("medicos")
        self.assertIn("zz-vigente", services)
        self.assertNotIn("zz-baja", services)
        self.assertEqual({i for i in doctors if i.startswith("zz-")}, {"zz-doc-vigente"})
        self.assertLessEqual(services, catalog.known_ids("servicios"))
        self.assertTrue({"zz-baja", "zz-doc-baja", "zz-doc-incompleto"} <= catalog.known_ids("servicios")
                        | catalog.known_ids("medicos"))
        # The script asks the live catalog, not the one that ever was.
        self.assertNotIn("zz-baja", self.script._live_ids("servicios"))
        self.assertIn("zz-baja", self.script._known("servicios"))


class TestSourceAndDuplicates(_Base):
    """(e) The import refuses to run where the file came from, and sees a request
    the target already holds under another key."""

    def test_it_refuses_on_the_installation_the_file_came_from(self):
        self.export(self.source())
        before = self.count()
        for apply in (False, True):
            code, lines = self.import_(apply=apply, host=SOURCE.upper())
            self.assertEqual(code, 2)
            self.assertIn("ESTA instalación", "\n".join(lines))
        self.assertEqual(self.count(), before)

    def test_it_refuses_when_the_rows_are_here_under_their_own_ids_whatever_the_origin_says(self):
        ok, pend, *_ = self.source()
        self.export([ok, pend])
        with open(self.file, encoding="utf-8") as f:
            data = json.load(f)
        data["origen"] = "tipeado.example"                       # a typo at export: the name no longer gives it away
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        before = self.count()
        code, lines = self.import_(apply=True)
        self.assertEqual(code, 2)
        self.assertIn("ESTA instalación", "\n".join(lines))
        self.assertEqual(self.count(), before)

    def test_it_does_not_mistake_another_installation_whose_ids_coincide(self):
        rid = _row(dni="30999000", when=self.when + datetime.timedelta(days=1), doctor="sebastian-frias")
        self.write([self.entry(rid)])                            # same id as a row here, another person and time
        code, lines = self.import_()
        self.assertEqual(code, 0, lines)
        self.assertNotIn("ESTA instalación", "\n".join(lines))

    def test_a_row_the_import_made_never_makes_the_target_look_like_the_source(self):
        self.write([self.entry(1)])
        self.assertEqual(self.import_(apply=True)[0], 0)
        with db._connect() as conn, conn.cursor() as cur:
            made = sqlcore.run(cur, sa.select(sm.booking_requests.c.id, sm.booking_requests.c.created)).fetchone()
        # Another file from a source whose request #<id> has the DNI and creation time of
        # the row made here: its id came out the same by chance on the two sequences.
        self.write([self.entry(made["id"], dni="31000001", created=made["created"].isoformat(),
                               appointment_at=(self.when + datetime.timedelta(days=2)).isoformat())],
                   origin="otra-fuente.example")
        code, lines = self.import_()
        self.assertNotIn("ESTA instalación", "\n".join(lines))
        self.assertEqual(code, 0, lines)

    def test_without_its_own_origin_an_installation_cannot_tell_and_refuses(self):
        self.exported_then_target()
        with _env.patched_env(TURNOS_PUBLIC_ORIGIN=None):
            lines = []
            with patch.object(self.script, "_known", lambda tipo: set(_CATALOG[tipo])):
                self.assertEqual(self.script.import_file(self.file, apply=True, emit=lines.append), 2)
        self.assertIn("TURNOS_PUBLIC_ORIGIN", "\n".join(lines))
        self.assertEqual(self.count(), 0)

    def test_the_same_request_under_another_key_is_a_duplicate_in_the_plan(self):
        ok, pend = self.exported_then_target()
        # Already on the target (loaded by hand, no import key): the approved one with the same
        # DNI, service and time — under another doctor, so only the duplicate rule can see it —
        # and the pending one with the same DNI, service and creation time.
        with open(self.file, encoding="utf-8") as f:
            sent = {r["origen_id"]: r for r in json.load(f)["solicitudes"]}
        _row(dni="30111222", when=self.when, doctor="sebastian-frias")
        _row(dni="30333444", service="cardiologia-hospital", status="pendiente", doctor=None,
             created=db_values._coerce_dt(sent[pend]["created"]))
        code, lines = self.import_(apply=True)
        text = "\n".join(lines)
        self.assertEqual(code, 1)
        self.assertEqual(text.count("duplicado_en_destino"), 2)
        self.assertEqual(self.count(), 2)

    def test_a_closed_request_of_the_target_is_not_a_duplicate_and_the_file_cannot_repeat_itself(self):
        self.write([self.entry(1), self.entry(2, dni="31000001", assigned_doctor_id="sebastian-frias")])
        _row(dni="31000001", when=self.when, status="cancelada", doctor="sebastian-frias")
        code, lines = self.import_()
        text = "\n".join(lines)
        self.assertIn("crear     #1", text)
        self.assertIn("CONFLICTO #2 — duplicado_en_el_archivo", text)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
