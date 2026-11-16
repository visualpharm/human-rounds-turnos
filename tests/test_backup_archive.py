import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core import backup_crypto as c
from app.ops import backup_archive as a
import _env  # noqa: E402


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # The release identity a deploy provides (Coolify's SOURCE_COMMIT):
        # without it a checkout that is not a git repository (an image, an
        # exported tree) makes every archive «partial».
        _env.set_env(self, SOURCE_COMMIT="0123456789abcdef0123456789abcdef01234567")
        self.data = self.root / "data"
        self.data.mkdir()
        self.env = self.root / "turnos.env"
        self.env.write_text("TURNOS_BACKUP_RECOVERY_KEY=NEVER-COPY\nOTHER=synthetic\n")
        self.dump = self.root / "db.dump"
        self.dump.write_bytes(b"PGDMP-fixture")

    def build(self, **kw):
        return a.build(self.root / "archive.tar", self.dump, data_roots={"data": self.data},
                       private_packs=None, env_file=self.env,
                       database={"references": [], "counts": {}, "alembic_head": "fixture", "snapshot_at": "2026-10-14T00:00:00+00:00"}, **kw)

    def test_manifest_hashes_release_pin_env_redaction_and_originals(self):
        p = self.data / "medico-fotos/.originals/fixture/portrait-a/original"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"original")
        manifest = self.build()
        self.assertIn("requirements_sha256", manifest["release"])
        self.assertIn("git_commit", manifest["release"])
        self.assertIn("data/medico-fotos/.originals/fixture/portrait-a/original", {f["path"] for f in manifest["files"]})
        with tarfile.open(self.root / "archive.tar") as tar:
            self.assertNotIn(b"NEVER-COPY", tar.extractfile("config/installation.env").read())
        target = self.root / "restore"
        self.assertEqual(a.unpack_verified(self.root / "archive.tar", target)["archive_id"], manifest["archive_id"])
        self.assertEqual((target / "data/medico-fotos/.originals/fixture/portrait-a/original").read_bytes(), b"original")

    def test_skips_are_partial_symlinks_and_size_limit(self):
        (self.data / "link").symlink_to(self.env)
        (self.data / "large").write_bytes(b"x" * 100)
        with patch.object(a, "MAX_FILE", 50):
            manifest = self.build()
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual({r["reason"] for r in manifest["skipped"]}, {"symlink", "size_cap"})

    def test_missing_reference_is_partial(self):
        with patch.object(a, "reference_names", return_value=["unmapped-reference/ordenes/missing.pdf"]):
            manifest = self.build()
        self.assertEqual(manifest["reference_check"]["missing"], ["unmapped-reference/ordenes/missing.pdf"])
        self.assertEqual(manifest["status"], "partial")

    def test_reference_lost_from_the_source_disk_is_reported_not_partial(self):
        with patch.object(a, "reference_names", return_value=["data/ordenes/missing.pdf"]):
            manifest = self.build()
        self.assertEqual(manifest["reference_check"]["absent_at_source"], ["data/ordenes/missing.pdf"])
        self.assertEqual(manifest["reference_check"]["missing"], [])

    def test_tampered_manifest_path_or_hash_and_nonempty_destination_refused(self):
        self.build()
        bad = self.root / "bad.tar"
        with tarfile.open(self.root / "archive.tar") as src, tarfile.open(bad, "w") as dst:
            for member in src:
                data = src.extractfile(member).read()
                if member.name == "database.dump":
                    data = b"x" * len(data)
                dst.addfile(member, io.BytesIO(data))
        with self.assertRaises(a.ArchiveError):
            a.unpack_verified(bad, self.root / "bad-restore")
        self.assertFalse((self.root / "bad-restore").exists())
        (self.root / "occupied").mkdir()
        (self.root / "occupied/keep").write_text("keep")
        with self.assertRaises(a.ArchiveError):
            a.unpack_verified(self.root / "archive.tar", self.root / "occupied")

    def test_file_changing_during_read_is_retried_once_and_reported(self):
        (self.data / "changing").write_bytes(b"synthetic")
        real_state = a._state
        counter = 0
        def changing(st):
            nonlocal counter
            counter += 1
            return real_state(st) + (counter,)
        with patch.object(a, "_state", side_effect=changing):
            manifest = self.build()
        self.assertIn("data/changing", manifest["changed_during_copy"])
        self.assertEqual(manifest["status"], "partial")
        self.assertTrue(any(r["reason"] == "changed_during_copy" for r in manifest["skipped"]))

    def test_env_secret_cannot_leak_through_a_regular_data_copy_of_a_symlinked_env(self):
        actual = self.data / "installation-config"
        actual.write_text("TURNOS_BACKUP_RECOVERY_KEY=NEVER-COPY\nOTHER=synthetic\n")
        self.env.unlink()
        self.env.symlink_to(actual)
        self.build()
        with tarfile.open(self.root / "archive.tar") as tar:
            self.assertNotIn(b"NEVER-COPY", tar.extractfile("data/installation-config").read())

    def test_config_writer_staging_is_explicitly_excluded(self):
        (self.data / ".env-fixture.tmp").write_text("TURNOS_BACKUP_RECOVERY_KEY=NEVER-COPY")
        manifest = self.build()
        self.assertTrue(any(r["reason"] == "config_write_staging" and r["allowed"] for r in manifest["skipped"]))
        self.assertNotIn("data/.env-fixture.tmp", {f["path"] for f in manifest["files"]})
        self.assertEqual(manifest["status"], "complete")

    def test_effective_config_basename_and_checkout_named_copy_are_redacted(self):
        # Neither copy aliases the canonical file. Use core.config's path
        # accessor for the effective basename; retain suffix-based redaction
        # for copied env files, including the checkout basename.
        for name in ("runtime-config", ".env"):
            (self.data / name).write_text(
                "TURNOS_BACKUP_RECOVERY_KEY=NEVER-COPY\nOTHER=synthetic\n")
        with _env.patched_env({"TURNOS_ENV_FILE": str(self.root / "runtime-config")}):
            self.build()
        with tarfile.open(self.root / "archive.tar") as tar:
            for name in ("runtime-config", ".env"):
                content = tar.extractfile("data/" + name).read()
                self.assertNotIn(b"NEVER-COPY", content)
                self.assertIn(b"OTHER=synthetic", content)

    def test_empty_data_and_private_directories_survive_restore(self):
        (self.data / "empty").mkdir()
        self.build()
        a.unpack_verified(self.root / "archive.tar", self.root / "empty-restored")
        self.assertTrue((self.root / "empty-restored/data/empty").is_dir())
