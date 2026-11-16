"""test_db_catalog_store.py — the catalog / translations / legal / REFEPS / searches /
login-panel / alert-claims statements of `core/db.py` against a real Postgres.

Stage 3 of the SQLAlchemy Core move converts these. The file pins each function's
result (wired Spanish-key dicts, ordering, upserts, the doc merge of the boolean
setters, the cascades into `booking_requests` and the AI register, the "analytics
never raise" contract, the purges' `LIMIT`) — written and green on the hand-written SQL
first, unedited on Core.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import clock as core_clock  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_ai  # noqa: E402
from app.core import db_analytics  # noqa: E402

import _clock  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_catalog_store")


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
            _dbtest.delete_booking_requests(cur)
            cur.execute("TRUNCATE catalog, translations, legal_docs, refeps_cache, searches, "
                        "login_panel_events, alert_claims, ai_register_changes")

    def rows(self, sql, params=()):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


# ---- catalog overlay -------------------------------------------------------------------

class TestCatalogReads(DbCase):
    def test_list_returns_every_row_of_a_type_ordered_by_id_wired(self):
        db_catalog.catalog_upsert("centros", "b", {"nombre": "B"})
        db_catalog.catalog_upsert("centros", "a", {"nombre": "A"})
        db_catalog.catalog_upsert("centros", "c", {"nombre": "C"}, borrado=True)
        db_catalog.catalog_upsert("servicios", "x", {"nombre": "X"})
        rows = db_catalog.catalog_list("centros")
        self.assertEqual([r["id"] for r in rows], ["a", "b", "c"])
        self.assertEqual([r["borrado"] for r in rows], [False, False, True])
        self.assertEqual(set(rows[0]), {"tipo", "id", "doc", "borrado", "created", "updated"})
        self.assertEqual(rows[0]["tipo"], "centros")
        self.assertEqual(rows[0]["doc"], {"nombre": "A"})
        self.assertEqual(db_catalog.catalog_list("nothing"), [])

    def test_get_returns_the_one_row_or_none(self):
        db_catalog.catalog_upsert("centros", "a", {"nombre": "A"})
        row = db_catalog.catalog_get("centros", "a")
        self.assertEqual((row["tipo"], row["id"], row["doc"]), ("centros", "a", {"nombre": "A"}))
        self.assertIsNone(db_catalog.catalog_get("centros", "zzz"))
        self.assertIsNone(db_catalog.catalog_get("servicios", "a"))


class TestCatalogUpsert(DbCase):
    def test_creates_then_replaces_the_doc_and_refreshes_updated(self):
        first = db_catalog.catalog_upsert("centros", "a", {"nombre": "A", "n": 1})
        self.assertEqual((first["tipo"], first["id"], first["doc"], first["borrado"]),
                         ("centros", "a", {"nombre": "A", "n": 1}, False))
        second = db_catalog.catalog_upsert("centros", "a", {"otro": "campo"}, borrado=True)
        self.assertEqual(second["doc"], {"otro": "campo"})
        self.assertIs(second["borrado"], True)
        self.assertEqual(second["created"], first["created"])
        self.assertGreater(second["updated"], first["updated"])
        self.assertEqual(len(db_catalog.catalog_list("centros")), 1)

    def test_a_none_doc_is_stored_as_a_json_null_not_a_sql_null(self):
        saved = db_catalog.catalog_upsert("centros", "a", None)
        self.assertIsNone(saved["doc"])
        self.assertEqual(self.rows("SELECT doc::text AS doc FROM catalog")[0]["doc"], "null")

    def test_unicode_and_nested_docs_roundtrip(self):
        doc = {"nombre": "Clínica Ñandú", "horarios": [{"dia": 1, "desde": "08:00"}], "x": None}
        db_catalog.catalog_upsert("centros", "a", doc)
        self.assertEqual(db_catalog.catalog_get("centros", "a")["doc"], doc)

    def test_on_the_callers_connection_commits_with_the_caller(self):
        with db._connect() as conn:
            db_catalog.catalog_upsert("centros", "a", {"n": 1}, connection=conn)
            conn.rollback()
        self.assertIsNone(db_catalog.catalog_get("centros", "a"))
        with db._connect() as conn:
            db_catalog.catalog_upsert("centros", "a", {"n": 1}, connection=conn)
        self.assertIsNotNone(db_catalog.catalog_get("centros", "a"))

    def test_renaming_a_doctor_renames_the_stored_name_of_their_appointments(self):
        with db._connect() as conn, conn.cursor() as cur:
            for rid, doctor_id, name in ((1, "7", "Old Name"), (2, "7", "Other Typo"),
                                         (3, "8", "Someone Else"), (4, None, "Old Name")):
                cur.execute("INSERT INTO booking_requests (id, assigned_doctor_id, assigned_doctor) "
                            "VALUES (%s,%s,%s)", (rid, doctor_id, name))
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "  New Name  "})
        names = {r["id"]: r["assigned_doctor"] for r in
                 self.rows("SELECT id, assigned_doctor FROM booking_requests ORDER BY id")}
        self.assertEqual(names, {1: "New Name", 2: "New Name", 3: "Someone Else", 4: "Old Name"})

    def test_a_doctor_without_a_name_or_another_type_renames_nothing(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO booking_requests (id, assigned_doctor_id, assigned_doctor) "
                        "VALUES (1, '7', 'Old Name')")
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "   "})
        db_catalog.catalog_upsert("servicios", 7, {"nombre": "Different"})
        self.assertEqual(self.rows("SELECT assigned_doctor FROM booking_requests")[0]
                         ["assigned_doctor"], "Old Name")

    def test_enabling_the_interview_of_a_doctor_leaves_register_events(self):
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "A"})
        self.assertEqual(db_ai.ai_register_list_changes(), [])  # unchanged flag: no events
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "A", "entrevista_habilitada": True})
        events = db_ai.ai_register_list_changes()
        self.assertEqual({e["function_id"] for e in events},
                         {"interview", "triage", "health_profile", "consult_documents"})
        self.assertTrue(all(e["kind"] == "enabled" and not e["substantial"] for e in events))
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "A", "entrevista_habilitada": True})
        self.assertEqual(len(db_ai.ai_register_list_changes()), 4)  # same flag again: nothing new
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "A", "entrevista_habilitada": False})
        self.assertEqual(len(db_ai.ai_register_list_changes()), 8)

    def test_the_register_event_rolls_back_with_the_doctor_write(self):
        with patch.object(db_ai, "ai_register_append", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                db_catalog.catalog_upsert("medicos", "7", {"nombre": "A", "entrevista_habilitada": True})
        self.assertIsNone(db_catalog.catalog_get("medicos", "7"))

    def test_changing_the_voice_model_leaves_an_event_and_a_snapshot(self):
        db_catalog.catalog_upsert("config", "chat_voice", {"model": "voice/one"})
        db_catalog.catalog_upsert("config", "chat_voice", {"model": "voice/two"})
        events = db_ai.ai_register_list_changes()
        models = [e for e in events if e["kind"] == "model"]
        self.assertEqual(len(models), 2)
        self.assertTrue(all(e["function_id"] == "voice_transcription" and e["substantial"]
                            for e in models))
        self.assertTrue([e for e in events if e["kind"] == "snapshot"])


class TestCatalogBooleanSetters(DbCase):
    def test_a_missing_overlay_gets_the_complete_doc_plus_the_key(self):
        rows = db_catalog.catalog_set_boolean_many(
            "servicios", [("a", {"nombre": "A", "x": 1}), ("b", {"nombre": "B"})], "flag", True)
        self.assertEqual([(r["tipo"], r["id"], r["doc"], r["borrado"]) for r in rows],
                         [("servicios", "a", {"nombre": "A", "x": 1, "flag": True}, False),
                          ("servicios", "b", {"nombre": "B", "flag": True}, False)])
        self.assertEqual(db_catalog.catalog_get("servicios", "a")["doc"],
                         {"nombre": "A", "x": 1, "flag": True})

    def test_an_existing_overlay_is_merged_not_replaced(self):
        db_catalog.catalog_upsert("servicios", "a", {"nombre": "Edited", "concurrent": "edit"})
        before = db_catalog.catalog_get("servicios", "a")
        (row,) = db_catalog.catalog_set_boolean_many(
            "servicios", [("a", {"nombre": "Stale base", "x": 1})], "flag", False)
        self.assertEqual(row["doc"], {"nombre": "Edited", "concurrent": "edit", "flag": False})
        self.assertGreater(row["updated"], before["updated"])
        self.assertEqual(row["created"], before["created"])

    def test_a_deleted_overlay_aborts_the_whole_batch(self):
        db_catalog.catalog_upsert("servicios", "gone", {"nombre": "G"}, borrado=True)
        with self.assertRaises(LookupError):
            db_catalog.catalog_set_boolean_many(
                "servicios", [("fresh", {"nombre": "F"}), ("gone", {"nombre": "G"})], "flag", True)
        self.assertIsNone(db_catalog.catalog_get("servicios", "fresh"))  # rolled back with the batch
        self.assertEqual(db_catalog.catalog_get("servicios", "gone")["doc"], {"nombre": "G"})

    def test_an_empty_batch_returns_an_empty_list(self):
        self.assertEqual(db_catalog.catalog_set_boolean_many("servicios", [], "flag", True), [])

    def test_the_interview_flag_of_doctors_leaves_register_events_against_the_previous_value(self):
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "A", "entrevista_habilitada": False})
        before = len(db_ai.ai_register_list_changes())
        db_catalog.catalog_set_boolean_many(
            "medicos", [("7", {"nombre": "A"}), ("8", {"nombre": "B", "entrevista_habilitada": True})],
            "entrevista_habilitada", True)
        events = db_ai.ai_register_list_changes()
        # doctor 7: False -> True (4 events); doctor 8: no overlay, base True -> True (none)
        self.assertEqual(len(events) - before, 4)
        db_catalog.catalog_set_boolean_many("servicios", [("a", {})], "entrevista_habilitada", True)
        db_catalog.catalog_set_boolean_many("medicos", [("9", {})], "other_flag", True)
        self.assertEqual(len(db_ai.ai_register_list_changes()), len(events))  # only doctors + that key


class TestCatalogSetDoctorEmail(DbCase):
    """One email per doctor (2026-11-03): the record's follows its account's."""

    def doc(self, **extra):
        return dict({"id": "7", "nombre": "Dr A", "_private": "never stored"}, **extra)

    def test_a_missing_overlay_is_created_from_the_doctor_without_private_keys(self):
        db_catalog.catalog_set_doctor_email(self.doc(email="old@x.com"), " a@x.com ")
        row = db_catalog.catalog_get("medicos", "7")
        self.assertEqual(row["doc"], {"id": "7", "nombre": "Dr A", "email": "a@x.com"})
        self.assertIs(row["borrado"], False)

    def test_an_overlay_keeps_its_other_edits_and_takes_the_new_email(self):
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "Edited", "email": "mine@x.com"})
        db_catalog.catalog_set_doctor_email(self.doc(), "a@x.com")
        self.assertEqual(db_catalog.catalog_get("medicos", "7")["doc"],
                         {"nombre": "Edited", "email": "a@x.com"})

    def test_an_empty_email_removes_the_key(self):
        db_catalog.catalog_upsert("medicos", "7", {"nombre": "Edited", "email": "mine@x.com"})
        db_catalog.catalog_set_doctor_email(self.doc(), "  ")
        self.assertEqual(db_catalog.catalog_get("medicos", "7")["doc"], {"nombre": "Edited"})
        db_catalog.catalog_set_doctor_email(self.doc(id="9", email="x@x.com"), None)
        self.assertEqual(db_catalog.catalog_get("medicos", "9")["doc"], {"id": "9", "nombre": "Dr A"})

    def test_a_deleted_overlay_is_left_alone(self):
        db_catalog.catalog_upsert("medicos", "8", {"nombre": "gone"}, borrado=True)
        db_catalog.catalog_set_doctor_email(self.doc(id="8"), "a@x.com")
        self.assertEqual(db_catalog.catalog_get("medicos", "8")["doc"], {"nombre": "gone"})


class TestCatalogDeletes(DbCase):
    def test_mark_deleted_updates_an_existing_row_only(self):
        created = db_catalog.catalog_upsert("centros", "a", {"n": 1})
        row = db_catalog.catalog_mark_deleted("centros", "a", True)
        self.assertIs(row["borrado"], True)
        self.assertEqual(row["doc"], {"n": 1})
        self.assertGreater(row["updated"], created["updated"])
        self.assertIs(db_catalog.catalog_mark_deleted("centros", "a", False)["borrado"], False)
        self.assertIsNone(db_catalog.catalog_mark_deleted("centros", "missing", True))
        self.assertIsNone(db_catalog.catalog_get("centros", "missing"))

    def test_delete_row_removes_one_row_and_tolerates_a_missing_one(self):
        db_catalog.catalog_upsert("centros", "a", {})
        db_catalog.catalog_upsert("centros", "b", {})
        self.assertIsNone(db_catalog.catalog_delete_row("centros", "a"))
        self.assertIsNone(db_catalog.catalog_delete_row("centros", "never"))
        self.assertEqual([r["id"] for r in db_catalog.catalog_list("centros")], ["b"])

    def test_both_honor_the_callers_connection(self):
        db_catalog.catalog_upsert("centros", "a", {})
        with db._connect() as conn:
            db_catalog.catalog_mark_deleted("centros", "a", True, connection=conn)
            db_catalog.catalog_delete_row("centros", "a", connection=conn)
            conn.rollback()
        self.assertIs(db_catalog.catalog_get("centros", "a")["borrado"], False)


# ---- translations -----------------------------------------------------------------------

class TestTranslations(DbCase):
    def test_get_and_all_on_an_empty_table(self):
        self.assertIsNone(db_catalog.translation_get("k", "en"))
        self.assertEqual(db_catalog.translations_all("en"), {})

    def test_upsert_creates_then_replaces_and_returns_the_row(self):
        row = db_catalog.translation_upsert("svc.1.name", "en", "hash1", "Cardiology", model="m1")
        self.assertEqual((row["source_key"], row["lang"], row["source_hash"], row["content"],
                          row["model"], row["override"], row["updated_by"]),
                         ("svc.1.name", "en", "hash1", "Cardiology", "m1", False, None))
        self.assertEqual(set(row), {"source_key", "lang", "source_hash", "content", "model",
                                    "override", "created", "updated", "updated_by"})
        again = db_catalog.translation_upsert("svc.1.name", "en", "", "Heart", override=1, updated_by="ana")
        self.assertEqual((again["source_hash"], again["content"], again["model"],
                          again["override"], again["updated_by"]),
                         ("", "Heart", None, True, "ana"))
        self.assertEqual(again["created"], row["created"])
        self.assertGreater(again["updated"], row["updated"])
        self.assertEqual(db_catalog.translation_get("svc.1.name", "en"), again)
        self.assertIsNone(db_catalog.translation_get("svc.1.name", "pt"))

    def test_all_is_scoped_to_one_language_and_keyed_by_source_key(self):
        db_catalog.translation_upsert("a", "en", "h", "A-en")
        db_catalog.translation_upsert("b", "en", "h", "B-en")
        db_catalog.translation_upsert("a", "pt", "h", "A-pt")
        rows = db_catalog.translations_all("en")
        self.assertEqual(set(rows), {"a", "b"})
        self.assertEqual(rows["a"]["content"], "A-en")
        self.assertEqual(rows["a"]["lang"], "en")

    def test_clear_override_deletes_that_row_only(self):
        db_catalog.translation_upsert("a", "en", "h", "A-en", override=True)
        db_catalog.translation_upsert("a", "pt", "h", "A-pt")
        self.assertIsNone(db_catalog.translation_clear_override("a", "en"))
        self.assertIsNone(db_catalog.translation_clear_override("never", "en"))
        self.assertIsNone(db_catalog.translation_get("a", "en"))
        self.assertIsNotNone(db_catalog.translation_get("a", "pt"))

    def test_drop_pii_placeholders_removes_poisoned_machine_rows_and_counts_them(self):
        db_catalog.translation_upsert("p1", "en", "h", "Call [teléfono 1] now")
        db_catalog.translation_upsert("p2", "en", "h", "Mail [EMAIL 12]")
        db_catalog.translation_upsert("p3", "en", "h", "[Document 3]")
        db_catalog.translation_upsert("kept_human", "en", "h", "Call [phone 1]", override=True)
        db_catalog.translation_upsert("kept_clean", "en", "h", "Call the phone")
        db_catalog.translation_upsert("kept_other_lang", "pt", "h", "[phone 1]")
        db_catalog.translation_upsert("kept_no_digits", "en", "h", "[phone]")
        self.assertEqual(db_catalog.translations_drop_pii_placeholders("en"), 3)
        self.assertEqual(set(db_catalog.translations_all("en")),
                         {"kept_human", "kept_clean", "kept_no_digits"})
        self.assertEqual(set(db_catalog.translations_all("pt")), {"kept_other_lang"})
        self.assertEqual(db_catalog.translations_drop_pii_placeholders("en"), 0)

    def test_drop_prefix_removes_unoverridden_rows_that_start_with_it(self):
        db_catalog.translation_upsert("f1", "en", "h", "[en] fake one")
        db_catalog.translation_upsert("f2", "en", "h", "[en] fake two")
        db_catalog.translation_upsert("kept_human", "en", "h", "[en] hand edit", override=True)
        db_catalog.translation_upsert("kept_real", "en", "h", "A real [en] translation")
        db_catalog.translation_upsert("kept_pt", "pt", "h", "[en] other language")
        self.assertEqual(db_catalog.translations_drop_prefix("en", "[en] "), 2)
        self.assertEqual(set(db_catalog.translations_all("en")), {"kept_human", "kept_real"})
        self.assertEqual(db_catalog.translations_drop_prefix("en", "[en] "), 0)


# ---- legal documents --------------------------------------------------------------------

class TestLegalDocs(DbCase):
    def test_get_is_none_until_saved(self):
        self.assertIsNone(db_catalog.legal_get("terms"))

    def test_save_upserts_and_returns_the_wired_row(self):
        first = db_catalog.legal_save("terms", "v1 body", usuario="ana")
        self.assertEqual((first["doc"], first["body"], first["updated_by"]),
                         ("terms", "v1 body", "ana"))
        self.assertEqual(set(first), {"doc", "body", "created", "updated", "updated_by"})
        second = db_catalog.legal_save("terms", "v2 body")
        self.assertEqual((second["body"], second["updated_by"]), ("v2 body", None))
        self.assertEqual(second["created"], first["created"])
        self.assertGreater(second["updated"], first["updated"])
        self.assertEqual(db_catalog.legal_get("terms"), second)
        self.assertIsNone(db_catalog.legal_get("privacy"))

    def test_delete_returns_to_the_original_and_tolerates_a_missing_row(self):
        db_catalog.legal_save("terms", "body")
        db_catalog.legal_save("privacy", "other")
        self.assertIsNone(db_catalog.legal_delete("terms"))
        self.assertIsNone(db_catalog.legal_delete("terms"))
        self.assertIsNone(db_catalog.legal_get("terms"))
        self.assertIsNotNone(db_catalog.legal_get("privacy"))


# ---- REFEPS cache -----------------------------------------------------------------------

class TestRefepsCache(DbCase):
    def test_get_is_none_until_set_then_returns_the_raw_row(self):
        self.assertIsNone(db_catalog.refeps_get("MP123"))
        db_catalog.refeps_set("MP123", [{"profesion": "Médico", "n": 1}])
        row = db_catalog.refeps_get("MP123")
        self.assertEqual(set(row), {"items", "ok", "checked"})
        self.assertEqual(row["items"], [{"profesion": "Médico", "n": 1}])
        self.assertIs(row["ok"], True)
        self.assertIsInstance(row["checked"], datetime)

    def test_set_upserts_and_returns_the_wired_row(self):
        first = db_catalog.refeps_set("MP123", [1])
        self.assertEqual(set(first), {"matricula", "items", "ok", "checked"})
        self.assertEqual((first["matricula"], first["items"], first["ok"]), ("MP123", [1], True))
        second = db_catalog.refeps_set("MP123", [], ok=False)
        self.assertEqual((second["items"], second["ok"]), ([], False))
        self.assertGreater(second["checked"], first["checked"])
        self.assertEqual(len(self.rows("SELECT * FROM refeps_cache")), 1)
        self.assertIsNone(db_catalog.refeps_get("MP999"))


# ---- searches -----------------------------------------------------------------------------

class TestSearches(DbCase):
    def test_log_normalizes_the_query_and_stores_the_count_and_source(self):
        db_analytics.log_search("  Pediatría   HOY ", "3", origen="home")
        db_analytics.log_search("otra", None)
        db_analytics.log_search("   ", 5)  # blank: nothing stored
        db_analytics.log_search(None, 5)
        rows = self.rows("SELECT q, results, source, ts FROM searches ORDER BY id")
        self.assertEqual([(r["q"], r["results"], r["source"]) for r in rows],
                         [("pediatría hoy", 3, "home"), ("otra", 0, None)])
        self.assertIsInstance(rows[0]["ts"], datetime)

    def test_log_never_raises_when_the_database_fails(self):
        with patch.object(db, "_connect", side_effect=RuntimeError("down")):
            self.assertIsNone(db_analytics.log_search("x", 1))
        self.assertEqual(self.rows("SELECT * FROM searches"), [])

    def test_top_groups_by_query_and_orders_by_count_then_query(self):
        for q, n in (("b", 1), ("a", 0), ("a", 2), ("c", 0), ("c", 0), ("b", 4), ("z", 1),
                     ("z", 1), ("z", 1)):
            db_analytics.log_search(q, n)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO searches (q, results, ts) VALUES "
                        "('old', 1, now() - interval '90 days')")
        self.assertEqual(db_analytics.top_searches(), [("z", 3, 3), ("a", 2, 1), ("b", 2, 2), ("c", 2, 0)])
        self.assertEqual(db_analytics.top_searches(dias=365)[-1], ("old", 1, 1))

    def test_top_never_raises_when_the_database_fails(self):
        with patch.object(db, "_connect", side_effect=RuntimeError("down")):
            self.assertEqual(db_analytics.top_searches(), [])

    def test_purge_deletes_old_rows_up_to_the_limit_and_counts_them(self):
        with db._connect() as conn, conn.cursor() as cur:
            for n in range(5):
                cur.execute("INSERT INTO searches (q, results, ts) VALUES (%s, 1, "
                            "now() - interval '200 days')", ("old%d" % n,))
            cur.execute("INSERT INTO searches (q, results, ts) VALUES ('recent', 1, now())")
        self.assertEqual(db_analytics.purge_old_searches(days=180, limit=3), 3)
        self.assertEqual(db_analytics.purge_old_searches(days=180, limit=3), 2)
        self.assertEqual(db_analytics.purge_old_searches(days=180, limit=3), 0)
        self.assertEqual([r["q"] for r in self.rows("SELECT q FROM searches")], ["recent"])
        self.assertEqual(db_analytics.purge_old_searches(days="0", limit="10"), 1)


# ---- login panel experiment -------------------------------------------------------------------

class TestLoginPanelEvents(DbCase):
    def log(self, sid, variant, event, tab=None, item=None, ms=None):
        db_analytics.log_panel_login(sid, variant, event, tab=tab, item=item, ms=ms)

    def test_log_stores_the_event_and_never_raises(self):
        self.log("s1", "a", "tab", tab="tips", item="t1", ms=12)
        (row,) = self.rows("SELECT * FROM login_panel_events")
        self.assertEqual((row["sid"], row["variant"], row["event"], row["tab"], row["item"],
                          row["ms"]), ("s1", "a", "tab", "tips", "t1", 12))
        self.assertIsInstance(row["ts"], datetime)
        with patch.object(db, "_connect", side_effect=RuntimeError("down")):
            self.assertIsNone(db_analytics.log_panel_login("s", "a", "tab"))
        self.assertEqual(len(self.rows("SELECT * FROM login_panel_events")), 1)

    def test_stats_of_an_empty_table(self):
        self.assertEqual(db_analytics.stats_panel_login(),
                         {"sesiones": 0, "logins": 0, "errores": 0,
                          "sesiones_con_error": 0, "ms_mediana_login": None})

    def test_stats_count_sessions_and_the_share_that_hit_an_error(self):
        for args in (
            ("s1", "a", "exposicion", "memes", "m1"),
            ("s1", "a", "tab", "tips", "t1"),
            ("s1", "a", "login_ok", None, None, 100),
            ("s2", "a", "exposicion", "memes", "m1"),
            ("s2", "a", "login_ok", None, None, 300),
            ("s2", "a", "login_error"),
            ("s2", "a", "login_ok", None, None, None),
            ("s3", "b", "exposicion", "memes", None),
            ("s4", "b", "exposicion", None, None),
        ):
            self.log(*args)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO login_panel_events (sid, variant, event, ts) "
                        "VALUES ('old', 'a', 'login_error', now() - interval '90 days')")
        stats = db_analytics.stats_panel_login()
        # s1 y s2 tocaron el login (la exposicion del gate no es un intento);
        # s2 fallo una vez y aun asi entro. La mediana es de los ms presentes.
        self.assertEqual(stats, {"sesiones": 2, "logins": 2, "errores": 1,
                                 "sesiones_con_error": 1, "ms_mediana_login": 200.0})
        wide = db_analytics.stats_panel_login(dias=365)
        self.assertEqual(wide["sesiones"], 3)
        self.assertEqual(wide["sesiones_con_error"], 2)

    def test_stats_let_a_database_failure_propagate(self):
        with patch.object(db, "_connect", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                db_analytics.stats_panel_login()

    def test_purge_deletes_old_rows_up_to_the_limit(self):
        with db._connect() as conn, conn.cursor() as cur:
            for n in range(4):
                cur.execute("INSERT INTO login_panel_events (sid, variant, event, ts) VALUES "
                            "(%s, 'a', 'tab', now() - interval '200 days')", ("old%d" % n,))
        self.log("new", "a", "tab")
        self.assertEqual(db_analytics.purge_old_login_panel_events(days=180, limit=3), 3)
        self.assertEqual(db_analytics.purge_old_login_panel_events(days=180, limit=3), 1)
        self.assertEqual(db_analytics.purge_old_login_panel_events(days=180, limit=3), 0)
        self.assertEqual([r["sid"] for r in self.rows("SELECT sid FROM login_panel_events")], ["new"])


# ---- daily alert claims ---------------------------------------------------------------------------

class TestAlertClaims(DbCase):
    def test_a_claim_is_won_once_per_key_and_day(self):
        self.assertIs(db_analytics.claim_daily_alert("disk", "2026-11-02"), True)
        self.assertIs(db_analytics.claim_daily_alert("disk", "2026-11-02"), False)
        self.assertIs(db_analytics.claim_daily_alert("disk", "2026-11-03"), True)
        self.assertIs(db_analytics.claim_daily_alert("mail", "2026-11-02"), True)
        rows = self.rows("SELECT alert_key, day, claimed_at FROM alert_claims ORDER BY alert_key, day")
        self.assertEqual([(r["alert_key"], r["day"]) for r in rows],
                         [("disk", date(2026, 11, 2)), ("disk", date(2026, 11, 3)),
                          ("mail", date(2026, 11, 2))])
        self.assertIsInstance(rows[0]["claimed_at"], datetime)

    # Instants around both midnights (ART = UTC-3): 21:00-24:00 ART is already the next UTC day.
    PURGE_MOMENTS = ("2026-10-27T00:30:00Z", "2026-10-27T01:30:00Z", "2026-10-27T02:59:59Z",
                     "2026-10-27T03:00:00Z", "2026-10-27T12:00:00Z", "2026-10-27T23:59:59Z")

    def test_purge_deletes_claims_older_than_the_days_up_to_the_limit(self):
        clock = _clock.freeze(self, self.PURGE_MOMENTS[0])
        for moment in self.PURGE_MOMENTS:
            with self.subTest(moment=moment):
                clock.set(moment)
                today = core_clock.today()  # the installation's day, not the process's or Postgres'
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("TRUNCATE alert_claims")
                    for n in range(4):
                        cur.execute("INSERT INTO alert_claims (alert_key, day) VALUES (%s, %s)",
                                    ("old%d" % n, today - timedelta(days=200)))
                    cur.execute("INSERT INTO alert_claims (alert_key, day) VALUES ('edge', %s)",
                                (today - timedelta(days=180),))
                    cur.execute("INSERT INTO alert_claims (alert_key, day) VALUES ('new', %s)", (today,))
                self.assertEqual(db_analytics.purge_old_alert_claims(days=180, limit=3), 3)
                self.assertEqual(db_analytics.purge_old_alert_claims(days=180, limit=3), 1)
                self.assertEqual(db_analytics.purge_old_alert_claims(days=180, limit=3), 0)
                self.assertEqual({r["alert_key"] for r in self.rows("SELECT alert_key FROM alert_claims")},
                                 {"edge", "new"})
                self.assertEqual(db_analytics.purge_old_alert_claims(days="0", limit="10"), 1)  # 'edge' (day < today)


if __name__ == "__main__":
    unittest.main()
