"""Snapshot inventory and strict, no-overwrite restoration of an authenticated tar.

No cloud, DB or UI dependency. Call build only AFTER the database dump completes.
All source files are opened through no-follow directory descriptors. Omissions
are data in the manifest, never quiet 'continue' statements.
"""
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

from app.core import backup_settings, config
from app.core import paths

ROOT = Path(paths.PROJECT_ROOT).resolve()
MAX_FILE = 200 * 1024 * 1024
ALLOWLIST = {"legacy_backup_manifest": "Obsolete B2 producer cache; durable receipts are in PostgreSQL",
             "config_write_staging": "Ephemeral canonical-env writer staging may contain the KEK; regenerate, never archive",
             "recovery_key_excluded": "KEK and retained KEKs must be held outside the encrypted archive"}


class ArchiveError(ValueError):
    pass


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_name(name):
    if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or "\x00" in name
            or any(p in {"", ".", ".."} for p in name.split("/"))):
        raise ArchiveError("Unsafe archive path")
    return name


def safe_root(path):
    """Reject symlink components; allow only macOS's fixed /tmp,/var,/etc aliases."""
    path = Path(os.path.abspath(path))
    if os.uname().sysname == "Darwin":
        for alias in ("/tmp", "/var", "/etc"):
            if str(path) == alias or str(path).startswith(alias + "/"):
                if os.path.islink(alias) and os.path.realpath(alias) == "/private" + alias:
                    path = Path("/private" + str(path))
                break
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ArchiveError("Symlink in backup path")
    return path


def _directory_fd(path):
    """Walk components with no-follow dirfds, including root-open races."""
    path = safe_root(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def release_info():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        commit = result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        commit = None
    requirements = ROOT / "requirements.txt"
    return {"git_commit": os.environ.get("SOURCE_COMMIT") or os.environ.get("COOLIFY_GIT_COMMIT_SHA") or commit,
            "image_digest": os.environ.get("TURNOS_IMAGE_DIGEST") or os.environ.get("IMAGE_DIGEST"),
            "image_tag": os.environ.get("TURNOS_IMAGE_TAG"),
            "app_version": os.environ.get("TURNOS_VERSION"),
            "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest()}


def reference_names(database, roots):
    output = []
    for value in database.get("references", []):
        if not value:
            continue
        value = str(value)
        mapped = None
        for label, root in roots.items():
            try:
                mapped = label + "/" + str(Path(value).relative_to(root)).replace(os.sep, "/")
                break
            except ValueError:
                pass
        if mapped is None:
            # Stored referrals are commonly relative to the repository root.
            candidate = value.lstrip("/")
            if candidate.startswith("data/"):
                mapped = candidate
            else:
                mapped = "unmapped-reference/" + value
        output.append(mapped)
    return sorted(set(output))


def _state(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _initial_manifest(data_roots, database, tool_versions, archive_id):
    return {"format": 1, "producer": "human-rounds.backup_full.v1", "archive_id": archive_id or str(uuid.uuid4()),
            "created_at": timestamp(), "database_snapshot_at": database["snapshot_at"],
            "files_copy_started_at": timestamp(), "release": release_info(),
            "alembic_head": database["alembic_head"],
            "expected_alembic_head": database.get("expected_alembic_head", database["alembic_head"]),
            "row_counts": database.get("counts", {}),
            "tools": tool_versions or {}, "files": [], "directories": [], "skipped": [], "changed_during_copy": [],
            "sources": {k: str(v) for k, v in data_roots.items()},
            "excluded": dict(ALLOWLIST)}


def _source_roots(manifest, data_roots, private_packs, env_file):
    """The labelled roots to archive; also records every source path in the manifest."""
    roots = dict(data_roots)
    if private_packs:
        roots["private-packs"] = Path(private_packs)
    manifest["sources"].update({"private-packs": str(private_packs) if private_packs else None,
                                "config/installation.env": str(env_file)})
    return roots


def _env_aliases(env_file):
    """The canonical env by absolute path, real path and (dev, ino) identity."""
    env_abs = Path(os.path.abspath(env_file))
    env_real = Path(os.path.realpath(env_file))
    try:
        # Metadata only: identify regular aliases/hardlinks of the canonical
        # env even if the canonical pathname itself must be skipped as a link.
        env_stat = os.stat(env_file)
        env_identity = (env_stat.st_dev, env_stat.st_ino)
    except OSError:
        env_identity = None
    return env_abs, env_real, env_identity


class _Copier:
    """Copies the permitted files into one open tar and records every omission in the manifest."""

    def __init__(self, tar, manifest, env_aliases):
        self.tar, self.manifest = tar, manifest
        self.env_abs, self.env_real, self.env_identity = env_aliases

    def skip(self, name, reason):
        self.manifest["skipped"].append({"path": name, "reason": reason, "allowed": reason in ALLOWLIST})

    def add(self, name, inp, length):
        safe_name(name)
        digest = hashlib.sha256()
        class Reader:
            def read(self, size=-1):
                block = inp.read(size)
                digest.update(block)
                return block
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.mtime = length, 0o600, 0
        self.tar.addfile(info, Reader())
        self.manifest["files"].append({"path": name, "size": length, "sha256": digest.hexdigest()})

    def snapshot(self, parent, name, member, *, sanitize=False):
        # A seekable temp snapshot makes a changing file retryable without
        # leaving a duplicate/untrusted tar member in the output.
        for attempt in range(2):
            tmp = tempfile.TemporaryFile()
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, "rb") as inp:
                    before = os.fstat(inp.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        self.skip(member, "unsafe_path")
                        tmp.close()
                        return None
                    if before.st_size > MAX_FILE:
                        self.skip(member, "size_cap")
                        tmp.close()
                        return None
                    left = MAX_FILE + 1
                    while left and (block := inp.read(min(1024 * 1024, left))):
                        tmp.write(block)
                        left -= len(block)
                    after, listed = os.fstat(inp.fileno()), os.stat(name, dir_fd=parent, follow_symlinks=False)
                unchanged = _state(before) == _state(after) == _state(listed) and tmp.tell() == after.st_size
                if not unchanged:
                    if member not in self.manifest["changed_during_copy"]:
                        self.manifest["changed_during_copy"].append(member)
                    tmp.close()
                    if attempt == 1:
                        self.skip(member, "changed_during_copy")
                    continue
                size = tmp.tell()
                tmp.seek(0)
                if sanitize:
                    text = tmp.read().decode("utf-8")
                    clean = backup_settings.sanitize_env(text).encode()
                    tmp.seek(0)
                    tmp.truncate()
                    tmp.write(clean)
                    tmp.seek(0)
                    size = len(clean)
                    if clean != text.encode():
                        self.skip(member, "recovery_key_excluded")
                return tmp, size
            except (OSError, UnicodeError, ValueError):
                tmp.close()
                self.skip(member, "unreadable")
                return None
        return None

    def walk(self, fd, label, root_path):
        safe_name(label)
        self.manifest["directories"].append(label)
        try:
            names = sorted(os.listdir(fd))
        except OSError:
            self.skip(label, "unreadable")
            return
        for name in names:
            member = label + "/" + name
            try:
                safe_name(member)
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    self.skip(member, "symlink")
                elif stat.S_ISDIR(st.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        self.walk(child, member, root_path / name)
                    finally:
                        os.close(child)
                elif name.startswith(".backup-manifest"):
                    self.skip(member, "legacy_backup_manifest")
                elif name.startswith(".env-") and name.endswith(".tmp"):
                    self.skip(member, "config_write_staging")
                else:
                    is_env = ((st.st_dev, st.st_ino) == self.env_identity or root_path / name == self.env_abs
                              or root_path / name == self.env_real
                              or config.is_env_file_name(name))
                    snap = self.snapshot(fd, name, member, sanitize=is_env)
                    if snap:
                        inp, length = snap
                        with inp:
                            self.add(member, inp, length)
            except (OSError, ArchiveError):
                self.skip(member, "unsafe_path")


def _copy_roots(copier, roots, target):
    for label, root in roots.items():
        try:
            root = safe_root(root)
            if safe_root(ROOT).is_relative_to(root) or safe_root(target).is_relative_to(root):
                copier.skip(label, "unsafe_path")
                continue
            fd = _directory_fd(root)
            try:
                copier.walk(fd, label, root)
            finally:
                os.close(fd)
        except (OSError, ArchiveError):
            copier.skip(label, "unreadable")


def _copy_installation_env(copier, env_file):
    try:
        env_abs = safe_root(env_file)
        parent = _directory_fd(env_abs.parent)
        try:
            snap = copier.snapshot(parent, env_abs.name, "config/installation.env", sanitize=True)
            if snap:
                inp, length = snap
                with inp:
                    copier.add("config/installation.env", inp, length)
        finally:
            os.close(parent)
    except (OSError, ArchiveError):
        copier.skip("config/installation.env", "unreadable")


def _absent_at_source(name, roots):
    """True when a referenced archive name has no file at all on the source disk.

    Such a file was lost before this run, so no archive can hold it: it is
    reported, but it does not make every archive
    partial. A name outside every root, or a file that exists but was not copied
    (symlink, size cap, unreadable), is never "absent" — that stays partial.
    """
    label, _, rest = name.partition("/")
    if label not in roots or not rest:
        return False
    root = os.path.normpath(str(roots[label]))
    path = os.path.normpath(os.path.join(root, rest))
    if not path.startswith(root + os.sep):
        return False
    return not os.path.lexists(path)


def _close_manifest(copier, database, roots):
    manifest = copier.manifest
    present = {f["path"] for f in manifest["files"]}
    refs = reference_names(database, roots)
    not_copied = [p for p in refs if p not in present]
    absent = [p for p in not_copied if _absent_at_source(p, roots)]
    manifest["reference_check"] = {"checked": len(refs), "missing": [p for p in not_copied if p not in absent],
                                   "absent_at_source": absent}
    manifest["files_copy_finished_at"] = timestamp()
    if manifest["alembic_head"] != manifest["expected_alembic_head"]:
        copier.skip("database", "schema_head_mismatch")
    if not manifest["release"]["git_commit"] and not manifest["release"]["image_digest"]:
        copier.skip("release", "release_identity_unavailable")
    manifest["status"] = "partial" if (manifest["reference_check"]["missing"] or manifest["changed_during_copy"]
                                            or any(not r["allowed"] for r in manifest["skipped"])) else "complete"


def _add_manifest_member(tar, manifest):
    raw = json.dumps(manifest, sort_keys=True, indent=2).encode()
    info = tarfile.TarInfo("manifest.json")
    info.size, info.mode = len(raw), 0o600
    tar.addfile(info, io.BytesIO(raw))


def build(target, dump, *, data_roots, private_packs, env_file, database, tool_versions=None, archive_id=None):
    manifest = _initial_manifest(data_roots, database, tool_versions, archive_id)
    roots = _source_roots(manifest, data_roots, private_packs, env_file)
    env_aliases = _env_aliases(env_file)
    with tarfile.open(target, "w") as tar:
        copier = _Copier(tar, manifest, env_aliases)
        with Path(dump).open("rb") as inp:
            copier.add("database.dump", inp, Path(dump).stat().st_size)
        _copy_roots(copier, roots, target)
        _copy_installation_env(copier, env_file)
        _close_manifest(copier, database, roots)
        _add_manifest_member(tar, manifest)
    return manifest


def unpack_verified(archive, destination):
    """Only call with authenticated plaintext. Validate all bytes before placement.

    Destination must be absent; staging is a private sibling on the same
    filesystem. Never use tar.extract(all) or trust archive permissions/links.
    """
    destination = safe_root(destination)
    if destination.exists():
        raise ArchiveError("Restore destination must not exist")
    if not destination.parent.is_dir():
        raise ArchiveError("Restore parent directory must exist")
    with tempfile.TemporaryDirectory(prefix=".hr-restore-", dir=destination.parent) as scratch:
        scratch = Path(scratch)
        seen, files, manifest = set(), {}, None
        with tarfile.open(archive, "r:") as tar:
            for member in tar:
                safe_name(member.name)
                if not member.isfile() or member.name in seen:
                    raise ArchiveError("Restore contains links, non-files or duplicate entries")
                seen.add(member.name)
                if member.name == "manifest.json":
                    if member.size > 64 * 1024 * 1024:
                        raise ArchiveError("Manifest exceeds restore bound")
                    inp = tar.extractfile(member)
                    with inp:
                        manifest = json.load(inp)
                    continue
                target = scratch / member.name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = hashlib.sha256()
                length = 0
                with tar.extractfile(member) as inp, target.open("xb") as out:
                    os.chmod(target, 0o600)
                    while block := inp.read(1024 * 1024):
                        out.write(block)
                        digest.update(block)
                        length += len(block)
                files[member.name] = {"path": member.name, "size": length, "sha256": digest.hexdigest()}
        if not isinstance(manifest, dict) or manifest.get("producer") != "human-rounds.backup_full.v1":
            raise ArchiveError("Backup manifest missing or wrong producer")
        expected = manifest.get("files")
        if (not isinstance(expected, list) or len(expected) != len(files)
                or any(not isinstance(row, dict) or files.get(row.get("path")) != row for row in expected)
                or len({row["path"] for row in expected}) != len(expected) or "database.dump" not in files):
            raise ArchiveError("Backup manifest file hash or inventory mismatch")
        directories = manifest.get("directories", [])
        if not isinstance(directories, list) or len(directories) != len(set(directories)):
            raise ArchiveError("Invalid backup directory inventory")
        for name in directories:
            safe_name(name)
            if name in files:
                raise ArchiveError("Backup directory collides with a file")
            (scratch / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        (scratch / "manifest.json").write_text(json.dumps(manifest, indent=2))
        os.chmod(scratch / "manifest.json", 0o600)
        # mkdir is the no-replace reservation; rename into an already-existing
        # operator path could otherwise overwrite an empty directory in a race.
        destination.mkdir(mode=0o700)
        try:
            for item in scratch.iterdir():
                os.rename(item, destination / item.name)
        except BaseException:
            # Keep partial output for diagnosis; never clear an operator target.
            raise
    return manifest
