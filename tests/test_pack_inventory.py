"""Hermetic contracts for the read-only public pack inventory handoff."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import pack_inventory


def _policy(path):
    path.write_text(json.dumps({
        "policy_version": "2.3.0-tier7-test",
        "policy_name": "test policy",
        "required_public_artifacts": [],
        "forbidden_paths": [
            {"id": "private-town-pack", "match": "prefix",
             "pattern": "packs/ar-b-pinamar", "description": "private"},
            {"id": "private-family", "match": "regex",
             "pattern": "(^|/)packs/[^/]+-private($|/)", "description": "private"},
        ],
        "secret_marker_classes": [], "external_evidence": [],
    }), encoding="utf-8")


def _pack(root, pack_id, payload=b"public"):
    directory = root / "packs" / pack_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pack.json").write_text(json.dumps({"id": pack_id}), encoding="utf-8")
    (directory / "payload.bin").write_bytes(payload)
    return directory


class PackInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "candidate"
        self.root.mkdir()
        self.policy = self.root / "policy.json"
        _policy(self.policy)
        for pack_id in pack_inventory.PUBLIC_PACK_IDS:
            _pack(self.root, pack_id, pack_id.encode("ascii"))
        self.private = _pack(self.root, "ar-b-pinamar", b"private sentinel")
        (self.private / "never-open.txt").write_text("private sentinel", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def inventory(self):
        return pack_inventory.build_inventory(self.root, self.policy)

    def test_current_model_is_complete_value_free_and_deterministic(self):
        first, first_code = self.inventory()
        second, second_code = self.inventory()
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(first, second)
        self.assertEqual(first["result"], "complete")
        self.assertEqual(first["categories"], {
            "excluded_private": 1, "external_opaque": 0,
            "public_verified": 4, "unclassified_blocked": 0,
        })
        rendered = json.dumps(first, sort_keys=True)
        self.assertNotIn("ar-b-pinamar", rendered)
        self.assertNotIn("never-open", rendered)
        self.assertNotIn(str(self.root), rendered)
        self.assertTrue(first["handoff"]["read_only"])
        self.assertFalse(first["handoff"]["copy_performed"])
        self.assertFalse(first["handoff"]["delete"])
        self.assertFalse(first["handoff"]["mutation"])

    def test_public_change_changes_tree_and_aggregate_digest(self):
        before, code = self.inventory()
        self.assertEqual(code, 0)
        (self.root / "packs" / "ar" / "payload.bin").write_bytes(b"changed")
        after, code = self.inventory()
        self.assertEqual(code, 0)
        self.assertNotEqual(before["totals"]["aggregate_sha256"], after["totals"]["aggregate_sha256"])
        before_ar = next(item for item in before["public_packs"] if item["id"] == "ar")
        after_ar = next(item for item in after["public_packs"] if item["id"] == "ar")
        self.assertNotEqual(before_ar["tree_sha256"], after_ar["tree_sha256"])

    def test_unknown_root_blocks_without_echoing_its_name(self):
        unknown = self.root / "packs" / "do-not-report-this"
        unknown.mkdir()
        (unknown / "private.txt").write_text("sentinel", encoding="utf-8")
        report, code = self.inventory()
        self.assertEqual(code, 1)
        self.assertEqual(report["result"], "blocked")
        self.assertGreater(report["categories"]["unclassified_blocked"], 0)
        self.assertNotIn("do-not-report-this", json.dumps(report, sort_keys=True))

    def test_private_root_is_classified_without_opening_a_child(self):
        original_open = os.open

        def guarded_open(name, *args, **kwargs):
            self.assertNotEqual(name, "never-open.txt")
            return original_open(name, *args, **kwargs)

        with patch("scripts.pack_inventory.os.open", side_effect=guarded_open):
            report, code = self.inventory()
        self.assertEqual(code, 0)
        self.assertEqual(report["categories"]["excluded_private"], 1)

    def test_external_root_is_opaque_and_not_echoed(self):
        external = Path(self.temp.name) / "external-private-root"
        external.mkdir()
        (external / "secret.txt").write_text("private", encoding="utf-8")
        report, code = pack_inventory.build_inventory(self.root, self.policy, [external])
        self.assertEqual(code, 0)
        self.assertEqual(report["categories"]["external_opaque"], 1)
        self.assertNotIn(str(external), json.dumps(report, sort_keys=True))

    def test_missing_public_pack_wrong_manifest_and_symlink_fail_closed(self):
        for shape in ("missing", "wrong-id", "symlink"):
            with self.subTest(shape=shape):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td) / "candidate"
                    root.mkdir()
                    policy = root / "policy.json"
                    _policy(policy)
                    for pack_id in pack_inventory.PUBLIC_PACK_IDS:
                        _pack(root, pack_id)
                    _pack(root, "ar-b-pinamar", b"private")
                    target = root / "packs" / "ar"
                    if shape == "missing":
                        for child in target.iterdir():
                            child.unlink()
                        target.rmdir()
                    elif shape == "wrong-id":
                        (target / "pack.json").write_text('{"id":"wrong"}', encoding="utf-8")
                    else:
                        outside = Path(td) / "outside"
                        outside.mkdir()
                        (outside / "pack.json").write_text('{"id":"ar"}', encoding="utf-8")
                        (outside / "payload.bin").write_bytes(b"outside")
                        target.rename(root / "packs" / "ar-real")
                        os.symlink(outside, target)
                    report, code = pack_inventory.build_inventory(root, policy)
                    self.assertEqual(code, 1)
                    self.assertEqual(report["result"], "blocked")

    def test_nonregular_public_entry_fails_closed(self):
        fifo = self.root / "packs" / "global" / "unsafe"
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        os.mkfifo(fifo)
        try:
            report, code = self.inventory()
            self.assertEqual(code, 1)
            self.assertEqual(report["result"], "blocked")
        finally:
            fifo.unlink()

    def test_policy_matcher_classifies_any_policy_excluded_root(self):
        _pack(self.root, "withheld-private", b"private")
        report, code = self.inventory()
        self.assertEqual(code, 0)
        self.assertEqual(report["categories"]["excluded_private"], 2)

    def test_root_swap_is_detected_without_exposing_paths(self):
        original = pack_inventory._walk_public_directory
        replacement = Path(self.temp.name) / "replacement"
        replacement.mkdir()
        (replacement / "packs").mkdir()
        swapped = False

        def swap_once(*args, **kwargs):
            nonlocal swapped
            if not swapped and self.root.exists():
                self.root.rename(Path(self.temp.name) / "old-candidate")
                replacement.rename(self.root)
                swapped = True
            return original(*args, **kwargs)

        with patch("scripts.pack_inventory._walk_public_directory", side_effect=swap_once):
            report, code = self.inventory()
        self.assertEqual(code, 1)
        self.assertNotIn("old-candidate", json.dumps(report, sort_keys=True))

    def test_same_inode_large_file_mutation_during_read_is_blocked(self):
        target = self.root / "packs" / "global" / "large-sensitive.bin"
        target.write_bytes(b"A" * (2 * 1024 * 1024 + 19))
        before = os.stat(target)
        original_read = os.read
        mutated = False

        def mutate_mid_read(fd, amount):
            nonlocal mutated
            chunk = original_read(fd, amount)
            if (not mutated and chunk and
                    os.fstat(fd).st_ino == before.st_ino):
                target.write_bytes(b"B" * before.st_size)
                os.utime(target, ns=(before.st_atime_ns,
                                     before.st_mtime_ns + 1_000_000_000))
                mutated = True
                self.assertEqual(os.stat(target).st_ino, before.st_ino)
            return chunk

        with patch("scripts.pack_inventory.os.read", side_effect=mutate_mid_read):
            report, code = self.inventory()
        rendered = json.dumps(report, sort_keys=True)
        self.assertTrue(mutated)
        self.assertEqual(code, 1)
        self.assertEqual(report["result"], "blocked")
        self.assertNotIn("large-sensitive", rendered)
        self.assertNotIn("AAAA", rendered)
        self.assertNotIn("BBBB", rendered)

    def test_late_pack_root_insertion_and_removal_are_blocked(self):
        packs = self.root / "packs"
        original_names = pack_inventory._directory_names
        packs_inode = os.stat(packs).st_ino

        for operation in ("insert", "remove"):
            with self.subTest(operation=operation):
                calls = 0
                inserted = packs / "late-root-sensitive"
                moved = self.root / "held-private-sensitive"

                def mutate_on_final_list(fd):
                    nonlocal calls
                    if os.fstat(fd).st_ino == packs_inode:
                        calls += 1
                        if calls == 2:
                            if operation == "insert":
                                inserted.write_text("private value", encoding="utf-8")
                            else:
                                self.private.rename(moved)
                    return original_names(fd)

                try:
                    with patch("scripts.pack_inventory._directory_names",
                               side_effect=mutate_on_final_list):
                        report, code = self.inventory()
                    rendered = json.dumps(report, sort_keys=True)
                    self.assertEqual(code, 1)
                    self.assertEqual(report["result"], "blocked")
                    self.assertNotIn("late-root-sensitive", rendered)
                    self.assertNotIn("held-private-sensitive", rendered)
                    self.assertNotIn("private value", rendered)
                finally:
                    if inserted.exists():
                        inserted.unlink()
                    if moved.exists():
                        moved.rename(self.private)

    def test_late_nested_insertion_and_removal_are_blocked(self):
        target_dir = self.root / "packs" / "global"
        original_names = pack_inventory._directory_names
        target_inode = os.stat(target_dir).st_ino

        for operation in ("insert", "remove"):
            with self.subTest(operation=operation):
                calls = 0
                inserted = target_dir / "late-nested-sensitive"
                removable = target_dir / "remove-nested-sensitive"
                if operation == "remove":
                    removable.write_text("private value", encoding="utf-8")

                def mutate_on_final_list(fd):
                    nonlocal calls
                    if os.fstat(fd).st_ino == target_inode:
                        calls += 1
                        if calls == 2:
                            if operation == "insert":
                                inserted.write_text("private value", encoding="utf-8")
                            else:
                                removable.unlink()
                    return original_names(fd)

                try:
                    with patch("scripts.pack_inventory._directory_names",
                               side_effect=mutate_on_final_list):
                        report, code = self.inventory()
                    rendered = json.dumps(report, sort_keys=True)
                    self.assertEqual(code, 1)
                    self.assertEqual(report["result"], "blocked")
                    self.assertNotIn("late-nested-sensitive", rendered)
                    self.assertNotIn("remove-nested-sensitive", rendered)
                    self.assertNotIn("private value", rendered)
                finally:
                    if inserted.exists():
                        inserted.unlink()
                    if removable.exists():
                        removable.unlink()

    def test_cli_is_canonical_and_byte_stable(self):
        command = [sys.executable, str(Path(pack_inventory.__file__)),
                   "--root", str(self.root), "--policy", str(self.policy)]
        first = subprocess.run(command, check=False, capture_output=True)
        second = subprocess.run(command, check=False, capture_output=True)
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, b"")
        self.assertTrue(first.stdout.endswith(b"\n"))
        self.assertEqual(json.loads(first.stdout)["schema_version"], "1.0")

    def test_tool_source_has_no_network_config_database_or_subprocess_dependency(self):
        source = Path(pack_inventory.__file__).read_text(encoding="utf-8")
        for forbidden in ("import socket", "import subprocess", "config.", "sqlite", "psycopg"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("os.environ", source)

    def test_no_descriptor_leak_on_failure(self):
        fd_dir = Path("/dev/fd")
        if not fd_dir.exists():
            self.skipTest("fd listing unavailable")
        before = len(list(fd_dir.iterdir()))
        (self.root / "packs" / "global" / "unsafe").symlink_to("payload.bin")
        _report, code = self.inventory()
        self.assertEqual(code, 1)
        after = len(list(fd_dir.iterdir()))
        self.assertLessEqual(after, before + 1)


if __name__ == "__main__":
    unittest.main()
