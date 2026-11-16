"""test_integrity_races.py — concurrency races for the Tier 1.4 data-integrity
guards (docs/roadmap/data-integrity-guards-plan.md).

Each test fires N threads at the SAME contended operation through a
`threading.Barrier` (house pattern, see tests/test_slot_holds.py:172
`test_carrera_un_solo_ganador`) and asserts exactly one winner + side effects
fired once — race-shaped, not a mirror of the implementation. Against the
pre-guard code every one of these is either flaky (both threads can win) or
outright double-executes a side effect; against the guarded code the result
is deterministic every run.

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
         discover -s tests -p "test_integrity_races.py" -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta, time as dtime
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_values  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_requests    # noqa: E402
from app.admin_panel import admin_appointments  # noqa: E402
from app.auth import admin_auth          # noqa: E402
from app.patient_web import catalog             # noqa: E402
from app.patient_web import site_availability, site_base
from app.modules.turnos import slot_holds          # noqa: E402
from app.notifications import reminders           # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

_CLIENT = TestClient(app_module.app)


def _alembic_head():
    """The Alembic head revision, read from the script directory itself.

    A fresh bootstrap must land stamped EXACTLY at head — pinning the literal
    revision id here would just rot on the next migration and say nothing
    about the property under test."""
    from alembic.script import ScriptDirectory
    from alembic.config import Config
    # Reading revisions needs only the script directory. Avoid Alembic's
    # locale-decoded ini reader under the managed runner's ASCII C locale.
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()
from app.modules.turnos import waitlist            # noqa: E402
from app.core import tz as _tz  # noqa: E402

from app.auth import turnstile           # noqa: E402
from app.core import config  # noqa: E402
turnstile.ENV_PATH = ""    # no leer el .env real (mismo truco que los otros tests)
from app.ai import abuse_guard  # noqa: E402
abuse_guard.ENV_PATH = ""
config.ENV_PATH = ""

_ART = _tz.tzinfo()

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_integrity",
                           seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE appointment_offers RESTART IDENTITY")
            cur.execute("TRUNCATE waitlist RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")


def _run_concurrently(fn, n):
    """Fires `fn(i)` from `n` threads released at the same instant (barrier).
    Returns (resultados, excepciones) — parallel lists indexed by thread i,
    None where the other list has the real value."""
    barrera = threading.Barrier(n)
    resultados = [None] * n
    excepciones = [None] * n

    def _worker(i):
        barrera.wait()
        try:
            resultados[i] = fn(i)
        except Exception as e:  # noqa: BLE001 — captured for the assertions
            excepciones[i] = e

    hilos = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return resultados, excepciones


def _crear_solicitud(dni="20111000"):
    return db_booking.create_booking_request(
        dni, "Ana", "Gómez", "1990-01-01", "wa:1", "cardiologia", "centro-a")


# ============================================================================
# Infra compartida por la SEGUNDA mitad del archivo — las carreras a nivel
# de RUTA (HTTP handler, FakeHandler, notificaciones contadas). La primera
# mitad de arriba (db-helper races) no la necesita — llama directo a
# db.py/reminders.py/waitlist.py.
# ============================================================================

def _admin_tok(rol="admin", usuario=None):
    """Sesión admin válida — cookie firmada (login real vive en admin_auth),
    mismo patrón que test_booking_requests.py:_tok()."""
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario or admin_auth._admin_user(), rol)}


_PACIENTE = {"dni": "20999321", "nombre": "Rita", "apellido": "Gómez",
             "nacimiento": "1990-01-01", "sexo": "F", "contacto": "wa:11-5555"}

# webdata propio para las rutas que leen catalog.py directo (admin_appointments,
# slot_holds._create_hold) — no todas las rutas lo necesitan: booking_requests._create
# puede evitarlo del todo monkeypatcheando SERVICES_PATH (ver CreateRouteFixtureTestCase).
_AGENDA_ANCHA = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00", "hasta": "23:40", "cada_min": 20}
_CENTROS_RUTA = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS_RUTA = {"servicios": [
    {"id": "cardiologia", "nombre": "Cardiología", "centro": "centro-a",
     "modo": "solicitud", "agenda": _AGENDA_ANCHA},
]}
_MEDICOS_RUTA = {"medicos": []}


def _write_fixtures_ruta(webdir):
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
        json.dump(_CENTROS_RUTA, f)
    with open(os.path.join(webdir, "servicios.json"), "w", encoding="utf-8") as f:
        json.dump(_SERVICIOS_RUTA, f)
    with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
        json.dump(_MEDICOS_RUTA, f)


def _primeros_slots_ruta(n=2):
    slots = site_availability._agenda_slots(_AGENDA_ANCHA)
    return [(s["dia"], s["hora"]) for s in slots[:n]]


class RouteFixtureTestCase(unittest.TestCase):
    """Base para las rutas que leen catalog.py de verdad (mesa/admin_appointments,
    /api/slot-hold) — webdata propio vía TURNOS_WEBDATA_DIR (aislado del real),
    mismo patrón que test_slot_holds.py:ReservasFixtureTestCase."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._tmp = tempfile.mkdtemp(prefix="integrity-route-test-")
        webdir = os.path.join(self._tmp, "webdata")
        _write_fixtures_ruta(webdir)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()
        slot_holds.invalidate()
        slot_holds._HITS.clear()

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()
        slot_holds.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_tables()


class CreateRouteFixtureTestCase(unittest.TestCase):
    """Base para POST /api/requests (booking_requests._create) — catálogo
    propio vía SERVICES_PATH monkeypatcheado (mismo truco que
    test_booking_requests.py), sin necesidad de webdata: el servicio sale de
    ese archivo y los centros de `catalog.centers` (parcheado acá: el alta
    rechaza un centro que el catálogo no tiene)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.enterContext(patch.object(catalog, "centers", return_value=[{"slug": "centro-a"}]))
        _reset_tables()
        self._tmp = tempfile.mkdtemp(prefix="integrity-create-test-")
        cat = os.path.join(self._tmp, "servicios-integrity.json")
        with open(cat, "w", encoding="utf-8") as f:
            json.dump({"servicios": [{"id": "cardiologia", "nombre": "Cardiología"}]}, f)
        self._orig_services_path = booking_requests.SERVICES_PATH
        booking_requests.SERVICES_PATH = cat
        self._orig_get_user = booking_requests._get_user
        booking_requests._get_user = lambda h: dict(_PACIENTE)
        # límite de sitio = 1 (default): asegura que ningún test anterior que
        # haya subido MAX_ACTIVE_REQUESTS (p.ej. test_booking_requests.py,
        # cuando corren juntos en un mismo `unittest discover`) contamine la
        # carrera del límite que este archivo quiere ejercitar.
        _env.set_env(self, MAX_ACTIVE_REQUESTS=None)

    def tearDown(self):
        booking_requests._get_user = self._orig_get_user
        booking_requests.SERVICES_PATH = self._orig_services_path
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_tables()


def _fresh_db_url(name):
    """Crea (drop+create) una base vacía propia `name` colgada del MISMO
    servidor que _MAINT_URL, y devuelve su URL de conexión. Usada por los
    tests de bootstrap/migración (items 8-10) — necesitan una base en un
    estado que el fixture del módulo (`_db`, ya con el esquema aplicado) no
    puede darles."""
    import psycopg
    with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
            cur.execute("CREATE DATABASE %s" % name)
    p = urlsplit(_TEST_URL)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def _drop_db(name):
    import psycopg
    with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)


class TestApproveRace(unittest.TestCase):
    """Item 1 — approve/reject status-CAS (db.py:1761 `approve`, wired through
    `booking_decisions._persist_approval`)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_aprobar_dos_veces_un_solo_ganador_un_solo_email(self):
        sid = _crear_solicitud()
        turno_dt = datetime.now(_ART) + timedelta(days=1)

        def _aprobar(_i):
            return booking_decisions._persist_approval(
                sid, turno_dt, resuelta_por="operador-test")

        with patch("app.notifications.send.notify_approved") as mock_notify:
            resultados, excepciones = _run_concurrently(_aprobar, 8)

        ganadores = [r for r in resultados if r is not None]
        perdedores = [e for e in excepciones if isinstance(e, db_booking.AlreadyProcessed)]
        otras = [e for e in excepciones
                 if e is not None and not isinstance(e, db_booking.AlreadyProcessed)]

        self.assertEqual(otras, [], f"excepciones inesperadas: {otras}")
        self.assertEqual(len(ganadores), 1, f"esperaba 1 ganador, hubo {len(ganadores)}")
        self.assertEqual(len(perdedores), 7)
        # el email de aprobación salió UNA sola vez, no una por hilo
        self.assertEqual(mock_notify.call_count, 1)

        fila = db_booking.get_booking_request(sid)
        self.assertEqual(fila["estado"], "aprobada")


class TestDoctorSlotCollision(unittest.TestCase):
    """Item 2 — el índice único (assigned_doctor_id, appointment_at) parcial:
    dos solicitudes PENDIENTES distintas, mismo médico+horario, aprobadas a
    la vez → sólo una queda 'aprobada'."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_dos_pendientes_mismo_medico_y_horario_una_sola_aprobada(self):
        sid_a = _crear_solicitud("20111001")
        sid_b = _crear_solicitud("20111002")
        turno_dt = datetime.now(_ART) + timedelta(days=1)

        def _aprobar(i):
            sid = sid_a if i == 0 else sid_b
            return db_booking.approve(sid, turno_dt, {}, medico_asignado="Dr. Test",
                               medico_asignado_id="doc-1", resuelta_por="t")

        resultados, excepciones = _run_concurrently(_aprobar, 2)

        ganadores = [r for r in resultados if r is not None]
        conflictos = [e for e in excepciones if isinstance(e, db_booking.SlotConflict)]
        otras = [e for e in excepciones
                 if e is not None and not isinstance(e, db_booking.SlotConflict)]

        self.assertEqual(otras, [], f"excepciones inesperadas: {otras}")
        self.assertEqual(len(ganadores), 1, f"esperaba 1 ganador, hubo {len(ganadores)}")
        self.assertEqual(len(conflictos), 1)

        aprobadas = [db_booking.get_booking_request(sid_a), db_booking.get_booking_request(sid_b)]
        self.assertEqual(sum(1 for f in aprobadas if f["estado"] == "aprobada"), 1)

    def test_sobreturno_exime_del_indice_bajo_carrera(self):
        """El primer approve normal y un segundo con sobreturno=True al MISMO
        (médico, horario), disparados a la vez: el sobreturno nunca choca
        contra el índice, corra antes o después que el normal."""
        sid_normal = _crear_solicitud("20111003")
        sid_sobre = _crear_solicitud("20111004")
        turno_dt = datetime.now(_ART) + timedelta(days=2)

        def _aprobar(i):
            if i == 0:
                return db_booking.approve(sid_normal, turno_dt, {}, medico_asignado="Dr. Test",
                                   medico_asignado_id="doc-2", resuelta_por="t")
            return db_booking.approve(sid_sobre, turno_dt, {}, medico_asignado="Dr. Test",
                               medico_asignado_id="doc-2", resuelta_por="t",
                               sobreturno=True)

        resultados, excepciones = _run_concurrently(_aprobar, 2)

        otras = [e for e in excepciones if e is not None]
        self.assertEqual(otras, [], f"el sobreturno no debería chocar nunca: {otras}")
        self.assertEqual(sum(1 for r in resultados if r is not None), 2)

        fila_normal = db_booking.get_booking_request(sid_normal)
        fila_sobre = db_booking.get_booking_request(sid_sobre)
        self.assertEqual(fila_normal["estado"], "aprobada")
        self.assertEqual(fila_sobre["estado"], "aprobada")
        self.assertTrue(fila_sobre["sobreturno"])

    def test_porcentaje_compite_atomico_entre_dos_minutos(self):
        """One shared budget cannot be spent twice by concurrent minutes."""
        sid_a = _crear_solicitud("20111013")
        sid_b = _crear_solicitud("20111014")
        start = datetime.now(_ART).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=3)
        end = start + timedelta(hours=1)

        def _aprobar(i):
            return db_booking.approve(
                sid_a if i == 0 else sid_b, start if i == 0 else start + timedelta(minutes=20), {},
                medico_asignado="Dra. Porcentaje", medico_asignado_id="doctor-percent",
                sobreturno=True, overbooking_capacity=1,
                overbooking_window=(start, end),
                overbooking_lock_key="test:percentage-cross-minute",
                overbooking_center_slug="centro-a")

        resultados, excepciones = _run_concurrently(_aprobar, 2)
        self.assertEqual(sum(r is not None for r in resultados), 1)
        self.assertEqual(sum(isinstance(e, db_booking.OverbookingCapacityReached) for e in excepciones), 1)

    def test_porcentaje_cuenta_id_y_legacy_solo_en_su_centro(self):
        when = datetime.now(_ART) + timedelta(days=4)
        end = when + timedelta(hours=1)
        rows = [db_booking.create_booking_request(f"201119{i}", "Ana", "Gómez", "1990-01-01", "wa:1",
                                          "cardiologia", center)
                for i, center in enumerate(("centro-a", "centro-a", "centro-a", "centro-b"))]
        db_booking.approve(rows[0], when, medico_asignado="Dra. Álvarez", medico_asignado_id="doc-a", sobreturno=True)
        db_booking.approve(rows[1], when, medico_asignado="Dra. Álvarez", sobreturno=True)  # legacy
        db_booking.approve(rows[2], when, medico_asignado="Dra. Otra", sobreturno=True)      # unrelated NULL id
        db_booking.approve(rows[3], when, medico_asignado="Dra. Álvarez", medico_asignado_id="doc-a", sobreturno=True)
        self.assertEqual(db_booking.overbooking_count(when, end, "Dra. Álvarez", "doc-a", "centro-a"), 2)
        self.assertEqual(db_booking.overbooking_count(when, end, "Dra. Álvarez", "doc-a", "centro-b"), 1)
        self.assertEqual(db_booking.overbooking_count(when, end, "Dra. Álvarez", None, "centro-a"), 1)
        self.assertEqual(db_booking.overbooking_count(when, end, "Dra. Otra", None, "centro-a"), 1)
        db_booking.cancel(rows[0])
        self.assertEqual(db_booking.overbooking_count(when, end, "Dra. Álvarez", "doc-a", "centro-a"), 1)


class TestBookingLimitRace(unittest.TestCase):
    """Item 3 — límite de turnos activos bajo `pg_advisory_xact_lock`
    (db.py:3545 `create_booking_request_with_limit`)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_limite_uno_dos_creaciones_concurrentes_mismo_paciente_una_sola(self):
        dni = "20111005"

        def _crear(i):
            return db_booking.create_booking_request_with_limit(
                dni, "Ana", "Gómez", "1990-01-01", "wa:1", "cardiologia",
                "centro-a", limite=1)

        resultados, excepciones = _run_concurrently(_crear, 6)

        creadas = [r for r in resultados if r is not None]
        rechazadas = [e for e in excepciones if isinstance(e, db_booking.LimitReached)]
        otras = [e for e in excepciones
                 if e is not None and not isinstance(e, db_booking.LimitReached)]

        self.assertEqual(otras, [], f"excepciones inesperadas: {otras}")
        self.assertEqual(len(creadas), 1, f"esperaba 1 creada, hubo {len(creadas)}")
        self.assertEqual(len(rechazadas), 5)

        activos = db_slots.count_active_requests(dni, "cardiologia")
        self.assertEqual(activos, 1)


class TestIdemKeyRace(unittest.TestCase):
    """Item 4 — Confirmá idempotente (idem_key, ON CONFLICT DO NOTHING +
    fetch-existing)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_doble_submit_mismo_idem_key_una_sola_fila(self):
        dni = "20111006"
        idem_key = "hold-token-abc"

        def _crear(_i):
            return db_booking.create_booking_request_with_limit(
                dni, "Ana", "Gómez", "1990-01-01", "wa:1", "cardiologia",
                "centro-a", limite=10, idem_key=idem_key)

        resultados, excepciones = _run_concurrently(_crear, 8)

        otras = [e for e in excepciones if e is not None]
        self.assertEqual(otras, [], f"un idem_key repetido nunca debería chocar: {otras}")

        ids = {r["id"] for r in resultados if r is not None}
        self.assertEqual(len(ids), 1, f"esperaba UN solo id, hubo {ids}")
        # las 8 respuestas apuntan a la MISMA fila
        self.assertTrue(all(r["id"] == next(iter(ids)) for r in resultados))

        activos = db_slots.count_active_requests(dni, "cardiologia")
        self.assertEqual(activos, 1)


class TestReminderClaimRace(unittest.TestCase):
    """Item 5 — reminders.tick() claim-CAS (db_booking.claim_reminder)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_dos_ticks_concurrentes_mandan_un_solo_recordatorio(self):
        sid = _crear_solicitud("20111007")
        ahora_art = datetime.now(_ART).replace(hour=10, minute=0, second=0, microsecond=0)
        manana = ahora_art.date() + timedelta(days=1)
        turno_dt = datetime.combine(manana, dtime(9, 30), tzinfo=_ART)
        db_booking.approve(sid, turno_dt, {}, resuelta_por="t")
        now_utc = ahora_art.astimezone(timezone.utc)

        def _tick(_i):
            return reminders.tick(now_utc)

        with patch("app.notifications.send.notify_reminder") as mock_notify:
            _run_concurrently(_tick, 2)

        self.assertEqual(mock_notify.call_count, 1)
        fila = db_booking.get_booking_request(sid)
        self.assertIsNotNone(fila["recordatorio_dt"])

    def test_dos_turnos_debidos_bajo_ticks_solapados_uno_por_turno(self):
        """Item 6 (varios turnos): dos solicitudes DISTINTAS caen mañana, tres
        workers (`tick()`) las barren a la vez — cada turno manda exactamente
        UN recordatorio (ni cero, ni dos), nunca uno cruzado con el del otro."""
        ahora_art = datetime.now(_ART).replace(hour=10, minute=0, second=0, microsecond=0)
        manana = ahora_art.date() + timedelta(days=1)
        sid_a = _crear_solicitud("20111070")
        sid_b = _crear_solicitud("20111071")
        turno_a = datetime.combine(manana, dtime(9, 30), tzinfo=_ART)
        turno_b = datetime.combine(manana, dtime(15, 0), tzinfo=_ART)
        db_booking.approve(sid_a, turno_a, {}, resuelta_por="t")
        db_booking.approve(sid_b, turno_b, {}, resuelta_por="t")
        now_utc = ahora_art.astimezone(timezone.utc)

        def _tick(_i):
            return reminders.tick(now_utc)

        with patch("app.notifications.send.notify_reminder") as mock_notify:
            _run_concurrently(_tick, 3)

        self.assertEqual(mock_notify.call_count, 2,
                          "dos turnos debidos → exactamente dos recordatorios, uno cada uno")
        avisados = {c.args[0]["id"] for c in mock_notify.call_args_list}
        self.assertEqual(avisados, {sid_a, sid_b})
        self.assertIsNotNone(db_booking.get_booking_request(sid_a)["recordatorio_dt"])
        self.assertIsNotNone(db_booking.get_booking_request(sid_b)["recordatorio_dt"])


class TestWaitlistOfferRace(unittest.TestCase):
    """Item 6 — una sola oferta 'ofrecida' viva por slot
    (appointment_offers_live_slot_uniq, db_slots.create_appointment_offer)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_dos_liberaciones_concurrentes_mismo_slot_una_sola_oferta_viva(self):
        servicio_id, centro_slug = "cardiologia", "centro-a"
        slot_dt = datetime.now(timezone.utc) + timedelta(days=1)
        turno_actual = slot_dt + timedelta(days=1)  # más lejos: candidato válido
        sid = _crear_solicitud("20111008")
        waitlist.opt_in(sid, "20111008", "paciente@example.com",
                         servicio_id, centro_slug, turno_actual)

        def _liberar(_i):
            return waitlist.release_slot(servicio_id, centro_slug, slot_dt)

        with patch("app.notifications.send.notify_offer") as mock_notify:
            resultados, excepciones = _run_concurrently(_liberar, 6)

        otras = [e for e in excepciones if e is not None]
        self.assertEqual(otras, [], f"excepciones inesperadas: {otras}")
        ofertas_creadas = [r for r in resultados if r is not None]
        self.assertEqual(len(ofertas_creadas), 1,
                          f"esperaba 1 oferta creada, hubo {len(ofertas_creadas)}")
        self.assertEqual(mock_notify.call_count, 1)

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS n FROM appointment_offers
                   WHERE service_id=%s AND center_slug=%s AND slot_at=%s
                     AND status='ofrecida'""",
                (servicio_id, centro_slug, db_values._coerce_dt(slot_dt)))
            n = cur.fetchone()["n"]
        self.assertEqual(n, 1)


class TestHoldAcquireFailsClosed(unittest.TestCase):
    """Item 7 — `db_slots.hold_acquire` con la base inalcanzable degrada a False,
    nunca a True (fail closed, no fail open)."""

    def test_hold_acquire_con_db_inalcanzable_devuelve_false(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
            self.assertFalse(
                db_slots.hold_acquire("cardiologia", "2099-01-01", "09:00", "tokX"))


# ============================================================================
# SECCIÓN B — carreras a nivel de RUTA (HTTP real + side effects).
# A diferencia de la Sección A (que llama db.py directo), estos tests pasan
# por la composición FastAPI — el mismo camino que recorre un request real,
# con la respuesta HTTP
# (status + json) que el perdedor de la carrera efectivamente recibe.
# ============================================================================

class TestRouteApproveRejectRace(unittest.TestCase):
    """Item 1, a nivel de ruta — aprobar y rechazar la MISMA solicitud a la
    vez desde dos pestañas/operadores distintos: un solo estado final, un
    solo email (el del ganador), el perdedor recibe 409 sin romper."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_aprobar_y_rechazar_a_la_vez_por_ruta_un_solo_estado_final(self):
        sid = _crear_solicitud("20111100")
        turno_iso = (datetime.now(_ART) + timedelta(days=1)).isoformat()
        tok = _admin_tok()

        requests = [
            (f"/api/admin/requests/{sid}/approve", {"turno_dt": turno_iso}),
            (f"/api/admin/requests/{sid}/reject", {"motivo": "no corresponde"}),
        ]

        def _run(i):
            path, body = requests[i]
            return _CLIENT.post(path, json=body, headers=tok)

        with patch("app.notifications.send.notify_approved") as mock_aprobado, \
             patch("app.notifications.send.notify_rejected") as mock_rechazado:
            handlers, excepciones = _run_concurrently(_run, 2)

        h_aprobar, h_rechazar = handlers

        self.assertEqual([e for e in excepciones if e is not None], [])

        gano_aprobar = h_aprobar.status_code == 200
        gano_rechazar = h_rechazar.status_code == 200
        self.assertNotEqual(
            gano_aprobar, gano_rechazar,
            f"exactamente un ganador esperado: approve={h_aprobar.status_code} "
            f"reject={h_rechazar.status_code}")

        if gano_aprobar:
            self.assertEqual(h_rechazar.status_code, 409, h_rechazar.json())
            self.assertIn("error", h_rechazar.json())
            self.assertEqual(mock_aprobado.call_count, 1)
            self.assertEqual(mock_rechazado.call_count, 0)
        else:
            self.assertEqual(h_aprobar.status_code, 409, h_aprobar.json())
            self.assertIn("error", h_aprobar.json())
            self.assertEqual(mock_rechazado.call_count, 1)
            self.assertEqual(mock_aprobado.call_count, 0)

        fila = db_booking.get_booking_request(sid)
        self.assertEqual(fila["estado"], "aprobada" if gano_aprobar else "rechazada")


class TestRouteDoctorSlotCollision(unittest.TestCase):
    """Item 2, a nivel de ruta — dos POST /approve para solicitudes DISTINTAS
    al mismo (médico, horario): sólo uno gana, el otro recibe el mismo 409
    "turno_medico_tomado" que ya usa el pre-check secuencial (nunca un 500)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_dos_approve_por_ruta_mismo_medico_y_horario_un_solo_ganador(self):
        sid_a = _crear_solicitud("20111101")
        sid_b = _crear_solicitud("20111102")
        # Mismos valores que test_booking_requests.py:test_aprobar_bloquea_
        # mismo_medico_misma_hora (2099-07-20 cae dentro de la franja
        # `dias:[1]` de esa agenda — ya verificado, no recalcular).
        when = "2099-07-20T10:30-03:00"
        agenda_doctor = [{
            "id": "dra-ruta", "nombre": "Dra. Ruta",
            "agenda": {"franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30}],
                       "excepciones": []},
        }]
        tok = _admin_tok()
        requests = [
            (f"/api/admin/requests/{sid_a}/approve", {"turno_dt": when, "medico": "Dra. Ruta"}),
            (f"/api/admin/requests/{sid_b}/approve", {"turno_dt": when, "medico": "Dra. Ruta"}),
        ]

        def _run(i):
            path, body = requests[i]
            return _CLIENT.post(path, json=body, headers=tok)

        with patch("app.patient_web.catalog.doctors", return_value=agenda_doctor), \
             patch("app.notifications.send.notify_approved") as mock_aprobado:
            handlers, excepciones = _run_concurrently(_run, 2)

        self.assertEqual([e for e in excepciones if e is not None], [])

        ok = [h for h in handlers if h.status_code == 200]
        conflicto = [h for h in handlers if h.status_code == 409]
        self.assertEqual(len(ok), 1, [h.json() for h in handlers])
        self.assertEqual(len(conflicto), 1, [h.json() for h in handlers])
        self.assertEqual(conflicto[0].json()["error"], "turno_medico_tomado")
        self.assertEqual(mock_aprobado.call_count, 1)

        filas = [db_booking.get_booking_request(sid_a), db_booking.get_booking_request(sid_b)]
        self.assertEqual(sum(1 for f in filas if f["estado"] == "aprobada"), 1)
        self.assertEqual(sum(1 for f in filas if f["estado"] == "pendiente"), 1)


class TestRouteDeskVsPatientHold(RouteFixtureTestCase):
    """Item 3 — la mesa (POST /api/admin/appointments) contra un hold de
    paciente VIGENTE sobre el mismo slot: la mesa queda bloqueada con el
    MISMO 409 que la grilla pública, y el hold del paciente sigue en pie."""

    def test_mesa_no_puede_pisar_un_hold_de_paciente_vigente(self):
        dia, hora = _primeros_slots_ruta(1)[0]

        h_hold = _CLIENT.request("POST", "/api/slot-hold", json={"servicio_id": "cardiologia", "dia": dia,
                                    "hora": hora, "token": "paciente-tok"})
        self.assertEqual(h_hold.status_code, 200, h_hold.json())

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM booking_requests")
            antes = cur.fetchone()["n"]

        n = 5
        tok = _admin_tok()

        def _mesa(i):
            return _CLIENT.post(
                "/api/admin/appointments",
                json={"nota": f"Turno mostrador {i}",
                      "servicio_id": "cardiologia", "dia": dia, "hora": hora},
                headers=tok,
            )

        resultados, excepciones = _run_concurrently(_mesa, n)

        self.assertEqual([e for e in excepciones if e is not None], [])
        for h in resultados:
            self.assertEqual(h.status_code, 409, h.json())
            self.assertEqual(h.json()["error"], "turno_tomado")

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM booking_requests")
            despues = cur.fetchone()["n"]
        self.assertEqual(despues, antes,
                          "ningún turno de mostrador debería haberse creado")

        # el hold del paciente sigue vivo: renovarlo con SU token da 200 de nuevo
        h_renovar = _CLIENT.request("POST", "/api/slot-hold", json={"servicio_id": "cardiologia", "dia": dia,
                                       "hora": hora, "token": "paciente-tok"})
        self.assertEqual(h_renovar.status_code, 200, h_renovar.json())


class TestRouteBookingLimitRace(CreateRouteFixtureTestCase):
    """Item 4 — N POST /api/requests concurrentes del MISMO paciente+servicio,
    horarios DISTINTOS (así el único choque posible es el límite, no el
    hold): uno crea, el resto recibe el 409 "limite_turnos_activos"."""

    @patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 8, 9, 8))
    def test_limite_uno_por_ruta_pedidos_paralelos_distintos_horarios(self):
        n = 6

        def _post(i):
            h = _CLIENT.request("POST", "/api/requests", json={
                "servicio_id": "cardiologia", "centro_slug": "centro-a",
                "slot_dia": f"2099-08-{10 + i:02d}", "slot_hora": "09:00",
                "hold_token": f"tok-limite-{i}",
            })
            return h

        with patch("app.notifications.send.notify_received") as mock_notify:
            resultados, excepciones = _run_concurrently(_post, n)

        self.assertEqual([e for e in excepciones if e is not None], [])

        ok = [h for h in resultados if h.status_code == 200]
        limitados = [h for h in resultados if h.status_code == 409]
        self.assertEqual(len(ok), 1, [h.json() for h in resultados])
        self.assertEqual(len(limitados), n - 1)
        for h in limitados:
            self.assertEqual(h.json()["error"], "limite_turnos_activos")
        self.assertEqual(mock_notify.call_count, 1)

        activos = db_slots.count_active_requests(_PACIENTE["dni"], "cardiologia")
        self.assertEqual(activos, 1)


class TestRouteIdemKeyRace(CreateRouteFixtureTestCase):
    """Item 5 — Confirmá repetido (mismo hold_token/idem_key) disparado por
    N hilos a la vez: UNA sola fila en la base, y cada respuesta apunta al
    MISMO id como éxito — nunca un error, nunca una segunda fila."""

    @patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 8, 31, 8))
    def test_confirma_repetido_mismo_idem_key_por_ruta_una_sola_fila(self):
        """Doble tap (o retry tras timeout) del mismo idem_key, disparado
        por N hilos a la vez: UNA sola fila en la base, y cada respuesta —
        tanto el ganador que insertó como cada perdedor que sólo la
        encontró — apunta al MISMO id con `ok: True` (2026-08-05, tier 14
        fix; ver `db_slots.get_booking_request_by_idem_key` y
        `booking_create._respuesta_idem_existente`)."""
        n = 8
        idem_key = "hold-token-integrity-abc"

        def _post(_i):
            h = _CLIENT.request("POST", "/api/requests", json={
                "servicio_id": "cardiologia", "centro_slug": "centro-a",
                "slot_dia": "2099-09-01", "slot_hora": "10:00",
                "hold_token": idem_key, "idem_key": idem_key,
            })
            return h

        with patch("app.notifications.send.notify_received") as mock_notify:
            resultados, excepciones = _run_concurrently(_post, n)

        self.assertEqual([e for e in excepciones if e is not None], [])
        for h in resultados:
            self.assertEqual(h.status_code, 200, h.json())
            self.assertTrue(h.json().get("ok"))

        ids = {h.json()["id"] for h in resultados}
        self.assertEqual(len(ids), 1, f"esperaba UNA sola fila, hubo ids={ids}")

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM booking_requests WHERE idem_key=%s",
                        (idem_key,))
            n_filas = cur.fetchone()["n"]
        self.assertEqual(n_filas, 1)

        self.assertEqual(mock_notify.call_count, 1,
                          "un doble tap del mismo idem_key no debería mandar dos avisos")


# ============================================================================
# SECCIÓN D — bootstrap/migraciones bajo concurrencia real de PROCESOS, y el
# camino de una base "sucia" (datos de antes de este guard) contra el
# camino de una base recién creada.
# ============================================================================

_BOOT_SCRIPT = """import os, sys
sys.path.insert(0, {root!r})
os.environ.setdefault("TURNOS_FAKE_AI", "1")
os.environ.setdefault("TURNOS_SKIP_HSI", "1")
from app.core import db
conn = db._connect()
conn.close()
print("OK")
"""


class TestConcurrentBootstrap(unittest.TestCase):
    """Item 8 — dos PROCESOS separados arrancan contra la MISMA base vacía a
    la vez: ambos terminan limpio y el esquema quedó aplicado una sola vez
    (db_schema._ensure_schema's cross-process pg_advisory_lock, held through
    the pre-Alembic refusal + the entire Alembic layer)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_integrity_boot")
        self.db_url = _fresh_db_url(self.db_name)

    def tearDown(self):
        if HAVE_DB:
            _drop_db(self.db_name)

    def test_dos_procesos_bootstrapean_la_misma_base_vacia_a_la_vez(self):
        """Dos procesos que arrancan a la vez contra una base REALMENTE
        vacía (dos réplicas de un mismo deploy pegándole a una base recién
        creada): ambos terminan limpio y el esquema quedó aplicado una sola
        vez. `db.py:_ensure_schema` keeps the cross-process `pg_advisory_lock`
        on its own connection for the ENTIRE bootstrap — the pre-Alembic
        refusal and the Alembic layer (which on an empty database executes
        the full baseline) — so no other process can sneak in to run DDL in
        the middle (2026-08-05 tier 14 fix). An empty database never
        creates `schema_migrations`:
        the proof that it applied ONCE is that both processes finish clean
        and `alembic_version` has ONE row at head."""
        script_dir = tempfile.mkdtemp(prefix="boot-race-")
        script_path = os.path.join(script_dir, "boot.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(_BOOT_SCRIPT.format(root=ROOT))

        env = dict(os.environ)
        env["DATABASE_URL"] = self.db_url

        n = 2
        resultados = [None] * n
        barrera = threading.Barrier(n)

        def _lanzar(i):
            barrera.wait()
            resultados[i] = subprocess.run(
                [sys.executable, script_path], env=env,
                capture_output=True, text=True, timeout=90)

        hilos = [threading.Thread(target=_lanzar, args=(i,)) for i in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        shutil.rmtree(script_dir, ignore_errors=True)

        for p in resultados:
            self.assertEqual(p.returncode, 0,
                              f"stdout={p.stdout!r} stderr={p.stderr!r}")
            self.assertIn("OK", p.stdout)

        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations') IS NULL AS sin_sm")
            self.assertTrue(cur.fetchone()["sin_sm"],
                            "an empty database boots straight into Alembic — "
                            "the old runner's bookkeeping table never appears")
            cur.execute("SELECT version_num FROM alembic_version")
            versiones = [r["version_num"] for r in cur.fetchall()]
            self.assertEqual(versiones, [_alembic_head()],
                              "el esquema tiene que quedar aplicado UNA vez, sin duplicados")
            for idx in ("booking_requests_doctor_slot_uniq",
                        "appointment_offers_live_slot_uniq",
                        "booking_requests_idem_key_uniq"):
                cur.execute("SELECT 1 FROM pg_indexes WHERE indexname=%s", (idx,))
                self.assertIsNotNone(cur.fetchone(), f"falta el índice {idx}")


class TestFreshDatabaseBootstrap(unittest.TestCase):
    """Item 10 — el control del item 8: una base COMPLETAMENTE vacía,
    a single bootstrap — Alembic lands at head and the three guard indexes
    exist."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.db_name = _dbtest.run_scoped("sana_turnos_test_integrity_fresh")
        self.db_url = _fresh_db_url(self.db_name)

    def tearDown(self):
        if HAVE_DB:
            _drop_db(self.db_name)

    def test_bootstrap_desde_cero_deja_todos_los_guards_presentes(self):
        db_schema._ensured.clear()
        try:
            with _env.patched_env(DATABASE_URL=self.db_url):
                conn = db._connect()
                conn.close()
        finally:
            db_schema._ensured.clear()

        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations') IS NULL AS sin_sm")
            self.assertTrue(cur.fetchone()["sin_sm"],
                            "a freshly created database never gets the old "
                            "runner's bookkeeping table")
            cur.execute("SELECT version_num FROM alembic_version")
            self.assertEqual(cur.fetchone()["version_num"], _alembic_head())

            for idx in ("booking_requests_doctor_slot_uniq",
                        "appointment_offers_live_slot_uniq",
                        "booking_requests_idem_key_uniq"):
                cur.execute("SELECT 1 FROM pg_indexes WHERE indexname=%s", (idx,))
                self.assertIsNotNone(cur.fetchone(), f"falta el índice {idx}")

            for tabla in ("booking_requests", "appointment_offers", "slot_holds"):
                cur.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name=%s", (tabla,))
                self.assertIsNotNone(cur.fetchone(), f"falta la tabla {tabla}")


if __name__ == "__main__":
    unittest.main()
