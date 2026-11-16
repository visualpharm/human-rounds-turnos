"""test_db_ai_store.py — the AI statements of `core/db.py` against a real Postgres.

Stage 3 of the SQLAlchemy Core move converts the AI group of `core/db.py`: the
editable prompts (`prompt_*`), the cost ledger and its report queries (`log_ai_cost`,
`costs_*`, `ai_costs_has_rows`), the MCP daily budget (`mcp_ai_reserve`) and the AI
register (`ai_register_*`). This file pins each of those functions row for row —
the wired (Spanish-key) dicts, the ordering, the branches, the transaction they
share with their caller — so the conversion is proven to change nothing. It was
written and run green on the hand-written SQL first, and passes unedited on Core.
"""
import json
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import ai_register, db  # noqa: E402
from app.core import db_ai  # noqa: E402
from app.core import db_values  # noqa: E402
from app.core import tz as _tz  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_ai_store")

UTC = timezone.utc


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
            cur.execute("TRUNCATE ai_register_changes, ai_prompts, ai_prompts_history, "
                        "ai_costs, mcp_ai_budget")


# ---- prompts (the panel's overlay on the prompts of the code) ---------------

PROMPT = "orden_analisis"  # belongs to a registered AI function: edits leave register events


class TestPrompts(DbCase):
    def test_get_and_all_on_an_empty_table(self):
        self.assertIsNone(db_ai.prompt_get(PROMPT))
        self.assertEqual(db_ai.prompts_all(), {})
        self.assertEqual(db_ai.prompt_history(PROMPT), [])

    def test_save_creates_the_row_and_returns_it_wired(self):
        saved = db_ai.prompt_save(PROMPT, "english", "castellano", True, usuario="ana")
        self.assertEqual(
            {k: saved[k] for k in ("clave", "texto_en", "texto_es", "editado", "actualizado_por")},
            {"clave": PROMPT, "texto_en": "english", "texto_es": "castellano",
             "editado": True, "actualizado_por": "ana"})
        self.assertIsInstance(saved["actualizado"], datetime)
        self.assertEqual(set(saved), {"clave", "texto_en", "texto_es", "editado",
                                      "actualizado", "actualizado_por"})
        self.assertEqual(db_ai.prompt_get(PROMPT), saved)

    def test_save_coerces_editado_to_bool_and_keeps_a_null_spanish_text(self):
        saved = db_ai.prompt_save("other_key", "english", None, 0)
        self.assertIs(saved["editado"], False)
        self.assertIsNone(saved["texto_es"])
        self.assertIsNone(saved["actualizado_por"])

    def test_all_is_keyed_by_the_prompt_key_with_wired_rows(self):
        db_ai.prompt_save("k1", "en1", "es1", False)
        db_ai.prompt_save("k2", "en2", "es2", True, usuario="bea")
        rows = db_ai.prompts_all()
        self.assertEqual(set(rows), {"k1", "k2"})
        self.assertEqual(rows["k2"]["texto_es"], "es2")
        self.assertEqual(rows["k2"]["actualizado_por"], "bea")
        self.assertIs(rows["k1"]["editado"], False)
        self.assertEqual(rows["k1"]["clave"], "k1")

    def test_overwrite_archives_the_previous_edited_version(self):
        first = db_ai.prompt_save(PROMPT, "en v1", "es v1", True, usuario="ana")
        db_ai.prompt_save(PROMPT, "en v2", "es v2", True, usuario="bea")
        current = db_ai.prompt_get(PROMPT)
        self.assertEqual((current["texto_en"], current["texto_es"], current["actualizado_por"]),
                         ("en v2", "es v2", "bea"))
        history = db_ai.prompt_history(PROMPT)
        self.assertEqual(len(history), 1)
        self.assertEqual(
            (history[0]["clave"], history[0]["texto_en"], history[0]["texto_es"],
             history[0]["actualizado"], history[0]["actualizado_por"]),
            (PROMPT, "en v1", "es v1", first["actualizado"], "ana"))
        self.assertIn("id", history[0])

    def test_overwriting_a_cached_translation_archives_nothing(self):
        db_ai.prompt_save(PROMPT, "en v1", "cached translation", False)
        db_ai.prompt_save(PROMPT, "en v1", "human edit", True, usuario="ana")
        self.assertEqual(db_ai.prompt_history(PROMPT), [])

    def test_historial_false_archives_nothing(self):
        db_ai.prompt_save(PROMPT, "en v1", "es v1", True, usuario="ana")
        db_ai.prompt_save(PROMPT, "en v2", "es v2", True, usuario="bea", historial=False)
        self.assertEqual(db_ai.prompt_history(PROMPT), [])
        self.assertEqual(db_ai.prompt_get(PROMPT)["texto_en"], "en v2")

    def test_history_is_newest_first_limited_and_scoped_to_the_key(self):
        for n in range(1, 5):
            db_ai.prompt_save(PROMPT, "en v%d" % n, "es v%d" % n, True, usuario="u%d" % n)
        db_ai.prompt_save("other", "x", "y", True)
        db_ai.prompt_save("other", "x2", "y2", True)
        history = db_ai.prompt_history(PROMPT)
        self.assertEqual([h["texto_en"] for h in history], ["en v3", "en v2", "en v1"])
        self.assertEqual([h["texto_en"] for h in db_ai.prompt_history(PROMPT, limit=2)],
                         ["en v3", "en v2"])
        self.assertEqual([h["texto_en"] for h in db_ai.prompt_history("other")], ["x"])
        self.assertGreater(history[0]["id"], history[1]["id"])

    def test_an_edited_save_leaves_one_register_event_per_function_with_fingerprints(self):
        db_ai.prompt_save(PROMPT, "english text", "texto en castellano", True, usuario="ana")
        rows = db_ai.ai_register_list_changes()
        owners = [f["id"] for f in ai_register.FUNCTIONS if PROMPT in f["prompt_ids"]]
        self.assertEqual(sorted(r["function_id"] for r in rows), sorted(owners))
        row = rows[0]
        self.assertEqual((row["kind"], row["actor"], row["substantial"], row["status"]),
                         ("prompt", "ana", True, "pending"))
        self.assertEqual(row["details"]["after"], ai_register.fingerprint("texto en castellano"))
        self.assertIsNone(row["details"]["before"])

    def test_the_second_edit_records_the_previous_text_as_before(self):
        db_ai.prompt_save(PROMPT, "en v1", "es v1", True, usuario="ana")
        db_ai.prompt_save(PROMPT, "en v2", "es v2", True, usuario="bea")
        newest = db_ai.ai_register_list_changes()[0]
        self.assertEqual(newest["details"]["before"], ai_register.fingerprint("es v1"))
        self.assertEqual(newest["details"]["after"], ai_register.fingerprint("es v2"))

    def test_an_unedited_save_leaves_no_register_event(self):
        db_ai.prompt_save(PROMPT, "en", "es", False)
        self.assertEqual(db_ai.ai_register_list_changes(), [])

    def test_delete_removes_the_override_and_records_the_restore(self):
        db_ai.prompt_save(PROMPT, "en v1", "es v1", True, usuario="ana")
        before = len(db_ai.ai_register_list_changes())
        db_ai.prompt_delete(PROMPT, usuario="bea", restored_text="the original")
        self.assertIsNone(db_ai.prompt_get(PROMPT))
        self.assertEqual(db_ai.prompts_all(), {})
        events = db_ai.ai_register_list_changes()
        self.assertEqual(len(events), 2 * before)
        newest = events[0]
        self.assertEqual(newest["details"]["before"], ai_register.fingerprint("en v1"))
        self.assertEqual(newest["details"]["after"], ai_register.fingerprint("the original"))
        self.assertEqual(newest["actor"], "bea")

    def test_delete_keeps_the_history_and_ignores_a_missing_row(self):
        db_ai.prompt_save(PROMPT, "en v1", "es v1", True)
        db_ai.prompt_save(PROMPT, "en v2", "es v2", True)
        db_ai.prompt_delete(PROMPT)
        self.assertEqual(len(db_ai.prompt_history(PROMPT)), 1)
        db_ai.prompt_delete("never_saved")  # no row: no error, no event
        self.assertIsNone(db_ai.prompt_get("never_saved"))

    def test_deleting_an_unedited_row_leaves_no_register_event(self):
        db_ai.prompt_save(PROMPT, "en", "cached", False)
        db_ai.prompt_delete(PROMPT, usuario="bea", restored_text="orig")
        self.assertIsNone(db_ai.prompt_get(PROMPT))
        self.assertEqual(db_ai.ai_register_list_changes(), [])

    def test_the_register_event_rolls_back_with_the_prompt_write(self):
        with patch.object(db_ai, "ai_register_append", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                db_ai.prompt_save(PROMPT, "en", "es", True, usuario="ana")
        self.assertIsNone(db_ai.prompt_get(PROMPT))


# ---- cost ledger -----------------------------------------------------------

def _cost_rows(cur, *rows):
    """rows: (days_ago, operation, cost, ip, recognized)."""
    for days_ago, op, cost, ip, recognized in rows:
        cur.execute(
            "INSERT INTO ai_costs (ts, operation, cost_usd, ip, recognized) "
            "VALUES (now() - %s * interval '1 day', %s, %s, %s, %s)",
            (days_ago, op, cost, ip, recognized))


class TestCostLedger(DbCase):
    def one(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_costs ORDER BY id")
            return cur.fetchall()

    def test_log_inserts_one_row_with_clamped_fields(self):
        db_ai.log_ai_cost("o" * 60, 0.0125, ip="1.2.3.4", usuario_dni="1" * 30,
                       modelo="m" * 100, reconocido=True)
        (row,) = self.one()
        self.assertEqual(row["operation"], "o" * 40)
        self.assertEqual(float(row["cost_usd"]), 0.0125)
        self.assertEqual(row["ip"], "1.2.3.4")
        self.assertEqual(row["user_dni"], "1" * 20)
        self.assertEqual(row["model"], "m" * 80)
        self.assertIs(row["recognized"], True)
        self.assertIsInstance(row["ts"], datetime)

    def test_log_maps_blank_values_to_null_and_bad_costs_to_zero(self):
        db_ai.log_ai_cost("ocr", "not a number", ip="", usuario_dni=None, modelo="",
                       reconocido=None)
        db_ai.log_ai_cost(None, None, reconocido=False)
        first, second = self.one()
        self.assertEqual((first["operation"], float(first["cost_usd"]), first["ip"],
                          first["user_dni"], first["model"], first["recognized"]),
                         ("ocr", 0.0, None, None, None, None))
        self.assertEqual((second["operation"], float(second["cost_usd"]), second["recognized"]),
                         ("", 0.0, False))

    def test_old_rows_lose_their_ip_but_keep_their_cost(self):
        with db._connect() as conn, conn.cursor() as cur:
            _cost_rows(cur, (200, "ocr", 0.5, "1.1.1.1", True), (10, "ocr", 0.25, "2.2.2.2", True),
                       (300, "ocr", 0.125, None, None))
        self.assertEqual(db_ai.blank_old_ai_cost_ips(days=180, limit=100), 1)
        self.assertEqual(db_ai.blank_old_ai_cost_ips(days=180, limit=100), 0)  # idempotent
        old, recent, never = self.one()
        self.assertEqual((old["ip"], float(old["cost_usd"])), (None, 0.5))
        self.assertEqual((recent["ip"], float(recent["cost_usd"])), ("2.2.2.2", 0.25))
        self.assertEqual(never["ip"], None)

    def test_blanking_respects_the_batch_limit(self):
        with db._connect() as conn, conn.cursor() as cur:
            _cost_rows(cur, *[(200, "ocr", 0.1, "1.1.1.1", True)] * 3)
        self.assertEqual(db_ai.blank_old_ai_cost_ips(days=180, limit=2), 2)
        self.assertEqual(db_ai.blank_old_ai_cost_ips(days=180, limit=2), 1)

    def test_log_never_raises_when_the_database_fails(self):
        with patch.object(db, "_connect", side_effect=RuntimeError("down")):
            self.assertIsNone(db_ai.log_ai_cost("ocr", 1.0))
        self.assertEqual(self.one(), [])

    def test_has_rows(self):
        self.assertIs(db_ai.ai_costs_has_rows(), False)
        db_ai.log_ai_cost("ocr", 0)
        self.assertIs(db_ai.ai_costs_has_rows(), True)

    def test_summary_totals_and_per_ip_median_and_mean(self):
        with db._connect() as conn, conn.cursor() as cur:
            _cost_rows(
                cur,
                (1, "ocr", 1.0, "1.1.1.1", True),
                (2, "ocr", 2.0, "1.1.1.1", False),
                (3, "chat", 6.0, "2.2.2.2", None),
                (4, "chat", 0.5, None, True),
                (90, "ocr", 100.0, "3.3.3.3", True),  # outside the 30 day window
            )
        s = db_ai.costs_summary()
        self.assertEqual(s["operaciones"], 4)
        self.assertEqual(s["total_usd"], 9.5)
        self.assertEqual((s["reconocidas"], s["no_reconocidas"], s["ips"]), (2, 1, 2))
        # per-IP sums are 3.0 and 6.0
        self.assertEqual(s["mediana_por_ip"], 4.5)
        self.assertEqual(s["promedio_por_ip"], 4.5)
        wide = db_ai.costs_summary(dias=365)
        self.assertEqual((wide["operaciones"], wide["total_usd"], wide["ips"]), (5, 109.5, 3))

    def test_summary_of_an_empty_ledger(self):
        self.assertEqual(db_ai.costs_summary(), {
            "operaciones": 0, "total_usd": 0.0, "reconocidas": 0, "no_reconocidas": 0,
            "ips": 0, "mediana_por_ip": None, "promedio_por_ip": 0.0})

    def test_by_operation_orders_by_cost_then_count(self):
        with db._connect() as conn, conn.cursor() as cur:
            _cost_rows(
                cur,
                (1, "ocr", 1.0, None, True),
                (1, "ocr", 1.0, None, False),
                (1, "chat", 2.0, None, None),
                (1, "tie_many", 1.0, None, None),
                (1, "tie_many", 0.5, None, None),
                (1, "tie_few", 1.5, None, None),
                (60, "old", 50.0, None, None),
            )
        rows = db_ai.costs_by_operation()
        self.assertEqual(
            [(r["operacion"], r["cantidad"], r["costo_usd"]) for r in rows],
            [("ocr", 2, 2.0), ("chat", 1, 2.0), ("tie_many", 2, 1.5), ("tie_few", 1, 1.5)])
        ocr = rows[0]
        self.assertEqual((ocr["reconocidas"], ocr["no_reconocidas"]), (1, 1))
        self.assertEqual(set(ocr), {"operacion", "cantidad", "costo_usd", "reconocidas",
                                    "no_reconocidas"})
        self.assertEqual(len(db_ai.costs_by_operation(dias=365)), 5)

    def test_daily_groups_by_installation_day(self):
        tz_art = db_values._ART
        today = datetime.now(tz_art).date()
        d1, d2 = today - timedelta(days=3), today - timedelta(days=1)
        with db._connect() as conn, conn.cursor() as cur:
            for day, cost in ((d1, 1.0), (d1, 0.25), (d2, 2.0)):
                cur.execute("INSERT INTO ai_costs (ts, operation, cost_usd) VALUES (%s, 'ocr', %s)",
                            (datetime(day.year, day.month, day.day, 12, 0, tzinfo=tz_art), cost))
        self.assertEqual(db_ai.costs_daily(), [
            {"fecha": d1.isoformat(), "costo_usd": 1.25, "operaciones": 2},
            {"fecha": d2.isoformat(), "costo_usd": 2.0, "operaciones": 1}])
        self.assertEqual(db_ai.costs_daily(dias=2), [
            {"fecha": d2.isoformat(), "costo_usd": 2.0, "operaciones": 1}])

    def test_daily_buckets_by_the_installation_timezone_not_utc(self):
        # 02:30 UTC is still the previous evening in Buenos Aires.
        tz_art = db_values._ART
        today = datetime.now(tz_art).date()
        day = today - timedelta(days=2)
        utc_instant = datetime(day.year, day.month, day.day, 2, 30, tzinfo=UTC)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ai_costs (ts, operation, cost_usd) VALUES (%s, 'ocr', 1)",
                        (utc_instant,))
        (row,) = db_ai.costs_daily()
        self.assertEqual(row["fecha"], utc_instant.astimezone(tz_art).date().isoformat())

    def test_top_ips_orders_and_limits_and_skips_null_ips(self):
        with db._connect() as conn, conn.cursor() as cur:
            _cost_rows(
                cur,
                (1, "ocr", 1.0, "a", False),
                (1, "ocr", 1.0, "a", False),
                (1, "ocr", 3.0, "b", True),
                (1, "ocr", 2.0, "c", None),
                (1, "ocr", 9.0, None, True),
                (60, "ocr", 9.0, "old", True),
            )
        rows = db_ai.costs_top_ips()
        self.assertEqual([(r["ip"], r["operaciones"], r["costo_usd"], r["no_reconocidas"])
                          for r in rows],
                         [("b", 1, 3.0, 0), ("a", 2, 2.0, 2), ("c", 1, 2.0, 0)])
        self.assertEqual([r["ip"] for r in db_ai.costs_top_ips(limit=1)], ["b"])
        self.assertEqual({r["ip"] for r in db_ai.costs_top_ips(dias=365)}, {"a", "b", "c", "old"})

    def test_burn_monthly_counts_only_whole_calendar_days(self):
        self.assertEqual(db_ai.costs_burn_monthly(), {
            "dias_completos": 0, "total_usd_dias_completos": 0.0,
            "run_rate_mensual_usd": None, "desde": None, "hasta": None})
        tz_art = db_values._ART
        today = datetime.now(tz_art).date()

        def at(days_ago):
            day = today - timedelta(days=days_ago)
            return datetime(day.year, day.month, day.day, 12, 0, tzinfo=tz_art)

        with db._connect() as conn, conn.cursor() as cur:
            for days_ago, cost in ((5, 100.0), (3, 1.5), (1, 0.5), (0, 40.0)):
                cur.execute("INSERT INTO ai_costs (ts, operation, cost_usd) VALUES (%s, 'ocr', %s)",
                            (at(days_ago), cost))
        burn = db_ai.costs_burn_monthly()
        # first observed day (5 ago) and today are dropped: days 4..1 ago = 4 days
        self.assertEqual(burn["dias_completos"], 4)
        self.assertEqual(burn["total_usd_dias_completos"], 2.0)
        self.assertEqual(burn["run_rate_mensual_usd"], 2.0 / 4 * 30)
        self.assertEqual(burn["desde"], (today - timedelta(days=4)).isoformat())
        self.assertEqual(burn["hasta"], (today - timedelta(days=1)).isoformat())

    def test_burn_monthly_with_no_whole_day_yet_is_empty(self):
        db_ai.log_ai_cost("ocr", 1.0)
        self.assertEqual(db_ai.costs_burn_monthly()["dias_completos"], 0)
        self.assertIsNone(db_ai.costs_burn_monthly()["run_rate_mensual_usd"])


# ---- MCP daily budget --------------------------------------------------------

class TestMcpBudget(DbCase):
    def test_reserve_counts_up_to_the_limit_then_refuses(self):
        day = date(2026, 11, 2)
        self.assertEqual([db_ai.mcp_ai_reserve(3, day) for _ in range(5)], [1, 2, 3, None, None])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT day, uses FROM mcp_ai_budget")
            self.assertEqual(cur.fetchall(), [{"day": day, "uses": 3}])

    def test_each_day_has_its_own_counter(self):
        self.assertEqual(db_ai.mcp_ai_reserve(1, date(2026, 11, 2)), 1)
        self.assertIsNone(db_ai.mcp_ai_reserve(1, date(2026, 11, 2)))
        self.assertEqual(db_ai.mcp_ai_reserve(1, date(2026, 11, 3)), 1)

    def test_a_negative_limit_refuses_after_the_first_row(self):
        day = date(2026, 11, 2)
        self.assertEqual(db_ai.mcp_ai_reserve(5, day), 1)
        self.assertIsNone(db_ai.mcp_ai_reserve(-4, day))  # clamped to 0: nothing left

    def _budget_rows(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT day, uses FROM mcp_ai_budget ORDER BY day")
            return cur.fetchall()

    def test_a_zero_limit_on_an_empty_day_reserves_nothing(self):
        day = date(2026, 11, 2)
        self.assertIsNone(db_ai.mcp_ai_reserve(0, day))
        self.assertEqual(self._budget_rows(), [])

    def test_a_zero_limit_keeps_refusing_on_repeated_calls(self):
        day = date(2026, 11, 2)
        self.assertEqual([db_ai.mcp_ai_reserve(0, day) for _ in range(3)], [None, None, None])
        self.assertEqual(self._budget_rows(), [])

    def test_a_zero_limit_does_not_touch_a_day_that_already_has_uses(self):
        day = date(2026, 11, 2)
        self.assertEqual(db_ai.mcp_ai_reserve(2, day), 1)
        self.assertIsNone(db_ai.mcp_ai_reserve(0, day))
        self.assertEqual(self._budget_rows(), [{"day": day, "uses": 1}])

    def test_a_negative_limit_on_an_empty_day_reserves_nothing(self):
        day = date(2026, 11, 2)
        self.assertIsNone(db_ai.mcp_ai_reserve(-1, day))
        self.assertEqual(self._budget_rows(), [])

    def test_a_limit_of_one_gives_exactly_one_reservation(self):
        day = date(2026, 11, 2)
        self.assertEqual([db_ai.mcp_ai_reserve(1, day) for _ in range(4)], [1, None, None, None])
        self.assertEqual(self._budget_rows(), [{"day": day, "uses": 1}])

    def test_raising_the_limit_from_zero_starts_the_count_at_one(self):
        day = date(2026, 11, 2)
        self.assertIsNone(db_ai.mcp_ai_reserve(0, day))
        self.assertEqual([db_ai.mcp_ai_reserve(2, day) for _ in range(3)], [1, 2, None])

    def test_the_default_day_is_today_in_the_installation_timezone(self):
        self.assertEqual(db_ai.mcp_ai_reserve(2), 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT day FROM mcp_ai_budget")
            self.assertEqual(cur.fetchone()["day"], datetime.now(db_values._ART).date())


# ---- AI register -------------------------------------------------------------

def _event(function_id="referral", substantial=False, kind="enabled", **details):
    return {"function_id": function_id, "kind": kind, "actor": "ana",
            "substantial": substantial, "details": details or {"field": "x"}}


class TestAiRegister(DbCase):
    def test_append_with_no_events_writes_nothing(self):
        db_ai.ai_register_append([])
        db_ai.ai_register_append(None)
        self.assertEqual(db_ai.ai_register_list_changes(), [])

    def test_append_stores_status_from_substantiality_and_roundtrips_the_details(self):
        details = {"field": "x", "nested": {"n": [1, 2, "é"]}, "none": None}
        db_ai.ai_register_append([_event("referral", True, "prompt", **details),
                               _event("triage", False, "enabled")])
        rows = db_ai.ai_register_list_changes()
        self.assertEqual([r["function_id"] for r in rows], ["triage", "referral"])  # newest first
        triage, referral = rows
        self.assertEqual((referral["kind"], referral["actor"], referral["substantial"],
                          referral["status"]), ("prompt", "ana", True, "pending"))
        self.assertEqual(referral["details"], details)
        self.assertEqual((triage["substantial"], triage["status"]), (False, "recorded"))
        self.assertIsNone(referral["notification_reference"])
        self.assertIsNone(referral["notified_on"])
        self.assertIsNone(referral["notified_by"])
        self.assertIsNone(referral["notified_at"])
        self.assertIsInstance(referral["changed_at"], datetime)
        self.assertGreater(referral["id"], 0)
        self.assertIsInstance(rows[0], dict)

    def test_append_on_the_callers_connection_shares_its_transaction(self):
        with db._connect() as conn:
            db_ai.ai_register_append([_event()], connection=conn)
            conn.rollback()
        self.assertEqual(db_ai.ai_register_list_changes(), [])
        with db._connect() as conn:
            db_ai.ai_register_append([_event()], connection=conn)
        self.assertEqual(len(db_ai.ai_register_list_changes()), 1)

    def test_notify_records_the_attestation_once(self):
        db_ai.ai_register_append([_event(substantial=True, kind="prompt")])
        (row,) = db_ai.ai_register_list_changes()
        self.assertTrue(db_ai.ai_register_notify(row["id"], "EX-2026-1234", "2026-10-14", "  bea "))
        (done,) = db_ai.ai_register_list_changes()
        self.assertEqual((done["status"], done["notification_reference"], done["notified_on"],
                          done["notified_by"]),
                         ("notified", "EX-2026-1234", date(2026, 10, 14), "bea"))
        self.assertIsInstance(done["notified_at"], datetime)
        self.assertFalse(db_ai.ai_register_notify(row["id"], "EX-2026-1234", "2026-10-14", "bea"))

    def test_notify_ignores_non_substantial_and_unknown_changes(self):
        db_ai.ai_register_append([_event(substantial=False)])
        (row,) = db_ai.ai_register_list_changes()
        self.assertFalse(db_ai.ai_register_notify(row["id"], "EX-1", "2026-10-14", "bea"))
        self.assertFalse(db_ai.ai_register_notify(row["id"] + 99, "EX-1", "2026-10-14", "bea"))
        self.assertEqual(db_ai.ai_register_list_changes()[0]["status"], "recorded")

    def test_notify_validates_before_touching_the_database(self):
        db_ai.ai_register_append([_event(substantial=True)])
        (row,) = db_ai.ai_register_list_changes()
        tomorrow = (datetime.now(_tz.tzinfo()).date() + timedelta(days=2)).isoformat()
        for args, code in (
            ((row["id"], "EX-1", "2026-10-14", " "), "invalid_actor"),
            ((row["id"], "EX-1", "2026-10-14", None), "invalid_actor"),
            ((row["id"], "x", "2026-10-14", "bea"), "invalid_reference"),
            ((row["id"], "has space", "2026-10-14", "bea"), "invalid_reference"),
            ((row["id"], "EX-1", "14/10/2026", "bea"), "invalid_date"),
            ((row["id"], "EX-1", None, "bea"), "invalid_date"),
            ((row["id"], "EX-1", tomorrow, "bea"), "future_date"),
        ):
            with self.assertRaises(ValueError) as caught:
                db_ai.ai_register_notify(*args)
            self.assertEqual(str(caught.exception), code)
        self.assertEqual(db_ai.ai_register_list_changes()[0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
