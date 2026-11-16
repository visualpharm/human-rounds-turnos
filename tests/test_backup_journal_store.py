"""core/backup_journal.Store against a real Postgres: the full-backup evidence table
(`backup_runs`) and the session advisory lock that serializes backup runs.

`test_backup_full` drives the backup with an in-memory store; nothing there reads or writes the
real table. This file pins the real one: the lock is exclusive across sessions and released on
exit, `save` upserts one row per run id and keeps the whole row as `evidence`, `list` returns the
evidence documents newest first.
"""
import unittest

import _dbtest
from app.core import db
from app.core.backup_journal import Store

_database = _dbtest.PerModuleDb("sana_turnos_test_backup_journal_store")


def setUpModule():
    _database.set_up()


def tearDownModule():
    _database.tear_down()


LOCK_KEY = "turnos:backup-full:v1"


def row(run_id, started, **over):
    base = {"id": run_id, "started_at": started, "status": "running"}
    base.update(over)
    return base


def try_lock(cur):
    cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)::bigint) AS got", (LOCK_KEY,))
    return cur.fetchone()["got"]


class TestStore(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE backup_runs")

    def table(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM backup_runs ORDER BY id")
            return cur.fetchall()

    def test_save_requires_the_lock(self):
        with self.assertRaisesRegex(RuntimeError, "advisory lock"):
            Store().save(row("r1", "2026-10-25T10:00:00+00:00"))
        self.assertEqual(self.table(), [])

    def test_save_writes_the_columns_with_defaults_and_the_whole_row_as_evidence(self):
        store = Store()
        minimal = row("r1", "2026-10-25T10:00:00+00:00")
        with store.lock():
            store.save(minimal)
        (stored,) = self.table()
        self.assertEqual((stored["id"], stored["status"], stored["size"]), ("r1", "running", 0))
        self.assertEqual((stored["uploaded"], stored["integrity_verified"], stored["restore_tested"]),
                         (False, False, False))
        self.assertEqual((stored["finished_at"], stored["archive_key"], stored["sha256"], stored["error"]),
                         (None, None, None, None))
        self.assertEqual(stored["started_at"].isoformat(), "2026-10-25T10:00:00+00:00")
        self.assertEqual(stored["evidence"], minimal)

    def test_a_second_save_of_the_same_run_updates_everything_but_the_start(self):
        store = Store()
        with store.lock():
            store.save(row("r1", "2026-10-25T10:00:00+00:00"))
            done = row("r1", "2026-10-26T00:00:00+00:00", finished_at="2026-10-25T10:05:00+00:00", status="ok",
                       archive_key="k/r1.tar", size=1234, sha256="abc", uploaded=True,
                       integrity_verified=True, restore_tested=True, error="none", extra={"n": [1, 2]})
            store.save(done)
        (stored,) = self.table()
        self.assertEqual((stored["status"], stored["archive_key"], stored["size"], stored["sha256"], stored["error"]),
                         ("ok", "k/r1.tar", 1234, "abc", "none"))
        self.assertEqual((stored["uploaded"], stored["integrity_verified"], stored["restore_tested"]),
                         (True, True, True))
        self.assertEqual(stored["finished_at"].isoformat(), "2026-10-25T10:05:00+00:00")
        self.assertEqual(stored["started_at"].isoformat(), "2026-10-25T10:00:00+00:00",
                         "the conflict update never rewrites started_at")
        self.assertEqual(stored["evidence"], done)

    def test_save_commits_while_the_lock_stays_held(self):
        store = Store()
        with store.lock():
            store.save(row("r1", "2026-10-25T10:00:00+00:00"))
            self.assertEqual(len(self.table()), 1, "visible to another session before the lock is released")

    def test_list_returns_the_evidence_documents_newest_first_inside_and_outside_the_lock(self):
        store = Store()
        old, mid, new = (row("a", "2026-10-01T00:00:00+00:00"), row("b", "2026-10-10T00:00:00+00:00", status="ok"),
                         row("c", "2026-10-20T00:00:00+00:00"))
        with store.lock():
            for item in (mid, new, old):
                store.save(item)
            self.assertEqual(store.list(), [new, mid, old])
        self.assertEqual(Store().list(), [new, mid, old])

    def test_list_of_an_empty_journal_is_empty(self):
        self.assertEqual(Store().list(), [])

    def test_the_lock_excludes_other_sessions_until_it_exits(self):
        with db._connect() as other, other.cursor() as cur:
            with Store().lock():
                self.assertFalse(try_lock(cur))
            self.assertTrue(try_lock(cur), "the lock was released on exit")
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (LOCK_KEY,))

    def test_the_lock_is_released_after_a_failure_in_the_body(self):
        with self.assertRaises(ValueError):
            with Store().lock():
                raise ValueError("body")
        with db._connect() as other, other.cursor() as cur:
            self.assertTrue(try_lock(cur))
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (LOCK_KEY,))

    def test_the_lock_rolls_back_uncommitted_work_of_the_body(self):
        store = Store()
        with store.lock():
            with store.conn.cursor() as cur:
                cur.execute("INSERT INTO backup_runs(id, started_at, status) VALUES ('ghost', now(), 'x')")
        self.assertEqual(self.table(), [])


if __name__ == "__main__":
    unittest.main()
