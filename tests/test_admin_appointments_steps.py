"""`admin_appointments.create_appointment` pinned at its boundary (no HTTP; the routes are in
test_admin_appointments.py): each scenario compares what the caller can observe — the answer or the `ApiError`
(status and text), the booking rows left, and the slot holds left (the desk's own hold is released on every way out)."""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")

from app.core import db, db_accounts, db_booking, db_operators, db_slots, tz  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.admin_panel import admin_appointments  # noqa: E402
from app.admin_panel.native_json import ApiError  # noqa: E402
from app.modules.turnos import booking_decisions, slot_holds  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_turnos_steps", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_FRANJA = {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}


def _svc(sid, nombre, cluster, modo="solicitud", **extra):
    s = {"id": sid, "nombre": nombre, "centro": "hospital", "cluster": cluster, "categoria": "Consultorios",
         "modo": modo, "agenda": dict(_FRANJA)}
    s.update(extra)
    return s


def _doc(sid, nombre, cluster, **franja):
    return {"id": sid, "nombre": nombre, "especialidad": "x", "cluster": cluster, "centro": "hospital",
            "centros": [{"centro": "hospital", "franjas": [dict(_FRANJA, **franja)]}]}


_SERVICIOS = [_svc("cardio-hosp", "Cardiología", "cardiologia"), _svc("cardio-2", "Cardiología 2", "cardiologia"),
              _svc("derma-hosp", "Dermatología", "dermatologia"), _svc("lab-hosp", "Laboratorio", "laboratorio"),
              {"id": "guardia", "nombre": "Guardia", "centro": "hospital", "cluster": "clinica",
               "categoria": "Guardia", "modo": "walkin"},
              # a same-day opening: listed for today only, open all day
              _svc("guardia-hoy", "Guardia de hoy", "clinica", "walkin", apertura_mismo_dia=True,
                   agenda={"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00", "hasta": "23:59", "cada_min": 60})]
_MEDICOS = [_doc("med-sobre", "Ana Sobre", "cardiologia", cupos={"sobreturnos": 2}),
            _doc("med-sin", "Beto Sin", "cardiologia"), _doc("med-derma", "Dora Derma", "dermatologia")]
_ADMIN = {"rol": "admin", "usuario": "admin"}
_OTRO = ("30999888", "Otro", "Paciente")


def _write_fixtures(webdir, servicios):
    os.makedirs(webdir, exist_ok=True)
    for name, doc in (("servicios.json", {"servicios": servicios}),
                      ("centros-salud.json", {"centros": [{"slug": "hospital", "name": "Hospital", "address": "Av. 1"}]}),
                      ("medicos.json", {"medicos": _MEDICOS})):
        with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f)


def _dia(offset=1, business=True):
    d = datetime.now(tz.tzinfo()).date() + timedelta(days=offset)
    while business and d.isoweekday() > 5:
        d += timedelta(days=1)
    return d.isoformat()


def _persist_fails(exc):
    return [mock.patch.object(booking_decisions, "_persist_approval", side_effect=exc)]


class _Desk:
    """One method per scenario: returns (identity, propio, body, mocks) after arranging the database."""

    def __init__(self):
        self.dia = _dia()

    def b(self, **kw):
        body = {"dni": "30111222", "servicio_id": "cardio-hosp", "dia": self.dia, "hora": "09:20"}
        body.update(kw)
        return {k: v for k, v in body.items() if v is not None}

    def book(self, **kw):
        admin_appointments.create_appointment(_ADMIN, "", self.b(**kw))

    def previo(self, **kw):
        """A turno already given at 09:20, and a second patient to take the next one."""
        self.book(**kw)
        db_accounts.create_user(*_OTRO, "1980-01-01", "M", "2254550002", "secreto123")

    def medica(self):
        with db._connect() as c, c.cursor() as cur:
            cur.execute("DELETE FROM operators WHERE username='dra-ana'")
        db_operators.operator_create("dra-ana", "clave1234", "medico", medico_id="med-sobre")
        return {"rol": "medico", "usuario": "dra-ana", "medico_id": "med-sobre"}

    def mesa(self, centros):
        return ({"rol": "admisionista", "usuario": "mesa-propia"}, "", self.b(),
                [mock.patch.object(admin_appointments.admin_auth, "centros_permitidos", return_value=centros)])

    # patient
    def happy_dni(s): return _ADMIN, "", s.b(), []
    def note_only(s): return _ADMIN, "", s.b(dni=None, nota="  Señora   de  Lazcano "), []
    def note_is_clipped(s): return _ADMIN, "", s.b(dni=None, nota="x" * 200), []
    def dni_wins_over_note(s): return _ADMIN, "", s.b(nota="Prueba"), []
    def no_dni_no_note(s): return _ADMIN, "", s.b(dni=None), []
    def patient_missing(s): return _ADMIN, "", s.b(dni="99999999"), []

    def patient_archived(s):
        db_accounts.create_user("30222333", "Arch", "Ivada", "1970-01-01", "F", "2254550009", "secreto123")
        db_accounts.user_admin_update("30222333", {"activo": False})
        return _ADMIN, "", s.b(dni="30222333"), []

    def patient_odd_sex_and_no_contact(s):
        db_accounts.create_user("30333444", "Equis", "Sinsexo", "1990-05-05", "Q", "", "secreto123",
                                email="equis@example.com")
        return _ADMIN, "", s.b(dni="30333444"), []

    # service, center, day and time
    def service_invalid(s): return _ADMIN, "", s.b(servicio_id="nope"), []
    def walkin_service(s): return _ADMIN, "", s.b(servicio_id="guardia"), []
    def other_center_is_404(s): return s.mesa(["otro"])
    def time_missing(s): return _ADMIN, "", s.b(hora=None), []
    def day_malformed(s): return _ADMIN, "", s.b(dia="2026-1-5"), []
    def time_unparseable(s): return _ADMIN, "", s.b(hora="25:99"), []
    def same_day_opening_today(s): return _ADMIN, "", s.b(servicio_id="guardia-hoy", dia=_dia(0, False), hora="10:00"), []
    def same_day_opening_tomorrow(s): return _ADMIN, "", s.b(servicio_id="guardia-hoy", dia=_dia(1, False), hora="10:00"), []

    # overbooking
    def sobreturno_not_bool(s): return _ADMIN, "", s.b(sobreturno="si"), []

    def sobreturno_without_doctor(s):
        s.previo()
        return _ADMIN, "", s.b(dni=_OTRO[0], sobreturno=True), []

    def sobreturno_on_free_minute(s): return _ADMIN, "", s.b(medico="Ana Sobre", sobreturno=True), []

    def sobreturno_ok(s):
        s.previo(medico="Ana Sobre")
        return _ADMIN, "", s.b(dni=_OTRO[0], medico="Ana Sobre", sobreturno=True), []

    def sobreturno_doctor_without_quota(s):
        s.previo(medico="Beto Sin")
        return _ADMIN, "", s.b(dni=_OTRO[0], medico="Beto Sin", sobreturno=True), []

    def sobreturno_quota_spent(s):
        s.previo(medico="Ana Sobre")
        db_accounts.create_user("30777666", "Tercero", "Paciente", "1980-01-01", "M", "2254550003", "secreto123")
        db_accounts.create_user("30555444", "Cuarto", "Paciente", "1980-01-01", "M", "2254550004", "secreto123")
        for dni in (_OTRO[0], "30777666"):
            s.book(dni=dni, medico="Ana Sobre", sobreturno=True)
        return _ADMIN, "", s.b(dni="30555444", medico="Ana Sobre", sobreturno=True), []

    # doctor rules
    def lab_ignores_doctor(s): return _ADMIN, "", s.b(servicio_id="lab-hosp", medico="Ana Sobre"), []
    def doctor_outside_service_pool(s): return _ADMIN, "", s.b(medico="Dora Derma"), []
    def doctor_outside_schedule(s): return _ADMIN, "", s.b(medico="Beto Sin", hora="13:00"), []
    def doctor_outside_schedule_overbooking_skips_that_cut(s): return _ADMIN, "", s.b(medico="Beto Sin", hora="13:00", sobreturno=True), []

    def doctor_busy_in_another_service(s):
        s.previo(medico="Beto Sin", servicio_id="cardio-2")
        return _ADMIN, "", s.b(dni=_OTRO[0], medico="Beto Sin"), []

    def doctor_busy_same_minute(s):
        s.previo(medico="Beto Sin")
        return _ADMIN, "", s.b(dni=_OTRO[0], medico="Beto Sin"), []

    # the doctor's own session
    def own_session_books_own_agenda(s): return s.medica(), "Ana Sobre", s.b(medico="Beto Sin"), []
    def own_session_lab_is_403(s): return s.medica(), "Ana Sobre", s.b(servicio_id="lab-hosp"), []
    def own_session_foreign_service_is_404(s): return s.medica(), "Ana Sobre", s.b(servicio_id="derma-hosp"), []
    def own_session_outside_window_is_allowed(s): return s.medica(), "Ana Sobre", s.b(hora="13:00"), []

    # the desk's own hold, released on every way out
    def minute_taken_by_a_patient_hold(s):
        db_slots.hold_acquire("cardio-hosp", s.dia, "09:20", "wizard-tok-paciente")
        return _ADMIN, "", s.b(), []

    def slot_holds_blows_up(s):
        return _ADMIN, "", s.b(), [mock.patch.object(slot_holds, "try_take", side_effect=RuntimeError("boom"))]

    def slot_conflict_on_persist(s): return _ADMIN, "", s.b(medico="Beto Sin"), _persist_fails(db_booking.SlotConflict())
    def overbooking_capacity_on_persist(s): return _ADMIN, "", s.b(), _persist_fails(db_booking.OverbookingCapacityReached())
    def overbooking_policy_on_persist(s): return _ADMIN, "", s.b(), _persist_fails(booking_decisions.OverbookingPolicyDenied())
    def already_processed_on_persist(s): return _ADMIN, "", s.b(), _persist_fails(db_booking.AlreadyProcessed())
    def unexpected_error_on_persist(s): return _ADMIN, "", s.b(), _persist_fails(RuntimeError("boom"))

    def persist_returns_nothing(s):
        return _ADMIN, "", s.b(), [mock.patch.object(booking_decisions, "_persist_approval", return_value=None)]


# scenario -> (answer or error, slot holds left, rows left)
EXPECTED = {
    'happy_dni': ('ok DIA 09:20 Herrera, Sofía [Cardiología]', 0, ['aprobada dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=mesa:admin|sol=30111222|ev=mesa:admin']),
    'note_only': ('ok DIA 09:20 Señora de Lazcano [Cardiología] sin_ficha', 0, ['aprobada dni=None Señora de Lazcano |nac=None|sexo=None|tel=None|cardio-hosp@hospital|dr=None|by=mesa:admin|sol=None|ev=mesa:admin']),
    'note_is_clipped': ('ok DIA 09:20 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx [Cardiología] sin_ficha', 0, ['aprobada dni=None xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |nac=None|sexo=None|tel=None|cardio-hosp@hospital|dr=None|by=mesa:admin|sol=None|ev=mesa:admin']),
    'dni_wins_over_note': ('ok DIA 09:20 Herrera, Sofía [Cardiología]', 0, ['aprobada dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=mesa:admin|sol=30111222|ev=mesa:admin']),
    'no_dni_no_note': ('400 DNI inválido (7 a 9 dígitos).', 0, []),
    'patient_missing': ('404 paciente_no_encontrado', 0, []),
    'patient_archived': ('400 Ese paciente está archivado.', 0, []),
    'patient_odd_sex_and_no_contact': ('ok DIA 09:20 Sinsexo, Equis [Cardiología]', 0, ['aprobada dni=30333444 Equis Sinsexo|nac=1990-05-05|sexo=None|tel=equis@example.com|cardio-hosp@hospital|dr=None|by=mesa:admin|sol=30333444|ev=mesa:admin']),
    'service_invalid': ('400 servicio_id inválido', 0, []),
    'walkin_service': ('400 Ese servicio se atiende por orden de llegada, sin turno.', 0, []),
    'other_center_is_404': ('404 Ese servicio no es de tu centro de salud.', 0, []),
    'time_missing': ('400 Falta el día o la hora del turno.', 0, []),
    'day_malformed': ('400 Falta el día o la hora del turno.', 0, []),
    'time_unparseable': ('400 Día u hora inválidos.', 0, []),
    'same_day_opening_today': ('ok HOY 10:00 Herrera, Sofía [Guardia de hoy]', 0, ['aprobada dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|guardia-hoy@hospital|dr=None|by=mesa:admin|sol=30111222|ev=mesa:admin']),
    'same_day_opening_tomorrow': ('400 Ese listado se abre sólo para el día de hoy.', 0, []),
    'sobreturno_not_bool': ('400 sobreturno_invalido', 0, []),
    'sobreturno_without_doctor': ('400 sobreturno_requiere_medico', 0, []),
    'sobreturno_on_free_minute': ('409 sobreturno_requiere_turno_previo', 0, []),
    'sobreturno_ok': ('ok DIA 09:20 Paciente, Otro [Cardiología] sobreturno', 0, ['aprobada dni=30999888 Otro Paciente|nac=1980-01-01|sexo=M|tel=2254550002|cardio-hosp@hospital|dr=Ana Sobre|by=mesa:admin|sol=30999888|ev=mesa:admin S']),
    'sobreturno_doctor_without_quota': ('409 sobreturno_no_configurado', 0, []),
    'sobreturno_quota_spent': ('409 sobreturno_cupo_agotado', 0, []),
    'lab_ignores_doctor': ('ok DIA 09:20 Herrera, Sofía [Laboratorio]', 0, ['aprobada dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|lab-hosp@hospital|dr=None|by=mesa:admin|sol=30111222|ev=mesa:admin']),
    'doctor_outside_service_pool': ('409 medico_no_atiende_servicio', 0, []),
    'doctor_outside_schedule': ('409 turno_medico_fuera_de_agenda', 0, []),
    'doctor_outside_schedule_overbooking_skips_that_cut': ('409 sobreturno_requiere_turno_previo', 0, []),
    'doctor_busy_in_another_service': ('409 turno_medico_tomado', 0, []),
    'doctor_busy_same_minute': ('409 turno_tomado', 0, []),
    'own_session_books_own_agenda': ('ok DIA 09:20 Herrera, Sofía [Cardiología]', 0, ['aprobada dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=Ana Sobre|by=medico:dra-ana|sol=30111222|ev=medico:dra-ana']),
    'own_session_lab_is_403': ('403 Un médico sólo puede dar turnos de su propia agenda.', 0, []),
    'own_session_foreign_service_is_404': ('404 Ese servicio no es de tu agenda.', 0, []),
    'own_session_outside_window_is_allowed': ('ok DIA 13:00 Herrera, Sofía [Cardiología]', 0, ['aprobada dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=Ana Sobre|by=medico:dra-ana|sol=30111222|ev=medico:dra-ana']),
    'minute_taken_by_a_patient_hold': ('409 turno_tomado', 1, []),
    'slot_holds_blows_up': ('409 turno_tomado', 0, []),
    'slot_conflict_on_persist': ('409 turno_medico_tomado', 0, ['pendiente dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=None|sol=30111222|ev=mesa:admin']),
    'overbooking_capacity_on_persist': ('409 sobreturno_cupo_agotado', 0, ['pendiente dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=None|sol=30111222|ev=mesa:admin']),
    'overbooking_policy_on_persist': ('409 sobreturno_no_configurado', 0, ['pendiente dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=None|sol=30111222|ev=mesa:admin']),
    'already_processed_on_persist': ('409 La solicitud ya fue resuelta.', 0, ['pendiente dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=None|sol=30111222|ev=mesa:admin']),
    'unexpected_error_on_persist': ('raised RuntimeError', 0, ['pendiente dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=None|sol=30111222|ev=mesa:admin']),
    'persist_returns_nothing': ('ok pendiente Herrera, Sofía [Cardiología]', 0, ['pendiente dni=30111222 Sofía Herrera|nac=1953-08-11|sexo=F|tel=2254550001|cardio-hosp@hospital|dr=None|by=None|sol=30111222|ev=mesa:admin']),
}


def _row_text(r, created_by):
    """The booking row the call left, as one line: who, for whom, with which doctor, resolved by whom, and who the
    audit trail says created it."""
    return "%s dni=%s %s %s|nac=%s|sexo=%s|tel=%s|%s@%s|dr=%s|by=%s|sol=%s|ev=%s%s" % (
        r["estado"], r["dni"], r["nombre"], r["apellido"], r["nacimiento"], r["sexo"], r["contacto"],
        r["servicio_id"], r["centro_slug"], r["medico_asignado"], r["resuelta_por"], r["solicitante_dni"],
        created_by, " S" if r["sobreturno"] else "")


def _when(iso):
    """'DIA 09:20' / 'HOY 10:00' for an answer's turno_dt (Argentina time), 'pendiente' without one."""
    if not iso:
        return "pendiente"
    dt = datetime.fromisoformat(iso).astimezone(tz.tzinfo())
    day = {_dia(0, False): "HOY"}.get(dt.date().isoformat(), "DIA")
    return "%s %s" % (day, dt.strftime("%H:%M"))


class TestCreateAppointment(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, catalog, slot_holds RESTART IDENTITY CASCADE")
        tmp = tempfile.mkdtemp(prefix="admin-turnos-steps-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write_fixtures(os.path.join(tmp, "webdata"), _SERVICIOS)
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        self.addCleanup(catalog._JSON_CACHE.clear)
        self.addCleanup(catalog.invalidate)
        slot_holds._TAKEN_CACHE["ts"] = 0
        db_accounts.create_user("30111222", "Sofía", "Herrera", "1953-08-11", "F", "2254550001", "secreto123",
                                email="sofia@example.com")

    def observe(self, name):
        identity, propio, body, mocks = getattr(_Desk(), name)()
        slot_holds._TAKEN_CACHE["ts"] = 0
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT COALESCE(max(id), 0) AS m FROM booking_requests")
            before = cur.fetchone()["m"]
        try:
            with contextlib.ExitStack() as stack:
                for m in mocks:
                    stack.enter_context(m)
                r = admin_appointments.create_appointment(identity, propio, body)
            out = "ok %s %s [%s]%s%s" % (_when(r["turno_dt"]), r["paciente"][:30], r["servicio"],
                                         " sobreturno" if r["sobreturno"] else "", " sin_ficha" if r["sin_ficha"] else "")
        except ApiError as e:
            out = "%s %s" % (e.status, e.body["error"])
        except Exception as e:  # noqa: BLE001 — the type is part of the observation
            out = "raised " + type(e).__name__
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM slot_holds")
            holds = cur.fetchone()["n"]
            cur.execute("SELECT id FROM booking_requests WHERE id > %s ORDER BY id", (before,))
            rows = [db_booking.get_booking_request(r["id"]) for r in cur.fetchall()]
            texts = []
            for r in rows:
                cur.execute("SELECT actor FROM appointment_events WHERE request_id=%s AND kind='creada'", (r["id"],))
                texts.append(_row_text(r, cur.fetchone()["actor"]))
        return out, holds, texts


def _make(name):
    def test(self):
        self.assertEqual(self.observe(name), EXPECTED[name], name)
    test.__name__ = "test_" + name
    return test


for _n, _ in EXPECTED.items():
    setattr(TestCreateAppointment, "test_" + _n, _make(_n))


class TestScenarioList(unittest.TestCase):
    def test_every_scenario_has_an_expectation(self):
        names = {n for n in vars(_Desk) if not n.startswith("_") and n not in ("b", "book", "previo", "medica", "mesa")}
        self.assertEqual(names, set(EXPECTED))


if __name__ == "__main__":
    unittest.main()
