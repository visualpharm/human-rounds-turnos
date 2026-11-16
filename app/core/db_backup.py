"""core/db_backup.py — the backup journal, the purged-file memory and the demo-refresh marker.

`backup_journal_*` / `backup_version_*` / `backup_tombstone_rows` (the sweep of `ops/data_backups`),
`purged_file_*` (the retention memory) and `demo_refresh_completion*` (the demo refresher's latest
completed attempt). Split out of `core/db.py` in stage 3 of the SQLAlchemy Core move
(docs/architecture/sqlalchemy-core.md): the statements are Core, built from `core/schema_model.py`.

Callers write `db_backup.backup_journal_commit(...)`: `core/db.py` re-exports nothing of this module
(TASK-57, `tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()` and
each other by their own names, at CALL time, so a test that patches the owner patches what every
caller uses.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import schema_model as sm
from app.core import sqlcore


_DEMO_REFRESH_OUTCOMES = frozenset(("success", "partial", "failed"))


def demo_refresh_completion(outcome, attempted_for, conn=None):
    """Record the demo refresher's latest completed attempt.

    This is intentionally one bounded operational row, not an event log.  A
    green result is admitted only by ``ops.demo_refresh`` after its complete
    asset/referral/current-day contract has passed.  A degraded attempt is
    still written so callers cannot treat the previous successful day as a
    current green state.  ``success_for``/``last_success_at`` remain useful
    audit facts, but the status surface must require BOTH those fields and
    ``outcome='success'`` before it reports green.
    """
    if outcome not in _DEMO_REFRESH_OUTCOMES:
        raise ValueError("invalid demo refresh outcome")
    if conn is None:
        with db._connect() as own_conn:
            return demo_refresh_completion(outcome, attempted_for, conn=own_conn)
    t = sm.demo_refresh_completion
    with conn.cursor() as cur:
        success = sa.literal_column("'success'")
        insert = _pg.insert(t).values(
            job=sa.literal_column("'demo_refresh'"), attempted_for=attempted_for,
            outcome=outcome, last_attempt_at=sa.func.now(),
            success_for=sa.case((sa.literal(outcome) == success, sa.literal(attempted_for)),
                                else_=sa.null()),
            last_success_at=sa.case((sa.literal(outcome) == success, sa.func.now()),
                                    else_=sa.null()))
        sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.job],
            set_={"attempted_for": insert.excluded.attempted_for,
                  "outcome": insert.excluded.outcome,
                  "last_attempt_at": sa.func.now(),
                  "success_for": sa.case((insert.excluded.outcome == success,
                                          insert.excluded.success_for),
                                         else_=t.c.success_for),
                  "last_success_at": sa.case((insert.excluded.outcome == success,
                                              sa.func.now()),
                                             else_=t.c.last_success_at)}))


def demo_refresh_completion_status(conn=None):
    """Return the safe singleton completion evidence, or ``None`` if absent.

    The row has no credentials, configuration values, patient identifiers, or
    sample records.  A DB failure deliberately raises to its caller: admin
    System must render it as unavailable, never fall back to an old green
    answer cached in process memory.
    """
    if conn is None:
        with db._connect() as own_conn:
            return demo_refresh_completion_status(conn=own_conn)
    t = sm.demo_refresh_completion
    with conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(
            t.c.attempted_for, t.c.outcome, t.c.last_attempt_at, t.c.success_for,
            t.c.last_success_at,
        ).where(t.c.job == sa.literal_column("'demo_refresh'"))).fetchone()
    return dict(row) if row else None


def backup_journal_map(conn=None):
    """The whole backup journal as {path: row} — the sweep's working set
    (ops/data_backups.tick). One row per file ever backed up; bounded by
    the data volume's file count, so loading it whole is the cheap and
    simple shape (same reasoning as the catalog maps)."""
    if conn is None:
        with db._connect() as own_conn:
            return backup_journal_map(own_conn)
    t = sm.backup_journal
    with conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            t.c.path, t.c.sha256, t.c.size, t.c.mtime_ns, t.c.remote_name,
            t.c.remote_file_id, t.c.origin)).fetchall()
        return {r["path"]: r for r in rows}


def backup_journal_commit(path, sha256, size, mtime_ns, remote_name,
                          origin="upload", conn=None, receipt=None):
    """Commits ONE file's backup outcome (upsert by path). Called AFTER
    the remote accepted the bytes whose sha256 this records — never
    before — so the journal can under-claim (re-upload on the next sweep)
    but never over-claim. Idempotent: two committers of the same content
    converge on one row; a racing commit of DIFFERENT content is resolved
    by the next sweep's stat-vs-journal comparison (self-healing, see
    ops/data_backups' module docstring)."""
    if conn is None:
        with db._connect() as own_conn:
            return backup_journal_commit(path, sha256, size, mtime_ns,
                                         remote_name, origin, conn=own_conn,
                                         receipt=receipt)
    receipt = _normalise_backup_receipt(receipt, remote_name, sha256, size)
    # A current journal pointer is meaningful only with its immutable remote
    # receipt.  In particular, a reused/malformed B2 file id must not leave a
    # path pointing at a non-existent ledger row if receipt validation raises.
    # Use an explicit savepoint because callers often own a long-lived
    # session-advisory-lock transaction; a full rollback would release useful
    # earlier work while no rollback would commit a partial claim on context
    # exit.  ``ROLLBACK TO`` also clears a PostgreSQL error state.
    t = sm.backup_journal
    with conn.cursor() as cur:
        if receipt:
            sqlcore.run(cur, sa.text("SAVEPOINT backup_journal_receipt"))
        try:
            upsert = _pg.insert(t).values(
                path=path, sha256=sha256, size=int(size), mtime_ns=int(mtime_ns),
                remote_name=remote_name,
                remote_file_id=receipt["file_id"] if receipt else None,
                origin=origin, uploaded_at=sa.func.now())
            sqlcore.run(cur, upsert.on_conflict_do_update(
                index_elements=[t.c.path],
                set_={"sha256": upsert.excluded.sha256, "size": upsert.excluded.size,
                      "mtime_ns": upsert.excluded.mtime_ns,
                      "remote_name": upsert.excluded.remote_name,
                      "remote_file_id": upsert.excluded.remote_file_id,
                      "origin": upsert.excluded.origin, "uploaded_at": sa.func.now()}))
            if receipt:
                backup_version_record(path, receipt, conn=conn)
        except Exception:
            if receipt:
                sqlcore.run(cur, sa.text("ROLLBACK TO SAVEPOINT backup_journal_receipt"))
                sqlcore.run(cur, sa.text("RELEASE SAVEPOINT backup_journal_receipt"))
            raise
        else:
            if receipt:
                sqlcore.run(cur, sa.text("RELEASE SAVEPOINT backup_journal_receipt"))


def _normalise_backup_receipt(receipt, remote_name, sha256, size):
    """Validate a B2 acceptance receipt before it reaches durable state.

    ``None`` remains supported only for pre-receipt injected producer tests
    and migration-era journal rows.  A real B2 transport supplies all fields;
    malformed acceptance metadata must leave both journal and ledger untouched.
    """
    if receipt is None:
        return None
    if not isinstance(receipt, dict):
        raise ValueError("backup receipt must be a mapping")
    file_id = receipt.get("file_id") or receipt.get("fileId")
    file_name = receipt.get("remote_name") or receipt.get("fileName")
    sha1 = receipt.get("sha1") or receipt.get("contentSha1")
    length = receipt.get("size", receipt.get("contentLength"))
    action = receipt.get("action", "upload")
    if (not isinstance(file_id, str) or not file_id
            or not isinstance(file_name, str) or file_name != remote_name
            or not isinstance(sha1, str) or len(sha1) != 40
            or action != "upload"):
        raise ValueError("invalid B2 backup receipt identity")
    try:
        length = int(length)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid B2 backup receipt size") from exc
    if length != int(size):
        raise ValueError("B2 receipt size does not match accepted payload")
    if any(ch not in "0123456789abcdef" for ch in sha1.lower()):
        raise ValueError("invalid B2 backup receipt SHA-1")
    return {"file_id": file_id, "remote_name": file_name,
            "sha256": sha256, "sha1": sha1.lower(), "size": length}


def backup_version_record(journal_path, receipt, conn=None):
    """Insert one immutable B2 acceptance receipt or verify an exact replay.

    A file-id conflict whose acceptance facts differ is deliberately loud: it
    would mean the remote API, a test fake, or local bookkeeping attempted to
    reuse an immutable B2 id for different bytes.  It is never an upsert.
    """
    if conn is None:
        with db._connect() as own_conn:
            return backup_version_record(journal_path, receipt, conn=own_conn)
    normal = _normalise_backup_receipt(
        receipt, receipt.get("remote_name") or receipt.get("fileName"),
        receipt.get("sha256"), receipt.get("size", receipt.get("contentLength")))
    t = sm.backup_versions
    with conn.cursor() as cur:
        inserted = sqlcore.run(cur, _pg.insert(t).values(
            file_id=normal["file_id"], remote_name=normal["remote_name"],
            sha256=normal["sha256"], sha1=normal["sha1"], size=normal["size"],
            journal_path=journal_path,
        ).on_conflict_do_nothing(index_elements=[t.c.file_id]))
        if inserted.rowcount:
            return
        existing = sqlcore.run(cur, sa.select(
            t.c.file_id, t.c.remote_name, t.c.sha256, t.c.sha1, t.c.size, t.c.journal_path,
        ).where(t.c.file_id == normal["file_id"])).fetchone()
    if existing != {"file_id": normal["file_id"], "remote_name": normal["remote_name"],
                    "sha256": normal["sha256"], "sha1": normal["sha1"],
                    "size": normal["size"], "journal_path": journal_path}:
        raise ValueError("immutable B2 file id conflicts with a different receipt")


def backup_version_rows(conn=None):
    """All immutable receipts, keyed by B2 file id."""
    if conn is None:
        with db._connect() as own_conn:
            return backup_version_rows(own_conn)
    t = sm.backup_versions
    with conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            t.c.file_id, t.c.remote_name, t.c.sha256, t.c.sha1, t.c.size, t.c.journal_path,
            t.c.accepted_at)).fetchall()
        return {r["file_id"]: r for r in rows}


def backup_tombstone_rows(conn=None):
    """Exact remote-version deletions already journaled locally."""
    if conn is None:
        with db._connect() as own_conn:
            return backup_tombstone_rows(own_conn)
    t = sm.backup_version_tombstones
    with conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.file_id, t.c.remote_name, t.c.deleted_at)).fetchall()
        return {r["file_id"]: r for r in rows}


def backup_version_tombstone(file_id, remote_name, conn=None):
    """Record a successful exact remote deletion idempotently."""
    if not isinstance(file_id, str) or not file_id or not isinstance(remote_name, str) or not remote_name:
        raise ValueError("exact B2 file id and name are required for a tombstone")
    if conn is None:
        with db._connect() as own_conn:
            return backup_version_tombstone(file_id, remote_name, conn=own_conn)
    t = sm.backup_version_tombstones
    with conn.cursor() as cur:
        inserted = sqlcore.run(cur, _pg.insert(t).values(
            file_id=file_id, remote_name=remote_name,
        ).on_conflict_do_nothing(index_elements=[t.c.file_id]))
        if inserted.rowcount:
            return
        row = sqlcore.run(cur, sa.select(t.c.remote_name).where(t.c.file_id == file_id)).fetchone()
    if not row or row["remote_name"] != remote_name:
        raise ValueError("B2 tombstone id conflicts with a different remote name")


def backup_journal_refresh_stat(path, size, mtime_ns, conn=None):
    """Refreshes ONLY the stat prefilter of an existing row — the
    content-hash-identity case where a file's mtime moved but its bytes
    (sha256) are unchanged: nothing was uploaded, so uploaded_at, origin
    and remote_name stay put and only the prefilter learns the new stat."""
    if conn is None:
        with db._connect() as own_conn:
            return backup_journal_refresh_stat(path, size, mtime_ns, conn=own_conn)
    t = sm.backup_journal
    with conn.cursor() as cur:
        sqlcore.run(cur, sa.update(t).where(t.c.path == path).values(
            size=int(size), mtime_ns=int(mtime_ns)))


def purged_file_record(path, reason, conn=None):
    """Anota que la retención va a borrar (o borró) el archivo `path`
    (relativo a data/). Idempotente: la primera razón y fecha se conservan.
    Se llama ANTES de borrar el archivo: sin la anotación nada se borra."""
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError("a purged file needs a safe data-relative path")
    if conn is None:
        with db._connect() as own_conn:
            purged_file_record(path, reason, conn=own_conn)
            own_conn.commit()
            return
    t = sm.purged_files
    with conn.cursor() as cur:
        sqlcore.run(cur, _pg.insert(t).values(
            path=path, reason=str(reason or "retention"),
        ).on_conflict_do_nothing(index_elements=[t.c.path]))


def purged_file_paths(conn=None):
    """Todos los caminos anotados como borrados a propósito (set)."""
    if conn is None:
        with db._connect() as own_conn:
            return purged_file_paths(own_conn)
    with conn.cursor() as cur:
        return {r["path"] for r in sqlcore.run(cur, sa.select(sm.purged_files.c.path)).fetchall()}


def purged_files_pending(conn=None, limit=500):
    """Caminos cuyas versiones en B2 todavía no se borraron, los más viejos
    primero (el barrido de ops.data_backups los toma de a `limit`)."""
    if conn is None:
        with db._connect() as own_conn:
            return purged_files_pending(own_conn, limit)
    t = sm.purged_files
    with conn.cursor() as cur:
        return [r["path"] for r in sqlcore.run(cur, sa.select(t.c.path).where(
            t.c.remote_done.is_(None)).order_by(t.c.purged_at, t.c.path)
            .limit(int(limit))).fetchall()]


def purged_file_remote_done(path, conn=None):
    """Cierra un camino: ya no queda ninguna versión remota. La fila se queda
    (es la memoria) y se olvida su renglón de `backup_journal`, que ya no
    describe nada que exista en B2."""
    if conn is None:
        with db._connect() as own_conn:
            purged_file_remote_done(path, conn=own_conn)
            own_conn.commit()
            return
    purged, journal = sm.purged_files, sm.backup_journal
    with conn.cursor() as cur:
        sqlcore.run(cur, sa.update(purged).where(
            purged.c.path == path, purged.c.remote_done.is_(None),
        ).values(remote_done=sa.func.now()))
        sqlcore.run(cur, sa.delete(journal).where(journal.c.path == path))


def backup_journal_paths_like(prefix, conn=None):
    """Caminos de `backup_journal` que empiezan con `prefix` (un directorio
    relativo a data/, con la barra final)."""
    if conn is None:
        with db._connect() as own_conn:
            return backup_journal_paths_like(prefix, own_conn)
    t = sm.backup_journal
    with conn.cursor() as cur:
        return [r["path"] for r in sqlcore.run(cur, sa.select(t.c.path).where(
            sa.func.starts_with(t.c.path, prefix)).order_by(t.c.path)).fetchall()]


_PURGE_STATE_KEY = "remote_purge"


def backup_purge_state_save(closed, denied, failed, conn=None):
    """Deja lo que hizo el último pase de la purga remota de B2 (una sola fila):
    cuándo corrió y, si no hubo rechazo ni fallo, cuándo fue el último pase
    limpio. Lo lee «Sistema» desde el proceso web."""
    if conn is None:
        with db._connect() as own_conn:
            backup_purge_state_save(closed, denied, failed, conn=own_conn)
            own_conn.commit()
            return
    t = sm.backup_purge_state
    clean = int(denied) == 0 and int(failed) == 0
    ins = _pg.insert(t).values(
        key=_PURGE_STATE_KEY, last_pass_at=sa.func.now(),
        last_success_at=sa.func.now() if clean else None,
        closed=int(closed), denied=int(denied), failed=int(failed))
    update = {"last_pass_at": sa.func.now(), "closed": ins.excluded.closed,
              "denied": ins.excluded.denied, "failed": ins.excluded.failed}
    if clean:
        update["last_success_at"] = sa.func.now()
    with conn.cursor() as cur:
        sqlcore.run(cur, ins.on_conflict_do_update(index_elements=[t.c.key], set_=update))


def backup_purge_state(conn=None):
    """La fila de `backup_purge_state_save` más cuántas rutas de `purged_files` siguen
    esperando el borrado remoto (`pending`); sin pase todavía, las fechas son None y los conteos 0."""
    if conn is None:
        with db._connect() as own_conn:
            return backup_purge_state(conn=own_conn)
    t, p = sm.backup_purge_state, sm.purged_files
    with conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t).where(t.c.key == _PURGE_STATE_KEY)).fetchone()
        pending = sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(p).where(
            p.c.remote_done.is_(None))).fetchone()
    row = dict(row) if row else {"last_pass_at": None, "last_success_at": None,
                                 "closed": 0, "denied": 0, "failed": 0}
    row["pending"] = int(pending["n"])
    return row
