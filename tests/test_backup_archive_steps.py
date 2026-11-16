"""``backup_archive.build`` step by step: what the manifest, the tar and the skip list contain.

``build`` is one public entry point made of ordered steps (manifest header, root walks, the
canonical env file, the closing checks, the manifest member). These tests go through the entry point
only and pin the observable result of every step, so the function can be cut into named helpers
without a change in behaviour. ``test_backup_archive.py`` keeps the restore round trip.
"""
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import _env
from app.ops import backup_archive as a

ENV_WITH_KEY = "TURNOS_BACKUP_RECOVERY_KEY=NEVER-COPY\nOTHER=synthetic\n"
ENV_PLAIN = "OTHER=synthetic\n"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class BuildStepsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _env.set_env(self, TURNOS_ENV_FILE=str(self.root / "effective.cfg"))
        self.data = self.root / "data"
        self.data.mkdir()
        self.packs = self.root / "packs"
        self.env = self.root / "turnos.env"
        self.env.write_text(ENV_PLAIN)
        self.dump = self.root / "db.dump"
        self.dump.write_bytes(b"PGDMP-fixture")
        self.target = self.root / "archive.tar"
        self.release = {"git_commit": "c0ffee", "image_digest": None, "image_tag": None,
                        "app_version": None, "requirements_sha256": "r" * 64}
        self.enterContext(patch.object(a, "timestamp", side_effect=["T-created", "T-started", "T-finished"]))
        self.enterContext(patch.object(a, "release_info", return_value=self.release))

    def database(self, **extra):
        return {"snapshot_at": "S-dump", "alembic_head": "h1", "counts": {"patients": 3}, **extra}

    def build(self, *, roots=None, packs=None, env=None, database=None, **kw):
        return a.build(self.target, self.dump, data_roots=roots or {"data": self.data},
                       private_packs=packs, env_file=env or self.env,
                       database=database or self.database(), **kw)

    def members(self):
        with tarfile.open(self.target) as tar:
            return [m.name for m in tar]

    def content(self, name):
        with tarfile.open(self.target) as tar:
            return tar.extractfile(name).read()

    def write(self, rel, data=b"x"):
        path = self.data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data) if isinstance(data, bytes) else path.write_text(data)
        return path

    # -- the manifest header and the closing checks -------------------------------------------

    def test_manifest_header_files_and_closing_fields_are_exact(self):
        self.write("a.txt", b"alpha")
        manifest = self.build(tool_versions={"pg_dump": "16"}, archive_id="id-1")
        self.assertEqual(manifest, {
            "format": 1, "producer": "human-rounds.backup_full.v1", "archive_id": "id-1",
            "created_at": "T-created", "database_snapshot_at": "S-dump",
            "files_copy_started_at": "T-started", "files_copy_finished_at": "T-finished",
            "release": self.release, "alembic_head": "h1", "expected_alembic_head": "h1",
            "row_counts": {"patients": 3}, "tools": {"pg_dump": "16"},
            "files": [
                {"path": "database.dump", "size": 13, "sha256": _sha(b"PGDMP-fixture")},
                {"path": "data/a.txt", "size": 5, "sha256": _sha(b"alpha")},
                {"path": "config/installation.env", "size": len(ENV_PLAIN), "sha256": _sha(ENV_PLAIN.encode())},
            ],
            "directories": ["data"], "skipped": [], "changed_during_copy": [],
            "sources": {"data": str(self.data), "private-packs": None, "config/installation.env": str(self.env)},
            "excluded": dict(a.ALLOWLIST),
            "reference_check": {"checked": 0, "missing": [], "absent_at_source": []}, "status": "complete"})
        self.assertIsNot(manifest["excluded"], a.ALLOWLIST)

    def test_database_defaults_and_generated_archive_id(self):
        manifest = self.build(database={"snapshot_at": "S", "alembic_head": "h9"})
        self.assertEqual(manifest["row_counts"], {})
        self.assertEqual(manifest["tools"], {})
        self.assertEqual(manifest["expected_alembic_head"], "h9")
        self.assertEqual(str(uuid.UUID(manifest["archive_id"])), manifest["archive_id"])

    def test_tar_order_roots_then_packs_then_env_then_manifest_member(self):
        for rel in ("b.txt", "c.txt", "a/x.txt", "a/y/z.txt"):
            self.write(rel, rel.encode())
        (self.data / "e").mkdir()
        self.packs.mkdir()
        (self.packs / "p.json").write_text("{}")
        manifest = self.build(packs=str(self.packs))
        self.assertEqual(self.members(), [
            "database.dump", "data/a/x.txt", "data/a/y/z.txt", "data/b.txt", "data/c.txt",
            "private-packs/p.json", "config/installation.env", "manifest.json"])
        self.assertEqual(manifest["directories"], ["data", "data/a", "data/a/y", "data/e", "private-packs"])
        self.assertEqual([f["path"] for f in manifest["files"]], self.members()[:-1])
        self.assertEqual(manifest["sources"]["private-packs"], str(self.packs))
        with tarfile.open(self.target) as tar:
            for member in tar:
                self.assertEqual((member.mode, member.size > 0), (0o600, True), member.name)
                if member.name != "manifest.json":
                    self.assertEqual(member.mtime, 0, member.name)
        self.assertEqual(self.content("manifest.json"),
                         json.dumps(manifest, sort_keys=True, indent=2).encode())

    def test_references_are_checked_against_every_root_including_packs(self):
        self.write("a.txt")
        self.packs.mkdir()
        (self.packs / "p.json").write_text("{}")
        refs = [str(self.data / "a.txt"), str(self.data / "gone.txt"), "data/x.pdf", str(self.packs / "p.json"),
                "/abs/outside", "", None]
        manifest = self.build(packs=self.packs, database=self.database(references=refs))
        self.assertEqual(manifest["reference_check"], {
            "checked": 5,
            "missing": ["unmapped-reference//abs/outside"],
            "absent_at_source": ["data/gone.txt", "data/x.pdf"]})
        self.assertEqual(manifest["status"], "partial")

    def test_all_references_present_keeps_the_archive_complete(self):
        self.write("a.txt")
        manifest = self.build(database=self.database(references=[str(self.data / "a.txt")]))
        self.assertEqual(manifest["reference_check"], {"checked": 1, "missing": [], "absent_at_source": []})
        self.assertEqual(manifest["status"], "complete")

    def test_a_file_lost_from_the_source_disk_is_reported_but_keeps_the_archive_complete(self):
        # Prod 2026-10-29: eight referral files the database still names were gone
        # from the live volume long before the run; every archive came out partial.
        self.write("a.txt")
        refs = [str(self.data / "a.txt"), "data/ordenes/14.jpg", str(self.data / "ordenes/2.pdf")]
        manifest = self.build(database=self.database(references=refs))
        self.assertEqual(manifest["reference_check"], {
            "checked": 3, "missing": [], "absent_at_source": ["data/ordenes/14.jpg", "data/ordenes/2.pdf"]})
        self.assertEqual(manifest["status"], "complete")

    def test_a_present_file_left_out_of_the_archive_stays_partial(self):
        (self.data / "big.pdf").write_bytes(b"x" * 100)
        with patch.object(a, "MAX_FILE", 50):
            manifest = self.build(database=self.database(references=[str(self.data / "big.pdf")]))
        self.assertEqual(manifest["reference_check"]["missing"], ["data/big.pdf"])
        self.assertEqual(manifest["reference_check"]["absent_at_source"], [])
        self.assertEqual(manifest["status"], "partial")

    def test_a_reference_climbing_out_of_its_root_is_never_absent(self):
        manifest = self.build(database=self.database(references=["data/../outside.txt"]))
        self.assertEqual(manifest["reference_check"]["missing"], ["data/../outside.txt"])
        self.assertEqual(manifest["status"], "partial")

    def test_schema_mismatch_then_missing_release_identity_close_the_skip_list(self):
        self.env.write_text(ENV_WITH_KEY)
        self.write("link-target")
        (self.data / "link").symlink_to(self.data / "link-target")
        self.release.update(git_commit=None, image_digest=None)
        manifest = self.build(database=self.database(expected_alembic_head="h2"))
        self.assertEqual(manifest["skipped"], [
            {"path": "data/link", "reason": "symlink", "allowed": False},
            {"path": "config/installation.env", "reason": "recovery_key_excluded", "allowed": True},
            {"path": "database", "reason": "schema_head_mismatch", "allowed": False},
            {"path": "release", "reason": "release_identity_unavailable", "allowed": False}])
        self.assertEqual(manifest["expected_alembic_head"], "h2")
        self.assertEqual(manifest["status"], "partial")

    def test_either_release_pin_is_enough_and_allowed_skips_stay_complete(self):
        self.env.write_text(ENV_WITH_KEY)
        self.release.update(git_commit="", image_digest="sha256:abc")
        manifest = self.build()
        self.assertEqual(manifest["skipped"],
                         [{"path": "config/installation.env", "reason": "recovery_key_excluded", "allowed": True}])
        self.assertEqual(manifest["status"], "complete")

    # -- walking one root ---------------------------------------------------------------------

    def test_every_entry_kind_lands_in_files_or_skipped_in_listing_order(self):
        self.write(".backup-manifest-1.json")
        self.write(".env-x.tmp", "TURNOS_BACKUP_RECOVERY_KEY=NEVER-COPY")
        self.write("bad\\name")
        self.write("big", b"b" * 100)
        self.write("effective.cfg", ENV_WITH_KEY)
        os.mkfifo(self.data / "fifo")
        self.write("latin.env", b"\xff\xfe=1\n")
        (self.data / "link").symlink_to(self.env)
        self.write("ok.txt", b"ok")
        self.write("plain.env", ENV_PLAIN)
        self.write("secret.env", ENV_WITH_KEY)
        self.env.write_text(ENV_WITH_KEY)
        with patch.object(a, "MAX_FILE", 60):
            manifest = self.build()
        self.assertEqual([(r["path"], r["reason"], r["allowed"]) for r in manifest["skipped"]], [
            ("data/.backup-manifest-1.json", "legacy_backup_manifest", True),
            ("data/.env-x.tmp", "config_write_staging", True),
            ("data/bad\\name", "unsafe_path", False),
            ("data/big", "size_cap", False),
            ("data/effective.cfg", "recovery_key_excluded", True),
            ("data/fifo", "unsafe_path", False),
            ("data/latin.env", "unreadable", False),
            ("data/link", "symlink", False),
            ("data/secret.env", "recovery_key_excluded", True),
            ("config/installation.env", "recovery_key_excluded", True)])
        self.assertEqual(self.members(), [
            "database.dump", "data/effective.cfg", "data/ok.txt", "data/plain.env", "data/secret.env",
            "config/installation.env", "manifest.json"])
        self.assertEqual(self.content("data/plain.env"), ENV_PLAIN.encode())
        for name in ("data/effective.cfg", "data/secret.env", "config/installation.env"):
            self.assertEqual(self.content(name), ENV_PLAIN.encode(), name)
        self.assertEqual(manifest["status"], "partial")

    def test_hardlink_of_the_canonical_env_is_redacted_like_the_original(self):
        self.env.write_text(ENV_WITH_KEY)
        os.link(self.env, self.data / "alias-config")
        manifest = self.build()
        self.assertEqual(self.content("data/alias-config"), ENV_PLAIN.encode())
        self.assertIn({"path": "data/alias-config", "reason": "recovery_key_excluded", "allowed": True},
                      manifest["skipped"])

    @unittest.skipIf(os.geteuid() == 0, "directory permissions do not bind root")
    def test_a_directory_that_cannot_be_opened_is_skipped_and_the_walk_goes_on(self):
        self.write("locked/inside")
        self.write("z.txt")
        locked = self.data / "locked"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        manifest = self.build()
        self.assertEqual(manifest["skipped"], [{"path": "data/locked", "reason": "unsafe_path", "allowed": False}])
        self.assertEqual(manifest["directories"], ["data"])
        self.assertIn("data/z.txt", self.members())

    def test_a_directory_that_cannot_be_listed_is_recorded_then_skipped(self):
        self.write("sub/inside")
        self.write("z.txt")
        sub_inode = os.stat(self.data / "sub").st_ino
        real = os.listdir

        def listing(fd):
            if os.fstat(fd).st_ino == sub_inode:
                raise PermissionError("synthetic")
            return real(fd)
        with patch.object(a.os, "listdir", listing):
            manifest = self.build()
        self.assertEqual(manifest["directories"], ["data", "data/sub"])
        self.assertEqual(manifest["skipped"], [{"path": "data/sub", "reason": "unreadable", "allowed": False}])
        self.assertEqual(manifest["files"][1]["path"], "data/z.txt")

    # -- the retry for a file that changes while it is read ----------------------------------

    def _unstable_size(self, size, *, until):
        real = a._state
        calls = []

        def state(st):
            if st.st_size != size:
                return real(st)
            calls.append(1)
            return real(st) + (len(calls) if len(calls) <= until else 0,)
        return state

    def test_a_file_that_changes_once_is_retried_kept_and_reported(self):
        self.write("changing.txt", b"synthetic")
        with patch.object(a, "_state", self._unstable_size(9, until=2)):
            manifest = self.build()
        self.assertEqual(manifest["changed_during_copy"], ["data/changing.txt"])
        self.assertEqual(manifest["skipped"], [])
        self.assertEqual(self.members().count("data/changing.txt"), 1)
        self.assertEqual(self.content("data/changing.txt"), b"synthetic")
        self.assertEqual(manifest["status"], "partial")

    def test_a_file_that_keeps_changing_is_listed_once_skipped_and_not_archived(self):
        self.write("changing.txt", b"synthetic")
        with patch.object(a, "_state", self._unstable_size(9, until=10 ** 6)):
            manifest = self.build()
        self.assertEqual(manifest["changed_during_copy"], ["data/changing.txt"])
        self.assertEqual(manifest["skipped"],
                         [{"path": "data/changing.txt", "reason": "changed_during_copy", "allowed": False}])
        self.assertNotIn("data/changing.txt", self.members())
        self.assertNotIn("data/changing.txt", [f["path"] for f in manifest["files"]])

    # -- whole roots --------------------------------------------------------------------------

    def test_roots_that_cannot_be_walked_are_skipped_by_label_in_order(self):
        link = self.root / "linked"
        link.symlink_to(self.data)
        self.write("a.txt")
        # The checkout is a throwaway directory: a root that wrongly passes the check walks a few
        # bytes, never the machine's real tree.
        checkout = Path(self.enterContext(tempfile.TemporaryDirectory()))
        roots = {"missing": self.root / "nope", "wide": self.root, "whole": checkout, "linked": link,
                 "bad/../x": self.data, "data": self.data}
        with patch.object(a, "ROOT", checkout):
            manifest = self.build(roots=roots)
        self.assertEqual([(r["path"], r["reason"]) for r in manifest["skipped"]], [
            ("missing", "unreadable"), ("wide", "unsafe_path"), ("whole", "unsafe_path"),
            ("linked", "unreadable"), ("bad/../x", "unreadable")])
        self.assertEqual(manifest["directories"], ["data"])
        self.assertEqual(manifest["sources"], {k: str(v) for k, v in roots.items()}
                         | {"private-packs": None, "config/installation.env": str(self.env)})
        self.assertIn("data/a.txt", self.members())

    # -- the canonical env file ---------------------------------------------------------------

    def test_a_canonical_env_that_is_a_symlink_is_not_archived(self):
        real_env = self.root / "real.env"
        real_env.write_text(ENV_WITH_KEY)
        self.env.unlink()
        self.env.symlink_to(real_env)
        manifest = self.build()
        self.assertEqual(manifest["skipped"],
                         [{"path": "config/installation.env", "reason": "unreadable", "allowed": False}])
        self.assertNotIn("config/installation.env", self.members())

    def test_a_missing_env_or_env_directory_is_unreadable(self):
        for env in (self.root / "absent.env", self.root / "no-dir" / "x.env"):
            with self.subTest(env=str(env)), patch.object(a, "timestamp", side_effect=["a", "b", "c"]):
                manifest = self.build(env=env)
                self.assertEqual(manifest["skipped"],
                                 [{"path": "config/installation.env", "reason": "unreadable", "allowed": False}])
                self.assertEqual(manifest["sources"]["config/installation.env"], str(env))
                self.assertNotIn("config/installation.env", self.members())

    # -- failures -----------------------------------------------------------------------------

    def test_a_database_without_its_snapshot_or_head_fails_before_the_tar_exists(self):
        for missing in ("snapshot_at", "alembic_head"):
            with self.subTest(missing=missing):
                database = {k: v for k, v in self.database().items() if k != missing}
                with patch.object(a, "timestamp", side_effect=["a", "b", "c"]), self.assertRaises(KeyError):
                    self.build(database=database)
                self.assertFalse(self.target.exists())

    def test_a_missing_dump_fails_after_the_tar_was_opened(self):
        self.dump.unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assertEqual(self.target.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
