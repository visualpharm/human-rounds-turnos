"""test_queue_worker.py — Tier 4 worker-topology slice proof tests
(docs/roadmap/tier4-replatform-design.md, "Worker topology"): the queue
worker as its OWN process (`worker.py`), outside the web process. Six
properties, each proven directly:

  1. Delegation — TURNOS_QUEUE_WORKER set means the WEB process starts
     NOTHING for the migrated sweeps: not the thread, not the slice 5
     in-process queue worker — for waitlist AND reminders, with the OFF
     switches still winning and the default (flag unset) byte-identical
     to before (TestDelegationMatrix, no DB needed).
  2. Supervisor — asgi_main spawns worker.py as a child process only when
     TURNOS_QUEUE_WORKER=1, spawns nothing for the retired "external"/unset, and its
     shutdown hook SIGTERMs the child (TestSupervisor, no DB needed —
     subprocess.Popen mocked).
  3. Composition — worker.py registers BOTH migrated jobs on ONE app with
     the same task names and cadence derivation their in-process twins
     use, honoring each job's own OFF flag (TestWorkerComposition).
  4. Lifecycle — the real `python3 worker.py` process comes up, answers
     /health and /ready, and a SIGTERM exits 0 gracefully
     (TestWorkerProcessLifecycle, real subprocess + real Postgres).
  5. Durability — a job deferred while NO worker runs survives in
     `procrastinate_jobs` and executes exactly once when a worker later
     starts; a second worker racing the first never double-executes it
     (TestStoppedWorkerDurability / TestTwoWorkerContention, real
     subprocesses, execution count read from `procrastinate_events`).
  6. Periodic uniqueness — two workers' periodic deferrers deferring the
     SAME cron tick produce ONE job (`procrastinate_periodic_defers`'s
     unique constraint, exercised through procrastinate's own
     defer_periodic API — the exact call path both deferrers use)
     (TestPeriodicTickUniqueness).
  7. Registry — worker._JOBS is the one declarative surface for what runs
     and what each job may touch: capability sets pinned exactly, empty
     re-run contracts refused, and the env-only `--jobs` audit works with
     no DB (TestJobRegistry); every registered queue has a
     mid-task-SIGKILL re-run proof (TestBatch2CrashRecovery,
     TestWaitlistCrashRecovery, TestRemindersCrashRecovery).

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
      discover -s tests -p "test_queue_worker.py" -v
"""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
import _wait  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""      # no leer el .env real (mismo truco que test_integrity_races.py)

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.core import db_analytics  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.core import queue_infra  # noqa: E402
from app.ai import chart_summary                  # noqa: E402
from app.ai import chart_summary_queue            # noqa: E402
from app.connectors.hsi import hsi_client         # noqa: E402
from app.modules.turnos import hsi_fallback as hsi_resync        # noqa: E402
from app.modules.turnos import hsi_journal as hsi_sync           # noqa: E402
from app.modules.turnos import hsi_journal_queue as hsi_sync_queue  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import referrals_queue     # noqa: E402
from app.modules.turnos import waitlist           # noqa: E402
from app.modules.turnos import waitlist_queue     # noqa: E402
from app.notifications import reminders           # noqa: E402
from app.notifications import reminders_queue     # noqa: E402
from app.ops import housekeeping                  # noqa: E402
from app.ops import housekeeping_queue            # noqa: E402
from app.ops import data_backups_queue            # noqa: E402
from app.ops import translate_warmup              # noqa: E402
from app.ops import translate_warmup_queue        # noqa: E402

import _dbtest            # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_queue_worker")


def _until(condition, what="the condition", timeout=30.0, interval=0.01):
    """`_wait.until` with the ceiling read as a quiet machine's (`scale_with_load`): every wait of this
    file is on a real worker process or a real supervisor thread, which run at the machine's speed."""
    return _wait.until(condition, what, timeout=timeout, interval=interval, scale_with_load=True)

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
    # Prueba de no-fuga (2026-08-08, tras un worker.py huérfano con PPID 1
    # que colgó al harness): la suite FALLA si dejó algún worker.py de ESTE
    # checkout corriendo. Solo cuentan los de ESTE proceso (hijos suyos o
    # huérfanos): el worker vivo de otro shard paralelo de la suite no es fuga.
    leaked = _workerproc.leaked_worker_pids()
    if leaked:
        raise AssertionError(
            f"worker.py sigue corriendo tras la suite (PIDs {leaked}) — "
            "algún test no limpió su proceso")


class _FakeJobManager:
    """Minimal faithful stand-in for procrastinate's per-app
    `manager.JobManager` (the real `App.__init__` builds ONE per app, so
    `_guard_heartbeat_update_cancellation`'s boot-time rebind of
    `update_heartbeat` stays app-local exactly as in production). Every
    fake app driven through `_boot_and_run`/`main()` needs one because the
    guard reads and wraps the async `update_heartbeat(worker_id)`
    coroutine on every constructed worker. The REAL-manager cancellation
    proof lives in test_worker_periodic_cancellation.py — these doubles
    only carry the boot-time contract."""

    async def update_heartbeat(self, worker_id):
        return None


_FLAGS = ("TURNOS_QUEUE_WORKER", "TURNOS_WAITLIST_QUEUE",
          "TURNOS_WAITLIST_OFF", "TURNOS_LISTA_ESPERA_OFF",
          "TURNOS_REMINDERS_OFF", "TURNOS_RECORDATORIOS_OFF",
          "TURNOS_HOUSEKEEPING_OFF", "TURNOS_HOUSEKEEPING_INTERVAL_H",
          "TURNOS_REFERRAL_SWEEP_OFF", "TURNOS_REFERRAL_SWEEP_INTERVAL_H",
          "TURNOS_REFERRAL_RETENTION_DAYS",
          "TURNOS_BACKUP_OFF", "TURNOS_BACKUP_INTERVAL_MIN",
          "TURNOS_B2_KEY_ID", "TURNOS_B2_APP_KEY", "TURNOS_B2_BUCKET_ID",
          "TURNOS_WORKER_STALLED_S", "TURNOS_TEST_UNSAFE_STALLED",
          "TURNOS_TEST_SEAMS", "TURNOS_TEST_SWEEP_RELEASE_GATE_KEY",
          "TURNOS_TEST_SWEEP_RELEASE_GATE_TIMEOUT_S",
          "TURNOS_TEST_HSI_REKICK_GATE_TIMEOUT_S",
          # batch 3 (AI-cache warmups): the two jobs' own flags plus
          # LANGUAGES, which decides translate_warmup's eligibility — the
          # matrix must state it explicitly, never inherit the shell's.
          "TURNOS_SUMMARY_PREGEN", "TURNOS_SUMMARY_TICK_MIN",
          "TURNOS_TRANSLATE_WARMUP_OFF", "LANGUAGES",
          # hsi_sync is the only HSI job in the registry; hsi_resync stays
          # thread-only/manual even though it reuses the durable journal.
          "TURNOS_HSI_SYNC_OFF", "CONNECTOR_EMR_SYNC")


class _FlagSandbox(unittest.TestCase):
    """Backs up + clears every topology flag around each test, so the
    matrix below states EXACTLY which flags are set — never inherits one
    from the developer's shell."""

    def setUp(self):
        _env.set_env(self, {k: None for k in _FLAGS})


class TestDelegationMatrix(_FlagSandbox):
    """The worker-topology non-negotiable: with TURNOS_QUEUE_WORKER set,
    the web process starts NO sweep mechanism at all — the worker
    owns them. One if/elif chain per job module decides; these prove every
    branch combination reaches exactly one mechanism (or none), never two."""

    def test_worker_mode_is_the_one_parser_and_normalizes(self):
        # Revisión Fable 2026-08-08, hallazgo 1: un "1 " con espacio (paste
        # de Coolify) hacía que asgi_main lanzara el worker Y los módulos
        # web arrancaran sus hilos — doble barrido. Un solo parser.
        for crudo, esperado in (("1", "1"), ("1 ", "1"), (" external", ""),
                                 ("external", ""), ("", ""), ("0", ""),
                                 ("yes", ""), ("  ", "")):
            _env.set_env(self, TURNOS_QUEUE_WORKER=crudo)
            self.assertEqual(queue_infra.worker_mode(), esperado,
                             f"worker_mode({crudo!r})")
        _env.set_env(self, TURNOS_QUEUE_WORKER=None)
        self.assertEqual(queue_infra.worker_mode(), "")

    def test_worker_mode_cannot_be_enabled_by_db_backed_config(self):
        # Topology is process placement and only the process env is
        # authoritative: a settings override saying `1` is intentionally
        # ignored (no DB query is needed to decide it).
        _env.set_env(self, TURNOS_QUEUE_WORKER=None)
        with patch.object(config, "get", return_value="1") as get:
            self.assertEqual(queue_infra.worker_mode(), "")
        get.assert_not_called()

    def test_waitlist_delegates_no_thread_no_inprocess_queue(self):
        for modo in ("1", "1 "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch.object(waitlist, "_start_thread") as mock_thread, \
                 patch.object(waitlist, "_start_queue") as mock_queue:
                waitlist.start()
            mock_thread.assert_not_called()
            mock_queue.assert_not_called()

    def test_waitlist_delegation_wins_over_inprocess_queue_flag(self):
        # Both flags set: the old (slice 5) in-process path and the
        # worker path are mutually exclusive BY CONSTRUCTION — the
        # delegation check comes first, so the in-process worker never
        # starts alongside the supervised one.
        _env.set_env(self, TURNOS_QUEUE_WORKER="1", TURNOS_WAITLIST_QUEUE="1")
        with patch.object(waitlist, "_start_thread") as mock_thread, \
             patch.object(waitlist, "_start_queue") as mock_queue:
            waitlist.start()
        mock_thread.assert_not_called()
        mock_queue.assert_not_called()

    def test_waitlist_off_switch_wins_over_delegation(self):
        _env.set_env(self, TURNOS_WAITLIST_OFF="1", TURNOS_QUEUE_WORKER="1")
        with patch.object(waitlist, "_start_thread") as mock_thread, \
             patch.object(waitlist, "_start_queue") as mock_queue:
            waitlist.start()
        mock_thread.assert_not_called()
        mock_queue.assert_not_called()

    def test_waitlist_flag_unset_keeps_thread_exactly_as_before(self):
        with patch.object(waitlist, "_start_thread") as mock_thread, \
             patch.object(waitlist, "_start_queue") as mock_queue:
            waitlist.start()
        mock_thread.assert_called_once()
        mock_queue.assert_not_called()

    def test_reminders_delegates_no_thread(self):
        for modo in ("1", "1 "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch.object(reminders.threading, "Thread") as mock_thread:
                reminders.start()
            mock_thread.assert_not_called()

    def test_reminders_off_switch_wins_over_delegation(self):
        _env.set_env(self, TURNOS_REMINDERS_OFF="1", TURNOS_QUEUE_WORKER="1")
        with patch.object(reminders.threading, "Thread") as mock_thread:
            reminders.start()
        mock_thread.assert_not_called()

    def test_reminders_flag_unset_keeps_thread_exactly_as_before(self):
        with patch.object(reminders.threading, "Thread") as mock_thread:
            reminders.start()
        mock_thread.assert_called_once()

    # -- batch 2: housekeeping + referral retention -----------------------

    def test_housekeeping_delegation_keeps_disk_check_web_side(self):
        # Round-8 gate: with the worker owning the purges, the web
        # process still runs the disk check on its legacy schedule — a
        # single REDUCED thread whose target is _disk_loop, never the
        # full _loop.
        for modo in ("1", "1 "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch.object(housekeeping.threading, "Thread") as mock_thread:
                housekeeping.start()
            mock_thread.assert_called_once()
            self.assertIs(mock_thread.call_args.kwargs["target"],
                          housekeeping._disk_loop,
                          "delegated mode must start the disk-only loop")

    def test_housekeeping_off_switch_wins_over_delegation(self):
        _env.set_env(self, TURNOS_HOUSEKEEPING_OFF="1", TURNOS_QUEUE_WORKER="1")
        with patch.object(housekeeping.threading, "Thread") as mock_thread:
            housekeeping.start()
        mock_thread.assert_not_called()

    def test_housekeeping_flag_unset_keeps_thread_exactly_as_before(self):
        with patch.object(housekeeping.threading, "Thread") as mock_thread:
            housekeeping.start()
        mock_thread.assert_called_once()
        self.assertIs(mock_thread.call_args.kwargs["target"], housekeeping._loop,
                      "no-queue mode keeps the FULL legacy loop (purges + disk)")

    def test_referral_sweep_delegates_no_thread(self):
        for modo in ("1", "1 "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch.object(booking_referrals.threading, "Thread") as mock_thread:
                booking_referrals.start_referral_sweep()
            mock_thread.assert_not_called()

    def test_referral_sweep_off_switch_wins_over_delegation(self):
        _env.set_env(self, TURNOS_REFERRAL_SWEEP_OFF="1", TURNOS_QUEUE_WORKER="1")
        with patch.object(booking_referrals.threading, "Thread") as mock_thread:
            booking_referrals.start_referral_sweep()
        mock_thread.assert_not_called()

    def test_referral_retention_disabled_wins_over_delegation(self):
        # retention<=0 is an explicit opt-out: neither thread nor
        # delegation, with or without worker mode.
        for modo in ("", "1"):
            if modo:
                _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            _env.set_env(self, TURNOS_REFERRAL_RETENTION_DAYS="0")
            with patch.object(booking_referrals.threading, "Thread") as mock_thread:
                booking_referrals.start_referral_sweep()
            mock_thread.assert_not_called()

    def test_referral_sweep_flag_unset_keeps_thread_exactly_as_before(self):
        with patch.object(booking_referrals.threading, "Thread") as mock_thread:
            booking_referrals.start_referral_sweep()
        mock_thread.assert_called_once()

    # -- batch 3: the AI-cache warmups ------------------------------------

    def test_chart_summary_delegates_no_thread(self):
        for modo in ("1", "1 "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch.object(chart_summary.threading, "Thread") as mock_thread:
                self.assertIsNone(chart_summary.start())
            mock_thread.assert_not_called()

    def test_chart_summary_off_switch_wins_over_delegation(self):
        _env.set_env(self, TURNOS_SUMMARY_PREGEN="0", TURNOS_QUEUE_WORKER="1")
        with patch.object(chart_summary.threading, "Thread") as mock_thread:
            self.assertIsNone(chart_summary.start())
        mock_thread.assert_not_called()

    def test_chart_summary_flag_unset_keeps_thread_exactly_as_before(self):
        with patch.object(chart_summary.threading, "Thread") as mock_thread:
            chart_summary.start()
        mock_thread.assert_called_once()
        self.assertIs(mock_thread.call_args.kwargs["target"],
                      chart_summary._loop)

    def test_translate_warmup_delegates_no_thread(self):
        for modo in ("1", "1 "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch.object(translate_warmup, "pending_languages",
                              return_value=["en"]), \
                 patch.object(translate_warmup.threading, "Thread") as mock_thread:
                self.assertFalse(translate_warmup.start())
            mock_thread.assert_not_called()

    def test_translate_warmup_off_switch_wins_over_delegation(self):
        _env.set_env(self, TURNOS_TRANSLATE_WARMUP_OFF="1", TURNOS_QUEUE_WORKER="1")
        with patch.object(translate_warmup, "pending_languages",
                          return_value=["en"]), \
             patch.object(translate_warmup.threading, "Thread") as mock_thread:
            self.assertFalse(translate_warmup.start())
        mock_thread.assert_not_called()

    def test_translate_warmup_no_second_language_starts_no_thread(self):
        # The i18n "true no-op until you opt in" contract: with nothing
        # to translate no THREAD ever starts. Without worker mode the
        # language check decides; with worker mode the delegation check
        # fires first (the worker re-resolves eligibility per tick) —
        # either way, no thread.
        for modo in ("", "1"):
            if modo:
                _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch.object(translate_warmup, "pending_languages",
                              return_value=[]), \
                 patch.object(translate_warmup.threading, "Thread") as mock_thread:
                self.assertFalse(translate_warmup.start())
            mock_thread.assert_not_called()

    def test_translate_warmup_flag_unset_keeps_thread_exactly_as_before(self):
        with patch.object(translate_warmup, "pending_languages",
                          return_value=["en"]), \
             patch.object(translate_warmup.threading, "Thread") as mock_thread:
            self.assertTrue(translate_warmup.start())
        mock_thread.assert_called_once()
        self.assertIs(mock_thread.call_args.kwargs["target"],
                      translate_warmup._loop)


class TestSupervisor(_FlagSandbox):
    """asgi_main._start_queue_worker_supervisor(): spawn behavior per flag
    value, with subprocess.Popen mocked — no real worker ever runs here."""

    def _fake_proc(self):
        ev = threading.Event()
        class _P:
            pid = 4242
            wait_timeouts = []
            def wait(self, timeout=None):
                if timeout is not None:
                    self.wait_timeouts.append(timeout)
                ev.wait(timeout if timeout is not None else 30)
                return 0
            def poll(self):
                return 0 if ev.is_set() else None
            def terminate(self):
                ev.set()
            def kill(self):
                ev.set()
        p = _P()
        p.wait_timeouts = []
        return p

    def test_flag_unset_spawns_nothing(self):
        from app import asgi_main
        with patch("subprocess.Popen") as mock_popen:
            self.assertIsNone(asgi_main._start_queue_worker_supervisor())
        mock_popen.assert_not_called()

    def test_retired_external_value_spawns_nothing_and_says_so(self):
        from app import asgi_main
        _env.set_env(self, TURNOS_QUEUE_WORKER="external")
        err = io.StringIO()
        with patch("subprocess.Popen") as mock_popen, contextlib.redirect_stderr(err):
            self.assertIsNone(asgi_main._start_queue_worker_supervisor())
        mock_popen.assert_not_called()
        self.assertIn("no es un modo válido", err.getvalue())

    def test_flag_1_spawns_worker_py_and_shutdown_terminates_it(self):
        from app import asgi_main
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        proc = self._fake_proc()
        # The supervisor thread calls Popen: polling `mock.called` raced the
        # mock's own bookkeeping (called=True while call_args was still None).
        # The side effect records the command, THEN signals.
        spawned, cmds = threading.Event(), []

        def _popen(cmd, *args, **kwargs):
            cmds.append(cmd)
            spawned.set()
            return proc

        with patch("subprocess.Popen", side_effect=_popen):
            apagar = asgi_main._start_queue_worker_supervisor()
            self.assertIsNotNone(apagar, "modo supervisado debe devolver su hook de apagado")
            self.assertTrue(spawned.wait(5), "nunca lanzó worker.py")
            cmd = cmds[0]
            self.assertEqual(cmd[0], sys.executable)
            self.assertTrue(cmd[1].endswith("worker.py"))
            apagar()  # debe SIGTERMear al hijo y no colgarse
            self.assertEqual(proc.poll(), 0)

    def test_shutdown_wait_follows_graceful_timeout_env(self):
        # Revisión Fable 2026-08-08, hallazgo 4: la espera del supervisor
        # deriva de TURNOS_WORKER_GRACEFUL_TIMEOUT (+5 de margen), no de un
        # 25 fijo — subir el drain del worker no debe hacer que lo maten a
        # mitad de camino.
        from app import asgi_main
        _env.set_env(self, TURNOS_QUEUE_WORKER="1", TURNOS_WORKER_GRACEFUL_TIMEOUT="37")
        proc = self._fake_proc()
        with patch("subprocess.Popen", return_value=proc) as mock_popen:
            apagar = asgi_main._start_queue_worker_supervisor()
            _until(lambda: mock_popen.called, "el supervisor lanzó worker.py", timeout=5)
            apagar()
        self.assertIn(52.0, proc.wait_timeouts,
                       f"la espera no siguió al drain configurado: {proc.wait_timeouts}")

    def test_shutdown_wait_covers_pool_open_bound_not_just_drain(self):
        # Round-9 review finding: with a SMALL graceful timeout (default
        # 20s), `drain + margin` (25s) used to be SHORTER than psycopg_
        # pool's own hardcoded pool-open bound (queue_infra.
        # POOL_OPEN_TIMEOUT_S, 30s) — a worker stuck opening its pool
        # during a Postgres blip at startup got SIGKILLed before it could
        # ever react to the graceful request. The supervisor's wait must
        # cover whichever phase's bound is larger.
        from app import asgi_main
        from app.core import queue_infra
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        # No TURNOS_WORKER_GRACEFUL_TIMEOUT set — exercises the default
        # (20s), which is shorter than pool open. Both sequential pool
        # closes and the final margin make the default budget 45 seconds.
        _env.set_env(self, TURNOS_WORKER_GRACEFUL_TIMEOUT=None)
        proc = self._fake_proc()
        with patch("subprocess.Popen", return_value=proc) as mock_popen:
            apagar = asgi_main._start_queue_worker_supervisor()
            _until(lambda: mock_popen.called, "el supervisor lanzó worker.py", timeout=5)
            apagar()
        expected = (queue_infra.POOL_OPEN_TIMEOUT_S
                    + queue_infra.POOL_CLOSE_TIMEOUT_S
                    + queue_infra.CORE_DB_POOL_CLOSE_TIMEOUT_S
                    + queue_infra.SUPERVISOR_SIGKILL_MARGIN_S)
        self.assertIn(expected, proc.wait_timeouts,
                       f"la espera no cubrió el bound de apertura del pool "
                       f"({expected}s): {proc.wait_timeouts}")

    def _exiting_proc(self, code):
        class _P:
            pid = 555
            def wait(self, timeout=None):
                return code
            def poll(self):
                return code
            def terminate(self):
                pass
            def kill(self):
                pass
        return _P()

    def test_config_exit_2_is_not_retried(self):
        # Ronda Codex 2026-08-08: config permanente inválida (exit 2) no
        # se reintenta — un loop de reinicios no arregla configuración.
        from app import asgi_main
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        with patch("subprocess.Popen", return_value=self._exiting_proc(2)) as mock_popen:
            apagar = asgi_main._start_queue_worker_supervisor()
            _until(lambda: mock_popen.called, "el supervisor lanzó worker.py", timeout=5)
            # ventana en la que un reintento (backoff 1-2 s) ya habría pasado
            _wait.stays(lambda: mock_popen.call_count == 1,
                        "exit 2 no debe reintentarse jamás", for_s=3)
            apagar()

    def test_transient_exit_1_is_retried(self):
        from app import asgi_main
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        with patch("subprocess.Popen", return_value=self._exiting_proc(1)) as mock_popen:
            apagar = asgi_main._start_queue_worker_supervisor()
            _until(lambda: mock_popen.call_count >= 2,
                        "exit 1 (transitorio) se reintenta con backoff", timeout=8)
            self.assertGreaterEqual(mock_popen.call_count, 2,
                                    "exit 1 (transitorio) debe reintentarse con backoff")
            apagar()

    def test_shutdown_during_spawn_never_orphans(self):
        # Revisión Fable 2026-08-08, hallazgo 3, cerrado con candado por
        # pedido del responsable del producto: si atexit corre mientras el supervisor está
        # DENTRO del Popen, _apagar espera el candado y baja al hijo recién
        # nacido — nunca queda huérfano.
        from app import asgi_main
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        proc = self._fake_proc()
        spawn_entered = threading.Event()
        spawn_gate = threading.Event()

        def _popen_lento(*a, **k):
            spawn_entered.set()
            if not spawn_gate.wait(10):
                raise AssertionError("el test nunca liberó el Popen")
            return proc

        with patch("subprocess.Popen", side_effect=_popen_lento):
            apagar = asgi_main._start_queue_worker_supervisor()
            self.assertTrue(spawn_entered.wait(5), "el supervisor nunca llegó al Popen")
            t = threading.Thread(target=apagar)
            t.start()
            # _apagar ahora bloqueado en el candado
            _wait.stays(t.is_alive, "el candado debía retener a _apagar durante el spawn", for_s=0.3)
            spawn_gate.set()  # el Popen retorna con el hijo ya nacido
            t.join(10)
            self.assertFalse(t.is_alive(), "_apagar no terminó tras liberar el spawn")
            self.assertEqual(proc.poll(), 0,
                             "el hijo nacido durante el apagado debe ser terminado, no huérfano")


class TestWorkerComposition(_FlagSandbox):
    """worker.py._register_jobs(): both migrated jobs land on the ONE app
    with their canonical task names; each job's own OFF flag excludes just
    that job. No connection is opened — registration is pure."""

    def _app(self):
        import procrastinate
        import procrastinate.testing
        return procrastinate.App(connector=procrastinate.testing.InMemoryConnector())

    def test_all_non_connector_jobs_registered_by_default(self):
        # translate_warmup is registered even with NO second language
        # enabled: its eligibility is per-tick (pending_languages() inside
        # the sweep body), never a boot-time registration decision — a
        # language enabled from the panel must reach the next sweep
        # without a worker restart.
        # HSI is explicit opt-in: a bare installation must not start its
        # network-mutating queue. hsi_resync stays purely on-thread/manual.
        from app import worker
        app = self._app()
        colas = worker._register_jobs(app)
        self.assertEqual(colas, ["waitlist", "reminders", "housekeeping",
                                  "referrals", "chart_summary",
                                  "translate_warmup", "maintenance"])
        self.assertIn(waitlist_queue._TASK_NAME, app.tasks)
        self.assertIn(reminders_queue._TASK_NAME, app.tasks)
        self.assertIn(housekeeping_queue._TASK_NAME, app.tasks)
        self.assertIn(referrals_queue._TASK_NAME, app.tasks)
        self.assertIn(chart_summary_queue._TASK_NAME, app.tasks)
        self.assertIn(translate_warmup_queue._TASK_NAME, app.tasks)
        self.assertNotIn(hsi_sync_queue._TASK_NAME, app.tasks)
        self.assertIn("worker.maintenance.remove_old_jobs", app.tasks)
        # The exclusive pair registers procrastinate's native job lock as
        # the TASK default — every defer path (cron tick, startup defer,
        # recovery requeue) inherits it; batch-2 tasks carry none.
        for tn in (chart_summary_queue._TASK_NAME,
                   translate_warmup_queue._TASK_NAME):
            self.assertEqual(app.tasks[tn].lock,
                             queue_infra.task_lock_name(tn), tn)
        for tn in (waitlist_queue._TASK_NAME, reminders_queue._TASK_NAME,
                   housekeeping_queue._TASK_NAME, referrals_queue._TASK_NAME):
            self.assertIsNone(app.tasks[tn].lock, tn)

    def test_hsi_job_is_registered_only_after_explicit_connector_selection(self):
        from app import worker
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        app = self._app()
        self.assertIn("hsi_sync", worker._register_jobs(app))
        self.assertIn(hsi_sync_queue._TASK_NAME, app.tasks)
        self.assertEqual(
            app.tasks[hsi_sync_queue._TASK_NAME].lock,
            queue_infra.task_lock_name(hsi_sync_queue._TASK_NAME),
        )

    def test_off_flags_exclude_each_job_individually(self):
        from app import worker
        casos = (
            ("TURNOS_WAITLIST_OFF", "waitlist", waitlist_queue._TASK_NAME),
            ("TURNOS_REMINDERS_OFF", "reminders", reminders_queue._TASK_NAME),
            ("TURNOS_HOUSEKEEPING_OFF", "housekeeping", housekeeping_queue._TASK_NAME),
            ("TURNOS_REFERRAL_SWEEP_OFF", "referrals", referrals_queue._TASK_NAME),
            ("TURNOS_TRANSLATE_WARMUP_OFF", "translate_warmup",
             translate_warmup_queue._TASK_NAME),
        )
        todas = ["waitlist", "reminders", "housekeeping", "referrals",
                 "chart_summary", "translate_warmup", "maintenance"]
        for flag, cola, task_name in casos:
            with _env.patched_env({flag: "1"}):
                app = self._app()
                esperadas = [c for c in todas if c != cola]
                self.assertEqual(worker._register_jobs(app), esperadas,
                                 f"flag {flag} must exclude only {cola}")
                self.assertNotIn(task_name, app.tasks)
        # chart_summary's OFF is =0, not =1 (the flag predates the batch):
        _env.set_env(self, TURNOS_SUMMARY_PREGEN="0")
        app = self._app()
        self.assertEqual(worker._register_jobs(app),
                         [c for c in todas if c != "chart_summary"])
        self.assertNotIn(chart_summary_queue._TASK_NAME, app.tasks)

    def test_hsi_sync_off_excludes_only_that_queue(self):
        # No legacy fallback registers in its place — hsi_resync stays
        # purely on-thread, never queue-registered in any topology.
        from app import worker
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi", TURNOS_HSI_SYNC_OFF="1")
        app = self._app()
        colas = worker._register_jobs(app)
        self.assertNotIn("hsi_sync", colas)
        self.assertNotIn(hsi_sync_queue._TASK_NAME, app.tasks)

    def test_referral_retention_zero_excludes_job_like_the_thread_path(self):
        # The thread's second gate (retention<=0 = explicit opt-out) applies
        # the same in the worker: without the OFF flag, retention 0 doesn't
        # register the job.
        from app import worker
        _env.set_env(self, TURNOS_REFERRAL_RETENTION_DAYS="0")
        app = self._app()
        self.assertEqual(worker._register_jobs(app),
                         ["waitlist", "reminders", "housekeeping",
                          "chart_summary", "translate_warmup", "maintenance"])
        self.assertNotIn(referrals_queue._TASK_NAME, app.tasks)

    def test_all_off_still_yields_explicit_nonempty_queue_list(self):
        # Fable review 2026-08-08, finding 2: queues=None means "consume
        # ALL queues", and a worker without the task registered marks
        # failed any foreign job it picks up. The queue list is ALWAYS
        # explicit and never empty (maintenance included).
        from app import worker
        _env.set_env(self,
                     TURNOS_WAITLIST_OFF="1",
                     TURNOS_REMINDERS_OFF="1",
                     TURNOS_HOUSEKEEPING_OFF="1",
                     TURNOS_REFERRAL_SWEEP_OFF="1",
                     TURNOS_SUMMARY_PREGEN="0",
                     TURNOS_TRANSLATE_WARMUP_OFF="1",
                     TURNOS_HSI_SYNC_OFF="1",
                     TURNOS_HSI_RESYNC_OFF="1")
        app = self._app()
        self.assertEqual(worker._register_jobs(app), ["maintenance"])

    def test_batch2_cadence_derives_from_module_defaults(self):
        # The two batch-2 sweeps are HOURLY cadence (default 24h); the cron
        # derives from the SAME helpers the thread uses, and exact 24h
        # yields the daily cron.
        self.assertEqual(
            queue_infra.cron_for_interval(housekeeping._interval_h() * 60),
            "0 0 * * *")
        self.assertEqual(
            queue_infra.cron_for_interval(
                booking_referrals._referral_sweep_interval_h() * 60),
            "0 0 * * *")

    def test_batch2_unrepresentable_hours_reject_at_registration(self):
        # 5h doesn't divide 24 (0 */5 resets at the day boundary → a sweep
        # more frequent than configured): exact-or-reject, the ValueError
        # bubbles up to worker.main() which exits with the permanent config
        # code.
        from app import worker
        _env.set_env(self, TURNOS_HOUSEKEEPING_INTERVAL_H="5")
        with self.assertRaises(ValueError):
            worker._register_jobs(self._app())
        _env.set_env(self,
                     TURNOS_HOUSEKEEPING_INTERVAL_H=None,
                     TURNOS_REFERRAL_SWEEP_INTERVAL_H="7")
        with self.assertRaises(ValueError):
            worker._register_jobs(self._app())

    def test_cron_for_interval_hourly_and_above_exact_or_rejected(self):
        # Revisión Fable 2026-08-08, hallazgo 6 + corrección del responsable del producto: 60 ya
        # no se clampéa a */59 (:00+:59 = doble cadencia), y un intervalo
        # NO representable (90, 2880) se RECHAZA — jamás se aproxima a un
        # barrido MÁS frecuente que el configurado.
        from datetime import datetime, timezone

        from croniter import croniter
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Cada intervalo aceptado >= 60 se PRUEBA exacto con croniter (24
        # disparos > un día entero — atrapa el reinicio en el borde del
        # día, el defecto 1380 del re-review), no por enumeración a mano.
        for interval_min, esperado in ((60, "0 * * * *"), (120, "0 */2 * * *"),
                                        (180, "0 */3 * * *"), (480, "0 */8 * * *"),
                                        (720, "0 */12 * * *"), (1440, "0 0 * * *")):
            cron = queue_infra.cron_for_interval(interval_min)
            self.assertEqual(cron, esperado)
            it = croniter(cron, base)
            fires = [it.get_next(datetime) for _ in range(30)]
            gaps = {(fires[i + 1] - fires[i]).total_seconds() / 60
                    for i in range(len(fires) - 1)}
            self.assertEqual(gaps, {interval_min},
                             f"interval={interval_min}: gaps {sorted(gaps)}")
        # Sub-60: SÓLO divisores de 60 (ronda Codex 2026-08-08: */45
        # reinicia en el borde de la hora — hueco de 15 min, más rápido
        # que lo configurado; */59 tenía el mismo defecto).
        for divisor in (1, 2, 5, 30):
            self.assertEqual(queue_infra.cron_for_interval(divisor),
                             f"*/{divisor} * * * *")
        # No representable → rechazo: sub-60 no divisores (7, 45, 59),
        # horas que no dividen 24 (*/5, */23 reinician en el borde del
        # día), y todo lo demás.
        for no_representable in (7, 45, 59, 90, 61, 300, 420, 1380, 2880):
            with self.assertRaises(ValueError, msg=f"interval={no_representable}"):
                queue_infra.cron_for_interval(no_representable)

    def test_cron_for_interval_rejects_fractional_and_sub_one(self):
        # Review fix (2026-08-09): `max(1, int(interval_min))` used to
        # silently TRUNCATE (5.9 -> 5) and CLAMP (0.5 -> 1) instead of
        # rejecting — both the exact faster-than-configured defect class
        # this function's own docstring already bans for non-divisor
        # integers. hsi_sync._interval_min() returns a float (preserving
        # the thread's own fractional-minute test-seam support), which is
        # what first exposed this: the thread path can run at 0.05 min,
        # the queue path must refuse to approximate that as "every 1 min".
        for fraccional in (5.9, 0.5, 2.3, 10.0001):
            with self.assertRaises(ValueError, msg=f"interval={fraccional}"):
                queue_infra.cron_for_interval(fraccional)
        # A float that IS a whole number (5.0) is equivalent to int 5 —
        # accepted, not rejected merely for being a float.
        self.assertEqual(queue_infra.cron_for_interval(5.0), "*/5 * * * *")
        for sub_one in (0, -1, -60):
            with self.assertRaises(ValueError, msg=f"interval={sub_one}"):
                queue_infra.cron_for_interval(sub_one)

    def test_hsi_sync_register_accepts_fractional_intervals(self):
        # Review fix (2026-08-09): hsi_sync_queue.register no longer goes
        # through queue_infra.cron_for_interval AT ALL — it self-
        # reschedules (see that module's own docstring, "Why
        # self-rescheduling, not periodic cron"), so there is no cron
        # grid to approximate and therefore nothing to reject merely for
        # being fractional. This test used to assert the OPPOSITE (a
        # fractional TURNOS_HSI_SYNC_INTERVAL_MIN caused worker.py to
        # exit 2) — that was true only while hsi_sync used the shared
        # cron-based registration path; it stopped being true the moment
        # this module switched designs, and the old test would have hung
        # waiting for an exit that no longer happens (reproduced: a real
        # 30s subprocess timeout) rather than failing loudly.
        app = self._app()
        hsi_sync_queue.register(app, 5.9)  # must not raise
        self.assertIn(hsi_sync_queue._TASK_NAME, app.tasks)

    def test_hsi_sync_register_rejects_non_positive_interval(self):
        # The ONE value self-rescheduling genuinely cannot support: <= 0
        # would schedule_in immediately, forever. Proven at both layers —
        # the Python-level ValueError, and worker.py's real exit-2
        # contract end to end (main() already catches ValueError from
        # _register_jobs and maps it to EXIT_CONFIG; this pins that hsi_sync's
        # OWN register() is what raises it now that it no longer routes
        # through queue_infra.cron_for_interval).
        app = self._app()
        for malo in (0, -1, -5.9):
            with self.assertRaises(ValueError, msg=f"interval={malo}"):
                hsi_sync_queue.register(app, malo)

        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        env = {"PATH": os.environ.get("PATH", ""),
              "TURNOS_ENV_FILE": os.devnull,
              "TURNOS_QUEUE_WORKER": "1",
              "TURNOS_FAKE_AI": "1",
              "DATABASE_URL": _TEST_URL,
              # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — without
              # it hsi_sync never registers, so TURNOS_HSI_SYNC_INTERVAL_MIN
              # below would never even reach register()'s ValueError.
              "CONNECTOR_EMR_SYNC": "hsi",
              "TURNOS_HSI_SYNC_INTERVAL_MIN": "0"}
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "app", "worker.py")],
            capture_output=True, text=True, timeout=_wait.scaled(30), cwd=ROOT, env=env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("configuración inválida", proc.stderr)

    def test_batch3_cadence_derives_from_module_defaults(self):
        # chart_summary default 60 min → hourly cron; translate_warmup's
        # fixed 6 h constant → 0 */6. Both derive from the SAME helpers
        # the thread loops use, never a second parse.
        self.assertEqual(
            queue_infra.cron_for_interval(chart_summary._tick_min()),
            "0 * * * *")
        self.assertEqual(
            queue_infra.cron_for_interval(translate_warmup._interval_min()),
            "0 */6 * * *")

    def test_batch3_malformed_tick_falls_back_like_siblings(self):
        # Adversarial review finding 1: a malformed TURNOS_SUMMARY_TICK_MIN
        # must fall back to the default (waitlist/reminders convention),
        # never crash — `worker.py --jobs` is the tool an operator runs
        # to DEBUG a bad env var.
        _env.set_env(self, TURNOS_SUMMARY_TICK_MIN="abc")
        self.assertEqual(chart_summary._tick_min(), 60)
        from app import worker
        data = worker._audit_jobs()
        por_id = {j["id"]: j for j in data["jobs"]}
        self.assertEqual(por_id["chart_summary"]["cadence"], "every 60 min")

    def test_batch3_unrepresentable_tick_rejects_at_registration(self):
        # TURNOS_SUMMARY_TICK_MIN=45 is not exactly expressible (would
        # sweep faster than configured at the hour boundary): exact-or-
        # reject, the ValueError bubbles to worker.main()'s exit 2. The
        # THREAD path keeps accepting any interval, unchanged.
        from app import worker
        _env.set_env(self, TURNOS_SUMMARY_TICK_MIN="45")
        with self.assertRaises(ValueError):
            worker._register_jobs(self._app())

    def test_reminders_cadence_derives_from_same_default_as_thread(self):
        # The thread loop's default is 30 min (reminders._DEFAULT_INTERVAL_MIN);
        # the queue path must derive its cron from the SAME number, not a
        # second hardcoded one.
        self.assertEqual(
            queue_infra.cron_for_interval(reminders._DEFAULT_INTERVAL_MIN),
            "*/30 * * * *")


class TestJobRegistry(_FlagSandbox):
    """The declarative job registry (registry-hardening round, 2026-08-08):
    worker._JOBS is the ONE surface declaring what runs and what each job
    may touch. These tests pin the derivations — a batch-3 job cannot
    join without declaring capabilities."""

    def test_registry_order_and_complete_contracts(self):
        from app import worker
        self.assertEqual([s.id for s in worker._JOBS],
                         ["backup_full", "waitlist", "reminders", "housekeeping", "referrals",
                          "data_backups", "chart_summary", "translate_warmup", "hsi_sync"])
        for spec in worker._JOBS:
            self.assertIsInstance(spec.filesystem, bool, spec.id)
            self.assertIsInstance(spec.outbound, bool, spec.id)
            self.assertTrue(spec.rerun.strip(),
                            f"{spec.id}: a job without a declared re-run "
                            "contract cannot join the registry")
            self.assertTrue(spec.queue and spec.task_name and spec.label,
                            spec.id)
            self.assertTrue(spec.off_reason.strip(), spec.id)

    def test_capability_sets_are_exact(self):
        # The whole point of declaring capabilities: the sets are pinned.
        # referrals and data backups are filesystem jobs (round 8 made
        # housekeeping purges-only); outbound = the notification pair (claim/status-
        # guarded sends) plus the batch-3 AI-cache warmups (their sweeps
        # may call the external AI provider); startup = exactly the two
        # warmups (the thread twins' boot tick preserved as a deferred
        # immediate sweep — batch 2 accepted boundary-first cadence at
        # its own gate).
        from app import worker
        self.assertEqual({s.id for s in worker._JOBS if s.filesystem},
                         {"backup_full", "referrals", "data_backups"})
        self.assertEqual({s.id for s in worker._JOBS if s.outbound},
                         {"backup_full", "waitlist", "reminders", "data_backups",
                          "chart_summary", "translate_warmup", "hsi_sync"})
        self.assertEqual({s.id for s in worker._JOBS if s.startup},
                         {"data_backups", "chart_summary", "translate_warmup", "hsi_sync"})
        # Every outbound-capable sweep that can double-spend/double-mutate
        # on overlap is exclusive (execution-lock serialized) — the two
        # AI-cache warmups plus hsi_sync (real network mutations).
        self.assertEqual({s.id for s in worker._JOBS if s.exclusive},
                         {"backup_full", "data_backups", "chart_summary", "translate_warmup", "hsi_sync"})
        # startup_delay_s preserves each thread twin's own boot grace —
        # pinned against the module constant, not a second hardcode.
        delays = {s.id: s.startup_delay_s for s in worker._JOBS if s.startup}
        self.assertEqual(delays["chart_summary"], 0)
        self.assertEqual(delays["translate_warmup"],
                         translate_warmup.FIRST_RUN_DELAY_S)
        self.assertEqual(delays["hsi_sync"], 0)
        self.assertEqual(delays["data_backups"], data_backups_queue._STARTUP_GRACE_S)

    def test_audit_interface_values_are_english(self):
        # Codex language gate: the machine-readable operator interface is
        # English in KEYS and VALUES (labels, off reasons, cadence,
        # rerun contracts). Two pins: the whole JSON payload is plain
        # ASCII (our English strings never need diacritics — any accented
        # character is a Spanish leak), and the known Spanish words that
        # used to appear are banned outright. Legacy LOG prose (worker
        # stderr lines) deliberately stays Spanish and is not this
        # test's scope.
        from app import worker
        payload = json.dumps(worker._audit_jobs(), ensure_ascii=False)
        payload.encode("ascii")  # raises on any diacritic leak
        for palabra in ("cada ", "desactivad", "retención", "retencion",
                        "órdenes", "higiene", "recordatorios"):
            self.assertNotIn(palabra, payload, palabra)
        for spec in worker._JOBS:
            spec.label.encode("ascii")
            spec.off_reason.encode("ascii")
            spec.rerun.encode("ascii")

    def test_task_and_queue_identities_are_unique(self):
        # Identity uniqueness (Codex gate): the recovery's allowed-task
        # scoping and the janitor's queue scoping both key on these names
        # — a duplicate identity would silently merge two jobs' contracts.
        from app import worker
        tasks = [s.task_name for s in worker._JOBS] + list(worker._MAINT_TASKS)
        self.assertEqual(len(tasks), len(set(tasks)),
                         f"duplicate task identity: {tasks}")
        colas = [s.queue for s in worker._JOBS] + [worker._MAINT_QUEUE]
        self.assertEqual(len(colas), len(set(colas)),
                         f"duplicate queue identity: {colas}")

    def test_allowed_task_names_track_enabled_registry_plus_maintenance(self):
        from app import worker
        self.assertEqual(
            worker._allowed_task_names(),
            {s.task_name for s in worker._JOBS if s.enabled()}
            | set(worker._MAINT_TASKS))
        _env.set_env(self, TURNOS_WAITLIST_OFF="1")
        self.assertNotIn(waitlist_queue._TASK_NAME, worker._allowed_task_names(),
                         "a disabled job's task must leave the allowed set")

    def test_jobs_audit_cli_needs_no_db_and_lists_everything(self):
        # `python3 worker.py --jobs` in a bare env: NO DATABASE_URL, no
        # mode — the audit must work anywhere the code exists. Exit 0,
        # valid JSON, every registry field present (English keys — this
        # is an operator interface, not log prose), maintenance included.
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "app", "worker.py"), "--jobs"],
            capture_output=True, text=True, timeout=_wait.scaled(60), cwd=ROOT,
            env={"PATH": os.environ.get("PATH", ""),
                 "TURNOS_ENV_FILE": os.devnull,
                 "TURNOS_FAKE_AI": "1", "TURNOS_SKIP_HSI": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual([j["id"] for j in data["jobs"]],
                         ["backup_full", "waitlist", "reminders", "housekeeping", "referrals",
                          "data_backups", "chart_summary", "translate_warmup", "hsi_sync"])
        for j in data["jobs"]:
            for campo in ("label", "queue", "task", "enabled",
                          "filesystem", "outbound", "startup",
                          "startup_delay_s", "exclusive",
                          "cadence", "rerun"):
                self.assertIn(campo, j, j["id"])
            if j["id"] in {"backup_full", "data_backups", "hsi_sync"}:
                self.assertFalse(
                    j["enabled"],
                    "unconfigured infrastructure stays asleep",
                )
            else:
                self.assertTrue(j["enabled"], f"{j['id']}: default = enabled")
        self.assertEqual(data["maintenance"]["queue"], "maintenance")
        self.assertEqual(len(data["maintenance"]["tasks"]), 4)
        self.assertTrue(data["maintenance"]["rerun"].strip())

    def test_jobs_audit_with_real_env_is_side_effect_clean(self):
        # Codex regression: with the project's REAL env (DATABASE_URL
        # present → config.get consults the settings overrides), the
        # audit used to leave the psycopg pool for the GC — a
        # PythonFinalizationError on stderr at interpreter shutdown.
        # Now: exit 0, VALID stdout, and stderr EMPTY under
        # -W error::ResourceWarning.
        env = dict(os.environ)
        env.setdefault("TURNOS_FAKE_AI", "1")
        env.setdefault("TURNOS_SKIP_HSI", "1")
        proc = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning",
             os.path.join(ROOT, "app", "worker.py"), "--jobs"],
            capture_output=True, text=True, timeout=_wait.scaled(60), cwd=ROOT, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "",
                         "the audit must leave NOTHING behind — clean stderr")
        json.loads(proc.stdout)

    def test_audit_reflects_off_flags(self):
        from app import worker
        _env.set_env(self, TURNOS_HOUSEKEEPING_OFF="1")
        data = worker._audit_jobs()
        por_id = {j["id"]: j for j in data["jobs"]}
        self.assertFalse(por_id["housekeeping"]["enabled"])
        self.assertIsNone(por_id["housekeeping"]["cadence"])
        self.assertTrue(por_id["waitlist"]["enabled"])


# --------------------------------------------------------- proceso real --

# Harness extracted to tests/_workerproc.py the day a second real-subprocess
# suite appeared (test_business_queue_proof.py) — these thin wrappers keep
# this module's names (and its per-module _TEST_URL binding) unchanged.
import _workerproc  # noqa: E402


def _worker_env(extra=None):
    return _workerproc.worker_env(_TEST_URL, extra)


class _WorkerProc(_workerproc.WorkerProc):
    def __init__(self, extra_env=None, diagnostic=False):
        super().__init__(_worker_env(extra_env), diagnostic=diagnostic)


class TestWorkerProcHarness(unittest.TestCase):
    def test_timeout_preserves_phase_evidence_before_killing_process_group(self):
        """A shutdown timeout must retain the evidence that explains it."""
        class _TimedOutProc:
            pid = 4242

            def __init__(self):
                self.wait_calls = 0
                self.signals = []

            def poll(self):
                return None

            def send_signal(self, sig):
                self.signals.append(sig)

            def wait(self, timeout):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise subprocess.TimeoutExpired(["worker.py"], timeout)
                return -signal.SIGKILL

        harness = object.__new__(_workerproc.WorkerProc)
        harness.proc = _TimedOutProc()
        harness.diagnostic = False
        harness.lines = ["startup phase", "async pool close phase"]
        killed = []
        harness._killpg = lambda: killed.append(True)

        with self.assertRaisesRegex(
                AssertionError,
                r"(?s)worker pid 4242 exceeded the 0\.01s shutdown budget;.*"
                r"startup phase.*async pool close phase"):
            harness.stop(timeout=0.01)

        self.assertEqual(harness.proc.signals, [signal.SIGTERM])
        self.assertEqual(killed, [True])
        self.assertEqual(harness.proc.wait_calls, 2)

    def test_diagnostic_timeout_requests_dump_before_existing_group_kill(self):
        class _TimedOutProc:
            pid = 4243
            args = ["python", "worker_diagnostics_bootstrap.py", "worker.py"]

            def __init__(self):
                self.wait_calls = 0
                self.signals = []

            def poll(self):
                return None

            def send_signal(self, sig):
                self.signals.append(sig)

            def wait(self, timeout):
                self.wait_calls += 1
                if self.wait_calls <= 2:
                    raise subprocess.TimeoutExpired(["worker.py"], timeout)
                return -signal.SIGKILL

        harness = object.__new__(_workerproc.WorkerProc)
        harness.proc = _TimedOutProc()
        harness.diagnostic = True
        harness._diagnostic_ready = threading.Event()
        harness._diagnostic_ready.set()
        harness.lines = []
        events = []
        original_send = harness.proc.send_signal
        harness.proc.send_signal = lambda sig: (events.append(sig), original_send(sig))
        harness._killpg = lambda: events.append("killpg")
        with self.assertRaisesRegex(AssertionError, "shutdown budget"):
            harness.stop(timeout=0.01)
        self.assertEqual(events, [signal.SIGTERM, signal.SIGUSR1, "killpg"])

    def test_default_workerproc_wait_is_the_45_second_budget_unchanged(self):
        class _Proc:
            pid = 4244
            args = ["python", "worker.py"]
            def __init__(self): self.waits = []
            def poll(self): return None
            def send_signal(self, sig): pass
            def wait(self, timeout): self.waits.append(timeout); return 0

        harness = object.__new__(_workerproc.WorkerProc)
        harness.proc = _Proc()
        harness.diagnostic = False
        harness.lines = []
        with patch("app.core.queue_infra.supervisor_sigkill_budget", return_value=45.0):
            self.assertEqual(harness.stop(), 0)
        self.assertEqual(harness.proc.waits, [45.0])

    def test_dump_is_never_sent_to_normal_or_unready_child(self):
        harness = object.__new__(_workerproc.WorkerProc)
        harness._diagnostic_ready = threading.Event()
        harness.proc = unittest.mock.Mock()
        harness.proc.poll.return_value = None
        for enabled, ready in ((False, False), (False, True), (True, False)):
            harness.diagnostic = enabled
            harness._diagnostic_ready.set() if ready else harness._diagnostic_ready.clear()
            self.assertFalse(harness.request_diagnostic_dump())
        harness.proc.send_signal.assert_not_called()

    def test_wait_line_ceiling_grows_with_the_machine_load(self):
        """A booting worker logs "corriendo" at the machine's speed: under a parallel run the
        quiet-machine ceiling is stretched by the load, and a real hang still fails, naming it."""
        start = time.monotonic()

        class _Lines(list):
            def __iter__(self):
                if time.monotonic() - start >= 0.1 and not len(self):
                    self.append("[worker] worker 'w' corriendo (colas: x)")
                return super().__iter__()

        harness = object.__new__(_workerproc.WorkerProc)
        harness.proc = unittest.mock.Mock()
        harness.proc.poll.return_value = None
        harness.lines = _Lines()
        with patch.object(_workerproc._wait, "load_factor", return_value=4.0):
            self.assertIn("corriendo", harness.wait_line("corriendo", timeout=0.05))
            with self.assertRaisesRegex(AssertionError,
                                        r"nunca logueó 'apagado'.*x load 4\.0"):
                harness.wait_line("apagado", timeout=0.01)

    def test_timeout_evidence_preserves_first_task_in_long_dump(self):
        harness = object.__new__(_workerproc.WorkerProc)
        harness.lines = ["[worker-diagnostic] await-chain:begin",
                         "await first-task prune_stalled_workers manager.py:973"]
        harness.lines.extend("polling coroutine" for _ in range(90))
        harness.lines.append("[worker-diagnostic] await-chain:end")
        self.assertIn("prune_stalled_workers", harness._failure_evidence())


def _http_get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _job_rows():
    # Sólo los jobs del barrido de waitlist — el janitor de mantenimiento
    # registra los suyos propios y no es lo que estas pruebas cuentan.
    import psycopg
    with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("SELECT id, task_name, status FROM procrastinate_jobs "
                    "WHERE task_name=%s ORDER BY id", (waitlist_queue._TASK_NAME,))
        return cur.fetchall()


def _started_events(job_id):
    import psycopg
    with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM procrastinate_events WHERE job_id=%s AND type='started'",
                    (job_id,))
        return cur.fetchone()[0]


def _reset_queue_tables():
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("TRUNCATE procrastinate_jobs, procrastinate_events, "
                    "procrastinate_periodic_defers RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE alert_claims")


# The MANUAL defers of these suites use small timestamps (1, 2, 3…);
# the PERIODIC catch-up defers of the real worker use the cron tick's
# unix ts (huge). This filter separates the two — without it, a test that
# runs right after UTC midnight counts the daily catch-up job as its own
# (reproduced 2026-08-08).
_MANUAL_TS = "(args->>'timestamp')::bigint < 1000"


def _defer_sweep_without_worker(timestamp=0):
    """Defers one waitlist sweep job through the real connector — the
    'work queued while the worker is down' half of the durability story."""
    app = queue_infra.build_app()
    sweep = waitlist_queue.register(app, 2)
    app.open()
    try:
        sweep.defer(timestamp=timestamp)
    finally:
        app.close()


class _DbProcTest(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        if os.environ.get("DATABASE_URL") != _TEST_URL:
            # per-module DB — same dance _dbtest documents
            db_schema._ensured.clear()
            self.addCleanup(db_schema._ensured.clear)   # runs after the URL is back (LIFO)
            _env.set_env(self, DATABASE_URL=_TEST_URL)
        queue_infra.ensure_schema()
        _reset_queue_tables()
        self._procs = []
        self.addCleanup(self._kill_procs)

    def _kill_procs(self):
        for p in self._procs:
            p.cleanup()

    def _spawn(self, extra_env=None, diagnostic=False):
        p = _WorkerProc(extra_env, diagnostic=diagnostic)
        self._procs.append(p)
        return p

    def _wait_heartbeat_stale(self, stalled_s):
        """Wait until no worker row has a heartbeat younger than `stalled_s` seconds: a SIGKILLed
        worker is now 'stalled' for the startup recovery (the test procrastinate's
        select_stalled_jobs_by_heartbeat applies)."""
        import psycopg

        def _stale():
            with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
                cur.execute("SELECT count(*) FROM procrastinate_workers "
                            "WHERE last_heartbeat >= now() - (%s || ' SECOND')::interval",
                            (str(stalled_s),))
                return cur.fetchone()[0] == 0
        _until(_stale, f"no worker heartbeat younger than {stalled_s}s")


class TestWorkerProcessLifecycle(_DbProcTest):
    """The real process: entrypoint, health, readiness, graceful SIGTERM."""

    def test_health_ready_and_graceful_sigterm(self):
        # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — this proof
        # wants the FULL default registry on /health, hsi_sync included.
        w = self._spawn({"CONNECTOR_EMR_SYNC": "hsi"})
        port = w.health_port()
        w.wait_line("corriendo")
        code, body = _http_get(port, "/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        # Registration is observable on the LIVE worker (registry-hardening
        # round): /health names the consumed queues (English key — an
        # operator interface).
        self.assertEqual(body.get("queues"),
                         ["waitlist", "reminders", "housekeeping",
                          "referrals", "chart_summary", "translate_warmup",
                          "hsi_sync", "maintenance"])
        # readiness necesita fase=corriendo + Postgres alcanzable
        _until(lambda: _http_get(port, "/ready")[0] == 200, "/ready dio 200", timeout=10)
        exit_code = w.stop(signal.SIGTERM)
        self.assertEqual(exit_code, 0, "SIGTERM debe terminar con gracia (exit 0)")
        w.wait_line("apagado con gracia", timeout=5)

    def test_double_sigint_during_shutdown_exits_via_force_kill_not_crash(self):
        """Independent-review finding, real and reproduced (not
        theorized): a rapid DOUBLE SIGINT against a real `worker.py`
        subprocess used to crash with an uncaught `KeyboardInterrupt` and
        a non-contract exit code. Root cause: Python's default SIGINT
        disposition (unlike SIGTERM's silent termination) is
        `signal.default_int_handler`, which RAISES `KeyboardInterrupt` as
        a normal Python exception at whatever point of execution the
        interpreter happens to be — the OLD second-signal mechanism
        (restore default disposition, let it fire) hit this for SIGINT
        specifically. Fixed: a second signal now calls `_force_exit()`
        (`os._exit()`) directly, uniformly for both signals — see
        docs/solutions/dev/worker-signal-ownership-not-task-cancel.md.

        Real window, not timing luck: a housekeeping sweep job is
        deferred with TURNOS_TEST_SWEEP_HOLD_S set, so once the worker
        picks it up (status='doing', confirmed in Postgres before either
        signal is sent) `Worker.run()` is genuinely inside its graceful
        drain wait when both SIGINTs land — squarely 'during
        cleanup/shutdown', the exact stretch the independent review named
        (main()'s `finally: httpd.shutdown()`, or anywhere downstream of
        the first signal). Run repeatedly (not once) — the original bug
        was found via repeated real-process runs."""
        import psycopg
        from app import worker

        N = 6
        results = []
        for i in range(N):
            app = queue_infra.build_app()
            sweep = housekeeping_queue.register(app, 1440)
            app.open()
            try:
                sweep.defer(timestamp=i + 1)
            finally:
                app.close()

            w = self._spawn({
                "TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
                "TURNOS_TEST_SEAMS": "1",
                # Long enough that the drain is still genuinely in
                # progress when the second SIGINT lands; the force-kill
                # path must exit WITHOUT waiting for either of these.
                "TURNOS_TEST_SWEEP_HOLD_S": "20",
                "TURNOS_WORKER_GRACEFUL_TIMEOUT": "25",
            })
            w.wait_line("corriendo")

            # Startup carries real DB-round-trip overhead of its own
            # (the other queues' own startup sweeps — chart_summary,
            # translate_warmup, hsi_sync — dedup-check and sometimes
            # enqueue right after "corriendo" too), so the housekeeping
            # fetch can take a while to actually get scheduled; a short
            # deadline here made this flaky, not the fix (confirmed by
            # instrumented reruns: exit_code was always 3, clean, even on
            # the runs where 'doing' took past 20s) — 40s matches this
            # suite's own _wait_status default elsewhere.
            with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
                def _doing():
                    cur.execute(
                        "SELECT status FROM procrastinate_jobs "
                        f"WHERE task_name=%s AND {_MANUAL_TS} ORDER BY id",
                        (housekeeping_queue._TASK_NAME,))
                    row = cur.fetchone()
                    return row and row[0] == "doing"
                _until(_doing, f"cycle {i}: held sweep reached 'doing'", timeout=40)

            # The second SIGINT goes out only once the worker has COUNTED the first (its log line).
            # Two standard signals sent 150 ms apart to a process that has not yet run its handler
            # for the first (a busy machine) are one pending signal to the kernel: the worker sees a
            # single SIGINT, starts the graceful drain it was meant to cut short, and the test waits
            # out its 15 s for an exit that cannot come.
            w.proc.send_signal(signal.SIGINT)
            w.wait_line("recording graceful shutdown", timeout=15)
            t0 = time.monotonic()
            w.proc.send_signal(signal.SIGINT)
            try:
                exit_code = w.proc.wait(_wait.scaled(15))
            except subprocess.TimeoutExpired:
                w._killpg()
                w.proc.wait(10)
                raise AssertionError(
                    f"cycle {i}: never exited after double SIGINT — the "
                    f"force-kill path must not wait for the held job or "
                    f"the graceful timeout; stderr:\n" + "\n".join(w.lines))
            elapsed = time.monotonic() - t0
            stderr = w.output_after_exit()
            results.append((exit_code, elapsed, stderr))
            # Full reset, not just this task's rows: os._exit() abandons
            # whatever the OTHER registered queues' own startup sweeps
            # (chart_summary/translate_warmup/hsi_sync) were doing too,
            # leaving THEIR rows 'doing' forever from a dead worker's
            # perspective. Left alone, the next cycle's worker sees an
            # already-queued job for those and skips re-enqueuing
            # (dedup-by-queueing-lock) — never blocking housekeeping
            # itself, but confirmed by an instrumented rerun to
            # occasionally starve ITS fetch for the full deadline instead
            # (procrastinate's own scheduling, not a defect in this fix).
            # A clean slate per cycle removes that as a variable.
            _reset_queue_tables()

        for i, (exit_code, elapsed, stderr) in enumerate(results):
            self.assertNotIn("Traceback", stderr,
                             f"cycle {i}: uncaught exception crash:\n{stderr}")
            self.assertNotIn("KeyboardInterrupt", stderr,
                             f"cycle {i}: raw KeyboardInterrupt leaked:\n{stderr}")
            self.assertIn("segunda señal recibida", stderr,
                          f"cycle {i}: missing the deliberate force-exit "
                          f"log line:\n{stderr}")
            self.assertEqual(exit_code, worker.EXIT_FORCE_KILL,
                             f"cycle {i}: must exit via EXIT_FORCE_KILL "
                             f"(3), not a raw signal-kill or crash "
                             f"code:\n{stderr}")
            # os._exit() is immediate — it must NOT wait anywhere near
            # the 20s hold or the 25s graceful timeout.
            self.assertLess(elapsed, 5,
                            f"cycle {i}: force-kill took {elapsed:.1f}s — "
                            f"should be near-instant (os._exit bypasses "
                            f"the drain entirely)")

    def test_worker_registers_heartbeat_row_in_postgres(self):
        """The Postgres-side liveness signal: procrastinate 3.x keeps a
        heartbeat row per named worker — the census an operator (or the web
        side, if it ever wants to) reads WITHOUT a network path to the
        worker process."""
        import psycopg
        w = self._spawn()
        w.wait_line("corriendo")
        def _vivos():
            with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
                cur.execute("SELECT count(*) FROM procrastinate_workers")
                return cur.fetchone()[0]
        _until(lambda: _vivos() >= 1, "el worker registró su heartbeat", timeout=15)
        w.stop()

    def test_rapid_spawn_sigterm_never_orphans_worker_row(self):
        """Stress evidence for a reviewer's "intermittent orphan-worker
        race" finding on commit 08003b04 (real-process, repeated N times —
        a single run proves nothing about a timing-dependent race): SIGTERM
        sent as early as this harness can send it (right after the health
        line, racing `_boot_and_run`'s worker-construction point as tightly
        as an external signal can) must always exit 0 AND its
        `procrastinate_workers` row must actually be gone by the time the
        process has exited.

        Why the row must be gone, not merely "eventually reaped": procrastinate's
        own clean-shutdown path (`Worker.run()`) calls
        `app.job_manager.unregister_worker(worker_id)`, which executes
        `procrastinate_unregister_worker_v1` — a real `DELETE FROM
        procrastinate_workers WHERE id = worker_id`
        (site-packages/procrastinate/sql/schema.sql). So for every run that
        exits 0, `after - before` (new rows since this cycle's spawn) MUST be
        the empty set — a clean exit that still leaves a row behind IS the
        orphan bug this test exists to catch, and asserting only on the exit
        code (as a prior version of this test did) would silently miss it.

        A NON-zero exit is a different, weaker case: procrastinate's
        stalled-worker recovery is designed to eventually reap a row left by
        an unclean shutdown, so a lingering row there is expected, not itself
        proof of a bug — only the exit code is asserted for those runs."""
        # Bumped from 12 (worker-signal-ownership-not-task-cancel.md's
        # own permanent-regression baseline) after closing the early-
        # window raw-signal-kill race main()'s early handler now covers —
        # more cycles per run buys more confidence the ~1-in-40 race is
        # actually gone, not just less likely to show up in a small N.
        N = 40
        bad_exits = []
        orphans = []
        for i in range(N):
            # Each cycle measures the signal/worker-row contract, not work
            # accumulated by earlier cycles. Keep this worker maintenance-
            # only: current startup defers can enqueue fresh business work
            # after the table reset, and procrastinate deliberately cannot
            # cancel a synchronous task during shutdown. Separate tests own
            # those business-job shutdown contracts.
            _reset_queue_tables()
            before = self._worker_ids()
            w = self._spawn({
                "TURNOS_WAITLIST_OFF": "1",
                "TURNOS_REMINDERS_OFF": "1",
                "TURNOS_HOUSEKEEPING_OFF": "1",
                "TURNOS_REFERRAL_SWEEP_OFF": "1",
                "TURNOS_BACKUP_OFF": "1",
                "TURNOS_SUMMARY_PREGEN": "0",
                "TURNOS_TRANSLATE_WARMUP_OFF": "1",
                "TURNOS_HSI_SYNC_OFF": "1",
            }, diagnostic=True)
            w.health_port()
            # No explicit timeout: WorkerProc.stop now defaults to the
            # repo's own shutdown budget (queue_infra.supervisor_sigkill_
            # budget() — max(drain, POOL_OPEN_TIMEOUT_S) + margin), the
            # SAME ceiling asgi_main._terminar grants a supervised worker.
            # A prior hard-coded 25s was shorter than POOL_OPEN_TIMEOUT_S
            # (30s), so a worker still opening its pool during a startup
            # blip was SIGKILLed by the harness before it could exit
            # cleanly — the orphan/false-non-zero this stress loop hunts
            # for, induced by the harness itself rather than the code.
            exit_code = w.stop()
            after = self._worker_ids()
            new = after - before
            if exit_code != 0:
                # The worker's own stderr tail: a bare "[1]" (load ~200, 1/40)
                # cannot tell a pool that could not open from a shutdown bug.
                tail = "\n".join(w.output_after_exit(5).splitlines()[-15:])
                bad_exits.append(f"exit={exit_code} (run {i}):\n{tail}")
                # Non-clean exit: a lingering row is expected (reaped later
                # by stalled-worker recovery), not asserted on here.
                continue
            if new:
                orphans.append((i, sorted(new)))
        self.assertEqual(bad_exits, [],
                         f"{len(bad_exits)}/{N} rapid spawn+SIGTERM runs "
                         f"exited non-zero: {bad_exits}")
        self.assertEqual(orphans, [],
                         f"{len(orphans)}/{N} rapid spawn+SIGTERM runs exited "
                         f"CLEANLY (exit 0) but left a procrastinate_workers "
                         f"row behind — genuine orphan, cycle index and row "
                         f"id(s): {orphans}")

    def test_signal_immediately_after_spawn_never_crashes_or_hangs(self):
        """Characterizes the GENUINELY UNPROTECTED window (independent-
        review correction): interpreter startup + this module's own
        top-level imports (`ai.chart_summary_queue`,
        `modules.turnos.hsi_journal_queue`, `modules.turnos.
        referrals_queue`/`waitlist_queue`, `notifications.
        reminders_queue`, `ops.housekeeping_queue`/`translate_warmup_
        queue` — worker.py lines ~171-175) all run BEFORE `main()` is
        even a callable name, so nothing `main()` does — including its
        early plain-signal handler — can possibly run before them. THIS
        is the window; `test_rapid_spawn_sigterm_never_orphans_worker_
        row` above is NOT evidence about it (it signals only after
        `health_port()` confirms the "salud en http://…" line, by which
        point imports, module load, and the early handler install have
        ALL already finished — it structurally never touches this
        window).

        This probe races it directly: SIGTERM sent IMMEDIATELY after
        `Popen()` returns, no wait for any output at all — as early as
        this harness can possibly signal, deliberately BEFORE the
        health-line wait `test_rapid_spawn_...` relies on. A raw `-15`
        (killed by Python's default SIGTERM disposition, silent for
        SIGTERM — see the module docstring) is an EXPECTED, DOCUMENTED
        outcome here, not a bug: it means the signal landed before
        interpreter startup + these imports finished, a window this fix
        does not claim to close (closing it would mean installing a
        handler at MODULE level, before those imports — a materially
        bigger restructure, out of scope; see worker.py `main()`'s own
        "HONEST BOUND" comment). With a valid test DB and exactly ONE
        SIGTERM sent, the only two LEGITIMATE outcomes are -15 (raw,
        pre-handler window) or 0 (the handler was already installed and
        the single signal took the graceful-abort-before-bootstrap
        path); EXIT_TRANSIENT(1)/EXIT_CONFIG(2)/EXIT_FORCE_KILL(3) here
        would mean something ELSE is wrong (a real config/transient
        failure, or a spurious extra-signal path) and must fail loudly,
        not be shrugged off as "also fine". What this probe actually
        asserts: no hang (bounded exit), no Python-level crash
        (Traceback/KeyboardInterrupt in stderr), exactly one of those two
        legitimate outcomes, and — for the cycles that DO exit 0 — no
        orphaned `procrastinate_workers` row (same before/after row-id
        diff `test_rapid_spawn_sigterm_never_orphans_worker_row` uses;
        registration happens deep inside `Worker.run()`, far past this
        window, so a -15 cycle has structurally nothing to check there —
        nothing was ever registered)."""
        N = 20
        outcomes = []
        for i in range(N):
            before = self._worker_ids()
            w = self._spawn()
            w.proc.send_signal(signal.SIGTERM)
            try:
                exit_code = w.proc.wait(_wait.scaled(15))
            except subprocess.TimeoutExpired:
                w._killpg()
                w.proc.wait(10)
                raise AssertionError(
                    f"cycle {i}: never exited after an immediate SIGTERM "
                    f"— must not hang regardless of which window the "
                    f"signal landed in; stderr:\n" + "\n".join(w.lines))
            stderr = "\n".join(w.lines)
            outcomes.append(exit_code)
            self.assertNotIn("Traceback", stderr,
                             f"cycle {i} (exit={exit_code}): uncaught "
                             f"exception — must never happen regardless "
                             f"of which window the signal landed in:\n{stderr}")
            self.assertNotIn("KeyboardInterrupt", stderr,
                             f"cycle {i} (exit={exit_code}): raw "
                             f"KeyboardInterrupt leaked:\n{stderr}")
            self.assertIn(
                exit_code, (-15, 0),
                f"cycle {i}: exit={exit_code} is neither of the two "
                f"LEGITIMATE outcomes for a single SIGTERM against a "
                f"valid DB (-15 raw-kill in the pre-handler window, or 0 "
                f"graceful) — a config/transient/force-kill code here "
                f"means something else is genuinely wrong, not a variant "
                f"of the window being probed:\n{stderr}")
            if exit_code == 0:
                after = self._worker_ids()
                self.assertEqual(
                    after - before, set(),
                    f"cycle {i}: exited 0 but left a procrastinate_workers "
                    f"row behind — genuine orphan")

        # Characterization, not a pass-rate assertion (this window's
        # timing is inherently racy/system-dependent — the point is that
        # EITHER legitimate outcome is safe, not that one is more
        # common): print the observed split so a future run can see
        # whether the window's real-world size changed.
        raw_kill = outcomes.count(-15)
        handled = N - raw_kill
        print(f"[test] pre-handler-window probe: {raw_kill}/{N} raw "
              f"-15 (signal landed before interpreter startup + imports "
              f"finished), {handled}/{N} handled gracefully (exit 0) — "
              f"both are safe, documented outcomes",
              file=sys.stderr)

    def _worker_ids(self):
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT id FROM procrastinate_workers")
            return {row[0] for row in cur.fetchall()}

    def test_no_database_url_returns_config_exit_loudly(self):
        # In-process: db._db_url() resolving empty (no env var, no .env)
        # must abort main() before any queue machinery is touched, with el
        # código PERMANENTE (2) — el supervisor no debe reintentar una
        # config que un reinicio no arregla. Patched rather than a
        # subprocess because db.py's own .env fallback reads the repo ROOT.
        from app import worker
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        with patch.object(db, "_db_url", return_value=""):
            self.assertEqual(worker.main(), worker.EXIT_CONFIG)

    def test_cancelled_error_from_boot_and_run_maps_to_exit_transient(self):
        """Round-9 review finding: `_boot_and_run` deliberately re-raises
        `asyncio.CancelledError` after cleanup (required for correct
        cooperative-cancellation semantics — this file's own static gate
        bans `except BaseException`/bare `except:`, so it can't just be
        swallowed there). Before this fix, `main()`'s `except Exception`
        never caught it (CancelledError is a BaseException, not an
        Exception, since Python 3.8) — it propagated straight out of
        main() uncaught, bypassing `_STATE["fase"]="error"` and the
        documented "failures exit loudly... with TWO distinct codes the
        supervisor honors" contract. In-process, `_boot_and_run` mocked to
        raise directly — isolates main()'s own exception mapping from
        whichever real code path can reach it."""
        import asyncio
        from app import worker
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")

        async def _raise_cancelled(*a, **k):
            raise asyncio.CancelledError()

        old_fase = worker._STATE.get("fase")
        try:
            with patch.object(db, "_db_url", return_value="postgresql://u:p@127.0.0.1:1/x"), \
                 patch.object(queue_infra, "ensure_schema"), \
                 patch.object(worker, "_register_jobs", return_value=["maintenance"]), \
                 patch.object(worker, "_start_health_server", return_value=None), \
                 patch.object(worker, "_boot_and_run", _raise_cancelled):
                result = worker.main()
            fase_after = worker._STATE.get("fase")
        finally:
            worker._STATE["fase"] = old_fase
        self.assertEqual(result, worker.EXIT_TRANSIENT,
                         "CancelledError from _boot_and_run must map to "
                         "EXIT_TRANSIENT, never propagate uncaught")
        self.assertEqual(fase_after, "error",
                         "_STATE['fase'] must be marked 'error', matching "
                         "every other failure path")

    def test_early_window_sigterm_sets_stop_requested_before_loop_starts(self):
        """A DIFFERENT, REAL, reproducible bug (documented in
        docs/solutions/dev/worker-signal-ownership-not-task-cancel.md): the
        only thing that ever installed a SIGTERM/SIGINT handler in this
        process used to be _boot_and_run's own loop.add_signal_handler(),
        which only runs once asyncio.run() has actually started executing
        that coroutine — so a signal landing between _start_health_server()
        (what an external readiness prober waits on) and that point hit
        Python's default disposition and killed the process outright,
        measured at ~2-4% of rapid spawn+immediate-SIGTERM cycles.

        In-process reproduction of exactly that window: _start_health_server
        is faked to deliver a REAL SIGTERM to this process (os.kill) at the
        precise point the real function would have just started listening —
        i.e. strictly BEFORE asyncio.run(_boot_and_run(...)) executes a
        single line. With the fix, main()'s own early plain signal.signal()
        handler catches it, _boot_and_run's very first action folds it into
        the existing stop_requested contract, and main() returns 0 having
        never reached recover/defer/Worker.run() or fase="corriendo" — the
        same graceful-abort-before-bootstrap path a signal during real
        bootstrap already takes."""
        from app import worker as _worker

        reached_stages = []

        async def _fake_recover(app, colas):
            reached_stages.append("recover")
            return 0

        async def _fake_defer(app):
            reached_stages.append("defer")
            return 0

        class _FakeWorker:
            def stop(self):
                reached_stages.append("stop")

            async def run(self):
                reached_stages.append("run")
                return None

        class _FakeApp:
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def open_async(self):
                yield None

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker()

        def _fake_health_server():
            # The exact window the fix closes: a signal delivered here has
            # NOT YET reached asyncio.run(_boot_and_run(...)) — before the
            # fix, nothing in the process would have caught it.
            os.kill(os.getpid(), signal.SIGTERM)
            return None

        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        old_fase = _worker._STATE.get("fase")
        _worker._STATE["fase"] = "arrancando"
        prev_sigterm = signal.getsignal(signal.SIGTERM)
        prev_sigint = signal.getsignal(signal.SIGINT)
        try:
            with patch.object(db, "_db_url", return_value="postgresql://u:p@127.0.0.1:1/x"), \
                 patch.object(queue_infra, "ensure_schema"), \
                 patch.object(queue_infra, "build_app", return_value=_FakeApp()), \
                 patch.object(_worker, "_register_jobs", return_value=["maintenance"]), \
                 patch.object(_worker, "_start_health_server", _fake_health_server), \
                 patch.object(_worker, "_recover_stalled_jobs", _fake_recover), \
                 patch.object(_worker, "_defer_startup_jobs", _fake_defer):
                result = _worker.main()
            fase_after = _worker._STATE.get("fase")
        finally:
            _worker._STATE["fase"] = old_fase

        self.assertEqual(result, 0,
                         "an early-window SIGTERM must still exit "
                         "gracefully (0), not the raw-signal-kill this "
                         "bug used to produce")
        self.assertEqual(reached_stages, [],
                         "recover/defer/Worker.run() must never run once "
                         "an early stop was recorded")
        self.assertEqual(fase_after, "apagando",
                         "main() reaches its own final 'apagando' log on "
                         "this path too (same graceful convention as a "
                         "normal shutdown) — the invariant that actually "
                         "matters is checked next: fase must never have "
                         "passed through corriendo")
        self.assertNotIn("corriendo", reached_stages + [fase_after],
                         "fase must never reach corriendo when the stop "
                         "was requested before bootstrap even began")
        self.assertEqual(signal.getsignal(signal.SIGTERM), prev_sigterm,
                         "main() must restore the exact pre-call SIGTERM "
                         "disposition on exit — a leaked handler would "
                         "corrupt every later tests/signal in this process")
        self.assertEqual(signal.getsignal(signal.SIGINT), prev_sigint,
                         "same for SIGINT")

    def test_second_early_signal_force_kills(self):
        """Round-9's 'second signal force-kills' discipline extended to
        the NEW early phase (design point 3 of the fix), UPDATED for the
        KeyboardInterrupt-crash finding (independent review): a signal
        landing during the pre-asyncio window must still leave a SECOND
        signal free to force an immediate exit — but no longer by
        restoring the OS default disposition (silent for SIGTERM, but
        `signal.default_int_handler` — a real, catchable
        `KeyboardInterrupt` — for SIGINT, which is exactly what crashed
        with an uncaught traceback). The handler now stays installed
        (never restores mid-window) and calls `_force_exit()` — patched
        here to a recorder so the test process survives — on the SECOND
        (and only the second) invocation, for BOTH signals. Proof,
        without ever delivering a literal second real signal through the
        OS (which would just kill the test runner): drives two REAL
        SIGTERMs through the fake health-server seam and asserts
        `_force_exit` was reached exactly once, with the right code and
        signal, only after the first signal already recorded the stop
        request."""
        from app import worker as _worker

        class _FakeWorker:
            def stop(self):
                pass

            async def run(self):
                return None

        class _FakeApp:
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def open_async(self):
                yield None

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker()

        force_exit_calls = []

        def _fake_force_exit(code, signum):
            force_exit_calls.append((code, signum))

        def _fake_health_server():
            os.kill(os.getpid(), signal.SIGTERM)
            # A second SIGTERM landing "a moment later" in the same
            # early window — this call is what used to be able to reach
            # Python's default SIGINT disposition for SIGINT; for SIGTERM
            # specifically the old restore-disposition design already
            # handled this correctly, so proving BOTH signals now go
            # through the SAME deliberate path (not "SIGTERM was already
            # fine, only SIGINT needed the fix") is the point.
            os.kill(os.getpid(), signal.SIGTERM)
            return None

        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        old_fase = _worker._STATE.get("fase")
        _worker._STATE["fase"] = "arrancando"
        try:
            with patch.object(db, "_db_url", return_value="postgresql://u:p@127.0.0.1:1/x"), \
                 patch.object(queue_infra, "ensure_schema"), \
                 patch.object(queue_infra, "build_app", return_value=_FakeApp()), \
                 patch.object(_worker, "_register_jobs", return_value=["maintenance"]), \
                 patch.object(_worker, "_start_health_server", _fake_health_server), \
                 patch.object(_worker, "_force_exit", _fake_force_exit):
                result = _worker.main()
            fase_after = _worker._STATE.get("fase")
        finally:
            _worker._STATE["fase"] = old_fase

        self.assertEqual(force_exit_calls, [(_worker.EXIT_FORCE_KILL, signal.SIGTERM)],
                         "the SECOND early signal must reach _force_exit "
                         "exactly once, with EXIT_FORCE_KILL and the "
                         "actual signal that fired")
        # With _force_exit patched to a no-op recorder (never actually
        # exiting, unlike the real os._exit()), execution continues as if
        # the process were still alive — proving the FIRST signal's
        # effect (the early stop request) still took, same as before.
        self.assertEqual(result, 0,
                         "the first signal's stop request must still be "
                         "honored gracefully (exit 0), independent of "
                         "what a later signal does")
        self.assertEqual(fase_after, "apagando")

    def test_second_early_signal_calling_real_force_exit_terminates(self):
        """The previous test proves the COUNTER logic without literally
        calling os._exit() (which would kill the test runner). This test
        proves the OTHER half — that a genuinely UNPATCHED _force_exit()
        really does terminate immediately — in an isolated CHILD process,
        so the test runner itself is never at risk. A one-shot in-process
        call to _handle_early_signal (reached by triggering the real
        early-window path) is exercised twice, second time hitting the
        REAL _force_exit(); the child's exit code and stderr are the
        proof."""
        import textwrap
        script = textwrap.dedent(f"""
            import os, signal, sys
            sys.path.insert(0, {ROOT!r})
            os.environ["TURNOS_QUEUE_WORKER"] = "1"
            from app import worker
            from app.core import db, queue_infra

            class _FakeWorker:
                def stop(self): pass
                async def run(self): return None

            class _FakeApp:
                from contextlib import asynccontextmanager
                @asynccontextmanager
                async def open_async(self):
                    yield None
                def perform_import_paths(self): pass
                def _worker(self, **kwargs): return _FakeWorker()

            def _fake_health_server():
                os.kill(os.getpid(), signal.SIGTERM)
                os.kill(os.getpid(), signal.SIGTERM)
                # Unreachable if the fix works: the second kill above
                # must have already terminated this process via
                # os._exit() before control returns here.
                print("UNREACHABLE: returned after the second signal",
                      file=sys.stderr, flush=True)
                return None

            db._db_url = lambda: "postgresql://u:p@127.0.0.1:1/x"
            queue_infra.ensure_schema = lambda: None
            queue_infra.build_app = lambda: _FakeApp()
            worker._register_jobs = lambda app: ["maintenance"]
            worker._await_settings_db = lambda *a, **k: None
            worker._start_health_server = _fake_health_server
            worker.main()
            print("UNREACHABLE: main() returned normally",
                  file=sys.stderr, flush=True)
        """)
        proc = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            capture_output=True, text=True, timeout=_wait.scaled(20))
        self.assertEqual(proc.returncode, 3,
                         f"must exit via EXIT_FORCE_KILL (3), not a "
                         f"Python-default-disposition kill or crash; "
                         f"stderr:\n{proc.stderr}")
        self.assertIn("segunda señal recibida", proc.stderr)
        self.assertNotIn("UNREACHABLE", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("KeyboardInterrupt", proc.stderr)

    def test_two_async_phase_signals_force_exit(self):
        """Two real asyncio-phase signals use the shared counter: the
        first is a graceful request and the second exits via the unpatched
        `_force_exit()` contract rather than a default disposition."""
        import textwrap
        script = textwrap.dedent(f"""
            import asyncio, os, signal, sys
            sys.path.insert(0, {ROOT!r})
            os.environ["TURNOS_QUEUE_WORKER"] = "1"
            from app import worker
            from app.core import db, queue_infra

            class _FakeWorker:
                def stop(self): pass
                async def run(self): return None

            class _FakeApp:
                from contextlib import asynccontextmanager
                @asynccontextmanager
                async def open_async(self):
                    # This happens only after _boot_and_run installed the
                    # real asyncio handler.  Yield once so its callback has
                    # recorded the first request before the bootstrap check.
                    os.kill(os.getpid(), signal.SIGTERM)
                    await asyncio.sleep(0)
                    yield None
                    # Runs on ctx.__aexit__() — _boot_and_run's own
                    # outer finally, reached on EVERY exit path from the
                    # inner try (including the immediate "stop already
                    # requested" return this test exercises), always
                    # AFTER loop.add_signal_handler() has already run.
                    # A DIFFERENT signal than phase 0's (SIGINT, not
                    # SIGTERM) — proving the count carries across
                    # handlers, not just across repeats of the same one.
                    os.kill(os.getpid(), signal.SIGINT)
                    # Yield control so the event loop actually dispatches
                    # the pending signal callback (add_signal_handler
                    # callbacks run via call_soon, never synchronously
                    # inline) — real _force_exit() should fire well
                    # inside this window.
                    await asyncio.sleep(2)
                    print("UNREACHABLE: open_async teardown finished "
                          "after the second signal",
                          file=sys.stderr, flush=True)
                def perform_import_paths(self): pass
                def _worker(self, **kwargs): return _FakeWorker()

            def _fake_health_server():
                return None

            db._db_url = lambda: "postgresql://u:p@127.0.0.1:1/x"
            queue_infra.ensure_schema = lambda: None
            queue_infra.build_app = lambda: _FakeApp()
            worker._register_jobs = lambda app: ["maintenance"]
            worker._await_settings_db = lambda *a, **k: None
            worker._start_health_server = _fake_health_server
            worker.main()
            print("UNREACHABLE: main() returned normally",
                  file=sys.stderr, flush=True)
        """)
        proc = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            capture_output=True, text=True, timeout=_wait.scaled(20))
        self.assertEqual(proc.returncode, 3,
                         f"two async-phase signals must exit via EXIT_FORCE_KILL "
                         f"(3), not run forever or crash; stderr:\n"
                         f"{proc.stderr}")
        self.assertIn("SIGTERM/SIGINT received during the async worker phase",
                      proc.stderr, "the first signal must be owned by the "
                      "async handler")
        self.assertIn("segunda señal recibida (SIGINT)", proc.stderr,
                      "the second async signal must use the deliberate "
                      "force-exit path")
        self.assertNotIn("UNREACHABLE", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("KeyboardInterrupt", proc.stderr)

    def test_early_signal_before_schema_skips_every_later_side_effect(self):
        """A real early SIGTERM may finish only the current bounded DB-URL
        lookup.  It must not start schema DDL, app construction, job
        registration, or the health thread afterwards."""
        import textwrap
        script = textwrap.dedent(f"""
            import os, signal, sys
            sys.path.insert(0, {ROOT!r})
            os.environ["TURNOS_QUEUE_WORKER"] = "1"
            from app import worker
            from app.core import db, outbound_guard, queue_infra

            events = []
            def mark(name):
                events.append(name)
                print(name, flush=True)

            outbound_guard.requested = lambda: False
            def db_url():
                mark("db_url")
                os.kill(os.getpid(), signal.SIGTERM)
                return "postgresql://test"
            db._db_url = db_url
            queue_infra.ensure_schema = lambda: mark("schema")
            queue_infra.build_app = lambda: mark("build")
            worker._register_jobs = lambda app: mark("register")
            worker._await_settings_db = lambda *a, **k: None
            worker._start_health_server = lambda: mark("health")
            code = worker.main()
            print("RESULT", code, events, flush=True)
        """)
        proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                              capture_output=True, text=True, timeout=_wait.scaled(20))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("RESULT 0 ['db_url']", proc.stdout)
        self.assertNotIn("schema", proc.stdout)
        self.assertNotIn("build", proc.stdout)
        self.assertNotIn("register", proc.stdout)
        self.assertNotIn("health", proc.stdout)

    def test_early_signal_inside_schema_finishes_schema_then_stops(self):
        """A first signal never interrupts the bounded schema step, but
        its completion is the last startup effect before the clean exit."""
        import textwrap
        script = textwrap.dedent(f"""
            import os, signal, sys
            sys.path.insert(0, {ROOT!r})
            os.environ["TURNOS_QUEUE_WORKER"] = "1"
            from app import worker
            from app.core import db, outbound_guard, queue_infra

            events = []
            def mark(name):
                events.append(name)
                print(name, flush=True)

            outbound_guard.requested = lambda: False
            db._db_url = lambda: "postgresql://test"
            def schema():
                mark("schema-start")
                os.kill(os.getpid(), signal.SIGTERM)
                mark("schema-finish")
            queue_infra.ensure_schema = schema
            queue_infra.build_app = lambda: mark("build")
            worker._register_jobs = lambda app: mark("register")
            worker._await_settings_db = lambda *a, **k: None
            worker._start_health_server = lambda: mark("health")
            code = worker.main()
            print("RESULT", code, events, flush=True)
        """)
        proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                              capture_output=True, text=True, timeout=_wait.scaled(20))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("RESULT 0 ['schema-start', 'schema-finish']", proc.stdout)
        self.assertNotIn("build", proc.stdout)
        self.assertNotIn("register", proc.stdout)
        self.assertNotIn("health", proc.stdout)

    def test_signal_after_real_async_handler_removal_forces_exit_3(self):
        """The old cleanup restored SIGTERM's default disposition before
        main's finally.  Inject the second signal immediately after the
        *real* asyncio removal: it must remain pending until the shared
        plain handler is restored, then take EXIT_FORCE_KILL (not raw 143).
        """
        import textwrap
        script = textwrap.dedent(f"""
            import asyncio, os, signal, sys
            from asyncio import unix_events
            sys.path.insert(0, {ROOT!r})
            os.environ["TURNOS_QUEUE_WORKER"] = "1"
            from app import worker
            from app.core import db, outbound_guard, queue_infra

            outbound_guard.requested = lambda: False
            db._db_url = lambda: "postgresql://test"
            queue_infra.ensure_schema = lambda: None

            class _FakeJobManager:
                # Same boot-time contract the in-process fakes mirror
                # (see the module-level _FakeJobManager): the async
                # update_heartbeat coroutine the worker's heartbeat-cancellation
                # guard wraps at construction time.
                async def update_heartbeat(self, worker_id): return None

            class _FakeWorker:
                def __init__(self, app, **kwargs):
                    # procrastinate's Worker.__init__ stores the constructing
                    # app; the guard reads worker.app.job_manager.
                    self.app = app
                def stop(self):
                    print("worker-stop", file=sys.stderr, flush=True)
                async def run(self):
                    os.kill(os.getpid(), signal.SIGTERM)
                    await asyncio.sleep(0.05)

            class _FakeApp:
                from contextlib import asynccontextmanager
                def __init__(self):
                    # Real App.__init__ builds one JobManager per app.
                    self.job_manager = _FakeJobManager()
                @asynccontextmanager
                async def open_async(self):
                    yield None
                def perform_import_paths(self): pass
                def _worker(self, **kwargs): return _FakeWorker(self)

            queue_infra.build_app = lambda: _FakeApp()
            worker._register_jobs = lambda app: ["maintenance"]
            worker._await_settings_db = lambda *a, **k: None
            worker._start_health_server = lambda: None
            original = unix_events._UnixSelectorEventLoop.remove_signal_handler
            fired = False
            def remove_then_signal(loop, signum):
                nonlocal_fired = globals().get("_fired", False)
                result = original(loop, signum)
                if signum == signal.SIGTERM and not nonlocal_fired:
                    globals()["_fired"] = True
                    os.kill(os.getpid(), signal.SIGTERM)
                return result
            unix_events._UnixSelectorEventLoop.remove_signal_handler = remove_then_signal
            worker.main()
            print("UNREACHABLE", file=sys.stderr, flush=True)
        """)
        proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                              capture_output=True, text=True, timeout=_wait.scaled(20))
        self.assertEqual(proc.returncode, 3,
                         f"signal after actual asyncio handler removal must "
                         f"take EXIT_FORCE_KILL, not raw SIGTERM: {proc.stderr}")
        self.assertIn("segunda señal recibida (SIGTERM)", proc.stderr)
        self.assertNotIn("UNREACHABLE", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("KeyboardInterrupt", proc.stderr)

    def test_invalid_interval_exits_config_code(self):
        # Subprocess real: un intervalo no expresable (7 min) es config
        # permanente → exit 2 y línea de log clara (ronda Codex).
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "app", "worker.py")],
            cwd=ROOT,
            env=_worker_env({"TURNOS_WAITLIST_INTERVAL_MIN": "7"}),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        try:
            self.assertEqual(proc.wait(_wait.scaled(20)), 2)
            self.assertIn("configuración inválida", proc.stderr.read())
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(10)
            raise
        finally:
            proc.stderr.close()

    def test_unreachable_postgres_exits_1_loudly(self):
        # Real subprocess: the startup contract is fail-LOUD (exit 1, the
        # supervisor decides), never a web-style silent degradation.
        # 127.0.0.1:1 refuses instantly — no timeout dance.
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "app", "worker.py")],
            cwd=ROOT,
            env=_worker_env({"DATABASE_URL": "postgresql://x:x@127.0.0.1:1/nada"}),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        try:
            self.assertEqual(proc.wait(_wait.scaled(20)), 1)
            self.assertIn("no se pudo asegurar el esquema", proc.stderr.read())
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(10)
            raise
        finally:
            proc.stderr.close()


class TestStoppedWorkerDurability(_DbProcTest):
    """Queued work survives a worker that isn't there — the durable-queue
    property the daemon threads never had. Defer with NO worker running,
    verify the row waits in Postgres, start a worker, verify it executes
    exactly once."""

    def test_job_deferred_while_worker_down_runs_once_on_start(self):
        _defer_sweep_without_worker()
        rows = _job_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "todo", "sin worker, el job espera en la cola")

        w = self._spawn()
        w.wait_line("corriendo")
        _until(lambda: _job_rows()[0][2] == "succeeded",
                    "el job encolado se ejecutó tras arrancar el worker")
        rows = _job_rows()
        self.assertEqual(_started_events(rows[0][0]), 1, "debe ejecutarse EXACTAMENTE una vez")
        w.stop()

    def test_disabled_jobs_worker_never_consumes_foreign_queues(self):
        # Revisión Fable 2026-08-08, hallazgo 2, la mitad de integración:
        # un worker con ambos jobs apagados NO consume la cola de waitlist
        # — el job ajeno espera (todo), nunca queda failed/succeeded.
        _defer_sweep_without_worker()
        w = self._spawn({"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1"})
        w.wait_line("corriendo")
        _wait.stays(lambda: _job_rows()[0][2] == "todo",
                    "un worker sin el job registrado no debe tocarlo", for_s=4)
        w.stop()


class TestTwoWorkerContention(_DbProcTest):
    """Two real worker processes against the same Postgres: every queued
    job executes exactly once (FOR UPDATE SKIP LOCKED on fetch)."""

    def test_jobs_execute_once_with_two_workers(self):
        for ts in range(1, 6):
            _defer_sweep_without_worker(timestamp=ts)
        self.assertEqual(len(_job_rows()), 5)

        w1 = self._spawn()
        w2 = self._spawn()
        w1.wait_line("corriendo")
        w2.wait_line("corriendo")
        _until(lambda: all(r[2] == "succeeded" for r in _job_rows()),
                    "los 5 jobs se ejecutaron", timeout=45)
        rows = _job_rows()
        for job_id, _name, _status in rows:
            self.assertEqual(_started_events(job_id), 1,
                             f"job {job_id} se ejecutó más de una vez bajo contención")
        w1.stop()
        w2.stop()


class TestJanitorRetention(_DbProcTest):
    """El janitor es una corrutina AWAITEADA de verdad (responsable del producto reprodujo el
    borrador sync: devolvía la corrutina sin ejecutar, borraba NADA y
    reportaba éxito) — probado acá contra filas reales: borra sólo lo
    viejo y terminado (succeeded/cancelled > retención) de SUS PROPIAS
    colas; failed, lo reciente y las colas ajenas quedan."""

    def _seed(self, queue, status, event_type, age_days):
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, status) "
                "VALUES (%s, %s, %s::procrastinate_job_status) RETURNING id",
                (queue, waitlist_queue._TASK_NAME, status))
            job_id = cur.fetchone()[0]
            # el trigger de INSERT puede haber escrito su propio evento a
            # now() — lo reemplazamos por EL evento con la edad que este
            # caso necesita (delete_old_jobs mira el evento más reciente)
            cur.execute("DELETE FROM procrastinate_events WHERE job_id=%s", (job_id,))
            cur.execute(
                "INSERT INTO procrastinate_events (job_id, type, at) VALUES "
                "(%s, %s::procrastinate_job_event_type, now() - make_interval(days => %s))",
                (job_id, event_type, age_days))
            return job_id

    def _vivos(self):
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT id FROM procrastinate_jobs")
            return {r[0] for r in cur.fetchall()}

    def test_only_old_finished_rows_of_own_queues_are_deleted(self):
        import asyncio

        from app import worker
        viejo_ok = self._seed("waitlist", "succeeded", "succeeded", 5)
        viejo_cancel = self._seed("waitlist", "cancelled", "cancelled", 5)
        viejo_failed = self._seed("waitlist", "failed", "failed", 5)
        reciente = self._seed("waitlist", "succeeded", "succeeded", 0)
        ajeno = self._seed("cola_ajena", "succeeded", "succeeded", 5)

        app = queue_infra.build_app()
        janitor = worker._register_janitor(
            app, ["waitlist", "reminders", worker._MAINT_QUEUE])
        fn = getattr(janitor, "func", janitor)

        async def _run():
            async with app.open_async():
                return await fn(timestamp=0)

        resultado = asyncio.run(_run())
        self.assertEqual(resultado["retencion_h"], worker._JOB_RETENTION_H)

        vivos = self._vivos()
        self.assertNotIn(viejo_ok, vivos, "succeeded viejo debe borrarse")
        self.assertNotIn(viejo_cancel, vivos, "cancelled viejo debe borrarse")
        self.assertIn(viejo_failed, vivos, "failed se conserva como rastro de diagnóstico")
        self.assertIn(reciente, vivos, "lo reciente se conserva")
        self.assertIn(ajeno, vivos, "una cola ajena jamás se barre")


class TestPeriodicTickUniqueness(_DbProcTest):
    """Two periodic deferrers (one per worker) deferring the SAME cron tick
    produce ONE job — `procrastinate_periodic_defers`' unique constraint,
    exercised through the same defer_periodic_job call path every worker's
    deferrer uses."""

    def test_same_tick_deferred_twice_yields_one_job(self):
        import asyncio

        async def _defer_twice():
            app = queue_infra.build_app()
            sweep = waitlist_queue.register(app, 2)
            async with app.open_async():
                # El deferrer real pasa el mismo tick como task kwarg y como
                # defer_timestamp (procrastinate valida que coincidan).
                configure = sweep.configure(task_kwargs={"timestamp": 1234567890})
                ids = []
                for _ in range(2):
                    job = await app.job_manager.defer_periodic_job(
                        job=configure.job,
                        periodic_id="",
                        defer_timestamp=1234567890)
                    ids.append(job)
                return ids

        ids = asyncio.run(_defer_twice())
        self.assertIsNotNone(ids[0], "la primera vez debe encolar")
        self.assertIsNone(ids[1], "la segunda vez (mismo tick) NO debe encolar otro job")
        self.assertEqual(len(_job_rows()), 1)


class TestWorkerModeGate(_FlagSandbox):
    """Fail-closed gate: `worker.py` runs only as the supervised child
    (TURNOS_QUEUE_WORKER=1). Any other mode refuses to run (exit 2, before
    any DB touch) — a manual one-shot run must state its mode."""

    def _main_logs(self):
        from app import worker
        logs = []
        with patch.object(worker, "_log", logs.append), \
             patch.object(db, "_db_url", return_value=""):
            code = worker.main()
        return code, logs

    def test_worker_refuses_to_run_with_mode_unset_or_invalid(self):
        # Round-6 gate: a worker with a missing or typoed mode (or the
        # retired "external") refuses to run outright.
        from app import worker
        for crudo in (None, "", "0", "yes", "extrenal", "external", "  "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=crudo)
            code, logs = self._main_logs()
            self.assertEqual(code, worker.EXIT_CONFIG, f"modo={crudo!r}")
            self.assertTrue(any("no es un modo válido" in l for l in logs),
                            f"modo={crudo!r}: {logs}")
            self.assertFalse(any("DATABASE_URL" in l for l in logs),
                             f"modo={crudo!r} debe rechazarse antes de la config de DB")

    def test_the_supervised_mode_passes_the_mode_gate(self):
        from app import worker
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        code, logs = self._main_logs()
        self.assertEqual(code, worker.EXIT_CONFIG)
        self.assertTrue(any("DATABASE_URL" in l for l in logs), logs)

    def test_stalled_threshold_below_floor_is_rejected(self):
        # Codex gate 2026-08-08: a threshold under the heartbeat margin
        # would requeue jobs of a LIVE worker — invalid config, exit 2.
        from app import worker
        _env.set_env(self, TURNOS_QUEUE_WORKER="1", TURNOS_WORKER_STALLED_S="5")
        code, logs = self._main_logs()
        self.assertEqual(code, worker.EXIT_CONFIG)
        self.assertTrue(any("piso seguro" in l for l in logs), logs)

    def test_stalled_threshold_floor_lifts_only_under_both_test_gates(self):
        from app import worker
        _env.set_env(self,
                     TURNOS_QUEUE_WORKER="1",
                     TURNOS_WORKER_STALLED_S="5",
                     TURNOS_TEST_UNSAFE_STALLED="1",
                     TURNOS_TEST_SEAMS="1")
        code, logs = self._main_logs()
        self.assertEqual(code, worker.EXIT_CONFIG)
        self.assertFalse(any("piso seguro" in l for l in logs), logs)
        self.assertTrue(any("DATABASE_URL" in l for l in logs))

    def test_unsafe_stalled_alone_without_master_gate_is_inert(self):
        # Codex gate round 4: one accidentally inherited TURNOS_TEST_*
        # line must be inert on its own — the floor still rejects.
        from app import worker
        _env.set_env(self,
                     TURNOS_QUEUE_WORKER="1",
                     TURNOS_WORKER_STALLED_S="5",
                     TURNOS_TEST_UNSAFE_STALLED="1")
        code, logs = self._main_logs()
        self.assertEqual(code, worker.EXIT_CONFIG)
        self.assertTrue(any("piso seguro" in l for l in logs), logs)

    def test_stalled_threshold_nonnumeric_fails_closed(self):
        # A typo ("30s", "abc") must be exit 2, never a silent default.
        from app import worker
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        for valor in ("abc", "30s", "1,5"):
            _env.set_env(self, TURNOS_WORKER_STALLED_S=valor)
            code, logs = self._main_logs()
            self.assertEqual(code, worker.EXIT_CONFIG, valor)
            self.assertTrue(any("no es un número" in l for l in logs),
                            f"{valor}: {logs}")

    def test_stalled_threshold_nan_and_inf_always_rejected(self):
        # NaN compares False against the floor and would silently pass
        # it; an infinite threshold means recovery never fires. Both are
        # invalid config even UNDER the test gate.
        from app import worker
        _env.set_env(self, TURNOS_QUEUE_WORKER="1")
        for valor in ("nan", "inf", "-inf"):
            for con_gate in (False, True):
                _env.set_env(self, TURNOS_WORKER_STALLED_S=valor,
                             TURNOS_TEST_UNSAFE_STALLED="1" if con_gate else None)
                code, logs = self._main_logs()
                self.assertEqual(code, worker.EXIT_CONFIG,
                                 f"{valor} gate={con_gate}")
                self.assertTrue(any("finito" in l for l in logs),
                                f"{valor} gate={con_gate}: {logs}")


class TestBatch2SweepEffects(_DbProcTest):
    """Batch 2 (housekeeping + referral retention) against the REAL worker:
    a job enqueued with the worker down waits in the queue, executes
    EXACTLY once when the worker starts, and produces the SAME observable
    effect the thread produces — the old `searches` row purged; the
    terminal referral file deleted from disk and its pointer cleared —
    leaving intact what the sweep must NOT touch (the recent one)."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1"}

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _defer(self, register_fn, timestamp=1):
        app = queue_infra.build_app()
        sweep = register_fn(app, 1440)
        app.open()
        try:
            sweep.defer(timestamp=timestamp)
        finally:
            app.close()

    def _jobs_for(self, task_name):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id, status FROM procrastinate_jobs "
                        f"WHERE task_name=%s AND {_MANUAL_TS} ORDER BY id",
                        (task_name,))
            return cur.fetchall()

    def _wait_succeeded(self, task_name, timeout=40):
        def _terminadas():
            filas = self._jobs_for(task_name)
            if filas and all(st in ("succeeded", "failed") for _i, st in filas):
                return filas
        filas = _until(_terminadas, f"jobs of {task_name} finished", timeout=timeout)
        malos = [f for f in filas if f[1] == "failed"]
        self.assertFalse(malos, f"jobs of {task_name} failed: {malos}")
        return filas

    def test_housekeeping_job_purges_old_search_rows_exactly_once(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("INSERT INTO searches (ts, q, results) VALUES "
                        "(now() - interval '400 days', 'batch2-vieja', 0) RETURNING id")
            vieja = cur.fetchone()[0]
            cur.execute("INSERT INTO searches (q, results) VALUES "
                        "('batch2-reciente', 0) RETURNING id")
            reciente = cur.fetchone()[0]
        self.addCleanup(self._purge_searches)

        self._defer(housekeeping_queue.register)
        filas = self._jobs_for(housekeeping_queue._TASK_NAME)
        self.assertEqual([st for _i, st in filas], ["todo"],
                         "without a worker, the job waits in the queue")

        w = self._spawn(dict(self._ENV_FOCO))
        w.wait_line("corriendo")
        filas = self._wait_succeeded(housekeeping_queue._TASK_NAME)
        self.assertEqual(_started_events(filas[0][0]), 1,
                         "must execute EXACTLY once")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id FROM searches WHERE id = ANY(%s)",
                        ([vieja, reciente],))
            vivos = {r[0] for r in cur.fetchall()}
        self.assertNotIn(vieja, vivos, "the 400-day-old search must be purged")
        self.assertIn(reciente, vivos, "the recent search must remain")
        w.stop()

    def _purge_searches(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM searches WHERE q LIKE 'batch2-%'")

    def test_referral_job_purges_terminal_file_and_pointer_exactly_once(self):
        import shutil
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="batch2-ordenes-")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        vieja_path = os.path.join(tmpdir, "orden-vieja.jpg")
        reciente_path = os.path.join(tmpdir, "orden-reciente.jpg")
        for p in (vieja_path, reciente_path):
            with open(p, "wb") as f:
                f.write(b"jpg-de-prueba")

        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, referral_path, "
                "  referral_mime, resolved_at, created) "
                "VALUES ('99900001', 'Prueba', 'Batch2', 'cardiologia', "
                "  'hospital-pinamar', 'rechazada', %s, 'image/jpeg', "
                "  now() - interval '200 days', now() - interval '201 days') "
                "RETURNING id", (vieja_path,))
            vieja = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, referral_path, "
                "  referral_mime, resolved_at, created) "
                "VALUES ('99900002', 'Prueba', 'Batch2', 'cardiologia', "
                "  'hospital-pinamar', 'rechazada', %s, 'image/jpeg', "
                "  now() - interval '1 day', now() - interval '2 days') "
                "RETURNING id", (reciente_path,))
            reciente = cur.fetchone()[0]
        self.addCleanup(self._purge_requests)

        self._defer(referrals_queue.register)
        w = self._spawn(dict(self._ENV_FOCO))
        w.wait_line("corriendo")
        filas = self._wait_succeeded(referrals_queue._TASK_NAME)
        self.assertEqual(_started_events(filas[0][0]), 1,
                         "must execute EXACTLY once")

        self.assertFalse(os.path.exists(vieja_path),
                         "the 200-day-old terminal file must be deleted")
        self.assertTrue(os.path.exists(reciente_path),
                        "a RECENT terminal (< retention) is never a candidate")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id, referral_path FROM booking_requests "
                        "WHERE id = ANY(%s) ORDER BY id", ([vieja, reciente],))
            filas_db = dict(cur.fetchall())
        self.assertIsNone(filas_db[vieja], "the purged pointer must be NULL")
        self.assertEqual(filas_db[reciente], reciente_path,
                         "the recent one keeps both file AND pointer")
        w.stop()

    def _purge_requests(self):
        with self._pg() as c, c.cursor() as cur:
            _dbtest.delete_booking_requests(
                cur, "dni LIKE %s AND last_name='Batch2'", ("999000%",))


class TestBatch2TwoWorkerContention(_DbProcTest):
    """Contention proof SPECIFIC to the batch-2 tasks (the generic
    waitlist one doesn't exercise these bodies): three housekeeping +
    three referral jobs under TWO concurrent workers each execute exactly
    once."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1"}

    def _defer(self, register_fn, timestamp):
        app = queue_infra.build_app()
        sweep = register_fn(app, 1440)
        app.open()
        try:
            sweep.defer(timestamp=timestamp)
        finally:
            app.close()

    def _rows(self, task_name):
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT id, status FROM procrastinate_jobs "
                        f"WHERE task_name=%s AND {_MANUAL_TS} ORDER BY id",
                        (task_name,))
            return cur.fetchall()

    def test_batch2_jobs_execute_once_with_two_workers(self):
        for ts in (1, 2, 3):
            self._defer(housekeeping_queue.register, ts)
            self._defer(referrals_queue.register, ts)

        w1 = self._spawn(dict(self._ENV_FOCO))
        w2 = self._spawn(dict(self._ENV_FOCO))
        w1.wait_line("corriendo")
        w2.wait_line("corriendo")
        nombres = (housekeeping_queue._TASK_NAME, referrals_queue._TASK_NAME)

        def _todos_listos():
            todos = [r for n in nombres for r in self._rows(n)]
            return len(todos) == 6 and all(st == "succeeded" for _i, st in todos)
        _until(_todos_listos, "los 6 jobs se ejecutaron", timeout=60)
        for nombre in nombres:
            filas = self._rows(nombre)
            self.assertEqual([st for _i, st in filas], ["succeeded"] * 3,
                             f"{nombre}: {filas}")
            for job_id, _st in filas:
                self.assertEqual(_started_events(job_id), 1,
                                 f"job {job_id} of {nombre} executed more than once")
        w1.stop()
        w2.stop()


class _CrashRecoveryMixin:
    """The mid-task-SIGKILL-and-replace choreography, shared by every
    per-queue crash-recovery suite (extracted from TestBatch2CrashRecovery
    in the registry-hardening round, when waitlist/reminders gained the
    same proof): defer a manual job, start a worker whose task bodies
    hold (TURNOS_TEST_SWEEP_HOLD_S through the shared
    queue_infra.test_hold seam), SIGKILL it genuinely mid-task, observe
    the defect ('doing' forever), start a replacement, and assert the
    startup recovery requeues + the replacement executes exactly once
    more. Subclasses provide _ENV_FOCO (their own focus flags + the
    second-scale stalled threshold under BOTH test gates)."""

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _defer(self, register_fn):
        app = queue_infra.build_app()
        sweep = register_fn(app, 1440)
        app.open()
        try:
            sweep.defer(timestamp=1)
        finally:
            app.close()

    def _status(self, task_name):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id, status FROM procrastinate_jobs "
                        f"WHERE task_name=%s AND {_MANUAL_TS} ORDER BY id",
                        (task_name,))
            return cur.fetchall()

    def _wait_status(self, task_name, wanted, timeout=40):
        def _alcanzado():
            filas = self._status(task_name)
            return filas[0] if filas and filas[0][1] == wanted else None
        return _until(_alcanzado, f"{task_name} reached {wanted}", timeout=timeout)


    def _crash_mid_task_then_recover(self, register_fn, task_name, effect_ok, off=None):
        """`off`: env that switches off the OTHER sweeps of a shared queue
        class for this one proof. A sync sweep body runs in a thread that
        procrastinate cannot cancel: a SIGTERM that lands inside it waits
        for the body to finish, past the graceful drain. The startup
        translation sweep (en, the whole catalog) ran 190 s at load ~105,
        so a replacement that picked it up during the chart-summary proof
        overran its 45 s stop budget (2026-11-07, thread dump)."""
        self._defer(register_fn)
        foco = dict(self._ENV_FOCO, **(off or {}))
        env_hold = dict(foco)
        env_hold["TURNOS_TEST_SWEEP_HOLD_S"] = "30"
        w1 = self._spawn(env_hold)
        w1.wait_line("corriendo")
        job_id, _st = self._wait_status(task_name, "doing")
        w1._killpg()          # SIGKILL to the group: GENUINE death mid-task
        w1.proc.wait(10)

        # the dead one's heartbeat now exceeds TURNOS_WORKER_STALLED_S
        self._wait_heartbeat_stale(foco["TURNOS_WORKER_STALLED_S"])
        filas = self._status(task_name)
        self.assertEqual(filas[0][1], "doing",
                         "without recovery, the job stays 'doing' forever — "
                         f"the defect's state must be observable: {filas}")

        # NO hold: the replacement executes right away. Diagnostic: once in
        # 20 runs under load this replacement logged the shutdown request and
        # never exited (2026-11-07); its shutdown phases, await chains and
        # thread stacks then land in the failure (WorkerProc.stop()).
        w2 = self._spawn(dict(foco), diagnostic=True)
        w2.wait_line("re-encolado", timeout=25)
        self._wait_status(task_name, "succeeded", timeout=40)
        self.assertEqual(_started_events(job_id), 2,
                         "one execution from the dead one + ONE from the replacement, never more")
        effect_ok()
        w2.stop()


class TestBatch2CrashRecovery(_CrashRecoveryMixin, _DbProcTest):
    """The Codex-gate blocker, closed and proven for EACH batch-2 task:
    procrastinate 3.9 fetches only status='todo', so a worker SIGKILLed
    genuinely MID-task (TURNOS_TEST_SWEEP_HOLD_S keeps the body running)
    leaves its job 'doing' forever — no built-in recovery. worker.py's
    startup recovery pass (`_recover_stalled_jobs`) requeues it, the
    replacement executes it, and the business effect lands exactly once
    (both sweeps are idempotent per item)."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
                 "TURNOS_WORKER_STALLED_S": "2",
                 # Second-scale threshold ONLY under BOTH test gates: in
                 # production the 30 s floor rejects with exit 2.
                 "TURNOS_TEST_UNSAFE_STALLED": "1",
                 "TURNOS_TEST_SEAMS": "1"}

    def test_housekeeping_survives_mid_task_sigkill_with_one_effect(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("INSERT INTO searches (ts, q, results) VALUES "
                        "(now() - interval '400 days', 'batch2-crash-vieja', 0) RETURNING id")
            vieja = cur.fetchone()[0]
            cur.execute("INSERT INTO searches (q, results) VALUES "
                        "('batch2-crash-reciente', 0) RETURNING id")
            reciente = cur.fetchone()[0]
        self.addCleanup(self._purge_searches)

        def effect_ok():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT id FROM searches WHERE id = ANY(%s)",
                            ([vieja, reciente],))
                vivos = {r[0] for r in cur.fetchall()}
            self.assertNotIn(vieja, vivos, "the old one must be purged after recovery")
            self.assertIn(reciente, vivos, "the recent one must remain — effect happens ONCE")

        self._crash_mid_task_then_recover(
            housekeeping_queue.register, housekeeping_queue._TASK_NAME, effect_ok)

    def _purge_searches(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM searches WHERE q LIKE 'batch2-crash-%'")

    def test_referrals_survive_mid_task_sigkill_with_one_effect(self):
        import shutil
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="batch2-crash-ordenes-")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        vieja_path = os.path.join(tmpdir, "orden-crash.jpg")
        with open(vieja_path, "wb") as f:
            f.write(b"jpg")
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, referral_path, "
                "  referral_mime, resolved_at, created) "
                "VALUES ('99900003', 'Prueba', 'Batch2Crash', 'cardiologia', "
                "  'hospital-pinamar', 'rechazada', %s, 'image/jpeg', "
                "  now() - interval '200 days', now() - interval '201 days') "
                "RETURNING id", (vieja_path,))
            fila = cur.fetchone()[0]
        self.addCleanup(self._purge_requests)

        def effect_ok():
            self.assertFalse(os.path.exists(vieja_path),
                             "the file must end up deleted after recovery")
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT referral_path FROM booking_requests WHERE id=%s",
                            (fila,))
                self.assertIsNone(cur.fetchone()[0], "and the pointer must be NULL")

        self._crash_mid_task_then_recover(
            referrals_queue.register, referrals_queue._TASK_NAME, effect_ok)

    def _purge_requests(self):
        with self._pg() as c, c.cursor() as cur:
            _dbtest.delete_booking_requests(cur, "last_name='Batch2Crash'")


class TestWaitlistCrashRecovery(_CrashRecoveryMixin, _DbProcTest):
    """Registry-hardening round: the same mid-task-SIGKILL proof batch 2
    already has, for the WAITLIST queue — with TestRemindersCrashRecovery
    below, every queue in worker._JOBS now has a structural re-run proof
    behind its declared `rerun` contract, not only an audited statement.
    The business effect exercised is the sweep's own state machine: an
    offer expired past its window ends 'expirada' exactly once, and the
    re-run creates no duplicate offer (the queue behind it is empty, so
    the engine's next action is 'vacante', which touches only 'ofrecida'
    rows)."""

    _ENV_FOCO = {"TURNOS_REMINDERS_OFF": "1", "TURNOS_HOUSEKEEPING_OFF": "1",
                 "TURNOS_REFERRAL_SWEEP_OFF": "1",
                 "TURNOS_WORKER_STALLED_S": "2",
                 "TURNOS_TEST_UNSAFE_STALLED": "1",
                 "TURNOS_TEST_SEAMS": "1"}

    def test_waitlist_survives_mid_task_sigkill_with_one_effect(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO appointment_offers (slot_at, service_id, "
                "  center_slug, token, sent_at, expires_at, status) "
                "VALUES (now() + interval '3 days', 'cardiologia', "
                "  'hospital-pinamar', 'crash-waitlist-tok', "
                "  now() - interval '2 hours', now() - interval '1 hour', "
                "  'ofrecida') RETURNING id")
            oferta = cur.fetchone()[0]
        self.addCleanup(self._purge_offers)

        def effect_ok():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT status FROM appointment_offers WHERE id=%s",
                            (oferta,))
                self.assertEqual(cur.fetchone()[0], "expirada",
                                 "the stale offer must end expired after recovery")
                cur.execute("SELECT count(*) FROM appointment_offers "
                            "WHERE token LIKE 'crash-waitlist-%'")
                self.assertEqual(cur.fetchone()[0], 1,
                                 "the re-run must not mint a second offer row")

        self._crash_mid_task_then_recover(
            waitlist_queue.register, waitlist_queue._TASK_NAME, effect_ok)

    def _purge_offers(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM appointment_offers "
                        "WHERE token LIKE 'crash-waitlist-%'")


class TestRemindersCrashRecovery(_CrashRecoveryMixin, _DbProcTest):
    """Registry-hardening round, reminders half. What THIS suite proves is
    the queue-level property: a job SIGKILLed mid-task is requeued and the
    replacement converges it to 'succeeded', executing exactly once more.
    The MUTATION guard behind the declared `rerun` contract (claim-CAS
    before send: at-most-once per reminder even across overlapping runs)
    is deliberately not re-proven here — it is race-proven directly in
    test_integrity_races.py (item 5, 8-thread claim race), and the sweep's
    business body is time-of-day gated (09:00–21:00 ART), which would make
    any send-side assertion here depend on the wall clock."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_HOUSEKEEPING_OFF": "1",
                 "TURNOS_REFERRAL_SWEEP_OFF": "1",
                 "TURNOS_WORKER_STALLED_S": "2",
                 "TURNOS_TEST_UNSAFE_STALLED": "1",
                 "TURNOS_TEST_SEAMS": "1"}

    def test_reminders_survive_mid_task_sigkill_and_converge(self):
        def effect_ok():
            filas = self._status(reminders_queue._TASK_NAME)
            self.assertEqual([st for _i, st in filas], ["succeeded"],
                             f"exactly the one manual job, converged: {filas}")

        self._crash_mid_task_then_recover(
            reminders_queue.register, reminders_queue._TASK_NAME, effect_ok)


class TestRecoveryTaskScoping(_DbProcTest):
    """Codex gate, registry round: recovery is scoped by TASK NAME derived
    from the enabled registry + maintenance, not only by queue. A stuck
    'doing' job in an OWNED queue whose task this worker never declared
    (foreign producer, or a future job with no rerun contract) must NOT be
    requeued — it stays 'doing', loudly, for the worker that knows it."""

    def _seed_stuck(self, queue, task_name):
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, \
             c.cursor() as cur:
            cur.execute(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, status, args) "
                "VALUES (%s, %s, 'doing', '{\"timestamp\": 1}') RETURNING id",
                (queue, task_name))
            return cur.fetchone()[0]

    def _estado(self, job_id):
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT status FROM procrastinate_jobs WHERE id=%s", (job_id,))
            return cur.fetchone()[0]

    def _recover(self, colas):
        import asyncio

        from app import worker
        app = queue_infra.build_app()

        async def _run():
            async with app.open_async():
                return await worker._recover_stalled_jobs(app, colas)

        return asyncio.run(_run())

    def test_foreign_task_in_owned_queue_is_never_requeued(self):
        from app import worker
        ajeno = self._seed_stuck("waitlist", "foreign.undeclared.task")
        propio = self._seed_stuck("waitlist", waitlist_queue._TASK_NAME)
        logs = []
        with patch.object(worker, "_log", logs.append):
            requeued = self._recover(["waitlist"])
        self.assertEqual(requeued, 1,
                         "exactly the DECLARED task is recovered")
        self.assertEqual(self._estado(propio), "todo",
                         "the declared task must be requeued")
        self.assertEqual(self._estado(ajeno), "doing",
                         "the undeclared task must stay 'doing', untouched")
        self.assertTrue(any("no declarada" in l for l in logs),
                        f"the skip must be loud: {logs}")

    def test_disabled_jobs_task_is_not_recovered_either(self):
        # The allowed set tracks the ENABLED registry: with waitlist OFF
        # its queue isn't consumed, and even a direct recovery pass over
        # that queue name must refuse its task.
        atascado = self._seed_stuck("waitlist", waitlist_queue._TASK_NAME)
        _env.set_env(self, TURNOS_WAITLIST_OFF="1")
        requeued = self._recover(["waitlist"])
        self.assertEqual(requeued, 0)
        self.assertEqual(self._estado(atascado), "doing")

    def test_stalled_row_with_duplicated_queueing_lock_still_recovers(self):
        """The live-demo deadlock (2026-09-11→15): retry_job flips a stalled
        'doing' row to 'todo' WITHOUT touching its queueing_lock, and that
        lock's unique index only covers 'todo' rows — so a startup defer
        that landed while the row sat stuck (its dedup can't see 'doing')
        already holds the same key, the flip loses to UniqueViolation on
        every pass, and the stuck row holds its execution lock forever
        while every later sweep piles up behind it. Recovery must release
        the queueing_lock before retrying: a row leaving 'doing' no longer
        needs a QUEUED-row dedup key."""
        import psycopg
        ql = "startup:waitlist.deadlock-proof"
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, \
             c.cursor() as cur:
            # row A: the stalled sweep, 'doing', still carrying its
            # queueing_lock (exactly what job 32832 looked like).
            cur.execute(
                "INSERT INTO procrastinate_jobs "
                "  (queue_name, task_name, status, queueing_lock, args) "
                "VALUES (%s, %s, 'doing', %s, '{\"timestamp\": 1}') "
                "RETURNING id",
                ("waitlist", waitlist_queue._TASK_NAME, ql))
            atascada = cur.fetchone()[0]
            # row B: the startup defer a LATER boot enqueued while A was
            # stuck — legal, because the unique index only counts 'todo'.
            cur.execute(
                "INSERT INTO procrastinate_jobs "
                "  (queue_name, task_name, status, queueing_lock, args) "
                "VALUES (%s, %s, 'todo', %s, '{\"timestamp\": 1}') "
                "RETURNING id",
                ("waitlist", waitlist_queue._TASK_NAME, ql))
            duplicada = cur.fetchone()[0]
        requeued = self._recover(["waitlist"])
        self.assertEqual(requeued, 1,
                         "the collision must not cost the recovery its own retry")
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            filas = {}
            for jid in (atascada, duplicada):
                cur.execute("SELECT status, queueing_lock FROM procrastinate_jobs "
                            "WHERE id = %s", (jid,))
                filas[jid] = cur.fetchone()
        self.assertEqual(filas[atascada][0], "todo",
                         "the stalled row is requeued despite the colliding lock")
        self.assertIsNone(filas[atascada][1],
                          "a row leaving 'doing' drops its queueing dedup key")
        self.assertEqual(filas[duplicada][0], "todo")
        self.assertEqual(filas[duplicada][1], ql,
                         "the queued row keeps its own queueing_lock")


class TestRerunContractsRunTwice(_DbProcTest):
    """Deterministic run-twice evidence for each declared rerun contract
    (Codex gate): the crash suites SIGKILL during the PRE-body hold, which
    proves recovery but not what happens after a FIRST effect has landed.
    These cases run each sweep body twice against seeded state — the
    second pass must be a no-op, which is exactly what each _JOBS entry's
    `rerun` field claims."""

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def test_waitlist_second_tick_is_a_noop(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO appointment_offers (slot_at, service_id, "
                "  center_slug, token, sent_at, expires_at, status) "
                "VALUES (now() + interval '3 days', 'cardiologia', "
                "  'hospital-pinamar', 'runtwice-wl-tok', "
                "  now() - interval '2 hours', now() - interval '1 hour', "
                "  'ofrecida') RETURNING id")
            oferta = cur.fetchone()[0]
        self.addCleanup(self._purge_offers)

        primera = waitlist.tick()
        self.assertEqual(primera["expiradas"], 1, primera)
        segunda = waitlist.tick()
        self.assertEqual(sum(segunda.values()), 0,
                         f"a re-run after the effect must do NOTHING: {segunda}")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT status FROM appointment_offers WHERE id=%s", (oferta,))
            self.assertEqual(cur.fetchone()[0], "expirada")
            cur.execute("SELECT count(*) FROM appointment_offers "
                        "WHERE token LIKE 'runtwice-wl-%'")
            self.assertEqual(cur.fetchone()[0], 1, "no duplicate offer row")

    def _purge_offers(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM appointment_offers WHERE token LIKE 'runtwice-wl-%'")

    def test_reminders_claim_cas_second_claim_loses(self):
        # The reminders rerun contract IS the claim-CAS: after the first
        # mutation (the claim), a re-run must win nothing. Full N-thread
        # race lives in test_integrity_races item 5; this pins the
        # run-twice shape next to the contract that cites it.
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, created) "
                "VALUES ('99900010', 'Prueba', 'RunTwice', 'cardiologia', "
                "  'hospital-pinamar', 'aprobada', now()) RETURNING id")
            fila = cur.fetchone()[0]
        self.addCleanup(self._purge_requests)
        self.assertTrue(db_booking.claim_reminder(fila), "first run claims")
        self.assertFalse(db_booking.claim_reminder(fila),
                         "a re-run finds the claim taken and sends nothing")

    def _purge_requests(self):
        with self._pg() as c, c.cursor() as cur:
            _dbtest.delete_booking_requests(cur, "last_name='RunTwice'")

    def test_housekeeping_second_purge_pass_is_a_noop(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("INSERT INTO searches (ts, q, results) VALUES "
                        "(now() - interval '400 days', 'runtwice-vieja', 0) RETURNING id")
            vieja = cur.fetchone()[0]
            cur.execute("INSERT INTO searches (q, results) VALUES "
                        "('runtwice-reciente', 0) RETURNING id")
            reciente = cur.fetchone()[0]
        self.addCleanup(self._purge_searches)

        housekeeping.tick_purges()
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id FROM searches WHERE id = ANY(%s)",
                        ([vieja, reciente],))
            vivos = {r[0] for r in cur.fetchall()}
        self.assertNotIn(vieja, vivos)
        self.assertIn(reciente, vivos)
        segunda = housekeeping.tick_purges()
        self.assertEqual(segunda.get("searches", 0), 0,
                         f"a re-run deletes nothing new: {segunda}")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM searches WHERE id=%s", (reciente,))
            self.assertEqual(cur.fetchone()[0], 1, "the recent row survives both")

    def _purge_searches(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM searches WHERE q LIKE 'runtwice-%'")

    def test_referrals_second_sweep_is_a_noop(self):
        import shutil
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="runtwice-ordenes-")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        vieja_path = os.path.join(tmpdir, "orden-runtwice.jpg")
        with open(vieja_path, "wb") as f:
            f.write(b"jpg")
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, referral_path, "
                "  referral_mime, resolved_at, created) "
                "VALUES ('99900011', 'Prueba', 'RunTwiceRef', 'cardiologia', "
                "  'hospital-pinamar', 'rechazada', %s, 'image/jpeg', "
                "  now() - interval '200 days', now() - interval '201 days') "
                "RETURNING id", (vieja_path,))
            fila = cur.fetchone()[0]
        self.addCleanup(self._purge_ref_requests)

        primera = booking_referrals.sweep_referrals()
        self.assertEqual(primera, 1, "first run deletes the file")
        self.assertFalse(os.path.exists(vieja_path))
        segunda = booking_referrals.sweep_referrals()
        self.assertEqual(segunda, 0,
                         "a cleared pointer is no candidate — re-run is a no-op")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT referral_path FROM booking_requests WHERE id=%s",
                        (fila,))
            self.assertIsNone(cur.fetchone()[0])

    def _purge_ref_requests(self):
        with self._pg() as c, c.cursor() as cur:
            _dbtest.delete_booking_requests(cur, "last_name='RunTwiceRef'")

    # -- batch 3: the AI-cache warmups (TURNOS_FAKE_AI=1 module-wide, so
    # both bodies run their deterministic path — zero provider calls,
    # zero spend, by construction) --------------------------------------

    def test_chart_summary_second_tick_is_a_noop(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, assigned_doctor_id, "
                "  appointment_at, created) "
                "VALUES ('99900012', 'Prueba', 'RunTwiceSum', 'cardiologia', "
                "  'hospital-pinamar', 'aprobada', 'medico-runtwice', "
                "  now() + interval '4 hours', now())")
        self.addCleanup(self._purge_summaries)

        primera = chart_summary.tick()
        self.assertEqual(primera, 1, "first tick generates the pair's summary")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*), max(fingerprint) FROM patient_summaries "
                        "WHERE dni='99900012'")
            n, fp1 = cur.fetchone()
        self.assertEqual(n, 1)
        segunda = chart_summary.tick()
        self.assertEqual(segunda, 0,
                         "unchanged sources: the fingerprint guard makes the "
                         "re-run regenerate nothing")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*), max(fingerprint) FROM patient_summaries "
                        "WHERE dni='99900012'")
            n2, fp2 = cur.fetchone()
        self.assertEqual((n2, fp2), (1, fp1),
                         "one row per (dni, doctor), byte-stable across re-runs")

    def _purge_summaries(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM patient_summaries WHERE dni='99900012'")
            _dbtest.delete_booking_requests(cur, "last_name='RunTwiceSum'")

    def test_translate_warmup_second_sweep_is_a_noop(self):
        # sweep('en') against the real pack: the first pass fills the
        # cache (fake provider — TURNOS_FAKE_AI=1 is translate._fake()'s
        # fallback), the second finds every source hash fresh and
        # translates nothing. This is the rerun contract verbatim.
        self.addCleanup(self._purge_translations)
        total, done = translate_warmup.sweep("en")
        self.assertGreater(done, 0, "first sweep fills the cache")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM translations WHERE lang='en'")
            filled = cur.fetchone()[0]
        self.assertGreater(filled, 0)
        total2, done2 = translate_warmup.sweep("en")
        self.assertEqual(done2, 0,
                         "every source hash fresh: the re-run rewrites nothing")
        self.assertEqual(total2, total)
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM translations WHERE lang='en'")
            self.assertEqual(cur.fetchone()[0], filled,
                             "row count stable across re-runs")

    def _purge_translations(self):
        from app.core import translate
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM translations WHERE lang='en'")
        translate.invalidate_rows("en")


class TestAiCacheCrashRecovery(_CrashRecoveryMixin, _DbProcTest):
    """Batch 3's half of the per-queue SIGKILL proof: a worker killed
    genuinely mid-sweep leaves the job 'doing'; the replacement's startup
    recovery requeues it and the re-run converges to ONE cache effect —
    one summary row per (dni, doctor), one translation row per (key,
    lang) — exactly what each registry entry's rerun contract claims.
    TURNOS_FAKE_AI=1 (worker_env baseline) keeps both bodies on the
    deterministic path: zero provider calls under every crash."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
                 "TURNOS_HOUSEKEEPING_OFF": "1",
                 "TURNOS_REFERRAL_SWEEP_OFF": "1",
                 "LANGUAGES": "es,en",
                 "TURNOS_WORKER_STALLED_S": "2",
                 "TURNOS_TEST_UNSAFE_STALLED": "1",
                 "TURNOS_TEST_SEAMS": "1"}

    def test_chart_summary_survives_mid_task_sigkill_with_one_row(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, assigned_doctor_id, "
                "  appointment_at, created) "
                "VALUES ('99900013', 'Prueba', 'CrashSum', 'cardiologia', "
                "  'hospital-pinamar', 'aprobada', 'medico-crash', "
                "  now() + interval '4 hours', now())")
        self.addCleanup(self._purge_crash_rows)

        def effect_ok():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT count(*) FROM patient_summaries "
                            "WHERE dni='99900013'")
                self.assertEqual(cur.fetchone()[0], 1,
                                 "exactly one summary row after recovery")

        # The translation sweep is the other proof's subject; here it would
        # only be a long uncancellable body inside w2's stop (see the helper).
        self._crash_mid_task_then_recover(
            chart_summary_queue.register, chart_summary_queue._TASK_NAME,
            effect_ok, off={"TURNOS_TRANSLATE_WARMUP_OFF": "1"})

    def test_translate_warmup_survives_mid_task_sigkill_and_converges(self):
        def effect_ok():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT count(*) FROM translations WHERE lang='en'")
                filled = cur.fetchone()[0]
                self.assertGreater(filled, 0,
                                   "the recovered sweep fills the cache")
                cur.execute("SELECT count(*) FROM (SELECT source_key FROM "
                            "translations WHERE lang='en' GROUP BY source_key "
                            "HAVING count(*) > 1) d")
                self.assertEqual(cur.fetchone()[0], 0,
                                 "one row per (key, lang) — no duplicates")

        self.addCleanup(self._purge_crash_translations)
        self._crash_mid_task_then_recover(
            translate_warmup_queue.register, translate_warmup_queue._TASK_NAME,
            effect_ok, off={"TURNOS_SUMMARY_PREGEN": "0"})

    def _purge_crash_rows(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM patient_summaries WHERE dni='99900013'")
            _dbtest.delete_booking_requests(cur, "last_name='CrashSum'")

    def _purge_crash_translations(self):
        from app.core import translate
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM translations WHERE lang='en'")
        translate.invalidate_rows("en")


class TestSweepStopsOnSignal(_CrashRecoveryMixin, _DbProcTest):
    """A SIGTERM in the middle of the translation sweep (the whole catalog
    into en, ~1300 strings, 33 batches) used to wait for the last batch:
    procrastinate runs the sync body in a thread it cannot cancel, so the
    worker overran the supervisor's 45 s stop budget and was SIGKILLed (190 s
    at load ~105, 2026-11-07). Now the body asks `core.worker_stop` between
    batches: the worker exits 0 inside the budget, the strings it did not
    reach stay stale, and the next run translates exactly those.
    TURNOS_TEST_SWEEP_STEP_HOLD_S makes every batch take 3 s on any machine
    (~100 s for the catalog), so a body that ignores the stop cannot pass."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
                 "TURNOS_HOUSEKEEPING_OFF": "1",
                 "TURNOS_REFERRAL_SWEEP_OFF": "1",
                 "TURNOS_SUMMARY_PREGEN": "0",
                 "LANGUAGES": "es,en",
                 "TURNOS_TEST_SEAMS": "1"}

    def _filas_en(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM translations WHERE lang='en'")
            return cur.fetchone()[0]

    def _latest(self, task_name):
        filas = self._status(task_name)
        return filas[-1] if filas else None

    def test_sigterm_mid_translation_exits_in_budget_and_next_run_finishes(self):
        self.addCleanup(self._purge_translations)
        self._purge_translations()
        tarea = translate_warmup_queue._TASK_NAME
        self._defer(translate_warmup_queue.register)
        w1 = self._spawn(dict(self._ENV_FOCO, TURNOS_TEST_SWEEP_STEP_HOLD_S="3"))
        w1.wait_line("corriendo")
        self._wait_status(tarea, "doing")
        # The first batch is in the cache: the sweep is genuinely half done.
        _until(lambda: self._filas_en() > 0, "first translation batch written",
               timeout=60)
        inicio = time.monotonic()
        codigo = w1.stop()   # AssertionError + thread dump past the 45 s budget
        tardo = time.monotonic() - inicio
        self.assertEqual(codigo, 0, w1._failure_evidence())
        self.assertLess(tardo, 45, f"stop took {tardo:.1f}s")
        parcial = self._filas_en()
        self.assertGreater(parcial, 0)
        self.assertEqual(self._status(tarea)[0][1], "succeeded",
                         "a halted sweep returns: its job ends, never 'doing'")

        # The next run (no hold) translates what the stopped one left.
        self._defer(translate_warmup_queue.register)
        w2 = self._spawn(dict(self._ENV_FOCO))
        w2.wait_line("corriendo")
        _until(lambda: (self._latest(tarea) or (0, ""))[1] == "succeeded"
               and len(self._status(tarea)) == 2,
               "the next sweep succeeded", timeout=120)
        completo = self._filas_en()
        self.assertGreater(completo, parcial,
                           "the stopped sweep left strings for the next run")
        w2.stop()
        # Nothing is left stale: a third sweep (fake provider, set for this
        # module) has nothing to translate.
        _total, hechas = translate_warmup.sweep("en")
        self.assertEqual(hechas, 0, "the next run finished the catalog")
        self.assertEqual(self._filas_en(), completo)

    def _purge_translations(self):
        from app.core import translate
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM translations WHERE lang='en'")
        translate.invalidate_rows("en")


class TestStartupSweeps(_DbProcTest):
    """The batch-3 first-run contract (the card's user-visible-behavior
    gate), preserved VERBATIM per twin — not uniformly: chart_summary's
    thread ticks immediately at boot, so its startup sweep runs
    immediately (startup_delay_s=0); translate_warmup's thread sleeps
    FIRST_RUN_DELAY_S (30 s) first, so its startup sweep is durably
    SCHEDULED 30 s out (schedule_at → the job row's scheduled_at, never
    a sleep). Either way a cold install never waits a whole cron
    interval (60 min / 6 h). Defers are durable (Postgres rows) and
    queued-row-deduplicated (per-task queueing lock)."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
                 "TURNOS_HOUSEKEEPING_OFF": "1",
                 "TURNOS_REFERRAL_SWEEP_OFF": "1",
                 "LANGUAGES": "es,en"}

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _startup_jobs(self, task_name):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT status FROM procrastinate_jobs "
                        "WHERE task_name=%s AND queueing_lock=%s",
                        (task_name, f"startup:{task_name}"))
            return [r[0] for r in cur.fetchall()]

    def test_boot_chart_immediate_translation_scheduled_30s(self):
        self.addCleanup(self._purge_effects)
        w = self._spawn(dict(self._ENV_FOCO))
        w.wait_line("corriendo")
        # one startup chart-summary sweep, run at boot
        # (startup_delay_s=0 — immediate, like its thread)
        _until(lambda: self._startup_jobs(chart_summary_queue._TASK_NAME) == ["succeeded"],
                    "one startup chart-summary sweep succeeded at boot", timeout=40)
        # translate_warmup keeps its thread twin's 30 s boot grace — as a
        # DURABLE scheduled defer, not a sleep: the job row exists now,
        # 'todo', with scheduled_at 30 s after the boot instant. (Its
        # execution is proven by the crash/run-twice suites; waiting the
        # grace out here would only slow the suite.) Measured against the
        # boot instant the row itself carries (its `timestamp` argument,
        # the same core.clock reading as scheduled_at), never against the
        # database's now(): that one is another machine's clock, read after
        # a delay that grows with the load.
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "SELECT status, extract(epoch FROM scheduled_at "
                "- to_timestamp((args->>'timestamp')::bigint)) "
                "FROM procrastinate_jobs WHERE task_name=%s AND "
                "queueing_lock=%s",
                (translate_warmup_queue._TASK_NAME,
                 f"startup:{translate_warmup_queue._TASK_NAME}"))
            filas = cur.fetchall()
        self.assertEqual(len(filas), 1, filas)
        estado, gracia = filas[0]
        self.assertEqual(estado, "todo")
        # the argument is whole seconds, scheduled_at keeps the fraction
        self.assertTrue(30 <= float(gracia) < 31,
                        "the 30 s grace lives in scheduled_at (durable), "
                        f"not in a sleep — got {gracia!r}s after boot")
        # Both startup rows persist the task's native execution lock —
        # asserted on the COLUMN, not inferred from behavior.
        with self._pg() as c, c.cursor() as cur:
            for tn in (chart_summary_queue._TASK_NAME,
                       translate_warmup_queue._TASK_NAME):
                cur.execute("SELECT lock FROM procrastinate_jobs "
                            "WHERE task_name=%s", (tn,))
                locks = [r[0] for r in cur.fetchall()]
                self.assertTrue(locks, tn)
                self.assertEqual(set(locks),
                                 {queue_infra.task_lock_name(tn)}, tn)
            # The proof is done; the row would come due in under 30 s and
            # its sync body (the whole catalog into en, 190 s at load ~105)
            # cannot be cancelled by the stop below. Move it out of reach.
            cur.execute(
                "UPDATE procrastinate_jobs SET scheduled_at = now() + interval '1 hour' "
                "WHERE task_name=%s AND status='todo'",
                (translate_warmup_queue._TASK_NAME,))
        w.stop()

    def test_startup_translation_grace_is_30s_after_the_boot_clock(self):
        # The grace is computed from core.clock, so a frozen clock pins it to
        # the second: scheduled_at is exactly boot + 30 s, the row's
        # `timestamp` is the boot instant itself, and chart_summary's row
        # (no grace) carries the same instant with no schedule of its own.
        import asyncio
        from datetime import datetime, timedelta, timezone
        import _clock
        from app import worker

        boot = datetime(2026, 11, 8, 12, 0, 0, 250000, tzinfo=timezone.utc)
        _clock.freeze(self, boot)
        _env.set_env(self, **self._ENV_FOCO)

        async def _defer():
            app = queue_infra.build_app()
            async with app.open_async():
                worker._register_jobs(app)
                return await worker._defer_startup_jobs(app)

        self.assertEqual(asyncio.run(_defer()), 2)
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT task_name, scheduled_at, (args->>'timestamp')::bigint "
                        "FROM procrastinate_jobs WHERE queueing_lock LIKE 'startup:%%'")
            filas = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        self.assertEqual(
            filas[translate_warmup_queue._TASK_NAME],
            (boot + timedelta(seconds=30), int(boot.timestamp())))
        self.assertEqual(
            filas[chart_summary_queue._TASK_NAME], (None, int(boot.timestamp())))

    def test_startup_defer_is_deduplicated_by_queueing_lock(self):
        # Two boots while the first startup sweep is still queued (no
        # consumer running) defer ONE job per task, not two — the
        # AlreadyEnqueued path. A third defer after the queue drains is
        # legitimate and matches the thread's boot tick, not tested here.
        #
        # hsi_sync is dispatched as a BACKGROUND task, not awaited inline
        # (bug fix, 2026-08-09 — see worker._dispatch_hsi_sync_rekick's
        # own docstring: awaiting it here would reopen the exact
        # head-of-line-blocking bug the fix closes), so `_defer_startup_
        # jobs()`'s own synchronous `deferred` count no longer includes
        # it — it logs and counts its own outcome independently once the
        # background task resolves. This test needs to observe that
        # ACTUAL outcome, so it awaits the dispatched task(s) to their
        # own natural completion (`asyncio.wait`, no cancellation) —
        # NOT `worker._drain_hsi_sync_startup_tasks()`, which is the
        # SHUTDOWN policy (cancel immediately, see its own docstring for
        # why) and would kill the just-dispatched rekick before it ever
        # got to run, silently zeroing out the very row this test checks
        # for (a real bug this test caught while it was being written).
        import asyncio
        from app import worker

        async def _wait_for_hsi_sync_dispatch():
            pending = [t for t in worker._HSI_SYNC_STARTUP_TASKS if not t.done()]
            if pending:
                await asyncio.wait(pending)

        async def _twice():
            app = queue_infra.build_app()
            async with app.open_async():
                worker._register_jobs(app)
                first = await worker._defer_startup_jobs(app)
                await _wait_for_hsi_sync_dispatch()
                second = await worker._defer_startup_jobs(app)
                await _wait_for_hsi_sync_dispatch()
            return first, second

        _env.set_env(self,
                     TURNOS_WAITLIST_OFF="1",
                     TURNOS_REMINDERS_OFF="1",
                     TURNOS_HOUSEKEEPING_OFF="1",
                     TURNOS_REFERRAL_SWEEP_OFF="1")
        # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — this test's
        # whole point is hsi_sync's own startup-defer dedup, so it must
        # actually register.
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        first, second = asyncio.run(_twice())
        self.assertEqual(first, 2, "both AI-cache warmups defer at boot "
                         "synchronously — hsi_sync no longer counted here, "
                         "it logs/counts its own outcome from its own "
                         "background task")
        self.assertEqual(second, 0, "the queueing lock dedups the re-defer")
        for task in (chart_summary_queue._TASK_NAME,
                     translate_warmup_queue._TASK_NAME,
                     hsi_sync_queue._TASK_NAME):
            self.assertEqual(len(self._startup_jobs(task)), 1, task)
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM procrastinate_jobs "
                        "WHERE queueing_lock LIKE 'startup:%%' AND task_name "
                        "NOT IN (%s, %s, %s)",
                        (chart_summary_queue._TASK_NAME,
                         translate_warmup_queue._TASK_NAME,
                         hsi_sync_queue._TASK_NAME))
            self.assertEqual(cur.fetchone()[0], 0,
                             "batch-2 jobs get NO startup defer (hsi_resync "
                             "isn't even in the registry)")

    def _purge_effects(self):
        from app.core import translate
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM translations WHERE lang='en'")
        translate.invalidate_rows("en")


class TestAiCacheExecutionLock(_DbProcTest):
    """The contention bar for outbound-capable sweeps: procrastinate's
    queueing lock dedups QUEUED rows only, so execution overlap is
    prevented by the task-level native job `lock` every invocation path
    inherits (exclusive=True at registration — "no two jobs with the
    same lock string can run simultaneously", enforced at fetch). Two
    real-process proofs: startup-vs-startup with two live workers, and
    two independent jobs of one task racing two workers — the cron-vs-
    startup shape (a cron tick is just another job row of the same
    task; the lock cannot tell triggers apart, which is the point).
    The invariant asserted throughout: NEVER two 'doing' rows of the
    task at once, and one business effect."""

    # TURNOS_HSI_SYNC_OFF (review gate, 2026-08-09): this class's contract
    # is chart_summary/AI-cache execution-lock serialization — hsi_sync is
    # unrelated to it and no test in this class exercises it. The
    # harness's usual TURNOS_SKIP_HSI=1 baseline only skips real HSI
    # network calls, NOT registration/boot-kick dispatch, so hsi_sync's
    # own boot-time rekick() was still live here, contending for the same
    # Postgres connection pool as the 25s+25s chart_summary hold this
    # class's timing model depends on — causality confirmed empirically
    # (5/5 clean with it off vs 1/3 failures with it on, same machine,
    # same run). Disabling it here preserves this class's actual
    # serialization proof while removing a genuinely unrelated confound;
    # hsi_sync's OWN cross-component proof (does its boot kick block
    # unrelated queues) stays in TestHsiSyncStartupDecoupling, which does
    # NOT disable it — that is deliberate and unchanged.
    _ENV_BASE = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
                 "TURNOS_HOUSEKEEPING_OFF": "1",
                 "TURNOS_REFERRAL_SWEEP_OFF": "1",
                 "TURNOS_TRANSLATE_WARMUP_OFF": "1",
                 "TURNOS_HSI_SYNC_OFF": "1"}

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _doing_count(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM procrastinate_jobs "
                        "WHERE task_name=%s AND status='doing'",
                        (chart_summary_queue._TASK_NAME,))
            return cur.fetchone()[0]

    def _assert_lock_column(self):
        """The serialization evidence at the SOURCE: every persisted row
        of the task carries the native lock string — not inferred from
        doing-counts alone."""
        esperado = queue_infra.task_lock_name(chart_summary_queue._TASK_NAME)
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id, lock FROM procrastinate_jobs "
                        "WHERE task_name=%s",
                        (chart_summary_queue._TASK_NAME,))
            filas = cur.fetchall()
        self.assertTrue(filas, "at least one job row must exist")
        for jid, lock in filas:
            self.assertEqual(lock, esperado, f"job {jid} lock column")

    def _seed_pair(self, dni, apellido):
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, assigned_doctor_id, "
                "  appointment_at, created) "
                "VALUES (%s, 'Prueba', %s, 'cardiologia', "
                "  'hospital-pinamar', 'aprobada', 'medico-lock', "
                "  now() + interval '4 hours', now())", (dni, apellido))

    def _purge_pair(self, dni, apellido):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM patient_summaries WHERE dni=%s", (dni,))
            _dbtest.delete_booking_requests(cur, "last_name=%s", (apellido,))

    def _gate_env(self, gate_key):
        env = dict(self._ENV_BASE)
        # The held worker waits at the gate while the test boots a second
        # worker and watches the queue (two load-scaled waits of up to
        # 20 s x 8 each). The seam's 30 s default broke that choreography at
        # load ~200 (A's job 'failed' before B's job could wait beside it);
        # the test always releases the gate, so this is only a safety net.
        env.update({"TURNOS_TEST_SEAMS": "1",
                    "TURNOS_TEST_SWEEP_RELEASE_GATE_KEY": gate_key,
                    "TURNOS_TEST_SWEEP_RELEASE_GATE_TIMEOUT_S": "600"})
        return env

    def _hold_env(self, seconds):
        env = dict(self._ENV_BASE)
        env.update({"TURNOS_TEST_SEAMS": "1",
                    "TURNOS_TEST_SWEEP_HOLD_S": str(seconds)})
        return env

    def _gate_lock_key(self, gate_key):
        return "test_sweep_release_gate:" + gate_key

    def _history_until(self, predicate, timeout, label):
        started = time.monotonic()
        history = []
        matched = []

        def _poll():
            states = self._startup_status()
            point = (round(time.monotonic() - started, 3), states)
            if not history or history[-1][1] != states:
                history.append(point)
            self.assertLessEqual(states.count("doing"), 1,
                                 f"two concurrent executions: {history}")
            if predicate(states):
                matched.append(states)
                return True

        try:
            _until(_poll, label, timeout=timeout, interval=0.1)
        except AssertionError as exc:
            if matched or not str(exc).startswith("timed out after"):
                raise
            states = self._startup_status()
            if not history or history[-1][1] != states:
                history.append((round(time.monotonic() - started, 3), states))
            self.fail(f"{label} before {timeout:g}s; status history: {history}")
        return matched[0], history

    def test_release_gate_requires_master_test_seam(self):
        """A copied gate key cannot stall a real worker without the master switch."""
        import psycopg
        gate_key = "ai_cache_inert_" + str(id(self))
        holder = psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)
        holder_cur = holder.cursor()
        holder_cur.execute("SELECT pg_advisory_lock(hashtext(%s))",
                           (self._gate_lock_key(gate_key),))
        self.addCleanup(holder.close)
        _env.set_env(self, TURNOS_TEST_SEAMS=None, TURNOS_TEST_SWEEP_RELEASE_GATE_KEY=gate_key)
        started = time.monotonic()
        queue_infra.test_hold()
        self.assertLess(time.monotonic() - started, 1.0,
                        "a release-gate key without TURNOS_TEST_SEAMS=1 must be inert")

        _env.set_env(self, TURNOS_TEST_SEAMS="1")
        finished = threading.Event()
        errors = []

        def _wait_at_gate():
            try:
                queue_infra.test_hold()
            except Exception as exc:  # noqa: BLE001 - report worker-thread failures
                errors.append(exc)
            finally:
                finished.set()

        waiting = threading.Thread(target=_wait_at_gate, daemon=True)
        waiting.start()
        _wait.stays(lambda: not finished.is_set(),
                    "the armed release gate must wait while its lock is held", for_s=0.2)
        holder_cur.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                           (self._gate_lock_key(gate_key),))
        waiting.join(5)
        self.assertTrue(finished.is_set(), "the released gate must unblock")
        self.assertEqual(errors, [])

    def test_release_gate_transaction_lock_cannot_leak_after_error(self):
        """The pooled connection rollback releases an armed gate on every error path."""
        gate_key = "ai_cache_failed_gate_" + str(id(self))
        lock_key = self._gate_lock_key(gate_key)
        with self.assertRaisesRegex(RuntimeError, "forced gate failure"):
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS acquired",
                            (lock_key,))
                self.assertTrue(cur.fetchone()["acquired"])
                raise RuntimeError("forced gate failure")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS acquired",
                        (lock_key,))
            self.assertTrue(cur.fetchone()["acquired"],
                            "the failed prior call must not strand the pooled gate lock")

    def test_release_gate_timeout_cannot_reach_sweep_effect(self):
        """A broken test rendezvous raises before the guarded sweep body runs."""
        import psycopg
        gate_key = "ai_cache_timeout_gate_" + str(id(self))
        holder = psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)
        holder_cur = holder.cursor()
        holder_cur.execute("SELECT pg_advisory_lock(hashtext(%s))",
                           (self._gate_lock_key(gate_key),))
        effects = []
        self.addCleanup(holder.close)
        _env.set_env(self, TURNOS_TEST_SEAMS="1", TURNOS_TEST_SWEEP_RELEASE_GATE_KEY=gate_key)
        with self.assertRaisesRegex(
                RuntimeError,
                r"test release gate .* was not released within 0\.1s; refusing to run the sweep"):
            queue_infra._wait_for_test_release_gate(timeout_s=0.1, poll_s=0.01)
            effects.append("sweep body ran")
        self.assertEqual(effects, [],
                         "a timed-out gate must fail before the business effect")

    def _startup_status(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "SELECT status FROM procrastinate_jobs WHERE task_name=%s "
                "AND queueing_lock IS NOT NULL ORDER BY id",
                (chart_summary_queue._TASK_NAME,))
            return [r[0] for r in cur.fetchall()]

    def test_startup_vs_startup_two_workers_never_overlap(self):
        # Worker A reaches 'doing' and waits at a test-owned release gate.
        # Worker B then boots while A's row is 'doing' — the queueing lock
        # no longer dedups (A's row left 'todo'), so B defers its OWN startup
        # job… which the native task lock keeps un-fetchable in 'todo'.  The
        # test releases A only after directly observing that state; no fixed
        # 25s sleeps or suite-load timing assumption remains.
        self._seed_pair("99900014", "LockAA")
        self.addCleanup(self._purge_pair, "99900014", "LockAA")
        import psycopg
        gate_key = "ai_cache_startup_" + str(id(self))
        gate_conn = psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)
        gate_cur = gate_conn.cursor()
        gate_cur.execute("SELECT pg_advisory_lock(hashtext(%s))",
                         (self._gate_lock_key(gate_key),))
        released = False

        def _release_gate():
            nonlocal released
            if released:
                return
            released = True
            try:
                gate_cur.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                                 (self._gate_lock_key(gate_key),))
            finally:
                gate_conn.close()

        self.addCleanup(_release_gate)
        wa = self._spawn(self._gate_env(gate_key))
        wa.wait_line("corriendo")
        _states, a_history = self._history_until(
            lambda states: states == ["doing"], 20,
            "A's startup sweep never reached doing")

        wb = self._spawn(self._gate_env(gate_key))
        wb.wait_line("corriendo")
        self._assert_lock_column()
        _states, waiting_history = self._history_until(
            lambda states: states == ["doing", "todo"], 20,
            "B's startup job never waited in todo beside A")
        _release_gate()

        states, completion_history = self._history_until(
            lambda states: states == ["succeeded", "succeeded"], 20,
            "serialized startup jobs never both succeeded")
        self.assertEqual(states, ["succeeded", "succeeded"],
                         "both startup jobs end succeeded, serialized; "
                         f"A={a_history}; waiting={waiting_history}; "
                         f"completion={completion_history}")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM patient_summaries "
                        "WHERE dni='99900014'")
            self.assertEqual(cur.fetchone()[0], 1,
                             "exactly one summary row despite two workers")
        wa.stop()
        wb.stop()

    def test_two_jobs_of_one_task_serialize_across_workers(self):
        # The cron-vs-startup shape without waiting for a real boundary:
        # two independent job rows of the SAME task (what a cron tick
        # next to a startup defer is), two live workers racing them. The
        # native lock lets exactly one be 'doing' at any moment — the
        # other waits at fetch — and the business effect lands once.
        self._seed_pair("99900015", "LockBB")
        self.addCleanup(self._purge_pair, "99900015", "LockBB")

        app = queue_infra.build_app()
        sweep = chart_summary_queue.register(app, 1440)
        app.open()
        try:
            sweep.defer(timestamp=1)
            sweep.defer(timestamp=2)
        finally:
            app.close()

        # The job stays ENABLED (its queue must be consumed); the workers'
        # own startup defers add one more same-lock row, which the
        # _MANUAL_TS filter keeps out of this test's accounting — and the
        # task lock serializes it with the two manual rows anyway.
        env = self._hold_env(10)

        wa = self._spawn(env)
        wb = self._spawn(env)
        wa.wait_line("corriendo")
        wb.wait_line("corriendo")

        def _manual_rows():
            with self._pg() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT status FROM procrastinate_jobs WHERE "
                    f"task_name=%s AND {_MANUAL_TS} ORDER BY id",
                    (chart_summary_queue._TASK_NAME,))
                return [r[0] for r in cur.fetchall()]

        self._assert_lock_column()
        def _both_succeeded():
            estados = _manual_rows()
            # The serialization invariant, sampled across the whole run
            # and over ALL rows of the task (manual + startup):
            self.assertLessEqual(self._doing_count(), 1,
                                 "two concurrent executions of one task")
            return len(estados) == 2 and all(s == "succeeded" for s in estados)
        _until(_both_succeeded, "both manual jobs succeeded", timeout=90, interval=0.25)
        self.assertEqual(_manual_rows(), ["succeeded", "succeeded"])
        self._assert_lock_column()

        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM patient_summaries "
                        "WHERE dni='99900015'")
            filas = cur.fetchone()[0]
        self.assertEqual(filas, 1,
                         "exactly one summary row no matter how the two "
                         "workers raced")
        wa.stop()
        wb.stop()


class _FakeHsiServer:
    """Minimal local HTTP server implementing just enough of the real HSI
    REST surface for HsiClient's REAL, unmodified login/ensure_patient/
    create_appointment methods (connectors/hsi/hsi_client.py) to run
    genuinely over HTTP inside a SEPARATE subprocess worker — not a
    scripted push_turno stand-in and not an in-process object patch,
    which can't cross a process boundary. Tracks created appointments so
    a SECOND real HTTP round trip (a retried replay) exercises
    create_appointment's own patient+diary+date dedup branch
    (hsi_client.py:737) for real."""

    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        self.appointments = []
        self._next_id = 1
        self.create_calls = 0
        state = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: ANN002 — silence per-request logging
                pass

            def _json(self, code, body, cookie=None):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if cookie:
                    self.send_header("Set-Cookie", f"token={cookie}; Path=/")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                if self.path == "/api/auth":
                    self._json(200, True, cookie="faketoken")
                    return
                if self.path.startswith("/api/institutions/1/medicalConsultations/appointments"):
                    state.create_calls += 1
                    appt_id = state._next_id
                    state._next_id += 1
                    state.appointments.append({
                        "id": appt_id, "date": body["date"], "hour": body["hour"],
                        "appointmentStateId": 1,
                        "patient": {"id": str(body["patientId"])},
                    })
                    self._json(200, appt_id)
                    return
                self._json(404, {"error": "unmatched POST " + self.path})

            def do_GET(self):
                from urllib.parse import urlsplit, parse_qs
                p = urlsplit(self.path)
                qs = parse_qs(p.query)
                if p.path == "/api/institution/1/healthcareprofessional":
                    self._json(200, [{"id": 1, "fullName": "Dr Fake"}])
                    return
                if p.path == "/api/institutions/1/medicalConsultations/diary":
                    self._json(200, [{"id": 1, "clinicalSpecialtyName": "Cardiología",
                                       "doctorsOfficeDescription": "Consultorio 1",
                                       "startDate": "2020-01-01", "endDate": "2030-01-01",
                                       "appointmentDuration": 30, "isExpired": False}])
                    return
                if p.path == "/api/institutions/1/medicalConsultations/diary/1":
                    hours = [{"openingHours": {"dayWeekId": d, "from": "09:00:00",
                                                "to": "17:00:00", "id": 100 + d}}
                             for d in range(1, 8)]
                    self._json(200, {"appointmentDuration": 30, "startDate": "2020-01-01",
                                      "endDate": "2030-01-01", "diaryOpeningHours": hours})
                    return
                if p.path == "/api/patient/minimalsearch":
                    self._json(200, [555])  # always "found" — patient create not exercised
                    return
                if p.path == "/api/institutions/1/medicalConsultations/appointments/diary/1":
                    on_date = (qs.get("from") or [""])[0]
                    self._json(200, [a for a in state.appointments if a["date"] == on_date])
                    return
                self._json(404, {"error": "unmatched GET " + self.path})

        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class TestHsiSyncCrashRecoveryAndContention(_CrashRecoveryMixin, _DbProcTest):
    """Batch 4's own two-worker/crash proof for the durable HSI journal —
    named separately from TestAiCacheCrashRecovery/TestAiCacheExecutionLock
    (those stay AI-specific) even though the underlying mechanism is
    IDENTICAL code: `register_periodic_sweep(exclusive=True)`, the same
    native per-task lock, the same startup-defer and stalled-job-recovery
    paths. test_hsi_queue_proof.py deliberately narrowed this out as
    follow-up; this class closes it — the card's acceptance gate names
    "native task locking, two-worker contention, retry/interruption
    recovery" explicitly for HSI, not just "the mechanism exists
    elsewhere".

    TURNOS_SKIP_HSI=1 throughout the CONTENTION/LOCK cases: tick()'s
    network phases become no-ops (module contract), which is what makes
    hsi_sync's task body deterministic there — exactly the same "stay on
    the fake/no-op path so the crash/lock proof isolates the QUEUE
    mechanism, not HSI's own behavior" discipline TURNOS_FAKE_AI=1 gives
    the AI-cache suites. The remote-accepted/local-uncommitted window gets
    its OWN two proofs below, deliberately NOT on the no-op path: a
    same-process crash simulation
    (test_remote_accepted_local_uncommitted_window_never_double_books,
    real outbox row + the real HsiClient.create_appointment dedup branch
    via a stateful in-process fake) and a genuine subprocess SIGKILL
    (test_genuine_sigkill_inside_remote_accepted_window, real worker
    process killed while paused — TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S —
    strictly AFTER a fake local HSI HTTP server confirms the booking,
    observing the outbox row land in 'sending' with an active lease, not
    'error', then reclaimed once the lease is force-expired). HSI-down/
    backoff classification itself is unchanged code, already proven at
    the unit level in test_hsi_sync.py (TestOutboxReplay) — not
    re-proven here."""

    _ENV_FOCO = {"TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
                 "TURNOS_HOUSEKEEPING_OFF": "1",
                 "TURNOS_REFERRAL_SWEEP_OFF": "1",
                 "TURNOS_SUMMARY_PREGEN": "0",
                 "TURNOS_TRANSLATE_WARMUP_OFF": "1",
                 "TURNOS_SKIP_HSI": "1",
                 "TURNOS_WORKER_STALLED_S": "2",
                 "TURNOS_TEST_UNSAFE_STALLED": "1",
                 "TURNOS_TEST_SEAMS": "1",
                 # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — this
                 # whole class's subject is the hsi_sync job itself, so
                 # every spawned worker here needs it registered.
                 "CONNECTOR_EMR_SYNC": "hsi"}

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def test_hsi_sync_survives_mid_task_sigkill_exactly_once(self):
        def effect_ok():
            # TURNOS_SKIP_HSI=1 means tick() touched nothing outbound —
            # the assertion is on the JOB, not a business row: the
            # replacement's single execution is what _crash_mid_task_
            # then_recover already checked via _started_events == 2;
            # this closes the loop by confirming no SECOND 'doing' ever
            # coexisted with the first (the lock, not just the recovery
            # pass, is what this test is really about).
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT count(*) FROM procrastinate_jobs "
                            "WHERE task_name=%s AND status='doing'",
                            (hsi_sync_queue._TASK_NAME,))
                self.assertEqual(cur.fetchone()[0], 0,
                                 "no job left 'doing' after recovery")

        self._crash_mid_task_then_recover(
            hsi_sync_queue.register, hsi_sync_queue._TASK_NAME, effect_ok)

    def test_two_hsi_sync_jobs_serialize_across_two_workers(self):
        # The cron-vs-startup shape: two independent rows of the SAME
        # task, two live workers racing them — never two 'doing' at once,
        # both converge to 'succeeded'.
        app = queue_infra.build_app()
        sweep = hsi_sync_queue.register(app, 5)
        app.open()
        try:
            sweep.defer(timestamp=1)
            sweep.defer(timestamp=2)
        finally:
            app.close()

        env = dict(self._ENV_FOCO)
        # TURNOS_WORKER_STALLED_S raised from _ENV_FOCO's fast "2"
        # (review gate, 2026-08-09, same reasoning as the rolling-restart
        # and cold-boot proofs below): neither worker here ever crashes,
        # so a low threshold risks one worker's own startup recovery
        # misjudging the OTHER's still-alive 'doing' job as stalled.
        env.update({"TURNOS_TEST_SWEEP_HOLD_S": "8", "TURNOS_WORKER_STALLED_S": "60"})
        wa = self._spawn(env)
        wb = self._spawn(env)
        wa.wait_line("corriendo")
        wb.wait_line("corriendo")

        def _manual_rows():
            with self._pg() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT status FROM procrastinate_jobs WHERE "
                    f"task_name=%s AND {_MANUAL_TS} ORDER BY id",
                    (hsi_sync_queue._TASK_NAME,))
                return [r[0] for r in cur.fetchall()]

        def _doing_count():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT count(*) FROM procrastinate_jobs "
                            "WHERE task_name=%s AND status='doing'",
                            (hsi_sync_queue._TASK_NAME,))
                return cur.fetchone()[0]

        esperado = queue_infra.task_lock_name(hsi_sync_queue._TASK_NAME)
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT lock FROM procrastinate_jobs WHERE task_name=%s",
                        (hsi_sync_queue._TASK_NAME,))
            locks = [r[0] for r in cur.fetchall()]
        self.assertTrue(locks)
        self.assertEqual(set(locks), {esperado})

        def _both_succeeded():
            estados = _manual_rows()
            self.assertLessEqual(_doing_count(), 1,
                                 "two concurrent executions of one task")
            return len(estados) == 2 and all(s == "succeeded" for s in estados)
        _until(_both_succeeded, "both manual jobs succeeded", timeout=60, interval=0.25)
        self.assertEqual(_manual_rows(), ["succeeded", "succeeded"])
        wa.stop()
        wb.stop()

    def test_remote_accepted_local_uncommitted_window_never_double_books(self):
        """The specific crash window the card names: HSI confirms a create
        remotely, then Sana dies BEFORE persisting the local outbox/mirror
        write. Real Postgres row throughout (hsi_outbox + booking_requests
        via db_hsi.hsi_outbox_ack_appointment, not a mocked db.* function like
        test_hsi_sync.py's TestOutboxReplay) — the mock boundary is only
        the network client.

        Codex review fix (2026-08-09): the first draft's fake scripted
        push_turno's return value directly ("just return appointment_id 777
        again") — that proves the fake's script, not the real dedup code.
        `_StatefulHsiFake` below instead runs the REAL, unmodified
        `HsiClient.create_appointment` (connectors/hsi/hsi_client.py:699)
        on both attempts, faking only the documented lower I/O seam —
        `_booked_hours`' own docstring calls itself "the long-standing test
        seam where _booked_hours is substituted" (hsi_client.py:483) — plus
        the diary/login/create-HTTP boundaries `create_appointment` never
        exposes another way. The dedup branch that fires on attempt #2 (the
        `existing = next(...)` match at hsi_client.py:737, verified via
        `deduplicated: True` in the returned dict, a field ONLY that real
        branch sets) is therefore the actual production code, driven by an
        in-memory appointments store this test controls — not a
        stand-in claiming to model it.

        Simulated crash: db_hsi.hsi_outbox_ack_appointment (the one
        transaction that links the booking and settles the intent) raises
        on attempt #1 (the
        "process died after HSI's real 'ok' and before the local write"
        moment) — _replay_one's except-clause is generic, so this lands the
        row in 'error', durably retryable (the outbox's own contract, not a
        special case). A forced reset (the same db_hsi.hsi_outbox_reset the
        admin "Reintentar" button uses) stands in for the next tick/replay
        that would naturally reclaim it in production; nothing about
        _replay_one's own logic is bypassed."""
        # This module sets TURNOS_SKIP_HSI=1 at import time (line 59) and
        # never restores it — the documented cross-module pollution
        # test_hsi_sync.py/test_hsi_cassette.py already pop/restore around.
        # hsi_sync.replay() checks this flag FIRST and short-circuits to 0
        # without even claiming a row, so it must be popped for the real
        # replay path (and the real HSI-client call, faked below) to run.
        _env.set_env(self, TURNOS_SKIP_HSI=None)
        # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — hsi_sync.
        # replay() no-ops via _emr_selected() with no connector configured.
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, appointment_at, created) "
                "VALUES ('99900016', 'Prueba', 'CrashWindow', 'cardiologia', "
                "  'hospital-pinamar', 'aprobada', "
                "  now() + interval '5 hours', now()) RETURNING id")
            sid = cur.fetchone()[0]
        self.addCleanup(self._purge_crash_window, sid)

        row = db_booking.get_booking_request(sid)
        turno_dt = row["turno_dt"]
        hsi_sync.enqueue_appointment(row, turno_dt)

        attempts = {"n": 0}
        real_ack = db_hsi.hsi_outbox_ack_appointment

        def flaky_ack(item, result, followup):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("simulated crash: died after HSI accepted, "
                                  "before the local write landed")
            return real_ack(item, result, followup)

        class _StatefulHsiFake:
            """push_turno, backed by a REAL HsiClient.create_appointment
            call each time — only _ensure_login/_match_diary/diary_detail/
            _slots_for_weekday/_booked_hours/_post are stubbed (the I/O
            boundary create_appointment itself has no other seam for). The
            in-memory `store` persists across both push_turno calls on
            THIS instance, exactly like HSI's own remote appointments list
            would carry attempt #1's booking into attempt #2's view."""

            def __init__(self):
                self.push_calls = 0
                self.remote_book_calls = 0
                self._store = []
                self._next_id = 1

            def push_turno(self, dni, nombre, apellido, nacimiento_iso, sexo,
                           especialidad, when_iso, medico=None, sobreturno=False):
                self.push_calls += 1
                client = hsi_client.HsiClient()
                client._ensure_login = lambda: None
                client._match_diary = lambda diary_hint, professional_hint=None: {
                    "diary_id": 1, "specialty": "Cardiología", "professional": "Dr Fake"}
                client.diary_detail = lambda diary_id: {
                    "appointmentDuration": 30, "startDate": "2020-01-01",
                    "endDate": "2030-01-01"}
                client._slots_for_weekday = lambda detail, weekday: [(9 * 3600, 17 * 3600, 42)]

                store = self._store

                def _booked_hours(diary_id, on_date):
                    # Mirrors the real method's own side effect
                    # (hsi_client.py:474-490): populate _appointments_cache
                    # from what the "remote" already has for this day —
                    # that cache is what the real dedup branch reads.
                    appts = [a for a in store if a["date"] == on_date]
                    client._appointments_cache = getattr(client, "_appointments_cache", {})
                    client._appointments_cache[(int(diary_id), str(on_date))] = appts
                    return {a["hour"] for a in appts if a.get("appointmentStateId") != 4}
                client._booked_hours = _booked_hours

                def _post(path, body=None, params=None):
                    self.remote_book_calls += 1
                    appt_id = self._next_id
                    self._next_id += 1
                    store.append({"id": appt_id, "date": body["date"], "hour": body["hour"],
                                 "appointmentStateId": 1,
                                 "patient": {"id": str(body["patientId"])}})
                    return appt_id
                client._post = _post

                result = client.create_appointment(
                    "555", especialidad, when_iso, professional_hint=medico,
                    overturn=bool(sobreturno), exact=bool(medico))
                result.setdefault("checkpoint", {})
                return result

        fake = _StatefulHsiFake()

        with patch.object(db_hsi, "hsi_outbox_ack_appointment", flaky_ack), \
             patch("app.connectors.hsi.hsi_client", fake, create=True):
            replayed_1 = hsi_sync.replay()
        self.assertEqual(replayed_1, 0,
                         "the crashed attempt confirms nothing — "
                         "_replay_one's except-clause caught the raise")
        self.assertEqual(fake.push_calls, 1, "HSI WAS called once — the "
                         "crash is on the LOCAL side, after the remote 'ok'")
        self.assertEqual(fake.remote_book_calls, 1,
                         "one real booking made remotely (via the fake's "
                         "_post) on the crashed attempt")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT state FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            self.assertEqual(cur.fetchone()[0], "error",
                             "durably retryable, never silently dropped")

        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            outbox_id = cur.fetchone()[0]
        db_hsi.hsi_outbox_reset(outbox_id)  # the next tick's natural reclaim, forced now

        with patch("app.connectors.hsi.hsi_client", fake, create=True):
            replayed_2 = hsi_sync.replay()
        self.assertEqual(replayed_2, 1, "the retry confirms — real db_hsi.hsi_outbox_ack_appointment now")
        self.assertEqual(fake.push_calls, 2,
                         "HSI called AGAIN (Sana's local state forgot the "
                         "first accept) — this is the residual cost the "
                         "rerun contract documents, not eliminated by it")
        self.assertEqual(fake.remote_book_calls, 1,
                         "the REAL create_appointment dedup branch fired on "
                         "attempt #2 — no second remote booking call")

        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT hsi_appointment_id, hsi_status FROM "
                        "booking_requests WHERE id=%s", (sid,))
            appt_id, status = cur.fetchone()
            cur.execute("SELECT state FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            outbox_state = cur.fetchone()[0]
        self.assertEqual(appt_id, "1",
                         "SAME appointment id both attempts — the real "
                         "create-dedup probe, not a second booking")
        self.assertEqual(status, "ok")
        self.assertEqual(outbox_state, "confirmed")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM hsi_outbox WHERE entity_id=%s",
                        (str(sid),))
            self.assertEqual(cur.fetchone()[0], 1,
                             "one outbox row throughout — no duplicate intent")

    def _purge_crash_window(self, sid):
        with self._pg() as c, c.cursor() as cur:
            # The fake HsiClient's dedup path writes REAL hsi_appointments/
            # hsi_checkpoints mirror rows (push_turno's own checkpoint
            # default, unconditional) — leaving them contaminates any
            # later test whose fake also mints appointment id "1".
            cur.execute("DELETE FROM hsi_appointments WHERE hsi_appointment_id='1'")
            cur.execute("DELETE FROM hsi_checkpoints WHERE entity_type='appointment' AND entity_id='1'")
            cur.execute("DELETE FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            _dbtest.delete_booking_requests(cur, "id=%s", (sid,))

    def test_genuine_sigkill_inside_remote_accepted_window(self):
        """The card's literal ask: a REAL SIGKILL (not a raised Python
        exception) genuinely inside the remote-accepted/local-uncommitted
        window, against a real subprocess worker and a real (local, fake)
        HSI HTTP server — not a same-process simulation.

        Choreography:
        1. Seed a real hsi_outbox 'create' row.
        2. Spawn worker A with TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S set: its
           startup-deferred hsi_sync sweep claims the row (state='sending',
           a 5-min lease), calls the fake server's REAL create_appointment
           path (one booking made), then PAUSES — genuinely, inside
           _replay_one, after the remote 'ok' and before the local write.
        3. Wait for the row to actually be observed 'sending' (not
           inferred), SIGKILL worker A's whole process group.
        4. Assert the row is 'sending' with an ACTIVE (non-expired) lease
           — NOT 'error': a real crash here does not go through
           _replay_one's except-clause at all, unlike a raised exception.
        5. Force the lease into the past (simulates the 5 real minutes
           passing — a legitimate technique: this is TIME, not the crash,
           which already genuinely happened).
        6. Spawn worker B (no hold): its startup recovery requeues A's
           stalled job; the re-run's hsi_outbox_claim() now sees the
           expired lease and reclaims the SAME row. The retried
           create_appointment call hits the fake server's diary+date
           listing, finds the appointment worker A already booked, and
           the REAL dedup branch confirms it — never a second POST.
        """
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, appointment_at, created) "
                "VALUES ('99900020', 'Prueba', 'GenuineSigkillWindow', "
                "  'cardiologia', 'hospital-pinamar', 'aprobada', "
                "  now() + interval '9 hours', now()) RETURNING id")
            sid = cur.fetchone()[0]
        self.addCleanup(self._purge_genuine_sigkill_row, sid)

        row = db_booking.get_booking_request(sid)
        # Real Postgres access from THIS process, via the queue module's
        # own function — the DB is the only thing worker A/B and this
        # test process share.
        from app.modules.turnos import hsi_journal as _hsi_sync_mod
        _hsi_sync_mod.enqueue_appointment(row, row["turno_dt"])

        fake = _FakeHsiServer()
        self.addCleanup(fake.stop)

        env_base = dict(self._ENV_FOCO)
        env_base["TURNOS_SKIP_HSI"] = ""  # this test's whole point: exercise the real path
        env_base["HSI_URL"] = f"http://127.0.0.1:{fake.port}"

        env_hold = dict(env_base)
        env_hold["TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S"] = "20"
        wa = self._spawn(env_hold)
        wa.wait_line("corriendo")

        def _outbox_row():
            with self._pg() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT state, lease_expires_at > now() FROM hsi_outbox "
                    "WHERE entity_id=%s", (str(sid),))
                return cur.fetchone()

        # 'sending' alone is written at CLAIM time, BEFORE the HTTP create
        # — waiting on state alone races ahead of the actual remote
        # acceptance. Require the create to have genuinely landed too
        # (fake.create_calls == 1) before treating the window as open.
        def _window_open():
            row2 = _outbox_row()
            if row2 and row2[0] == "sending" and row2[1] and fake.create_calls == 1:
                return row2
        estado = _until(_window_open, "the row reached ('sending', active lease) "
                             "with a landed remote create", timeout=30)
        self.assertTrue(estado[1], "lease must still be ACTIVE at the "
                        "moment of the kill — this is the window")
        self.assertEqual(fake.create_calls, 1,
                         "exactly one remote booking before the kill")

        wa._killpg()
        wa.proc.wait(10)
        # TURNOS_WORKER_STALLED_S=2 (this class's _ENV_FOCO): worker B's
        # startup recovery only requeues jobs whose heartbeat is > 2s
        # stale. Waiting less than that races ahead of the recovery this
        # test means to exercise — B would boot, see A as still "live",
        # and this test would end up waiting on the 5-minute steady-state
        # maintenance pass instead of proving the startup-recovery path.
        self._wait_heartbeat_stale(self._ENV_FOCO["TURNOS_WORKER_STALLED_S"])

        # Confirm the GENUINE-crash state: 'sending', not 'error' — the
        # exact difference a raised-exception simulation cannot show.
        estado_tras_kill = _outbox_row()
        self.assertEqual(estado_tras_kill[0], "sending",
                         "a real SIGKILL never reaches _replay_one's "
                         "except-clause — the row stays exactly where the "
                         "claim left it, unlike a caught exception")

        # Time passing (the lease's own 5-minute contract), not a second
        # crash — force it into the past so the test doesn't wait 5 real
        # minutes for what is, by design, a durable timeout.
        with self._pg() as c, c.cursor() as cur:
            cur.execute("UPDATE hsi_outbox SET lease_expires_at = "
                       "now() - interval '1 second' WHERE entity_id=%s",
                       (str(sid),))

        env_replacement = dict(env_base)
        wb = self._spawn(env_replacement)
        wb.wait_line("corriendo")

        _until(lambda: (_outbox_row() or ("",))[0] == "confirmed",
                    "the replacement reclaimed the lease-expired row", timeout=40)
        self.assertEqual(fake.create_calls, 1,
                         "the retry's create_appointment call found the "
                         "SAME appointment via the real dedup branch — "
                         "NEVER a second remote booking")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT hsi_appointment_id FROM booking_requests "
                        "WHERE id=%s", (sid,))
            self.assertEqual(cur.fetchone()[0], "1",
                             "the one appointment the fake server ever created")
            cur.execute("SELECT count(*) FROM hsi_outbox WHERE entity_id=%s",
                        (str(sid),))
            self.assertEqual(cur.fetchone()[0], 1, "one outbox row throughout")
        wb.stop()

    def _purge_genuine_sigkill_row(self, sid):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM hsi_appointments WHERE hsi_appointment_id='1'")
            cur.execute("DELETE FROM hsi_checkpoints WHERE entity_type='appointment' AND entity_id='1'")
            cur.execute("DELETE FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            _dbtest.delete_booking_requests(cur, "id=%s", (sid,))

    def _rows_for_task(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id, status FROM procrastinate_jobs "
                        "WHERE task_name=%s ORDER BY id",
                        (hsi_sync_queue._TASK_NAME,))
            return cur.fetchall()

    def _cold_boot_race(self, a_skip_lock, b_skip_lock):
        """Event-driven, luck-free choreography (redesign, 2026-08-09,
        reviewer finding — SUPERSEDES the earlier fixed-6s-hold version:
        "6 seconds is enough for a real worker to boot" was still a
        timing ASSUMPTION, not a structural guarantee, however
        empirically it was chosen. This version never waits a fixed
        duration to decide when it's SAFE to act — every step below
        waits for something actually OBSERVED instead):

        1. The TEST itself acquires a dedicated release-gate advisory
           lock (`hsi_sync_queue._maybe_rekick_release_gate`'s own
           mechanism, a DIFFERENT lock key than rekick()'s real
           coordination lock) BEFORE spawning anyone.
        2. B spawns FIRST, alone, with TURNOS_TEST_HSI_REKICK_GATE_KEY
           set to that SAME key — its own startup rekick() checks
           chain_alive() (False, genuine cold start), logs
           "rekick: chain_alive=False, procediendo", then blocks polling
           for the TEST's gate lock (cooperatively — this never depends
           on how long that polling takes). The test waits for that
           EXACT log line before spawning A, so A's boot is KNOWN to
           start only after B's decision is already made — no
           launch-order luck.
        3. A spawns. Both A and B carry TURNOS_TEST_SWEEP_HOLD_S=15 on a
           quiet machine, times the load per CPU under load
           (holds the TICK BODY itself once fetched into 'doing') so
           whichever job lands first stays 'doing' throughout the
           assertion window and can never self-reschedule a legitimate
           continuation that would be misread as a second race casualty.
        4. What the test waits for NEXT, before releasing the gate,
           differs by branch — and is the actual fix (see each caller's
           own docstring for the reasoning, not just "wait N seconds"):
           - RED (b_skip_lock=True — B holds no real lock, cannot block
             anyone): wait for A's job to genuinely reach 'doing', a
             real DB poll, no assumption about how long that takes.
           - GREEN (b_skip_lock=False — B holds the REAL lock while
             gate-waiting): wait for A's OWN
             "rekick: lock contendido, esperando" log line —
             `_acquire_rekick_lock()`'s own proof that A tried and
             genuinely failed to acquire the real lock at least once,
             i.e. that B still holds it at THAT exact moment.
        5. ONLY THEN does the test release the gate — an explicit signal
           it controls, never a timer expiring on its own. B wakes,
           logs "rekick: hold terminado, prosiguiendo", and acts on its
           (possibly stale) decision.
        6. `a_skip_lock`/`b_skip_lock` also select whether each worker's
           OWN rekick() acquires the real Postgres advisory lock or
           skips it (double-gated test seam, unrelated to the gate
           above). Returns the rows for hsi_sync's task, polled to a
           STABLE final state (not a single snapshot) while still well
           inside the first job's 15s tick-body hold, plus that first
           job's own id. Cleanup is SIGKILL (`cleanup()`, not `stop()`):
           these tests deliberately leave a job 'doing' by design.

        TURNOS_WORKER_STALLED_S overridden to a SAFE value (review gate,
        2026-08-09 — reviewer finding): NEITHER worker here is ever
        genuinely killed during the measurement window (cleanup()
        SIGKILLs only in teardown, after assertions run), so this
        class's own _ENV_FOCO default of "2" — correct ONLY for the
        genuine-crash tests elsewhere in this class — would let
        whichever worker's job reaches 'doing' first be misjudged as
        stalled by the OTHER worker's own one-shot startup recovery pass
        and wrongly reclaimed, corrupting the row-state this proof
        asserts on (and, for the GREEN test specifically, doing so for
        the WRONG reason — a spurious reclaim producing a second row has
        nothing to do with whether the advisory lock actually serialized
        the two kickers). A threshold safely above the whole race's real
        duration removes this by construction."""
        import psycopg
        gate_key = "cold_boot_" + str(id(self)) + "_" + str(a_skip_lock) + str(b_skip_lock)
        gate_conn = psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)
        gate_cur = gate_conn.cursor()
        gate_cur.execute("SELECT pg_advisory_lock(hashtext(%s))",
                         ("test_rekick_gate:" + gate_key,))
        released = {"done": False}

        def _release_gate():
            if released["done"]:
                return
            released["done"] = True
            try:
                gate_cur.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                                 ("test_rekick_gate:" + gate_key,))
            finally:
                gate_conn.close()

        self.addCleanup(_release_gate)  # safety net only — released explicitly below

        # The hold must outlast A's boot plus the stabilization poll, both at
        # the machine's speed: a quiet machine's 15 s times the load per CPU.
        hold_s = str(max(15, round(_wait.scaled(15))))
        env_b = dict(self._ENV_FOCO)
        env_b["TURNOS_TEST_SWEEP_HOLD_S"] = hold_s
        env_b["TURNOS_TEST_HSI_REKICK_GATE_KEY"] = gate_key
        # B waits at the gate through A's whole boot and the wait for A's
        # proof (load-scaled waits of up to 20 s x 8 each). The seam's 30 s
        # default let B give up at load ~215 ("cadena ya viva" in A, B's row
        # never raced); the test always releases the gate.
        env_b["TURNOS_TEST_HSI_REKICK_GATE_TIMEOUT_S"] = "600"
        env_b["TURNOS_WORKER_STALLED_S"] = "60"
        if b_skip_lock:
            env_b["TURNOS_TEST_HSI_SKIP_ADVISORY_LOCK"] = "1"
        wb = self._spawn(env_b)
        self.addCleanup(wb.cleanup)
        wb.wait_line("corriendo")
        wb.wait_line("rekick: chain_alive=False, procediendo")
        # B is now genuinely blocked polling the TEST's own gate lock —
        # true regardless of b_skip_lock, which only controls whether B
        # ALSO holds the real coordination lock while it waits there.

        env_a = dict(self._ENV_FOCO)
        env_a["TURNOS_TEST_SWEEP_HOLD_S"] = hold_s
        env_a["TURNOS_WORKER_STALLED_S"] = "60"
        if a_skip_lock:
            env_a["TURNOS_TEST_HSI_SKIP_ADVISORY_LOCK"] = "1"
        wa = self._spawn(env_a)
        self.addCleanup(wa.cleanup)
        wa.wait_line("corriendo")

        if b_skip_lock:
            # RED: B holds no real lock, so only A's own progress matters
            # — wait for its job to be OBSERVED 'doing', not for a clock.
            def _a_doing():
                filas = self._rows_for_task()
                return filas and filas[0][1] == "doing"
            _until(_a_doing, "A's job reached 'doing' before the gate could be "
                        "released (else B's later insert cannot race a live row)", timeout=20)
        else:
            # GREEN: B holds the REAL lock while gate-waiting, so A must
            # be genuinely blocked trying to acquire it — wait for A's
            # OWN proof of a failed attempt, not an assumed elapsed time.
            wa.wait_line("rekick: lock contendido, esperando", timeout=20)

        # Release the gate NOW that the relevant condition has been
        # directly observed — an explicit signal, not a timer.
        _release_gate()
        wb.wait_line("rekick: hold terminado, prosiguiendo", timeout=15)

        # Poll to a STABLE state: require the SAME snapshot on two
        # consecutive reads (a beat apart) before trusting it — catches
        # both "B hasn't finished its defer yet" and "a delayed second
        # insert is still in flight". This part is deliberately a poll
        # (not another gate) — it observes an EVENTUAL outcome, it is
        # not synchronizing two processes' causal order. A first row still
        # 'todo' is not an outcome yet: no worker has fetched it (at load
        # ~300 on 18 CPUs two reads 0.5 s apart both saw it, 2026-11-06),
        # and the beat between reads runs at the machine's speed too.
        anterior = []

        def _estable():
            actual = self._rows_for_task()
            igual = (bool(actual) and actual[0][1] != "todo"
                     and actual == (anterior[-1] if anterior else None))
            anterior.append(actual)
            return actual if igual else None
        estable = _until(_estable, "row state stabilized", timeout=15,
                         interval=_wait.scaled(0.5))
        self.assertTrue(estable[0][1] == "doing",
                        f"the first job must be genuinely 'doing' (still "
                        f"inside its {hold_s}s hold) at snapshot time: {estable}")
        return estable, estable[0][0]

    def test_simultaneous_cold_boot_race_is_real_without_the_lock(self):
        """RED proof: A keeps its real lock (uncontested — B never
        acquires one), so A proceeds immediately: chain_alive() False,
        defers, its job reaches 'doing' — the test directly OBSERVES this
        (a real DB poll, see _cold_boot_race) before ever releasing B's
        gate. B's advisory lock is SKIPPED
        (TURNOS_TEST_HSI_SKIP_ADVISORY_LOCK=1, double-gated test seam) —
        B already decided chain_alive()==False (stale, from BEFORE A's
        job existed — proven by the log-line-before-spawn ordering in
        _cold_boot_race) and, once the test releases its gate (only
        after confirming A is 'doing'), acts on that stale decision with
        no lock to stop it: A's row is 'doing' (not 'todo'), so the
        queueing lock's todo-only uniqueness does not block B's insert.
        The causal chain is therefore fully observed, not inferred:
        B decided BEFORE A existed, B acted demonstrably AFTER A was
        'doing'. Exactly {doing: 1, todo: 1} — the race, reproduced
        deterministically, proving the GREEN test below relies on the
        lock, not luck."""
        filas, primer_id = self._cold_boot_race(a_skip_lock=False, b_skip_lock=True)
        estados = sorted(f[1] for f in filas)
        self.assertEqual(estados, ["doing", "todo"],
                         f"the race must be REPRODUCED without B's lock — "
                         f"expected exactly one 'doing' (A) plus one "
                         f"'todo' (B's unguarded insert): {filas}")

    def test_simultaneous_cold_boot_never_double_kicks(self):
        """GREEN proof: the IDENTICAL choreography, B's lock intact. B
        acquires the REAL advisory lock, checks chain_alive() (False),
        logs the sync line, and blocks at the TEST's gate WHILE STILL
        HOLDING THE REAL LOCK. A boots (after B's first sync log) and
        tries to acquire the SAME real lock — blocks immediately, before
        it can even check chain_alive() (and therefore never logs its
        own decision line) — the test directly OBSERVES this via A's OWN
        "rekick: lock contendido, esperando" log line (proof of a
        genuinely failed attempt) before ever releasing B's gate. Only
        then does B wake, defer (uncontested — A is still polling for
        the real lock) and release it. A then acquires it, checks
        chain_alive() (now True — B's row exists), and returns without
        deferring anything. Exactly ONE row, 'doing', for the whole
        race — every step of this ordering was directly observed by the
        test, never assumed from elapsed time."""
        filas, primer_id = self._cold_boot_race(a_skip_lock=False, b_skip_lock=False)
        self.assertEqual(len(filas), 1,
                         f"the advisory lock must have serialized the two "
                         f"kickers down to ONE row: {filas}")
        self.assertEqual(filas[0][1], "doing", filas)
        self.assertEqual(_started_events(primer_id), 1,
                         "exactly one execution — never a genuine "
                         "back-to-back tick from the two simultaneous kicks")

    def test_rekick_skip_lock_flag_alone_is_inert(self):
        """The double-gate itself, proven directly (no subprocess
        needed): TURNOS_TEST_HSI_SKIP_ADVISORY_LOCK=1 WITHOUT
        TURNOS_TEST_SEAMS=1 must be a complete no-op — a real deployment
        that somehow inherited this one stray env line (a copied .env, a
        pasted Coolify var) must never silently disable the lock."""
        from app.modules.turnos import hsi_journal_queue as hsq
        _env.set_env(self, TURNOS_TEST_SEAMS=None, TURNOS_TEST_HSI_SKIP_ADVISORY_LOCK="1")
        self.assertFalse(hsq._rekick_skip_advisory_lock(),
                         "the flag alone (no TURNOS_TEST_SEAMS=1) must "
                         "be inert")
        _env.set_env(self, TURNOS_TEST_SEAMS="1")
        self.assertTrue(hsq._rekick_skip_advisory_lock(),
                        "both vars together must actually arm the seam")

    def test_rekick_hold_flag_alone_is_inert(self):
        """Same double-gate proof for TURNOS_TEST_HSI_REKICK_HOLD_S: the
        flag alone (no TURNOS_TEST_SEAMS=1) must not hold at all — timed
        directly, not inferred, since _maybe_rekick_hold() has no return
        value to assert on."""
        import asyncio
        from app.modules.turnos import hsi_journal_queue as hsq
        _env.set_env(self, TURNOS_TEST_SEAMS=None, TURNOS_TEST_HSI_REKICK_HOLD_S="5")
        started = time.monotonic()
        asyncio.run(hsq._maybe_rekick_hold())
        self.assertLess(time.monotonic() - started, 1.0,
                        "the hold flag alone (no TURNOS_TEST_SEAMS=1) "
                        "must be inert")

        _env.set_env(self, TURNOS_TEST_SEAMS="1", TURNOS_TEST_HSI_REKICK_HOLD_S="1")
        started = time.monotonic()
        asyncio.run(hsq._maybe_rekick_hold())
        self.assertGreaterEqual(time.monotonic() - started, 0.9,
                                "both vars together must actually hold")

    def test_force_reschedule_fail_flag_alone_is_inert(self):
        """Same double-gate proof for
        TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE (review gate,
        2026-08-09 — this seam had no dedicated inertness test alongside
        the two above): the flag alone (no TURNOS_TEST_SEAMS=1) must
        never raise — a real deployment that somehow inherited this one
        stray env line (a copied .env, a pasted Coolify var) must never
        have its self-reschedule silently start failing. Calls
        _maybe_force_reschedule_failure() directly (it's a plain sync
        function, no event loop needed) and resets the module-level
        one-shot flag around each half so the two assertions don't
        interfere with each other."""
        from app.modules.turnos import hsi_journal_queue as hsq
        old_flag = hsq._force_reschedule_fail_once
        try:
            _env.set_env(self,
                         TURNOS_TEST_SEAMS=None,
                         TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE="1")
            hsq._force_reschedule_fail_once = False
            hsq._maybe_force_reschedule_failure()  # must not raise

            _env.set_env(self, TURNOS_TEST_SEAMS="1")
            hsq._force_reschedule_fail_once = False
            with self.assertRaises(RuntimeError):
                hsq._maybe_force_reschedule_failure()
        finally:
            hsq._force_reschedule_fail_once = old_flag

    def test_rekick_gate_flag_alone_is_inert(self):
        """Same double-gate proof for TURNOS_TEST_HSI_REKICK_GATE_KEY
        (review gate, 2026-08-09 — the seam added for the event-driven
        cold-boot-race redesign, _cold_boot_race above): the flag alone
        (no TURNOS_TEST_SEAMS=1) must not gate at all, EVEN WHILE the
        named key is genuinely held by someone else — timed directly,
        same technique as test_rekick_hold_flag_alone_is_inert, since
        _maybe_rekick_release_gate() has no return value to assert on.
        With TURNOS_TEST_SEAMS=1 also set, the gate genuinely BLOCKS
        while held — proven against a short safety timeout (well under
        the function's own 30s default) rather than waiting for that
        default to actually fire, so this unit test stays fast.

        `cur` is now REQUIRED (review round 3 — the function no longer
        opens its own connection, see its own docstring), so this test
        supplies one directly, exactly like rekick() itself would — with
        the SAME row_factory=dict_row every real caller gets from
        `core.db._connect()` (review gate: a plain `psycopg.connect()`
        here returns tuple rows, and `_maybe_rekick_release_gate()`
        indexes its result by column name — the contract every cursor
        passed to it must satisfy, not something to special-case around)."""
        import asyncio
        import psycopg
        from psycopg.rows import dict_row
        from app.modules.turnos import hsi_journal_queue as hsq
        key = "inert_check_key"
        holder = psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)
        holder_cur = holder.cursor()
        holder_cur.execute("SELECT pg_advisory_lock(hashtext(%s))",
                           ("test_rekick_gate:" + key,))
        caller_conn = psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5,
                                      row_factory=dict_row)
        caller_cur = caller_conn.cursor()
        try:
            _env.set_env(self, TURNOS_TEST_SEAMS=None, TURNOS_TEST_HSI_REKICK_GATE_KEY=key)
            started = time.monotonic()
            asyncio.run(hsq._maybe_rekick_release_gate(caller_cur))
            self.assertLess(time.monotonic() - started, 1.0,
                            "the gate flag alone (no TURNOS_TEST_SEAMS=1) "
                            "must be inert even while the key is held")

            _env.set_env(self, TURNOS_TEST_SEAMS="1")
            started = time.monotonic()
            asyncio.run(hsq._maybe_rekick_release_gate(caller_cur, timeout_s=2.0, poll_s=0.1))
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 1.9,
                                    "both vars together must actually block "
                                    "for the whole safety timeout, since "
                                    "the holder above never released it")
        finally:
            caller_conn.close()
            holder_cur.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                               ("test_rekick_gate:" + key,))
            holder.close()

    def test_rekick_closes_connection_when_unlock_fails(self):
        """Item 4's own regression proof (review round: the stranded-
        advisory-lock hardening in rekick()'s `finally` needs a
        deterministic test, not just an argument that it's safe).
        TURNOS_TEST_HSI_BREAK_UNLOCK=1 substitutes a deliberately-invalid
        SQL fragment for the real `pg_advisory_unlock(...)` call — a
        GENUINE Postgres error (UndefinedFunction), not a monkeypatch or
        a simulated end state — reached only AFTER the lock was
        genuinely acquired (`held=True`), the exact path the hardening
        exists for.

        Proof the fallback (closing the raw connection) actually ran:
        psycopg_pool's own public `get_stats()['returns_bad']` counter
        increments exactly when `_return_connection()` sees a RETURNED
        (not checked-out) connection's UNKNOWN transaction status and
        discards it rather than reusing it — `connections_lost` is a
        DIFFERENT counter, for a connection found already-broken at
        CHECKOUT time (`from_getconn=True` in that same function); this
        test's own connection is returned via `putconn`, never
        checked-out broken, so `returns_bad` is the one that moves (both
        confirmed by reading `_return_connection()`'s own source, not
        assumed — an earlier pass named the wrong counter). In-process,
        no subprocess worker needed: rekick() is called directly against
        a freshly-registered app on this test's own connection pool.

        `app.open_async()`, not `app.open()` (review gate: the sync open
        only readies procrastinate's SYNC connector; rekick() awaits
        `defer_async()` on the ASYNC one -- see
        `core.queue_infra.build_app()`'s own docstring, "only configures
        an ASYNC connector" -- so a sync-only open left rekick() hitting
        AppNotOpen and returning False before ever reaching the
        unlock-failure path this test exists to prove)."""
        import asyncio
        from app.modules.turnos import hsi_journal_queue as hsq
        _env.set_env(self, TURNOS_TEST_SEAMS="1", TURNOS_TEST_HSI_BREAK_UNLOCK="1")
        # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — rekick()
        # no-ops via _emr_selected() with no connector configured.
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")

        app = queue_infra.build_app()
        hsq.register(app, 5)

        async def _run():
            async with app.open_async():
                pool = db._get_pool(_TEST_URL)
                antes = pool.get_stats().get("returns_bad", 0)
                resultado = await hsq.rekick(app)
                despues = pool.get_stats().get("returns_bad", 0)
                return resultado, antes, despues

        resultado, antes, despues = asyncio.run(_run())
        self.assertTrue(resultado, "rekick() must still succeed "
                        "in deferring the job — only the "
                        "CONNECTION handling is under test here, "
                        "not the defer outcome")
        self.assertEqual(despues, antes + 1,
                         "the broken-connection fallback "
                         "(conn.close()) must have made the pool "
                         "discard exactly one connection on "
                         "return (returns_bad), not silently reuse "
                         "a lock-holding one")

    def test_break_unlock_flag_alone_is_inert(self):
        """Same double-gate proof for TURNOS_TEST_HSI_BREAK_UNLOCK: the
        flag alone (no TURNOS_TEST_SEAMS=1) must not break the unlock at
        all — a real deployment that somehow inherited this one stray
        env line must never have its own lock-release silently start
        failing. Timed via hsq._test_break_unlock() directly (no event
        loop needed, it's a plain sync function)."""
        from app.modules.turnos import hsi_journal_queue as hsq
        _env.set_env(self, TURNOS_TEST_SEAMS=None, TURNOS_TEST_HSI_BREAK_UNLOCK="1")
        self.assertFalse(hsq._test_break_unlock(),
                         "the flag alone (no TURNOS_TEST_SEAMS=1) "
                         "must be inert")
        _env.set_env(self, TURNOS_TEST_SEAMS="1")
        self.assertTrue(hsq._test_break_unlock(),
                        "both vars together must actually arm the seam")

    def test_hold_after_remote_accept_flag_alone_is_inert(self):
        """Same double-gate proof for TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S
        (this seam had no dedicated inertness test alongside the others
        in this class — review gate, 2026-08-09): the flag alone (no
        TURNOS_TEST_SEAMS=1) must not hold at all. This is
        hsi_sync._test_hold_after_remote_accept — the seam
        test_genuine_sigkill_inside_remote_accepted_window uses to pause
        _replay_one genuinely inside the remote-accepted/local-
        uncommitted crash window (see that function's own docstring) —
        timed directly since it's a plain sync function with no return
        value to assert on."""
        from app.modules.turnos import hsi_journal as hsi_sync
        _env.set_env(self, TURNOS_TEST_SEAMS=None, TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S="1")
        started = time.monotonic()
        hsi_sync._test_hold_after_remote_accept()
        self.assertLess(time.monotonic() - started, 0.5,
                        "the hold flag alone (no TURNOS_TEST_SEAMS=1) "
                        "must be inert")

        _env.set_env(self, TURNOS_TEST_SEAMS="1")
        started = time.monotonic()
        hsi_sync._test_hold_after_remote_accept()
        self.assertGreaterEqual(time.monotonic() - started, 0.9,
                                "both vars together must actually hold")

    def test_rolling_restart_never_produces_back_to_back_tick(self):
        """Review gate (2026-08-09): worker B booting while worker A's
        hsi_sync row is genuinely 'doing' (A alive, NOT crashed — a
        rolling restart, supervisor bounce, or a second worker starting
        for unrelated reasons) must never queue a second immediate tick.
        The execution lock alone would only SERIALIZE that second tick,
        not prevent it — it would fire the instant A's in-flight tick
        finishes, reproducing the exact back-to-back HSI read this whole
        self-rescheduling design exists to close. hsi_sync_queue.rekick()
        (called by B's own startup defer) must see A's 'doing' row via
        chain_alive() and do nothing.

        TURNOS_SKIP_HSI=1 keeps tick() itself fast/deterministic — this
        proves the SCHEDULING invariant (never two 'todo'/'doing' rows,
        exactly one 'started' event across the whole window), not HSI
        behavior.

        TURNOS_WORKER_STALLED_S overridden to a SAFE value here (review
        gate, 2026-08-09 — reviewer finding): this class's own _ENV_FOCO
        sets it to "2" for the GENUINE-crash tests elsewhere in this
        class (e.g. test_hsi_sync_survives_mid_task_sigkill_exactly_once),
        where the low threshold is CORRECT — the worker really was
        SIGKILLed, its heartbeat really did stop, and 2 s is plenty of
        margin once that's true. Neither worker in THIS test ever
        crashes: both stay genuinely alive and heartbeating the whole
        time. Reusing that same 2 s threshold here — far below the real
        production floor of 30 s (worker._STALLED_FLOOR_S, ~3 heartbeat
        intervals, enforced at startup for exactly this reason) and
        smaller than a single heartbeat interval (~10 s) — would let
        worker B's OWN startup stalled-job recovery
        (worker._recover_stalled_jobs, which keys purely on heartbeat
        age, no cross-check against whether the worker is actually
        alive) misjudge A's still-'doing', still-alive row as stalled
        and wrongly requeue it out from under A: exactly the false
        positive this test must NOT depend on getting lucky about.
        Setting a threshold safely above any elapsed time this test's
        own window can produce removes that race by construction,
        deterministically, rather than by hoping B's recovery pass
        happens to run before or after the danger window."""
        # A's tick must still be 'doing' after B's WHOLE boot. A boot runs
        # at the machine's speed (13-22 s at load ~60 on 18 CPUs, 2026-11-06,
        # against a fixed 8 s hold that let A finish first), so the hold is
        # the quiet-machine 12 s times the current load per CPU.
        hold_s = max(12, round(_wait.scaled(12)))
        env_hold = dict(self._ENV_FOCO)
        env_hold["TURNOS_TEST_SWEEP_HOLD_S"] = str(hold_s)
        env_hold["TURNOS_WORKER_STALLED_S"] = str(max(60, 5 * hold_s))
        wa = self._spawn(env_hold)
        wa.wait_line("corriendo")

        def _rows():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT id, status FROM procrastinate_jobs "
                            "WHERE task_name=%s ORDER BY id",
                            (hsi_sync_queue._TASK_NAME,))
                return cur.fetchall()

        _until(lambda: (lambda filas: filas and filas[0][1] == "doing")(_rows()),
                    "A's startup tick reached 'doing'", timeout=20)

        # B boots WHILE A is still genuinely 'doing' — not crashed, not
        # killed. Its own startup rekick() must see the live chain and
        # do nothing: still exactly one row, still 'doing'. Same safe
        # TURNOS_WORKER_STALLED_S override as env_hold above — B's OWN
        # startup stalled-job recovery pass must not misjudge A as dead.
        env_b = dict(self._ENV_FOCO)
        env_b["TURNOS_WORKER_STALLED_S"] = env_hold["TURNOS_WORKER_STALLED_S"]
        wb = self._spawn(env_b)
        wb.wait_line("corriendo")
        # let B's own startup defer attempt actually run: the window in which a second row
        # (or the first leaving 'doing') would show
        _wait.stays(lambda: [f[1] for f in _rows()] == ["doing"],
                    "B's boot must not queue a second row, and A stays 'doing'", for_s=1.5)

        # Let A's held tick finish naturally (no kill). Exactly one
        # 'started' event for the whole window, and the self-reschedule
        # (NOT a boot-time kick) produces the next row, correctly spaced.
        filas = _until(lambda: (lambda f: f if f and f[0][1] == "succeeded" else None)(_rows()),
                       "A's held tick succeeded", timeout=hold_s + 20)
        self.assertEqual(_started_events(filas[0][0]), 1,
                         "exactly one execution of the first tick — B's "
                         "boot never triggered a second one")

        segunda = _until(lambda: (lambda f: f if len(f) == 2 else None)(_rows()),
                              "the chain's own self-reschedule produced the next row", timeout=10)
        diff = self._cadence_diff_s(segunda[0][0], segunda[1][0])
        self.assertLessEqual(abs(diff), 2.0,
                             "the next tick must be scheduled at EXACTLY "
                             "job1's succeeded-event timestamp + the full "
                             f"configured interval, not shifted by B's "
                             f"boot — off by {diff!r}s")
        wa.stop()
        wb.stop()

    def _cadence_diff_s(self, anchor_job_id, continuation_job_id):
        """Exact, non-flaky cadence check (review fix, 2026-08-09):
        `scheduled_at - now()` at ASSERTION time depends on how long the
        test itself took to reach the assertion — a loose bound like
        ">250s" still permits a real ~50s cadence error to pass silently.
        This instead compares the continuation job's `scheduled_at`
        DIRECTLY against the anchor job's OWN durable 'succeeded' event
        timestamp plus the configured interval, in ONE SQL snapshot (no
        wall-clock-at-assertion-time involved at all). Returns the
        difference in seconds — 0 is exact; a real defer/rekick spends at
        most low-single-digit seconds computing and inserting, so the
        caller's tolerance can be tight."""
        from app.modules.turnos import hsi_journal as hsi_sync
        interval_s = hsi_sync._interval_min() * 60
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "SELECT extract(epoch FROM ("
                "  (SELECT scheduled_at FROM procrastinate_jobs WHERE id=%s)"
                "  - ("
                "    (SELECT max(at) FROM procrastinate_events "
                "     WHERE job_id=%s AND type='succeeded')"
                "    + (%s * interval '1 second')"
                "  )"
                "))",
                (continuation_job_id, anchor_job_id, interval_s))
            return cur.fetchone()[0]

    def test_watchdog_heals_broken_chain_while_worker_stays_up(self):
        """Review gate (2026-08-09): a swallowed self-reschedule failure
        must not stall the chain until the next worker BOOT — a
        long-lived worker that never restarts would silently stop
        ticking hsi_sync forever.

        Review-fix round 2: the first cut of this test SIMULATED the
        failure's END STATE (deleted the continuation row after the fact)
        rather than forcing the actual failure PATH — that proves the
        watchdog notices an already-broken chain, not that this module's
        own swallowed-exception handling is what survives a genuine
        defer_async() failure while the completing tick's own result is
        preserved. TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE=1 (double-
        gated behind TURNOS_TEST_SEAMS=1, same discipline as every other
        seam here) makes hsi_sync_queue._maybe_force_reschedule_failure()
        raise ONCE, from INSIDE the self-reschedule's own try/except —
        the exact call site a real failure would hit.

        Manually defers ONE watchdog invocation (bypassing its real
        5-minute cron — a test-timing concern only, the same reasoning
        test_two_jobs_of_one_task_serialize_across_two_workers already
        uses to test a periodic task without waiting for a real boundary)
        onto the SAME worker process that is still running, proving the
        heal happens without any restart — and that the healed schedule
        is interval-respecting (rekick(), not "now")."""
        env = dict(self._ENV_FOCO)
        env["TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE"] = "1"
        wa = self._spawn(env)
        wa.wait_line("corriendo")

        def _rows():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT id, status FROM procrastinate_jobs "
                            "WHERE task_name=%s ORDER BY id",
                            (hsi_sync_queue._TASK_NAME,))
                return cur.fetchall()

        # The forced failure fires INSIDE the first tick's own execution,
        # so the row still ends 'succeeded' (this tick's real result is
        # not lost over the scheduling hiccup).
        _until(lambda: (lambda f: f and f[0][1] == "succeeded")(_rows()),
                    "the first tick still succeeded despite the forced reschedule failure",
                    timeout=20)
        # Proof the failure was GENUINE (not inferred from row absence,
        # which races against procrastinate's own catch-up defer for
        # periodic tasks — the watchdog's `@app.periodic` registration
        # immediately queues a catch-up run for the current cron tick,
        # so it can heal the chain within a second or two on its own,
        # before this test ever reaches its manual trigger below): the
        # except-clause's own log line is the evidence, not the row count.
        wa.wait_line("no se pudo reprogramar", timeout=5)

        # Confirm the worker process is still alive (the exception did not
        # crash it) before triggering the watchdog.
        self.assertIsNone(wa.proc.poll(), "worker A must still be running")

        # Manually defer ONE watchdog run onto worker A's OWN queues —
        # not a restart, the SAME live process picks it up.
        from app import worker
        app = queue_infra.build_app()
        watchdog_task = worker._register_hsi_sync_watchdog(app)
        app.open()
        try:
            watchdog_task.defer(timestamp=0)
        finally:
            app.close()

        healed = _until(lambda: (lambda f: f if len(f) == 2 else None)(_rows()),
                             "the watchdog re-kicked the chain while worker A stayed up",
                             timeout=20)
        self.assertIsNone(wa.proc.poll(), "worker A must STILL be running "
                          "— the whole point is no restart happened")

        # The healed schedule must be EXACTLY interval-respecting
        # (rekick()'s own invariant: last_success + interval, algebraically
        # independent of how long the watchdog took to run — see
        # rekick()'s docstring), not "now" and not loosely "close to" the
        # full interval. Same exact-diff check as the rolling-restart
        # proof, no wall-clock-at-assertion-time involved.
        diff = self._cadence_diff_s(healed[0][0], healed[1][0])
        self.assertLessEqual(abs(diff), 2.0,
                             "the watchdog's rekick must schedule at EXACTLY "
                             "job1's succeeded-event timestamp + the full "
                             f"configured interval — off by {diff!r}s")
        wa.stop()

    def test_rekick_survives_pool_size_one(self):
        """Connection-pool-exhaustion proof (review gate, 2026-08-09):
        TURNOS_DB_POOL=1 is a REAL, supported configuration
        (core.db._pool_size(), default 15) — not hypothetical. rekick()'s
        own critical section used to nest TWO simultaneous pooled
        checkouts inside the SAME call stack: the outer connection its
        own `with db._connect()...` holds for the whole function, plus a
        SECOND one chain_alive() (and, sequentially, its now-replaced
        elapsed-time sibling, see _next_schedule_at()) opened for
        themselves. With a size-1 pool, that second checkout can never
        succeed while the first is still held — db._connect()'s own
        bounded `getconn(timeout=3)` then raises DbUnavailable, which
        used to escape rekick() uncaught straight to _defer_startup_jobs'
        outer except: the FIRST hsi_sync tick would silently never get
        deferred at all under this config, forever, on every single
        boot — no concurrency needed to reproduce this, it happened on
        the very first (and only) rekick() call. Fixed by chain_alive()
        and _next_schedule_at() taking rekick()'s own already-open
        cursor instead of opening their own — see their docstrings.

        Real subprocess, real Postgres, HARD bounded deadlines (not
        "hope it doesn't hang forever" — a regression here fails loudly
        within seconds via wait_line's own timeout, never hangs the
        suite)."""
        env = dict(self._ENV_FOCO)
        env["TURNOS_DB_POOL"] = "1"
        wa = self._spawn(env)
        wa.wait_line("corriendo")
        wa.wait_line("rekick: chain_alive=False, procediendo", timeout=10)

        # The job this rekick() call deferred must actually have LANDED —
        # proof the whole critical section completed under the size-1
        # pool, not just that the log line printed before a later crash.
        def _deferred():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT status FROM procrastinate_jobs "
                            "WHERE task_name=%s", (hsi_sync_queue._TASK_NAME,))
                return cur.fetchall()
        _until(_deferred, "hsi_sync's first tick was deferred under TURNOS_DB_POOL=1 "
                    "(else the nested-checkout bug is back)", timeout=10)
        wa.stop()

    def test_sweep_hold_does_not_starve_worker_heartbeat(self):
        """Event-loop-blocking proof, PART 1 of 2 (review gate,
        2026-08-09, reviewer-confirmed MANDATORY): before the fix,
        _sweep's async body called queue_infra.test_hold() (a real
        time.sleep) and hsi_sync.tick() directly, un-offloaded — blocking
        the worker's single asyncio event loop for the ENTIRE hold,
        starving procrastinate's own ~10s heartbeat sender
        (worker._update_heartbeat, an asyncio.create_task on that SAME
        loop) right along with it. A tick genuinely slower than the
        stalled-job threshold could then make a DIFFERENT worker's
        stalled-job recovery (keyed purely on heartbeat age) requeue and
        re-run the SAME still-genuinely-running job.

        Holds a real tick for 18s (TURNOS_TEST_SWEEP_HOLD_S — comfortably
        past one 10s heartbeat interval, short enough to keep the suite
        fast) and polls procrastinate_workers.last_heartbeat WHILE the
        job stays 'doing': before the fix this value freezes for the
        whole hold (the event loop is asleep); after the fix
        (run_in_executor offload) it keeps advancing, because the hold
        now runs on a SEPARATE thread, leaving the loop free to run
        procrastinate's own heartbeat coroutine.

        Pinned to THIS worker's own row `id` (captured once, then read
        back by id every poll) rather than a bare "whichever row has the
        newest heartbeat" query — procrastinate_workers isn't truncated
        between tests, so a stale row from an earlier crashed worker
        could otherwise coincide; pinning by id makes the proof
        unambiguously about worker A specifically."""
        # The poll below grows with the machine load, so the hold it must
        # stay inside grows the same way (18 s on a quiet machine).
        hold_s = max(18, round(_wait.scaled(18)))
        env = dict(self._ENV_FOCO)
        env["TURNOS_TEST_SWEEP_HOLD_S"] = str(hold_s)
        wa = self._spawn(env)
        wa.wait_line("corriendo")

        def _job_status():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT status FROM procrastinate_jobs "
                            "WHERE task_name=%s", (hsi_sync_queue._TASK_NAME,))
                row = cur.fetchone()
                return row[0] if row else None

        def _heartbeat_row():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT id, last_heartbeat FROM procrastinate_workers "
                            "ORDER BY last_heartbeat DESC LIMIT 1")
                return cur.fetchone()

        def _heartbeat_of(worker_id):
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT last_heartbeat FROM procrastinate_workers "
                            "WHERE id=%s", (worker_id,))
                row = cur.fetchone()
                return row[0] if row else None

        _until(lambda: _job_status() == "doing",
                    "hsi_sync's startup tick reached 'doing'", timeout=10)

        primero_row = _heartbeat_row()
        self.assertIsNotNone(primero_row, "worker never registered a heartbeat")
        worker_id, primero = primero_row

        # Poll for up to 14s (comfortably inside the 18s hold) for THIS
        # worker's own row to advance past its FIRST observed value.
        def _advanced():
            if _job_status() != "doing":
                return True  # the hold ended early — the assertion below catches it
            actual = _heartbeat_of(worker_id)
            return actual is not None and actual > primero
        _until(_advanced, "the worker's heartbeat advanced while hsi_sync's tick was "
                    f"genuinely 'doing' for {hold_s}s (else the event loop is still being blocked "
                    "by the sweep body)", timeout=14, interval=1.0)

        # The job must STILL be 'doing' here — otherwise the heartbeat's
        # advance proves nothing about whether the hold blocked the loop
        # (the tick could simply have finished and normal operation
        # resumed on its own).
        self.assertEqual(_job_status(), "doing",
                         "the tick finished before the heartbeat check "
                         "completed — this proof needs it still 'doing'")
        # SIGKILL, like the cold-boot proofs: a graceful stop would drain the
        # rest of a load-scaled hold, which can outlast the shutdown budget.
        wa.cleanup()

    def test_rekick_lock_wait_does_not_starve_worker_heartbeat(self):
        """Event-loop-blocking proof, PART 2 of 2, DISTINCT from the
        sweep-body proof above (review gate, 2026-08-09 — REDESIGNED
        after a first attempt tested the wrong window and failed for
        that reason, not because the fix was wrong):

        First cut contended the lock during worker A's STARTUP rekick()
        call (`_defer_startup_jobs`, invoked inside `_boot_and_run()`
        BEFORE `app.run_worker_async()` is ever called). That window has
        NO live heartbeat to prove anything about: procrastinate's own
        `Worker._update_heartbeat` side task (and `register_worker()`,
        which creates the `procrastinate_workers` row in the first
        place) are both created inside `Worker.run()` — confirmed
        directly against procrastinate/worker.py, not assumed — which
        `app.run_worker_async()` only reaches AFTER `_defer_startup_jobs`
        has already returned. A test measuring heartbeat freshness during
        that window is measuring a loop that isn't running yet; it
        cannot fail honestly even with the OLD blocking bug still in
        place. Fixed by moving the contention to the STEADY-STATE
        WATCHDOG path (`worker._register_hsi_sync_watchdog`) instead —
        that one only ever runs once the worker is genuinely up and
        heartbeating, the same real-world caller
        test_watchdog_heals_broken_chain_while_worker_stays_up already
        exercises for a different property.

        Choreography: worker A boots normally (no hold) — its cold-start
        tick succeeds fast, but its OWN self-reschedule is forced to
        fail ONCE (TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE=1, the
        SAME seam that test uses) so the chain is genuinely broken
        (chain_alive()==False) by the time A is fully up and
        heartbeating. The test confirms A's heartbeat row exists (real
        proof Worker.run() has started, not inferred from "corriendo"
        which logs BEFORE that phase begins), takes the real
        coordination lock on its OWN connection, and manually defers ONE
        watchdog run — A's own live watchdog task picks it up, calls
        rekick(), and must contend for the lock the test holds:
        confirmed via A's OWN "rekick: lock contendido, esperando" line,
        a real observation. WHILE A is known to be blocked (post-boot,
        heartbeat coroutine genuinely alive), the test polls A's
        heartbeat and confirms it advances.

        Pinned to THIS worker's own row `id` (captured once, then read
        back by id every poll), same reasoning as
        test_sweep_hold_does_not_starve_worker_heartbeat above —
        unambiguously about worker A, not "whichever row is newest"."""
        env = dict(self._ENV_FOCO)
        env["TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE"] = "1"
        wa = self._spawn(env)
        wa.wait_line("corriendo")

        def _job_status():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT status FROM procrastinate_jobs "
                            "WHERE task_name=%s", (hsi_sync_queue._TASK_NAME,))
                row = cur.fetchone()
                return row[0] if row else None

        def _heartbeat_row():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT id, last_heartbeat FROM procrastinate_workers "
                            "ORDER BY last_heartbeat DESC LIMIT 1")
                return cur.fetchone()

        def _heartbeat_of(worker_id):
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT last_heartbeat FROM procrastinate_workers "
                            "WHERE id=%s", (worker_id,))
                row = cur.fetchone()
                return row[0] if row else None

        # A's OWN startup tick succeeds fast (no hold); the forced
        # failure means no continuation row appears — a real observation
        # (the row's status), not a guess about timing.
        _until(lambda: _job_status() == "succeeded",
                    "hsi_sync's startup tick reached 'succeeded'", timeout=15)
        wa.wait_line("no se pudo reprogramar", timeout=5)

        # Real proof A is genuinely past Worker.run()'s own startup and
        # heartbeating — not inferred from "corriendo". Also identifies
        # A's OWN worker row by id for the rest of this test.
        primero_row = _until(_heartbeat_row, "worker A registered a heartbeat", timeout=15)
        worker_id, primero = primero_row

        lock_key = hsi_sync_queue._TASK_NAME + ":rekick"
        lock_conn = self._pg()
        lock_cur = lock_conn.cursor()
        lock_cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (lock_key,))
        try:
            from app import worker
            app = queue_infra.build_app()
            watchdog_task = worker._register_hsi_sync_watchdog(app)
            app.open()
            try:
                watchdog_task.defer(timestamp=0)
            finally:
                app.close()

            wa.wait_line("rekick: lock contendido, esperando", timeout=15)

            _until(lambda: (_heartbeat_of(worker_id) or primero) > primero,
                        "the worker's heartbeat advanced while its watchdog-triggered "
                        "rekick() was genuinely blocked contending for the advisory lock "
                        "(else the event loop is still being blocked by lock acquisition)",
                        timeout=13, interval=1.0)
        finally:
            lock_cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,))
            lock_conn.close()
        wa.stop()


class TestWorkerSignalHandlerRestoration(unittest.TestCase):
    """Signal restoration seams are pure process-state tests, never DB tests."""

    def test_direct_boot_restores_exact_prior_signal_handlers(self):
        import asyncio
        from contextlib import asynccontextmanager
        import signal as _signal
        from app import worker as _worker

        async def _fake_recover(app, colas):
            return 0

        async def _fake_defer(app):
            return 0

        async def _fake_drain():
            pass

        class _FakeWorker:
            def __init__(self, app, **kwargs):
                # procrastinate's Worker.__init__ stores the constructing
                # app (worker.app.job_manager reaches the heartbeat guard).
                self.app = app

            def stop(self):
                pass

            async def run(self):
                return None

        class _FakeApp:
            def __init__(self):
                # Real App.__init__ builds one JobManager per app.
                self.job_manager = _FakeJobManager()

            @asynccontextmanager
            async def open_async(self):
                yield None

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker(self, **kwargs)

        def _prior_sigterm(signum, frame):
            return None

        def _prior_sigint(signum, frame):
            return None

        original_sigterm = _signal.getsignal(_signal.SIGTERM)
        original_sigint = _signal.getsignal(_signal.SIGINT)
        old_fase = _worker._STATE.get("fase")
        _signal.signal(_signal.SIGTERM, _prior_sigterm)
        _signal.signal(_signal.SIGINT, _prior_sigint)
        try:
            with patch.object(_worker, "_drain_hsi_sync_startup_tasks", _fake_drain), \
                 patch.object(_worker, "_recover_stalled_jobs", _fake_recover), \
                 patch.object(_worker, "_defer_startup_jobs", _fake_defer):
                asyncio.run(_worker._boot_and_run(
                    _FakeApp(), [], "test-worker", 3.0))
            self.assertIs(_signal.getsignal(_signal.SIGTERM), _prior_sigterm)
            self.assertIs(_signal.getsignal(_signal.SIGINT), _prior_sigint)
        finally:
            _worker._STATE["fase"] = old_fase
            _signal.signal(_signal.SIGTERM, original_sigterm)
            _signal.signal(_signal.SIGINT, original_sigint)

    def test_main_restores_handlers_when_deferred_core_import_fails(self):
        import builtins
        import signal as _signal
        from app import worker as _worker

        def _prior_sigterm(signum, frame):
            return None

        def _prior_sigint(signum, frame):
            return None

        real_import = builtins.__import__

        def _fail_deferred_core_import(name, globals=None, locals=None,
                                       fromlist=(), level=0):
            if name == "app.core" and level == 0:
                raise ImportError("synthetic deferred core import failure")
            return real_import(name, globals, locals, fromlist, level)

        original_sigterm = _signal.getsignal(_signal.SIGTERM)
        original_sigint = _signal.getsignal(_signal.SIGINT)
        _signal.signal(_signal.SIGTERM, _prior_sigterm)
        _signal.signal(_signal.SIGINT, _prior_sigint)
        try:
            with patch("builtins.__import__", side_effect=_fail_deferred_core_import):
                with self.assertRaisesRegex(
                        ImportError, "synthetic deferred core import failure"):
                    _worker.main()
            self.assertIs(_signal.getsignal(_signal.SIGTERM), _prior_sigterm)
            self.assertIs(_signal.getsignal(_signal.SIGINT), _prior_sigint)
        finally:
            _signal.signal(_signal.SIGTERM, original_sigterm)
            _signal.signal(_signal.SIGINT, original_sigint)


class TestAsyncSignalHandoff(unittest.TestCase):
    """ONE SIGTERM landing while `_boot_and_run` hands SIGTERM/SIGINT from
    main()'s plain handler to asyncio must count ONCE — a graceful stop
    (exit 0), never the second-signal force exit (exit 3).

    The window is inside asyncio itself: `loop.add_signal_handler()` calls
    `signal.set_wakeup_fd()` and only then `signal.signal(sig, noop)`. A
    signal tripped between the two reaches BOTH channels — main()'s plain
    handler (still installed) and the wakeup byte that becomes the asyncio
    callback — and the shared `_SignalState` counter saw two signals
    (~1 in 40 cycles of
    `test_rapid_spawn_sigterm_never_orphans_worker_row`). Here the signal
    is injected deterministically from a wrapped `set_wakeup_fd`, in a real
    child running the real `worker.main()` with the same no-DB fakes as
    `test_two_async_phase_signals_force_exit`. Two deliveries, because the
    kernel may hand a process-directed SIGTERM to any thread that does not
    block it (the real worker has its health and pool threads by then):
    the main thread itself, and another thread while main still sits in
    the window."""

    def _run_child(self, delivery):
        import textwrap
        script = textwrap.dedent(f"""
            import asyncio, os, signal, sys, threading, time
            sys.path.insert(0, {ROOT!r})
            os.environ["TURNOS_QUEUE_WORKER"] = "1"
            from app import worker
            from app.core import db, queue_infra

            delivery = {delivery!r}
            other_thread_ready = threading.Event()
            never = threading.Event()

            def _other_thread():
                # Stands in for worker-health / the DB pool threads: alive,
                # idle, and NOT blocking SIGTERM.
                other_thread_ready.set()
                never.wait()

            other = threading.Thread(target=_other_thread, daemon=True)
            other.start()
            other_thread_ready.wait(5)

            real_set_wakeup_fd = signal.set_wakeup_fd
            injected = []

            def _set_wakeup_fd(fd, *args, **kwargs):
                previous = real_set_wakeup_fd(fd, *args, **kwargs)
                if fd != -1 and not injected:
                    # asyncio's add_signal_handler() has just armed the
                    # wakeup fd and has NOT yet replaced the plain handler.
                    injected.append(delivery)
                    print("INJECTED " + delivery, file=sys.stderr, flush=True)
                    if delivery == "main-thread":
                        os.kill(os.getpid(), signal.SIGTERM)
                    else:
                        signal.pthread_kill(other.ident, signal.SIGTERM)
                        # Let the other thread take it while this thread
                        # is still inside the window.
                        time.sleep(0.05)
                return previous

            signal.set_wakeup_fd = _set_wakeup_fd

            class _FakeWorker:
                def stop(self): pass
                async def run(self): return None

            class _FakeApp:
                from contextlib import asynccontextmanager
                @asynccontextmanager
                async def open_async(self):
                    # A real pool open awaits I/O, so the loop reads the
                    # wakeup fd before the bootstrap's stop check.
                    await asyncio.sleep(0.2)
                    yield None
                def perform_import_paths(self): pass
                def _worker(self, **kwargs): return _FakeWorker()

            db._db_url = lambda: "postgresql://u:p@127.0.0.1:1/x"
            queue_infra.ensure_schema = lambda: None
            queue_infra.build_app = lambda: _FakeApp()
            worker._register_jobs = lambda app: ["maintenance"]
            worker._await_settings_db = lambda *a, **k: None
            worker._start_health_server = lambda: None
            code = worker.main()
            print("MAIN RETURNED", code, file=sys.stderr, flush=True)
            sys.exit(code)
        """)
        return subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            capture_output=True, text=True, timeout=_wait.scaled(20))

    def _assert_one_graceful_stop(self, proc, delivery):
        self.assertIn("INJECTED " + delivery, proc.stderr,
                      "the probe never reached asyncio's set_wakeup_fd — "
                      f"nothing was tested; stderr:\n{proc.stderr}")
        self.assertNotIn("segunda señal recibida", proc.stderr,
                         "ONE signal in the handoff window was counted "
                         f"twice; stderr:\n{proc.stderr}")
        self.assertEqual(proc.returncode, 0,
                         "ONE SIGTERM during the asyncio handoff must be a "
                         "graceful stop (exit 0), not EXIT_FORCE_KILL (3); "
                         f"stderr:\n{proc.stderr}")
        self.assertIn("MAIN RETURNED 0", proc.stderr)
        self.assertIn("apagando con gracia", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_sigterm_to_main_thread_during_handoff_counts_once(self):
        proc = self._run_child("main-thread")
        self._assert_one_graceful_stop(proc, "main-thread")

    def test_sigterm_to_other_thread_during_handoff_counts_once(self):
        proc = self._run_child("other-thread")
        self._assert_one_graceful_stop(proc, "other-thread")

    def test_handoff_echo_absorbs_only_its_own_twin(self):
        """The absorb is bounded: only the first asyncio callback for a
        signal the plain handler counted INSIDE the handoff, and only within
        `_HANDOFF_ECHO_TTL_S` — a genuine later second signal still counts
        (and so still force-exits, as
        `test_second_signal_forces_exit_not_default_disposition` pins)."""
        from app import worker as _worker
        state = _worker._SignalState()
        state.note_plain_signal(signal.SIGTERM)  # outside the handoff
        self.assertFalse(state.take_handoff_echo(signal.SIGTERM))

        state.handoff = True
        state.note_plain_signal(signal.SIGTERM)
        state.handoff = False
        self.assertFalse(state.take_handoff_echo(signal.SIGINT),
                         "an echo belongs to its own signal number")
        self.assertTrue(state.take_handoff_echo(signal.SIGTERM), "the twin")
        self.assertFalse(state.take_handoff_echo(signal.SIGTERM),
                         "a second callback is a genuine second signal")

        state.handoff_echoes[signal.SIGTERM] = (
            time.monotonic() - _worker._HANDOFF_ECHO_TTL_S - 1)
        self.assertFalse(state.take_handoff_echo(signal.SIGTERM),
                         "a stale echo never absorbs a later signal")


class TestHsiSyncStartupDecoupling(_DbProcTest):
    """hsi_sync's own boot-time rekick() must not gate run_worker_async()
    (bug found running the FULL local CI ladder under real system load,
    not the 4 HSI-focused modules alone — test_startup_vs_startup_two_
    workers_never_overlap, an EARLIER-batch chart_summary test with
    nothing to do with HSI on its face, intermittently left a job in
    'doing' past its convergence deadline under full-suite load).

    Root cause: _defer_startup_jobs() used to `await`
    hsi_sync_queue.rekick(app) INLINE, in the same loop as every other
    startup kick — but unlike every other kick (a fast fire-and-forget
    defer_async(), milliseconds), rekick() can genuinely block for up to
    its own 20s lock-contention timeout (_acquire_rekick_lock) when two
    workers boot together and race hsi_sync's coordination lock. Because
    _defer_startup_jobs() runs BEFORE run_worker_async() (worker.py's
    _boot_and_run()), that inline await head-of-line-blocked EVERY OTHER
    queue's job processing behind hsi_sync's own contention.

    Fix: worker._dispatch_hsi_sync_rekick() fires the kick as a
    supervised background asyncio task instead — three explicit
    guarantees, each proven by its own test below (a bare
    asyncio.create_task() would satisfy none of them on its own):
    1. exceptions stay visible (the SAME _log(...) line as before, not
       swallowed by asyncio's default unhandled-task-exception handler);
    2. the task cannot be silently garbage-collected (a strong reference
       held in worker._HSI_SYNC_STARTUP_TASKS);
    3. shutdown accounts for it explicitly
       (worker._drain_hsi_sync_startup_tasks(), called in a `finally`
       right after run_worker_async() returns OR raises — cancel
       immediately, then a bounded wait INSIDE the outer supervisor's
       own SIGKILL margin, with real headroom reserved before it (not
       the full margin — racing the SIGKILL at the exact same instant
       could lose the very log line this exists to guarantee), to
       observe cancellation land, never a silent drop)."""

    def test_dispatch_logs_exception_when_rekick_raises(self):
        """Guarantee 1: a rekick() failure inside the background task
        must still reach the SAME log line the old inline `await` path
        produced (f"startup sweep {label}: rekick explotó ({e!r})"), not
        vanish into asyncio's own default unhandled-task-exception
        handler (a "Task exception was never retrieved" warning printed
        only on garbage collection — easy to miss, and not this
        codebase's own _log(...) convention). In-process, no subprocess
        needed: rekick() itself is mocked to raise, isolating the
        dispatch/supervision wiring from real Postgres behavior."""
        import asyncio
        from app import worker as _worker
        from unittest.mock import patch

        spec = next(s for s in _worker._JOBS if s.id == "hsi_sync")
        logged = []

        async def _go():
            app = object()  # never touched — rekick() itself is fully mocked
            task = _worker._dispatch_hsi_sync_rekick(app, spec)
            await asyncio.wait_for(task, timeout=5)

        with patch.object(_worker, "_log", side_effect=lambda m: logged.append(m)):
            with patch("app.modules.turnos.hsi_journal_queue.rekick",
                       side_effect=RuntimeError("boom-from-test")):
                asyncio.run(_go())

        self.assertTrue(
            any("rekick explotó" in m and spec.label in m and "RuntimeError" in m
                and "boom-from-test" not in m for m in logged),
            f"expected the rekick-failure log line (type, never the message), got: {logged}")

    def test_shutdown_cancels_and_logs_pending_rekick_task(self):
        """Guarantee 3 (shutdown accounts for it, never silently drops
        it), and the ACTUAL contract this worker is supervised under:
        asgi_main.py's `_terminar` gives a worker subprocess exactly
        `TURNOS_WORKER_GRACEFUL_TIMEOUT + queue_infra.
        SUPERVISOR_SIGKILL_MARGIN_S` from SIGTERM to SIGKILL — ONE
        budget for the WHOLE shutdown (run_worker_async()'s own drain of
        real in-flight jobs, THEN _drain_hsi_sync_startup_tasks()'s
        cancel-and-observe of hsi_sync's kick), not one budget per step.
        SIGTERM arrives here while hsi_sync's background kick is
        genuinely still held (TURNOS_TEST_HSI_REKICK_HOLD_S=20, far
        longer than either budget) — the worker must still exit cleanly
        WELL WITHIN the combined asgi_main limit, not wait out the whole
        20s hold, and the log must show the task was DELIBERATELY
        cancelled (worker._drain_hsi_sync_startup_tasks' own log line),
        proving the outcome was an explicit policy, not silence. This is
        the subprocess-level (not just helper-level) proof of the real
        supervised contract — the worker never actually runs under
        asgi_main in this test (WorkerProc spawns it directly), so the
        proof is that OUR OWN exit comfortably beats the budget
        asgi_main would allow before it'd SIGKILL.

        SIGTERM readiness (review gate, 2026-08-09): "corriendo" logs
        BEFORE `asyncio.run(_boot_and_run(...))` even starts (confirmed
        against worker.py's own `main()`), and the rekick-hold log can
        fire while `_defer_startup_jobs()` is still running — BEFORE
        `run_worker_async()` has installed its SIGTERM handler
        (confirmed against procrastinate/worker.py: `Worker._run_loop()`
        only enters `signals.on_stop(self.stop)` after
        `_start_side_tasks()`, itself only reached once
        `app.run_worker_async()` is called). Sending SIGTERM based on
        either log alone is race-prone — it could land before the
        handler exists, exercising Python's default SIGTERM disposition
        instead of the graceful-cancellation path this test exists to
        prove.

        Proof used: `procrastinate_workers` is NEVER truncated between
        tests (`_reset_queue_tables()` doesn't touch it — confirmed, not
        assumed), and earlier SIGKILLed workers in this same suite can
        leave stale rows with a recent-looking `last_heartbeat` — a
        SECOND review catch: "ORDER BY last_heartbeat DESC LIMIT 1"
        alone can select a stale row from a DIFFERENT worker, not this
        subprocess's own, which would prove nothing about THIS worker's
        signal-handler state. Fixed by snapshotting every existing
        worker `id` BEFORE `_spawn()`, then polling AFTER for exactly
        ONE new `id` not in that snapshot — that new id can only belong
        to this subprocess, and `register_worker()` running inside
        `Worker.run()` (with no `await` between `_start_side_tasks()`
        and `_run_loop()` entering `signals.on_stop(self.stop)` —
        confirmed against procrastinate's own source) means observing
        it is real proof the handler is already live, not an inference
        from a log line on an unrelated code path."""
        import psycopg

        def _worker_ids():
            with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
                cur.execute("SELECT id FROM procrastinate_workers")
                return {row[0] for row in cur.fetchall()}

        antes = _worker_ids()

        graceful = 2.0
        env = {
            "TURNOS_TEST_SEAMS": "1",
            "TURNOS_TEST_HSI_REKICK_HOLD_S": "20",
            "TURNOS_WORKER_GRACEFUL_TIMEOUT": str(graceful),
            "TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
            "TURNOS_HOUSEKEEPING_OFF": "1", "TURNOS_REFERRAL_SWEEP_OFF": "1",
            "TURNOS_SUMMARY_PREGEN": "0", "TURNOS_TRANSLATE_WARMUP_OFF": "1",
            "TURNOS_SKIP_HSI": "1",
            # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — this
            # class's whole subject is hsi_sync's own boot-time rekick.
            "CONNECTOR_EMR_SYNC": "hsi",
        }
        wa = self._spawn(env)
        wa.wait_line("corriendo")
        wa.wait_line("rekick: chain_alive=False, procediendo", timeout=15)

        # exactly-one new procrastinate_workers row for this subprocess: else its identity cannot
        # be pinned, so SIGTERM below would prove nothing about the graceful-cancellation path
        # (0 new = run_worker_async() may never have started; >1 new = another worker in this
        # suite is registering concurrently, which the serial-DB-lane discipline should already
        # rule out)
        nuevo_id = _until(
            lambda: (lambda nuevos: next(iter(nuevos)) if len(nuevos) == 1 else None)(
                _worker_ids() - antes),
            "exactly one new procrastinate_workers row appeared for this subprocess", timeout=15)

        inicio = time.monotonic()
        exit_code = wa.stop(timeout=_wait.scaled(15))
        elapsed = time.monotonic() - inicio
        # The worker's own deadlines are wall-clock and do not stretch; the
        # process overhead around them (signal delivery, event-loop and
        # interpreter teardown, log I/O) does. At load ~200 on 18 CPUs that
        # overhead alone took 5-7 s. `load_slack` is the extra a loaded
        # machine gets over the quiet allowance below — 0 on a quiet
        # machine, so both checks keep their exact historical bounds there.
        load_slack = _wait.scaled(2) - 2
        self.assertEqual(exit_code, 0, "SIGTERM must still exit gracefully (0)")
        # Two checks, not one (review gate, 2026-08-09): the first prior
        # draft compared `elapsed` only against the OUTER asgi_main
        # ceiling plus an unjustified flat "+3" — that both mis-timed the
        # allowance (the timer starts AFTER spawn, so there is no
        # spawn overhead to excuse) and gave no evidence that real
        # headroom survives before the actual SIGKILL point.
        #
        # 1. Against the worker's OWN, deliberately tighter self-imposed
        #    deadline (_drain_hsi_sync_startup_tasks' reduced margin —
        #    see queue_infra.HSI_SYNC_DRAIN_HEADROOM_S), with a SMALL,
        #    justified allowance for genuine real-subprocess overhead
        #    this direct-spawn test incurs (OS signal delivery, Python
        #    interpreter/event-loop teardown, log I/O — real costs, not
        #    padding for a wrong margin).
        drain_margin = (queue_infra.SUPERVISOR_SIGKILL_MARGIN_S
                        - queue_infra.HSI_SYNC_DRAIN_HEADROOM_S)
        worker_own_deadline = graceful + drain_margin
        self.assertLess(
            elapsed, worker_own_deadline + 2 + load_slack,
            f"shutdown took {elapsed:.1f}s — must fit inside the worker's "
            f"own self-imposed deadline of {worker_own_deadline:g}s "
            f"(graceful={graceful:g}s + reduced drain margin="
            f"{drain_margin:g}s), plus a small allowance for genuine "
            f"subprocess signal-delivery/teardown overhead "
            f"(+{load_slack:.1f}s for the machine load)")
        # 2. STRICTLY against the real outer asgi_main._terminar ceiling —
        #    ZERO slop on a quiet machine. This is the actual proof that
        #    headroom is preserved before the supervisor's SIGKILL, not
        #    merely that this test's own number-fudging lets it pass; only
        #    the load-grown process overhead (`load_slack`) is added.
        asgi_main_ceiling = graceful + queue_infra.SUPERVISOR_SIGKILL_MARGIN_S
        self.assertLess(
            elapsed, asgi_main_ceiling + load_slack,
            f"shutdown took {elapsed:.1f}s — must leave REAL headroom "
            f"before the actual outer asgi_main SIGKILL point of "
            f"{asgi_main_ceiling:g}s (graceful={graceful:g}s + "
            f"SUPERVISOR_SIGKILL_MARGIN_S="
            f"{queue_infra.SUPERVISOR_SIGKILL_MARGIN_S:g}s), not just "
            f"barely beat it — and well short of the full 20s hold")
        wa.wait_line("hsi_sync: kick de arranque en curso cancelado al apagar",
                     timeout=2)

    def test_unrelated_queue_processes_promptly_despite_held_rekick(self):
        """Guarantee 3 / the PRIMARY regression proof: hsi_sync's own
        boot-time coordination must not head-of-line-block every OTHER
        queue's job processing at worker startup. A waitlist sweep is
        manually deferred BEFORE the worker even starts (genuinely
        'todo', waiting the instant the worker's job-consumption loop
        opens) — hsi_sync's rekick is then held for 15s
        (TURNOS_TEST_HSI_REKICK_HOLD_S). DB-observable proof, not a bare
        timing guess: the waitlist job must reach 'doing' or 'succeeded'
        well before the hold ends. This test FAILS against the pre-fix
        code (awaiting rekick() inline delays run_worker_async(), and
        with it EVERY queue's consumption, for the full hold) and
        PASSES after it — confirmed by reverting the fix locally before
        landing this test (see the session report for the RED/GREEN
        proof)."""
        _defer_sweep_without_worker()
        filas = _job_rows()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0][2], "todo")

        env = {
            "TURNOS_TEST_SEAMS": "1",
            "TURNOS_TEST_HSI_REKICK_HOLD_S": "15",
            "TURNOS_HOUSEKEEPING_OFF": "1", "TURNOS_REFERRAL_SWEEP_OFF": "1",
            "TURNOS_SUMMARY_PREGEN": "0", "TURNOS_TRANSLATE_WARMUP_OFF": "1",
            "TURNOS_SKIP_HSI": "1", "TURNOS_REMINDERS_OFF": "1",
            "CONNECTOR_EMR_SYNC": "hsi",
        }
        wa = self._spawn(env)
        self.addCleanup(wa.cleanup)
        wa.wait_line("corriendo")
        wa.wait_line("rekick: chain_alive=False, procediendo", timeout=15)

        _until(lambda: (lambda f: f and f[0][2] in ("doing", "succeeded"))(_job_rows()),
                    "waitlist's job started processing while hsi_sync's rekick was held for "
                    "15s (else run_worker_async() is blocked behind it)", timeout=8)
        wa.stop()

    def test_no_bare_or_baseexception_clauses_can_swallow_cancellation(self):
        """_drain_hsi_sync_startup_tasks' own docstring rests on a
        checkable claim: nothing reachable from the dispatched rekick
        task can swallow asyncio.CancelledError. CancelledError has been
        a BaseException subclass since Python 3.8 SPECIFICALLY so that
        `except Exception` clauses cannot catch it — but that only holds
        if this codebase never writes a bare `except:` or an
        `except BaseException` that WOULD catch it. Static, not
        behavioral: greps the actual reachable call chain's source for
        either pattern and fails loudly if one is ever added — the
        empirical proof that cancellation isn't swallowed TODAY is
        test_shutdown_cancels_and_logs_pending_rekick_task (a real
        subprocess, real SIGTERM, real bounded exit); this test is what
        keeps that proof valid tomorrow.

        Scope (independent-review gate, 2026-08-09 — the first draft
        only scanned worker.py and hsi_sync_queue.py, but rekick() itself
        calls `core.db._connect()` (hsi_sync_queue.py:338,474) and
        `hsi_sync.tick()` (hsi_sync_queue.py:601) — both genuinely
        reachable from the dispatched task, and neither was covered, so
        the test's own docstring claim didn't match what it actually
        checked): core/db.py and connectors/hsi/hsi_sync.py are now
        scanned too."""
        import re
        from app import worker as _worker
        from app.modules.turnos import hsi_journal_queue as _hsi_sync_queue
        from app.modules.turnos import hsi_journal as _hsi_sync
        from app.core import db as _db

        bare_or_base = re.compile(
            r"^\s*except\s*(:|BaseException\b)", re.MULTILINE)
        for mod in (_worker, _hsi_sync_queue, _hsi_sync, _db):
            with open(mod.__file__, encoding="utf-8") as f:
                src = f.read()
            hits = bare_or_base.findall(src)
            self.assertFalse(
                hits,
                f"{mod.__file__} has a bare `except:` or `except "
                f"BaseException` — that WOULD swallow "
                f"asyncio.CancelledError, breaking the cooperative-"
                f"cancellation contract _drain_hsi_sync_startup_tasks "
                f"relies on: {hits}")

    def test_drain_returns_promptly_for_cooperative_slow_task(self):
        """The normal case: a task still running when run_worker_async()
        returns (an ordinary `asyncio.sleep`, which — like every real
        await in the rekick path — responds to cancellation immediately)
        gets cancelled IMMEDIATELY by _drain_hsi_sync_startup_tasks() (no
        wait-for-natural-completion phase — a second one would double-
        spend the outer supervisor's budget, see that function's own
        docstring) and is observed cancelled WELL inside the function's
        OWN reduced internal margin (queue_infra.
        SUPERVISOR_SIGKILL_MARGIN_S minus queue_infra.
        HSI_SYNC_DRAIN_HEADROOM_S — deliberately less than the full
        supervisor margin, see that constant's own comment), nowhere
        near it. In-process, no subprocess needed: isolates the helper's
        own timing from process-spawn overhead — the subprocess-level
        proof of the SAME contract is
        test_shutdown_cancels_and_logs_pending_rekick_task, above."""
        import asyncio
        from app import worker as _worker

        logged = []

        async def _slow():
            await asyncio.sleep(60)

        async def _go():
            task = asyncio.create_task(_slow())
            _worker._HSI_SYNC_STARTUP_TASKS.add(task)
            task.add_done_callback(_worker._HSI_SYNC_STARTUP_TASKS.discard)
            started = time.monotonic()
            await _worker._drain_hsi_sync_startup_tasks()
            return time.monotonic() - started

        with patch.object(_worker, "_log", side_effect=lambda m: logged.append(m)):
            elapsed = asyncio.run(_go())

        drain_margin = (queue_infra.SUPERVISOR_SIGKILL_MARGIN_S
                        - queue_infra.HSI_SYNC_DRAIN_HEADROOM_S)
        self.assertLess(
            elapsed, drain_margin,
            f"drain took {elapsed:.2f}s for a cooperative task — "
            f"asyncio.sleep cancels essentially instantly, so this "
            f"should be nowhere near the function's own reduced "
            f"{drain_margin:g}s cancel-observation ceiling")
        self.assertTrue(
            any("cancelado al apagar" in m for m in logged),
            f"expected the cancelled-and-logged line, got: {logged}")
        self.assertEqual(len(_worker._HSI_SYNC_STARTUP_TASKS), 0,
                         "the done task must be discarded from the "
                         "strong-reference set")

    def test_drain_runs_even_when_worker_run_raises(self):
        """The exact gap a review pass caught in an earlier draft: the
        original code called `await app.run_worker_async(...)` and only
        THEN `await _drain_hsi_sync_startup_tasks(...)` — sequentially,
        not in a `finally`. A crash there (a real possibility: a dropped
        DB connection, a signal-handler exception) would skip the drain
        entirely, abandoning whatever hsi_sync kick was still pending
        with no log, no trace. worker._boot_and_run() now wraps the call
        in try/finally; this proves it — a fake app whose constructed
        Worker's own run() raises, and asserts (a) the exception still
        propagates (this function does not swallow real worker crashes)
        and (b) the drain ran anyway, exactly once.

        `_worker(**kwargs)` (not `run_worker_async(**kwargs)`) is the
        call `_boot_and_run` actually makes since the round-6 credential-
        guard-adjacent SIGTERM-lifecycle fix (worker.py's own docstring):
        constructing the Worker directly lets the signal handler call
        `Worker.stop()` — a safe flag-set with no cancellation-during-
        prune/register risk — instead of `task.cancel()`, which could
        land inside procrastinate's own non-cancellation-safe bootstrap
        steps. The fake mirrors that shape, not the old
        `run_worker_async` one."""
        import asyncio
        from app import worker as _worker
        from contextlib import asynccontextmanager

        drain_calls = []

        async def _fake_drain():
            drain_calls.append(True)

        async def _fake_recover(app, colas):
            return 0

        async def _fake_defer(app):
            return 0

        class _FakeWorker:
            def __init__(self, app, **kwargs):
                # procrastinate's Worker.__init__ stores the constructing
                # app (worker.app.job_manager reaches the heartbeat guard).
                self.app = app

            def stop(self):
                pass

            async def run(self):
                raise RuntimeError("simulated Worker.run() crash")

        class _FakeApp:
            def __init__(self):
                # Real App.__init__ builds one JobManager per app.
                self.job_manager = _FakeJobManager()

            @asynccontextmanager
            async def open_async(self):
                yield self

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker(self, **kwargs)

        with patch.object(_worker, "_drain_hsi_sync_startup_tasks", _fake_drain), \
             patch.object(_worker, "_recover_stalled_jobs", _fake_recover), \
             patch.object(_worker, "_defer_startup_jobs", _fake_defer):
            with self.assertRaises(RuntimeError):
                asyncio.run(_worker._boot_and_run(
                    _FakeApp(), [], "test-worker", 3.0))

        self.assertEqual(drain_calls, [True],
                         "the drain must run exactly once even though "
                         "Worker.run() raised")

    def test_bootstrap_phase_ordering_no_signal(self):
        """Property 1 of the readiness invariant — this module's own
        docstring documents `/ready` as "worker loop running AND
        Postgres reachable", and a first draft of the round-4-6 SIGTERM
        fix flipped `_STATE["fase"]` right after `open_async()` alone,
        which a review caught: recover/defer/Worker-construction hadn't
        even started yet at that point, so `/ready` could claim safety
        while bootstrap was still stuck. `fase` must stay "arrancando"
        through open, THROUGH recover, and THROUGH defer, flipping to
        "corriendo" only once the Worker is about to run. No signal
        involved here — that's the OTHER test, deliberately kept
        separate: sending a stop mid-bootstrap makes `_boot_and_run`
        correctly abort BEFORE later phases, so a single test asserting
        both "all phases run in order" and "a signal aborts early" would
        contradict itself (a real bug caught turning this test's first
        draft into an actual hang, not a mistake to repeat). Every wait
        is bounded (`asyncio.wait_for`) so a real regression here FAILS
        with a clear timeout instead of hanging the suite."""
        import asyncio
        from app import worker as _worker

        _TIMEOUT = 5

        checkpoints = []
        release = {"open": asyncio.Event(), "recover": asyncio.Event(),
                   "defer": asyncio.Event()}
        reached = {"open": asyncio.Event(), "recover": asyncio.Event(),
                   "defer": asyncio.Event(), "run": asyncio.Event()}

        async def _fake_recover(app, colas):
            reached["recover"].set()
            await release["recover"].wait()
            checkpoints.append(("after_recover", _worker._STATE["fase"]))
            return 0

        async def _fake_defer(app):
            reached["defer"].set()
            await release["defer"].wait()
            checkpoints.append(("after_defer", _worker._STATE["fase"]))
            return 0

        async def _fake_drain():
            pass

        class _FakeWorker:
            def __init__(self, app, **kwargs):
                # procrastinate's Worker.__init__ stores the constructing
                # app (worker.app.job_manager reaches the heartbeat guard).
                self.app = app

            def stop(self):
                pass

            async def run(self):
                reached["run"].set()
                checkpoints.append(("worker_run_start", _worker._STATE["fase"]))
                return None

        class _FakeApp:
            def __init__(self):
                # Real App.__init__ builds one JobManager per app.
                self.job_manager = _FakeJobManager()

            class _Ctx:
                async def __aenter__(self):
                    reached["open"].set()
                    await release["open"].wait()
                    checkpoints.append(("after_open", _worker._STATE["fase"]))
                    return None

                async def __aexit__(self, *exc):
                    return None

            def open_async(self):
                return self._Ctx()

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker(self, **kwargs)

        async def _run():
            with patch.object(_worker, "_drain_hsi_sync_startup_tasks", _fake_drain), \
                 patch.object(_worker, "_recover_stalled_jobs", _fake_recover), \
                 patch.object(_worker, "_defer_startup_jobs", _fake_defer):
                task = asyncio.create_task(
                    _worker._boot_and_run(_FakeApp(), [], "test-worker", 3.0))

                await asyncio.wait_for(reached["open"].wait(), _TIMEOUT)
                self.assertEqual(_worker._STATE["fase"], "arrancando",
                                 "/ready must not go green while open_async() "
                                 "is still in flight")
                release["open"].set()

                await asyncio.wait_for(reached["recover"].wait(), _TIMEOUT)
                self.assertEqual(_worker._STATE["fase"], "arrancando",
                                 "/ready must not go green during "
                                 "recovery — bootstrap isn't done")
                release["recover"].set()

                await asyncio.wait_for(reached["defer"].wait(), _TIMEOUT)
                self.assertEqual(_worker._STATE["fase"], "arrancando",
                                 "/ready must not go green during startup "
                                 "sweeps — the Worker doesn't exist yet, "
                                 "so no job loop could be consuming")
                release["defer"].set()

                await asyncio.wait_for(task, _TIMEOUT)
                self.assertEqual(_worker._STATE["fase"], "corriendo",
                                 "fase must reach corriendo once the "
                                 "Worker is genuinely about to run")

        # Explicit precondition, not just "whatever it happened to be" —
        # these tests run inside the same process as every other direct
        # `_boot_and_run` test in this class, and `_STATE` is module-level
        # shared state; a prior test's real end-state ("corriendo") must
        # not leak in as this test's starting assumption (review catch:
        # inheriting "corriendo" made the first assertion here trivially
        # true instead of actually proving anything).
        old_fase = _worker._STATE.get("fase")
        _worker._STATE["fase"] = "arrancando"
        try:
            asyncio.run(_run())
        finally:
            _worker._STATE["fase"] = old_fase

        self.assertEqual(
            checkpoints, [("after_open", "arrancando"),
                          ("after_recover", "arrancando"),
                          ("after_defer", "arrancando"),
                          ("worker_run_start", "corriendo")],
            "fase must stay arrancando through the ENTIRE bootstrap — "
            "open, recover, AND defer — flipping to corriendo only "
            "exactly when Worker.run() is about to start")

    def test_early_sigterm_aborts_bootstrap_before_worker_exists(self):
        """Property 2 of the readiness invariant — signal ownership.
        `test_bootstrap_phase_ordering_no_signal` proves the PHASE
        ordering alone, which would keep passing even if
        `add_signal_handler` were moved to after `open()`, or dropped
        entirely, as long as the fase-flip ordering stayed correct on
        its own. This test proves the handler is actually live from the
        very first line: a REAL `os.kill(os.getpid(), SIGTERM)` sent
        while genuinely blocked inside `open_async().__aenter__()` must
        make `_boot_and_run` abort gracefully WITHOUT EVER reaching
        recover, defer, or Worker.run() — and without `fase` ever
        reaching "corriendo". Every wait is bounded so a real regression
        (the handler silently not installed, or installed too late)
        fails with a clear timeout instead of hanging the suite."""
        import asyncio
        import os
        import signal as _signal
        from app import worker as _worker

        _TIMEOUT = 5

        reached_stages = []
        fase_result = []
        release_open = asyncio.Event()
        reached_open = asyncio.Event()

        async def _fake_recover(app, colas):
            reached_stages.append("recover")
            return 0

        async def _fake_defer(app):
            reached_stages.append("defer")
            return 0

        async def _fake_drain():
            pass

        class _FakeWorker:
            def stop(self):
                pass

            async def run(self):
                reached_stages.append("run")
                return None

        class _FakeApp:
            class _Ctx:
                async def __aenter__(self):
                    reached_open.set()
                    await release_open.wait()
                    return None

                async def __aexit__(self, *exc):
                    return None

            def open_async(self):
                return self._Ctx()

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker()

        async def _run():
            with patch.object(_worker, "_drain_hsi_sync_startup_tasks", _fake_drain), \
                 patch.object(_worker, "_recover_stalled_jobs", _fake_recover), \
                 patch.object(_worker, "_defer_startup_jobs", _fake_defer):
                task = asyncio.create_task(
                    _worker._boot_and_run(_FakeApp(), [], "test-worker", 3.0))

                await asyncio.wait_for(reached_open.wait(), _TIMEOUT)
                self.assertEqual(_worker._STATE["fase"], "arrancando")
                # Real signal, delivered to THIS process, while genuinely
                # blocked in open() — proves the handler is live this
                # early, not inferred from later behavior.
                os.kill(os.getpid(), _signal.SIGTERM)
                await asyncio.sleep(0.05)  # let the loop dispatch it
                release_open.set()

                await asyncio.wait_for(task, _TIMEOUT)
                # Captured HERE, before the outer finally restores
                # `_STATE["fase"]` to its pre-test value — reading it
                # AFTER that restore would silently check the RESTORED
                # value instead of what this run actually produced
                # (review catch: with old_fase=="corriendo", the
                # post-restore assertion would read "corriendo" back and
                # still pass even if this run had genuinely reached
                # "corriendo" itself, proving nothing).
                fase_result.append(_worker._STATE["fase"])

        # Explicit precondition, not just "whatever it happened to be" —
        # these tests run inside the same process as every other direct
        # `_boot_and_run` test in this class, and `_STATE` is module-level
        # shared state; a prior test's real end-state ("corriendo") must
        # not leak in as this test's starting assumption (review catch:
        # inheriting "corriendo" made the first assertion here trivially
        # true instead of actually proving anything).
        old_fase = _worker._STATE.get("fase")
        _worker._STATE["fase"] = "arrancando"
        try:
            asyncio.run(_run())
        finally:
            _worker._STATE["fase"] = old_fase

        self.assertEqual(
            reached_stages, [],
            "a SIGTERM caught during open() must abort bootstrap before "
            "recover, defer, or Worker.run() — none of them should ever "
            "have run")
        self.assertEqual(fase_result, ["arrancando"],
                         "a SIGTERM caught before Worker.run() must "
                         "never let fase reach corriendo")

    def test_signal_during_run_calls_worker_stop_not_task_cancel(self):
        """Second half of the signal-ownership proof: once the Worker
        exists and is running, a real SIGTERM must be handled by
        `Worker.stop()` (a safe flag-set — see worker.py's own docstring
        for why `task.cancel()` was proven unsafe here across three
        review rounds), not by task cancellation propagating as an
        exception, and `_STATE["fase"]` must have already been
        "corriendo" by the time that happens (never flips late). Bounded
        waits throughout so a regression fails fast instead of hanging."""
        import asyncio
        import os
        import signal as _signal
        from app import worker as _worker

        _TIMEOUT = 5

        worker_running = asyncio.Event()
        stop_calls = []
        fase_at_run = []

        async def _fake_recover(app, colas):
            return 0

        async def _fake_defer(app):
            return 0

        async def _fake_drain():
            pass

        class _FakeWorker:
            def __init__(self, app, **kwargs):
                # procrastinate's Worker.__init__ stores the constructing
                # app (worker.app.job_manager reaches the heartbeat guard).
                self.app = app

            def stop(self):
                stop_calls.append(_worker._STATE["fase"])

            async def run(self):
                fase_at_run.append(_worker._STATE["fase"])
                worker_running.set()
                while not stop_calls:
                    await asyncio.sleep(0.01)
                return None

        class _FakeApp:
            from contextlib import asynccontextmanager

            def __init__(self):
                # Real App.__init__ builds one JobManager per app.
                self.job_manager = _FakeJobManager()

            @asynccontextmanager
            async def open_async(self):
                yield None

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker(self, **kwargs)

        async def _run():
            with patch.object(_worker, "_drain_hsi_sync_startup_tasks", _fake_drain), \
                 patch.object(_worker, "_recover_stalled_jobs", _fake_recover), \
                 patch.object(_worker, "_defer_startup_jobs", _fake_defer):
                task = asyncio.create_task(
                    _worker._boot_and_run(_FakeApp(), [], "test-worker", 3.0))
                await asyncio.wait_for(worker_running.wait(), _TIMEOUT)
                self.assertEqual(fase_at_run, ["corriendo"],
                                 "fase must already be corriendo by the "
                                 "time Worker.run() starts")
                os.kill(os.getpid(), _signal.SIGTERM)
                await asyncio.wait_for(task, _TIMEOUT)

        # Explicit precondition, not just "whatever it happened to be" —
        # these tests run inside the same process as every other direct
        # `_boot_and_run` test in this class, and `_STATE` is module-level
        # shared state; a prior test's real end-state ("corriendo") must
        # not leak in as this test's starting assumption (review catch:
        # inheriting "corriendo" made the first assertion here trivially
        # true instead of actually proving anything).
        old_fase = _worker._STATE.get("fase")
        _worker._STATE["fase"] = "arrancando"
        try:
            asyncio.run(_run())
        finally:
            _worker._STATE["fase"] = old_fase

        self.assertEqual(stop_calls, ["corriendo"],
                         "Worker.stop() must be called exactly once, "
                         "with fase already corriendo, not task.cancel()")

    def test_second_signal_forces_exit_not_default_disposition(self):
        """Round-9 review finding: a SECOND SIGTERM/SIGINT must be a
        force-kill escape hatch, not another no-op call to Worker.stop().
        Round-9's OWN fix (restore the OS default disposition on the
        first signal, so a second one falls through to it) was itself
        found broken by an independent review: Python's default
        disposition is silent immediate termination for SIGTERM, but for
        SIGINT it's `signal.default_int_handler`, which RAISES
        `KeyboardInterrupt` as a normal, catchable exception at whatever
        point of execution the interpreter happens to be — reproducible
        as a real uncaught-exception crash on a rapid double SIGINT (see
        docs/solutions/dev/worker-signal-ownership-not-task-cancel.md).

        Fixed: the handler stays installed for `_boot_and_run`'s entire
        lifetime (never removed after the first signal) and counts
        invocations; the SECOND one calls `_force_exit()` — patched here
        to a recorder so this in-process test doesn't kill the test
        runner — instead of falling through to whatever the signal's OS
        default disposition happens to be. Proof: `worker.stop()` is
        called exactly once (from the first signal only), `_force_exit`
        is called exactly once (from the second, with EXIT_FORCE_KILL and
        the real signal number), and the task completes cleanly with
        neither an exception nor a second `worker.stop()` call."""
        import asyncio
        import os
        import signal as _signal
        from app import worker as _worker

        _TIMEOUT = 5
        worker_running = asyncio.Event()
        stop_calls = []
        force_exit_calls = []

        def _fake_force_exit(code, signum):
            force_exit_calls.append((code, signum))

        async def _fake_recover(app, colas):
            return 0

        async def _fake_defer(app):
            return 0

        async def _fake_drain():
            pass

        class _FakeWorker:
            def __init__(self, app, **kwargs):
                # procrastinate's Worker.__init__ stores the constructing
                # app (worker.app.job_manager reaches the heartbeat guard).
                self.app = app

            def stop(self):
                stop_calls.append(_signal.getsignal(_signal.SIGTERM))

            async def run(self):
                worker_running.set()
                while not stop_calls:
                    await asyncio.sleep(0.01)
                # Simulate a real drain window: without this, run()
                # returns within ~10ms of the first signal and
                # _boot_and_run tears its handlers down (outer finally)
                # before the second signal — sent shortly after — has a
                # chance to land on THIS function's handler at all, which
                # would silently fall through to the real OS default
                # disposition and kill the TEST process itself.
                await asyncio.sleep(0.3)
                return None

        class _FakeApp:
            from contextlib import asynccontextmanager

            def __init__(self):
                # Real App.__init__ builds one JobManager per app.
                self.job_manager = _FakeJobManager()

            @asynccontextmanager
            async def open_async(self):
                yield None

            def perform_import_paths(self):
                pass

            def _worker(self, **kwargs):
                return _FakeWorker(self, **kwargs)

        async def _run():
            with patch.object(_worker, "_drain_hsi_sync_startup_tasks", _fake_drain), \
                 patch.object(_worker, "_recover_stalled_jobs", _fake_recover), \
                 patch.object(_worker, "_defer_startup_jobs", _fake_defer), \
                 patch.object(_worker, "_force_exit", _fake_force_exit):
                task = asyncio.create_task(
                    _worker._boot_and_run(_FakeApp(), [], "test-worker", 3.0))
                await asyncio.wait_for(worker_running.wait(), _TIMEOUT)
                os.kill(os.getpid(), _signal.SIGTERM)
                # A moment later, not immediately — gives the first
                # signal's callback a real chance to run first (asyncio
                # dispatches signal callbacks via call_soon, not inline).
                await asyncio.sleep(0.05)
                os.kill(os.getpid(), _signal.SIGTERM)
                await asyncio.wait_for(task, _TIMEOUT)

        asyncio.run(_run())

        self.assertEqual(len(stop_calls), 1,
                         "Worker.stop() must be called exactly once, "
                         "from the FIRST signal only")
        self.assertEqual(
            force_exit_calls, [(_worker.EXIT_FORCE_KILL, _signal.SIGTERM)],
            "the SECOND signal must reach _force_exit exactly once, with "
            "EXIT_FORCE_KILL and the real signal — never fall through to "
            "the OS default disposition (silent for SIGTERM, but a "
            "catchable KeyboardInterrupt for SIGINT)")


class TestHsiResyncRerun(_DbProcTest):
    """The thread/manual fallback's composed durable replay proof.

    hsi_resync deliberately is not queue-registered, but each row now passes
    through hsi_sync's exact-item journal path. This uses a disposable
    Postgres database and a loopback fake HSI server, not a replay mock: HSI
    accepts once, the first local acknowledgement is interrupted, and the
    second fallback tick reuses the same durable intent and HsiClient's real
    remote dedup branch to converge without a second booking.
    """

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def test_remote_accept_local_interruption_recovers_once_through_fallback(self):
        _env.set_env(self, TURNOS_SKIP_HSI=None, TURNOS_CONFIG_LOCK="1")  # SKIP_HSI: module-level pollution, see line 59
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, hsi_status, "
                "  appointment_at, created) "
                "VALUES ('99900017', 'Prueba', 'ResyncCrashWindow', 'cardiologia', "
                "  'hospital-pinamar', 'aprobada', 'error', "
                "  now() + interval '6 hours', now()) RETURNING id")
            sid = cur.fetchone()[0]
        self.addCleanup(self._purge_resync_row, sid)

        fake = _FakeHsiServer()
        self.addCleanup(fake.stop)
        _env.set_env(self, HSI_URL=f"http://127.0.0.1:{fake.port}")
        # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — hsi_fallback's
        # tick() replays through hsi_journal's exact-item path, which
        # no-ops via _emr_selected() with no connector configured.
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")

        def interrupted_local_ack(*args, **kwargs):
            raise RuntimeError("simulated local interruption after HSI accepted")

        with patch.object(db_hsi, "hsi_outbox_ack_appointment", interrupted_local_ack):
            primera = hsi_resync.tick()
        self.assertEqual(primera, 0,
                         "remote acceptance without local acknowledgement is not success")
        self.assertEqual(fake.create_calls, 1,
                         "the first fallback tick reached the real loopback HSI once")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT id, state FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            outbox_id, state = cur.fetchone()
        self.assertEqual(state, "error", "the interrupted durable row is retryable")

        self.assertIsNotNone(db_hsi.hsi_outbox_reset(outbox_id))
        # The fallback is a best-effort periodic retry: under the full managed
        # suite, unrelated worker lifecycle proofs can briefly churn the shared
        # Postgres pool and make one tick return 0 by contract.  Require bounded
        # convergence instead of assuming the very next poll is infallible.
        segunda = _until(hsi_resync.tick, "the fallback tick replayed the row",
                              timeout=5, interval=0.05)
        self.assertEqual(segunda, 1, "the same fallback row confirms on replay")
        self.assertEqual(fake.create_calls, 1,
                         "HsiClient found the remotely accepted booking; no second POST")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT hsi_status, hsi_appointment_id FROM booking_requests WHERE id=%s", (sid,))
            self.assertEqual(cur.fetchone(), ("ok", "1"))
            cur.execute("SELECT state, count(*) OVER () FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            state, count = cur.fetchone()
        self.assertEqual(state, "confirmed")
        self.assertEqual(count, 1, "one durable intent across both fallback runs")

    def _purge_resync_row(self, sid):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("DELETE FROM hsi_appointments WHERE hsi_appointment_id='1'")
            cur.execute("DELETE FROM hsi_checkpoints WHERE entity_type='appointment' AND entity_id='1'")
            cur.execute("DELETE FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            _dbtest.delete_booking_requests(cur, "id=%s", (sid,))


class TestQueueJobExcludesDiskAlert(_DbProcTest):
    """Round-8 gate: the queued housekeeping job is purges-only. Under
    alert-triggering conditions (warn/alert = 100%, owner email seeded,
    dummy Mailgun creds) with the outbound kill switch armed as a
    SENTINEL (any outbound attempt would log BLOQUEADO), two queued
    sweeps succeed with zero outbound attempts, zero low-disk logs and
    zero alert claims: the disk check never rides the queue — it stays
    web-side on the legacy schedule (see the delegation matrix)."""

    def test_queue_housekeeping_never_alerts_or_reaches_outbound(self):
        import shutil
        import tempfile
        datadir = tempfile.mkdtemp(prefix="batch2-datadir-")
        self.addCleanup(shutil.rmtree, datadir, True)
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO admin_account_security (username, owner_email) "
                "VALUES ('admin', 'dueno@example.invalid') "
                "ON CONFLICT (username) DO UPDATE SET owner_email=EXCLUDED.owner_email")
        self.addCleanup(self._clear_owner_email)

        app = queue_infra.build_app()
        sweep = housekeeping_queue.register(app, 1440)
        app.open()
        try:
            sweep.defer(timestamp=1)
            sweep.defer(timestamp=2)
        finally:
            app.close()

        w = self._spawn({
            "TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
            "TURNOS_OUTBOUND_KILL": "1",
            "MAILGUN_API_KEY": "clave-de-prueba",
            "MAILGUN_DOMAIN": "mg.proof.invalid",
            "MAIL_ENABLED": "1",
            "TURNOS_DATA_DIR": datadir,
            "TURNOS_DISK_WARN_PCT": "100",
            "TURNOS_DISK_ALERT_PCT": "100",
        })
        w.wait_line("candado de salida armado")
        w.wait_line("corriendo")
        def _both_finished():
            with self._pg() as c, c.cursor() as cur:
                cur.execute("SELECT count(*) FROM procrastinate_jobs "
                            f"WHERE task_name=%s AND status='succeeded' AND {_MANUAL_TS}",
                            (housekeeping_queue._TASK_NAME,))
                return cur.fetchone()[0] == 2
        _until(_both_finished, "the two sweeps finished", timeout=40)

        for prohibido in ("BLOQUEADO", "espacio libre bajo", "alerta"):
            self.assertFalse([l for l in w.lines if prohibido in l],
                             f"the queued job must never reach the disk check: {prohibido!r}")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM alert_claims")
            self.assertEqual(cur.fetchone()[0], 0,
                             "the queued job must never touch alert claims")
        w.stop()

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _clear_owner_email(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("UPDATE admin_account_security SET owner_email=NULL "
                        "WHERE username='admin' AND owner_email='dueno@example.invalid'")


class TestAlertClaimAtomicity(_DbProcTest):
    """db_analytics.claim_daily_alert against REAL Postgres: exactly one winner,
    also under an N-thread race with their own connections — the base of
    the disk email's at-most-once (Codex gate 2026-08-08)."""

    def test_second_claim_same_key_and_day_loses(self):
        self.assertTrue(db_analytics.claim_daily_alert("test_claim", "2026-08-08"))
        self.assertFalse(db_analytics.claim_daily_alert("test_claim", "2026-08-08"))
        self.assertTrue(db_analytics.claim_daily_alert("test_claim", "2026-08-09"),
                        "a different day is a different claim")
        self.assertTrue(db_analytics.claim_daily_alert("otra_clave", "2026-08-08"),
                        "a different key is a different claim")

    def test_exactly_one_winner_under_thread_race(self):
        resultados = []
        errores = []
        barrera = threading.Barrier(8)

        def _compite():
            try:
                barrera.wait()
                resultados.append(db_analytics.claim_daily_alert("test_carrera", "2026-08-08"))
            except Exception as e:  # noqa: BLE001 — a dead thread is a test failure
                errores.append(repr(e))

        hilos = [threading.Thread(target=_compite) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        self.assertFalse(any(h.is_alive() for h in hilos),
                         "all contenders must finish within the timeout")
        self.assertEqual(errores, [], f"threads with error: {errores}")
        self.assertEqual(len(resultados), 8,
                         "all 8 contenders must complete and report")
        self.assertEqual(resultados.count(True), 1,
                         f"exactly ONE winner among 8 threads: {resultados}")

    def test_old_claims_are_purged_recent_ones_kept(self):
        # The claim feature must clean up after itself (adversarial
        # review 2026-08-08): rows older than the retention window go,
        # today's stay.
        import psycopg
        from datetime import timedelta
        from app.core import clock
        # The purge cuts at the INSTALLATION's day (clock.today()), not Postgres' current_date.
        hoy = clock.today()
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, \
             c.cursor() as cur:
            cur.execute("INSERT INTO alert_claims (alert_key, day) VALUES "
                        "('test_purga', %s), ('test_purga', %s)",
                        (hoy - timedelta(days=200), hoy))
        borradas = db_analytics.purge_old_alert_claims(days=180)
        self.assertEqual(borradas, 1)
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT day FROM alert_claims WHERE alert_key='test_purga'")
            filas = cur.fetchall()
        self.assertEqual(len(filas), 1, "only today's claim survives")


class TestAlertKillBoundary(_DbProcTest):
    """Kill-boundary proof for the WEB-SIDE alert (round 8 moved it off
    the queue, the proof moves with it): a child process runs
    check_disk() directly; the send seam durably records the simulated
    provider ACCEPTANCE and then holds, and the child is SIGKILLed after
    acceptance but BEFORE check_disk returns. A rerun of check_disk()
    against the same Postgres must find the day\'s claim consumed and
    never produce a second acceptance."""

    def test_sigkill_after_provider_acceptance_never_sends_twice(self):
        import shutil
        import tempfile
        datadir = tempfile.mkdtemp(prefix="batch2-killboundary-")
        self.addCleanup(shutil.rmtree, datadir, True)
        accept_file = os.path.join(datadir, "acceptances.log")
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO admin_account_security (username, owner_email) "
                "VALUES ('admin', 'dueno@example.invalid') "
                "ON CONFLICT (username) DO UPDATE SET owner_email=EXCLUDED.owner_email")
        self.addCleanup(self._clear_owner_email)

        seams = {
            "TURNOS_DATA_DIR": datadir,
            "TURNOS_DISK_WARN_PCT": "100",
            "TURNOS_DISK_ALERT_PCT": "100",
            "TURNOS_TEST_SEAMS": "1",
            "TURNOS_TEST_MAIL_ACCEPT_FILE": accept_file,
        }
        child_env = {
            "PATH": os.environ.get("PATH", ""),
            "DATABASE_URL": _TEST_URL,
            "TURNOS_ENV_FILE": os.devnull,
            "TURNOS_FAKE_AI": "1",
            "TURNOS_SKIP_HSI": "1",
            "TURNOS_TEST_MAIL_HOLD_S": "30",
            **seams,
        }
        child = subprocess.Popen(
            [sys.executable, "-c",
             "from app.ops import housekeeping; housekeeping.check_disk()"],
            cwd=ROOT, env=child_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        self.addCleanup(self._kill_child, child)
        def _accepted():
            # The line, not the file: the seam's open(..., "a") creates the file a moment before it
            # writes and fsyncs "accepted" (load ~110: killed in between, 0 != 1).
            if not os.path.exists(accept_file):
                return False
            with open(accept_file) as f:
                return "accepted" in f.read()
        _until(_accepted, "the simulated acceptance was recorded", timeout=30, interval=0.1)
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)  # after acceptance, before return
        child.wait(10)
        with open(accept_file) as f:
            self.assertEqual(f.read().count("accepted"), 1)
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM alert_claims WHERE alert_key='disk_low_email'")
            self.assertEqual(cur.fetchone()[0], 1,
                             "the claim must be durably consumed BEFORE the crash")

        # The rerun (fresh pass, same Postgres, no hold): the consumed
        # claim must prevent a second acceptance, and say so.
        from app.ops import housekeeping as hk
        logs = []
        with _env.patched_env(seams), patch.object(hk, "_log", logs.append):
            out = hk.check_disk()
        self.assertFalse(out["emailed"],
                         "the rerun must not send again — the day\'s claim is consumed")
        self.assertTrue(any("ya fue reclamada" in l for l in logs),
                        f"the rerun must say WHY it is not alerting: {logs}")
        with open(accept_file) as f:
            self.assertEqual(f.read().count("accepted"), 1,
                             "the durable claim must prevent a SECOND acceptance "
                             "after the post-acceptance SIGKILL")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM alert_claims WHERE alert_key='disk_low_email'")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_accept_file_seam_alone_without_master_gate_is_inert(self):
        # Round-4 master-gate proof, now on the web-side path: the seam
        # var WITHOUT TURNOS_TEST_SEAMS=1 must not swallow the real send
        # — the real (unconfigured-Mailgun) send is attempted, fails, and
        # no simulated acceptance is ever recorded.
        import shutil
        import tempfile
        datadir = tempfile.mkdtemp(prefix="batch2-seamgate-")
        self.addCleanup(shutil.rmtree, datadir, True)
        accept_file = os.path.join(datadir, "acceptances.log")
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO admin_account_security (username, owner_email) "
                "VALUES ('admin', 'dueno@example.invalid') "
                "ON CONFLICT (username) DO UPDATE SET owner_email=EXCLUDED.owner_email")
        self.addCleanup(self._clear_owner_email)

        from app.ops import housekeeping as hk
        with _env.patched_env({
                "TURNOS_DATA_DIR": datadir,
                "TURNOS_DISK_WARN_PCT": "100",
                "TURNOS_DISK_ALERT_PCT": "100",
                "TURNOS_TEST_MAIL_ACCEPT_FILE": accept_file,
                # NO TURNOS_TEST_SEAMS: the seam must be dead.
        }):
            out = hk.check_disk()
        self.assertFalse(os.path.exists(accept_file),
                         "the seam recorded an acceptance without the master gate")
        self.assertFalse(out["emailed"],
                         "the real (unconfigured) send path must fail honestly")

    def _kill_child(self, child):
        if child.poll() is None:
            try:
                os.killpg(os.getpgid(child.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            child.wait(10)

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _clear_owner_email(self):
        with self._pg() as c, c.cursor() as cur:
            cur.execute("UPDATE admin_account_security SET owner_email=NULL "
                        "WHERE username='admin' AND owner_email='dueno@example.invalid'")


class TestWorkerHealthServerBoundary(unittest.TestCase):
    """Health server serves only /health and /ready via GET, no app paths."""

    def setUp(self):
        from app import worker

        self._original_state = worker._STATE.copy()
        self._original_db_ok = worker._db_ok
        worker._db_ok = lambda: True
        worker._STATE["fase"] = "corriendo"
        worker._STATE["queues"] = ["test_queue"]

        _env.set_env(self, TURNOS_WORKER_HEALTH_PORT="0")
        self._httpd = worker._start_health_server()
        if self._httpd is None:
            raise RuntimeError("Failed to start health server")
        self._port = self._httpd.server_address[1]

    def tearDown(self):
        from app import worker
        worker._STATE.clear()
        worker._STATE.update(self._original_state)
        worker._db_ok = self._original_db_ok
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    def _request(self, path, method="GET", body=None):
        import urllib.request
        url = f"http://127.0.0.1:{self._port}{path}"
        if body and isinstance(body, dict):
            body = json.dumps(body).encode()
        req = urllib.request.Request(url, data=body, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                payload = e.read()
            try:
                return e.code, json.loads(payload)
            except json.JSONDecodeError:
                return e.code, None
        except urllib.error.URLError as e:
            return None, str(e)

    def test_health_and_ready(self):
        from app import worker
        code, body = self._request("/health")
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))
        self.assertIn("fase", body)
        self.assertIsInstance(body.get("queues"), list)

        code, body = self._request("/ready")
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))

        worker._STATE["fase"] = "arrancando"
        code, body = self._request("/ready")
        self.assertEqual(code, 503)
        self.assertFalse(body.get("ok"))
        worker._STATE["fase"] = "corriendo"

    def test_app_paths_404(self):
        for path in ["/", "/agenda", "/servicios", "/admin", "/api/admin/modules", "/api/booking", "/cuenta"]:
            code, body = self._request(path)
            self.assertEqual(code, 404, f"{path} must return 404, got {code}")

    def test_non_get_methods_fail(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            code, _ = self._request("/health", method=method, body={})
            self.assertEqual(code, 501, f"{method} must return 501 (unimplemented), got {code}")

    def test_invalid_paths_404(self):
        for path in ["/nonexistent", "/api/foo", "/admin/bar"]:
            code, _ = self._request(path)
            self.assertEqual(code, 404, f"{path} must return 404, got {code}")

    def test_content_type_json(self):
        import urllib.request
        url = f"http://127.0.0.1:{self._port}/health"
        with urllib.request.urlopen(url, timeout=5) as r:
            self.assertTrue(r.headers.get("Content-Type", "").startswith("application/json"))

    def test_no_db_handlers_invoked(self):
        from app import worker
        self.assertTrue(worker._db_ok())
        self.assertEqual(self._request("/health")[0], 200)
        self.assertEqual(self._request("/ready")[0], 200)


if __name__ == "__main__":
    unittest.main()
