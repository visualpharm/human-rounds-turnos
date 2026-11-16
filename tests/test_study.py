"""Anonymous real-participant study: privacy, deterministic analysis and storage."""
import hashlib
import json
import unittest
from unittest.mock import patch

from app.modules.turnos import study, study_store
import _dbtest

_db = _dbtest.PerModuleDb("sana_turnos_test_study")

def setUpModule():
    _db.set_up()

def tearDownModule():
    _db.tear_down()


class StudyPrivacy(unittest.TestCase):
    def test_definition_has_verbatim_seed_and_stop_flags(self):
        definition = study.definition("es")
        self.assertEqual([len(v["tasks"]) for v in definition["roles"].values()], [5, 5, 6])
        self.assertEqual(definition["roles"]["patient"]["tasks"][0]["instruction"],
                         "Encontrá qué médicos atienden Pediatría y en qué centro.")
        self.assertTrue(definition["roles"]["admin"]["tasks"][0]["stop_before_save"])
        self.assertEqual([q["id"] for q in definition["final_questions"]], ["F1", "F2", "F3"])

    def test_untrusted_events_cannot_store_values_text_ids_queries_or_pii(self):
        event = study.sanitize_event({"task_id": "P1", "t": 1500, "kind": "click",
            "path": "/admin/patients/36555444?name=Ana#secret",
            "target": {"tag": "INPUT", "index": 2, "interactive": True,
                       "text": "Ana Pérez", "value": "36555444", "id": "Ana"},
            "value": "private", "ip": "1.2.3.4"}, "patient")
        encoded = json.dumps(event)
        for forbidden in ("36555444", "Ana", "private", "1.2.3.4", "?", "#"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(event["path"], "/admin/patients/:item")
        self.assertEqual(event["target"], {"tag": "input", "index": 2, "interactive": True})
        self.assertEqual(study.safe_path("/Ana-Perez/36555444?x=secret"), "/other")

    def test_invalid_role_task_kind_and_numeric_bounds(self):
        for event in ({"task_id": "D1", "kind": "click", "t": 0},
                      {"task_id": "P1", "kind": "input", "t": 0},
                      {"task_id": "P1", "kind": "click", "t": -1}):
            with self.assertRaises(ValueError):
                study.sanitize_event(event, "patient")
        with self.assertRaises(ValueError):
            study.sanitize_result({"task_id": "P1", "outcome": "ok", "ease": 6}, "patient")

    def test_production_cannot_enable_study_and_retention_defaults_safely(self):
        with patch("app.modules.turnos.study.config.get", side_effect=lambda k, default: {"TURNOS_STUDY": "1", "TURNOS_DEMO_MODE": "0"}.get(k, default)):
            self.assertFalse(study.enabled())
            self.assertEqual(study.retention_days(), 90)
        with patch("app.modules.turnos.study.config.get", return_value="bad"):
            self.assertEqual(study.retention_days(), 90)

    def test_navigation_within_dead_click_window_suppresses_candidate(self):
        events = [{"session_id": 1, "task_id": "P1", "kind": "click", "t": 0,
                   "target": {"interactive": False}, "path": "/"},
                  {"session_id": 1, "task_id": "P1", "kind": "nav", "t": 1000, "path": "/doctors"},
                  {"session_id": 1, "task_id": "P1", "kind": "visibility", "t": 2000, "path": "/doctors"}]
        self.assertNotIn("dead_click", {c["kind"] for c in study.analyze([], events)})

    def test_abandonment_does_not_flag_an_active_participant(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        sessions = [{"id": 1, "finished_at": None}]
        events = [{"session_id": 1, "task_id": "P1", "kind": "nav", "t": 0,
                   "path": "/", "recorded_at": now}]
        self.assertNotIn("abandoned", {c["kind"] for c in study.analyze([], events, sessions, now)})
        events[0]["recorded_at"] = now - timedelta(minutes=31)
        self.assertIn("abandoned", {c["kind"] for c in study.analyze([], events, sessions, now)})

    def test_comments_redact_document_email_phone_and_full_name(self):
        text = study.safe_comment("Me llamo Ana Pérez, DNI 36.555.444, ana@example.org, +54 9 2254 555555")
        for private in ("Ana Pérez", "36.555.444", "ana@example.org", "2254"):
            self.assertNotIn(private, text)

    def test_analysis_flags_all_signals_and_preserves_decisions_on_rerun(self):
        results = [{"session_id": 1, "task_id": "P1", "outcome": "stuck", "ease": 1,
                    "duration_ms": 200000, "role": "patient"}]
        events = [{"session_id": 1, "task_id": "P1", "kind": "click", "t": 100,
                   "target": {"interactive": False}, "path": "/"},
                  {"session_id": 1, "task_id": "P1", "kind": "visibility", "t": 2000,
                   "path": "/"}]
        events += [{"session_id": 1, "task_id": "P1", "kind": "nav", "t": t,
                    "path": p} for t, p in enumerate(["/", "/doctors", "/", "/doctors", "/"])]
        events.append({"session_id": 2, "task_id": "P2", "kind": "nav", "t": 0, "path": "/"})
        candidates = study.analyze(results, events)
        self.assertTrue({"difficulty", "duration", "dead_click", "backtrack", "abandoned", "low_ease"}
                        <= {c["kind"] for c in candidates})
        self.assertEqual(candidates, study.analyze(results, events))


class StudyStore(unittest.TestCase):
    def setUp(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)

    def test_consent_hash_storage_results_finish_and_retention(self):
        with self.assertRaises(ValueError):
            study_store.create_session("patient", "es", False, "mobile")
        token = study_store.create_session("patient", "es", True, "mobile")
        session = study_store.session(token)
        self.assertNotIn(token, json.dumps(session, default=str))
        self.assertEqual(session["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        study_store.add_events(token, [{"task_id": "P1", "kind": "nav", "t": 0, "path": "/?dni=36555444"}])
        study_store.save_result(token, {"task_id": "P1", "outcome": "stuck", "ease": 1,
                                       "comment": "DNI 36555444", "duration_ms": 3000})
        study_store.save_result(token, {"task_id": "P1", "outcome": "stuck", "ease": 1,
                                       "duration_ms": 3000})
        report = study_store.report()
        self.assertEqual(len([r for r in report["results"] if r["session_id"] == session["id"]]), 1)
        self.assertNotIn("36555444", json.dumps(report, default=str))
        study_store.run_analysis()
        finding = study_store.report()["findings"][0]
        study_store.update_finding(finding["id"], {"status": "fixed", "fix_note": "Repaired"}, "owner")
        study_store.run_analysis()
        self.assertEqual(study_store.report()["findings"][0]["status"], "fixed")
        study_store.finish(token, {"F1": "Nada", "F2": "Búsqueda", "F3": "maybe"})
        with self.assertRaises(ValueError):
            study_store.add_events(token, [{"task_id": "P1", "kind": "nav", "t": 1, "path": "/"}])
        from app.core import db
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE study_sessions SET started_at=now()-interval '91 days' WHERE id=%s", (session["id"],))
        self.assertEqual(study_store.purge(90), 1)
        self.assertIsNone(study_store.session(token))
        with db._connect() as conn, conn.cursor() as cur:
            for table in ("study_events", "study_task_results"):
                cur.execute(f"SELECT count(*) AS n FROM {table} WHERE session_id=%s", (session["id"],))
                self.assertEqual(cur.fetchone()["n"], 0)


class StudyStoreOffline(unittest.TestCase):
    def cursor(self, rows):
        from unittest.mock import MagicMock
        connection = MagicMock()
        cur = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = rows
        return connection, cur

    def test_store_sanitizes_again_at_write_boundary_and_hashes_token(self):
        connection, cur = self.cursor([{"id": 1, "role": "patient", "finished_at": None}, {"n": 0}])
        token = "synthetic-" + "session-handle"
        with patch.object(study_store.db, "_connect", return_value=connection):
            self.assertEqual(study_store.add_events(token, [{"task_id": "P1", "kind": "click", "t": 0,
                "path": "/account?dni=36555444", "target": {"tag": "input", "value": "36555444"}}]), 1)
        calls = cur.execute.call_args_list
        self.assertNotIn(token, str(calls))
        self.assertIn("FOR UPDATE", calls[0].args[0])
        params = calls[-1].args[1]
        # Core statements bind by name (the column's name for an insert).
        self.assertEqual(params["path"], "/account")
        self.assertNotIn("36555444", json.dumps(params["target"].obj))

    def test_event_cap_does_not_block_result_or_finish(self):
        connection, cur = self.cursor([{"id": 1, "role": "patient", "finished_at": None}, {"n": 3000}])
        with patch.object(study_store.db, "_connect", return_value=connection):
            self.assertEqual(study_store.add_events("token", [{"task_id": "P1", "kind": "nav", "t": 0, "path": "/"}]), 0)
        self.assertFalse(any("INSERT" in c.args[0] for c in cur.execute.call_args_list))

    def test_purge_locks_and_removes_children_before_parent(self):
        connection, cur = self.cursor([])
        cur.fetchall.return_value = [{"id": 1}, {"id": 2}]
        with patch.object(study_store.db, "_connect", return_value=connection):
            # Explicit days: the default reads the retention setting through the
            # same connection, which would put a catalog SELECT first.
            self.assertEqual(study_store.purge(days=30), 2)
        sql = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql[0])
        self.assertIn("study_events", sql[1])
        self.assertIn("study_task_results", sql[2])
        self.assertIn("study_sessions", sql[3])

    def test_status_only_update_preserves_fix_evidence_and_rejects_private_urls(self):
        connection, cur = self.cursor([{"id": 1}])
        with patch.object(study_store.db, "_connect", return_value=connection):
            study_store.update_finding(1, {"status": "in_progress"}, "owner")
        params = cur.execute.call_args.args[1]
        # fix_note, commit, live_url and detail all travel as NULL, so the COALESCE keeps the stored value
        self.assertEqual(sorted(k for k, v in params.items() if v is None),
                         ["param_1", "param_2", "param_3", "param_4"])
        self.assertEqual(params["status"], "in_progress")
        for url in ("javascript:alert(1)", "https://example.org/?dni=36555444", "https://user:pass@example.org/"):
            with self.assertRaises(ValueError):
                study_store.update_finding(1, {"status": "fixed", "live_url": url}, "owner")

    def test_reviewer_can_correct_a_manual_findings_evidence_only(self):
        connection, cur = self.cursor([{"id": 1}])
        with patch.object(study_store.db, "_connect", return_value=connection):
            study_store.update_finding(1, {"status": "fixed", "detail": "Escribió ana@example.org"}, "owner")
        sql, params = cur.execute.call_args.args
        self.assertIn("CASE WHEN (study_findings.source = ", sql)
        self.assertIn("Escribió [dato omitido]", params.values())
        connection, cur = self.cursor([{"id": 1}])
        with patch.object(study_store.db, "_connect", return_value=connection):
            study_store.update_finding(1, {"status": "fixed"}, "owner")
        self.assertIsNone(cur.execute.call_args.args[1]["param_1"])

    def test_manual_finding_may_cite_a_final_question_but_never_an_unknown_task(self):
        # A participant's «what was most confusing» answer (F1-F3) is evidence
        # a finding can cite (study 2026-10-15); a task of another role is not.
        connection, cur = self.cursor([{"id": 7}])
        with patch.object(study_store.db, "_connect", return_value=connection):
            self.assertEqual(study_store.add_finding(
                {"role": "admin", "task_id": "F1", "title": "Falta avisar que vienen tareas"}, "owner"), 7)
        for task in ("P1", "F9"):
            with self.assertRaises(ValueError):
                study_store.add_finding({"role": "admin", "task_id": task, "title": "x"}, "owner")


class UsabilityPeriod(unittest.TestCase):
    def test_study_days_are_installation_dates_not_utc(self):
        from datetime import datetime, timezone
        from app.admin_panel import admin_usability_screen
        late = datetime(2026, 10, 15, 0, 5, tzinfo=timezone.utc)  # 21:05 on the 14th in Buenos Aires
        later = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(admin_usability_screen.period([{"started_at": later}, {"started_at": late}]),
                         {"first": "2026-10-14", "last": "2026-10-16"})
        self.assertIsNone(admin_usability_screen.period([]))
