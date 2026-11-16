"""Installation-independent encrypted PostgreSQL + files backups.

All cloud operations are explicit, kill-switch guarded and serialized with the
same session advisory lock as the run journal. No provider is contacted at import.
"""
import contextlib
import datetime as dt
import hashlib
import hmac
import importlib.metadata
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy import func, not_, select, text

from app.core import backup_crypto as crypto, backup_lock, backup_settings as settings, config, db, outbound_guard, sqlcore
from app.core import paths
from app.core import db_schema
from app.core import launch_signals
from app.core.backup_journal import Store
from app.core.schema_model import alembic_version, booking_requests, catalog, metadata
from app.ops import backup_archive as archive

ROOT = Path(paths.PROJECT_ROOT).resolve()
timestamp = archive.timestamp
KEY_TABLES = ("booking_requests", "catalog", "users", "operators", "appointment_events")


class BackupError(RuntimeError):
    pass


def preflight():
    if outbound_guard.requested():
        raise BackupError("TURNOS_OUTBOUND_KILL=1 forbids backup operations")


def _tool(name):
    path = shutil.which(name)
    if not path:
        raise BackupError(name + " is missing; install PostgreSQL client tools")
    return path


def _pg_environment(url):
    # Do not pass connection URLs/passwords as command-line arguments or echo
    # stderr: libpq and pg tools can include credentials in error messages.
    names = {"dbname": "PGDATABASE", "user": "PGUSER", "host": "PGHOST", "hostaddr": "PGHOSTADDR",
             "port": "PGPORT", "password": "PGPASSWORD", "sslmode": "PGSSLMODE",
             "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY", "sslrootcert": "PGSSLROOTCERT",
             "application_name": "PGAPPNAME", "options": "PGOPTIONS", "service": "PGSERVICE",
             "passfile": "PGPASSFILE", "target_session_attrs": "PGTARGETSESSIONATTRS",
             "channel_binding": "PGCHANNELBINDING", "connect_timeout": "PGCONNECT_TIMEOUT"}
    values = conninfo_to_dict(url)
    if set(values) - set(names):
        raise BackupError("PostgreSQL connection has unsupported backup client options")
    env = {"PATH": os.environ.get("PATH", os.defpath), "PGCONNECT_TIMEOUT": "10",
           "HOME": os.environ.get("HOME", "/nonexistent")}
    env.update({names[k]: v for k, v in values.items()})
    return env


def _pg_run(argv, url):
    try:
        result = subprocess.run(argv, env=_pg_environment(url), capture_output=True, timeout=21600)
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError(Path(argv[0]).name + " could not finish") from None
    if result.returncode:
        raise BackupError(Path(argv[0]).name + " failed; check PostgreSQL access and client/server versions")
    return result


def _scalar(conn, stmt):
    """First column of the first row of `stmt`, run on a plain psycopg connection
    (this module's connections are not the pool's: their rows are tuples)."""
    with conn.cursor() as cur:
        return sqlcore.run(cur, stmt).fetchone()[0]


def _head(conn):
    return _scalar(conn, select(alembic_version.c.version_num))


def _counts(conn):
    counts = {}
    for table in KEY_TABLES:
        if not _scalar(conn, select(func.to_regclass(table))):
            raise BackupError("Required application table is missing")
        counts[table] = _scalar(conn, select(func.count()).select_from(metadata.tables[table]))
    return counts


def dump_database(target, url):
    pg_dump = _tool("pg_dump")
    if not url:
        raise BackupError("DATABASE_URL is required")
    with psycopg.connect(url, connect_timeout=10) as conn:
        with conn.cursor() as cur:  # the first statement of the transaction
            sqlcore.run(cur, text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        snapshot = _scalar(conn, select(func.pg_export_snapshot()))
        captured_at = timestamp()
        head = _head(conn)
        from alembic.script import ScriptDirectory
        expected_head = ScriptDirectory.from_config(db_schema._alembic_config(url)).get_current_head()
        counts = _counts(conn)
        with conn.cursor() as cur:
            references = [r[0] for r in sqlcore.run(cur, select(booking_requests.c.referral_path).where(
                booking_requests.c.referral_path.is_not(None))).fetchall()]
        # Catalog photo overrides can name local file URLs as well as bundled
        # or remote pictures. Only local persistent data paths are references.
        def visit(value):
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str) and (value.startswith(("/data/", "data/"))):
                references.append(value.split("?", 1)[0])
        with conn.cursor() as cur:
            for row in sqlcore.run(cur, select(catalog.c.doc).where(
                    catalog.c.type.in_(("medicos", "centros")), not_(catalog.c.deleted))).fetchall():
                visit(row[0])
        _pg_run([pg_dump, "--format=custom", "--no-owner", "--no-acl", "--snapshot=" + snapshot,
                 "--file=" + str(target)], url)
        version = subprocess.run([pg_dump, "--version"], capture_output=True, text=True, timeout=5)
        return {"snapshot_at": captured_at, "dump_finished_at": timestamp(), "alembic_head": head, "expected_alembic_head": expected_head,
                "counts": counts, "references": references,
                "tools": {"pg_dump": version.stdout.strip(), "python": os.sys.version.split()[0],
                          "cryptography": importlib.metadata.version("cryptography")}}


def _database_identity(url):
    values = conninfo_to_dict(url)
    return tuple(values.get(k, "") for k in ("host", "hostaddr", "port", "dbname"))


def restore_database(dump, target_url, *, live_url=None):
    """Explicit empty-target only. Never clean, replace or stamp a database."""
    pg_restore = _tool("pg_restore")
    if not target_url or (live_url and _database_identity(target_url) == _database_identity(live_url)):
        raise BackupError("Restore database must be explicitly different from the live database")
    with psycopg.connect(target_url, connect_timeout=10) as conn:
        # Includes non-table objects, so an apparently empty database with
        # functions/views is never considered a disposable destination.
        # The system catalogs are not part of the schema model: this one statement is SQL text.
        exists = _scalar(conn, text("""SELECT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
            UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema'))"""))
        if exists:
            raise BackupError("Restore database must be empty; existing objects were preserved")
        server_version = _scalar(conn, text("SELECT current_setting('server_version_num')::int"))
    if server_version < 170000:
        _restore_through_sql(pg_restore, dump, target_url)
        return
    _pg_run([pg_restore, "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
             "--dbname=" + conninfo_to_dict(target_url).get("dbname", ""), str(dump)], target_url)


# pg_dump 17 opens every dump with this line, even when the server it dumped is older; a server
# before 17 rejects it. It is the only statement dropped, and only for such servers.
_TRANSACTION_TIMEOUT_SET = "SET transaction_timeout = 0;"


def _restore_through_sql(pg_restore, dump, target_url):
    """Restore into a server older than the dump client: pg_restore writes the SQL text, the one
    unsupported line is dropped, and psql applies it all-or-nothing, stopping at the first error."""
    psql = _tool("psql")
    with tempfile.TemporaryDirectory(prefix="hr-restore-") as scratch:
        raw, clean = Path(scratch) / "dump.sql", Path(scratch) / "dump-compatible.sql"
        _pg_run([pg_restore, "--no-owner", "--no-acl", "--file=" + str(raw), str(dump)], target_url)
        with raw.open("rb") as src, clean.open("wb") as dst:  # bytes: the dump's encoding is never guessed
            for line in src:
                if line.rstrip(b"\r\n") != _TRANSACTION_TIMEOUT_SET.encode():
                    dst.write(line)
        _pg_run([psql, "--no-psqlrc", "--quiet", "-v", "ON_ERROR_STOP=1", "--single-transaction",
                 "--dbname=" + conninfo_to_dict(target_url).get("dbname", ""), "--file=" + str(clean)], target_url)


def scratch_restore(directory, manifest, scratch_url, *, live_url):
    """Create/drop only one uniquely named DB owned by THIS invocation.

    scratch_url is an explicit administrative connection to the operator's test
    cluster. It is never a live-DB default. A failed test DB is retained.
    """
    if not scratch_url:
        raise BackupError("Scratch database is unconfigured")
    name = "hr_backup_test_" + uuid.uuid4().hex
    target = make_conninfo(scratch_url, dbname=name)
    with psycopg.connect(scratch_url, autocommit=True, connect_timeout=10) as admin:
        quoted = sqlcore.quote_identifier(name)  # an identifier cannot be a parameter
        with admin.cursor() as cur:
            sqlcore.run(cur, text(f"CREATE DATABASE {quoted} TEMPLATE template0"))
        try:
            restore_database(Path(directory) / "database.dump", target, live_url=live_url)
            with psycopg.connect(target, connect_timeout=10) as conn:
                head = _head(conn)
                counts = _counts(conn)
            if (head != manifest["alembic_head"] or head != manifest.get("expected_alembic_head", manifest["alembic_head"])
                    or counts != manifest["row_counts"]):
                raise BackupError("Scratch restore schema revision or row counts do not match")
            # unpack_verified already checked every file, stronger than a sample.
            with admin.cursor() as cur:
                sqlcore.run(cur, text(f"DROP DATABASE {quoted}"))
        except Exception:
            raise BackupError("Scratch restore failed; retained database " + name) from None
    return {"database": name, "alembic_head": head, "row_counts": counts, "files": "all_hashes_matched"}


def _ownership(manifest, keys, *, sign=False):
    signature = manifest.pop("signature", None)
    try:
        raw = b"HRBACK-manifest-v1:" + crypto.canonical(manifest)
        if sign:
            signature = {ident: hmac.digest(key, raw, "sha256").hex() for ident, key in keys.items()}
        elif (not isinstance(signature, dict) or not any(
                ident in signature and hmac.compare_digest(signature[ident], hmac.digest(key, raw, "sha256").hex())
                for ident, key in keys.items())):
            raise BackupError("Manifest ownership signature does not match")
    finally:
        if signature:
            manifest["signature"] = signature
    return signature


def _sign_tar(path, manifest, keys):
    # Only the final manifest member changes; avoid loading the database or
    # files into memory. Rebuild into a second private staging file.
    _ownership(manifest, keys, sign=True)
    import io
    import tarfile
    replacement = path.with_suffix(".signed")
    with tarfile.open(path, "r:") as src, tarfile.open(replacement, "w") as dst:
        for member in src:
            if member.name != "manifest.json":
                with src.extractfile(member) as inp:
                    dst.addfile(member, inp)
        raw = json.dumps(manifest, sort_keys=True, indent=2).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size, info.mode = len(raw), 0o600
        dst.addfile(info, io.BytesIO(raw))
    os.replace(replacement, path)


def restore_local(encrypted, destination, keys, *, database_url=None, live_url=None):
    if database_url:
        preflight()
    destination = archive.safe_root(destination)
    if destination.exists():
        raise BackupError("Restore destination must not exist")
    with tempfile.TemporaryDirectory(prefix=".hr-decrypt-", dir=destination.parent) as root:
        plain = Path(root, "archive.tar")
        with Path(encrypted).open("rb") as inp, plain.open("xb") as out:
            crypto.decrypt(inp, out, keys)
        staged = Path(root, "verified")
        manifest = archive.unpack_verified(plain, staged)
        # Offline recovery authenticates the complete AEAD stream/header and
        # file inventory. The extra installation ownership MAC is a retention
        # admission check, not a reason to reject a correctly rewrapped archive.
        if database_url:
            restore_database(staged / "database.dump", database_url, live_url=live_url)
        # Reserve an absent destination before moving validated content.
        destination.mkdir(mode=0o700)
        for item in staged.iterdir():
            os.rename(item, destination / item.name)
        return manifest


def verify_remote(row, cloud, keys, root):
    root = Path(root)
    encrypted = root / (uuid.uuid4().hex + ".hrb")
    cloud.download(row["archive_key"], encrypted)
    with encrypted.open("rb") as inp:
        digest = hashlib.file_digest(inp, "sha256").hexdigest()
    if encrypted.stat().st_size != row["size"] or digest != row["sha256"]:
        raise BackupError("Downloaded backup does not match its upload receipt")
    target = root / uuid.uuid4().hex
    manifest = restore_local(encrypted, target, keys)
    _ownership(manifest, keys)
    if manifest["archive_id"] != row["id"]:
        raise BackupError("Downloaded backup archive id mismatch")
    return manifest, target


def retention_plan(rows, cfg):
    rows = sorted((r for r in rows if r.get("uploaded") and not r.get("deleted_at")), key=lambda r: r["started_at"], reverse=True)
    tested = [r for r in rows if r.get("restore_tested") and r.get("status") == "complete"]
    if len(tested) < 3:
        return []
    protected = {r["id"] for r in tested[:3]}
    for period, count_key in (("daily", "TURNOS_BACKUP_RETAIN_DAILY"), ("weekly", "TURNOS_BACKUP_RETAIN_WEEKLY"), ("monthly", "TURNOS_BACKUP_RETAIN_MONTHLY")):
        buckets = set()
        for row in rows:
            date = dt.datetime.fromisoformat(row["started_at"])
            bucket = date.date() if period == "daily" else date.isocalendar()[:2] if period == "weekly" else (date.year, date.month)
            if bucket not in buckets:
                if len(buckets) >= int(cfg[count_key]):
                    break
                buckets.add(bucket)
                protected.add(row["id"])
    newest_tested = tested[0]["started_at"]
    return [r for r in rows if r["id"] not in protected and r["started_at"] < newest_tested
            and r.get("archive_key", "").startswith(cfg["TURNOS_BACKUP_S3_PREFIX"] + "hr-full-")
            and r["archive_key"].endswith(".hrb")]


def apply_retention(plan, cfg, cloud, *, keys, store=None):
    if cfg["TURNOS_BACKUP_RETENTION_APPLY"] != "1":
        return []
    if backup_lock.locked():
        # The municipal installation deletes no backup: the switch is
        # reported loudly, the backup that called this still succeeds.
        backup_lock.warn("rotation of the full backups (TURNOS_BACKUP_RETENTION_APPLY=1)")
        return []
    preflight()
    if store is None or getattr(store, "conn", None) is None:
        raise BackupError("Applied retention requires the journal advisory lock")
    allowed = {r["id"] for r in retention_plan(store.list(), cfg)}
    if any(r["id"] not in allowed for r in plan):
        raise BackupError("Retention evidence changed; regenerate the plan")
    deleted = []
    for row in plan:
        with tempfile.TemporaryDirectory(prefix="hr-retention-") as root:
            manifest, _ = verify_remote(row, cloud, keys, root)
            expected = cfg["TURNOS_BACKUP_S3_PREFIX"] + "hr-full-" + manifest["archive_id"] + ".hrb"
            if row["archive_key"] != expected:
                raise BackupError("Retention object name does not match signed manifest")
            cloud.delete(row["archive_key"])
            row = dict(row, deleted_at=timestamp())
            if store:
                store.save(row)
            deleted.append(row["id"])
    return deleted


def run(*, cfg=None, cloud=None, store=None, dump_fn=None, roots=None, private_packs=None, env_file=None, scheduled=False,
        on_start=None):
    preflight()
    cfg = cfg or settings.snapshot()
    settings.validate(cfg)
    if not settings.configured(cfg):
        raise BackupError("Encrypted backups are unconfigured")
    keys = settings.keys()
    cloud, store = cloud or settings.client(cfg), store or Store()
    dump_fn = dump_fn or dump_database
    live_url = db._db_url()
    if roots is None:
        data = Path(config.get("TURNOS_DATA_DIR") or ROOT / "data")
        roots = {"data": data}
        if data.absolute() != (ROOT / "data").absolute():
            roots["legacy-data"] = ROOT / "data"
        legacy_pack = config.get("TURNOS_WEBDATA_DIR")
        if legacy_pack:
            roots["legacy-webdata"] = Path(legacy_pack)
        private_packs = config.get("TURNOS_PRIVATE_PACKS_DIR") or None
    env_file = env_file or config.env_file_path()
    with store.lock():
        rows = store.list()
        if scheduled and rows:
            # Last attempt, not last success: failed invocations wait for the
            # daily cadence; manual run is the explicit immediate retry.
            last = dt.datetime.fromisoformat(rows[0]["started_at"])
            if dt.datetime.now(dt.timezone.utc) - last < dt.timedelta(minutes=int(cfg["TURNOS_BACKUP_INTERVAL_MIN"])):
                return {"skipped": "not_due"}
        row = {"id": str(uuid.uuid4()), "started_at": timestamp(), "status": "running",
               "uploaded": False, "integrity_verified": False, "restore_tested": False,
               "destination_host": __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(cfg["TURNOS_BACKUP_S3_ENDPOINT"]).hostname}
        row["archive_key"] = cfg["TURNOS_BACKUP_S3_PREFIX"] + "hr-full-" + row["id"] + ".hrb"
        store.save(row)
        if on_start is not None:
            on_start()   # a run really begins here (not a skipped "not due" pass): the launch signal's check-in
        def record_multipart(key, ident):
            row.setdefault("multipart_uploads", []).append({"key": key, "upload_id": ident})
            store.save(row)
        cloud.on_multipart_started = record_multipart
        try:
            with tempfile.TemporaryDirectory(prefix="hr-backup-") as scratch:
                root = Path(scratch)
                dump, plain, encrypted = root / "database.dump", root / "archive.tar", root / "archive.hrb"
                database = dump_fn(dump, live_url)  # MUST precede every file read
                manifest = archive.build(plain, dump, data_roots=roots, private_packs=private_packs,
                                         env_file=env_file, database=database, tool_versions=database.get("tools"), archive_id=row["id"])
                manifest["dump_finished_at"] = database.get("dump_finished_at", timestamp())
                _sign_tar(plain, manifest, keys)
                with plain.open("rb") as inp, encrypted.open("xb") as out:
                    crypto.encrypt(inp, out, keys, archive_id=row["id"])
                row["size"] = encrypted.stat().st_size
                with encrypted.open("rb") as inp:
                    row["sha256"] = hashlib.file_digest(inp, "sha256").hexdigest()
                etag = cloud.upload(row["archive_key"], encrypted)
                head = cloud.head(row["archive_key"])
                if head["size"] != row["size"] or not head.get("etag") or head["etag"] != etag:
                    raise BackupError("Remote backup HEAD size or ETag mismatch")
                row.update(uploaded=True, uploaded_at=timestamp(), etag=head["etag"])
                store.save(row)
                # Full download/decrypt/hash validation for ALL sizes. This is
                # deliberately stronger than sampled checks above 2 GiB.
                verified_manifest, restored = verify_remote(row, cloud, keys, root)
                row.update(integrity_verified=True, integrity_verified_at=timestamp(),
                           status=manifest["status"], integrity_mode="complete",
                           skipped=manifest["skipped"], reference_check=manifest["reference_check"])
                store.save(row)
                tested = next((r for r in rows if r.get("restore_tested") and not r.get("deleted_at")), None)
                due = not tested or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(tested["restore_tested_at"])
                                     >= dt.timedelta(days=int(cfg["TURNOS_BACKUP_RESTORE_INTERVAL_DAYS"])))
                if due and cfg["TURNOS_BACKUP_SCRATCH_DATABASE_URL"]:
                    try:
                        evidence = scratch_restore(restored, verified_manifest, cfg["TURNOS_BACKUP_SCRATCH_DATABASE_URL"], live_url=live_url)
                        row.update(restore_tested=True, restore_tested_at=timestamp(), restore_evidence=evidence)
                    except BackupError:
                        row["restore_error"] = "scratch_restore_failed"
                elif due:
                    row["restore_error"] = "scratch_database_unconfigured"
                row["finished_at"] = timestamp()
                store.save(row)
                plan = retention_plan(store.list(), cfg)
                row["retention_plan"] = [r["archive_key"] for r in plan]
                apply_retention(plan, cfg, cloud, keys=keys, store=store)
                # Separate cleanup: only old incomplete uploads under our name
                # namespace, never other S3 clients' uploads.
                owned = {(u["key"], u["upload_id"]) for r in store.list() for u in r.get("multipart_uploads", [])}
                cloud.cleanup_incomplete(cfg["TURNOS_BACKUP_S3_PREFIX"], before=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=2),
                                         owned_uploads=owned)
                store.save(row)
                return row
        except Exception as exc:
            row.update(status="failed", finished_at=timestamp(),
                       error="pg_dump_missing" if isinstance(exc, BackupError) and str(exc).startswith("pg_dump is missing") else "backup_run_failed")
            store.save(row)
            if isinstance(exc, BackupError):
                raise
            raise BackupError("Backup failed; inspect the backup status and PostgreSQL tools") from None


def verify(*, restore_test=False):
    preflight()
    cfg, keys, store = settings.snapshot(), settings.keys(), Store()
    cloud = settings.client(cfg)
    with store.lock():
        row = next((r for r in store.list() if r.get("uploaded") and not r.get("deleted_at")), None)
        if not row:
            raise BackupError("No uploaded backup to verify")
        with tempfile.TemporaryDirectory(prefix="hr-verify-") as scratch:
            try:
                manifest, target = verify_remote(row, cloud, keys, scratch)
            except Exception:
                row.update(integrity_verified=False, status="failed", error="integrity_verification_failed")
                store.save(row)
                raise BackupError("Backup integrity verification failed") from None
            row.update(integrity_verified=True, integrity_verified_at=timestamp(), integrity_mode="complete",
                       status=manifest["status"], error=None)
            if restore_test:
                try:
                    evidence = scratch_restore(target, manifest, cfg["TURNOS_BACKUP_SCRATCH_DATABASE_URL"], live_url=db._db_url())
                    row.update(restore_tested=True, restore_tested_at=timestamp(), restore_evidence=evidence, restore_error=None)
                except Exception:
                    row.update(restore_tested=False, restore_error="scratch_restore_failed")
                    store.save(row)
                    raise BackupError("Backup restore test failed; inspect the isolated scratch cluster") from None
            store.save(row)
            return row


def _attempt():
    """One pass of the legacy thread: a scheduled run when backups are configured. A failure is journaled by
    `run` itself («Sistema» reads it); the code is the launch signal (core/launch_signals.py)."""
    try:
        if settings.configured():
            run(scheduled=True)
    except Exception:
        launch_signals.handled("backup_full_failed")  # journal/System owns the detail; the code is the signal


def start():
    """Legacy thread topology; worker topology owns its registry job instead."""
    from app.core import queue_infra
    if queue_infra.worker_mode() or not settings.configured():
        return None
    def loop():
        # Keep disabled/changed settings live; no cloud at startup/import.
        import time
        time.sleep(60)
        while True:
            _attempt()
            time.sleep(60)
    thread = threading.Thread(target=loop, name="backup-full", daemon=True)
    thread.start()
    return thread
