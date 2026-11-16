"""Durable full-backup evidence and a cross-process/cross-topology lock.

Query style (2026-10-25, SQLAlchemy Core stage 2): the statements are Core
constructs over `backup_runs` (`core/schema_model.py`) run on the pooled psycopg
connection through `core/sqlcore.py`; behavior is pinned in
`tests/test_backup_journal_store.py`."""
import contextlib
from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from app.core import db, sqlcore
from app.core.schema_model import backup_runs

DDL = """CREATE TABLE IF NOT EXISTS backup_runs (
    id text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL,
    archive_key text,
    size bigint NOT NULL DEFAULT 0,
    sha256 text,
    uploaded boolean NOT NULL DEFAULT false,
    integrity_verified boolean NOT NULL DEFAULT false,
    restore_tested boolean NOT NULL DEFAULT false,
    error text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);"""

_LOCK_KEY = "turnos:backup-full:v1"


def _lock_key():
    return cast(func.hashtext(_LOCK_KEY), BigInteger)


class Store:
    def __init__(self):
        self.conn = None

    @contextlib.contextmanager
    def lock(self):
        with db._connect() as conn:
            # The lock waits for another backup on purpose, and the web pool's lock limit
            # must not cut that wait: this session takes no limit while it holds the lock.
            with db.without_timeouts(conn):
                with conn.cursor() as cur:
                    sqlcore.run(cur, select(func.pg_advisory_lock(_lock_key())))
                self.conn = conn
                try:
                    yield self
                finally:
                    self.conn = None
                    try:
                        conn.rollback()
                        with conn.cursor() as cur:
                            sqlcore.run(cur, select(func.pg_advisory_unlock(_lock_key())))
                        conn.commit()
                    except Exception:
                        conn.close()  # never return a session lock to the pool
                        raise

    def save(self, row):
        if self.conn is None:
            raise RuntimeError("Backup journal write requires the backup advisory lock")
        with self.conn.cursor() as cur:
            insertion = insert(backup_runs).values(
                id=row["id"], started_at=row["started_at"], finished_at=row.get("finished_at"),
                status=row["status"], archive_key=row.get("archive_key"), size=row.get("size", 0),
                sha256=row.get("sha256"), uploaded=row.get("uploaded", False),
                integrity_verified=row.get("integrity_verified", False),
                restore_tested=row.get("restore_tested", False), error=row.get("error"), evidence=row)
            sqlcore.run(cur, insertion.on_conflict_do_update(
                index_elements=[backup_runs.c.id],
                set_={name: getattr(insertion.excluded, name) for name in (
                    "finished_at", "status", "archive_key", "size", "sha256", "uploaded",
                    "integrity_verified", "restore_tested", "error", "evidence")}))
        self.conn.commit()  # SESSION lock remains held

    def list(self):
        if self.conn is not None:
            return self._list(self.conn)
        with db._connect() as conn:
            return self._list(conn)

    @staticmethod
    def _list(conn):
        with conn.cursor() as cur:
            rows = sqlcore.run(cur, select(backup_runs.c.evidence).order_by(backup_runs.c.started_at.desc()))
            return [row["evidence"] for row in rows.fetchall()]
