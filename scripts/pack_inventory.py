#!/usr/bin/env python3
"""Read-only public-pack inventory handoff.

This tool inventories only the four pack roots that the public-cut policy
currently exposes (``global``, ``ar``, ``ar-b``, and ``example-town``).  It
does not create an ``INVENTORY.json`` and it never opens, lists, hashes, or
reports excluded pack roots.  Private pack byte integrity remains the
responsibility of :func:`core.data_packs.write_inventory` and
:func:`core.data_packs.verify_required_packs` in the private installation.

The JSON result intentionally contains aggregate information only.  It has no
file names, paths, source values, timestamps, or error details, so it is safe
to hand to a public-cut reviewer.
"""

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # Direct ``python scripts/pack_inventory.py`` execution.
    from public_cut_preflight import PlatformError, PolicyError, PublicCutPreflight
except ImportError:  # Import from the repository test suite.
    from scripts.public_cut_preflight import PlatformError, PolicyError, PublicCutPreflight


SCHEMA_VERSION = "1.0"
PUBLIC_PACK_IDS = ("global", "ar", "ar-b", "example-town")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)


class InventoryFailure(RuntimeError):
    """An unsafe or inconsistent tree, deliberately without path details."""


def _safety_snapshot(st: os.stat_result) -> Tuple[int, int, int, int, int, int, int]:
    """Metadata whose change can invalidate a descriptor-backed read."""
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)


def _same_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return _safety_snapshot(left) == _safety_snapshot(right)


def _safe_fstat(fd: int, failure: str) -> os.stat_result:
    try:
        return os.fstat(fd)
    except OSError as exc:
        raise InventoryFailure(failure) from exc


def _directory_names(fd: int) -> List[str]:
    try:
        with os.scandir(fd) as entries:
            return sorted(entry.name for entry in entries)
    except OSError as exc:
        raise InventoryFailure("public tree unreadable") from exc


def _lstat_directory(path: Path) -> os.stat_result:
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise InventoryFailure("root unreadable") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise InventoryFailure("root unsafe")
    return st


def _open_directory(parent_fd: Optional[int], name: Any) -> Tuple[int, os.stat_result]:
    try:
        if parent_fd is None:
            fd = os.open(name, _DIRECTORY_FLAGS)
        else:
            fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise InventoryFailure("directory unsafe") from exc
    try:
        held = _safe_fstat(fd, "directory unsafe")
        if not stat.S_ISDIR(held.st_mode):
            raise InventoryFailure("directory unsafe")
        return fd, held
    except BaseException:
        os.close(fd)
        raise


def _entry_matches(parent_fd: int, name: str, held: os.stat_result,
                   directory: bool) -> bool:
    try:
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    expected = stat.S_ISDIR(visible.st_mode) if directory else stat.S_ISREG(visible.st_mode)
    return expected and not stat.S_ISLNK(visible.st_mode) and _same_snapshot(visible, held)


def _hash_file(parent_fd: int, name: str, before: os.stat_result) -> Tuple[int, str]:
    """Hash one regular file through its parent descriptor, then re-check it."""
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise InventoryFailure("public entry unsafe")
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise InventoryFailure("public file unsafe") from exc
    try:
        held_before = _safe_fstat(fd, "public file unsafe")
        if not stat.S_ISREG(held_before.st_mode) or not _same_snapshot(before, held_before):
            raise InventoryFailure("public file unsafe")
        digest = hashlib.sha256()
        total = 0
        try:
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
        except OSError as exc:
            raise InventoryFailure("public file unreadable") from exc
        held_after = _safe_fstat(fd, "public file changed")
        if (not _same_snapshot(held_before, held_after) or
                total != held_after.st_size or
                not _entry_matches(parent_fd, name, held_after, directory=False)):
            raise InventoryFailure("public file changed")
        return total, digest.hexdigest()
    finally:
        os.close(fd)


def _walk_public_directory(dir_fd: int, parent_fd: int,
                           visible_name: str) -> Tuple[int, int, str]:
    """Return (files, bytes, Merkle digest), holding every ancestor by FD."""
    held_before = _safe_fstat(dir_fd, "public tree unreadable")
    if not stat.S_ISDIR(held_before.st_mode):
        raise InventoryFailure("public tree unsafe")
    names = _directory_names(dir_fd)

    rows: List[Tuple[str, int, str]] = []
    count = 0
    byte_count = 0
    for name in names:
        try:
            before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            raise InventoryFailure("public tree unreadable") from exc
        if stat.S_ISLNK(before.st_mode):
            raise InventoryFailure("public tree symlink")
        if stat.S_ISDIR(before.st_mode):
            try:
                child_fd, child_held = _open_directory(dir_fd, name)
            except InventoryFailure:
                raise InventoryFailure("public tree changed") from None
            try:
                if not _same_snapshot(before, child_held):
                    raise InventoryFailure("public tree changed")
                child_count, child_bytes, child_digest = _walk_public_directory(
                    child_fd, dir_fd, name)
                if not _entry_matches(dir_fd, name, child_held, directory=True):
                    raise InventoryFailure("public tree changed")
            finally:
                os.close(child_fd)
            count += child_count
            byte_count += child_bytes
            rows.append(("d:" + name, child_count, child_digest))
        elif stat.S_ISREG(before.st_mode):
            size, digest = _hash_file(dir_fd, name, before)
            count += 1
            byte_count += size
            rows.append(("f:" + name, size, digest))
        else:
            raise InventoryFailure("public entry unsafe")

    names_after = _directory_names(dir_fd)
    held_after = _safe_fstat(dir_fd, "public tree changed")
    if (names_after != names or not _same_snapshot(held_before, held_after) or
            not _entry_matches(parent_fd, visible_name, held_after, directory=True)):
        raise InventoryFailure("public tree changed")
    tree = hashlib.sha256()
    for kind_and_name, amount, digest in rows:
        tree.update(kind_and_name.encode("utf-8"))
        tree.update(b"\0")
        tree.update(str(amount).encode("ascii"))
        tree.update(b"\0")
        tree.update(digest.encode("ascii"))
        tree.update(b"\n")
    return count, byte_count, tree.hexdigest()


def _read_pack_id(pack_fd: int) -> str:
    """Require a regular, no-follow manifest with a matching public pack id."""
    try:
        manifest_before = os.stat("pack.json", dir_fd=pack_fd, follow_symlinks=False)
    except OSError as exc:
        raise InventoryFailure("public manifest missing") from exc
    if stat.S_ISLNK(manifest_before.st_mode) or not stat.S_ISREG(manifest_before.st_mode):
        raise InventoryFailure("public manifest unsafe")
    try:
        fd = os.open("pack.json", _FILE_FLAGS, dir_fd=pack_fd)
    except OSError as exc:
        raise InventoryFailure("public manifest unsafe") from exc
    try:
        held_before = _safe_fstat(fd, "public manifest unsafe")
        if not stat.S_ISREG(held_before.st_mode) or not _same_snapshot(manifest_before, held_before):
            raise InventoryFailure("public manifest unsafe")
        chunks: List[bytes] = []
        total = 0
        try:
            while True:
                chunk = os.read(fd, 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 1024 * 1024:
                    raise InventoryFailure("public manifest unsafe")
                chunks.append(chunk)
        except OSError as exc:
            raise InventoryFailure("public manifest unreadable") from exc
        held_after = _safe_fstat(fd, "public manifest changed")
        if (not _same_snapshot(held_before, held_after) or
                not _entry_matches(pack_fd, "pack.json", held_after, directory=False)):
            raise InventoryFailure("public manifest changed")
        try:
            document = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InventoryFailure("public manifest malformed") from exc
        if not isinstance(document, dict) or not isinstance(document.get("id"), str):
            raise InventoryFailure("public manifest malformed")
        return document["id"]
    finally:
        os.close(fd)


def _inspect_external_roots(roots: Iterable[Path]) -> int:
    """Classify supplied external roots without opening or reporting them."""
    count = 0
    for root in roots:
        try:
            os.lstat(root)
        except OSError as exc:
            raise InventoryFailure("external root unreadable") from exc
        count += 1
    return count


def _report(policy_version: str, public: List[Dict[str, Any]],
            excluded_private: int, external_count: int, blocked: int) -> Dict[str, Any]:
    """Build the fixed, value-free result schema for every normal outcome."""
    public.sort(key=lambda item: item["id"])
    aggregate = hashlib.sha256()
    for item in public:
        aggregate.update(item["id"].encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(item["tree_sha256"].encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(str(item["files"]).encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(str(item["bytes"]).encode("ascii"))
        aggregate.update(b"\n")
    result = "complete" if not blocked else "blocked"
    return {
        "categories": {
            "excluded_private": excluded_private,
            "external_opaque": external_count,
            "public_verified": len(public),
            "unclassified_blocked": blocked,
        },
        "handoff": {
            "copy_performed": False,
            "delete": False,
            "mutation": False,
            "next_gates": [
                "core.data_packs.write_inventory()",
                "core.data_packs.verify_required_packs()",
                "scripts/public_cut_preflight.py",
            ],
            "private_byte_integrity_owner": "app.core.data_packs",
            "read_only": True,
        },
        "policy_version": policy_version,
        "public_packs": public,
        "result": result,
        "schema_version": SCHEMA_VERSION,
        "totals": {
            "aggregate_sha256": aggregate.hexdigest(),
            "bytes": sum(item["bytes"] for item in public),
            "files": sum(item["files"] for item in public),
            "packs": len(public),
        },
    }


def build_inventory(root: Path, policy_path: Path,
                    external_roots: Iterable[Path] = ()) -> Tuple[Dict[str, Any], int]:
    """Build a value-free public-cut handoff and its process exit status."""
    if not hasattr(os, "O_NOFOLLOW") or not os.O_NOFOLLOW:
        raise PlatformError("O_NOFOLLOW unavailable")
    try:
        original_root = Path(os.path.abspath(root))
        root_before = _lstat_directory(original_root)
        checker = PublicCutPreflight(original_root, policy_path)
        # Constructing the checker validates the policy; only this matcher is used
        # here.  Running the preflight would intentionally scan unrelated content.
        external_count = _inspect_external_roots(external_roots)
        root_fd, root_held = _open_directory(None, original_root)
    except InventoryFailure:
        return _report("unavailable", [], 0, 0, 1), 1
    public: List[Dict[str, Any]] = []
    excluded_private = 0
    blocked = 0
    try:
        if not _same_snapshot(root_before, root_held):
            raise InventoryFailure("root changed")
        packs_fd, packs_held = _open_directory(root_fd, "packs")
        try:
            if not _entry_matches(root_fd, "packs", packs_held, directory=True):
                raise InventoryFailure("packs root changed")
            names = _directory_names(packs_fd)
            names_set = set(names)
            for pack_id in PUBLIC_PACK_IDS:
                if pack_id not in names_set:
                    raise InventoryFailure("public pack missing")
            for name in names:
                rel = "packs/" + name
                rule_id = checker._rule_matches(rel)
                try:
                    entry = os.stat(name, dir_fd=packs_fd, follow_symlinks=False)
                except OSError as exc:
                    raise InventoryFailure("pack root unreadable") from exc
                if name in PUBLIC_PACK_IDS:
                    if rule_id is not None:
                        raise InventoryFailure("public pack policy conflict")
                    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                        raise InventoryFailure("public pack unsafe")
                    pack_fd, pack_held = _open_directory(packs_fd, name)
                    try:
                        if not _same_snapshot(entry, pack_held):
                            raise InventoryFailure("public pack changed")
                        declared_id = _read_pack_id(pack_fd)
                        if declared_id != name:
                            raise InventoryFailure("public manifest identity")
                        file_count, byte_count, tree_sha256 = _walk_public_directory(
                            pack_fd, packs_fd, name)
                        if not _entry_matches(packs_fd, name, pack_held, directory=True):
                            raise InventoryFailure("public pack changed")
                    finally:
                        os.close(pack_fd)
                    public.append({"bytes": byte_count, "files": file_count,
                                   "id": name, "tree_sha256": tree_sha256})
                elif rule_id is not None:
                    # This is the only permitted observation of excluded roots:
                    # name classification plus an lstat-equivalent dirfd stat.
                    excluded_private += 1
                else:
                    blocked += 1
            names_after = _directory_names(packs_fd)
            packs_after = _safe_fstat(packs_fd, "packs root changed")
            if (names_after != names or not _same_snapshot(packs_held, packs_after) or
                    not _entry_matches(root_fd, "packs", packs_after, directory=True)):
                raise InventoryFailure("packs root changed")
        finally:
            os.close(packs_fd)
        try:
            root_after = os.lstat(original_root)
        except OSError as exc:
            raise InventoryFailure("root changed") from exc
        root_fd_after = _safe_fstat(root_fd, "root changed")
        if (not stat.S_ISDIR(root_after.st_mode) or
                not _same_snapshot(root_held, root_fd_after) or
                not _same_snapshot(root_after, root_fd_after)):
            raise InventoryFailure("root changed")
    except InventoryFailure:
        blocked += 1
    finally:
        os.close(root_fd)

    report = _report(checker.policy["policy_version"], public, excluded_private,
                     external_count, blocked)
    return report, 0 if report["result"] == "complete" else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only public pack inventory")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--policy", type=Path,
                        default=Path(__file__).with_name("public_cut_policy.json"))
    parser.add_argument("--external-root", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    try:
        report, code = build_inventory(args.root, args.policy, args.external_root)
    except (InventoryFailure, PolicyError, PlatformError, OSError):
        # Do not leak a root, external path, or policy detail in a public handoff.
        report = _report("unavailable", [], 0, 0, 1)
        code = 2
    sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
