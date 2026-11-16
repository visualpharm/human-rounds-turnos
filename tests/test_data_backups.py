"""Deterministic producer tests for the B2 backup journal.

The B2 transport is injected and the database helpers are a tiny in-memory
journal. These tests never need B2 credentials, a real database, or patient
data.
"""

import hashlib
import os
import types
import sys
import tempfile
import unittest
import contextlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ops import data_backups  # noqa: E402
import _env  # noqa: E402
from app.core import db_backup  # noqa: E402


class TestDataBackupsJournal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data = data_backups.DATA_DIR
        data_backups.DATA_DIR = self._tmp.name
        self.journal = {}
        self.refreshes = []

        self.map_mock = mock.patch.object(
            db_backup, "backup_journal_map", side_effect=self._map).start()
        self.commit_mock = mock.patch.object(
            db_backup, "backup_journal_commit", side_effect=self._commit).start()
        self.refresh_mock = mock.patch.object(
            db_backup, "backup_journal_refresh_stat",
            side_effect=self._refresh_stat).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        data_backups.DATA_DIR = self._orig_data
        self._tmp.cleanup()

    def _map(self):
        return {path: dict(row) for path, row in self.journal.items()}

    def _commit(self, path, sha256, size, mtime_ns, remote_name, origin="upload", receipt=None):
        self.journal[path] = {
            "sha256": sha256,
            "size": size,
            "mtime_ns": mtime_ns,
            "remote_name": remote_name,
            "remote_file_id": receipt["file_id"] if receipt else None,
            "origin": origin,
        }

    def _refresh_stat(self, path, size, mtime_ns):
        self.refreshes.append((path, size, mtime_ns))
        self.journal[path]["size"] = size
        self.journal[path]["mtime_ns"] = mtime_ns

    def _write(self, rel, content=b"x"):
        path = os.path.join(self._tmp.name, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_new_upload_records_exact_accepted_bytes_and_remote_name(self):
        payload = b"png-bytes"
        self._write("orders/1.png", payload)
        uploaded = []

        count = data_backups.tick(
            subir_fn=lambda remote, data, sha256: uploaded.append((remote, data, sha256)))

        expected_hash = hashlib.sha256(payload).hexdigest()
        self.assertEqual(count, 1)
        self.assertEqual(uploaded, [("data/orders/1.png", payload, expected_hash)])
        self.assertEqual(self.journal["orders/1.png"]["sha256"], expected_hash)
        self.assertEqual(self.journal["orders/1.png"]["remote_name"], "data/orders/1.png")
        self.assertEqual(self.journal["orders/1.png"]["size"], len(payload))

    def test_malformed_remote_receipt_neither_commits_current_path_nor_claims_success(self):
        """A B2 HTTP success body is not acceptance unless it matches bytes.

        This is intentionally an injected transport rather than a mock of the
        validator: the real producer must reject malformed remote metadata
        before calling either journal helper.
        """
        payload = b"receipt-boundary"
        self._write("orders/1.png", payload)
        bad = {
            "fileId": "bad-id", "fileName": "data/orders/other.png",
            "contentLength": len(payload) + 1,
            "contentSha1": hashlib.sha1(payload).hexdigest(), "action": "upload",
        }
        self.assertEqual(data_backups.tick(subir_fn=lambda *_args: bad), 0)
        self.assertEqual(self.journal, {})
        self.commit_mock.assert_not_called()

    def test_valid_remote_receipt_is_bound_to_the_current_journal_path(self):
        payload = b"receipt-boundary"
        self._write("orders/1.png", payload)
        receipt = {
            "fileId": "exact-id", "fileName": "data/orders/1.png",
            "contentLength": len(payload),
            "contentSha1": hashlib.sha1(payload).hexdigest(), "action": "upload",
        }
        self.assertEqual(data_backups.tick(subir_fn=lambda *_args: receipt), 1)
        self.assertEqual(self.journal["orders/1.png"]["remote_file_id"], "exact-id")

    def test_unchanged_file_skips_transport(self):
        self._write("orders/1.png", b"first")
        uploaded = []
        transport = lambda remote, data, sha256: uploaded.append(remote)

        self.assertEqual(data_backups.tick(subir_fn=transport), 1)
        self.assertEqual(data_backups.tick(subir_fn=transport), 0)
        self.assertEqual(uploaded, ["data/orders/1.png"])

    def test_changed_bytes_upload_again(self):
        path = self._write("orders/1.png", b"v1")
        uploaded = []
        transport = lambda remote, data, sha256: uploaded.append((remote, data, sha256))
        self.assertEqual(data_backups.tick(subir_fn=transport), 1)

        with open(path, "wb") as f:
            f.write(b"v2-with-a-different-size")
        self.assertEqual(data_backups.tick(subir_fn=transport), 1)
        self.assertEqual(len(uploaded), 2)
        self.assertEqual(uploaded[-1][1], b"v2-with-a-different-size")
        self.assertEqual(self.journal["orders/1.png"]["sha256"], uploaded[-1][2])

    def test_upload_failure_leaves_file_pending_for_retry(self):
        self._write("orders/1.png", b"retry")
        attempts = []

        def failing_transport(remote, data, sha256):
            attempts.append((remote, data, sha256))
            raise OSError("synthetic transport failure")

        self.assertEqual(data_backups.tick(subir_fn=failing_transport), 0)
        self.assertEqual(self.journal, {})

        self.assertEqual(data_backups.tick(
            subir_fn=lambda remote, data, sha256: attempts.append((remote, data, sha256))), 1)
        self.assertEqual(len(attempts), 2)
        self.assertIn("orders/1.png", self.journal)

    def test_journal_commit_failure_reuploads_instead_of_claiming_success(self):
        self._write("orders/1.png", b"commit-retry")
        uploaded = []
        real_commit = self._commit
        attempts = 0

        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("synthetic journal failure")
            return real_commit(*args, **kwargs)

        self.commit_mock.side_effect = fail_once
        transport = lambda remote, data, sha256: uploaded.append((remote, data, sha256))
        self.assertEqual(data_backups.tick(subir_fn=transport), 0)
        self.assertEqual(self.journal, {})
        self.assertEqual(data_backups.tick(subir_fn=transport), 1)

        self.assertEqual(len(uploaded), 2, "the unjournaled remote upload is retried")
        self.assertIn("orders/1.png", self.journal)

    def test_stat_only_change_refreshes_journal_without_remote_upload(self):
        path = self._write("orders/1.png", b"same-bytes")
        uploaded = []
        transport = lambda remote, data, sha256: uploaded.append((remote, data, sha256))
        self.assertEqual(data_backups.tick(subir_fn=transport), 1)
        uploaded.clear()

        initial_mtime = os.stat(path).st_mtime_ns
        os.utime(path, ns=(initial_mtime, initial_mtime + 2_000_000_000))
        current_mtime = os.stat(path).st_mtime_ns
        self.assertNotEqual(current_mtime, initial_mtime)

        self.assertEqual(data_backups.tick(subir_fn=transport), 0)
        self.assertEqual(uploaded, [])
        self.assertEqual(self.refreshes[-1], ("orders/1.png", len(b"same-bytes"), current_mtime))
        self.assertEqual(self.journal["orders/1.png"]["mtime_ns"], current_mtime)

    def test_changed_remote_prefix_reuploads_identical_bytes_under_new_name(self):
        payload = b"same-bytes"
        self._write("orders/1.png", payload)
        uploaded = []
        transport = lambda remote, data, sha256: uploaded.append((remote, data, sha256))

        with mock.patch.object(data_backups, "_env", return_value="first/"):
            self.assertEqual(data_backups.tick(subir_fn=transport), 1)
        with mock.patch.object(data_backups, "_env", return_value="second/"):
            self.assertEqual(data_backups.tick(subir_fn=transport), 1)

        self.assertEqual(
            uploaded,
            [("first/orders/1.png", payload, hashlib.sha256(payload).hexdigest()),
             ("second/orders/1.png", payload, hashlib.sha256(payload).hexdigest())])
        self.assertEqual(self.journal["orders/1.png"]["remote_name"], "second/orders/1.png")

    def test_unstable_descriptor_read_neither_uploads_nor_commits(self):
        self._write("orders/1.png", b"racing-writer")
        stable = types.SimpleNamespace(st_size=len(b"racing-writer"), st_mtime=1,
                                       st_mtime_ns=1, st_ctime=1, st_ctime_ns=1,
                                       st_dev=1, st_ino=2)
        changed = types.SimpleNamespace(st_size=len(b"racing-writer"), st_mtime=1,
                                        st_mtime_ns=1, st_ctime=2, st_ctime_ns=2,
                                        st_dev=1, st_ino=2)
        uploaded = []

        with mock.patch.object(data_backups.os, "fstat", side_effect=[stable, changed]):
            self.assertEqual(data_backups.tick(
                subir_fn=lambda remote, data, sha256: uploaded.append(remote)), 0)
        self.assertEqual(uploaded, [])
        self.assertEqual(self.journal, {})
        self.commit_mock.assert_not_called()

    def test_pathname_replacement_after_read_neither_uploads_nor_commits(self):
        path = self._write("orders/1.png", b"replace-race")
        descriptor = types.SimpleNamespace(st_size=len(b"replace-race"), st_mtime=1,
                                           st_mtime_ns=1, st_ctime=1, st_ctime_ns=1,
                                           st_dev=1, st_ino=2)
        replacement = types.SimpleNamespace(st_size=len(b"replace-race"), st_mtime=1,
                                            st_mtime_ns=1, st_ctime=1, st_ctime_ns=1,
                                            st_dev=1, st_ino=3)
        uploaded = []

        with mock.patch.object(
                data_backups, "_candidate_paths",
                return_value=[("orders/1.png", path, descriptor)]), \
             mock.patch.object(data_backups.os, "fstat",
                               side_effect=[descriptor, descriptor]), \
             mock.patch.object(data_backups.os, "stat", return_value=replacement):
            self.assertEqual(data_backups.tick(
                subir_fn=lambda remote, data, sha256: uploaded.append(remote)), 0)
        self.assertEqual(uploaded, [])
        self.assertEqual(self.journal, {})
        self.commit_mock.assert_not_called()

    def test_legacy_manifest_family_is_never_read_or_uploaded(self):
        legacy_paths = [
            self._write(".backup-manifest.json", b"not-json"),
            self._write(".backup-manifest.json.tmp", b"crash-left-temp"),
        ]
        self._write("orders/1.png", b"real-content")
        real_open = open
        uploaded = []

        def forbid_legacy_read(path, *args, **kwargs):
            if os.path.abspath(os.fspath(path)) in {
                    os.path.abspath(legacy_path) for legacy_path in legacy_paths}:
                raise AssertionError("legacy manifest must not be opened")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=forbid_legacy_read):
            self.assertEqual(data_backups.tick(
                subir_fn=lambda remote, data, sha256: uploaded.append(remote)), 1)
        self.assertEqual(uploaded, ["data/orders/1.png"])

    def test_unavailable_journal_prevents_a_false_successful_upload(self):
        self._write("orders/1.png", b"journal-down")
        self.map_mock.side_effect = OSError("synthetic database outage")
        uploaded = []

        self.assertEqual(data_backups.tick(
            subir_fn=lambda remote, data, sha256: uploaded.append(remote)), 0)
        self.assertEqual(uploaded, [])

    def test_no_credentials_keeps_thread_entrypoint_asleep(self):
        self._write("orders/1.png")
        with mock.patch.object(data_backups, "_env", return_value=""):
            self.assertEqual(data_backups.tick(), 0)
            self.assertIsNone(data_backups.start())

    def test_worker_mode_delegates_before_the_legacy_thread_checks_credentials(self):
        for mode in ("1", "1 "):
            with self.subTest(mode=mode), \
                 _env.patched_env({"TURNOS_QUEUE_WORKER": mode}, clear=False), \
                 mock.patch.object(data_backups, "configured", return_value=False), \
                 mock.patch.object(data_backups.threading, "Thread") as thread:
                self.assertIsNone(data_backups.start())
            thread.assert_not_called()

    def test_unsetting_worker_mode_restores_the_legacy_thread(self):
        # Rollback is intentionally simple: remove the topology mode and
        # the existing daemon-thread path starts again, with no worker row.
        with _env.patched_env({"TURNOS_QUEUE_WORKER": ""}, clear=False), \
             mock.patch.object(data_backups, "configured", return_value=True), \
             mock.patch.object(data_backups.threading, "Thread") as thread:
            instance = thread.return_value
            self.assertIs(data_backups.start(), instance)
        thread.assert_called_once()
        instance.start.assert_called_once()

    def test_test_b2_endpoint_is_inert_without_the_master_test_gate(self):
        with _env.patched_env({
                "TURNOS_TEST_SEAMS": "",
                "TURNOS_TEST_B2_API_URL": "http://127.0.0.1:1/should-not-win",
                "TURNOS_TEST_BACKUP_DATA_DIR": "/tmp/should-not-win",
                "TURNOS_TEST_BACKUP_HOLD_AFTER_ACCEPT_S": "999",
        }, clear=False):
            self.assertEqual(data_backups._b2_authorize_url(),
                             "https://api.backblazeb2.com/b2api/v2/b2_authorize_account")
            self.assertEqual(data_backups._data_dir(), data_backups.DATA_DIR)
            with mock.patch.object(data_backups.time, "sleep") as sleep:
                data_backups._hold_after_test_accept()
            sleep.assert_not_called()

    def test_one_connection_pool_shape_uses_the_lock_connection_for_journal(self):
        """The held session lock must not require a second pooled checkout."""
        self._write("orders/one.bin", b"one")
        calls = []

        class Cur:
            def execute(_self, sql, params=None):
                calls.append((sql, params))
            def __enter__(_self):
                return _self
            def __exit__(_self, *unused):
                return False

        class Conn:
            def __init__(_self):
                _self.cur = Cur()
                _self.commits = 0
                _self.closed = 0
            def cursor(_self):
                return _self.cur
            def commit(_self):
                _self.commits += 1
            def close(_self):
                _self.closed += 1

        conn = Conn()
        seen = []
        with mock.patch.object(data_backups, "_env", return_value="data/"), \
             mock.patch.object(data_backups.db, "_connect",
                               return_value=contextlib.nullcontext(conn)) as connect, \
             mock.patch.object(db_backup, "backup_journal_map",
                               side_effect=lambda *, conn=None: (seen.append(conn) or {})), \
             mock.patch.object(db_backup, "backup_journal_commit",
                               side_effect=lambda *a, conn=None, **kw: seen.append(conn)):
            self.assertEqual(data_backups.tick(
                subir_fn=lambda *unused: None, lock_sweep=True), 1)
        self.assertEqual(connect.call_count, 1, "TURNOS_DB_POOL=1 must not self-deadlock")
        self.assertEqual(seen, [conn, conn])
        self.assertEqual(conn.commits, 1)
        self.assertTrue(any("pg_advisory_lock" in sql for sql, _ in calls))
        self.assertTrue(any("pg_advisory_unlock" in sql for sql, _ in calls))

    def test_cold_config_checkout_finishes_before_the_sweep_lock(self):
        """A DB-backed prefix lookup must not nest inside the held lock."""
        self._write("orders/one.bin", b"one")
        active = 0
        max_active = 0

        class Cur:
            def execute(_self, _sql, _params=None):
                pass
            def __enter__(_self):
                return _self
            def __exit__(_self, *unused):
                return False

        class Conn:
            def __init__(_self):
                _self.cur = Cur()
            def cursor(_self):
                return _self.cur
            def commit(_self):
                pass

        class Checkout:
            def __init__(_self, conn):
                _self.conn = conn
            def __enter__(_self):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                return _self.conn
            def __exit__(_self, *unused):
                nonlocal active
                active -= 1
                return False

        config_conn = Conn()
        lock_conn = Conn()
        checkouts = iter((Checkout(config_conn), Checkout(lock_conn)))

        def cold_prefix(_key, _default=""):
            with data_backups.db._connect():
                pass
            return "data/"

        with mock.patch.object(data_backups.db, "_connect",
                               side_effect=lambda: next(checkouts)) as connect, \
             mock.patch.object(data_backups, "_env", side_effect=cold_prefix), \
             mock.patch.object(db_backup, "backup_journal_map", return_value={}), \
             mock.patch.object(db_backup, "backup_journal_commit"):
            self.assertEqual(data_backups.tick(
                subir_fn=lambda *unused: None, lock_sweep=True), 1)

        self.assertEqual(connect.call_count, 2)
        self.assertEqual(max_active, 1, "config and sweep checkouts must be sequential")
        self.assertEqual(active, 0)

    def test_lock_acquisition_failure_is_a_safe_zero_not_an_exception(self):
        self._write("orders/one.bin", b"one")
        with mock.patch.object(data_backups.db, "_connect", side_effect=OSError("db down")):
            self.assertEqual(data_backups.tick(
                subir_fn=lambda *unused: self.fail("transport must not run"),
                lock_sweep=True), 0)

    def test_unlock_failure_discards_connection_without_changing_tick_result(self):
        self._write("orders/one.bin", b"one")

        class Cur:
            def execute(_self, sql, params=None):
                if "pg_advisory_unlock" in sql:
                    raise OSError("synthetic unlock failure")
            def __enter__(_self):
                return _self
            def __exit__(_self, *unused):
                return False

        class Conn:
            def __init__(_self):
                _self.closed = 0
                _self.cur = Cur()
            def cursor(_self):
                return _self.cur
            def commit(_self):
                pass
            def close(_self):
                _self.closed += 1

        conn = Conn()
        with mock.patch.object(data_backups.db, "_connect",
                               return_value=contextlib.nullcontext(conn)), \
             mock.patch.object(db_backup, "backup_journal_map", return_value={}), \
             mock.patch.object(db_backup, "backup_journal_commit"):
            self.assertEqual(data_backups.tick(subir_fn=lambda *unused: None,
                                                lock_sweep=True), 1)
        self.assertEqual(conn.closed, 1, "a failed unlock must not return a lock holder to pool")

    def test_unlock_failure_returns_a_closed_raw_connection_to_the_real_pool_wrapper(self):
        """`_PooledConn` is the production return path, not nullcontext.

        The pool receives the raw connection only after `_sweep_lock` closed
        it, so psycopg_pool discards it instead of making a session advisory
        lock re-entrant for a later borrower.
        """
        self._write("orders/one.bin", b"one")

        class Cur:
            def execute(_self, sql, params=None):
                if "pg_advisory_unlock" in sql:
                    raise OSError("synthetic unlock failure")
            def __enter__(_self):
                return _self
            def __exit__(_self, *unused):
                return False

        class Raw:
            def __init__(_self):
                _self.closed = False
                _self.cur = Cur()
            def cursor(_self):
                return _self.cur
            def commit(_self):
                pass
            def close(_self):
                _self.closed = True
            def __exit__(_self, *unused):
                return False

        class Pool:
            def __init__(_self):
                _self.returned = []
            def putconn(_self, raw):
                self.assertTrue(raw.closed,
                                "the pool must receive a closed raw connection")
                _self.returned.append(raw)

        raw, pool = Raw(), Pool()
        wrapped = data_backups.db._PooledConn(pool, raw)
        with mock.patch.object(data_backups.db, "_connect", return_value=wrapped), \
             mock.patch.object(db_backup, "backup_journal_map", return_value={}), \
             mock.patch.object(db_backup, "backup_journal_commit"):
            self.assertEqual(data_backups.tick(subir_fn=lambda *unused: None,
                                                lock_sweep=True), 1)
        self.assertEqual(pool.returned, [raw])


if __name__ == "__main__":
    unittest.main()
