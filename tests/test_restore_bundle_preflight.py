"""Generated-fixture tests for scripts/restore_bundle_preflight.py.

Every bundle here is synthetic bytes built in a temporary directory: an
SQL dump of fictional records, a tar of generated volume files, and a
manifest written from the real artifacts.  No test touches a database, the
network, real credentials, PHI or the checkout's packs, and none is
skipped.  A green suite proves the preflight's own contract only — it is
not a restore and does not close audit row 8.3.
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import restore_bundle_preflight as preflight  # noqa: E402
from restore_bundle_preflight import Limits, PreflightError, run_preflight  # noqa: E402,E501
from _fstime import restore_mtime_after_ctime_moves  # noqa: E402


DUMP_BYTES = (
    b"-- human-rounds synthetic rehearsal dump v1\n"
    b"-- fictional records only, never a real installation\n"
    b"CREATE TABLE fictional_appointments (id integer, patient text);\n"
    b"INSERT INTO fictional_appointments VALUES (1, 'Fictional Person');\n"
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
CAPTURED_AT = "2026-09-10T12:00:00+00:00"
NOTE_BYTES = b"synthetic note v1\n"
PHOTO_BYTES = b"RIFF\x24\x00\x00\x00WEBP synthetic-volume-bytes-v1\n"
DEFAULT_MEMBERS = [
    ("data/note.txt", NOTE_BYTES),
    ("data/uploads/ficcion.webp", PHOTO_BYTES),
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _info(name, size=0, typ=tarfile.REGTYPE):
    info = tarfile.TarInfo(name)
    info.size = size
    info.type = typ
    info.mode = 0o644
    return info


def _raw_tar(entries):
    """Build an uncompressed tar from (TarInfo, payload-or-None) entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w",
                      format=tarfile.USTAR_FORMAT) as tar:
        for info, payload in entries:
            tar.addfile(
                info, io.BytesIO(payload) if payload is not None else None)
    return buf.getvalue()


def _tar_bytes(members):
    """Build an uncompressed tar from (name, payload-or-None) entries,
    where a None payload means an explicit directory member."""
    entries = []
    for name, payload in members:
        if payload is None:
            entries.append((_info(name, 0, tarfile.DIRTYPE), None))
        else:
            entries.append((_info(name, len(payload)), payload))
    return _raw_tar(entries)


def _base_manifest(dump, archive_bytes, dump_name="rehearsal.sql",
                   archive_name="volume.tar"):
    return {
        "version": 1,
        "source_commit": COMMIT,
        "captured_at": CAPTURED_AT,
        "artifacts": {
            "dump": {"path": dump_name, "size": len(dump),
                     "sha256": _sha(dump)},
            "archive": {"path": archive_name, "size": len(archive_bytes),
                        "sha256": _sha(archive_bytes)},
        },
    }


def _build_bundle(root, dump=None, members=None, archive_bytes=None,
                  manifest_mutate=None, dump_name="rehearsal.sql",
                  archive_name="volume.tar", manifest_name="bundle.json",
                  manifest_raw=None):
    """Write a complete synthetic bundle into ``root`` and return its
    paths.  The manifest is always computed from the bytes actually
    written unless ``manifest_mutate`` or ``manifest_raw`` overrides it."""
    dump = DUMP_BYTES if dump is None else dump
    if archive_bytes is None:
        archive_bytes = _tar_bytes(
            DEFAULT_MEMBERS if members is None else members)
    dump_path = os.path.join(root, dump_name)
    archive_path = os.path.join(root, archive_name)
    manifest_path = os.path.join(root, manifest_name)
    with open(dump_path, "wb") as handle:
        handle.write(dump)
    with open(archive_path, "wb") as handle:
        handle.write(archive_bytes)
    if manifest_raw is not None:
        if isinstance(manifest_raw, (bytes, bytearray)):
            with open(manifest_path, "wb") as handle:
                handle.write(manifest_raw)
            return {"manifest": manifest_path, "dump": dump_path,
                    "archive": archive_path}
        manifest = manifest_raw
    else:
        manifest = _base_manifest(dump, archive_bytes, dump_name,
                                  archive_name)
        if manifest_mutate is not None:
            manifest_mutate(manifest)
    manifest_path = os.path.join(root, manifest_name)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return {"manifest": manifest_path, "dump": dump_path,
            "archive": archive_path}


class PreflightTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            prefix="restore-preflight-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def bundle(self, **kwargs):
        return _build_bundle(self.root, **kwargs)

    def refused(self, paths, limits=None, code=None, artifact=None):
        with self.assertRaises(PreflightError) as ctx:
            run_preflight(paths["manifest"], paths["dump"], paths["archive"],
                          limits)
        error = ctx.exception
        if code is not None:
            self.assertEqual(error.code, code, msg=str(error))
        if artifact is not None:
            self.assertEqual(error.details.get("artifact"), artifact)
        return error


class TestPreflightSuccess(PreflightTestCase):
    def test_valid_bundle_returns_full_inventory(self):
        paths = self.bundle()
        inventory = run_preflight(paths["manifest"], paths["dump"],
                                  paths["archive"])
        self.assertEqual(inventory["status"], "ok")
        self.assertTrue(inventory["preflight_only"])
        self.assertEqual(inventory["dump"]["size"], len(DUMP_BYTES))
        self.assertEqual(inventory["dump"]["sha256"], _sha(DUMP_BYTES))
        self.assertEqual(inventory["dump"]["filename"], "rehearsal.sql")
        archive = inventory["archive"]
        self.assertEqual(archive["filename"], "volume.tar")
        self.assertEqual(archive["member_count"], 2)
        self.assertEqual(archive["member_bytes_total"],
                         len(NOTE_BYTES) + len(PHOTO_BYTES))
        self.assertEqual(
            [member["name"] for member in archive["members"]],
            ["data/note.txt", "data/uploads/ficcion.webp"])
        self.assertTrue(all(member["type"] == "file"
                            for member in archive["members"]))
        self.assertEqual(inventory["manifest"]["source_commit"], COMMIT)
        self.assertEqual(inventory["manifest"]["version"], 1)
        self.assertEqual(inventory["limits"], Limits().as_dict())

    def test_integrity_note_states_integrity_is_not_authenticity(self):
        paths = self.bundle()
        inventory = run_preflight(paths["manifest"], paths["dump"],
                                  paths["archive"])
        note = inventory["integrity_note"]
        self.assertIn("not signed", note)
        self.assertIn("not proof of authenticity", note)
        self.assertIn("not a restore", note)

    def test_explicit_directory_members_are_inventoried(self):
        paths = self.bundle(members=[
            ("data/", None),
            ("data/note.txt", NOTE_BYTES),
        ])
        inventory = run_preflight(paths["manifest"], paths["dump"],
                                  paths["archive"])
        self.assertEqual(
            inventory["archive"]["members"],
            [{"name": "data", "type": "directory", "size": 0},
             {"name": "data/note.txt", "type": "file",
              "size": len(NOTE_BYTES)}])
        self.assertEqual(inventory["archive"]["member_count"], 2)

    def test_uppercase_manifest_hex_is_normalized_to_lowercase(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["sha256"] = \
                _sha(DUMP_BYTES).upper()
        paths = self.bundle(manifest_mutate=mutate)
        inventory = run_preflight(paths["manifest"], paths["dump"],
                                  paths["archive"])
        self.assertEqual(inventory["dump"]["sha256"], _sha(DUMP_BYTES))

    def test_inputs_are_never_mutated_and_nothing_is_extracted(self):
        paths = self.bundle()

        def snapshot():
            state = {}
            for current, dirs, names in os.walk(self.root):
                for name in names:
                    path = os.path.join(current, name)
                    st = os.stat(path)
                    with open(path, "rb") as handle:
                        state[os.path.relpath(path, self.root)] = (
                            st.st_size, st.st_mtime_ns, _sha(handle.read()))
            return state

        before = snapshot()
        run_preflight(paths["manifest"], paths["dump"], paths["archive"])
        self.assertEqual(snapshot(), before)

    def test_inventory_is_json_serializable(self):
        paths = self.bundle()
        inventory = run_preflight(paths["manifest"], paths["dump"],
                                  paths["archive"])
        self.assertEqual(json.loads(json.dumps(inventory)), inventory)


class TestManifestSchema(PreflightTestCase):
    def test_manifest_that_is_not_json_is_refused(self):
        paths = self.bundle(manifest_raw=b"this is not json at all")
        self.refused(paths, code="manifest")

    def test_json_array_is_refused(self):
        paths = self.bundle(manifest_raw=b"[1, 2, 3]")
        self.refused(paths, code="manifest")

    def test_empty_manifest_file_is_refused(self):
        paths = self.bundle()
        with open(paths["manifest"], "wb") as handle:
            handle.write(b"")
        self.refused(paths, code="manifest")

    def test_missing_top_level_key_is_refused(self):
        paths = self.bundle(manifest_mutate=lambda m: m.pop("artifacts"))
        self.refused(paths, code="manifest")

    def test_unexpected_top_level_key_is_refused(self):
        def mutate(manifest):
            manifest["extra"] = True
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_wrong_version_is_refused(self):
        def mutate(manifest):
            manifest["version"] = 2
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_string_version_is_refused(self):
        def mutate(manifest):
            manifest["version"] = "1"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_boolean_version_is_refused(self):
        def mutate(manifest):
            manifest["version"] = True
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_empty_source_commit_is_refused(self):
        def mutate(manifest):
            manifest["source_commit"] = ""
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_whitespace_source_commit_is_refused(self):
        def mutate(manifest):
            manifest["source_commit"] = "abc def"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_non_iso_captured_at_is_refused(self):
        def mutate(manifest):
            manifest["captured_at"] = "yesterday"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_artifacts_missing_archive_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"].pop("archive")
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_artifact_extra_key_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["mtime"] = 0
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_artifact_missing_key_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["archive"].pop("size")
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_zero_size_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["size"] = 0
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_negative_size_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["size"] = -1
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_string_size_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["size"] = "123"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_boolean_size_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["size"] = True
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_short_sha_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["sha256"] = "0" * 63
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_non_hex_sha_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["archive"]["sha256"] = "z" * 64
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_path_with_separator_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["path"] = "sub/rehearsal.sql"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_parent_path_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["path"] = ".."
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_glob_star_path_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["path"] = "*.sql"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_glob_question_path_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["archive"]["path"] = "volume?.tar"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_glob_bracket_path_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["archive"]["path"] = "volume[0].tar"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_backslash_path_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["path"] = "rehearsal\\.sql"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_absolute_path_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["path"] = "/etc/rehearsal.sql"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_empty_path_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["path"] = ""
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")

    def test_same_filename_for_both_artifacts_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["archive"]["path"] = "rehearsal.sql"
        self.refused(self.bundle(manifest_mutate=mutate), code="manifest")


class TestInputFiles(PreflightTestCase):
    def test_symlinked_manifest_is_refused(self):
        paths = self.bundle()
        link = os.path.join(self.root, "link-bundle.json")
        os.symlink(paths["manifest"], link)
        paths["manifest"] = link
        self.refused(paths, code="input")

    def test_symlinked_dump_is_refused(self):
        paths = self.bundle()
        actual = os.path.join(self.root, "real-dump.sql")
        os.rename(paths["dump"], actual)
        os.symlink(actual, paths["dump"])
        self.refused(paths, code="input", artifact="dump")

    def test_symlinked_archive_is_refused(self):
        paths = self.bundle()
        actual = os.path.join(self.root, "real-volume.tar")
        os.rename(paths["archive"], actual)
        os.symlink(actual, paths["archive"])
        self.refused(paths, code="input", artifact="archive")

    def test_missing_dump_is_refused(self):
        paths = self.bundle()
        os.unlink(paths["dump"])
        self.refused(paths, code="input", artifact="dump")

    def test_directory_as_dump_is_refused(self):
        paths = self.bundle()
        os.unlink(paths["dump"])
        os.mkdir(paths["dump"])
        self.refused(paths, code="input", artifact="dump")

    def test_dump_basename_mismatch_is_refused(self):
        paths = self.bundle()
        renamed = os.path.join(self.root, "other-name.sql")
        with open(paths["dump"], "rb") as src, \
                open(renamed, "wb") as dst:
            dst.write(src.read())
        paths["dump"] = renamed
        self.refused(paths, code="artifact-name", artifact="dump")

    def test_archive_basename_mismatch_is_refused(self):
        paths = self.bundle()
        renamed = os.path.join(self.root, "other-name.tar")
        with open(paths["archive"], "rb") as src, \
                open(renamed, "wb") as dst:
            dst.write(src.read())
        paths["archive"] = renamed
        self.refused(paths, code="artifact-name", artifact="archive")

    def test_oversized_manifest_is_refused(self):
        paths = self.bundle()
        self.refused(paths, limits=Limits(max_manifest_bytes=8),
                     code="manifest-oversize")


class TestArtifactVerification(PreflightTestCase):
    def test_wrong_dump_hash_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["sha256"] = "0" * 64
        self.refused(self.bundle(manifest_mutate=mutate),
                     code="hash-mismatch", artifact="dump")

    def test_wrong_archive_hash_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["archive"]["sha256"] = "0" * 64
        self.refused(self.bundle(manifest_mutate=mutate),
                     code="hash-mismatch", artifact="archive")

    def test_wrong_dump_size_is_refused(self):
        def mutate(manifest):
            manifest["artifacts"]["dump"]["size"] += 1
        self.refused(self.bundle(manifest_mutate=mutate),
                     code="size-mismatch", artifact="dump")

    def test_dump_mutated_after_manifest_is_refused(self):
        paths = self.bundle()
        with open(paths["dump"], "ab") as handle:
            handle.write(b"-- late byte\n")
        self.refused(paths, code="size-mismatch", artifact="dump")

    def test_archive_mutated_after_manifest_is_refused(self):
        paths = self.bundle()
        with open(paths["archive"], "ab") as handle:
            handle.write(b"trailing byte")
        self.refused(paths, code="size-mismatch", artifact="archive")

    def test_nul_only_dump_with_honest_manifest_is_refused(self):
        paths = self.bundle(dump=b"\x00")
        self.refused(paths, code="empty-contents", artifact="dump")

    def test_dump_over_size_limit_is_refused(self):
        paths = self.bundle()
        self.refused(paths, limits=Limits(max_dump_bytes=8),
                     code="dump-oversize")

    def test_archive_over_size_limit_is_refused(self):
        paths = self.bundle()
        self.refused(paths, limits=Limits(max_archive_bytes=64),
                     code="archive-oversize")

    def test_hash_match_does_not_make_a_truncated_tar_valid(self):
        full = _tar_bytes(DEFAULT_MEMBERS)
        # Cut within the FIRST file payload, not unused tar record padding.
        paths = self.bundle(archive_bytes=full[:512 + len(NOTE_BYTES) - 1])
        self.refused(paths, code="archive-invalid")

    def test_shorter_optional_record_padding_is_valid(self):
        paths = self.bundle(archive_bytes=_tar_bytes(DEFAULT_MEMBERS)[:-512])
        self.assertEqual(run_preflight(paths["manifest"], paths["dump"], paths["archive"])["status"], "ok")

    def test_compressed_archive_is_refused(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = _info("data/note.txt", len(NOTE_BYTES))
            tar.addfile(info, io.BytesIO(NOTE_BYTES))
        paths = self.bundle(archive_bytes=buf.getvalue())
        self.refused(paths, code="archive-invalid")

    def test_memberless_archive_is_refused(self):
        paths = self.bundle(archive_bytes=_tar_bytes([]))
        self.refused(paths, code="archive-empty")

    def test_same_file_guard_distinguishes_changed_files(self):
        first = os.path.join(self.root, "one.bin")
        second = os.path.join(self.root, "two.bin")
        with open(first, "wb") as handle:
            handle.write(b"a")
        with open(second, "wb") as handle:
            handle.write(b"a")
        st_first = os.stat(first)
        st_second = os.stat(second)
        self.assertTrue(preflight._same_file(st_first, os.stat(first)))
        self.assertFalse(preflight._same_file(st_first, st_second))
        with open(first, "wb") as handle:
            handle.write(b"longer now")
        self.assertFalse(preflight._same_file(st_first, os.stat(first)))


class TestUnsafeMembers(PreflightTestCase):
    def test_absolute_member_name_is_refused(self):
        paths = self.bundle(members=[("/etc/passwd", b"evil\n")])
        self.refused(paths, code="member")

    def test_parent_member_name_is_refused(self):
        paths = self.bundle(members=[("../escape.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_nested_parent_member_name_is_refused(self):
        paths = self.bundle(members=[("data/../../escape.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_backslash_member_name_is_refused(self):
        paths = self.bundle(members=[("data\\note.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_windows_drive_member_name_is_refused(self):
        paths = self.bundle(members=[("C:note.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_dot_prefix_member_name_is_refused(self):
        paths = self.bundle(members=[("./note.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_double_slash_member_name_is_refused(self):
        paths = self.bundle(members=[("data//note.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_dot_component_member_name_is_refused(self):
        paths = self.bundle(members=[("data/./note.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_control_character_member_name_is_refused(self):
        paths = self.bundle(members=[("\x01note.txt", b"evil\n")])
        self.refused(paths, code="member")

    def test_duplicate_file_member_is_refused(self):
        paths = self.bundle(members=[
            ("data/note.txt", NOTE_BYTES),
            ("data/note.txt", NOTE_BYTES),
        ])
        self.refused(paths, code="duplicate-member")

    def test_duplicate_directory_member_is_refused(self):
        paths = self.bundle(members=[
            ("data/", None),
            ("data/", None),
            ("data/note.txt", NOTE_BYTES),
        ])
        self.refused(paths, code="duplicate-member")

    def test_directory_over_file_collision_is_refused(self):
        paths = self.bundle(members=[
            ("data", NOTE_BYTES),
            ("data/", None),
        ])
        self.refused(paths, code="member-collision")

    def test_file_over_directory_collision_is_refused(self):
        paths = self.bundle(members=[
            ("data/", None),
            ("data", NOTE_BYTES),
        ])
        self.refused(paths, code="member-collision")

    def test_file_used_as_directory_prefix_is_refused(self):
        paths = self.bundle(members=[
            ("a", b"file-a\n"),
            ("a/b", b"file-b\n"),
        ])
        self.refused(paths, code="member-collision")

    def test_file_nested_under_later_file_is_refused(self):
        paths = self.bundle(members=[
            ("a/b", b"file-b\n"),
            ("a", b"file-a\n"),
        ])
        self.refused(paths, code="member-collision")

    def test_symlink_member_is_refused(self):
        info = _info("data/link", 0, tarfile.SYMTYPE)
        info.linkname = "/etc/passwd"
        paths = self.bundle(archive_bytes=_raw_tar([(info, None)]))
        self.refused(paths, code="member")

    def test_hardlink_member_is_refused(self):
        info = _info("data/hardlink", 0, tarfile.LNKTYPE)
        info.linkname = "data/note.txt"
        paths = self.bundle(archive_bytes=_raw_tar([(info, None)]))
        self.refused(paths, code="member")

    def test_fifo_member_is_refused(self):
        info = _info("data/pipe", 0, tarfile.FIFOTYPE)
        paths = self.bundle(archive_bytes=_raw_tar([(info, None)]))
        self.refused(paths, code="member")

    def test_character_device_member_is_refused(self):
        info = _info("data/device", 0, tarfile.CHRTYPE)
        info.devmajor = 1
        info.devminor = 3
        paths = self.bundle(archive_bytes=_raw_tar([(info, None)]))
        self.refused(paths, code="member")


class TestBounds(PreflightTestCase):
    def test_member_count_bound_is_enforced(self):
        paths = self.bundle(members=[
            ("a.txt", b"a\n"), ("b.txt", b"b\n"), ("c.txt", b"c\n"),
        ])
        self.refused(paths, limits=Limits(max_members=2),
                     code="member-count")

    def test_member_size_bound_is_enforced(self):
        payload = b"x" * 50
        paths = self.bundle(members=[("big.txt", payload)])
        self.refused(paths, limits=Limits(max_member_bytes=49),
                     code="member-size")

    def test_directory_member_size_bound_is_enforced(self):
        info = _info("data/", 8, tarfile.DIRTYPE)
        paths = self.bundle(archive_bytes=_raw_tar([(info, b"y" * 8)]))
        self.refused(paths, limits=Limits(max_member_bytes=4),
                     code="member-size")

    def test_total_member_bytes_bound_is_enforced(self):
        paths = self.bundle(members=[
            ("a.txt", b"x" * 40), ("b.txt", b"y" * 40),
        ])
        self.refused(paths, limits=Limits(max_total_member_bytes=69),
                     code="archive-total")

    def test_non_positive_limit_is_refused(self):
        paths = self.bundle()
        self.refused(paths, limits=Limits(max_members=0), code="limits")

    def test_boolean_limit_is_refused(self):
        paths = self.bundle()
        self.refused(paths, limits=Limits(max_members=True), code="limits")


class TestIndependentSafetyRegressions(PreflightTestCase):
    def test_filesystem_root_is_a_structured_input_refusal(self):
        paths = self.bundle()
        for label in paths:
            with self.subTest(label=label):
                self.refused(dict(paths, **{label: "/"}), code="input", artifact=label)

    def test_excessively_nested_manifest_is_a_structured_refusal(self):
        self.refused(self.bundle(manifest_raw=b"[" * 2000 + b"]" * 2000), code="manifest")

    def test_duplicate_json_keys_are_rejected_at_every_level(self):
        for duplicate in ('"version":1,', '"dump":{},', '"size":1,'):
            with self.subTest(duplicate=duplicate):
                raw = json.dumps(_base_manifest(DUMP_BYTES, _tar_bytes(DEFAULT_MEMBERS)))
                marker = '{' if 'version' in duplicate else ('"artifacts": {' if 'dump' in duplicate else '"dump": {')
                raw = raw.replace(marker, marker + duplicate, 1)
                self.refused(self.bundle(manifest_raw=raw.encode()), code="manifest")

    def test_timezone_free_capture_timestamp_is_rejected(self):
        self.refused(self.bundle(manifest_mutate=lambda m: m.update(captured_at="2026-09-10T08:00:00")), code="manifest")

    def test_ancestor_symlink_is_rejected_for_each_explicit_input(self):
        paths = self.bundle()
        alias = os.path.join(self.root, "alias")
        os.symlink(self.root, alias)
        for label, path in paths.items():
            with self.subTest(label=label):
                redirected = dict(paths, **{label: os.path.join(alias, os.path.basename(path))})
                self.refused(redirected, code="input", artifact=label)

    def test_fifo_input_fails_without_blocking(self):
        paths = self.bundle()
        os.unlink(paths["dump"])
        os.mkfifo(paths["dump"])
        self.refused(paths, code="input", artifact="dump")

    def test_concatenated_tar_and_nonzero_trailer_are_rejected(self):
        first = _tar_bytes(DEFAULT_MEMBERS)
        for tail in (_tar_bytes([("hidden.txt", b"hidden")]), b"junk" + bytes(508)):
            with self.subTest(tail_length=len(tail)):
                self.refused(self.bundle(archive_bytes=first + tail), code="archive-invalid")

    def test_two_end_blocks_are_mandatory(self):
        single = _tar_bytes([("one.txt", b"one")])
        for end in (1024, 1536):
            with self.subTest(end=end):
                self.refused(self.bundle(archive_bytes=single[:end]), code="archive-invalid")

    def test_pax_metadata_is_rejected_before_reading_declared_payload(self):
        header = _info("extension", 10 ** 9, tarfile.XHDTYPE).tobuf(format=tarfile.USTAR_FORMAT)
        self.refused(self.bundle(archive_bytes=header + bytes(1024)), code="member")

    def test_sparse_file_extension_is_rejected(self):
        header = _info("sparse", 0, tarfile.GNUTYPE_SPARSE).tobuf(format=tarfile.USTAR_FORMAT)
        self.refused(self.bundle(archive_bytes=header + bytes(1024)), code="member")

    def test_member_count_stops_before_decoding_more_headers(self):
        paths = self.bundle(members=[("a", b"a"), ("b", b"b"), ("c", b"c")])
        original = preflight.tarfile.TarInfo.frombuf
        with mock.patch.object(preflight.tarfile.TarInfo, "frombuf", wraps=original) as parse:
            self.refused(paths, limits=Limits(max_members=1), code="member-count")
            self.assertEqual(parse.call_count, 1)

    def test_negative_member_size_and_directory_payload_are_rejected(self):
        for member in (_info("negative", -1), _info("directory", 1, tarfile.DIRTYPE)):
            with self.subTest(name=member.name):
                with self.assertRaises(PreflightError) as error:
                    preflight._validate_members([member], Limits())
                self.assertEqual(error.exception.code, "member-size")

    def test_growing_dump_is_bounded_during_hash_not_only_at_open(self):
        paths = self.bundle()
        real_sha = hashlib.sha256
        calls = []
        class GrowingDigest:
            def __init__(self): self.digest = real_sha()
            def update(self, chunk):
                calls.append(len(chunk))
                self.digest.update(chunk)
                with open(paths["dump"], "ab") as writer:
                    writer.write(bytes(128))
            def hexdigest(self): return self.digest.hexdigest()
        with mock.patch.object(preflight.hashlib, "sha256", GrowingDigest), mock.patch.object(preflight, "_READ_CHUNK", 32):
            self.refused(paths, limits=Limits(max_dump_bytes=len(DUMP_BYTES)), code="dump-oversize")
        self.assertLessEqual(sum(calls), len(DUMP_BYTES))

    def test_same_size_same_mtime_dump_mutation_during_tar_parse_is_rejected(self):
        paths = self.bundle()
        before = os.stat(paths["dump"])
        parse = preflight._read_archive
        def mutate(*args):
            with open(paths["dump"], "r+b") as writer:
                writer.write(b"XX")
            restore_mtime_after_ctime_moves(paths["dump"], before)
            return parse(*args)
        with mock.patch.object(preflight, "_read_archive", side_effect=mutate):
            self.refused(paths, code="dump-changed")

    def test_archive_modified_after_hash_before_parse_is_rejected(self):
        paths = self.bundle()
        parse = preflight._read_archive
        def mutate(*args):
            with open(paths["archive"], "r+b") as writer:
                writer.seek(512)
                writer.write(b"XX")
            return parse(*args)
        with mock.patch.object(preflight, "_read_archive", side_effect=mutate):
            self.refused(paths, code="archive-changed")

    def test_verified_path_replacement_is_not_confused_with_held_inode(self):
        paths = self.bundle()
        parse = preflight._read_archive
        def replace(*args):
            os.rename(paths["dump"], paths["dump"] + ".retained")
            with open(paths["dump"], "wb") as writer:
                writer.write(DUMP_BYTES)
            return parse(*args)
        with mock.patch.object(preflight, "_read_archive", side_effect=replace):
            self.refused(paths, code="dump-changed")


class TestCli(PreflightTestCase):
    def test_cli_success_prints_inventory_and_exits_zero(self):
        paths = self.bundle()
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, "restore_bundle_preflight.py"),
             "--manifest", paths["manifest"], "--dump", paths["dump"],
             "--archive", paths["archive"]],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["archive"]["member_count"], 2)

    def test_cli_failure_prints_structured_error_and_exits_one(self):
        paths = self.bundle()
        with open(paths["dump"], "ab") as handle:
            handle.write(b"-- corrupted\n")
        result = subprocess.run(
            [sys.executable,
             os.path.join(SCRIPTS_DIR, "restore_bundle_preflight.py"),
             "--manifest", paths["manifest"], "--dump", paths["dump"],
             "--archive", paths["archive"]],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["code"], "size-mismatch")


class TestImportInertness(PreflightTestCase):
    def test_import_never_touches_environment_or_filesystem(self):
        probe = os.path.join(self.root, "probe_inert.py")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write(
                "import importlib.util\n"
                "import os\n"
                "import sys\n"
                "before_env = dict(os.environ)\n"
                "before_cwd = sorted(os.listdir('.'))\n"
                "spec = importlib.util.spec_from_file_location(\n"
                "    'restore_bundle_preflight', sys.argv[1])\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "sys.modules[spec.name] = module\n"
                "spec.loader.exec_module(module)\n"
                "assert dict(os.environ) == before_env, "
                "'import mutated the environment'\n"
                "assert sorted(os.listdir('.')) == before_cwd, "
                "'import touched the filesystem'\n"
                "assert hasattr(module, 'run_preflight')\n"
                "print('inert-ok')\n")
        result = subprocess.run(
            [sys.executable, probe,
             os.path.join(SCRIPTS_DIR, "restore_bundle_preflight.py")],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("inert-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
