"""test_db_backup_store.py — the backup / purged-files / demo-refresh statements of
`core/db.py` against a real Postgres.

Stage 3 of the SQLAlchemy Core move converts these: `demo_refresh_completion*`,
`backup_journal_*`, `backup_version_*`, `backup_tombstone_rows`, `purged_file*`. The
file pins each function's result, the idempotence/upsert behavior, the savepoint
discipline of `backup_journal_commit` and the choice between "own connection" and
"the caller's connection" — written and green on the hand-written SQL first, unedited
on Core.
"""
import os
import sys
import unittest
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_backup  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_backup_store")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE backup_journal, backup_versions, backup_version_tombstones, "
                        "purged_files, demo_refresh_completion")

    def rows(self, sql, params=()):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def receipt(file_id, remote_name, size, sha1="a" * 40, sha256="s256"):
    return {"file_id": file_id, "remote_name": remote_name, "sha1": sha1, "size": size,
            "sha256": sha256}


# ---- demo refresh completion ---------------------------------------------------

D1, D2 = date(2026, 11, 2), date(2026, 11, 3)


class TestDemoRefreshCompletion(DbCase):
    def test_status_is_none_until_something_is_recorded(self):
        self.assertIsNone(db_backup.demo_refresh_completion_status())

    def test_an_invalid_outcome_is_refused_before_touching_the_database(self):
        with self.assertRaises(ValueError):
            db_backup.demo_refresh_completion("green", D1)
        self.assertIsNone(db_backup.demo_refresh_completion_status())

    def test_success_records_both_audit_facts(self):
        db_backup.demo_refresh_completion("success", D1)
        status = db_backup.demo_refresh_completion_status()
        self.assertEqual(set(status), {"attempted_for", "outcome", "last_attempt_at",
                                       "success_for", "last_success_at"})
        self.assertEqual((status["attempted_for"], status["outcome"], status["success_for"]),
                         (D1, "success", D1))
        self.assertIsInstance(status["last_attempt_at"], datetime)
        self.assertIsInstance(status["last_success_at"], datetime)

    def test_a_first_degraded_attempt_has_no_success_facts(self):
        db_backup.demo_refresh_completion("partial", D1)
        status = db_backup.demo_refresh_completion_status()
        self.assertEqual((status["outcome"], status["attempted_for"]), ("partial", D1))
        self.assertIsNone(status["success_for"])
        self.assertIsNone(status["last_success_at"])

    def test_a_degraded_attempt_keeps_the_previous_success_facts(self):
        db_backup.demo_refresh_completion("success", D1)
        first = db_backup.demo_refresh_completion_status()
        db_backup.demo_refresh_completion("failed", D2)
        status = db_backup.demo_refresh_completion_status()
        self.assertEqual((status["outcome"], status["attempted_for"]), ("failed", D2))
        self.assertEqual(status["success_for"], D1)
        self.assertEqual(status["last_success_at"], first["last_success_at"])
        self.assertGreaterEqual(status["last_attempt_at"], first["last_attempt_at"])

    def test_a_later_success_moves_the_success_facts(self):
        db_backup.demo_refresh_completion("success", D1)
        first = db_backup.demo_refresh_completion_status()
        db_backup.demo_refresh_completion("success", D2)
        status = db_backup.demo_refresh_completion_status()
        self.assertEqual((status["success_for"], status["attempted_for"]), (D2, D2))
        self.assertGreater(status["last_success_at"], first["last_success_at"])
        self.assertEqual(len(self.rows("SELECT job FROM demo_refresh_completion")), 1)

    def test_on_the_callers_connection_the_row_commits_with_the_caller(self):
        with db._connect() as conn:
            db_backup.demo_refresh_completion("success", D1, conn=conn)
            self.assertEqual(db_backup.demo_refresh_completion_status(conn=conn)["success_for"], D1)
            conn.rollback()
        self.assertIsNone(db_backup.demo_refresh_completion_status())
        with db._connect() as conn:
            db_backup.demo_refresh_completion("success", D1, conn=conn)
        self.assertEqual(db_backup.demo_refresh_completion_status()["outcome"], "success")


# ---- backup journal ----------------------------------------------------------------

class TestBackupJournal(DbCase):
    def test_map_is_empty_then_keyed_by_path_with_the_seven_columns(self):
        self.assertEqual(db_backup.backup_journal_map(), {})
        db_backup.backup_journal_commit("a/one.txt", "sha-one", 10, 111, "remote/one", origin="upload")
        db_backup.backup_journal_commit("b/two.txt", "sha-two", 20, 222, "remote/two", origin="restore")
        journal = db_backup.backup_journal_map()
        self.assertEqual(set(journal), {"a/one.txt", "b/two.txt"})
        self.assertEqual(journal["a/one.txt"], {
            "path": "a/one.txt", "sha256": "sha-one", "size": 10, "mtime_ns": 111,
            "remote_name": "remote/one", "remote_file_id": None, "origin": "upload"})
        self.assertEqual(journal["b/two.txt"]["origin"], "restore")

    def test_commit_defaults_the_origin_and_coerces_numbers(self):
        db_backup.backup_journal_commit("p", "sha", "7", "9", "remote/p")
        row = self.rows("SELECT * FROM backup_journal")[0]
        self.assertEqual((row["origin"], row["size"], row["mtime_ns"]), ("upload", 7, 9))
        self.assertIsInstance(row["uploaded_at"], datetime)

    def test_commit_is_an_upsert_by_path_that_refreshes_uploaded_at(self):
        db_backup.backup_journal_commit("p", "sha1", 1, 1, "remote/p1")
        first = self.rows("SELECT * FROM backup_journal")[0]
        db_backup.backup_journal_commit("p", "sha2", 2, 2, "remote/p2", origin="restore")
        (row,) = self.rows("SELECT * FROM backup_journal")
        self.assertEqual((row["sha256"], row["size"], row["mtime_ns"], row["remote_name"],
                          row["origin"]), ("sha2", 2, 2, "remote/p2", "restore"))
        self.assertGreater(row["uploaded_at"], first["uploaded_at"])

    def test_commit_with_a_receipt_stores_the_file_id_and_the_immutable_version(self):
        db_backup.backup_journal_commit("p", "sha256-p", 5, 1, "remote/p",
                                 receipt=receipt("fid-1", "remote/p", 5, sha256="sha256-p"))
        (row,) = self.rows("SELECT * FROM backup_journal")
        self.assertEqual(row["remote_file_id"], "fid-1")
        versions = db_backup.backup_version_rows()
        self.assertEqual(set(versions), {"fid-1"})
        self.assertEqual(
            {k: versions["fid-1"][k] for k in ("file_id", "remote_name", "sha256", "sha1",
                                               "size", "journal_path")},
            {"file_id": "fid-1", "remote_name": "remote/p", "sha256": "sha256-p",
             "sha1": "a" * 40, "size": 5, "journal_path": "p"})
        self.assertIsInstance(versions["fid-1"]["accepted_at"], datetime)

    def test_a_malformed_receipt_leaves_journal_and_ledger_untouched(self):
        for bad in ("not a mapping",
                    receipt("", "remote/p", 5),
                    receipt("fid", "other/name", 5),
                    receipt("fid", "remote/p", 5, sha1="short"),
                    receipt("fid", "remote/p", 5, sha1="z" * 40),
                    receipt("fid", "remote/p", 6)):
            with self.assertRaises(ValueError):
                db_backup.backup_journal_commit("p", "sha", 5, 1, "remote/p", receipt=bad)
        self.assertEqual(self.rows("SELECT * FROM backup_journal"), [])
        self.assertEqual(self.rows("SELECT * FROM backup_versions"), [])

    def test_a_conflicting_receipt_rolls_back_only_its_own_savepoint(self):
        db_backup.backup_journal_commit("first", "sha-1", 5, 1, "remote/first",
                                 receipt=receipt("fid-1", "remote/first", 5, sha256="sha-1"))
        with db._connect() as conn:
            db_backup.purged_file_record("kept/by/caller.txt", "retention", conn=conn)
            with self.assertRaises(ValueError):
                # same immutable file id, different bytes
                db_backup.backup_journal_commit("second", "sha-2", 5, 2, "remote/second", conn=conn,
                                         receipt=receipt("fid-1", "remote/second", 5, sha256="sha-2"))
            # the caller's earlier work is still alive in the same transaction
            self.assertEqual(db_backup.purged_file_paths(conn=conn), {"kept/by/caller.txt"})
        self.assertEqual(db_backup.purged_file_paths(), {"kept/by/caller.txt"})
        self.assertEqual(set(db_backup.backup_journal_map()), {"first"})
        self.assertEqual(set(db_backup.backup_version_rows()), {"fid-1"})

    def test_commit_on_the_callers_connection_commits_with_the_caller(self):
        with db._connect() as conn:
            db_backup.backup_journal_commit("p", "sha", 1, 1, "remote/p", conn=conn,
                                     receipt=receipt("fid", "remote/p", 1, sha256="sha"))
            conn.rollback()
        self.assertEqual(db_backup.backup_journal_map(), {})
        self.assertEqual(db_backup.backup_version_rows(), {})

    def test_refresh_stat_updates_only_size_and_mtime(self):
        db_backup.backup_journal_commit("p", "sha", 1, 1, "remote/p", origin="restore")
        before = self.rows("SELECT * FROM backup_journal")[0]
        db_backup.backup_journal_refresh_stat("p", "50", "60")
        after = self.rows("SELECT * FROM backup_journal")[0]
        self.assertEqual((after["size"], after["mtime_ns"]), (50, 60))
        for column in ("sha256", "remote_name", "origin", "uploaded_at", "remote_file_id"):
            self.assertEqual(after[column], before[column], column)
        db_backup.backup_journal_refresh_stat("missing", 1, 2)  # no row: nothing happens
        self.assertEqual(len(self.rows("SELECT * FROM backup_journal")), 1)

    def test_paths_like_is_a_literal_prefix_match_in_path_order(self):
        for path in ("docs/b.txt", "docs/a.txt", "docs/sub/c.txt", "docsx/d.txt", "other/e.txt",
                     "100%/f.txt", "1000/g.txt"):
            db_backup.backup_journal_commit(path, "sha", 1, 1, "remote/" + path)
        self.assertEqual(db_backup.backup_journal_paths_like("docs/"),
                         ["docs/a.txt", "docs/b.txt", "docs/sub/c.txt"])
        self.assertEqual(db_backup.backup_journal_paths_like("100%"), ["100%/f.txt"])
        self.assertEqual(db_backup.backup_journal_paths_like("nothing/"), [])


class TestBackupVersions(DbCase):
    def test_record_is_idempotent_for_an_exact_replay(self):
        rec = receipt("fid", "remote/x", 3)
        self.assertIsNone(db_backup.backup_version_record("x", rec))
        self.assertIsNone(db_backup.backup_version_record("x", rec))
        self.assertEqual(len(self.rows("SELECT * FROM backup_versions")), 1)

    def test_record_refuses_a_different_receipt_for_the_same_file_id(self):
        db_backup.backup_version_record("x", receipt("fid", "remote/x", 3))
        for other_path, other in (("y", receipt("fid", "remote/x", 3)),
                                  ("x", receipt("fid", "remote/x", 3, sha1="b" * 40)),
                                  ("x", receipt("fid", "remote/x", 3, sha256="other"))):
            with self.assertRaises(ValueError):
                db_backup.backup_version_record(other_path, other)
        (row,) = self.rows("SELECT * FROM backup_versions")
        self.assertEqual((row["journal_path"], row["sha1"], row["sha256"]), ("x", "a" * 40, "s256"))

    def test_record_accepts_the_b2_field_spellings(self):
        db_backup.backup_version_record("x", {"fileId": "fid-b2", "fileName": "remote/x",
                                       "contentSha1": "c" * 40, "contentLength": 4,
                                       "sha256": "s"})
        (row,) = self.rows("SELECT * FROM backup_versions")
        self.assertEqual((row["file_id"], row["remote_name"], row["sha1"], row["size"]),
                         ("fid-b2", "remote/x", "c" * 40, 4))

    def test_record_on_the_callers_connection_rolls_back_with_it(self):
        with db._connect() as conn:
            db_backup.backup_version_record("x", receipt("fid", "remote/x", 3), conn=conn)
            conn.rollback()
        self.assertEqual(db_backup.backup_version_rows(), {})

    def test_rows_are_keyed_by_file_id(self):
        db_backup.backup_version_record("x", receipt("f1", "remote/x", 3))
        db_backup.backup_version_record("y", receipt("f2", "remote/y", 4))
        self.assertEqual(set(db_backup.backup_version_rows()), {"f1", "f2"})
        self.assertEqual(db_backup.backup_version_rows()["f2"]["journal_path"], "y")


class TestTombstones(DbCase):
    def test_rows_are_empty_then_keyed_by_file_id(self):
        self.assertEqual(db_backup.backup_tombstone_rows(), {})
        db_backup.backup_version_tombstone("f1", "remote/x")
        db_backup.backup_version_tombstone("f2", "remote/y")
        rows = db_backup.backup_tombstone_rows()
        self.assertEqual(set(rows), {"f1", "f2"})
        self.assertEqual(set(rows["f1"]), {"file_id", "remote_name", "deleted_at"})
        self.assertEqual(rows["f1"]["remote_name"], "remote/x")
        self.assertIsInstance(rows["f1"]["deleted_at"], datetime)

    def test_recording_twice_is_idempotent_and_keeps_the_first_deletion_time(self):
        db_backup.backup_version_tombstone("f1", "remote/x")
        first = db_backup.backup_tombstone_rows()["f1"]["deleted_at"]
        self.assertIsNone(db_backup.backup_version_tombstone("f1", "remote/x"))
        self.assertEqual(db_backup.backup_tombstone_rows()["f1"]["deleted_at"], first)

    def test_a_different_remote_name_for_the_same_id_is_refused(self):
        db_backup.backup_version_tombstone("f1", "remote/x")
        with self.assertRaises(ValueError):
            db_backup.backup_version_tombstone("f1", "remote/other")

    def test_ids_and_names_are_required(self):
        for args in (("", "n"), ("f", ""), (None, "n"), ("f", None), (1, "n")):
            with self.assertRaises(ValueError):
                db_backup.backup_version_tombstone(*args)
        self.assertEqual(db_backup.backup_tombstone_rows(), {})

    def test_on_the_callers_connection_rolls_back_with_it(self):
        with db._connect() as conn:
            db_backup.backup_version_tombstone("f1", "remote/x", conn=conn)
            conn.rollback()
        self.assertEqual(db_backup.backup_tombstone_rows(), {})


# ---- purged files ----------------------------------------------------------------------

class TestPurgedFiles(DbCase):
    def test_record_commits_on_its_own_connection(self):
        self.assertIsNone(db_backup.purged_file_record("a/b.pdf", "retention"))
        (row,) = self.rows("SELECT * FROM purged_files")
        self.assertEqual((row["path"], row["reason"], row["remote_done"]), ("a/b.pdf", "retention", None))
        self.assertIsInstance(row["purged_at"], datetime)
        self.assertEqual(db_backup.purged_file_paths(), {"a/b.pdf"})

    def test_record_keeps_the_first_reason_and_defaults_it(self):
        db_backup.purged_file_record("a/b.pdf", "subject erase")
        db_backup.purged_file_record("a/b.pdf", "something else")
        db_backup.purged_file_record("c.pdf", None)
        db_backup.purged_file_record("d.pdf", "")
        reasons = {r["path"]: r["reason"] for r in self.rows("SELECT * FROM purged_files")}
        self.assertEqual(reasons, {"a/b.pdf": "subject erase", "c.pdf": "retention",
                                   "d.pdf": "retention"})

    def test_record_refuses_unsafe_paths(self):
        for bad in ("", "/abs/path", "a/../b", "../b", None, 5):
            with self.assertRaises(ValueError):
                db_backup.purged_file_record(bad, "retention")
        self.assertEqual(db_backup.purged_file_paths(), set())

    def test_record_on_the_callers_connection_waits_for_the_caller(self):
        with db._connect() as conn:
            db_backup.purged_file_record("a.pdf", "retention", conn=conn)
            self.assertEqual(db_backup.purged_file_paths(), set())  # not committed yet
            self.assertEqual(db_backup.purged_file_paths(conn=conn), {"a.pdf"})
            conn.rollback()
        self.assertEqual(db_backup.purged_file_paths(), set())

    def test_pending_lists_open_paths_oldest_first_then_by_path(self):
        for path in ("c.pdf", "b.pdf", "a.pdf", "old.pdf", "done.pdf"):
            db_backup.purged_file_record(path, "retention")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE purged_files SET purged_at = purged_at - interval '1 day' "
                        "WHERE path = 'old.pdf'")
            cur.execute("UPDATE purged_files SET remote_done = now() WHERE path = 'done.pdf'")
            cur.execute("UPDATE purged_files SET purged_at = '2030-01-01' WHERE path IN "
                        "('a.pdf', 'b.pdf', 'c.pdf')")
        self.assertEqual(db_backup.purged_files_pending(), ["old.pdf", "a.pdf", "b.pdf", "c.pdf"])
        self.assertEqual(db_backup.purged_files_pending(limit=2), ["old.pdf", "a.pdf"])
        self.assertEqual(db_backup.purged_files_pending(limit="1"), ["old.pdf"])

    def test_remote_done_closes_the_path_and_forgets_its_journal_row(self):
        db_backup.purged_file_record("a.pdf", "retention")
        db_backup.backup_journal_commit("a.pdf", "sha", 1, 1, "remote/a")
        db_backup.backup_journal_commit("keep.pdf", "sha", 1, 1, "remote/keep")
        self.assertIsNone(db_backup.purged_file_remote_done("a.pdf"))
        (row,) = self.rows("SELECT * FROM purged_files")
        self.assertIsInstance(row["remote_done"], datetime)
        self.assertEqual(db_backup.purged_files_pending(), [])
        self.assertEqual(set(db_backup.backup_journal_map()), {"keep.pdf"})
        first = row["remote_done"]
        db_backup.backup_journal_commit("a.pdf", "sha", 1, 1, "remote/a")
        db_backup.purged_file_remote_done("a.pdf")  # closed already: timestamp stays, journal row goes
        self.assertEqual(self.rows("SELECT remote_done FROM purged_files")[0]["remote_done"], first)
        self.assertEqual(set(db_backup.backup_journal_map()), {"keep.pdf"})

    def test_remote_done_for_an_unknown_path_still_clears_a_journal_row(self):
        db_backup.backup_journal_commit("ghost.pdf", "sha", 1, 1, "remote/ghost")
        db_backup.purged_file_remote_done("ghost.pdf")
        self.assertEqual(db_backup.backup_journal_map(), {})
        self.assertEqual(self.rows("SELECT * FROM purged_files"), [])

    def test_remote_done_on_the_callers_connection_waits_for_the_caller(self):
        db_backup.purged_file_record("a.pdf", "retention")
        with db._connect() as conn:
            db_backup.purged_file_remote_done("a.pdf", conn=conn)
            conn.rollback()
        self.assertEqual(db_backup.purged_files_pending(), ["a.pdf"])


if __name__ == "__main__":
    unittest.main()
