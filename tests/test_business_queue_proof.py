"""test_business_queue_proof.py — safe activation proof for the BUSINESS
queues on the worker topology (Tier 4 follow-up card, 2026-08-08: the
predecessor worker-topology slice closed with an explicit caveat — the demo
soak proved process topology with a maintenance marker only, waitlist/
reminders queue behavior was proven only in-process. This suite is the
missing half: the REAL `waitlist.tick()` and `reminders.tick()/
tick_interview()` handlers, consumed by the REAL `python3 worker.py`
process, against a disposable per-module Postgres, with the fail-closed
outbound kill switch (core/outbound_guard.py, TURNOS_OUTBOUND_KILL=1)
armed so no email/WhatsApp/webhook can leave even though the transports
are CONFIGURED (dummy Mailgun creds on purpose — a send is ATTEMPTED and
must be BLOCKED, not silently skipped).

What each part proves:

  A. TestOutboundGuard (no DB) — the kill switch itself: default-deny
     (an external connect raises before any packet leaves), loopback and
     the DATABASE_URL endpoint pass, mailgun.send under the guard attempts
     and degrades to False (never raises), and the FAIL-CLOSED contract:
     worker.main() refuses to run (EXIT_CONFIG) when the switch is
     requested but cannot arm.
  B. TestBusinessQueueProof (real DB + real subprocess) — seeded fictional
     rows only (DNI prefix 999…): each business sweep job is consumed
     EXACTLY once (procrastinate_events started=1), produces its durable
     transition (reminder_at claimed; expired offer -> 'expirada' + next
     candidate's offer row 'ofrecida'), a SIGKILLed worker's replacement
     re-sweeps without duplicates (claim-CAS holds across processes), two
     workers racing one job execute it once, and the suite leaves ZERO
     synthetic rows behind (explicit purge + a final emptiness assertion).

Determinism note: reminders.tick() only acts inside 09:00–21:00 LOCAL
(installation tz). The worker subprocess gets a TURNOS_TIMEZONE chosen at
runtime (Etc/GMT±N) so "now" is always ~12:00 local — no wall-clock
flakiness, no production code touched.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
      discover -s tests -p "test_business_queue_proof.py" -v
"""
import os
import socket
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
import _wait  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""      # no leer el .env real (mismo truco que test_queue_worker.py)

from app.core import db               # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.core import outbound_guard   # noqa: E402
from app.core import queue_infra      # noqa: E402
from app.modules.turnos import waitlist        # noqa: E402
from app.modules.turnos import waitlist_queue  # noqa: E402
from app.notifications import mailgun          # noqa: E402
from app.notifications import reminders_queue  # noqa: E402
from app import worker                     # noqa: E402

import _dbtest       # noqa: E402
import _workerproc   # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_bqproof")

HAVE_DB = False
DB_SKIP_REASON = ""
_TEST_URL = ""

# Todos los datos sembrados usan este prefijo de DNI — la prueba final de
# "cero filas sintéticas huérfanas" barre por él.
_DNI_PREFIX = "999"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()
    # Solo los workers de ESTE proceso: el de otro shard paralelo no es fuga.
    leaked = _workerproc.leaked_worker_pids()
    if leaked:
        raise AssertionError(
            f"worker.py sigue corriendo tras la suite (PIDs {leaked}) — "
            "algún test no limpió su proceso")


# --------------------------------------------------------------------------
# A. El candado en sí — sin DB.
# --------------------------------------------------------------------------

class TestOutboundGuard(unittest.TestCase):
    """El switch falla CERRADO: default-deny en connect, y un worker que lo
    pide pero no lo puede armar se niega a correr."""

    def setUp(self):
        self.addCleanup(outbound_guard.disarm)
        _env.set_env(self, {outbound_guard.FLAG: None})

    def test_requested_parses_like_worker_mode(self):
        for valor, esperado in (("1", True), ("1 ", True), (" 1", True),
                                 ("", False), ("0", False), ("external", False)):
            with _env.patched_env({outbound_guard.FLAG: valor}):
                self.assertEqual(outbound_guard.requested(), esperado, valor)
        with _env.patched_env({outbound_guard.FLAG: None}):
            self.assertFalse(outbound_guard.requested())

    def test_default_deny_blocks_external_connect_before_any_packet(self):
        outbound_guard.arm()
        antes = outbound_guard.blocked_count()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        # 203.0.113.0/24 es TEST-NET-3 (RFC 5737): jamás ruteable — y el
        # guard tiene que rechazar ANTES de intentar salir, así que el test
        # no depende de eso.
        with self.assertRaises(outbound_guard.OutboundBlocked):
            s.connect(("203.0.113.9", 443))
        self.assertEqual(outbound_guard.blocked_count(), antes + 1)

    def test_connect_ex_sendto_and_sendmsg_also_deny(self):
        outbound_guard.arm()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        with self.assertRaises(outbound_guard.OutboundBlocked):
            s.connect_ex(("203.0.113.9", 443))
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(u.close)
        with self.assertRaises(outbound_guard.OutboundBlocked):
            u.sendto(b"x", ("203.0.113.9", 53))
        m = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(m.close)
        # sendmsg con destino explícito — la ruta que la revisión
        # adversarial (2026-08-08) probó abierta antes de este parche.
        with self.assertRaises(outbound_guard.OutboundBlocked):
            m.sendmsg([b"x"], [], 0, ("203.0.113.9", 53))

    def test_loopback_only_on_db_ports(self):
        """Loopback NO es un pase libre: WAHA corre en puertos loopback en
        despliegues reales. Sólo el puerto de la DB pasa."""
        with _env.patched_env({"DATABASE_URL":
                                      "postgresql://u:p@127.0.0.1:5432/x"}):
            with patch.object(db, "_db_url", lambda: os.environ["DATABASE_URL"]):
                outbound_guard.arm()
        # Un listener loopback en un puerto efímero (≠5432): BLOQUEADO.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(srv.close)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(c.close)
        with self.assertRaises(outbound_guard.OutboundBlocked):
            c.connect(srv.getsockname())
        # El puerto de la DB en loopback: pasa el guard (si no hay Postgres
        # escuchando, el error es refused/timeout — NUNCA OutboundBlocked).
        c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(c2.close)
        c2.settimeout(0.2)
        try:
            c2.connect(("127.0.0.1", 5432))
        except outbound_guard.OutboundBlocked:
            self.fail("loopback en el puerto de la DB no debe bloquearse")
        except OSError:
            pass

    def test_database_url_endpoint_is_allowlisted(self):
        # Un literal IP no-loopback como host de DB: getaddrinfo lo devuelve
        # tal cual (sin DNS). El guard lo deja pasar — y el connect real
        # falla por timeout/refused, NUNCA por OutboundBlocked.
        with _env.patched_env({"DATABASE_URL":
                                      "postgresql://u:p@203.0.113.7:5432/x"}):
            with patch.object(db, "_db_url", lambda: os.environ["DATABASE_URL"]):
                outbound_guard.arm()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        s.settimeout(0.05)
        try:
            s.connect(("203.0.113.7", 5432))
        except outbound_guard.OutboundBlocked:
            self.fail("el endpoint de DATABASE_URL no debe bloquearse")
        except OSError:
            pass  # timeout/refused: el guard lo dejó pasar, que es lo probado
        # …y el MISMO host en otro puerto sigue negado.
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s2.close)
        with self.assertRaises(outbound_guard.OutboundBlocked):
            s2.connect(("203.0.113.7", 9999))

    def test_mailgun_attempt_is_blocked_and_degrades_to_false(self):
        """Con credenciales PRESENTES (el envío se intenta de verdad), el
        candado corta el connect y mailgun.send devuelve False sin levantar
        — el contrato best-effort de todos los notify_* sobrevive."""
        outbound_guard.arm()
        antes = outbound_guard.blocked_count()
        # MAIL_ENABLED=1 explícito: varias suites vecinas exportan "0" a
        # nivel módulo y en un discover completo eso llega hasta acá — sin
        # esto el send se saltea ANTES de conectar y el test pasa vacío.
        with _env.patched_env({"MAILGUN_API_KEY": "clave-de-prueba",
                                      "MAILGUN_DOMAIN": "mg.proof.invalid",
                                      "MAIL_ENABLED": "1"}):
            ok = mailgun.send("nadie@example.invalid", "prueba", "cuerpo")
        self.assertFalse(ok)
        self.assertGreater(outbound_guard.blocked_count(), antes)

    def test_fail_closed_worker_refuses_to_run_when_arm_fails(self):
        _env.set_env(self, {outbound_guard.FLAG: "1"})
        with patch.object(outbound_guard, "arm",
                           side_effect=RuntimeError("no pude")):
            self.assertEqual(worker.main(), worker.EXIT_CONFIG)

    def test_disarm_restores_originals(self):
        outbound_guard.arm()
        outbound_guard.disarm()
        self.assertFalse(outbound_guard.armed())
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        s.settimeout(0.05)
        try:
            s.connect(("203.0.113.9", 443))
        except outbound_guard.OutboundBlocked:
            self.fail("desarmado no debe seguir bloqueando")
        except OSError:
            pass  # timeout esperado: el connect salió del guard


# --------------------------------------------------------------------------
# B. Las colas de negocio reales, en el worker real, con el candado armado.
# --------------------------------------------------------------------------

def _choose_tz():
    """Una zona Etc/GMT±N donde AHORA son ~12:00 locales — así la ventana
    09:00–21:00 de reminders.tick() está siempre abierta, sin tocar código
    de producción ni el reloj."""
    h = datetime.now(timezone.utc).hour
    x = (12 - h) % 24
    # POSIX invierte el signo: Etc/GMT-5 = UTC+5.
    name = ("Etc/GMT" if x == 0 else
            f"Etc/GMT-{x}" if x <= 14 else f"Etc/GMT+{24 - x}")
    return name, ZoneInfo(name)


def _pg():
    import psycopg
    return psycopg.connect(_TEST_URL, connect_timeout=5)


def _jobs_by_task(task_name):
    with _pg() as c, c.cursor() as cur:
        cur.execute("SELECT id, status FROM procrastinate_jobs "
                    "WHERE task_name=%s ORDER BY id", (task_name,))
        return cur.fetchall()


def _started_events(job_id):
    with _pg() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM procrastinate_events "
                    "WHERE job_id=%s AND type='started'", (job_id,))
        return cur.fetchone()[0]


def _wait_jobs_succeeded(task_name, n, timeout=45):
    """Espera hasta que task_name tenga >= n jobs y TODOS terminales; falla
    si alguno terminó 'failed'."""
    vistas = []

    def terminales():
        vistas[:] = _jobs_by_task(task_name)
        return len(vistas) >= n and all(st in ("succeeded", "failed") for _i, st in vistas)

    try:
        _wait.until(terminales, f"los jobs de {task_name} terminan", timeout=timeout, interval=0.25)
    except AssertionError:
        raise AssertionError(f"jobs de {task_name} nunca terminaron: {_jobs_by_task(task_name)}") from None
    malos = [(i, st) for i, st in vistas if st == "failed"]
    if malos:
        raise AssertionError(f"jobs de {task_name} fallaron: {malos}")
    return list(vistas)


def _defer_sweeps(waitlist_ts=None, reminders_ts=None):
    """Encola barridos SIN worker corriendo (durabilidad): la misma app y el
    mismo register() que usa worker.py."""
    app = queue_infra.build_app()
    w = waitlist_queue.register(app, 30)
    r = reminders_queue.register(app, 30)
    app.open()
    try:
        if waitlist_ts is not None:
            w.defer(timestamp=waitlist_ts)
        if reminders_ts is not None:
            r.defer(timestamp=reminders_ts)
    finally:
        app.close()


class TestBusinessQueueProof(unittest.TestCase):
    _seq = 0

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        if os.environ.get("DATABASE_URL") != _TEST_URL:
            db_schema._ensured.clear()
            self.addCleanup(db_schema._ensured.clear)   # after the environment is put back
            _env.set_env(self, DATABASE_URL=_TEST_URL)
        queue_infra.ensure_schema()
        with _pg() as c, c.cursor() as cur:
            cur.execute("TRUNCATE procrastinate_jobs, procrastinate_events, "
                        "procrastinate_periodic_defers RESTART IDENTITY CASCADE")
            c.commit()
        self.tz_name, self.tz = _choose_tz()
        self._procs = []
        self.addCleanup(self._kill_procs)
        self.addCleanup(self._purge_synthetic)

    def _kill_procs(self):
        for p in self._procs:
            p.cleanup()

    def _spawn(self, extra=None):
        env = {"TURNOS_OUTBOUND_KILL": "1",
               "TURNOS_TIMEZONE": self.tz_name,
               # Credenciales FALSAS a propósito, para AMBOS transportes: el
               # envío se intenta de verdad y el candado lo corta — no un
               # skip silencioso por desconfigurado (hallazgo 2 de la
               # revisión adversarial: sin proveedor WA configurado,
               # _whatsapp_reminder hacía no-op y WhatsApp nunca se probaba).
               "MAILGUN_API_KEY": "clave-de-prueba",
               "MAILGUN_DOMAIN": "mg.proof.invalid",
               "MAIL_ENABLED": "1",
               # Mail with links is not sent without a public origin; the
               # attempt must reach the transport for the lock to cut it.
               "TURNOS_PUBLIC_ORIGIN": "https://example.health.invalid",
               "TURNOS_WA_PROVIDER": "meta",
               "META_WA_TOKEN": "token-de-prueba",
               "META_WA_PHONE_NUMBER_ID": "999000111",
               # Hospital-initiated WhatsApp is off unless switched on
               # (2026-10-21): on here so the reminder really tries.
               "TURNOS_WA_NOTIFY": "1",
               "TURNOS_WAITLIST_INTERVAL_MIN": "30",
               "TURNOS_REMINDER_INTERVAL_MIN": "30"}
        env.update(extra or {})
        p = _workerproc.WorkerProc(_workerproc.worker_env(_TEST_URL, env))
        self._procs.append(p)
        return p

    # -- siembra de datos FICTICIOS (prefijo 999) ---------------------------

    def _dni(self):
        TestBusinessQueueProof._seq += 1
        return f"{_DNI_PREFIX}{os.getpid() % 10000:04d}{self._seq:03d}"

    def _tomorrow_local(self, hour):
        manana = (datetime.now(self.tz) + timedelta(days=1)).date()
        return datetime(manana.year, manana.month, manana.day, hour, 0,
                        tzinfo=self.tz).astimezone(timezone.utc)

    def _seed_reminder_row(self):
        dni = self._dni()
        db_accounts.create_user(dni, "Prueba", "Sintética", "1980-01-01", "F",
                        f"wa:{dni}", "x", email=f"proof-{dni}@example.invalid")
        sid = db_booking.create_booking_request(dni, "Prueba", "Sintética",
                                          "1980-01-01", f"wa:{dni}",
                                          "cardiologia", "hospital-pinamar",
                                          solicitante_dni=dni)
        db_booking.approve(sid, self._tomorrow_local(12), {"estado": "skip"})
        return sid, dni

    def _seed_waitlist_scenario(self):
        """Un cupo liberado con una oferta 'ofrecida' YA VENCIDA para C1 y
        una candidata C2 esperando: tick() debe expirar la de C1 y crear la
        de C2 — las dos transiciones durables del motor real."""
        slot = self._tomorrow_local(15)
        lejos = slot + timedelta(days=7)
        ids = {}
        for tag in ("c1", "c2"):
            dni = self._dni()
            sid = db_booking.create_booking_request(dni, "Espera", tag.upper(),
                                              "1975-05-05", f"wa:{dni}",
                                              "cardiologia", "hospital-pinamar",
                                              solicitante_dni=dni)
            db_booking.approve(sid, lejos, {"estado": "skip"})
            wid = waitlist.opt_in(sid, dni, f"proof-{dni}@example.invalid",
                                    "cardiologia", "hospital-pinamar", lejos)
            ids[tag] = {"sid": sid, "dni": dni, "wid": wid}
        ahora = datetime.now(timezone.utc)
        oferta_c1 = db_slots.create_appointment_offer(
            slot, "cardiologia", "hospital-pinamar", ids["c1"]["wid"],
            f"tok-{ids['c1']['dni']}", ahora - timedelta(hours=2),
            ahora - timedelta(hours=1))
        self.assertIsNotNone(oferta_c1)
        ids["oferta_c1"] = oferta_c1
        ids["slot"] = slot
        return ids

    def _purge_synthetic(self):
        """Borra TODO lo sembrado (por prefijo de DNI) — la mitad "sin filas
        huérfanas" de la evidencia. test_zz_no_synthetic_rows_left asserta
        que el barrido realmente dejó cero."""
        with _pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM appointment_offers WHERE waitlist_id IN "
                        "(SELECT id FROM waitlist WHERE dni LIKE %s)",
                        (_DNI_PREFIX + "%",))
            cur.execute("DELETE FROM waitlist WHERE dni LIKE %s", (_DNI_PREFIX + "%",))
            _dbtest.delete_booking_requests(cur, "dni LIKE %s", (_DNI_PREFIX + "%",))
            cur.execute("DELETE FROM users WHERE dni LIKE %s", (_DNI_PREFIX + "%",))
            c.commit()

    def _reminder_at(self, sid):
        with _pg() as c, c.cursor() as cur:
            cur.execute("SELECT reminder_at FROM booking_requests WHERE id=%s", (sid,))
            return cur.fetchone()[0]

    # -- los casos ----------------------------------------------------------

    def test_sweeps_consume_once_durable_transitions_no_outbound(self):
        sid, _dni = self._seed_reminder_row()
        wl = self._seed_waitlist_scenario()
        _defer_sweeps(waitlist_ts=1, reminders_ts=1)

        p = self._spawn()
        p.wait_line("candado de salida armado")
        p.wait_line("worker '", timeout=30)

        jobs_r = _wait_jobs_succeeded(reminders_queue._TASK_NAME, 1)
        jobs_w = _wait_jobs_succeeded(waitlist_queue._TASK_NAME, 1)
        for job_id, _st in jobs_r + jobs_w:
            self.assertEqual(_started_events(job_id), 1,
                             f"job {job_id} debe ejecutarse EXACTAMENTE una vez")

        # Transición durable 1: el recordatorio quedó reclamado (claim-CAS).
        self.assertIsNotNone(self._reminder_at(sid))

        # Transición durable 2: la oferta vencida de C1 expiró y C2 tiene la
        # SUYA — exactamente una viva para ese cupo.
        with _pg() as c, c.cursor() as cur:
            cur.execute("SELECT status FROM appointment_offers WHERE id=%s",
                        (wl["oferta_c1"],))
            self.assertEqual(cur.fetchone()[0], "expirada")
            cur.execute("""SELECT waitlist_id FROM appointment_offers
                           WHERE service_id='cardiologia' AND status='ofrecida'
                             AND slot_at=%s""", (wl["slot"],))
            vivas = cur.fetchall()
            self.assertEqual(len(vivas), 1)
            self.assertEqual(vivas[0][0], wl["c2"]["wid"])

        # Sin salida: los envíos se INTENTARON por los DOS transportes
        # (mailgun y Meta WhatsApp, ambos con credenciales puestas) y el
        # candado los cortó — evidencia en el stderr del worker real.
        p.wait_line("BLOQUEADO", timeout=10)
        p.wait_line("send FAILED", timeout=10)          # mailgun intentó
        p.wait_line("Meta WhatsApp plantilla turnos_recordatorio FAILED", timeout=10)  # WhatsApp intentó
        self.assertFalse(any("-> HTTP 2" in l for l in p.lines),
                         "ningún envío pudo salir con el candado armado")
        self.assertGreaterEqual(
            sum("BLOQUEADO" in l for l in p.lines), 2,
            "los dos transportes deben haber sido cortados por el candado")
        self.assertEqual(p.stop(), 0)

    def test_sigkill_and_restart_never_duplicates(self):
        sid, _dni = self._seed_reminder_row()
        _defer_sweeps(reminders_ts=1)

        p1 = self._spawn()
        p1.wait_line("candado de salida armado")
        jobs = _wait_jobs_succeeded(reminders_queue._TASK_NAME, 1)
        marca = self._reminder_at(sid)
        self.assertIsNotNone(marca)

        # Muerte violenta y reemplazo: el barrido siguiente NO re-procesa la
        # fila ya reclamada ni re-ejecuta el job ya cerrado.
        p1._killpg()
        p1.proc.wait(10)
        _defer_sweeps(reminders_ts=2)
        p2 = self._spawn()
        p2.wait_line("candado de salida armado")
        _wait_jobs_succeeded(reminders_queue._TASK_NAME, 2)

        for job_id, _st in _jobs_by_task(reminders_queue._TASK_NAME):
            self.assertEqual(_started_events(job_id), 1)
        self.assertEqual(self._reminder_at(sid), marca,
                         "el reclamo no debe re-escribirse tras el reinicio")
        self.assertFalse(any("send FAILED" in l for l in p2.lines),
                         "el segundo barrido no debe intentar ningún envío: "
                         "la fila ya estaba reclamada")
        self.assertEqual(p2.stop(), 0)
        self.assertEqual(jobs[0][1], "succeeded")

    def test_two_workers_one_business_job_single_execution(self):
        sid, _dni = self._seed_reminder_row()
        p1 = self._spawn()
        p2 = self._spawn()
        p1.wait_line("candado de salida armado")
        p2.wait_line("candado de salida armado")
        _defer_sweeps(reminders_ts=3)
        filas = _wait_jobs_succeeded(reminders_queue._TASK_NAME, 1)
        self.assertEqual(_started_events(filas[0][0]), 1,
                         "dos workers compitiendo: el job corre UNA vez")
        self.assertIsNotNone(self._reminder_at(sid))
        self.assertEqual(p1.stop(), 0)
        self.assertEqual(p2.stop(), 0)

    def test_zz_no_synthetic_rows_left(self):
        """Corre último (orden alfabético de unittest): tras los purges de
        cada test, NINGUNA fila sintética queda en ninguna tabla tocada."""
        for tabla, filtro in (("booking_requests", "dni LIKE %s"),
                               ("waitlist", "dni LIKE %s"),
                               ("users", "dni LIKE %s")):
            with _pg() as c, c.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {tabla} WHERE {filtro}",
                            (_DNI_PREFIX + "%",))
                self.assertEqual(cur.fetchone()[0], 0, tabla)
        with _pg() as c, c.cursor() as cur:
            cur.execute("""SELECT count(*) FROM appointment_offers ao
                           JOIN waitlist w ON w.id = ao.waitlist_id
                           WHERE w.dni LIKE %s""", (_DNI_PREFIX + "%",))
            self.assertEqual(cur.fetchone()[0], 0, "appointment_offers")


if __name__ == "__main__":
    unittest.main()
