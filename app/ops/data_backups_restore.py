"""Operator-only B2 version catalog, restore, reconciliation and retention.

This module deliberately has no worker registration, HTTP route, boot hook, or
timer.  It is a recovery operator's tool: list remote versions, reconcile
them with the immutable acceptance ledger, restore one exact B2 file id into a
fresh safe destination, and *plan* conservative retention.  ``apply_retention``
defaults to dry-run; no production process imports or invokes a delete path.

The producer in :mod:`ops.data_backups` stores an immutable receipt for every
new B2 version.  Pre-ledger ``backup_journal`` rows have no remote file id and
are intentionally reported as legacy/protected, never guessed into a delete
candidate.  An accepted remote upload followed by a database crash is likewise
an unledgered orphan: visible during reconciliation and protected by default.

All public helpers accept injected fetch/list/delete functions for operational
drills.  The tests use loopback fake B2 stores and generated non-PHI bytes; no
fixture needs a real B2 credential or a live data volume.

On the municipal installation (`core.backup_lock`) the exact delete and an
applied retention are refused before any request; listing, reconciliation,
the dry-run plan and restores stay.
"""

import hashlib
import json
import os
import shutil
import stat
import tempfile
import urllib.error
import urllib.request

from app.core import backup_lock, outbound_guard
from app.core import db_backup
from app.ops import data_backups


class BackupVersionError(ValueError):
    """Remote metadata or a restore destination violated a safety contract."""


class RemoteAlreadyAbsent(RuntimeError):
    """An exact B2 deletion was already effective before a retry recorded it."""


class RemoteDeleteDenied(RuntimeError):
    """B2 refused an exact deletion for lack of permission (HTTP 401/403 on
    ``b2_delete_file_version``): the application key has no ``deleteFiles``
    capability. ``code`` is the HTTP status; B2's own message is never kept."""

    def __init__(self, code):
        super().__init__(f"B2 refused the deletion (HTTP {code}): application key without deleteFiles")
        self.code = code


# B2 answers 401 with these codes when the TOKEN is the problem, not the key's capabilities.
_B2_TOKEN_REFUSALS = {"expired_auth_token", "bad_auth_token"}


_MAX_BYTES = data_backups._MAX_FILE_MB * 1024 * 1024


def _outbound_preflight():
    """Refuse an operator-side B2 action before transport or local mutation.

    The worker arms the socket guard at process startup, but this module is
    deliberately usable outside worker.py for a recovery drill.  Respecting
    the requested environment flag here closes that direct-call bypass: no
    authorization request, download, exact delete, restore output, or
    tombstone write may begin while ``TURNOS_OUTBOUND_KILL=1`` is set.
    """
    if outbound_guard.requested():
        raise BackupVersionError("TURNOS_OUTBOUND_KILL=1 forbids B2 operator actions")


def _prefix(prefix=None):
    value = data_backups._env("TURNOS_B2_PREFIX", "data/") if prefix is None else prefix
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise BackupVersionError("B2 prefix must be a non-empty slash-only relative prefix")
    if value.startswith("/") or not value.endswith("/"):
        raise BackupVersionError("B2 prefix must be a relative directory prefix ending in /")
    if any(part in {"", ".", ".."} for part in value[:-1].split("/")):
        raise BackupVersionError("B2 prefix has an unsafe path segment")
    return value


def safe_relative_name(remote_name, *, prefix=None):
    """Map one exact B2 name under ``prefix`` to a safe local relative path.

    This rejects paths rather than normalizing them.  A restore tool should
    never turn ``..`` or a changed prefix into a plausible local destination.
    Legacy local manifests are producer state, not restorable user content.
    """
    prefix = _prefix(prefix)
    if not isinstance(remote_name, str) or not remote_name:
        raise BackupVersionError("B2 file name must be a non-empty string")
    if "\x00" in remote_name or "\\" in remote_name or remote_name.startswith("/"):
        raise BackupVersionError("B2 file name is not a safe relative POSIX path")
    if not remote_name.startswith(prefix):
        raise BackupVersionError("B2 file name is outside the configured prefix")
    relative = remote_name[len(prefix):]
    parts = relative.split("/")
    if not relative or any(part in {"", ".", ".."} for part in parts):
        raise BackupVersionError("B2 file name has an unsafe path segment")
    if any(part.startswith(data_backups._LEGACY_MANIFEST_PREFIX) for part in parts):
        raise BackupVersionError("legacy backup manifest artifacts are never restorable")
    if os.path.isabs(relative):
        raise BackupVersionError("B2 file name is absolute")
    return relative


def _require_upload(record):
    if not isinstance(record, dict):
        raise BackupVersionError("B2 version record must be a mapping")
    action = record.get("action", "upload")
    if action != "upload":
        raise BackupVersionError("only B2 upload versions can be restored or retained")
    file_id = record.get("file_id") or record.get("fileId")
    remote_name = record.get("remote_name") or record.get("fileName")
    sha256 = record.get("sha256")
    sha1 = record.get("sha1") or record.get("contentSha1")
    length = record.get("size", record.get("contentLength"))
    if not isinstance(file_id, str) or not file_id:
        raise BackupVersionError("B2 version lacks an exact file id")
    if not isinstance(remote_name, str) or not remote_name:
        raise BackupVersionError("B2 version lacks an exact file name")
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise BackupVersionError("B2 version lacks a SHA-256 receipt")
    if not isinstance(sha1, str) or len(sha1) != 40:
        raise BackupVersionError("B2 version lacks a SHA-1 receipt")
    try:
        length = int(length)
    except (TypeError, ValueError) as exc:
        raise BackupVersionError("B2 version lacks a numeric size") from exc
    if length < 0 or length > _MAX_BYTES:
        raise BackupVersionError("B2 version size is outside the backup bound")
    if any(ch not in "0123456789abcdef" for ch in sha256.lower() + sha1.lower()):
        raise BackupVersionError("B2 version has a malformed checksum")
    return {"file_id": file_id, "remote_name": remote_name,
            "sha256": sha256.lower(), "sha1": sha1.lower(), "size": length,
            "action": "upload", "journal_path": record.get("journal_path"),
            "accepted_at": record.get("accepted_at"),
            "upload_timestamp": record.get("uploadTimestamp", 0)}


def _remote_shape(record):
    """Normalize a list-file-versions record without declaring it trusted."""
    if not isinstance(record, dict):
        return None
    action = record.get("action", "upload")
    file_id = record.get("file_id") or record.get("fileId")
    remote_name = record.get("remote_name") or record.get("fileName")
    if not isinstance(file_id, str) or not file_id or not isinstance(remote_name, str) or not remote_name:
        return None
    out = dict(record)
    out.update({"file_id": file_id, "remote_name": remote_name, "action": action})
    try:
        out["size"] = int(record.get("size", record.get("contentLength", 0)))
    except (TypeError, ValueError):
        out["size"] = -1
    out["sha1"] = record.get("sha1") or record.get("contentSha1")
    out["upload_timestamp"] = record.get("uploadTimestamp", 0) or 0
    return out


def list_remote_versions(*, prefix=None, page_fetch=None):
    """Read every B2 file-version page, advancing both continuation cursors.

    ``nextFileName`` alone is insufficient: B2 can have several file ids for
    the same name.  Reusing only the name skips or repeats a version at that
    boundary, so both cursors are carried exactly and a stalled pair is a hard
    error rather than an infinite catalog loop.
    """
    prefix = _prefix(prefix)
    page_fetch = page_fetch or _b2_list_page
    start_name = start_id = None
    seen_cursors = set()
    records = []
    while True:
        page = page_fetch(start_name, start_id, prefix)
        if not isinstance(page, dict) or not isinstance(page.get("files", []), list):
            raise BackupVersionError("B2 list_file_versions response is malformed")
        records.extend(page["files"])
        next_name = page.get("nextFileName")
        next_id = page.get("nextFileId")
        if next_name is None and next_id is None:
            return records
        if not isinstance(next_name, str) or not next_name or not isinstance(next_id, str) or not next_id:
            raise BackupVersionError("B2 pagination cursor must contain both name and id")
        cursor = (next_name, next_id)
        if cursor in seen_cursors:
            raise BackupVersionError("B2 version pagination cursor did not advance")
        seen_cursors.add(cursor)
        start_name, start_id = cursor


def _b2_list_page(start_name, start_id, prefix):
    """Production B2 list call.  It is operator-invoked only, never scheduled."""
    _outbound_preflight()
    if not data_backups.configured():
        raise BackupVersionError("B2 is not configured")
    body = {"bucketId": data_backups._env("TURNOS_B2_BUCKET_ID"), "prefix": prefix}
    if start_name is not None:
        body["startFileName"] = start_name
        body["startFileId"] = start_id
    for attempt in (1, 2):
        auth = data_backups._authorize()
        try:
            return data_backups._req_json(
                auth["apiUrl"] + "/b2api/v2/b2_list_file_versions",
                {"Authorization": auth["authorizationToken"]}, body)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 1:
                exc.close()
                continue
            exc.close()
            raise


def reconcile(remote_records, journal_rows, ledger_rows, tombstones, *, prefix=None):
    """Classify remote/version evidence without mutating a database or B2.

    An unknown, orphan, hide marker, prefix mismatch, legacy journal row, or
    unexplained missing immutable receipt is a protection signal.  The
    retention planner refuses to produce any delete candidate while one exists.
    """
    prefix = _prefix(prefix)
    remote_by_id = {}
    unknown = []
    hides = []
    outside_prefix = []
    for raw in remote_records:
        item = _remote_shape(raw)
        if item is None:
            unknown.append(raw)
            continue
        if item["file_id"] in remote_by_id:
            unknown.append(raw)
            continue
        remote_by_id[item["file_id"]] = item
        try:
            safe_relative_name(item["remote_name"], prefix=prefix)
        except BackupVersionError:
            outside_prefix.append(item)
            continue
        if item["action"] == "hide":
            hides.append(item)
        elif item["action"] != "upload":
            unknown.append(item)

    known = {}
    missing = []
    for file_id, ledger in ledger_rows.items():
        try:
            expected = _require_upload(ledger)
        except BackupVersionError:
            unknown.append(ledger)
            continue
        remote = remote_by_id.get(file_id)
        tombstone = tombstones.get(file_id)
        if tombstone and remote is not None:
            # A remote version that reappears after an exact deletion record
            # is anomalous (or a fabricated list response).  It is evidence
            # to investigate, never a harmless stale row to retain/prune.
            unknown.append(remote)
            continue
        if remote is None:
            if tombstone and tombstone.get("remote_name") == expected["remote_name"]:
                continue
            missing.append(expected)
            continue
        if (remote["action"] != "upload" or remote["remote_name"] != expected["remote_name"]
                or remote["size"] != expected["size"]
                or str(remote.get("sha1") or "").lower() != expected["sha1"]):
            unknown.append(remote)
            continue
        expected["upload_timestamp"] = remote.get("upload_timestamp", 0)
        known[file_id] = expected

    orphans = []
    for file_id, remote in remote_by_id.items():
        if file_id in ledger_rows or file_id in tombstones:
            continue
        if remote["action"] == "upload":
            orphans.append(remote)

    current = set()
    legacy = []
    current_mismatch = []
    for path, row in journal_rows.items():
        file_id = row.get("remote_file_id")
        if not file_id:
            legacy.append({"path": path, **row})
            continue
        expected = known.get(file_id)
        if not expected or expected["remote_name"] != row.get("remote_name"):
            current_mismatch.append({"path": path, **row})
            continue
        current.add(file_id)

    return {
        "remote_by_id": remote_by_id, "known": known, "current": current,
        "legacy": legacy, "missing": missing, "orphans": orphans,
        "hides": hides, "unknown": unknown, "outside_prefix": outside_prefix,
        "current_mismatch": current_mismatch,
    }


def reconcile_live(*, prefix=None, page_fetch=None, conn=None):
    """Operator helper: obtain remote catalog then compare it to local evidence."""
    remote = list_remote_versions(prefix=prefix, page_fetch=page_fetch)
    journal = db_backup.backup_journal_map(conn=conn)
    ledger = db_backup.backup_version_rows(conn=conn)
    tombstones = db_backup.backup_tombstone_rows(conn=conn)
    return reconcile(remote, journal, ledger, tombstones, prefix=prefix)


def _sort_key(record):
    value = record.get("upload_timestamp", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def retention_plan(reconciliation, *, dry_run=True):
    """Create a conservative retention plan; default is non-mutating dry run.

    Each path retains its current receipt plus the newest *prior* verified
    receipt.  Any uncertain catalog state globally suppresses deletion — a
    backup system is not where a convenient guess is acceptable.
    """
    unsafe = ("legacy", "missing", "orphans", "hides", "unknown",
              "outside_prefix", "current_mismatch")
    blockers = {key: reconciliation.get(key, []) for key in unsafe
                if reconciliation.get(key)}
    known = reconciliation.get("known", {})
    current = set(reconciliation.get("current", set()))
    groups = {}
    for record in known.values():
        groups.setdefault(record.get("journal_path"), []).append(record)
    protected = {}
    candidates = []
    for path, items in groups.items():
        ordered = sorted(items, key=_sort_key, reverse=True)
        for record in ordered:
            if record["file_id"] in current:
                protected[record["file_id"]] = "current"
        prior = next((item for item in ordered if item["file_id"] not in current), None)
        if prior is not None:
            protected[prior["file_id"]] = "newest prior verified"
        for record in ordered:
            if record["file_id"] not in protected:
                candidates.append({"file_id": record["file_id"],
                                   "remote_name": record["remote_name"],
                                   "journal_path": path})
    if blockers:
        candidates = []
    return {"dry_run": bool(dry_run), "candidates": candidates,
            "protected": protected, "blockers": blockers}


def apply_retention(plan, delete_fn, tombstone_fn=None, *, dry_run=True):
    """Apply an already-reviewed exact-id plan; dry-run is the default.

    ``delete_fn`` receives precisely ``(remote_name, file_id)``.  A retry that
    reports :class:`RemoteAlreadyAbsent` converges by recording the tombstone;
    a randomly missing ledger item never reaches this function because the
    planner treats it as a blocker.  There is intentionally no scheduler or
    configuration switch that calls this function.
    """
    if not isinstance(plan, dict) or plan.get("blockers"):
        raise BackupVersionError("retention plan is not safe to apply")
    if not dry_run:
        if backup_lock.locked():  # before the first candidate: nothing half done
            backup_lock.refuse("B2 retention of data/ backup versions")
        _outbound_preflight()
    tombstone_fn = tombstone_fn or db_backup.backup_version_tombstone
    outcomes = []
    for candidate in plan.get("candidates", []):
        file_id = candidate.get("file_id")
        remote_name = candidate.get("remote_name")
        if not isinstance(file_id, str) or not file_id or not isinstance(remote_name, str) or not remote_name:
            raise BackupVersionError("retention candidate lacks exact remote identity")
        if dry_run:
            outcomes.append({"file_id": file_id, "status": "would-delete"})
            continue
        try:
            delete_fn(remote_name, file_id)
            status = "deleted"
        except RemoteAlreadyAbsent:
            status = "already-absent"
        tombstone_fn(file_id, remote_name)
        outcomes.append({"file_id": file_id, "status": status})
    return outcomes


def delete_remote_version(remote_name, file_id):
    """Exact B2 deletion primitive for a reviewed manual plan only.

    Nothing in the application invokes this.  Its exact ``fileName`` plus
    ``fileId`` body is intentionally exposed separately from the dry-run
    planner so an operator cannot accidentally delete all versions sharing a
    filename. Refused on the municipal installation before any request.
    """
    if backup_lock.locked():
        backup_lock.refuse("B2 delete of a backup version")
    _outbound_preflight()
    safe_relative_name(remote_name)
    if not isinstance(file_id, str) or not file_id:
        raise BackupVersionError("exact B2 file id is required")
    if not data_backups.configured():
        raise BackupVersionError("B2 is not configured")
    auth = data_backups._authorize()
    try:
        result = data_backups._req_json(
            auth["apiUrl"] + "/b2api/v2/b2_delete_file_version",
            {"Authorization": auth["authorizationToken"]},
            {"fileName": remote_name, "fileId": file_id})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        code = exc.code
        exc.close()
        if code == 400 and "file_not_present" in body:
            raise RemoteAlreadyAbsent(file_id)
        if code in (401, 403) and not any(f'"{c}"' in body for c in _B2_TOKEN_REFUSALS):
            raise RemoteDeleteDenied(code) from None
        raise
    if not isinstance(result, dict) or result.get("fileId") != file_id or result.get("fileName") != remote_name:
        raise BackupVersionError("B2 delete receipt did not confirm exact file id and name")


def _assert_real_dir(path, *, create=False):
    requested = os.path.abspath(os.fspath(path))
    # macOS exposes its standard temporary roots through fixed system aliases
    # (`/var` -> `/private/var`, and similarly `/tmp`/`/etc`).  A portable
    # lstat walk must not reject TemporaryDirectory merely because that FIRST
    # OS-owned component is an alias.  Resolve only these exact aliases and
    # only when realpath proves the expected canonical target; every other
    # user-controlled symlink in an operator root still fails below.
    path = requested
    for shown, target in (("/var", "/private/var"), ("/tmp", "/private/tmp"),
                          ("/etc", "/private/etc")):
        if requested == shown or requested.startswith(shown + os.path.sep):
            expected = target + requested[len(shown):]
            if os.path.realpath(shown) == target:
                path = expected
            break
    current = os.path.sep
    for piece in path.split(os.path.sep)[1:]:
        current = os.path.join(current, piece)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if not create:
                raise BackupVersionError("restore root is not a directory")
            # The parent was lstat-checked as a real directory in the prior
            # loop iteration.  Creating one component at a time therefore
            # cannot write through a deeper user-controlled symlink the way
            # os.makedirs(requested) can.
            try:
                os.mkdir(current, 0o700)
                info = os.lstat(current)
            except OSError as exc:
                raise BackupVersionError("could not create safe restore directory") from exc
        except OSError as exc:
            raise BackupVersionError("restore directory disappeared") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BackupVersionError("restore path traverses a symlink")
        if not stat.S_ISDIR(info.st_mode):
            raise BackupVersionError("restore path component is not a directory")
    # Preserve the operator's spelling for the final returned restore path;
    # only the validation walk needs the canonical macOS system alias.
    return requested


def _safe_destination(root, relative):
    root = _assert_real_dir(root, create=True)
    target = root
    parts = relative.split("/")
    for piece in parts[:-1]:
        target = os.path.join(target, piece)
        if not os.path.exists(target):
            os.mkdir(target, 0o700)
        info = os.lstat(target)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise BackupVersionError("restore destination traverses a symlink or non-directory")
    target = os.path.join(target, parts[-1])
    if os.path.lexists(target):
        raise BackupVersionError("restore destination already exists")
    return target


def _metadata_for_download(meta):
    if not isinstance(meta, dict):
        raise BackupVersionError("B2 download response lacks metadata")
    file_id = meta.get("file_id") or meta.get("fileId") or meta.get("x-bz-file-id")
    name = meta.get("remote_name") or meta.get("fileName") or meta.get("x-bz-file-name")
    sha1 = meta.get("sha1") or meta.get("contentSha1") or meta.get("x-bz-content-sha1")
    size = meta.get("size", meta.get("contentLength", meta.get("content-length")))
    try:
        size = int(size)
    except (TypeError, ValueError) as exc:
        raise BackupVersionError("B2 download response lacks a numeric size") from exc
    return {"file_id": file_id, "remote_name": name, "sha1": str(sha1 or "").lower(), "size": size}


def restore_version(version, destination_root, scratch_root, fetch_fn, *, prefix=None,
                    purged_paths=None):
    """Restore one exact verified B2 version without overwriting a local path.

    A path the retention deleted on purpose (``purged_files``, schema review
    №11) is refused before anything is downloaded: a deleted copy must not
    come back through a restore. ``purged_paths`` injects that set; None reads
    the database (an unreadable journal does not block a disaster-recovery
    restore — ``reapply_purged_files`` removes what came back on the next
    retention pass).

    Bytes first land in a separate scratch directory.  File-id/name/size/SHA-1
    response metadata and SHA-1/SHA-256 computed from those bytes must all
    match the immutable receipt before an atomic no-replace link creates the
    destination.  A collision, symlink, traversal, changed prefix, or manifest
    artifact is an error that leaves the target untouched.
    """
    _outbound_preflight()
    expected = _require_upload(version)
    # A name of the municipal installation carries `~` + the SHA-256 of the
    # bytes (`backup_lock.addressed_name`): the file goes back to its plain path.
    relative = safe_relative_name(backup_lock.plain_name(expected["remote_name"], expected["sha256"]),
                                  prefix=prefix)
    if purged_paths is None:
        try:
            purged_paths = db_backup.purged_file_paths()
        except Exception:  # noqa: BLE001 - see the docstring
            purged_paths = set()
    if relative in purged_paths:
        raise BackupVersionError(
            "this file was purged on purpose by the retention and is not restored")
    scratch_root = _assert_real_dir(scratch_root, create=True)
    destination_root = _assert_real_dir(destination_root, create=True)
    scratch_real = os.path.realpath(scratch_root)
    destination_real = os.path.realpath(destination_root)
    common = os.path.commonpath((scratch_real, destination_real))
    if common in {scratch_real, destination_real}:
        raise BackupVersionError("restore scratch and destination roots must be disjoint")
    response = fetch_fn(expected["file_id"])
    if (not isinstance(response, tuple) or len(response) != 2
            or not isinstance(response[1], (bytes, bytearray))):
        raise BackupVersionError("download fetcher must return (metadata, bytes)")
    meta = _metadata_for_download(response[0])
    payload = bytes(response[1])
    if (meta["file_id"] != expected["file_id"] or meta["remote_name"] != expected["remote_name"]
            or meta["size"] != expected["size"] or meta["sha1"] != expected["sha1"]
            or len(payload) != expected["size"]
            or hashlib.sha1(payload).hexdigest() != expected["sha1"]
            or hashlib.sha256(payload).hexdigest() != expected["sha256"]):
        raise BackupVersionError("downloaded bytes or metadata do not match immutable B2 receipt")
    # Do not create nested destination directories until remote bytes and all
    # receipt metadata have passed.  A failed download therefore leaves no
    # plausible partial restore tree behind.
    target = _safe_destination(destination_root, relative)
    scratch_fd = scratch_path = stage_path = None
    try:
        scratch_fd, scratch_path = tempfile.mkstemp(prefix=".restore-", dir=scratch_root)
        with os.fdopen(scratch_fd, "wb") as out:
            scratch_fd = None
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        # Copy only verified scratch bytes into the target filesystem, then
        # atomically link it into a previously-absent destination.  ``link``
        # fails on collision instead of replacing user data like os.replace.
        target_parent = os.path.dirname(target)
        stage_fd, stage_path = tempfile.mkstemp(prefix=".restore-stage-", dir=target_parent)
        with os.fdopen(stage_fd, "wb") as out, open(scratch_path, "rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.link(stage_path, target)
        os.unlink(stage_path)
        stage_path = None
        return target
    finally:
        if scratch_fd is not None:
            os.close(scratch_fd)
        for path in (scratch_path, stage_path):
            if path:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


def download_remote_version(file_id):
    """Production exact-id download primitive for an explicit restore drill."""
    _outbound_preflight()
    if not isinstance(file_id, str) or not file_id:
        raise BackupVersionError("exact B2 file id is required")
    if not data_backups.configured():
        raise BackupVersionError("B2 is not configured")
    auth = data_backups._authorize()
    body = json.dumps({"fileId": file_id}).encode()
    download_url = auth.get("downloadUrl")
    if not isinstance(download_url, str) or not download_url.rstrip("/"):
        raise BackupVersionError("B2 authorization response lacks downloadUrl")
    request = urllib.request.Request(
        download_url.rstrip("/") + "/b2api/v2/b2_download_file_by_id", data=body,
        headers={"Authorization": auth["authorizationToken"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise BackupVersionError("B2 restore payload exceeds the configured backup bound")
        headers = {key.lower(): value for key, value in response.headers.items()}
    return headers, payload
