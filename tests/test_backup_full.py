import contextlib
import datetime as dt
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core import backup_settings as settings, backup_crypto as crypto
from app.ops import backup_full as b
import _env  # noqa: E402


class MemoryStore:
    def __init__(self):
        self.rows = []
    @contextlib.contextmanager
    def lock(self):
        self.conn = self
        try:
            yield self
        finally:
            self.conn = None
    def save(self, row):
        for i, old in enumerate(self.rows):
            if old["id"] == row["id"]:
                self.rows[i] = dict(row)
                return
        self.rows.append(dict(row))
    def list(self):
        return sorted(self.rows, key=lambda r: r["started_at"], reverse=True)


class FakeCloud:
    def __init__(self):
        self.objects, self.deleted = {}, []
    def upload(self, name, path):
        self.objects[name] = Path(path).read_bytes()
        return "fixture-etag"
    def head(self, name):
        return {"size": len(self.objects[name]), "etag": "fixture-etag"}
    def download(self, name, target):
        Path(target).write_bytes(self.objects[name])
    def delete(self, name):
        self.deleted.append(name)
        del self.objects[name]
    def cleanup_incomplete(self, *args, **kw):
        pass


class FullTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "synthetic.txt").write_text("fixture")
        self.env = self.root / "turnos.env"
        self.env.write_text("FIXTURE=1\n")
        self.cfg = dict(settings.DEFAULTS, TURNOS_BACKUP_ENABLED="1", TURNOS_BACKUP_S3_ENDPOINT="https://fixture.example",
                        TURNOS_BACKUP_S3_BUCKET="fixture", TURNOS_BACKUP_S3_KEY_ID="synthetic", TURNOS_BACKUP_S3_SECRET="synthetic")
        self.cfg.pop(settings.KEY)
        self.cfg.pop(settings.OLD_KEYS)
        self.key = b"k" * 32
        self.cloud, self.store = FakeCloud(), MemoryStore()
        self.patches = [patch.object(settings, "keys", return_value={crypto.key_id(self.key): self.key}),
                        patch.object(settings, "key_status", return_value={"configured": True, "fingerprint": crypto.key_id(self.key)})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        # A deploy's release identity: a checkout that is not a
        # git repository would otherwise make every run «partial».
        self.enterContext(_env.patched_env(TURNOS_OUTBOUND_KILL="0",
                                           SOURCE_COMMIT="0123456789abcdef0123456789abcdef01234567"))

    def dump(self, path, _url):
        Path(path).write_bytes(b"PGDMP-synthetic")
        return {"snapshot_at": b.timestamp(), "references": [], "counts": {}, "alembic_head": "fixture", "tools": {"pg_dump": "fixture"}}

    def run_backup(self, **kw):
        return b.run(cfg=self.cfg, cloud=self.cloud, store=self.store, dump_fn=self.dump,
                     roots={"data": self.data}, private_packs=None, env_file=self.env, **kw)

    def test_dump_before_files_upload_complete_integrity_and_no_implicit_restore(self):
        result = self.run_backup()
        self.assertTrue(result["uploaded"])
        self.assertTrue(result["integrity_verified"])
        self.assertFalse(result["restore_tested"])
        self.assertEqual(result["status"], "complete")
        self.assertNotIn(b"PGDMP-synthetic", next(iter(self.cloud.objects.values())))
        self.assertEqual(len(self.store.rows), 1)

    def test_tampered_download_never_claims_integrity(self):
        def bad(_key, target):
            Path(target).write_bytes(b"bad")
        self.cloud.download = bad
        with self.assertRaises(b.BackupError):
            self.run_backup()
        row = self.store.rows[0]
        self.assertTrue(row["uploaded"])
        self.assertFalse(row["integrity_verified"])
        self.assertEqual(row["status"], "failed")

    def test_outbound_kill_prevents_dump_and_cloud(self):
        with _env.patched_env({"TURNOS_OUTBOUND_KILL": "1"}), patch.object(b, "dump_database") as dump:
            with self.assertRaises(b.BackupError):
                self.run_backup()
            dump.assert_not_called()
        self.assertEqual(self.cloud.objects, {})

    def test_missing_pg_dump_fails_clearly(self):
        with patch.object(b.shutil, "which", return_value=None):
            with self.assertRaisesRegex(b.BackupError, "pg_dump"):
                b.dump_database(self.root / "dump", "postgresql://fixture/fixture")

    def test_retention_requires_newer_tested_floor_and_signed_ownership(self):
        first = self.run_backup()
        rows = []
        now = dt.datetime.now(dt.timezone.utc)
        for i in range(40):
            row = dict(first, id=str(i), started_at=(now-dt.timedelta(days=i)).isoformat(),
                       restore_tested=True, restore_tested_at=(now-dt.timedelta(days=i)).isoformat(),
                       archive_key="human-rounds/hr-full-" + str(i) + ".hrb")
            rows.append(row)
        cfg = dict(self.cfg, TURNOS_BACKUP_RETAIN_DAILY="1", TURNOS_BACKUP_RETAIN_WEEKLY="1", TURNOS_BACKUP_RETAIN_MONTHLY="1")
        plan = b.retention_plan(rows, cfg)
        self.assertTrue(plan)
        self.assertTrue(all(r["id"] not in {"0", "1", "2"} for r in plan))
        self.assertEqual(b.retention_plan([dict(r, restore_tested=False) for r in rows], cfg), [])
        self.assertEqual(b.apply_retention(plan, cfg, self.cloud, keys=settings.keys()), [])
        self.assertFalse(self.cloud.deleted)

    def test_system_states_keep_three_evidence_fields_and_plaintext_warning(self):
        now = dt.datetime.now(dt.timezone.utc)
        result = self.run_backup()
        state = settings.system_state(self.store.list(), self.cfg, now=now, plaintext=True)
        self.assertEqual(state["state"], "overdue")
        self.assertTrue(state["uploaded"])
        self.assertFalse(state["restore_tested"])
        self.assertTrue(state["plaintext_active"])
        self.assertEqual(settings.system_state([], dict(self.cfg, TURNOS_BACKUP_ENABLED="0"))["state"], "unconfigured")

    def test_rotated_archive_recovers_with_only_the_new_saved_key(self):
        new = b"n" * 32
        with patch.object(settings, "keys", return_value={crypto.key_id(self.key): self.key, crypto.key_id(new): new}):
            result = self.run_backup()
        encrypted = self.root / "saved.hrb"
        encrypted.write_bytes(self.cloud.objects[result["archive_key"]])
        manifest = b.restore_local(encrypted, self.root / "new-key-only", {crypto.key_id(new): new})
        self.assertEqual(manifest["archive_id"], result["id"])

    def test_applied_retention_deletes_only_signed_candidates_with_tested_floor(self):
        now = dt.datetime.now(dt.timezone.utc)
        for i in range(5):
            result = self.run_backup()
            row = next(r for r in self.store.rows if r["id"] == result["id"])
            row.update(started_at=(now-dt.timedelta(days=i)).isoformat(), restore_tested=True,
                       restore_tested_at=now.isoformat())
        cfg = dict(self.cfg, TURNOS_BACKUP_RETENTION_APPLY="1", TURNOS_BACKUP_RETAIN_DAILY="1",
                   TURNOS_BACKUP_RETAIN_WEEKLY="1", TURNOS_BACKUP_RETAIN_MONTHLY="1")
        with self.store.lock():
            plan = b.retention_plan(self.store.list(), cfg)
            deleted = b.apply_retention(plan, cfg, self.cloud, keys=settings.keys(), store=self.store)
        self.assertTrue(deleted)
        survivors = [r for r in self.store.list() if r.get("restore_tested") and not r.get("deleted_at")]
        self.assertGreaterEqual(len(survivors), 3)
        self.assertEqual(len(self.cloud.deleted), len(deleted))

    def test_database_restore_does_not_use_live_url_or_modify_nonempty_target(self):
        with patch.object(b, "_tool", return_value="pg_restore"), patch.object(b.psycopg, "connect") as connect:
            live = "postgresql://fixture@127.0.0.1/live"
            with self.assertRaisesRegex(b.BackupError, "different"):
                b.restore_database(self.root / "db.dump", live, live_url=live)
            connect.assert_not_called()
            conn = connect.return_value.__enter__.return_value
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (True,)
            with patch.object(b, "_pg_run") as pg:
                with self.assertRaisesRegex(b.BackupError, "empty"):
                    b.restore_database(self.root / "db.dump", "postgresql://fixture@127.0.0.1/occupied", live_url=live)
                pg.assert_not_called()

    def test_restore_on_an_older_server_drops_the_newer_client_transaction_timeout_line(self):
        """A pg_dump 17 dump opens with `SET transaction_timeout = 0;`, which a PostgreSQL 16
        server rejects: the restore goes through SQL text without that one line."""
        live = "postgresql://fixture@127.0.0.1/live"
        ran = []

        def fake_pg_run(argv, url):
            ran.append(argv)
            if argv[0] == "pg_restore":
                target = [a for a in argv if a.startswith("--file=")][0][len("--file="):]
                Path(target).write_text("SET statement_timeout = 0;\nSET transaction_timeout = 0;\n"
                                        "CREATE TABLE t (id int);\n")
            else:
                self.sql_seen = Path([a for a in argv if a.startswith("--file=")][0][len("--file="):]).read_text()

        with patch.object(b, "_tool", side_effect=lambda name: name), patch.object(b.psycopg, "connect") as connect, \
                patch.object(b, "_pg_run", side_effect=fake_pg_run):
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(False,), (160014,)]
            b.restore_database(self.root / "db.dump", "postgresql://fixture@127.0.0.1/scratch", live_url=live)
        self.assertEqual([a[0] for a in ran], ["pg_restore", "psql"])
        self.assertIn("-v", ran[1])
        self.assertIn("ON_ERROR_STOP=1", ran[1])
        self.assertIn("--single-transaction", ran[1])
        self.assertNotIn("transaction_timeout", self.sql_seen)
        self.assertIn("SET statement_timeout = 0;", self.sql_seen)
        self.assertIn("CREATE TABLE t (id int);", self.sql_seen)

    def test_restore_on_a_current_server_keeps_the_single_pg_restore(self):
        live = "postgresql://fixture@127.0.0.1/live"
        with patch.object(b, "_tool", side_effect=lambda name: name), patch.object(b.psycopg, "connect") as connect, \
                patch.object(b, "_pg_run") as pg:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(False,), (170005,)]
            b.restore_database(self.root / "db.dump", "postgresql://fixture@127.0.0.1/scratch", live_url=live)
        self.assertEqual(pg.call_count, 1)
        self.assertEqual(pg.call_args.args[0][0], "pg_restore")
        self.assertIn("--exit-on-error", pg.call_args.args[0])

    def test_missing_key_table_cannot_silently_pass_row_count_verification(self):
        from unittest.mock import MagicMock
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (None,)
        with self.assertRaisesRegex(b.BackupError, "table"):
            b._counts(conn)

    def test_weekly_restore_evidence_is_separate_and_failure_does_not_claim_it(self):
        self.cfg["TURNOS_BACKUP_SCRATCH_DATABASE_URL"] = "postgresql://fixture@127.0.0.1/scratch"
        with patch.object(b, "scratch_restore", return_value={"synthetic": True}) as restore:
            result = self.run_backup()
            self.assertTrue(result["restore_tested"])
            restore.assert_called_once()
            second = self.run_backup()
            self.assertFalse(second["restore_tested"])
            restore.assert_called_once()
        self.store.rows = []
        with patch.object(b, "scratch_restore", side_effect=b.BackupError("fixture")):
            result = self.run_backup()
        self.assertFalse(result["restore_tested"])
        self.assertTrue(result["integrity_verified"])
        self.assertEqual(result["restore_error"], "scratch_restore_failed")

    def test_failed_reverification_invalidates_old_integrity_claim(self):
        self.run_backup()
        self.cloud.download = lambda key, target: Path(target).write_bytes(b"corrupt")
        with patch.object(b, "Store", return_value=self.store), patch.object(settings, "snapshot", return_value=self.cfg), patch.object(settings, "client", return_value=self.cloud):
            with self.assertRaises(b.BackupError):
                b.verify()
        self.assertFalse(self.store.rows[0]["integrity_verified"])

    def test_rewrapped_archive_recovers_with_new_key_without_reencrypting_manifest(self):
        result = self.run_backup()
        encrypted = self.root / "old.hrb"
        encrypted.write_bytes(self.cloud.objects[result["archive_key"]])
        new = b"n" * 32
        revised = self.root / "rewrapped.hrb"
        with encrypted.open("rb") as src, revised.open("wb") as dst:
            crypto.rewrap(src, dst, settings.keys(), {crypto.key_id(new): new}, retain=True)
        manifest = b.restore_local(revised, self.root / "rewrapped-output", {crypto.key_id(new): new})
        self.assertEqual(manifest["archive_id"], result["id"])


class FullLoopbackTest(unittest.TestCase):
    def test_job_upload_and_full_readback_through_loopback_s3(self):
        from http.server import ThreadingHTTPServer
        import threading
        from test_backup_s3 import FakeS3
        from app.core.backup_s3 import S3
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
        except PermissionError:
            self.skipTest("Sandbox forbids loopback; full pipeline uses the in-memory cloud fixture separately")
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        FakeS3.objects, FakeS3.calls, FakeS3.fail, FakeS3.interrupted = {}, [], 0, False
        key = b"k" * 32
        cfg = {k: v for k, v in settings.DEFAULTS.items() if k not in {settings.KEY, settings.OLD_KEYS}}
        cfg.update(TURNOS_BACKUP_ENABLED="1", TURNOS_BACKUP_S3_ENDPOINT="https://fixture.example",
                   TURNOS_BACKUP_S3_BUCKET="fixture", TURNOS_BACKUP_S3_KEY_ID="synthetic", TURNOS_BACKUP_S3_SECRET="synthetic")
        with tempfile.TemporaryDirectory() as tmp, _env.patched_env({
            "TURNOS_TEST_SEAMS": "1", "TURNOS_TEST_BACKUP_S3_ENDPOINT": f"http://127.0.0.1:{server.server_port}", "TURNOS_OUTBOUND_KILL": "0"
        }), patch.object(settings, "keys", return_value={crypto.key_id(key): key}), patch.object(settings, "key_status", return_value={"configured": True}):
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data/synthetic").write_bytes(b"fixture")
            (root / "turnos.env").write_text("SYNTHETIC=1")
            def dump(path, url):
                Path(path).write_bytes(b"PGDMP-synthetic")
                return {"snapshot_at": b.timestamp(), "alembic_head": "fixture"}
            result = b.run(cfg=cfg, cloud=S3(cfg["TURNOS_BACKUP_S3_ENDPOINT"], "fixture", "synthetic", "synthetic"),
                           store=MemoryStore(), dump_fn=dump, roots={"data": root / "data"}, env_file=root / "turnos.env")
        self.assertTrue(result["uploaded"])
        self.assertTrue(result["integrity_verified"])
        self.assertTrue(any(m == "GET" for m, _ in FakeS3.calls))
