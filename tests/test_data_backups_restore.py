"""Adversarial proof for Tier 4's operator-only B2 restore/retention slice.

All bytes are generated non-PHI fixtures.  The catalog/fetch/delete paths are
injected fake-store functions; the one outbound-kill case points only to an
unreachable loopback port and proves no backup ledger mutation follows it.
``_dbtest`` supplies a disposable Postgres database for immutable-ledger and
rollback boundaries — no deployment database or B2 credential is used.
"""

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config, db, outbound_guard, queue_infra  # noqa: E402
from app.core import db_backup  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.ops import data_backups, data_backups_restore as restore  # noqa: E402

import _dbtest  # noqa: E402
import _env  # noqa: E402

config.ENV_PATH = ""
_db = _dbtest.PerModuleDb("sana_turnos_test_backup_restore")
HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _receipt(file_id, remote_name, payload, path="orders/a.bin", timestamp=0):
    return {
        "file_id": file_id, "remote_name": remote_name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "sha1": hashlib.sha1(payload).hexdigest(), "size": len(payload),
        "journal_path": path, "uploadTimestamp": timestamp,
    }


def _remote(receipt, *, action="upload", timestamp=0):
    return {
        "fileId": receipt["file_id"], "fileName": receipt["remote_name"],
        "contentLength": receipt["size"], "contentSha1": receipt["sha1"],
        "action": action, "uploadTimestamp": timestamp,
    }


class TestVersionCatalog(unittest.TestCase):
    def test_list_uses_both_b2_pagination_cursors_at_same_name_boundary(self):
        calls = []
        pages = {
            (None, None): {
                "files": [{"fileId": "v3", "fileName": "data/a.bin"}],
                "nextFileName": "data/a.bin", "nextFileId": "v2",
            },
            ("data/a.bin", "v2"): {
                "files": [{"fileId": "v2", "fileName": "data/a.bin"}],
                "nextFileName": None, "nextFileId": None,
            },
        }

        def fetch(name, file_id, prefix):
            calls.append((name, file_id, prefix))
            return pages[(name, file_id)]

        records = restore.list_remote_versions(prefix="data/", page_fetch=fetch)
        self.assertEqual([item["fileId"] for item in records], ["v3", "v2"])
        self.assertEqual(calls, [(None, None, "data/"), ("data/a.bin", "v2", "data/")])

    def test_pagination_rejects_one_cursor_or_a_stalled_pair(self):
        with self.assertRaises(restore.BackupVersionError):
            restore.list_remote_versions(page_fetch=lambda *_: {
                "files": [], "nextFileName": "data/a", "nextFileId": None})
        pages = [
            {"files": [], "nextFileName": "data/a", "nextFileId": "id"},
            {"files": [], "nextFileName": "data/a", "nextFileId": "id"},
        ]
        with self.assertRaises(restore.BackupVersionError):
            restore.list_remote_versions(page_fetch=lambda *_: pages.pop(0))

    def test_repeated_name_keeps_current_and_newest_prior_only(self):
        old = _receipt("old", "data/orders/a.bin", b"old", timestamp=10)
        prior = _receipt("prior", "data/orders/a.bin", b"prior", timestamp=20)
        current = _receipt("current", "data/orders/a.bin", b"current", timestamp=30)
        ledger = {item["file_id"]: item for item in (old, prior, current)}
        journal = {"orders/a.bin": {"remote_name": current["remote_name"],
                                     "remote_file_id": "current"}}
        remote = [_remote(old, timestamp=10), _remote(prior, timestamp=20),
                  _remote(current, timestamp=30)]
        reconciliation = restore.reconcile(remote, journal, ledger, {}, prefix="data/")
        plan = restore.retention_plan(reconciliation)
        self.assertEqual(plan["blockers"], {})
        self.assertEqual(plan["protected"], {"current": "current", "prior": "newest prior verified"})
        self.assertEqual(plan["candidates"], [{"file_id": "old", "remote_name": "data/orders/a.bin",
                                                  "journal_path": "orders/a.bin"}])
        self.assertTrue(plan["dry_run"])

    def test_hide_orphan_legacy_and_changed_prefix_are_global_retention_blockers(self):
        current = _receipt("current", "data/orders/a.bin", b"current", timestamp=30)
        journal = {"orders/a.bin": {"remote_name": current["remote_name"],
                                     "remote_file_id": "current"},
                   "old.bin": {"remote_name": "data/old.bin"}}
        base_remote = [_remote(current, timestamp=30)]
        cases = [
            base_remote + [{"fileId": "hide", "fileName": "data/orders/a.bin", "action": "hide"}],
            base_remote + [{"fileId": "orphan", "fileName": "data/orphan.bin", "action": "upload",
                            "contentLength": 1, "contentSha1": "0" * 40}],
            base_remote,
            [_remote(current, timestamp=30)],
        ]
        prefixes = ["data/", "data/", "data/", "changed/"]
        for remote, prefix in zip(cases, prefixes):
            with self.subTest(prefix=prefix, records=len(remote)):
                reconciliation = restore.reconcile(remote, journal, {"current": current}, {}, prefix=prefix)
                self.assertTrue(restore.retention_plan(reconciliation)["blockers"])
                self.assertEqual(restore.retention_plan(reconciliation)["candidates"], [])

    def test_tombstoned_version_reappearing_remotely_is_an_anomaly_not_a_candidate(self):
        item = _receipt("old", "data/orders/a.bin", b"old", timestamp=10)
        reconciliation = restore.reconcile(
            [_remote(item, timestamp=10)], {}, {"old": item},
            {"old": {"remote_name": "data/orders/a.bin"}}, prefix="data/")
        self.assertTrue(reconciliation["unknown"])
        self.assertEqual(restore.retention_plan(reconciliation)["candidates"], [])


class TestExactRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="turnos-restore-proof-")
        self.dest = os.path.join(self.tmp.name, "destination")
        self.scratch = os.path.join(self.tmp.name, "scratch")
        self.payload = b"generated non-phi restore fixture"
        self.version = _receipt("exact-v1", "data/orders/a.bin", self.payload)

    def tearDown(self):
        self.tmp.cleanup()

    def _fetch(self, file_id):
        self.assertEqual(file_id, "exact-v1")
        return ({"fileId": "exact-v1", "fileName": "data/orders/a.bin",
                 "contentLength": len(self.payload),
                 "contentSha1": hashlib.sha1(self.payload).hexdigest()}, self.payload)

    def test_fake_store_operational_restore_drill_is_exact_and_atomic(self):
        target = restore.restore_version(self.version, self.dest, self.scratch, self._fetch)
        self.assertEqual(target, os.path.join(self.dest, "orders", "a.bin"))
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), self.payload)
        self.assertFalse(any(name.startswith(".restore") for name in os.listdir(self.scratch)))

    def test_wrong_bytes_or_remote_metadata_never_places_a_file(self):
        bads = [
            ({"fileId": "wrong", "fileName": "data/orders/a.bin", "contentLength": len(self.payload),
              "contentSha1": hashlib.sha1(self.payload).hexdigest()}, self.payload),
            ({"fileId": "exact-v1", "fileName": "data/orders/a.bin", "contentLength": len(self.payload),
              "contentSha1": hashlib.sha1(self.payload).hexdigest()}, b"wrong bytes"),
        ]
        for response in bads:
            with self.subTest(response=response[0]["fileId"]):
                with self.assertRaises(restore.BackupVersionError):
                    restore.restore_version(self.version, self.dest, self.scratch,
                                            lambda _id, value=response: value)
                self.assertFalse(os.path.exists(os.path.join(self.dest, "orders", "a.bin")))

    def test_unsafe_names_symlink_and_collision_are_rejected_without_overwrite(self):
        unsafe = ["/data/a.bin", "data/../a.bin", "data/a\\b", "data/a\x00b",
                  "other/a.bin", "data/.backup-manifest.json"]
        for name in unsafe:
            with self.subTest(name=repr(name)):
                version = dict(self.version, remote_name=name)
                with self.assertRaises(restore.BackupVersionError):
                    restore.restore_version(version, self.dest, self.scratch, self._fetch)

        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(outside)
        os.makedirs(self.dest)
        os.symlink(outside, os.path.join(self.dest, "orders"))
        with self.assertRaises(restore.BackupVersionError):
            restore.restore_version(self.version, self.dest, self.scratch, self._fetch)
        self.assertEqual(os.listdir(outside), [])
        os.unlink(os.path.join(self.dest, "orders"))
        os.makedirs(os.path.join(self.dest, "orders"))
        target = os.path.join(self.dest, "orders", "a.bin")
        with open(target, "wb") as fh:
            fh.write(b"do not replace")
        with self.assertRaises(restore.BackupVersionError):
            restore.restore_version(self.version, self.dest, self.scratch, self._fetch)
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"do not replace")

    def test_scratch_and_destination_must_be_disjoint_before_fetch_or_output(self):
        calls = []
        for scratch, destination in ((self.dest, self.dest),
                                     (os.path.join(self.dest, "scratch"), self.dest),
                                     (self.scratch, os.path.join(self.scratch, "destination"))):
            with self.subTest(scratch=scratch, destination=destination):
                with self.assertRaises(restore.BackupVersionError):
                    restore.restore_version(self.version, destination, scratch,
                                            lambda _id: calls.append(_id))
        self.assertEqual(calls, [])

    def test_missing_child_below_a_symlink_root_is_never_created_outside_destination(self):
        outside = os.path.join(self.tmp.name, "outside-root")
        linked = os.path.join(self.tmp.name, "linked-root")
        os.makedirs(outside)
        os.symlink(outside, linked)
        with self.assertRaises(restore.BackupVersionError):
            restore.restore_version(self.version, os.path.join(linked, "new-child"),
                                    self.scratch, self._fetch)
        self.assertFalse(os.path.exists(os.path.join(outside, "new-child")))


class TestRetentionExecution(unittest.TestCase):
    def test_dry_run_never_calls_delete_or_tombstone(self):
        plan = {"blockers": {}, "candidates": [{"file_id": "id", "remote_name": "data/a.bin"}]}
        calls = []
        outcome = restore.apply_retention(plan, lambda *args: calls.append(args),
                                          lambda *args: calls.append(("tombstone",) + args))
        self.assertEqual(outcome, [{"file_id": "id", "status": "would-delete"}])
        self.assertEqual(calls, [])

    def test_delete_success_db_failure_converges_on_exact_retry(self):
        plan = {"blockers": {}, "candidates": [{"file_id": "id", "remote_name": "data/a.bin"}]}
        remote = {"present": True}
        tombstones = []

        def delete(name, file_id):
            self.assertEqual((name, file_id), ("data/a.bin", "id"))
            if not remote["present"]:
                raise restore.RemoteAlreadyAbsent(file_id)
            remote["present"] = False

        def broken_tombstone(*_args):
            raise OSError("simulated DB crash after delete")

        with self.assertRaises(OSError):
            restore.apply_retention(plan, delete, broken_tombstone, dry_run=False)
        outcome = restore.apply_retention(plan, delete,
                                          lambda *args: tombstones.append(args), dry_run=False)
        self.assertEqual(outcome, [{"file_id": "id", "status": "already-absent"}])
        self.assertEqual(tombstones, [("id", "data/a.bin")])


class TestOperatorTransportBoundary(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_OUTBOUND_KILL=None)

    def test_exact_id_download_uses_authorization_download_url_and_preserves_headers(self):
        captured = {}

        class Response:
            headers = {"X-Bz-File-Id": "exact", "X-Bz-File-Name": "data/a.bin",
                       "X-Bz-Content-Sha1": "0" * 40, "Content-Length": "0"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b""

        def open_fake(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = request.data
            captured["auth"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return Response()

        with mock.patch.object(data_backups, "configured", return_value=True), \
             mock.patch.object(data_backups, "_authorize", return_value={
                 "downloadUrl": "http://fake-download/", "authorizationToken": "tok"}), \
             mock.patch.object(restore.urllib.request, "urlopen", side_effect=open_fake):
            headers, payload = restore.download_remote_version("exact")
        self.assertEqual(captured["url"], "http://fake-download/b2api/v2/b2_download_file_by_id")
        self.assertEqual(captured["body"], b'{"fileId": "exact"}')
        self.assertEqual(captured["auth"], "tok")
        self.assertEqual(captured["timeout"], 60)
        self.assertEqual(headers["x-bz-file-id"], "exact")
        self.assertEqual(payload, b"")

    def test_kill_switch_refuses_direct_operator_paths_before_fetch_delete_or_tombstone(self):
        _env.set_env(self, TURNOS_OUTBOUND_KILL="1")
        calls = []
        with mock.patch.object(data_backups, "_authorize", side_effect=AssertionError("must not authorize")):
            with self.assertRaises(restore.BackupVersionError):
                restore.delete_remote_version("data/a.bin", "id")
            with self.assertRaises(restore.BackupVersionError):
                restore.download_remote_version("id")
        plan = {"blockers": {}, "candidates": [{"file_id": "id", "remote_name": "data/a.bin"}]}
        with self.assertRaises(restore.BackupVersionError):
            restore.apply_retention(plan, lambda *args: calls.append(args),
                                    lambda *args: calls.append(("tombstone",) + args), dry_run=False)
        tmp = tempfile.TemporaryDirectory(prefix="turnos-kill-direct-proof-")
        try:
            version = _receipt("id", "data/a.bin", b"x")
            with self.assertRaises(restore.BackupVersionError):
                restore.restore_version(version, os.path.join(tmp.name, "dest"),
                                        os.path.join(tmp.name, "scratch"),
                                        lambda *args: calls.append(("fetch",) + args))
            self.assertFalse(os.path.exists(os.path.join(tmp.name, "dest")))
        finally:
            tmp.cleanup()
        self.assertEqual(calls, [])


class TestImmutableLedgerDatabase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "no disposable Postgres")
        db.close_pools()
        db_schema._ensured.clear()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE backup_version_tombstones, backup_versions, backup_journal CASCADE")

    def _commit(self, path, payload, file_id, remote_name):
        receipt = _receipt(file_id, remote_name, payload, path)
        with db._connect() as conn:
            db_backup.backup_journal_commit(path, receipt["sha256"], receipt["size"], 1,
                                     remote_name, conn=conn, receipt=receipt)
            conn.commit()

    def test_receipt_conflict_rolls_back_both_current_journal_and_ledger(self):
        self._commit("orders/old.bin", b"old", "same-id", "data/orders/old.bin")
        conflicting = _receipt("same-id", "data/orders/new.bin", b"new", "orders/new.bin")
        with db._connect() as conn:
            with self.assertRaises(ValueError):
                db_backup.backup_journal_commit("orders/new.bin", conflicting["sha256"],
                                         conflicting["size"], 2,
                                         conflicting["remote_name"], conn=conn,
                                         receipt=conflicting)
            conn.commit()
        journal = db_backup.backup_journal_map()
        ledger = db_backup.backup_version_rows()
        self.assertEqual(set(journal), {"orders/old.bin"})
        self.assertEqual(set(ledger), {"same-id"})
        self.assertEqual(ledger["same-id"]["remote_name"], "data/orders/old.bin")

    def test_outbound_kill_blocks_b2_before_any_journal_or_receipt_mutation(self):
        tmp = tempfile.TemporaryDirectory(prefix="turnos-killed-restore-proof-")
        self.addCleanup(tmp.cleanup)  # registered first: runs last, after the guard and the env
        with open(os.path.join(tmp.name, "fixture.bin"), "wb") as fh:
            fh.write(b"non-phi")
        _env.set_env(
            self, TURNOS_TEST_SEAMS="1", TURNOS_TEST_BACKUP_DATA_DIR=tmp.name,
            TURNOS_TEST_B2_API_URL="http://127.0.0.1:9",
            TURNOS_B2_KEY_ID="test", TURNOS_B2_APP_KEY="test",
            TURNOS_B2_BUCKET_ID="test", TURNOS_B2_PREFIX="data/")
        queue_infra.ensure_schema()
        data_backups._upload_b2.__defaults__[0].clear()
        self.addCleanup(outbound_guard.disarm)
        outbound_guard.arm()
        self.assertEqual(data_backups.tick(lock_sweep=True), 0)
        self.assertEqual(db_backup.backup_journal_map(), {})
        self.assertEqual(db_backup.backup_version_rows(), {})

    def test_armed_subprocess_operator_kill_reaches_no_fake_b2_and_mutates_no_ledger(self):
        """Direct recovery tooling is safe even outside worker.py's startup.

        The child explicitly arms the process guard, then calls the production
        exact-id download primitive against a local fake B2 authorization
        endpoint.  The restore module's own preflight must stop before either
        its HTTP request or any local backup ledger/tombstone mutation.
        """
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                hits.append(self.path)
                self.send_response(500)
                self.end_headers()

            def do_POST(self):
                hits.append(self.path)
                self.send_response(500)
                self.end_headers()

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = dict(os.environ)
        env.update({
            "TURNOS_ENV_FILE": os.devnull, "TURNOS_OUTBOUND_KILL": "1",
            "TURNOS_TEST_SEAMS": "1",
            "TURNOS_TEST_B2_API_URL": "http://127.0.0.1:%d" % server.server_address[1],
            "TURNOS_B2_KEY_ID": "test", "TURNOS_B2_APP_KEY": "test",
            "TURNOS_B2_BUCKET_ID": "test", "TURNOS_B2_PREFIX": "data/",
            "DATABASE_URL": os.environ["DATABASE_URL"],
        })
        script = """
from app.core import outbound_guard
from app.ops import data_backups_restore as restore
outbound_guard.arm()
try:
    restore.download_remote_version('proof-id')
except restore.BackupVersionError:
    raise SystemExit(0)
raise SystemExit(7)
"""
        try:
            proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=20)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(hits, [])
        self.assertEqual(db_backup.backup_journal_map(), {})
        self.assertEqual(db_backup.backup_version_rows(), {})
        self.assertEqual(db_backup.backup_tombstone_rows(), {})


if __name__ == "__main__":
    unittest.main()
