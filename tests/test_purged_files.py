"""Files the retention deleted stay deleted — in B2 and after a restore
(hospital schema review №11, 2026-10-24).

The referral-file retention removed the file from the data volume only; every
copy the B2 sweep had already uploaded stayed there forever, and a restore of
an old volume brought the file back. Now the retention writes a journal row
(`purged_files`) BEFORE it deletes, and:
  - the B2 sweep deletes every remote version of a journaled path (exact file
    ids, with tombstones) and never uploads one again;
  - `restore_version` refuses a journaled path;
  - the next retention pass removes a journaled file that came back.
The B2 transport and the version listing are injected; nothing leaves the box."""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

import _dbtest  # noqa: E402
from app.core import config, db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_backup  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
from app.ops import data_backups, data_backups_restore as restore  # noqa: E402

config.ENV_PATH = ""
_db = _dbtest.PerModuleDb("sana_turnos_test_purged_files", seed=lambda: db_booking.get_booking_request(-1))
HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _version(file_id, name, payload=b"x"):
    return {"fileId": file_id, "fileName": name, "action": "upload",
            "contentLength": len(payload), "contentSha1": hashlib.sha1(payload).hexdigest(),
            "uploadTimestamp": 1}


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            for table in ("purged_files", "backup_journal", "backup_versions", "backup_version_tombstones"):
                cur.execute(f"DELETE FROM {table}")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # the data volume: <tmp>/ordenes is booking_requests.REFERRALS_DIR
        self.data = self.tmp.name
        self.ordenes = os.path.join(self.data, "ordenes")
        os.makedirs(os.path.join(self.ordenes, "pending"))
        for target, attr, value in (
                (booking_requests, "REFERRALS_DIR", self.ordenes),
                (data_backups, "DATA_DIR", self.data)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def _write(self, rel, content=b"orden"):
        path = os.path.join(self.data, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return path

    @staticmethod
    def _upload(remote, data, sha256):
        return {"fileId": "up-" + hashlib.sha1(remote.encode()).hexdigest()[:8], "fileName": remote,
                "contentLength": len(data), "contentSha1": hashlib.sha1(data).hexdigest(),
                "action": "upload"}


class TestJournal(_Base):
    def test_recording_is_idempotent_and_lists_what_is_pending(self):
        db_backup.purged_file_record("ordenes/1.png", "retention")
        db_backup.purged_file_record("ordenes/1.png", "retention")
        db_backup.purged_file_record("ordenes/2.png", "retention")
        self.assertEqual(db_backup.purged_file_paths(), {"ordenes/1.png", "ordenes/2.png"})
        self.assertEqual(sorted(db_backup.purged_files_pending()), ["ordenes/1.png", "ordenes/2.png"])

    def test_remote_done_closes_the_row_and_forgets_the_backup_journal_entry(self):
        self._write("ordenes/1.png")
        data_backups.tick(subir_fn=self._upload)
        self.assertIn("ordenes/1.png", db_backup.backup_journal_map())
        db_backup.purged_file_record("ordenes/1.png", "retention")
        db_backup.purged_file_remote_done("ordenes/1.png")
        self.assertEqual(db_backup.purged_files_pending(), [])
        self.assertEqual(db_backup.purged_file_paths(), {"ordenes/1.png"}, "the journal row stays: it is the memory")
        self.assertNotIn("ordenes/1.png", db_backup.backup_journal_map())


class TestRetentionWritesTheJournalFirst(_Base):
    def _sweep(self, rows):
        with mock.patch.object(booking_requests.store, "referrals_to_purge", return_value=rows), \
                mock.patch.object(booking_requests.store, "clear_referral_path") as clear:
            return booking_referrals.sweep_referrals(retention_days=30), clear

    def test_a_purged_file_is_journaled_under_its_data_relative_path(self):
        path = self._write("ordenes/7.png")
        n, clear = self._sweep([{"id": 7, "orden_path": path}])
        self.assertEqual(n, 1)
        self.assertFalse(os.path.exists(path))
        clear.assert_called_once_with(7)
        self.assertEqual(db_backup.purged_file_paths(), {"ordenes/7.png"})

    def test_a_missing_file_is_journaled_too(self):
        n, _ = self._sweep([{"id": 8, "orden_path": os.path.join(self.ordenes, "8.pdf")}])
        self.assertEqual(n, 1)
        self.assertEqual(db_backup.purged_file_paths(), {"ordenes/8.pdf"})

    def test_a_pointer_outside_the_current_volume_is_journaled_by_file_name(self):
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        path = os.path.join(elsewhere.name, "13.png")
        with open(path, "wb") as f:
            f.write(b"orden")
        n, _ = self._sweep([{"id": 13, "orden_path": path}])
        self.assertEqual(n, 1)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(db_backup.purged_file_paths(), {"ordenes/13.png"})

    def test_when_the_journal_cannot_be_written_the_file_stays(self):
        path = self._write("ordenes/9.png")
        with mock.patch.object(booking_requests.store, "purged_file_record", side_effect=RuntimeError("db")):
            n, clear = self._sweep([{"id": 9, "orden_path": path}])
        self.assertEqual(n, 0)
        self.assertTrue(os.path.exists(path), "no intent on record -> nothing deleted")
        clear.assert_not_called()

    def test_retention_zero_still_means_do_not_clean(self):
        path = self._write("ordenes/10.png")
        with mock.patch.object(booking_requests.store, "referrals_to_purge",
                               return_value=[{"id": 10, "orden_path": path}]) as lst:
            self.assertEqual(booking_referrals.sweep_referrals(retention_days=0), 0)
        lst.assert_not_called()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(db_backup.purged_file_paths(), set())

    def test_a_journaled_file_that_came_back_is_removed_on_the_next_pass(self):
        db_backup.purged_file_record("ordenes/11.png", "retention")
        back = self._write("ordenes/11.png")          # an old volume was restored
        alive = self._write("ordenes/12.png")         # never journaled: untouched
        self.assertEqual(booking_referrals.reapply_purged_files(), 1)
        self.assertFalse(os.path.exists(back))
        self.assertTrue(os.path.exists(alive))
        self.assertEqual(booking_referrals.reapply_purged_files(), 0)


class TestB2SweepDeletesPurgedCopies(_Base):
    def _ops(self, versions, delete_calls, fail=()):
        def list_fn(prefix):
            return [v for v in versions if v["fileName"].startswith(prefix)]

        def delete_fn(remote_name, file_id):
            if file_id in fail:
                raise OSError("b2 down")
            delete_calls.append((remote_name, file_id))
        return list_fn, delete_fn

    def test_every_version_of_the_journaled_path_goes_and_nothing_else(self):
        self._write("ordenes/5.png")
        data_backups.tick(subir_fn=self._upload)
        os.remove(os.path.join(self.ordenes, "5.png"))
        db_backup.purged_file_record("ordenes/5.png", "retention")
        versions = [_version("v1", "data/ordenes/5.png"), _version("v2", "data/ordenes/5.png"),
                    _version("v3", "data/ordenes/55.png"), _version("v4", "data/ordenes/6.png")]
        calls = []
        data_backups.tick(subir_fn=self._upload, purge_ops=self._ops(versions, calls))
        self.assertEqual(sorted(calls), [("data/ordenes/5.png", "v1"), ("data/ordenes/5.png", "v2")])
        self.assertEqual(set(db_backup.backup_tombstone_rows()), {"v1", "v2"})
        self.assertEqual(db_backup.purged_files_pending(), [])
        self.assertNotIn("ordenes/5.png", db_backup.backup_journal_map())
        # converged: a second sweep deletes nothing more
        again = []
        data_backups.tick(subir_fn=self._upload, purge_ops=self._ops(versions, again))
        self.assertEqual(again, [])

    def test_a_failed_delete_stays_pending_and_is_retried(self):
        db_backup.purged_file_record("ordenes/5.png", "retention")
        versions = [_version("v1", "data/ordenes/5.png"), _version("v2", "data/ordenes/5.png")]
        calls = []
        data_backups.tick(subir_fn=self._upload, purge_ops=self._ops(versions, calls, fail={"v2"}))
        self.assertEqual(db_backup.purged_files_pending(), ["ordenes/5.png"])
        data_backups.tick(subir_fn=self._upload, purge_ops=self._ops(versions, calls))
        self.assertEqual(db_backup.purged_files_pending(), [])
        self.assertIn(("data/ordenes/5.png", "v2"), calls)

    def test_a_version_already_gone_counts_as_deleted(self):
        db_backup.purged_file_record("ordenes/5.png", "retention")

        def delete_fn(remote_name, file_id):
            raise restore.RemoteAlreadyAbsent(file_id)
        data_backups.tick(subir_fn=self._upload, purge_ops=(
            lambda prefix: [_version("v1", "data/ordenes/5.png")], delete_fn))
        self.assertEqual(db_backup.purged_files_pending(), [])
        self.assertEqual(set(db_backup.backup_tombstone_rows()), {"v1"})

    def test_a_purged_path_is_never_uploaded_again(self):
        self._write("ordenes/5.png")                 # came back from an old restore
        db_backup.purged_file_record("ordenes/5.png", "retention")
        uploaded = []
        data_backups.tick(subir_fn=lambda r, d, s: uploaded.append(r) or self._upload(r, d, s),
                          purge_ops=self._ops([], []))
        self.assertEqual(uploaded, [])

    def test_without_purge_ops_an_injected_sweep_never_touches_the_remote(self):
        db_backup.purged_file_record("ordenes/5.png", "retention")
        calls = []
        data_backups.tick(subir_fn=self._upload)
        self.assertEqual(calls, [])
        self.assertEqual(db_backup.purged_files_pending(), ["ordenes/5.png"])

    def test_an_expired_pending_upload_loses_its_remote_copies_too(self):
        self._write("ordenes/pending/tok1")
        self._write("ordenes/pending/tok2")
        data_backups.tick(subir_fn=self._upload)
        os.remove(os.path.join(self.ordenes, "pending", "tok1"))   # the 48 h cleanup
        versions = [_version("a", "data/ordenes/pending/tok1"), _version("b", "data/ordenes/pending/tok2")]
        calls = []
        data_backups.tick(subir_fn=self._upload, purge_ops=self._ops(versions, calls))
        self.assertEqual(calls, [("data/ordenes/pending/tok1", "a")])
        self.assertNotIn("ordenes/pending/tok1", db_backup.backup_journal_map())
        self.assertIn("ordenes/pending/tok2", db_backup.backup_journal_map())

    def test_pending_rule_does_nothing_when_the_volume_is_not_mounted(self):
        self._write("ordenes/pending/tok1")
        data_backups.tick(subir_fn=self._upload)
        import shutil
        shutil.rmtree(self.ordenes)                   # a volume that is not there
        calls = []
        data_backups.tick(subir_fn=self._upload, purge_ops=self._ops(
            [_version("a", "data/ordenes/pending/tok1")], calls))
        self.assertEqual(calls, [])
        self.assertIn("ordenes/pending/tok1", db_backup.backup_journal_map())


def _http_error(status, code="unauthorized"):
    return urllib.error.HTTPError(
        "https://api.backblazeb2.com/b2api/v2/b2_delete_file_version", status, "refused", {},
        io.BytesIO(json.dumps({"code": code, "status": status, "message": "secret detail"}).encode()))


class TestB2KeyWithoutDeleteFiles(_Base):
    """pinamar, 2026-10-26: the application key could list, read and write but not delete, so
    every delete came back 401 and the sweep went on as if nothing was wrong (test doubles
    never refuse). The refusal must be visible, the paths must stay pending, and the next
    sweep with a good key must pick them up."""

    def setUp(self):
        super().setUp()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM alert_claims")
            cur.execute("DELETE FROM backup_purge_state")
        for path in ("ordenes/5.png", "ordenes/6.png"):
            db_backup.purged_file_record(path, "retention")
        self.versions = [_version("v5a", "data/ordenes/5.png"), _version("v5b", "data/ordenes/5.png"),
                         _version("v6", "data/ordenes/6.png")]
        self.deleted = []
        self.err = io.StringIO()
        p = mock.patch.object(sys, "stderr", self.err)
        p.start()
        self.addCleanup(p.stop)

    def _list(self, prefix):
        return [v for v in self.versions if v["fileName"].startswith(prefix)]

    def _real_delete(self, status, code="unauthorized"):
        """The real `delete_remote_version`, with B2's HTTP answer faked."""
        def urlopen(request, timeout):
            if status == 200:
                body = json.loads(request.data.decode())
                self.deleted.append((body["fileName"], body["fileId"]))

                class Response:
                    def __enter__(self_):
                        return self_

                    def __exit__(self_, *_args):
                        return False

                    def read(self_):
                        return json.dumps(body).encode()
                return Response()
            raise _http_error(status, code)
        stack = [mock.patch.object(data_backups, "configured", return_value=True),
                 mock.patch.object(data_backups, "_authorize", return_value={
                     "apiUrl": "http://fake-b2", "authorizationToken": "tok"}),
                 mock.patch.object(restore.urllib.request, "urlopen", side_effect=urlopen)]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)
        return restore.delete_remote_version

    def _sweep(self, delete_fn):
        with mock.patch("app.auth.admin_auth.owner_email", return_value="owner@example.org"), \
                mock.patch("app.notifications.mail.send", return_value=True) as send:
            data_backups.tick(subir_fn=self._upload, purge_ops=(self._list, delete_fn))
        return send

    def test_the_transport_names_a_capability_refusal(self):
        delete_fn = self._real_delete(401)
        with self.assertRaises(restore.RemoteDeleteDenied) as cm:
            delete_fn("data/ordenes/5.png", "v5a")
        self.assertEqual(cm.exception.code, 401)
        self.assertNotIn("secret detail", str(cm.exception))

    def test_a_rejected_token_is_not_called_a_missing_capability(self):
        delete_fn = self._real_delete(401, "expired_auth_token")
        with self.assertRaises(urllib.error.HTTPError):
            delete_fn("data/ordenes/5.png", "v5a")

    def test_a_refused_sweep_does_not_fail_keeps_the_paths_and_says_so(self):
        send = self._sweep(self._real_delete(401))
        self.assertEqual(sorted(db_backup.purged_files_pending()), ["ordenes/5.png", "ordenes/6.png"])
        self.assertEqual(set(db_backup.backup_tombstone_rows()), set())
        self.assertEqual(data_backups.last_purge(), {"closed": 0, "denied": 2, "failed": 0})
        log = self.err.getvalue()
        self.assertIn("ERROR", log)
        self.assertIn("deleteFiles", log)
        self.assertIn("2 purged file(s)", log)
        self.assertNotIn("secret detail", log)
        send.assert_called_once()
        self.assertIn("deleteFiles", send.call_args.args[2])

    def test_a_403_is_a_refusal_too(self):
        self._sweep(self._real_delete(403, "access_denied"))
        self.assertEqual(data_backups.last_purge()["denied"], 2)

    def test_the_refusal_stops_the_pass_after_the_first_attempt(self):
        calls = []

        def delete_fn(remote_name, file_id):
            calls.append(file_id)
            raise restore.RemoteDeleteDenied(401)
        self._sweep(delete_fn)
        self.assertEqual(len(calls), 1, "a key-wide refusal is not retried path by path")
        self.assertEqual(data_backups.last_purge()["denied"], 2)

    def test_the_alert_goes_out_once_a_day_however_many_sweeps_refuse(self):
        delete_fn = self._real_delete(401)
        first = self._sweep(delete_fn)
        second = self._sweep(delete_fn)
        first.assert_called_once()
        second.assert_not_called()
        self.assertEqual(data_backups.last_purge()["denied"], 2, "the log and the summary repeat every sweep")

    def test_the_next_sweep_with_a_good_key_picks_the_paths_up(self):
        self._sweep(self._real_delete(401))
        self.assertEqual(len(db_backup.purged_files_pending()), 2)
        self.err.truncate(0)
        send = self._sweep(self._real_delete(200))
        self.assertEqual(sorted(self.deleted), [("data/ordenes/5.png", "v5a"), ("data/ordenes/5.png", "v5b"),
                                                ("data/ordenes/6.png", "v6")])
        self.assertEqual(db_backup.purged_files_pending(), [])
        self.assertEqual(set(db_backup.backup_tombstone_rows()), {"v5a", "v5b", "v6"})
        self.assertEqual(data_backups.last_purge(), {"closed": 2, "denied": 0, "failed": 0})
        send.assert_not_called()
        self.assertNotIn("ERROR", self.err.getvalue())

    def _b2_env(self):
        _env.set_env(self, TURNOS_B2_KEY_ID="k", TURNOS_B2_APP_KEY="a", TURNOS_B2_BUCKET_ID="b")

    def _panel(self):
        from app.admin_panel import system_status
        cluster = system_status._cluster_b2()
        return cluster, {i["label"]: i for i in cluster["items"]}

    def test_a_refusal_shows_in_sistema_and_a_good_pass_clears_it(self):
        self._b2_env()
        self._sweep(self._real_delete(401))
        state = db_backup.backup_purge_state()
        self.assertEqual((state["denied"], state["closed"], state["pending"]), (2, 0, 2))
        self.assertIsNone(state["last_success_at"], "a refused pass is not a success")
        cluster, items = self._panel()
        warn = items["Permiso de borrado"]
        self.assertEqual(warn["estado"], "warn")
        self.assertEqual(cluster["estado"], "warn")
        self.assertIn("deleteFiles", warn["detalle"])
        self.assertIn("2 archivos que la retención ya borró siguen guardados", warn["detalle"])
        self.assertEqual(items["Archivos por borrar"]["valor"], "2")
        self.assertEqual(items["Último borrado limpio"]["valor"], "Todavía no")

        self._sweep(self._real_delete(200))
        state = db_backup.backup_purge_state()
        self.assertEqual((state["denied"], state["closed"], state["pending"]), (0, 2, 0))
        self.assertIsNotNone(state["last_success_at"])
        cluster, items = self._panel()
        self.assertNotIn("Permiso de borrado", items)
        self.assertEqual(cluster["estado"], "ok")
        self.assertEqual(items["Archivos por borrar"]["valor"], "0")
        self.assertNotEqual(items["Último borrado limpio"]["valor"], "Todavía no")

    def test_a_pass_with_nothing_to_purge_still_counts_as_the_last_clean_pass(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM purged_files")
        self._sweep(self._real_delete(200))
        state = db_backup.backup_purge_state()
        self.assertEqual((state["denied"], state["pending"]), (0, 0))
        self.assertIsNotNone(state["last_success_at"])

    def test_a_failed_pass_keeps_the_last_clean_time(self):
        self._sweep(self._real_delete(200))
        clean = db_backup.backup_purge_state()["last_success_at"]
        db_backup.purged_file_record("ordenes/7.png", "retention")
        self.versions.append(_version("v7", "data/ordenes/7.png"))

        def delete_fn(remote_name, file_id):
            raise OSError("b2 down")
        self._sweep(delete_fn)
        state = db_backup.backup_purge_state()
        self.assertEqual((state["failed"], state["denied"]), (1, 0))
        self.assertEqual(state["last_success_at"], clean)

    def test_other_failures_are_counted_apart_from_a_refusal(self):
        def delete_fn(remote_name, file_id):
            raise OSError("b2 down")
        self._sweep(delete_fn)
        self.assertEqual(data_backups.last_purge(), {"closed": 0, "denied": 0, "failed": 2})
        self.assertNotIn("deleteFiles", self.err.getvalue())


class TestRestoreRefusesPurgedPaths(_Base):
    def _version_record(self, name, payload=b"orden"):
        return {"file_id": "v1", "remote_name": name, "sha256": hashlib.sha256(payload).hexdigest(),
                "sha1": hashlib.sha1(payload).hexdigest(), "size": len(payload), "action": "upload"}

    def test_a_purged_path_is_not_restored_and_nothing_is_downloaded(self):
        db_backup.purged_file_record("ordenes/5.png", "retention")
        fetch = mock.Mock()
        dest, scratch = os.path.join(self.tmp.name, "dest"), os.path.join(self.tmp.name, "scratch")
        with self.assertRaises(restore.BackupVersionError) as cm:
            restore.restore_version(self._version_record("data/ordenes/5.png"), dest, scratch, fetch)
        self.assertIn("purged", str(cm.exception))
        fetch.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(dest, "ordenes", "5.png")))

    def test_an_unlisted_path_restores_as_before(self):
        payload = b"orden"
        fetch = lambda fid: ({"fileId": fid, "fileName": "data/ordenes/6.png", "contentLength": len(payload),
                              "contentSha1": hashlib.sha1(payload).hexdigest()}, payload)
        dest, scratch = os.path.join(self.tmp.name, "dest"), os.path.join(self.tmp.name, "scratch")
        target = restore.restore_version(self._version_record("data/ordenes/6.png"), dest, scratch, fetch)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), payload)


if __name__ == "__main__":
    unittest.main()
