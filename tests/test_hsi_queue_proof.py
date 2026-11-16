"""test_hsi_queue_proof.py — Tier 4 daemon batch 4 proof: the durable HSI
sync journal on the worker-topology registry
(docs/roadmap/tier4-replatform-design.md, "Worker topology").

Scope, stated honestly and CURRENT: ONLY hsi_sync (the durable bidirectional
journal) joins the queue registry. hsi_resync remains purely on-thread in
every topology, preserving its manual/compatibility role rather than becoming
a second queue migration. Its outward effect now reuses hsi_sync's journal,
but `TURNOS_QUEUE_WORKER` still never schedules it. There is therefore no
"sync-vs-resync registry exclusivity" question: hsi_resync is not a registry
candidate.

This file proves: delegation (hsi_sync's OFF flag wins over
TURNOS_QUEUE_WORKER, checked first, same order as every other
queue-migrated daemon; hsi_resync NEVER delegates — TURNOS_QUEUE_WORKER
does not affect it, only its own OFF flag does), the native per-task
execution lock being registered on hsi_sync's task, and zero-egress/
zero-mutation under the armed outbound kill switch with a CONFIGURED
dummy HSI_URL for BOTH tick() functions (real hsi_client network attempt,
BLOCKED at the socket, degrades without raising) — hsi_resync.tick() is
still real, reachable code (the thread can still run it), so its outbound
behavior under the kill switch stays worth proving even though it isn't
queue-scheduled.

Not "no DB dependency" though: the delegation/lock tests genuinely don't
touch Postgres, but TestZeroEgressUnderKillSwitch's resync case seeds a
REAL pending row (review-fix 2026-08-09 — the first draft asserted
nothing because it had no row to push, a vacuous pass) on its own
disposable per-module database (`_dbtest.PerModuleDb`, dropped in
tearDownModule, never the caller's real DATABASE_URL/TEST_DATABASE_URL) —
proving a real connect attempt is genuinely made and blocked, not just
that the flag is set.

The REAL two-worker contention, mid-task-SIGKILL-recovery, and
remote-accepted/local-uncommitted crash-window proofs for hsi_sync live in
tests/test_queue_worker.py's TestHsiSyncCrashRecoveryAndContention (real
disposable Postgres, real subprocess workers, a real local fake-HSI HTTP
server, a genuine SIGKILL — not inherited by citation from the AI-cache
pair, and not simulated with a raised Python exception). hsi_resync delegates
to that same exact-item replay primitive; its own focused tests prove that the
thread keeps stop-first and never reaches the historical direct push. HSI-down
backoff classification remains owned by the durable journal and is proven in
test_hsi_sync.py::TestOutboxReplay::test_remote_failure_stays_durably_retryable
and test_hsi_resync.py::TestTick::test_journal_sin_confirmar_corta_el_barrido.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
      discover -s tests -p "test_hsi_queue_proof.py" -v
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""      # never read the real .env (same trick as the siblings)

from app.core import outbound_guard  # noqa: E402
from app.core import queue_infra     # noqa: E402
from app.modules.turnos import hsi_fallback         # noqa: E402
from app.modules.turnos import hsi_journal           # noqa: E402
from app.modules.turnos import hsi_journal_queue     # noqa: E402

import _dbtest  # noqa: E402

# Disposable per-module database (tests/_dbtest.py's shared pattern — the
# ~28 other DB-backed test files use it, never a direct connect to
# whatever DATABASE_URL happens to resolve to). Review fix (2026-08-09):
# the first draft's TestZeroEgressUnderKillSwitch._pg() connected straight
# to TEST_DATABASE_URL-or-DATABASE_URL and ran real INSERT/DELETE against
# it — on a misconfigured env where either var points at a real dev/shared
# database, that mutates it. PerModuleDb instead CREATEs a throwaway
# sana_turnos_test_hsi_queue_proof database (dropped in tearDownModule)
# and points DATABASE_URL at THAT for the duration of this module's run —
# the only DB this file's tests ever touch.
_db = _dbtest.PerModuleDb("sana_turnos_test_hsi_queue_proof")

HAVE_DB = False
DB_SKIP_REASON = ""
_TEST_URL = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url
    # Pre-warm core.config's DB-backed settings lookup before tests that use
    # business-module configuration. `queue_infra.worker_mode()` itself is
    # intentionally process-env-only: topology is process placement, so it
    # cannot be supplied by a DB override or effective config file.
    try:
        config.get("TURNOS_QUEUE_WORKER")
    except Exception:  # noqa: BLE001 — no DB reachable: nothing to warm
        pass


def tearDownModule():
    _db.tear_down()

_FLAGS = ("CONNECTOR_EMR_SYNC", "TURNOS_QUEUE_WORKER", "TURNOS_HSI_SYNC_OFF", "TURNOS_HSI_RESYNC_OFF",
          "TURNOS_SKIP_HSI", "HSI_URL", "TURNOS_OUTBOUND_KILL")


class _FlagSandbox(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {k: None for k in _FLAGS})
        # This file proves the legacy HSI queue contract, so select the
        # legacy connector instead of relying on its old implicit default.
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")

    def tearDown(self):
        if outbound_guard.armed():
            outbound_guard.disarm()


class TestDelegationMatrix(_FlagSandbox):
    """hsi_sync: OFF wins over delegation, checked first — same order as
    every other queue-migrated daemon (translate_warmup.start(),
    chart_summary.start()). With TURNOS_QUEUE_WORKER set, the web process
    starts NO thread; the default (flag unset) stays byte-identical to
    before this batch.

    hsi_resync: the OPPOSITE contract, deliberately (batch-4 review,
    narrowed scope) — it NEVER delegates. TURNOS_QUEUE_WORKER has no
    effect on it in any value; only its own OFF flag stops the thread."""

    def test_hsi_sync_delegates_no_thread(self):
        for modo in ("1", "1 "):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo)
            with patch("threading.Thread") as mock_thread:
                self.assertIsNone(hsi_journal.start())
            mock_thread.assert_not_called()

    def test_hsi_sync_off_switch_wins_over_delegation(self):
        _env.set_env(self, TURNOS_HSI_SYNC_OFF="1", TURNOS_QUEUE_WORKER="1")
        with patch("threading.Thread") as mock_thread:
            self.assertIsNone(hsi_journal.start())
        mock_thread.assert_not_called()

    def test_hsi_sync_flag_unset_keeps_thread_exactly_as_before(self):
        with patch("threading.Thread") as mock_thread:
            hsi_journal.start()
        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["name"], "hsi-sync")

    def test_hsi_resync_never_delegates_regardless_of_queue_worker(self):
        for modo in (None, "1", "external"):
            _env.set_env(self, TURNOS_QUEUE_WORKER=modo or None)
            with patch.object(hsi_fallback.threading, "Thread") as mock_thread:
                hsi_fallback.start()
            mock_thread.assert_called_once()
            self.assertEqual(mock_thread.call_args.kwargs["name"], "hsi-resync",
                             f"TURNOS_QUEUE_WORKER={modo!r}")

    def test_hsi_resync_off_switch_still_stops_it(self):
        _env.set_env(self, TURNOS_HSI_RESYNC_OFF="1", TURNOS_QUEUE_WORKER="1")
        with patch.object(hsi_fallback.threading, "Thread") as mock_thread:
            self.assertIsNone(hsi_fallback.start())
        mock_thread.assert_not_called()


class TestRegistrationLock(_FlagSandbox):
    """The native per-task execution lock is actually applied to
    hsi_sync's task — same `queue_infra.task_lock_name` derivation the
    batch-3 AI-cache warmups use via `register_periodic_sweep`'s
    exclusive=True, though hsi_sync_queue.py registers its own
    `@app.task(..., lock=...)` directly (self-rescheduling, not a
    periodic sweep — see that module's docstring). Real two-worker
    contention already proven generically for the AI-cache pair
    (TestAiCacheExecutionLock) and specifically for hsi_sync itself
    (test_queue_worker.py's TestHsiSyncCrashRecoveryAndContention)."""

    def _app(self):
        import procrastinate
        import procrastinate.testing
        return procrastinate.App(connector=procrastinate.testing.InMemoryConnector())

    def test_hsi_sync_carries_the_native_lock(self):
        app = self._app()
        hsi_journal_queue.register(app, 5)
        tn = hsi_journal_queue._TASK_NAME
        self.assertEqual(app.tasks[tn].lock, queue_infra.task_lock_name(tn))

    def test_hsi_sync_is_the_only_hsi_job_in_the_registry(self):
        # hsi_resync stays purely on-thread — asserted at the registry
        # level so a future accidental re-addition fails loudly here,
        # not just in worker.py's own tests.
        from app import worker
        ids = {s.id for s in worker._JOBS}
        self.assertIn("hsi_sync", ids)
        self.assertNotIn("hsi_resync", ids)

    def test_startup_declared_for_hsi_sync(self):
        # hsi_sync's thread twin ticks immediately at boot — the registry
        # entry preserves that verbatim as a deferred immediate sweep.
        from app import worker
        by_id = {s.id: s for s in worker._JOBS}
        self.assertTrue(by_id["hsi_sync"].startup)
        self.assertEqual(by_id["hsi_sync"].startup_delay_s, 0)


class TestZeroEgressUnderKillSwitch(_FlagSandbox):
    """Zero external egress with a CONFIGURED dummy HSI_URL and the
    fail-closed outbound kill switch armed (core/outbound_guard.py) —
    same discipline as test_ai_cache_queue_proof.py for the batch-3
    warmups. A real hsi_client network attempt must be ATTEMPTED and
    BLOCKED, never silently skipped (an unconfigured/TURNOS_SKIP_HSI
    path would pass this vacuously). Proven for BOTH hsi_sync.tick() and
    hsi_resync.tick() — hsi_resync isn't queue-scheduled, but its tick()
    is still real, reachable code (the thread can run it any time), so
    its outbound behavior under the kill switch stays worth proving."""

    def setUp(self):
        super().setUp()
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def _pg(self):
        import psycopg
        # ONLY the disposable per-module DB (_TEST_URL, set up by
        # setUpModule via _dbtest.PerModuleDb) — never a fallback to
        # whatever DATABASE_URL/TEST_DATABASE_URL happen to resolve to in
        # the caller's shell, which could be a real dev/shared database.
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _purge_zero_egress_row(self, sid):
        with self._pg() as c, c.cursor() as cur:
            _dbtest.delete_booking_requests(cur, "id=%s", (sid,))

    def test_tick_degrades_safely_when_hsi_call_is_blocked_at_the_socket(self):
        _env.set_env(self, HSI_URL="http://127.0.0.1:8280")  # configured, dummy
        _env.set_env(self, TURNOS_SKIP_HSI=None)  # the real path must be exercised
        before = outbound_guard.blocked_count()
        outbound_guard.arm()
        self.addCleanup(outbound_guard.disarm)
        try:
            resultado = hsi_journal.tick()
        except Exception as exc:  # noqa: BLE001 — tick()'s own contract is never-raise
            self.fail(f"tick() must never raise even with HSI blocked: {exc!r}")
        self.assertGreater(outbound_guard.blocked_count(), before,
                           "a real connect toward HSI_URL must have been "
                           "attempted and refused at the socket")
        # reconcile() degrades to the zero-counts fallback (tick()'s own
        # except-clause contract); no schedules/appointments/patients can
        # have been imported because no packet reached HSI.
        self.assertEqual(resultado.get("schedules"), 0)
        self.assertEqual(resultado.get("appointments"), 0)
        self.assertEqual(resultado.get("patients", 0), 0)

    def test_resync_tick_never_raises_when_hsi_call_would_be_blocked(self):
        """Review-fix (2026-08-09): the first draft seeded no pending row,
        so `tick()` never even reached the push to HSI —
        `blocked_count` couldn't move and the test passed vacuously
        (identical failure mode to an unconfigured/TURNOS_SKIP_HSI path,
        the exact thing this class's own docstring warns against). Seeds a
        REAL pending row (hsi_status='error', future appointment — the
        same shape `db_booking.booking_requests_hsi_pending` selects) so `tick()`
        genuinely enters the durable exact-item replay path, attempts a
        network connect toward HSI_URL, and is refused at the socket."""
        with self._pg() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO booking_requests (dni, first_name, last_name, "
                "  service_id, center_slug, status, hsi_status, "
                "  appointment_at, created) "
                "VALUES ('99900019', 'Prueba', 'ZeroEgressResync', "
                "  'cardiologia', 'hospital-pinamar', 'aprobada', 'error', "
                "  now() + interval '8 hours', now()) RETURNING id")
            sid = cur.fetchone()[0]
        self.addCleanup(self._purge_zero_egress_row, sid)

        _env.set_env(self, HSI_URL="http://127.0.0.1:8280", TURNOS_SKIP_HSI=None)
        before = outbound_guard.blocked_count()
        outbound_guard.arm()
        self.addCleanup(outbound_guard.disarm)
        try:
            sincronizadas = hsi_fallback.tick()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"tick() must never raise: {exc!r}")
        self.assertGreater(outbound_guard.blocked_count(), before,
                           "a real connect toward HSI_URL must have been "
                           "attempted (via hsi_sync.replay_item) and "
                           "refused at the socket — otherwise this proves "
                           "nothing about the outbound path")
        with self._pg() as c, c.cursor() as cur:
            cur.execute("SELECT hsi_status FROM booking_requests WHERE id=%s", (sid,))
            self.assertEqual(cur.fetchone()[0], "error",
                             "blocked at the socket: no mutation, row stays "
                             "exactly as seeded")
        self.assertEqual(sincronizadas, 0)


if __name__ == "__main__":
    unittest.main()
