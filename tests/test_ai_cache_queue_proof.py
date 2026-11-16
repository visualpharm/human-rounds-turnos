"""test_ai_cache_queue_proof.py — zero-egress / zero-spend proof for the
batch-3 AI-cache warmups on the worker topology (Tier 4 periodic-daemon
batch 3, 2026-08-08). The batch-2 outbound proof
(test_business_queue_proof.py) covered the notification queues; this is
the same discipline for the two OUTBOUND-capable cache fills: the REAL
`chart_summary.tick()` and `translate_warmup.sweep()` bodies, consumed by
the REAL `python3 worker.py` process, against a disposable per-module
Postgres, with the fail-closed outbound kill switch
(core/outbound_guard.py, TURNOS_OUTBOUND_KILL=1) armed AND a dummy
OpenRouter key CONFIGURED on purpose: a provider call must be ATTEMPTED
and BLOCKED, never silently skipped — an unconfigured provider "passes"
this proof vacuously (docs/solutions/deploy/
candado-de-salida-fail-closed-prueba-colas-negocio.md).

What the one worker boot proves (the startup sweeps make it immediate —
no cron seams, no waiting for a boundary):

- The guard's BLOQUEADO line appears: a real connect toward the provider
  was attempted and denied at the socket, before any packet left.
- chart_summary degrades exactly as designed: the seeded pair's summary
  row EXISTS with ai_model='fallback' — the marker for "provider
  attempted and failed, deterministic build used". 'fake',
  'sin_clave_api' or 'ai_no_disponible' would each mean the provider
  path was never exercised, so the test pins 'fallback' specifically.
- translate_warmup degrades to zero: with the provider unreachable the
  sweep translates nothing (TranslationUnavailable is swallowed per
  batch/string) and writes NO cache rows — no fake markers, no spend.
- Both jobs still SUCCEED: provider failure is degradation, never a
  failed job row.
- Zero provider spend, by construction: no packet left the process
  (socket-level default-deny), so no billable call can exist.

The suite seeds fictional rows only (DNI prefix 999…), purges them, and
asserts emptiness at the end — same hygiene as the batch-2 proof.

Run:  python3 -W error::ResourceWarning -m unittest discover -s tests \
      -p "test_ai_cache_queue_proof.py" -v
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
import _wait  # noqa: E402
_env.process_defaults(TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""      # never read the real .env (same trick as the siblings)

from app.core import db_schema  # noqa: E402
from app.core import queue_infra  # noqa: E402
from app.ai import chart_summary_queue        # noqa: E402
from app.ops import translate_warmup_queue    # noqa: E402

import _dbtest      # noqa: E402
import _workerproc  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_ai_cache_proof")

HAVE_DB = False
DB_SKIP_REASON = ""
_TEST_URL = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()
    # Only this process's workers: a parallel shard's live worker is no leak.
    leaked = _workerproc.leaked_worker_pids()
    if leaked:
        raise AssertionError(
            f"worker.py still running after the suite (PIDs {leaked})")


_DNI = "99900021"


class TestAiCacheOutboundProof(unittest.TestCase):
    """One real worker boot under the armed kill switch with a configured
    dummy provider key: both AI-cache startup sweeps run, attempt, get
    blocked at the socket, and degrade exactly as their thread twins
    would with the provider down."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        if os.environ.get("DATABASE_URL") != _TEST_URL:
            db_schema._ensured.clear()
            self.addCleanup(db_schema._ensured.clear)   # runs after the URL is put back
            _env.set_env(self, DATABASE_URL=_TEST_URL)
        queue_infra.ensure_schema()
        self._procs = []
        self.addCleanup(self._kill_procs)
        self.addCleanup(self._purge_synthetic)

    def _kill_procs(self):
        for p in self._procs:
            p.cleanup()

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _spawn(self, extra=None):
        env = _workerproc.worker_env(_TEST_URL, {
            # The proof's whole point: FAKE OFF + a CONFIGURED (dummy) key,
            # so the provider path is genuinely exercised and the kill
            # switch is what stops it — never a silent skip.
            "TURNOS_FAKE_AI": "0",
            "OPENROUTER_API_KEY": "sk-or-dummy-proof-key",
            "TURNOS_OUTBOUND_KILL": "1",
            "LANGUAGES": "es,en",
            "TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
            "TURNOS_HOUSEKEEPING_OFF": "1", "TURNOS_REFERRAL_SWEEP_OFF": "1",
            **(extra or {}),
        })
        # Capture value-free await locations if this real worker again misses
        # its existing shutdown deadline; diagnostics do not extend the budget.
        p = _workerproc.WorkerProc(env, diagnostic=True)
        self._procs.append(p)
        return p

    def _startup_status(self, task_name):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT status FROM procrastinate_jobs "
                        "WHERE task_name=%s AND queueing_lock=%s",
                        (task_name, f"startup:{task_name}"))
            return [r[0] for r in cur.fetchall()]

    def _in_flight(self, *task_names):
        """Jobs of these tasks not yet finished — startup defers AND any
        periodic tick: a worker booted within procrastinate's 10-minute
        max_delay after a cron boundary (the hourly chart-summary tick at
        :00) defers that missed tick at once, right behind the startup one."""
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT task_name, status FROM procrastinate_jobs "
                        "WHERE task_name = ANY(%s) AND status IN ('todo', 'doing')",
                        (list(task_names),))
            return cur.fetchall()

    def _purge_synthetic(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM patient_summaries WHERE dni=%s", (_DNI,))
            _dbtest.delete_booking_requests(cur, "dni=%s", (_DNI,))
            cur.execute("DELETE FROM translations WHERE lang='en'")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM patient_summaries WHERE dni=%s",
                        (_DNI,))
            resto = cur.fetchone()[0]
        assert resto == 0, "the proof must leave zero synthetic rows behind"

    def test_armed_worker_attempts_blocks_and_degrades_without_spend(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, assigned_doctor_id, "
                "  appointment_at, created) "
                "VALUES (%s, 'Prueba', 'ProofAiCache', 'cardiologia', "
                "  'hospital-pinamar', 'aprobada', 'medico-proof', "
                "  now() + interval '4 hours', now())", (_DNI,))

        w = self._spawn()
        w.wait_line("candado de salida armado")
        w.wait_line("corriendo")

        # translate_warmup's startup sweep keeps its 30 s boot grace
        # (scheduled defer), so the deadline covers grace + polling.
        try:
            _wait.until(lambda: self._startup_status(chart_summary_queue._TASK_NAME) == ["succeeded"]
                        and self._startup_status(translate_warmup_queue._TASK_NAME) == ["succeeded"],
                        "both startup sweeps succeed", timeout=90, interval=0.3)
        except AssertionError:
            pass    # the assertions below say which sweep did not, with the worker's stderr
        resumen = self._startup_status(chart_summary_queue._TASK_NAME)
        catalogo = self._startup_status(translate_warmup_queue._TASK_NAME)
        self.assertEqual(resumen, ["succeeded"],
                         "the chart-summary sweep must SUCCEED under the "
                         "armed switch — provider failure is degradation, "
                         f"never a failed job (stderr: {w.lines[-15:]})")
        self.assertEqual(catalogo, ["succeeded"],
                         "the translation sweep must SUCCEED under the armed "
                         f"switch (stderr: {w.lines[-15:]})")

        # 1. The egress attempt happened and was denied AT THE SOCKET.
        bloqueos = [ln for ln in w.lines if "BLOQUEADO: connect" in ln]
        self.assertTrue(bloqueos,
                        "at least one real connect toward the provider must "
                        "have been attempted and blocked — without this the "
                        f"proof is vacuous. stderr: {w.lines}")

        # 2. chart_summary degraded via the ATTEMPTED-provider path.
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*), max(ai_model) FROM patient_summaries "
                        "WHERE dni=%s", (_DNI,))
            n, modelo = cur.fetchone()
        self.assertEqual(n, 1, "the pre-generated summary row exists")
        self.assertEqual(modelo, "fallback",
                         "'fallback' = provider attempted and failed, "
                         "deterministic build used; any other marker means "
                         "the provider path was never exercised")

        # 3. translate_warmup wrote NOTHING: no reachable provider, no
        #    fake markers, no rows — and therefore no spend anywhere.
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM translations WHERE lang='en'")
            self.assertEqual(cur.fetchone()[0], 0,
                             "a blocked provider fills nothing — and never "
                             "with fake markers")

        # Stop at a quiet queue: a sync job already running in its thread
        # cannot be interrupted, so a SIGTERM that lands mid-tick waits for
        # the whole tick's provider attempts and missed the shutdown budget.
        tasks = (chart_summary_queue._TASK_NAME, translate_warmup_queue._TASK_NAME)
        try:
            _wait.until(lambda: not self._in_flight(*tasks), "the warmup jobs go quiet",
                        timeout=120, interval=0.3)
        except AssertionError:
            pass    # the assertion below reports it, with the worker's stderr
        self.assertEqual(self._in_flight(*tasks), [],
                         f"the warmup jobs never went quiet (stderr: {w.lines[-15:]})")
        w.stop()
