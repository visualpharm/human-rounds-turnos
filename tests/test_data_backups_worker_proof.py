"""Real-process proof for the Tier 4 data-volume backup worker.

Every fixture here is generated below a temporary directory and every B2
request goes to the small loopback server in this file.  The database is
``_dbtest.PerModuleDb``'s disposable database.  No deployment data, B2
credential, remote network, or patient fixture is involved.

The intentionally separate cases cover the boundaries that unit mocks cannot:
two worker processes consume two real queue rows under the native task lock;
the second sweep sees the first journal commit and makes no second upload;
the session sweep lock works with a real one-connection pool; a genuine
SIGKILL after fake B2 acceptance but before the journal commit re-uploads and
converges to one row; a failed pre-accept transport leaves no row; the armed
outbound guard blocks a real socket attempt; and a worker started without the
supervised mode refuses before its deliberately-unreachable database can be touched.
"""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config, db, queue_infra  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.ops import data_backups, data_backups_queue  # noqa: E402

import _dbtest      # noqa: E402
import _env         # noqa: E402
import _wait        # noqa: E402
import _workerproc  # noqa: E402

config.ENV_PATH = ""  # tests never inherit a deployment dotenv file
_db = _dbtest.PerModuleDb("sana_turnos_test_backup_worker")
HAVE_DB = False
DB_SKIP_REASON = ""
_TEST_URL = ""
_TASK = data_backups_queue._TASK_NAME
_MANUAL = "(args->>'timestamp')::bigint < 1000"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()


class _FakeB2:
    """The real JSON upload sequence over loopback, with no B2 SDK/mock."""

    def __init__(self):
        self.uploads = []
        self.fail_before_accept = False
        self.hold_before_accept = False
        self.abort_before_accept = False
        self.upload_entered = threading.Event()
        self.release_upload = threading.Event()
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _json(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if self.path == "/b2api/v2/b2_authorize_account":
                    self._json(200, {"apiUrl": owner.base,
                                     "authorizationToken": "test-auth"})
                    return
                if self.path == "/b2api/v2/b2_get_upload_url":
                    self._json(200, {"uploadUrl": owner.base + "/upload",
                                     "authorizationToken": "test-upload"})
                    return
                if self.path == "/upload":
                    if owner.hold_before_accept:
                        owner.upload_entered.set()
                        owner.release_upload.wait(60)
                    if owner.abort_before_accept:
                        self._json(503, {"status": 503, "code": "test_aborted"})
                        return
                    if owner.fail_before_accept:
                        self._json(503, {"status": 503, "code": "test_down"})
                        return
                    with owner._lock:
                        owner.uploads.append((self.headers.get("X-Bz-File-Name"), body))
                        version = len(owner.uploads)
                    self._json(200, {
                        "fileId": "test-accepted-%d" % version,
                        "fileName": unquote(self.headers.get("X-Bz-File-Name") or ""),
                        "contentLength": len(body),
                        "contentSha1": hashlib.sha1(body).hexdigest(),
                        "action": "upload",
                    })
                    return
                self._json(404, {"error": "unmatched"})

            def do_GET(self):
                # B2 authorization is a GET with HTTP Basic credentials;
                # keep the fake protocol shape identical to urllib's real
                # request rather than accidentally turning it into POST.
                if self.path == "/b2api/v2/b2_authorize_account":
                    self._json(200, {"apiUrl": owner.base,
                                     "authorizationToken": "test-auth"})
                    return
                self._json(404, {"error": "unmatched"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _BackupProof(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "no disposable Postgres")
        self._tmp = tempfile.TemporaryDirectory(prefix="turnos-backup-proof-")
        self.b2 = _FakeB2()
        self._procs = []
        _env.set_env(
            self,
            DATABASE_URL=_TEST_URL,
            TURNOS_TEST_SEAMS="1",
            TURNOS_TEST_BACKUP_DATA_DIR=self._tmp.name,
            TURNOS_TEST_B2_API_URL=self.b2.base,
            TURNOS_B2_KEY_ID="test-key-id",
            TURNOS_B2_APP_KEY="test-app-key",
            TURNOS_B2_BUCKET_ID="test-bucket",
            TURNOS_B2_PREFIX="proof/",
            TURNOS_BACKUP_INTERVAL_MIN="45",
            TURNOS_BACKUP_OFF=None,
            TURNOS_TEST_BACKUP_HOLD_AFTER_ACCEPT_S=None,
        )
        db.close_pools()
        db_schema._ensured.clear()
        queue_infra.ensure_schema()
        self._reset_queue()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for proc in self._procs:
            proc.cleanup()
        self.b2.close()
        db.close_pools()
        db_schema._ensured.clear()
        self._tmp.cleanup()

    def _pg(self):
        import psycopg
        return psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5)

    def _reset_queue(self):
        with self._pg() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE procrastinate_jobs, procrastinate_events, "
                        "procrastinate_periodic_defers RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE backup_version_tombstones, backup_versions, backup_journal CASCADE")

    def _write_fixture(self, name="fixture.bin", content=b"non-phi-backup-proof"):
        path = os.path.join(self._tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    def _clear_transport_cache(self):
        # The production process cache is intentional.  Tests use a fresh
        # loopback server per method, so clear only its test-process cache.
        data_backups._upload_b2.__defaults__[0].clear()

    def _journal_rows(self):
        with self._pg() as conn, conn.cursor() as cur:
            cur.execute("SELECT path, sha256, remote_name, remote_file_id "
                        "FROM backup_journal ORDER BY path")
            return cur.fetchall()

    def _version_rows(self):
        with self._pg() as conn, conn.cursor() as cur:
            cur.execute("SELECT file_id, remote_name, sha256, sha1, size "
                        "FROM backup_versions ORDER BY file_id")
            return cur.fetchall()

    def _defer(self, timestamp):
        app = queue_infra.build_app()
        sweep = data_backups_queue.register(app, 45)
        app.open()
        try:
            sweep.defer(timestamp=timestamp)
        finally:
            app.close()

    def _manual_statuses(self):
        with self._pg() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM procrastinate_jobs WHERE task_name=%s AND "
                        + _MANUAL + " ORDER BY id", (_TASK,))
            return [row[0] for row in cur.fetchall()]

    def _spawn(self, extra=None):
        env = _workerproc.worker_env(_TEST_URL, {
            "TURNOS_TEST_SEAMS": "1",
            "TURNOS_TEST_BACKUP_DATA_DIR": self._tmp.name,
            "TURNOS_TEST_B2_API_URL": self.b2.base,
            "TURNOS_B2_KEY_ID": "test-key-id",
            "TURNOS_B2_APP_KEY": "test-app-key",
            "TURNOS_B2_BUCKET_ID": "test-bucket",
            "TURNOS_B2_PREFIX": "proof/",
            "TURNOS_BACKUP_INTERVAL_MIN": "45",
            "TURNOS_WAITLIST_OFF": "1", "TURNOS_REMINDERS_OFF": "1",
            "TURNOS_HOUSEKEEPING_OFF": "1", "TURNOS_REFERRAL_SWEEP_OFF": "1",
            "TURNOS_SUMMARY_PREGEN": "0", "TURNOS_TRANSLATE_WARMUP_OFF": "1",
            "TURNOS_HSI_SYNC_OFF": "1",
            **(extra or {}),
        })
        proc = _workerproc.WorkerProc(env)
        self._procs.append(proc)
        return proc

    def _wait_statuses(self, wanted, timeout=60):
        current = []

        def reached():
            current[:] = self._manual_statuses()
            return current == wanted

        try:
            _wait.until(reached, "manual backup jobs reach %r" % (wanted,), timeout=timeout, interval=0.2)
        except AssertionError:
            self.fail("manual backup jobs did not reach %r: %r" % (wanted, current))
        return list(current)

    def test_pool_size_one_real_postgres_sweep_completes(self):
        """The session lock and journal helpers share one checkout, so the
        real producer cannot self-deadlock with TURNOS_DB_POOL=1."""
        self._write_fixture()
        _env.set_env(self, TURNOS_DB_POOL="1")
        db.close_pools()
        self._clear_transport_cache()
        self.assertEqual(data_backups.tick(lock_sweep=True), 1)
        self.assertEqual(len(self.b2.uploads), 1)
        self.assertEqual(len(self._journal_rows()), 1)
        journal = self._journal_rows()[0]
        self.assertEqual(journal[3], "test-accepted-1")
        versions = self._version_rows()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0][0], journal[3])
        self.assertEqual(versions[0][1], journal[2])

    def test_cold_rekick_persists_the_legacy_60_second_grace_in_postgres(self):
        """First-run grace is a durable due timestamp, never a sleep."""
        async def kick():
            app = queue_infra.build_app()
            async with app.open_async():
                data_backups_queue.register(app, 45)
                return await data_backups_queue.rekick(
                    app, interval_min=45, startup_delay_s=60)

        self.assertTrue(asyncio.run(kick()))
        with self._pg() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, queueing_lock, "
                "extract(epoch FROM scheduled_at - clock_timestamp()) "
                "FROM procrastinate_jobs WHERE task_name=%s",
                (_TASK,))
            rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        status, queueing_lock, seconds = rows[0]
        self.assertEqual(status, "todo")
        self.assertEqual(queueing_lock, "startup:" + _TASK)
        self.assertGreater(seconds, 45, seconds)
        self.assertLessEqual(seconds, 65, seconds)

    def test_two_real_workers_two_rows_serialize_and_second_is_journal_noop(self):
        self._write_fixture()
        self._defer(101)
        self._defer(102)
        a = self._spawn({"TURNOS_TEST_BACKUP_HOLD_AFTER_ACCEPT_S": "3"})
        b = self._spawn({"TURNOS_TEST_BACKUP_HOLD_AFTER_ACCEPT_S": "3"})
        a.wait_line("corriendo")
        b.wait_line("corriendo")
        self._wait_statuses(["succeeded", "succeeded"], timeout=75)
        self.assertEqual(len(self.b2.uploads), 1,
                         "serialized second row must see the journal and no-op")
        self.assertEqual(len(self._journal_rows()), 1)
        a.stop()
        b.stop()

    def test_direct_legacy_tick_and_worker_share_the_postgres_sweep_lock(self):
        """Topology skew is safe independently of Procrastinate's task lock.

        The worker owns a real queued row and pauses only after the fake B2
        server accepted it.  A direct web-thread-shaped tick then blocks on
        the *session advisory* sweep lock (not the queue task lock), performs
        no second upload, and returns its journal no-op only after the worker
        commits/releases.
        """
        self._write_fixture()
        self._defer(105)
        worker = self._spawn({"TURNOS_TEST_BACKUP_HOLD_AFTER_ACCEPT_S": "5"})
        worker.wait_line("corriendo")
        worker.wait_line("remote accepted; holding before journal commit", timeout=35)
        self.assertEqual(len(self.b2.uploads), 1)
        self._clear_transport_cache()
        outcome = {}

        def direct_web_tick():
            outcome["uploaded"] = data_backups.tick(lock_sweep=True)

        direct = threading.Thread(target=direct_web_tick, daemon=True)
        direct.start()
        _wait.stays(direct.is_alive, "direct web tick must wait for worker's session lock", for_s=0.5)
        self.assertEqual(len(self.b2.uploads), 1,
                         "the direct path cannot reach B2 while worker holds lock")
        self._wait_statuses(["succeeded"], timeout=45)
        direct.join(20)
        self.assertFalse(direct.is_alive())
        self.assertEqual(outcome.get("uploaded"), 0)
        self.assertEqual(len(self.b2.uploads), 1)
        self.assertEqual(len(self._journal_rows()), 1)
        worker.stop()

    def test_sigkill_after_remote_accept_reuploads_then_leaves_one_journal_row(self):
        self._write_fixture()
        self._defer(103)
        worker = self._spawn({"TURNOS_TEST_BACKUP_HOLD_AFTER_ACCEPT_S": "30"})
        worker.wait_line("corriendo")
        worker.wait_line("remote accepted; holding before journal commit", timeout=35)
        self.assertEqual(len(self.b2.uploads), 1)
        worker._killpg()
        worker.proc.wait(10)
        self.assertEqual(self._journal_rows(), [],
                         "SIGKILL happened after remote acceptance, before commit")
        self._clear_transport_cache()
        self.assertEqual(data_backups.tick(lock_sweep=True), 1)
        self.assertEqual(len(self.b2.uploads), 2,
                         "uncommitted remote acceptance is safely retried")
        self.assertEqual(len(self._journal_rows()), 1)

    def test_failed_before_remote_accept_never_journals(self):
        self._write_fixture()
        self.b2.fail_before_accept = True
        self._clear_transport_cache()
        self.assertEqual(data_backups.tick(lock_sweep=True), 0)
        self.assertEqual(self.b2.uploads, [])
        self.assertEqual(self._journal_rows(), [])

    def test_sigkill_before_remote_accept_leaves_no_journal_or_remote_effect(self):
        """The distinct pre-accept crash boundary is not a 503 surrogate.

        The real child reaches a fake B2 upload request, whose server holds
        *before* recording acceptance.  Kill the child, make that synthetic
        remote abort its held request, then prove no remote acceptance and no
        journal row.  A fresh real tick can subsequently accept and journal
        the one pending non-PHI fixture.
        """
        self._write_fixture()
        self.b2.hold_before_accept = True
        self._defer(106)
        worker = self._spawn()
        worker.wait_line("corriendo")
        self.assertTrue(self.b2.upload_entered.wait(35),
                        "worker must reach the real fake-B2 upload boundary")
        worker._killpg()
        worker.proc.wait(10)
        self.b2.abort_before_accept = True
        self.b2.release_upload.set()
        _wait.stays(lambda: self.b2.uploads == [], "the aborted upload is never accepted", for_s=0.2)
        self.assertEqual(self.b2.uploads, [])
        self.assertEqual(self._journal_rows(), [])
        self.b2.hold_before_accept = False
        self.b2.abort_before_accept = False
        self._clear_transport_cache()
        self.assertEqual(data_backups.tick(lock_sweep=True), 1)
        self.assertEqual(len(self.b2.uploads), 1)
        self.assertEqual(len(self._journal_rows()), 1)

    def test_armed_worker_blocks_dummy_socket_and_never_journals_success(self):
        self._write_fixture()
        # TEST_SEAMS chooses this non-routable endpoint only inside the child;
        # the socket guard must refuse it before a packet can leave.
        self._defer(104)
        worker = self._spawn({
            "TURNOS_OUTBOUND_KILL": "1",
            # The local dummy endpoint is accepted by the test seam but
            # forbidden by the guard (loopback is allowed ONLY on the DB
            # port), so we prove a real socket refusal without DNS or any
            # external-network attempt.
            "TURNOS_TEST_B2_API_URL": "http://127.0.0.1:9",
        })
        worker.wait_line("candado de salida armado")
        worker.wait_line("BLOQUEADO: connect", timeout=35)
        self._wait_statuses(["succeeded"], timeout=45)
        self.assertEqual(self._journal_rows(), [])
        self.assertEqual(self.b2.uploads, [])
        worker.stop()


class TestWorkerModeBackupGate(unittest.TestCase):
    def test_the_retired_external_mode_refuses_before_database_or_fixture_scan(self):
        with tempfile.TemporaryDirectory(prefix="turnos-backup-gate-") as root:
            marker = os.path.join(root, "must-not-read.bin")
            with open(marker, "wb") as fh:
                fh.write(b"non-phi")
            env = {
                "PATH": os.environ.get("PATH", ""),
                "TURNOS_ENV_FILE": os.devnull,
                "TURNOS_QUEUE_WORKER": "external",
                "DATABASE_URL": "postgresql://127.0.0.1:1/unreachable",
                "TURNOS_TEST_SEAMS": "1",
                "TURNOS_TEST_BACKUP_DATA_DIR": root,
                "TURNOS_FAKE_AI": "1", "TURNOS_SKIP_HSI": "1",
            }
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "app", "worker.py")],
                                  cwd=ROOT, env=env, capture_output=True,
                                  text=True, timeout=20)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("no es un modo válido", proc.stderr)
        self.assertNotIn("Connection refused", proc.stderr)
        self.assertNotIn("no se pudo abrir", proc.stderr)

    def test_effective_file_cannot_enable_the_worker_mode(self):
        with tempfile.NamedTemporaryFile("w", prefix="turnos-topology-", delete=False) as fh:
            fh.write("TURNOS_QUEUE_WORKER=1\n")
            dotenv = fh.name
        self.addCleanup(lambda: os.path.exists(dotenv) and os.unlink(dotenv))
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TURNOS_ENV_FILE": dotenv,
            "DATABASE_URL": "postgresql://127.0.0.1:1/unreachable",
            "TURNOS_FAKE_AI": "1", "TURNOS_SKIP_HSI": "1",
        }
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "app", "worker.py")],
                              cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=20)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("no es un modo válido", proc.stderr)
        self.assertNotIn("Connection refused", proc.stderr)


if __name__ == "__main__":
    unittest.main()
