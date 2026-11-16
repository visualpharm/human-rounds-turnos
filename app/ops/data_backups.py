"""Off-box backup of the data/ volume to Backblaze B2.

The `data/` volume (medical referrals, profile/DNI photos, MSD snapshot)
needs an off-box copy in addition to Postgres's daily dump.  With no worker
topology, the legacy daemon thread in ``server.py`` sweeps it every
``TURNOS_BACKUP_INTERVAL_MIN`` minutes (default: six hours); with a worker,
the durable queue bridge schedules this producer instead.  Both paths use
the same B2 JSON API and journal semantics here.

Each accepted upload is recorded in Postgres's ``backup_journal``. ``path``
is the journal table's primary key; the accepted-byte identity recorded for
that path is the SHA-256 and size of the exact payload passed to B2, not a
local manifest or a path's timestamps. Size and ``mtime_ns`` are only a cheap
prefilter: if they change, the file is read and hashed before deciding whether
to upload. A stat-only change whose bytes still match refreshes the prefilter
metadata without producing a new remote version.

The order is deliberately remote acceptance, then journal commit. Thus a
database failure can cause a harmless re-upload on the next sweep, but the
journal never claims bytes that B2 did not accept.  New uploads validate and
record B2's immutable ``(fileId, fileName, contentLength, contentSha1)``
receipt alongside the current path journal.  Older journal rows have no B2
file id and remain deliberately non-prunable.  Operator-only remote listing,
restore, reconciliation, and retention planning live in
``ops.data_backups_restore``. The one remote deletion this sweep performs is
the purge of files the retention deleted on purpose (schema review №11): the
retention journals a path in ``purged_files`` before it deletes it, and the
sweep then deletes every B2 version of that path by exact file id (tombstoned),
never uploads it again, and does the same for expired ``ordenes/pending/*``
uploads (a 48 h scratch area). ``TURNOS_BACKUP_PURGE_REMOTE=0`` turns that
off; on the municipal installation (`*.gob.ar`) it is off whatever the setting
says; asking for it logs an ERROR at start and in every pass and shows a
warning in «Sistema», but the service starts (`remote_purge_enabled`,
`remote_purge_ignored`). There every B2 call passes `core.backup_lock`: only
reads, lists and uploads, and an upload only under a name that ends in `~`
+ the SHA-256 of its bytes, so a changed file goes to a new name and nothing
is ever overwritten. If B2 refuses the deletion because the application key lacks ``deleteFiles``
(HTTP 401/403), the pass stops at once, the paths stay pending, and the sweep
logs an ERROR, counts them in ``last_purge()["denied"]`` and emails the owner
once a day. A file that merely vanishes from the volume is NEVER a reason to delete
its backup. Version pruning/retention stays an operator-only tool.

Env-gated — with no credentials the module remains asleep and the server
boots normally:

  TURNOS_B2_KEY_ID / TURNOS_B2_APP_KEY   application key scoped to the bucket
  TURNOS_B2_BUCKET_ID                    bucket id (b2_create_bucket)
  TURNOS_B2_PREFIX                       default "data/"
  TURNOS_BACKUP_INTERVAL_MIN             default 360
  TURNOS_BACKUP_OFF=1                    turns off the thread

The prior ``data/.backup-manifest.json`` is ignored and never read or
written. ``tick()`` is the pure-testable part: it accepts an injectable
``subir_fn(remote_name, payload, sha256)`` and returns the number of uploads
whose remote acceptance and journal commit both succeeded. ``start()`` only
schedules it. Everything is best-effort: an I/O or journal failure leaves the
file pending and never takes down the server.
"""

import contextlib
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core import backup_lock, config, db
from app.core import paths
from app.core import db_backup
from app.core import db_locks
from app.core import launch_signals
from app.core import log_pii
from app.ops.purge_guard import remote_purge_enabled, remote_purge_ignored, warn_purge_ignored  # noqa: F401

ROOT = paths.PROJECT_ROOT
DATA_DIR = os.path.join(ROOT, "data")
_LEGACY_MANIFEST_PREFIX = ".backup-manifest"

_DEFAULT_INTERVAL_MIN = 360
_MAX_FILE_MB = 200        # files here are photos/PDFs; anything bigger is unusual
_SWEEP_LOCK_KEY = "turnos:data-backups:sweep"

_stop = threading.Event()
_last_purge = {"closed": 0, "denied": 0, "failed": 0}


_log = backup_lock.log


def _env(key, default=""):
    return config.get(key, default)


def _test_seams_enabled():
    """Whether the isolated backup proof may use its explicit test seams.

    A backup transport is a privileged path.  The local fake-B2 endpoint and
    alternate data root below are therefore inert unless the shared queue
    test master switch is set *and* their individual variable is supplied.
    They exist solely so the subprocess tests can exercise the real worker
    and real B2 protocol without reading a deployment data volume or making
    an external request.
    """
    from app.core import queue_infra
    return queue_infra.test_seams_enabled()


def _data_dir():
    """Data root, with a double-gated test-only non-PHI fixture override."""
    candidate = (os.environ.get("TURNOS_TEST_BACKUP_DATA_DIR")
                 if _test_seams_enabled() else "")
    return candidate or DATA_DIR


def _b2_authorize_url():
    """B2 authorization endpoint, with a double-gated local proof override."""
    base = (os.environ.get("TURNOS_TEST_B2_API_URL", "").rstrip("/")
            if _test_seams_enabled() else "")
    if base:
        parsed = urllib.parse.urlsplit(base)
        if (parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}):
            raise ValueError("TURNOS_TEST_B2_API_URL must be a loopback HTTP endpoint")
        return base + "/b2api/v2/b2_authorize_account"
    return "https://api.backblazeb2.com/b2api/v2/b2_authorize_account"


def _hold_after_test_accept():
    """Test-only SIGKILL boundary after a fake remote acceptance."""
    if not _test_seams_enabled():
        return
    try:
        hold = float(os.environ.get("TURNOS_TEST_BACKUP_HOLD_AFTER_ACCEPT_S", "0") or 0)
    except ValueError:
        hold = 0
    if hold > 0:
        _log("test seam: remote accepted; holding before journal commit")
        time.sleep(hold)


def configured():
    return bool(_env("TURNOS_B2_KEY_ID") and _env("TURNOS_B2_APP_KEY")
                and _env("TURNOS_B2_BUCKET_ID"))


def _interval_min():
    """The legacy thread's cadence parser, shared by the worker registry.

    Keep the old integer/fallback behaviour exactly: a malformed value falls
    back to six hours.  The queue bridge deliberately does *not* translate
    this into cron, so values such as 45 remain valid when worker mode is on.
    """
    try:
        return int(_env("TURNOS_BACKUP_INTERVAL_MIN", "") or _DEFAULT_INTERVAL_MIN)
    except ValueError:
        return _DEFAULT_INTERVAL_MIN


@contextlib.contextmanager
def _sweep_lock():
    """Hold the cross-topology lock for one whole real backup sweep.

    This is deliberately a *session* advisory lock, rather than a transaction
    lock: the critical section includes a potentially slow B2 upload and the
    separate journal helper transactions.  The connection remains checked out
    until every upload/commit decision is finished; PostgreSQL releases the
    lock automatically if a worker or web process dies.  A normal path always
    unlocks before returning the connection to its pool (session locks are
    otherwise re-entrant on a reused pooled connection).
    """
    conn_cm = cursor_cm = conn = cur = None
    try:
        # Split acquisition from the yielded critical section deliberately:
        # a contextmanager may yield only once.  Catching an unlock/close
        # error around a `with ... yield` block and yielding False again would
        # turn a genuine cleanup failure into ``RuntimeError: generator did
        # not stop``.
        conn_cm = db._connect()
        conn = conn_cm.__enter__()
        cursor_cm = conn.cursor()
        cur = cursor_cm.__enter__()
        db_locks._advisory_lock(cur, _SWEEP_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001 - no database means no backup claim
        _log(f"backup sweep lock unavailable; no files uploaded ({log_pii.exc(exc)})")
        if cursor_cm is not None:
            try:
                cursor_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - entry failure is already the result
                pass
        if conn_cm is not None:
            try:
                conn_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - entry failure is already the result
                pass
        yield False
        return
    try:
        yield conn
    finally:
        try:
            db_locks._advisory_unlock(cur, _SWEEP_LOCK_KEY)
        except Exception as exc:  # noqa: BLE001 - never reuse a lock holder
            _log(f"failed to release backup sweep lock ({log_pii.exc(exc)}); closing connection")
            try:
                # `_PooledConn` returns healthy connections to the pool.  A
                # closed raw connection is discarded instead, so a failed
                # unlock cannot leak a re-entrant session lock into later
                # unrelated work.
                conn.close()
            except Exception:  # noqa: BLE001 - it is already failing
                pass
        finally:
            try:
                cursor_cm.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - tick must never raise
                _log(f"backup lock cursor cleanup failed ({log_pii.exc(exc)})")
            try:
                conn_cm.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - tick must never raise
                _log(f"backup lock connection cleanup failed ({log_pii.exc(exc)})")


# ---------------------------------------------------------------- native B2

def _req_json(url, headers=None, body=None, raw=None, timeout=60):
    """Every B2 native call goes through here, so does the lock: on the
    municipal installation only reads, lists and uploads under a
    content-addressed name leave (`core.backup_lock.check_b2_call`)."""
    backup_lock.check_b2_call(url, headers, raw)
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _authorize():
    import base64
    tok = base64.b64encode(
        f"{_env('TURNOS_B2_KEY_ID')}:{_env('TURNOS_B2_APP_KEY')}".encode()).decode()
    return _req_json(_b2_authorize_url(),
                     {"Authorization": f"Basic {tok}"})


def _upload_b2(remote_name, payload, sha256, auth_cache={}):  # noqa: B006 — process cache
    """Uploads the already-snapshotted bytes to ``remote_name``.

    The caller computes ``sha256`` from ``payload`` before this transport is
    invoked. Checking it again here keeps the transport contract honest and
    makes it impossible for this function to upload different bytes than the
    journal identity it was given.
    """
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise ValueError("payload does not match its supplied SHA-256")
    for intento in (1, 2):
        if "auth" not in auth_cache:
            auth_cache["auth"] = _authorize()
        auth = auth_cache["auth"]
        try:
            up = _req_json(auth["apiUrl"] + "/b2api/v2/b2_get_upload_url",
                           {"Authorization": auth["authorizationToken"]},
                           {"bucketId": _env("TURNOS_B2_BUCKET_ID")})
            accepted = _req_json(up["uploadUrl"], {
                "Authorization": up["authorizationToken"],
                "X-Bz-File-Name": urllib.parse.quote(remote_name),
                "Content-Type": "b2/x-auto",
                "X-Bz-Content-Sha1": hashlib.sha1(payload).hexdigest(),
            }, raw=payload)
            receipt = _accepted_receipt(accepted, remote_name, payload, sha256)
            # Real-SIGKILL proof seam: the fake local B2 has durably
            # accepted these exact bytes, but the journal write below has
            # not started.  Both flags are required, so production cannot
            # accidentally sleep after an upload.
            _hold_after_test_accept()
            return receipt
        except urllib.error.HTTPError as e:  # noqa: PERF203
            if e.code in (401, 503) and intento == 1:
                e.close()  # swallowed for the retry: close the body it holds
                auth_cache.pop("auth", None)   # expired token: re-authorize
                continue
            # The error is re-raised to the best-effort producer, but its
            # response body still owns a temporary file/socket.  Close it
            # first so a failed B2 call cannot emit ResourceWarning at
            # worker shutdown (the disposable fake-B2 proof exercises this
            # 503 path too).
            e.close()
            raise


def _accepted_receipt(accepted, remote_name, payload, sha256):
    """Return a normalized immutable B2 acceptance receipt for ``payload``.

    B2's upload success response, not the request we sent, is the remote fact.
    Require it to name the exact version, path, byte length and SHA-1 before a
    database row can claim success.  A permissive response parser here would
    turn a proxy/test-fake mismatch into a false restore candidate.
    """
    if not isinstance(accepted, dict):
        raise ValueError("B2 upload did not return an acceptance receipt")
    expected_sha1 = hashlib.sha1(payload).hexdigest()
    if "file_id" in accepted:
        # A transport wrapper may already have normalized the B2 response.
        # Re-validate it against this exact snapshot; do not merely trust the
        # wrapper because this function is the producer's last mutation gate.
        receipt = dict(accepted)
        receipt.setdefault("remote_name", accepted.get("fileName"))
        receipt.setdefault("sha1", accepted.get("contentSha1"))
        receipt.setdefault("size", accepted.get("contentLength"))
    else:
        receipt = {
            "file_id": accepted.get("fileId"),
            "remote_name": accepted.get("fileName"),
            "sha256": sha256,
            "sha1": accepted.get("contentSha1"),
            "size": accepted.get("contentLength"),
            "action": accepted.get("action", "upload"),
        }
    try:
        length = int(receipt.get("size"))
    except (TypeError, ValueError) as exc:
        raise ValueError("B2 acceptance receipt lacks a numeric contentLength") from exc
    receipt["size"] = length
    receipt["sha256"] = sha256
    normal = db_backup._normalise_backup_receipt(receipt, remote_name, sha256, len(payload))
    if normal["sha1"] != expected_sha1:
        raise ValueError("B2 receipt SHA-1 does not match accepted payload")
    return normal


# ------------------------------------------------------------- journal sweep

def _remote_name(rel, prefix):
    return prefix + rel.replace(os.sep, "/")


def _mtime_ns(st):
    return getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))


def _ctime_ns(st):
    return getattr(st, "st_ctime_ns", int(st.st_ctime * 1_000_000_000))


def _same_file_state(left, right):
    """Whether two stats name the same unchanged file object.

    Metadata timestamps alone do not protect the sweep from a same-size write
    or a replacement of ``fpath`` while its old descriptor remains open. The
    device/inode pair binds a pathname to that descriptor; ctime catches
    metadata/content mutation that did not change size or mtime.
    """
    try:
        return (left.st_dev == right.st_dev
                and left.st_ino == right.st_ino
                and left.st_size == right.st_size
                and _mtime_ns(left) == _mtime_ns(right)
                and _ctime_ns(left) == _ctime_ns(right))
    except AttributeError:
        return False


def _candidate_paths():
    """Yield bounded files below ``DATA_DIR`` without reading their bytes."""
    data_dir = _data_dir()
    if not os.path.isdir(data_dir):
        return
    for dirpath, _dirs, files in os.walk(data_dir):
        for fn in files:
            # A former producer could leave its manifest or a crash-left temp
            # artifact in the data volume. Those are not backup content or
            # state for the journal producer, so never read, upload, migrate,
            # or modify any `.backup-manifest*` artifact.
            if fn.startswith(_LEGACY_MANIFEST_PREFIX):
                continue
            fpath = os.path.join(dirpath, fn)
            rel = os.path.relpath(fpath, data_dir)
            try:
                st = os.stat(fpath)
            except OSError:
                continue
            if st.st_size > _MAX_FILE_MB * 1024 * 1024:
                continue
            yield rel, fpath, st


def _snapshot(fpath):
    """Return ``(payload, sha256, size, mtime_ns)`` for a stable file.

    The descriptor's device/inode, size, mtime, and ctime must agree at both
    ends of the read. The pathname must also still identify that descriptor
    after the read. Otherwise a writer mutated or replaced the file and the
    next sweep retries. The journal size is always ``len(payload)``.
    """
    try:
        with open(fpath, "rb") as f:
            before = os.fstat(f.fileno())
            payload = f.read()
            after = os.fstat(f.fileno())
            path_after = os.stat(fpath)
    except OSError:
        return None
    if (not _same_file_state(before, after)
            or not _same_file_state(after, path_after)
            or after.st_size != len(payload)
            or len(payload) > _MAX_FILE_MB * 1024 * 1024):
        return None
    return payload, hashlib.sha256(payload).hexdigest(), len(payload), _mtime_ns(after)


def _row_names_path(row, remote_name):
    """Whether the journal row's remote name is this path's plain name, or
    the plain name addressed by the row's own SHA-256 (the municipal
    installation's form, `backup_lock.addressed_name`)."""
    name = row.get("remote_name")
    return name == remote_name or (
        isinstance(row.get("sha256"), str)
        and name == backup_lock.addressed_name(remote_name, row["sha256"]))


def _row_has_stat(row, size, mtime_ns, remote_name):
    try:
        return (int(row["size"]) == size
                and int(row["mtime_ns"]) == mtime_ns
                and _row_names_path(row, remote_name))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _purge_ops_default():
    """(list_fn, delete_fn) for deleting the remote copies of purged files, or
    None when the remote purge is off (`remote_purge_enabled`).
    `list_fn(dir_prefix)` returns the
    raw B2 version records under a directory prefix; `delete_fn(name, file_id)`
    deletes exactly one version. Both are the operator module's own
    primitives, imported lazily (it imports this module)."""
    if not remote_purge_enabled():
        return None
    from app.ops import data_backups_restore as restore
    return (lambda prefix: restore.list_remote_versions(prefix=prefix),
            restore.delete_remote_version)


def tick(subir_fn=None, *, lock_sweep=None, purge_ops=None):
    """Run one journal-backed sweep and return committed upload count.

    ``subir_fn(remote_name, payload, sha256)`` is an injectable transport.
    ``purge_ops=(list_fn, delete_fn)`` injects the B2 version listing/deletion
    used to remove the remote copies of files the retention deleted; a real
    (non-injected) sweep resolves it itself, an injected one without it never
    touches that part. The function never raises: unavailable journal state,
    upload failure, or commit failure leaves the row pending for a later sweep.
    """
    injected = subir_fn is not None
    if subir_fn is None:
        if not configured():
            return 0
        subir_fn = _upload_b2
        if purge_ops is None:
            purge_ops = _purge_ops_default()
        warn_purge_ignored("backup pass")
    # In-memory producer tests inject a transport and journal helpers, so
    # they intentionally exercise byte/journal semantics without a database
    # connection.  Every real invocation (thread or worker) takes the lock;
    # integration tests may request it explicitly with an injected transport.
    if lock_sweep is None:
        lock_sweep = not injected
    # Resolve DB-backed configuration before acquiring the session advisory
    # lock.  A cold config cache may need its own short pool checkout; doing
    # that inside the critical section would self-deadlock with a one-slot
    # pool even though every journal operation correctly reuses lock_conn.
    remote_prefix = _env("TURNOS_B2_PREFIX", "data/")
    if lock_sweep:
        with _sweep_lock() as lock_conn:
            if not lock_conn:
                return 0
            return _tick_unlocked(
                subir_fn, conn=lock_conn, injected=injected,
                remote_prefix=remote_prefix, purge_ops=purge_ops)
    return _tick_unlocked(
        subir_fn, injected=injected, remote_prefix=remote_prefix,
        purge_ops=purge_ops)


def _purge_remote(conn, remote_prefix, purge_ops):
    """Deletes, in B2, every version of the files the retention deleted on
    purpose (``purged_files`` rows still pending) and of the expired pending
    referral uploads (``ordenes/pending/*`` journal rows whose local file is
    gone — a 48 h scratch area, never the only copy of anything).

    Only an EXPLICIT record triggers a deletion of a retention file: a file
    that merely vanished from the volume is never a reason to delete its
    backup (that is what a backup is for). The pending rule is the one
    exception, and it refuses to act unless ``data/ordenes`` is there at all
    (an unmounted volume must not look like «everything expired»). Each
    version is deleted by exact file id and tombstoned; a path closes only
    when ALL its versions are gone, otherwise it stays pending for the next
    sweep. A refusal for lack of permission (the application key has no
    ``deleteFiles``) is the same for every path, so it ends the pass at the
    first attempt: the paths stay pending, ``last_purge()["denied"]`` counts
    them, and the sweep logs an ERROR and sends the owner the daily alert.
    Never raises. On the municipal installation it deletes nothing (a real
    sweep there has no `purge_ops` at all; injected ones are refused here,
    before the first listing)."""
    if backup_lock.locked():
        backup_lock.warn("remote purge of backups")
        return 0
    list_fn, delete_fn = purge_ops
    from app.ops.data_backups_restore import RemoteAlreadyAbsent, RemoteDeleteDenied
    global _last_purge
    _last_purge = {"closed": 0, "denied": 0, "failed": 0}
    try:
        paths = list(db_backup.purged_files_pending(conn=conn))
        data_dir = _data_dir()
        if os.path.isdir(os.path.join(data_dir, "ordenes")):
            paths += [p for p in db_backup.backup_journal_paths_like("ordenes/pending/", conn=conn)
                      if p not in paths and not os.path.exists(os.path.join(data_dir, p))]
    except Exception as e:  # noqa: BLE001
        _log(f"purge list unavailable; remote copies left as they are ({log_pii.exc(e)})")
        return 0
    by_dir = {}
    for path in paths:
        remote = _remote_name(path, remote_prefix)
        by_dir.setdefault(remote.rsplit("/", 1)[0] + "/", {})[remote] = path
    closed = failed = 0
    denied = False
    for dir_prefix, names in by_dir.items():
        if denied:
            break
        try:
            versions = list_fn(dir_prefix)
        except Exception as e:  # noqa: BLE001
            _log(f"could not list {dir_prefix} to purge remote copies ({log_pii.exc(e)})")
            failed += len(names)
            continue
        ids = {}
        for rec in versions or []:
            name, file_id = rec.get("fileName"), rec.get("fileId")
            # A version uploaded under a content-addressed name (the municipal
            # installation, `backup_lock.addressed_name`) is a copy of the same path.
            plain = backup_lock.plain_name(name) if isinstance(name, str) else name
            if plain in names and isinstance(file_id, str) and file_id:
                ids.setdefault(plain, []).append((name, file_id))
        for remote, path in names.items():
            complete = True
            for version_name, file_id in ids.get(remote, []):
                try:
                    delete_fn(version_name, file_id)
                except RemoteAlreadyAbsent:
                    pass
                except RemoteDeleteDenied as e:
                    denied = e
                    complete = False
                    break
                except Exception as e:  # noqa: BLE001 - retried by the next sweep
                    _log(f"could not delete a remote copy of {path}: {log_pii.exc(e)}")
                    complete = False
                    continue
                try:
                    db_backup.backup_version_tombstone(file_id, version_name, conn=conn)
                    if conn is not None:
                        conn.commit()
                except Exception as e:  # noqa: BLE001 - the delete is idempotent on retry
                    _log(f"could not record the deletion of a remote copy of {path}: {log_pii.exc(e)}")
                    complete = False
            if denied:
                break
            if not complete:
                failed += 1
                continue
            try:
                db_backup.purged_file_remote_done(path, conn=conn)
                if conn is not None:
                    conn.commit()
                closed += 1
            except Exception as e:  # noqa: BLE001
                _log(f"could not close the purge of {path}: {log_pii.exc(e)}")
                failed += 1
    if closed:
        _log(f"{closed} purged file(s): remote copies deleted from B2")
    n_denied = len(paths) - closed - failed if denied else 0
    _last_purge = {"closed": closed, "denied": n_denied, "failed": failed}
    try:
        db_backup.backup_purge_state_save(closed, n_denied, failed, conn=conn)
        if conn is not None:
            conn.commit()
    except Exception as e:  # noqa: BLE001 - the panel's state never takes the sweep down
        _log(f"could not record the purge pass for the panel ({log_pii.exc(e)})")
    if denied:
        _log(f"ERROR: the B2 application key has no deleteFiles capability (HTTP {denied.code}): "
             f"{n_denied} purged file(s) kept their remote copies and stay pending; "
             "issue a key with deleteFiles and the next sweep deletes them")
        _alert_delete_denied(n_denied, denied.code)
    return closed


def last_purge():
    """What the last remote purge pass did, as ``{"closed", "denied", "failed"}`` path counts
    (this process only; all zero before the first pass). ``denied`` is the paths left pending
    because B2 refused the deletion for lack of permission."""
    return dict(_last_purge)


def _alert_delete_denied(count, status):
    """Tells the owner, at most once a day (the claim is taken BEFORE the send, like the
    disk alert in housekeeping.check_disk), that B2 refuses deletions. Never raises."""
    try:
        from app.ops import housekeeping
        if not housekeeping._claim_day("b2_delete_denied", housekeeping._hoy()):
            return False
        from app.auth import admin_auth
        from app.notifications import mail
        destinatario = admin_auth.owner_email()
        if not destinatario:
            _log("b2: no owner email configured, the deleteFiles alert is not sent")
            return False
        from app.core import i18n
        # «1 archivo … sigue guardado», never «1 archivo(s)»: the one plural rule.
        if i18n.plural_category(count) == "one":
            quedan = f"{count} archivo que la retención ya borró sigue guardado en B2 y no se puede quitar"
            pronombre = "lo"
        else:
            quedan = f"{count} archivos que la retención ya borró siguen guardados en B2 y no se pueden quitar"
            pronombre = "los"
        ok = mail.send(
            destinatario, "Alerta: la clave de B2 no puede borrar archivos",
            f"B2 rechazó el borrado (HTTP {status}): la clave de aplicación no tiene el permiso "
            f"deleteFiles. {quedan} hasta que se cargue una clave con ese permiso "
            f"(TURNOS_B2_KEY_ID / TURNOS_B2_APP_KEY). El próximo barrido {pronombre} borra solo.")
        _log(f"b2: deleteFiles alert sent to {log_pii.email(destinatario)}" if ok
             else "b2: the deleteFiles alert was not sent (mail provider); no retry until tomorrow")
        return ok
    except Exception as e:  # noqa: BLE001 - the alert never takes the sweep down
        _log(f"b2: could not send the deleteFiles alert ({log_pii.exc(e)})")
        return False


def _repoint_to_stored(rel, stored, sha256, size, mtime_ns, conn):
    """When the content-addressed name already holds these exact bytes (a
    ledger receipt for the same path and SHA-256), point the journal back at
    that receipt instead of uploading again. True when it did (or when the
    commit failed: the file stays pending, nothing is sent)."""
    if not stored or stored.get("sha256") != sha256 or stored.get("journal_path") != rel:
        return False
    receipt = {"file_id": stored["file_id"], "remote_name": stored["remote_name"],
               "sha1": stored["sha1"], "size": stored["size"], "action": "upload"}
    try:
        if conn is not None:
            db_backup.backup_journal_commit(rel, sha256, size, mtime_ns, stored["remote_name"],
                                     conn=conn, receipt=receipt)
            conn.commit()
        else:
            db_backup.backup_journal_commit(rel, sha256, size, mtime_ns, stored["remote_name"], receipt=receipt)
    except Exception:  # noqa: BLE001 - pending for the next sweep
        _log(f"journal commit failed for {rel}; pending for next sweep")
    return True


def _tick_unlocked(subir_fn, conn=None, injected=False, remote_prefix="data/",
                   purge_ops=None):
    """The journal producer while the caller owns the sweep lock."""
    try:
        journal = db_backup.backup_journal_map(conn=conn) if conn is not None else db_backup.backup_journal_map()
    except Exception:  # noqa: BLE001 - journal failure must prevent upload
        _log("backup journal unavailable; no files uploaded")
        launch_signals.handled("backup_data_journal_failed")
        return 0
    purged = set()
    if purge_ops is not None or not injected:
        try:
            purged = db_backup.purged_file_paths(conn=conn)
        except Exception:  # noqa: BLE001 - without the memory, upload nothing
            _log("purge journal unavailable; no files uploaded")
            return 0
    subidos = 0
    ledger = None  # remote name -> stored receipt, read once (municipal installation only)
    for rel, fpath, st in _candidate_paths():
        if rel in purged:
            continue   # deleted on purpose: never goes back to B2
        remote_name = _remote_name(rel, remote_prefix)
        row = journal.get(rel)
        if row is not None and _row_has_stat(
                row, st.st_size, _mtime_ns(st), remote_name):
            continue
        snap = _snapshot(fpath)
        if snap is None:
            continue
        payload, sha256, size, mtime_ns = snap
        if (row is not None and row.get("sha256") == sha256
                and _row_names_path(row, remote_name)):
            try:
                if conn is not None:
                    db_backup.backup_journal_refresh_stat(rel, size, mtime_ns, conn=conn)
                    conn.commit()
                else:
                    db_backup.backup_journal_refresh_stat(rel, size, mtime_ns)
            except Exception:  # noqa: BLE001 - leave the safe prefilter stale
                _log(f"failed to refresh journal metadata for {rel}")
            continue
        if backup_lock.locked():
            # The municipal installation never overwrites: a changed file
            # goes to a new name that carries the SHA-256 of its bytes, and
            # bytes already stored under that name (A -> B -> A) are not
            # sent again — the journal points back at their receipt.
            remote_name = backup_lock.addressed_name(remote_name, sha256)
            if ledger is None:
                try:
                    rows = db_backup.backup_version_rows(conn=conn) if conn is not None else db_backup.backup_version_rows()
                    ledger = {r["remote_name"]: r for r in rows.values()}
                except Exception as e:  # noqa: BLE001 - without it, an upload could reuse a name
                    _log(f"backup ledger unavailable; no files uploaded ({log_pii.exc(e)})")
                    break
            if _repoint_to_stored(rel, ledger.get(remote_name), sha256, size, mtime_ns, conn):
                continue
        try:
            receipt = subir_fn(remote_name, payload, sha256)
            # Pre-receipt injected unit transports remain useful to exercise
            # the old producer contract without a B2 fake.  A configured
            # production transport has no such escape hatch: it must provide
            # a fully checked remote receipt before any journal mutation.
            if receipt is None:
                if not injected:
                    raise ValueError("B2 transport returned no acceptance receipt")
            else:
                receipt = _accepted_receipt(receipt, remote_name, payload, sha256)
        except Exception as e:  # noqa: BLE001
            _log(f"upload failed for {rel}: {log_pii.exc(e)} (pending for next sweep)")
            launch_signals.handled("backup_data_upload_failed")
            continue
        try:
            if conn is not None:
                if receipt is None:
                    db_backup.backup_journal_commit(rel, sha256, size, mtime_ns, remote_name,
                                             conn=conn)
                else:
                    db_backup.backup_journal_commit(rel, sha256, size, mtime_ns, remote_name,
                                             conn=conn, receipt=receipt)
                # Commit the journal outcome while retaining the SESSION lock:
                # the next serialized sweep sees it even when the pool has one
                # connection, while Postgres keeps the advisory lock held.
                conn.commit()
            else:
                if receipt is None:
                    db_backup.backup_journal_commit(rel, sha256, size, mtime_ns, remote_name)
                else:
                    db_backup.backup_journal_commit(rel, sha256, size, mtime_ns, remote_name,
                                             receipt=receipt)
        except Exception:  # noqa: BLE001 - remote is not falsely journaled
            _log(f"journal commit failed for {rel}; pending for next sweep")
            launch_signals.handled("backup_data_journal_failed")
            continue
        subidos += 1
    if subidos:
        _log(f"{subidos} file(s) backed up to B2")
    if purge_ops is not None:
        _purge_remote(conn, remote_prefix, purge_ops)
    return subidos


def start():
    if _env("TURNOS_BACKUP_OFF") == "1":
        _log("apagado por TURNOS_BACKUP_OFF=1")
        return None
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        _log(f"backup delegated to the worker (TURNOS_QUEUE_WORKER={modo_worker}) — no thread here")
        return None
    if not configured():
        _log("sin credenciales B2 (TURNOS_B2_*) — backup de data/ dormido")
        return None
    mins = _interval_min()

    def _loop():
        time.sleep(60)          # let the server boot in peace
        while not _stop.is_set():
            tick()
            _stop.wait(mins * 60)

    t = threading.Thread(target=_loop, name="backups-datos", daemon=True)
    t.start()
    _log(f"backup de data/ cada {mins} min → B2 sana-turnos-data")
    return t
