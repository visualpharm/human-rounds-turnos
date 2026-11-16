#!/usr/bin/env python3
"""Offline preflight for a synthetic restore bundle (SQL dump + volume tar).

Given an explicitly supplied manifest, an exact SQL dump and an exact tar
archive, this tool verifies their non-empty contents, byte sizes and SHA-256
digests against the manifest, and validates every tar member BEFORE any
recovery step may start.  It answers one question only: do these three files
still match their recorded description, and is the archive safe to walk?  It
is a prerequisite for the planned synthetic restore rehearsal
(docs/operations/restore-rehearsal.md), not an already integrated recovery runner.

Honest scope, read before relying on a green result:

* This is a PREFLIGHT, not a restore.  Nothing is extracted, booted or
  compared against application fixtures here, and a pass proves nothing
  about a recovered application.
* Integrity is not authenticity.  A manifest supplied next to the files it
  describes proves only that the bytes still match their recorded hashes.
  Anyone able to tamper with the bundle can rewrite the manifest too, so
  this is not a signed or trusted backup.  Authenticity requires an
  independent channel (hashes recorded away from the bundle, a signing
  key, or immutable upload receipts); this tool performs none of those.
* A pass here does not close audit row 8.3 and does not replace the
  operator-run rehearsal.

Mechanical guarantees:

* Standard library only, and import-inert: importing the module executes
  nothing (no environment reads, no filesystem access, no sys.path
  manipulation).
* No extraction, no subprocesses, no database or network, no env-file
  reads.  Every input is an explicit argument, opened read-only with
  O_NOFOLLOW, and never written.
* Exact filenames only: the manifest names each artifact by bare filename
  and the supplied path's basename must equal it.  No globs, no search.
* Inputs use canonical paths with no symlinked parent directories. Only
  uncompressed USTAR ordinary files/directories are supported; extensions
  are refused. Freeze writers and preserve the verified artifacts through
  recovery: a successful preflight does not lock files against later edits.
* Fail closed: any anomaly aborts with a structured error before a
  success inventory is produced.

Exit codes: 0 = preflight passed, 1 = preflight refused the bundle,
2 = usage error (argparse).
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import dataclasses
import datetime
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from typing import Any, Dict, List, Optional, Tuple


class PreflightError(RuntimeError):
    """The preflight refused the bundle.

    ``code`` names the failing phase; ``details`` is a value-free dict
    (artifact labels, member names, sizes), never file contents or hashes.
    """

    def __init__(self, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclasses.dataclass(frozen=True)
class Limits:
    """Conservative bounds so a synthetic rehearsal bundle cannot turn the
    preflight into a memory or disk bomb.  Every bound is an absolute cap,
    not a target; resize for a real installation explicitly."""

    max_manifest_bytes: int = 1 * 1024 * 1024
    max_dump_bytes: int = 256 * 1024 * 1024
    max_archive_bytes: int = 512 * 1024 * 1024
    max_members: int = 5_000
    max_member_bytes: int = 64 * 1024 * 1024
    max_total_member_bytes: int = 256 * 1024 * 1024

    def validate(self) -> None:
        for name in ("max_manifest_bytes", "max_dump_bytes",
                     "max_archive_bytes", "max_members",
                     "max_member_bytes", "max_total_member_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value < 1:
                raise PreflightError(
                    "limits", "limit %s must be a positive integer" % name,
                    {"limit": name})

    def as_dict(self) -> Dict[str, int]:
        return dataclasses.asdict(self)


MANIFEST_VERSION = 1
MANIFEST_KEYS = frozenset({"version", "source_commit", "captured_at",
                           "artifacts"})
ARTIFACT_KEYS = frozenset({"path", "size", "sha256"})
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_GLOB_CHARS_RE = re.compile(r"[*?\[\]]")
_READ_CHUNK = 1 << 20


def _reject(code: str, message: str,
            details: Optional[Dict[str, Any]] = None) -> None:
    raise PreflightError(code, message, details)


def _checked_filename(value: Any, label: str) -> str:
    """A bare, literal filename: no separators, no globs, no traversal."""
    if not isinstance(value, str) or not value:
        _reject("manifest", "%s path must be a non-empty string" % label,
                {"field": label})
    if value in (".", "..") or "/" in value or "\\" in value:
        _reject("manifest", "%s path must be a bare filename" % label,
                {"field": label})
    if _GLOB_CHARS_RE.search(value):
        _reject("manifest",
                "%s path must not contain glob characters" % label,
                {"field": label})
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        _reject("manifest", "%s path contains control characters" % label,
                {"field": label})
    return value


def parse_manifest(raw: bytes) -> Dict[str, Any]:
    """Strictly parse and normalize the manifest.  Anything unexpected is
    malformed: the schema is closed, not extended by ignore."""
    try:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PreflightError("manifest",
                             "manifest is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or frozenset(data) != MANIFEST_KEYS:
        _reject("manifest",
                "manifest must be an object with exactly the keys %s"
                % sorted(MANIFEST_KEYS))
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int) \
            or version != MANIFEST_VERSION:
        _reject("manifest", "manifest version must be the integer %d"
                % MANIFEST_VERSION)
    source_commit = data["source_commit"]
    if not isinstance(source_commit, str) or not source_commit \
            or any(ch.isspace() for ch in source_commit) \
            or any(ord(ch) < 0x20 for ch in source_commit):
        _reject("manifest", "source_commit must be a non-empty identifier")
    captured_at = data["captured_at"]
    if not isinstance(captured_at, str) or not captured_at:
        _reject("manifest", "captured_at must be a non-empty ISO-8601 string")
    try:
        captured = datetime.datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            raise ValueError("timestamp needs an explicit timezone")
    except ValueError as exc:
        raise PreflightError("manifest",
                             "captured_at is not an ISO-8601 timestamp") \
            from exc

    artifacts = data["artifacts"]
    if not isinstance(artifacts, dict) \
            or frozenset(artifacts) != frozenset({"dump", "archive"}):
        _reject("manifest",
                "artifacts must name exactly 'dump' and 'archive'")
    parsed: Dict[str, Dict[str, Any]] = {}
    for label in ("dump", "archive"):
        entry = artifacts[label]
        if not isinstance(entry, dict) or frozenset(entry) != ARTIFACT_KEYS:
            _reject("manifest",
                    "%s artifact must have exactly the keys %s"
                    % (label, sorted(ARTIFACT_KEYS)), {"artifact": label})
        size = entry["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            _reject("manifest", "%s size must be a positive integer" % label,
                    {"artifact": label})
        checksum = entry["sha256"]
        if not isinstance(checksum, str) or not _HEX64_RE.match(checksum):
            _reject("manifest",
                    "%s sha256 must be a 64-character hex digest" % label,
                    {"artifact": label})
        parsed[label] = {
            "path": _checked_filename(entry["path"], label),
            "size": size,
            "sha256": checksum.lower(),
        }
    if parsed["dump"]["path"] == parsed["archive"]["path"]:
        _reject("manifest", "dump and archive paths must differ")
    return {
        "version": version,
        "source_commit": source_commit,
        "captured_at": captured_at,
        "artifacts": parsed,
    }


def _open_input(path: str, label: str) -> Tuple[int, os.stat_result]:
    """Descriptor-walk all parents without following symlinks; never open
    a substituted FIFO in blocking mode. Callers must supply canonical paths."""
    if not isinstance(path, str) or not path or "\x00" in path:
        _reject("input", "%s path is missing" % label, {"artifact": label})
    parts = os.path.abspath(path).split(os.sep)[1:]
    parent = None
    fd = None
    try:
        parent = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        st_fd = os.fstat(fd)
        if not stat.S_ISREG(st_fd.st_mode):
            _reject("input", "%s is not a regular file" % label, {"artifact": label})
        result = (fd, st_fd)
        fd = None  # ownership transferred to caller
        return result
    except OSError as exc:
        raise PreflightError("input",
                             "%s is missing, unsafe or unreadable" % label,
                             {"artifact": label}) from exc
    finally:
        if parent is not None:
            os.close(parent)
        if fd is not None:
            os.close(fd)


def _scan_file(handle, max_bytes: int, label: str) -> Tuple[int, str, bool]:
    """Stream one open file: (size, sha256 hex, saw-any-non-zero-byte)."""
    digest = hashlib.sha256()
    size = 0
    nonzero = False
    handle.seek(0)
    while True:
        chunk = handle.read(min(_READ_CHUNK, max_bytes - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            _reject(label + "-oversize", "artifact grew past its byte limit", {"artifact": label})
        digest.update(chunk)
        if not nonzero and chunk.strip(b"\x00"):
            nonzero = True
    return size, digest.hexdigest(), nonzero


def _verify_artifact(label: str, handle, entry: Dict[str, Any],
                     max_bytes: int) -> Tuple[int, str, os.stat_result]:
    """Open, bound, hash and compare one artifact against its manifest
    entry.  Returns (size, sha256, stat) only when everything matches."""
    st = os.fstat(handle.fileno())
    if st.st_size > max_bytes:
        _reject("%s-oversize" % label,
                "%s exceeds the configured size bound" % label,
                {"artifact": label, "size": st.st_size, "bound": max_bytes})
    size, digest, nonzero = _scan_file(handle, max_bytes, label)
    if not _same_file(st, os.fstat(handle.fileno())):
        _reject(label + "-changed", "artifact changed during hashing", {"artifact": label})
    if size != entry["size"]:
        _reject("size-mismatch",
                "%s size does not match the manifest" % label,
                {"artifact": label, "manifest_size": entry["size"],
                 "actual_size": size})
    if digest != entry["sha256"]:
        _reject("hash-mismatch",
                "%s SHA-256 does not match the manifest" % label,
                {"artifact": label})
    if not nonzero and label == "dump":
        _reject("empty-contents",
                "%s has no non-zero bytes" % label, {"artifact": label})
    return size, digest, st


def _member_ancestors(parts: List[str]) -> List[str]:
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _validate_members(members: List[tarfile.TarInfo],
                      limits: Limits) -> Dict[str, Any]:
    """Validate every member's name, type, uniqueness and path collisions.
    Metadata only: member data is never read or extracted."""
    if not members:
        _reject("archive-empty", "archive contains no members")
    if len(members) > limits.max_members:
        _reject("member-count", "archive member count exceeds the bound",
                {"members": len(members), "bound": limits.max_members})

    entry_names: Dict[str, str] = {}  # normalized name -> "file"|"directory"
    files: Dict[str, int] = {}
    dirs: set = set()
    total = 0
    for info in members:
        name = info.name
        if not name or "\\" in name or name.startswith("/") \
                or name.startswith("./"):
            _reject("member", "member name is empty or not a safe relative "
                              "path", {"member": info.name})
        if len(name) >= 2 and name[1] == ":":
            _reject("member", "member name looks like an absolute Windows "
                              "path", {"member": name})
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
            _reject("member", "member name contains control characters",
                    {"member": name})
        is_dir = info.isdir()
        if is_dir:
            if name.endswith("/"):
                name = name[:-1]
        elif info.isreg():
            if name.endswith("/"):
                _reject("member", "regular member name ends with a slash",
                        {"member": name})
        else:
            _reject("member", "member is not a regular file or directory "
                              "(links and devices are refused)",
                    {"member": name, "tar_type": repr(info.type)})
        parts = name.split("/")
        if any(part in ("", ".", "..") for part in parts):
            _reject("member", "member name has an empty, '.' or '..' "
                              "component", {"member": info.name})
        name = "/".join(parts)
        if not name:
            _reject("member", "member name normalizes to nothing",
                    {"member": info.name})
        if name in entry_names:
            if entry_names[name] != ("directory" if is_dir else "file"):
                _reject("member-collision", "file and directory share a member path", {"member": name})
            _reject("duplicate-member", "member name appears twice",
                    {"member": name})
        for ancestor in _member_ancestors(parts):
            if ancestor in files:
                _reject("member-collision",
                        "a file member is also used as a directory prefix",
                        {"member": name, "conflict": ancestor})
        if info.size < 0:
            _reject("member-size", "member has negative size", {"member": name})
        if info.size > limits.max_member_bytes:
            _reject("member-size", "member size exceeds the bound",
                    {"member": name, "size": info.size,
                     "bound": limits.max_member_bytes})
        if is_dir:
            if info.size:
                _reject("member-size", "directory member contains data", {"member": name})
            if name in files:
                _reject("member-collision",
                        "directory member collides with a file member",
                        {"member": name})
            dirs.add(name)
            dirs.update(_member_ancestors(parts))
            entry_names[name] = "directory"
        else:
            if name in dirs:
                _reject("member-collision",
                        "file member collides with a directory member",
                        {"member": name})
            total += info.size
            if total > limits.max_total_member_bytes:
                _reject("archive-total",
                        "total member bytes exceed the bound",
                        {"member": name, "total": total,
                         "bound": limits.max_total_member_bytes})
            files[name] = info.size
            dirs.update(_member_ancestors(parts))
            entry_names[name] = "file"
    return {
        "member_count": len(members),
        "member_bytes_total": total,
        "entries": sorted(
            ({"name": name, "type": kind, "size": files.get(name, 0)}
             for name, kind in entry_names.items()),
            key=lambda entry: entry["name"]),
    }


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    """Two stats describe the same unchanged file, the identity guard
    between the hashing pass and the tar pass."""
    return (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) == \
        (second.st_dev, second.st_ino, second.st_size, second.st_mtime_ns, second.st_ctime_ns)


def _read_archive(handle, size: int, limits: Limits) -> Dict[str, Any]:
    """Bound raw headers before parsing extensions or allocating inventories.

    The rehearsal deliberately accepts only uncompressed USTAR regular files
    and directories. PAX/GNU extensions and sparse files are refused, not
    silently normalized. Exactly one archive and zero-only padding is allowed.
    """
    handle.seek(0)
    members = []
    total = 0
    if size % 512:
        _reject("archive-invalid", "tar length is not a complete block sequence")
    while True:
        header = handle.read(512)
        if len(header) != 512:
            _reject("archive-invalid", "tar has no complete end marker")
        if not header.strip(b"\0"):
            if handle.read(512) != b"\0" * 512:
                _reject("archive-invalid", "tar needs two zero end blocks")
            while handle.tell() < size:
                padding = handle.read(min(_READ_CHUNK, size - handle.tell()))
                if not padding or padding.strip(b"\0"):
                    _reject("archive-invalid", "tar has nonzero trailing data or is truncated")
            return _validate_members(members, limits)
        if len(members) >= limits.max_members:
            _reject("member-count", "archive member count exceeds the bound")
        if header[257:263] != b"ustar\0":
            _reject("archive-invalid", "only uncompressed USTAR archives are supported")
        info = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        if info.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
            _reject("member", "only ordinary files and directories are allowed")
        if info.size < 0 or info.size > limits.max_member_bytes or (info.isdir() and info.size):
            _reject("member-size", "invalid or oversized member payload")
        total += info.size
        if total > limits.max_total_member_bytes:
            _reject("archive-total", "total member bytes exceed the bound")
        payload_end = handle.tell() + ((info.size + 511) // 512) * 512
        if payload_end > size - 1024:
            _reject("archive-invalid", "tar member payload or end marker is truncated")
        # Payload bytes were already hashed through this same descriptor.
        handle.seek(payload_end)
        members.append(info)


def run_preflight(manifest_path: str, dump_path: str, archive_path: str,
                  limits: Optional[Limits] = None) -> Dict[str, Any]:
    """Verify the bundle end to end; return the inventory only if every
    check passes.  Raises PreflightError otherwise.  Never writes anything,
    never extracts, and never opens anything but its three explicit inputs."""
    if not hasattr(os, "O_NOFOLLOW"):
        _reject("platform",
                "platform lacks O_NOFOLLOW; refusing to run weakened")
    if limits is None:
        limits = Limits()
    limits.validate()

    supplied = {"manifest": manifest_path, "dump": dump_path, "archive": archive_path}
    try:
        with ExitStack() as stack:
            handles, before = {}, {}
            for label, path in supplied.items():
                fd, before[label] = _open_input(path, label)
                handles[label] = stack.enter_context(os.fdopen(fd, "rb"))
            raw = handles["manifest"].read(limits.max_manifest_bytes + 1)
            if len(raw) > limits.max_manifest_bytes:
                _reject("manifest-oversize", "manifest exceeds byte bound")
            if not raw:
                _reject("manifest", "manifest is empty")
            manifest = parse_manifest(raw)
            for label in ("dump", "archive"):
                expected = manifest["artifacts"][label]["path"]
                if os.path.basename(supplied[label]) != expected:
                    _reject("artifact-name", "artifact filename does not match manifest",
                            {"artifact": label})
            dump_size, dump_sha, _ = _verify_artifact(
                "dump", handles["dump"], manifest["artifacts"]["dump"], limits.max_dump_bytes)
            archive_size, archive_sha, _ = _verify_artifact(
                "archive", handles["archive"], manifest["artifacts"]["archive"], limits.max_archive_bytes)
            summary = _read_archive(handles["archive"], archive_size, limits)
            for label, handle in handles.items():
                if not _same_file(before[label], os.fstat(handle.fileno())):
                    _reject(label + "-changed", "input changed during preflight", {"artifact": label})
                # A replaced path must not be mistaken for the verified open inode.
                fd, after = _open_input(supplied[label], label)
                os.close(fd)
                if not _same_file(before[label], after):
                    _reject(label + "-changed", "input path changed during preflight", {"artifact": label})
    except (tarfile.TarError, OSError, EOFError, ValueError,
            UnicodeDecodeError) as exc:
        raise PreflightError(
            "archive-invalid",
            "archive is not a readable uncompressed tar "
            "(truncated, compressed or malformed)") from exc

    return {
        "status": "ok",
        "preflight_only": True,
        "integrity_note": (
            "Manifest-supplied SHA-256 values prove integrity against this "
            "manifest only. The manifest itself is not signed, so this is "
            "not proof of authenticity, and this preflight is not a restore."
        ),
        "manifest": {
            "version": manifest["version"],
            "source_commit": manifest["source_commit"],
            "captured_at": manifest["captured_at"],
            "path": os.path.abspath(manifest_path),
        },
        "dump": {
            "filename": manifest["artifacts"]["dump"]["path"],
            "size": dump_size,
            "sha256": dump_sha,
        },
        "archive": {
            "filename": manifest["artifacts"]["archive"]["path"],
            "size": archive_size,
            "sha256": archive_sha,
            "member_count": summary["member_count"],
            "member_bytes_total": summary["member_bytes_total"],
            "members": summary["entries"],
        },
        "limits": limits.as_dict(),
    }


def _limit_flags(parser: argparse.ArgumentParser) -> None:
    defaults = Limits()
    for name, help_text in (
        ("max_manifest_bytes", "maximum manifest size in bytes"),
        ("max_dump_bytes", "maximum SQL dump size in bytes"),
        ("max_archive_bytes", "maximum archive size in bytes"),
        ("max_members", "maximum archive member count"),
        ("max_member_bytes", "maximum single member size in bytes"),
        ("max_total_member_bytes", "maximum summed member size in bytes"),
    ):
        parser.add_argument("--" + name.replace("_", "-"), type=int,
                            default=getattr(defaults, name), help=help_text)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline preflight for a restore bundle: verify the "
                    "manifest, SQL dump and tar archive without extracting "
                    "anything. Integrity check only: the manifest is not "
                    "a signed trusted backup, and this is not a restore.")
    parser.add_argument("--manifest", required=True,
                        help="exact path to the bundle manifest JSON")
    parser.add_argument("--dump", required=True,
                        help="exact path to the SQL dump named by the "
                             "manifest")
    parser.add_argument("--archive", required=True,
                        help="exact path to the tar archive named by the "
                             "manifest")
    _limit_flags(parser)
    args = parser.parse_args(argv)

    limits = Limits(
        max_manifest_bytes=args.max_manifest_bytes,
        max_dump_bytes=args.max_dump_bytes,
        max_archive_bytes=args.max_archive_bytes,
        max_members=args.max_members,
        max_member_bytes=args.max_member_bytes,
        max_total_member_bytes=args.max_total_member_bytes,
    )
    try:
        inventory = run_preflight(args.manifest, args.dump, args.archive,
                                  limits)
    except PreflightError as exc:
        print(json.dumps({"status": "failed", "code": exc.code,
                          "error": str(exc), "details": exc.details},
                         indent=2, sort_keys=True))
        return 1
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return 0


__all__ = ["PreflightError", "Limits", "parse_manifest", "run_preflight",
           "main"]


if __name__ == "__main__":
    sys.exit(main())
