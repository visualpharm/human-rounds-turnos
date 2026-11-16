"""One controlled real-worker diagnostic lifecycle proof.

The test-table lock holds procrastinate's real prune_stalled_workers SQL call.
SIGUSR1 snapshots that exact await before the lock is released; the worker then
continues through its native register, graceful shutdown and unregistration.
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config, queue_infra  # noqa: E402
from app.core import db_schema  # noqa: E402
config.ENV_PATH = ""
import _dbtest  # noqa: E402
import _workerproc  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_worker_diag")
HAVE_DB = False
DB_SKIP_REASON = ""
TEST_URL = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON, TEST_URL = _db.have_db, _db.skip_reason, _db.test_url


def tearDownModule():
    _db.tear_down()
    # Match the suite-wide leak gate: diagnostic children retain worker.py in
    # argv, so this catches the bootstrap too.
    # Only this process's workers: a parallel shard's live worker is no leak.
    leaked = _workerproc.leaked_worker_pids()
    if leaked:
        raise AssertionError(f"worker diagnostic leaked PIDs {leaked}")


class TestWorkerDiagnostics(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.fail(DB_SKIP_REASON or "managed Postgres unavailable")
        self.addCleanup(db_schema._ensured.clear)  # registered first: runs once the url is back
        _env.set_env(self, DATABASE_URL=TEST_URL)
        db_schema._ensured.clear()
        queue_infra.ensure_schema()
        self.worker = None
        self.addCleanup(self._cleanup_worker)

    def _cleanup_worker(self):
        if self.worker is not None:
            self.worker.cleanup()

    def _worker_ids(self):
        import psycopg
        with psycopg.connect(TEST_URL, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM procrastinate_workers")
            return {row[0] for row in cur.fetchall()}

    def test_prune_wait_dump_then_graceful_unregistration(self):
        import psycopg
        before = self._worker_ids()
        # A synthetic lock on the test database only.  It blocks the real
        # installed procrastinate prune_stalled_workers call without mocking it.
        lock = psycopg.connect(TEST_URL, connect_timeout=5)
        self.addCleanup(lock.close)
        with lock.cursor() as cur:
            # SHARE ROW EXCLUSIVE blocks prune's DELETE (and later register's
            # INSERT) without
            # blocking the already-established schema/configuration reads
            # that precede Worker.run().  Autocommit would release this lock
            # immediately, so this controller intentionally owns one open
            # transaction until the explicit release below.
            cur.execute("LOCK TABLE procrastinate_workers IN SHARE ROW EXCLUSIVE MODE")

        env = {
            "TURNOS_OUTBOUND_KILL": "1",
            "TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
            "TURNOS_HOUSEKEEPING_OFF": "1", "TURNOS_REFERRAL_SWEEP_OFF": "1",
            "TURNOS_BACKUP_OFF": "1", "TURNOS_SUMMARY_PREGEN": "0",
            "TURNOS_TRANSLATE_WARMUP_OFF": "1", "TURNOS_HSI_SYNC_OFF": "1",
        }
        self.worker = _workerproc.WorkerProc(
            _workerproc.worker_env(TEST_URL, env), diagnostic=True)
        self.worker.wait_line("[worker-diagnostic] ready", timeout=10)
        self.worker.wait_line("prune:enter", timeout=20)
        self.assertTrue(self.worker.request_diagnostic_dump())
        self.worker.wait_line("await-chain:begin", timeout=5)
        self.worker.wait_line("prune_stalled_workers", timeout=5)

        lock.close()  # release the named wait; no signal/cancellation shortcut
        exit_code = self.worker.stop()
        self.assertEqual(exit_code, 0)
        traces = "\n".join(self.worker.lines)
        for marker in ("prune:enter", "prune:exit", "register:enter", "register:exit",
                       "shutdown:enter", "shutdown:exit", "unregister:enter", "unregister:exit"):
            self.assertIn(marker, traces)
        self.assertEqual(self._worker_ids() - before, set(),
                         "native unregister_worker must remove this worker row")
