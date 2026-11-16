"""test_study_store_db.py — `modules/turnos/study_store.py` against a real Postgres.

The adapter owns the four `study_*` tables. `test_study.StudyStore` walks one happy
path through it and `StudyStoreOffline` pins a few statements against a mock cursor;
this file pins every function's RESULT in the database — the rows it writes, what it
returns, the ordering, the branches of the `COALESCE` / `CASE` update, the retention
purge, the `SKIP LOCKED` behavior — so the statements can change spelling (stage 2 of
the SQLAlchemy Core move) with the same observable behavior.
"""
import hashlib
import os
import sys
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

from app.core import db  # noqa: E402
from app.modules.turnos import study, study_store  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_study_store_db")


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


def sql(text, params=()):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(text, params)
        return cur.fetchall() if cur.description else None


def event(task, t=0, kind="nav", path="/", **target):
    payload = {"task_id": task, "kind": kind, "t": t, "path": path}
    if target:
        payload["target"] = target
    return payload


class StudyStoreDb(unittest.TestCase):
    def setUp(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)
        for table in ("study_events", "study_task_results", "study_findings", "study_sessions"):
            sql(f"DELETE FROM {table}")
        self.task = sorted(study.task_ids("patient"))[0]
        self.token = study_store.create_session("patient", "es", True, "mobile")

    def row_id(self):
        return study_store.session(self.token)["id"]

    # ---- create_session / session -------------------------------------

    def test_create_session_stores_only_the_digest_and_the_closed_values(self):
        (row,) = sql("SELECT * FROM study_sessions")
        self.assertEqual(row["token_hash"], hashlib.sha256(self.token.encode()).hexdigest())
        self.assertEqual((row["role"], row["locale"], row["user_agent_class"]), ("patient", "es", "mobile"))
        self.assertIsNotNone(row["consent_at"])
        self.assertIsNotNone(row["started_at"])
        self.assertIsNone(row["finished_at"])

    def test_unknown_locale_falls_back_to_spanish_and_english_is_kept(self):
        study_store.create_session("doctor", "fr", True, "desktop")
        english = study_store.create_session("admin", "en", True, "tablet")
        self.assertEqual(sorted(r["locale"] for r in sql("SELECT locale FROM study_sessions WHERE role<>'patient'")),
                         ["en", "es"])
        self.assertEqual(study_store.session(english)["user_agent_class"], "tablet")

    def test_create_session_rejects_without_consent_unknown_role_or_agent_class(self):
        for args in (("patient", "es", False, "mobile"), ("patient", "es", None, "mobile"),
                     ("nurse", "es", True, "mobile"), ("patient", "es", True, "watch")):
            with self.assertRaises(ValueError):
                study_store.create_session(*args)
        self.assertEqual(len(sql("SELECT 1 FROM study_sessions")), 1)

    def test_session_lookup_returns_the_row_or_none(self):
        row = study_store.session(self.token)
        self.assertEqual((row["role"], row["locale"]), ("patient", "es"))
        self.assertIsNone(study_store.session("x" * 40))
        self.assertIsNone(study_store.session("short"))
        self.assertIsNone(study_store.session(None))
        self.assertIsNone(study_store.session("x" * 129))

    # ---- add_events ----------------------------------------------------

    def test_add_events_stores_sanitized_rows_in_order_and_returns_the_count(self):
        n = study_store.add_events(self.token, [
            event(self.task, 5, "click", "/account?dni=1", tag="BUTTON", interactive=True, index=3),
            event(self.task, 9, "nav", "/")])
        self.assertEqual(n, 2)
        rows = sql("SELECT * FROM study_events ORDER BY id")
        self.assertEqual([r["t"] for r in rows], [5, 9])
        self.assertEqual(rows[0]["path"], "/account")
        self.assertEqual(rows[0]["target"], {"tag": "button", "interactive": True, "index": 3})
        self.assertEqual(rows[1]["target"], {"tag": "other", "interactive": False, "index": 0})
        self.assertEqual({r["session_id"] for r in rows}, {self.row_id()})
        self.assertEqual({r["task_id"] for r in rows}, {self.task})

    def test_add_events_rejects_bad_batches_and_writes_nothing(self):
        for bad in ([], "x", [event(self.task)] * 101):
            with self.assertRaises(ValueError):
                study_store.add_events(self.token, bad)
        with self.assertRaises(ValueError):
            study_store.add_events(self.token, [event(self.task), event("NOPE")])
        self.assertEqual(sql("SELECT count(*) AS n FROM study_events")[0]["n"], 0)

    def test_add_events_caps_a_session_at_3000_rows(self):
        sql("""INSERT INTO study_events(session_id,task_id,t,kind,path)
               SELECT %s,%s,0,'nav','/' FROM generate_series(1,2990)""", (self.row_id(), self.task))
        self.assertEqual(study_store.add_events(self.token, [event(self.task, i) for i in range(20)]), 10)
        self.assertEqual(sql("SELECT count(*) AS n FROM study_events")[0]["n"], 3000)
        self.assertEqual(study_store.add_events(self.token, [event(self.task)]), 0)
        self.assertEqual(sql("SELECT count(*) AS n FROM study_events")[0]["n"], 3000)

    def test_add_events_counts_only_the_sessions_own_events(self):
        other = study_store.create_session("patient", "es", True, "mobile")
        study_store.add_events(other, [event(self.task)] * 5)
        self.assertEqual(study_store.add_events(self.token, [event(self.task)] * 3), 3)

    def test_unknown_session_and_finished_session_refuse_writes(self):
        with self.assertRaises(LookupError):
            study_store.add_events("z" * 40, [event(self.task)])
        study_store.finish(self.token, {})
        with self.assertRaises(ValueError):
            study_store.add_events(self.token, [event(self.task)])
        with self.assertRaises(ValueError):
            study_store.save_result(self.token, {"task_id": self.task, "outcome": "ok", "duration_ms": 1})

    # ---- save_result / completed ---------------------------------------

    def result(self, **over):
        base = {"task_id": self.task, "outcome": "ok", "ease": 4, "comment": "bien", "duration_ms": 2500}
        base.update(over)
        return base

    def test_save_result_writes_the_row_with_the_start_derived_from_the_duration(self):
        self.assertEqual(study_store.save_result(self.token, self.result()), {"task_id": self.task})
        (row,) = sql("SELECT * FROM study_task_results")
        self.assertEqual((row["outcome"], row["ease"], row["comment"], row["duration_ms"]), ("ok", 4, "bien", 2500))
        self.assertEqual(row["ended_at"] - row["started_at"], timedelta(milliseconds=2500))

    def test_the_first_result_of_a_task_wins_and_a_repeat_is_ignored(self):
        study_store.save_result(self.token, self.result(outcome="stuck", ease=1))
        self.assertEqual(study_store.save_result(self.token, self.result(outcome="ok", ease=5)),
                         {"task_id": self.task})
        (row,) = sql("SELECT * FROM study_task_results")
        self.assertEqual((row["outcome"], row["ease"]), ("stuck", 1))

    def test_save_result_scrubs_the_comment_and_rejects_bad_payloads(self):
        study_store.save_result(self.token, self.result(comment="Escribí a ana@example.org"))
        self.assertEqual(sql("SELECT comment FROM study_task_results")[0]["comment"], "Escribí a [dato omitido]")
        for bad in (self.result(outcome="great"), self.result(task_id="NOPE"), self.result(ease=9)):
            with self.assertRaises(ValueError):
                study_store.save_result(self.token, bad)
        with self.assertRaises(LookupError):
            study_store.save_result("z" * 40, self.result())

    def test_completed_lists_the_sessions_task_outcomes_only(self):
        other = study_store.create_session("patient", "es", True, "mobile")
        study_store.save_result(self.token, self.result(outcome="doubt"))
        study_store.save_result(other, self.result(outcome="stuck"))
        self.assertEqual(study_store.completed(self.token), [{"task_id": self.task, "outcome": "doubt"}])
        with self.assertRaises(LookupError):
            study_store.completed("z" * 40)

    # ---- finish --------------------------------------------------------

    def test_finish_skips_unanswered_tasks_records_the_answers_and_closes_the_session(self):
        study_store.save_result(self.token, self.result(outcome="stuck", ease=1))
        study_store.finish(self.token, {"F1": "Nada", "F2": "Buscar", "F3": "yes"})
        rows = {r["task_id"]: r for r in sql("SELECT * FROM study_task_results")}
        self.assertEqual(set(rows), set(study.task_ids("patient")) | {"F1", "F2", "F3"})
        self.assertEqual(rows[self.task]["outcome"], "stuck", "an answered task is not overwritten")
        for tid in study.task_ids("patient") - {self.task}:
            self.assertEqual((rows[tid]["outcome"], rows[tid]["duration_ms"], rows[tid]["comment"]), ("skipped", 0, ""))
        self.assertEqual((rows["F1"]["outcome"], rows["F1"]["comment"]), ("ok", "Nada"))
        self.assertEqual(rows["F3"]["comment"], "yes")
        self.assertIsNotNone(study_store.session(self.token)["finished_at"])

    def test_finishing_again_changes_nothing_and_bad_answers_are_refused(self):
        study_store.finish(self.token, {"F1": "uno"})
        before = sql("SELECT * FROM study_task_results ORDER BY id")
        finished = study_store.session(self.token)["finished_at"]
        self.assertIsNone(study_store.finish(self.token, {"F1": "dos"}))
        self.assertEqual(sql("SELECT * FROM study_task_results ORDER BY id"), before)
        self.assertEqual(study_store.session(self.token)["finished_at"], finished)
        for bad in ("nope", {"F3": "perhaps"}):
            with self.assertRaises(ValueError):
                study_store.finish(self.token, bad)
        with self.assertRaises(LookupError):
            study_store.finish("z" * 40, {})

    # ---- report / run_analysis ----------------------------------------

    def test_report_lists_sessions_results_with_role_and_findings_in_order(self):
        second = study_store.create_session("doctor", "en", True, "desktop")
        study_store.save_result(self.token, self.result())
        doctor_task = sorted(study.task_ids("doctor"))[0]
        study_store.save_result(second, {"task_id": doctor_task, "outcome": "ok", "duration_ms": 10})
        a = study_store.add_finding({"role": "admin", "task_id": "F1", "title": "A"}, "me")
        b = study_store.add_finding({"role": "admin", "task_id": "F2", "title": "B"}, "me")
        report = study_store.report()
        self.assertEqual([s["role"] for s in report["sessions"]], ["patient", "doctor"])
        self.assertEqual(set(report["sessions"][0]), {"id", "role", "locale", "started_at", "finished_at"})
        self.assertEqual([(r["session_id"], r["role"], r["task_id"]) for r in report["results"]],
                         sorted((r["session_id"], r["role"], r["task_id"]) for r in report["results"]))
        self.assertEqual({r["role"] for r in report["results"]}, {"patient", "doctor"})
        self.assertIn("duration_ms", report["results"][0])
        self.assertEqual([f["id"] for f in report["findings"]], [a, b])

    def stuck_session(self, count=3):
        for _ in range(count):
            token = study_store.create_session("patient", "es", True, "mobile")
            study_store.save_result(token, self.result(outcome="stuck", ease=1, duration_ms=90000))

    def test_run_analysis_writes_auto_findings_and_rerunning_refreshes_without_duplicating(self):
        self.stuck_session()
        count = study_store.run_analysis()
        self.assertGreater(count, 0)
        findings = sql("SELECT * FROM study_findings ORDER BY id")
        self.assertEqual(len(findings), count)
        self.assertEqual({f["source"] for f in findings}, {"auto"})
        self.assertEqual({f["status"] for f in findings}, {"found"})
        first = findings[0]
        self.assertEqual(study_store.run_analysis(), count)
        again = sql("SELECT * FROM study_findings ORDER BY id")
        self.assertEqual([f["id"] for f in again], [f["id"] for f in findings])
        self.assertGreaterEqual(again[0]["updated_at"], first["updated_at"])

    def test_rerun_keeps_a_reviewers_decision_but_refreshes_the_evidence(self):
        self.stuck_session()
        study_store.run_analysis()
        finding = sql("SELECT * FROM study_findings ORDER BY id")[0]
        study_store.update_finding(finding["id"], {"status": "fixed", "fix_note": "Repaired"}, "owner")
        sql("UPDATE study_findings SET evidence_summary='{}'::jsonb, detail='stale' WHERE id=%s", (finding["id"],))
        study_store.run_analysis()
        (after,) = sql("SELECT * FROM study_findings WHERE id=%s", (finding["id"],))
        self.assertEqual((after["status"], after["fix_note"], after["decided_by"]), ("fixed", "Repaired", "owner"))
        self.assertNotEqual(after["detail"], "stale")
        self.assertNotEqual(after["evidence_summary"], {})

    def test_analysis_with_no_data_adds_nothing(self):
        sql("DELETE FROM study_task_results")
        self.assertEqual(study_store.run_analysis(), 0)
        self.assertEqual(sql("SELECT count(*) AS n FROM study_findings")[0]["n"], 0)

    # ---- findings ------------------------------------------------------

    def test_add_finding_stores_a_manual_finding_and_returns_its_id(self):
        fid = study_store.add_finding({"role": "doctor", "task_id": "F1", "title": " Falta un botón ",
                                       "detail": "Escribió ana@example.org", "severity": "high"}, "reviewer")
        (row,) = sql("SELECT * FROM study_findings WHERE id=%s", (fid,))
        self.assertEqual((row["source"], row["role"], row["task_id"], row["severity"], row["status"]),
                         ("manual", "doctor", "F1", "high", "found"))
        self.assertEqual((row["title"], row["detail"]), ("Falta un botón", "Escribió [dato omitido]"))
        self.assertEqual(row["decided_by"], "reviewer")
        self.assertIsNotNone(row["decided_at"])
        self.assertEqual(study_store.add_finding({"role": "doctor", "task_id": "F1", "title": "x"}, "r") > fid, True)
        self.assertEqual(sql("SELECT severity FROM study_findings ORDER BY id DESC LIMIT 1")[0]["severity"], "medium")

    def test_add_finding_truncates_the_title_and_rejects_bad_input(self):
        fid = study_store.add_finding({"role": "admin", "task_id": "F2", "title": "t" * 300}, "r")
        self.assertEqual(len(sql("SELECT title FROM study_findings WHERE id=%s", (fid,))[0]["title"]), 200)
        for bad in ({"role": "x", "task_id": "F1", "title": "t"}, {"role": "admin", "task_id": "NOPE", "title": "t"},
                    {"role": "admin", "task_id": "F1", "title": "  "},
                    {"role": "admin", "task_id": "F1", "title": "t", "severity": "critical"}):
            with self.assertRaises(ValueError):
                study_store.add_finding(bad, "r")

    def manual(self, **over):
        fid = study_store.add_finding({"role": "admin", "task_id": "F1", "title": "T", "detail": "original"}, "writer")
        return fid

    def auto(self):
        self.stuck_session()
        study_store.run_analysis()
        return sql("SELECT id, detail FROM study_findings WHERE source='auto' ORDER BY id")[0]

    def test_status_only_update_keeps_the_fix_evidence_and_stamps_the_decision(self):
        fid = self.manual()
        study_store.update_finding(fid, {"status": "fixed", "fix_note": "N", "commit": "abc1234",
                                         "live_url": "https://example.org/x"}, "o1")
        study_store.update_finding(fid, {"status": "in_progress"}, "o2")
        (row,) = sql("SELECT * FROM study_findings WHERE id=%s", (fid,))
        self.assertEqual((row["status"], row["fix_note"], row["commit"], row["live_url"], row["detail"]),
                         ("in_progress", "N", "abc1234", "https://example.org/x", "original"))
        self.assertEqual(row["decided_by"], "o2")
        self.assertIsNotNone(row["decided_at"])
        self.assertGreaterEqual(row["updated_at"], row["created_at"])

    def test_update_replaces_only_the_fields_it_carries(self):
        fid = self.manual()
        study_store.update_finding(fid, {"status": "fixed", "fix_note": "uno", "commit": "abcdef1"}, "o")
        study_store.update_finding(fid, {"status": "fixed", "live_url": "https://example.org/" + "u" * 2000}, "o")
        (row,) = sql("SELECT * FROM study_findings WHERE id=%s", (fid,))
        self.assertEqual((row["fix_note"], row["commit"]), ("uno", "abcdef1"))
        self.assertEqual(len(row["live_url"]), 1000, "the live URL is cut at 1000 characters")
        study_store.update_finding(fid, {"status": "dismissed", "fix_note": "dos"}, "o")
        self.assertEqual(sql("SELECT fix_note, live_url FROM study_findings WHERE id=%s", (fid,))[0]["fix_note"], "dos")

    def test_a_reviewer_corrects_the_detail_of_a_manual_finding_but_not_of_an_auto_one(self):
        fid = self.manual()
        study_store.update_finding(fid, {"status": "found", "detail": "Escribió ana@example.org"}, "o")
        self.assertEqual(sql("SELECT detail FROM study_findings WHERE id=%s", (fid,))[0]["detail"],
                         "Escribió [dato omitido]")
        auto = self.auto()
        study_store.update_finding(auto["id"], {"status": "dismissed", "detail": "rewritten"}, "o")
        (row,) = sql("SELECT detail, status FROM study_findings WHERE id=%s", (auto["id"],))
        self.assertEqual((row["detail"], row["status"]), (auto["detail"], "dismissed"))

    def test_update_finding_refuses_unknown_ids_and_bad_values(self):
        with self.assertRaises(LookupError):
            study_store.update_finding(987654, {"status": "fixed"}, "o")
        fid = self.manual()
        for bad in ({"status": "bogus"}, {"status": "fixed", "commit": "not-hex"},
                    {"status": "fixed", "live_url": "http://example.org/"}):
            with self.assertRaises(ValueError):
                study_store.update_finding(fid, bad, "o")

    # ---- purge ---------------------------------------------------------

    def age(self, session_id, days):
        sql("UPDATE study_sessions SET started_at=now()-%s*interval '1 day' WHERE id=%s", (days, session_id))

    def populated(self, days):
        token = study_store.create_session("patient", "es", True, "mobile")
        study_store.add_events(token, [event(self.task)] * 2)
        study_store.save_result(token, self.result())
        sid = study_store.session(token)["id"]
        self.age(sid, days)
        return token, sid

    def test_purge_removes_expired_sessions_with_their_children_and_keeps_the_rest(self):
        _, old = self.populated(40)
        self.age(self.row_id(), 1)
        self.assertEqual(study_store.purge(days=30), 1)
        self.assertEqual([r["id"] for r in sql("SELECT id FROM study_sessions")], [self.row_id()])
        self.assertEqual(sql("SELECT count(*) AS n FROM study_events WHERE session_id=%s", (old,))[0]["n"], 0)
        self.assertEqual(sql("SELECT count(*) AS n FROM study_task_results WHERE session_id=%s", (old,))[0]["n"], 0)
        self.assertEqual(study_store.purge(days=30), 0)

    def test_purge_defaults_to_the_retention_setting_and_floors_days_at_one(self):
        _, old = self.populated(2)
        self.assertEqual(study_store.purge(), 0, "the default retention is 90 days")
        self.assertEqual(study_store.purge(days=0), 1, "days below one count as one")
        self.assertIsNone(sql("SELECT id FROM study_sessions WHERE id=%s", (old,)) or None)

    def test_purge_honors_the_limit_oldest_first(self):
        ids = [self.populated(40 + i)[1] for i in range(3)]
        self.assertEqual(study_store.purge(days=30, limit=2), 2)
        self.assertEqual(sorted(r["id"] for r in sql("SELECT id FROM study_sessions WHERE id=ANY(%s)", (ids,))),
                         [ids[-1]], "the lowest ids go first (ORDER BY id)")

    def test_purge_skips_a_session_whose_row_another_writer_holds(self):
        _, held = self.populated(40)
        with db._connect() as other, other.cursor() as cur:
            cur.execute("SELECT id FROM study_sessions WHERE id=%s FOR UPDATE", (held,))
            self.assertEqual(study_store.purge(days=30), 0)
        self.assertEqual(sql("SELECT count(*) AS n FROM study_sessions WHERE id=%s", (held,))[0]["n"], 1)
        self.assertEqual(study_store.purge(days=30), 1)

    def test_purge_also_deletes_old_findings_up_to_the_limit(self):
        ids = [study_store.add_finding({"role": "admin", "task_id": "F1", "title": f"t{i}"}, "r") for i in range(3)]
        sql("UPDATE study_findings SET created_at=now()-interval '40 days' WHERE id=ANY(%s)", (ids[:2],))
        study_store.purge(days=30, limit=1)
        self.assertEqual(len(sql("SELECT id FROM study_findings")), 2)
        study_store.purge(days=30, limit=10)
        self.assertEqual([r["id"] for r in sql("SELECT id FROM study_findings")], [ids[2]])

    def test_purge_returns_the_number_of_sessions_not_of_findings(self):
        fid = study_store.add_finding({"role": "admin", "task_id": "F1", "title": "t"}, "r")
        sql("UPDATE study_findings SET created_at=now()-interval '40 days' WHERE id=%s", (fid,))
        self.assertEqual(study_store.purge(days=30), 0)
        self.assertEqual(sql("SELECT count(*) AS n FROM study_findings")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
