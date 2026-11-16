"""test_sqlcore.py — `core/sqlcore.py`: SQLAlchemy Core statements run on the
psycopg cursor `db._connect()` already hands out.

DB-less: how a statement is compiled (placeholders, no casts, `IN` expansion,
JSON parameters). Real Postgres: that a Core statement shares the caller's
transaction with hand-written SQL (commit together, roll back together), keeps
the pool's dict rows, and moves JSONB, arrays, dates and `ON CONFLICT` upserts
the way psycopg moves them for a hand-written query.
"""
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from sqlalchemy import delete, func, insert, literal_column, select, text, update  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.core import db, sqlcore  # noqa: E402
from app.core.schema_model import (  # noqa: E402
    admin_preferences,
    operators,
    searches,
    waitlist,
)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_sqlcore")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestCompile(unittest.TestCase):
    def test_placeholders_are_named_and_values_are_not_inlined(self):
        sql, params = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.dni == "30'; --"))
        self.assertEqual("SELECT waitlist.id \nFROM waitlist \nWHERE waitlist.dni = %(dni_1)s", sql)
        self.assertEqual({"dni_1": "30'; --"}, params)

    def test_no_bind_casts_are_added(self):
        sql, _ = sqlcore.compile_statement(
            select(waitlist.c.id).where(waitlist.c.id == 3, waitlist.c.appointment_at > datetime.now(timezone.utc)))
        self.assertNotIn("::", sql)

    def test_in_lists_are_expanded_and_an_empty_one_matches_nothing(self):
        sql, params = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.id.in_([4, 5])))
        self.assertEqual(2, sql.count("%(id_1_"))
        self.assertEqual([4, 5], sorted(params.values()))
        sql, params = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.id.in_([])))
        self.assertIn("1 != 1", sql)
        self.assertEqual({}, params)

    def test_a_literal_percent_in_the_statement_is_escaped_for_psycopg(self):
        sql, params = sqlcore.compile_statement(
            select(literal_column("'50%'")).where(waitlist.c.id == 1))
        self.assertIn("'50%%'", sql)
        self.assertEqual({"id_1": 1}, params)

    def test_json_parameters_are_serialized_by_the_type(self):
        from psycopg.types.json import Jsonb
        _, params = sqlcore.compile_statement(
            insert(admin_preferences).values(username="u", key="k", value={"a": [1, "é"]}))
        self.assertIsInstance(params["value"], Jsonb)
        self.assertEqual({"a": [1, "é"]}, params["value"].obj)

    def test_json_values_inside_an_in_list_are_serialized_too(self):
        from psycopg.types.json import Jsonb
        sql, params = sqlcore.compile_statement(
            select(admin_preferences.c.key).where(admin_preferences.c.value.in_([{"a": 1}, {"b": 2}])))
        self.assertEqual(2, sql.count("%(value_1_"))
        self.assertEqual([{"a": 1}, {"b": 2}], [p.obj for p in params.values()])
        self.assertTrue(all(isinstance(p, Jsonb) for p in params.values()))

    def test_the_statement_is_not_executed_or_connected_by_compiling(self):
        # compiling needs no database at all
        self.assertTrue(sqlcore.compile_statement(delete(waitlist))[0].startswith("DELETE FROM waitlist"))


class TestPrepared(unittest.TestCase):
    """`sqlcore.prepare`: a statement built once, run with named values (the per-request paths)."""

    def test_renders_what_compile_statement_renders_for_the_same_values(self):
        from sqlalchemy import Text, bindparam
        prepared = sqlcore.prepare(select(waitlist.c.id).where(waitlist.c.dni == bindparam("dni", type_=Text)))
        sql, params = prepared.render(dni="30'; --")
        self.assertEqual("SELECT waitlist.id \nFROM waitlist \nWHERE waitlist.dni = %(dni)s", sql)
        self.assertEqual({"dni": "30'; --"}, params)
        self.assertNotIn("::", sql)

    def test_a_none_value_stays_equals_null_and_never_becomes_is_null(self):
        from sqlalchemy import Text, bindparam
        prepared = sqlcore.prepare(select(waitlist.c.id).where(waitlist.c.dni == bindparam("dni", type_=Text)))
        sql, params = prepared.render(dni=None)
        self.assertIn("waitlist.dni = %(dni)s", sql)
        self.assertNotIn("IS NULL", sql)
        self.assertEqual({"dni": None}, params)
        # whereas a statement built with a Python None per call is `IS NULL`
        self.assertIn("IS NULL", sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.dni == None))[0])  # noqa: E711

    def test_a_missing_value_is_an_error_not_a_silent_null(self):
        from sqlalchemy import Text, bindparam
        prepared = sqlcore.prepare(select(waitlist.c.id).where(waitlist.c.dni == bindparam("dni", type_=Text)))
        with self.assertRaises(Exception):
            prepared.render()

    def test_expanding_lists_and_json_values_go_through_the_bind_processors(self):
        from psycopg.types.json import Jsonb
        from sqlalchemy import Text, bindparam
        in_list = sqlcore.prepare(select(waitlist.c.id).where(
            waitlist.c.dni.in_(bindparam("dnis", expanding=True, type_=Text))))
        sql, params = in_list.render(dnis=["a", "b", "c"])
        self.assertEqual(3, sql.count("%(dnis_"))
        self.assertEqual(["a", "b", "c"], sorted(params.values()))
        sql, params = in_list.render(dnis=["z"])
        self.assertEqual(1, sql.count("%(dnis_"))
        doc = sqlcore.prepare(insert(admin_preferences).values(
            username=bindparam("u"), key="k", value=bindparam("v")))
        _, params = doc.render(u="x", v={"a": 1})
        self.assertIsInstance(params["v"], Jsonb)

    def test_one_prepared_statement_serves_every_call_with_its_own_values(self):
        from sqlalchemy import Text, bindparam
        prepared = sqlcore.prepare(select(waitlist.c.id).where(waitlist.c.dni == bindparam("dni", type_=Text)))
        self.assertEqual({"dni": "1"}, prepared.render(dni="1")[1])
        self.assertEqual({"dni": "2"}, prepared.render(dni="2")[1])
        self.assertEqual({"dni": "1"}, prepared.render(dni="1")[1])


class TestTextAndIdentifiers(unittest.TestCase):
    def test_a_text_statement_compiles_and_a_percent_in_it_is_escaped(self):
        from sqlalchemy import text
        sql, params = sqlcore.compile_statement(text("SELECT 1 WHERE 'a' NOT LIKE 'pg_toast%'"))
        self.assertEqual("SELECT 1 WHERE 'a' NOT LIKE 'pg_toast%%'", sql)
        self.assertEqual({}, params)

    def test_identifiers_are_quoted_only_when_they_must_be(self):
        self.assertEqual("hr_backup_test_0f3a", sqlcore.quote_identifier("hr_backup_test_0f3a"))
        self.assertEqual('"Mixed"', sqlcore.quote_identifier("Mixed"))
        self.assertEqual('"a""b"', sqlcore.quote_identifier('a"b'))
        self.assertEqual('"select"', sqlcore.quote_identifier("select"))

    def test_the_version_table_is_queryable_but_not_part_of_the_model(self):
        from app.core.schema_model import alembic_version, metadata
        self.assertNotIn("alembic_version", metadata.tables)
        sql, _ = sqlcore.compile_statement(select(alembic_version.c.version_num))
        self.assertEqual("SELECT alembic_version.version_num \nFROM alembic_version", sql)


class TestCompiledCache(unittest.TestCase):
    """The compiled SQL is cached by statement structure; the VALUES of each call are
    its own. A cache that leaked the first call's values into the next would be silent
    data corruption, so each kind of parameter is run twice with different values."""

    def twice(self, build, first, second):
        sqlcore._COMPILED_CACHE.clear()
        one = sqlcore.compile_statement(build(first))
        size = len(sqlcore._COMPILED_CACHE)
        two = sqlcore.compile_statement(build(second))
        self.assertEqual(size, len(sqlcore._COMPILED_CACHE), "the second call reused the compiled statement")
        self.assertEqual(1, size)
        return one, two

    def test_plain_values_are_read_from_each_call(self):
        (sql1, p1), (sql2, p2) = self.twice(
            lambda v: select(waitlist.c.id).where(waitlist.c.dni == v[0], waitlist.c.id > v[1]), ("aaa", 3), ("bbb", 4))
        self.assertEqual(sql1, sql2)
        self.assertEqual({"dni_1": "aaa", "id_1": 3}, p1)
        self.assertEqual({"dni_1": "bbb", "id_1": 4}, p2)

    def test_limit_and_offset_values_are_read_from_each_call(self):
        (_, p1), (_, p2) = self.twice(
            lambda n: select(waitlist.c.id).limit(n).offset(n * 10), 5, 7)
        self.assertEqual([5, 50], sorted(p1.values()))
        self.assertEqual([7, 70], sorted(p2.values()))

    def test_in_lists_of_different_lengths_expand_to_their_own_placeholders(self):
        sqlcore._COMPILED_CACHE.clear()
        sql1, p1 = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.id.in_([1, 2])))
        sql2, p2 = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.id.in_([7, 8, 9])))
        sql3, p3 = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.id.in_([])))
        self.assertEqual((2, 3), (sql1.count("%(id_1_"), sql2.count("%(id_1_")))
        self.assertEqual(([1, 2], [7, 8, 9], {}), (sorted(p1.values()), sorted(p2.values()), p3))
        self.assertIn("1 != 1", sql3)
        self.assertEqual(1, len(sqlcore._COMPILED_CACHE))

    def test_json_values_go_through_the_bind_processor_on_a_cache_hit_too(self):
        from psycopg.types.json import Jsonb
        (_, p1), (_, p2) = self.twice(
            lambda v: insert(admin_preferences).values(username="u", key="k", value=v), {"a": 1}, {"b": [2]})
        self.assertEqual(({"a": 1}, {"b": [2]}), (p1["value"].obj, p2["value"].obj))
        self.assertTrue(isinstance(p1["value"], Jsonb) and isinstance(p2["value"], Jsonb))

    def test_a_different_structure_or_inline_text_is_a_different_entry(self):
        sqlcore._COMPILED_CACHE.clear()
        a = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.dni == "x"))
        b = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.email == "x"))
        c = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.dni == literal_column("'x'")))
        d = sqlcore.compile_statement(select(waitlist.c.id).where(waitlist.c.dni == literal_column("'y'")))
        self.assertEqual(4, len(sqlcore._COMPILED_CACHE))
        self.assertEqual(4, len({a[0], b[0], c[0], d[0]}))
        self.assertIn("'y'", d[0])
        self.assertEqual({}, d[1])

    def test_concurrent_callers_each_get_their_own_values(self):
        import threading
        sqlcore._COMPILED_CACHE.clear()
        wrong = []

        def worker(base):
            for i in range(150):
                sql, params = sqlcore.compile_statement(
                    select(waitlist.c.id).where(waitlist.c.dni == f"d{base}-{i}", waitlist.c.id.in_([base, i])))
                if sorted(v for v in params.values() if isinstance(v, str)) != [f"d{base}-{i}"] \
                        or sorted(v for v in params.values() if isinstance(v, int)) != sorted([base, i]):
                    wrong.append((base, i, params))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([], wrong)


class TestRunOnTheCallersCursor(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM admin_preferences")
            cur.execute("DELETE FROM searches")
            # waitlist.request_id has a foreign key to booking_requests: clear the
            # children first, then give requests 1..3 their bare parent rows
            _dbtest.delete_booking_requests(cur)
            for request_id in (1, 2, 3):
                cur.execute("INSERT INTO booking_requests (id) VALUES (%s)", (request_id,))

    def test_rows_are_the_pools_dict_rows(self):
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, insert(waitlist).values(
                request_id=1, dni="d", email="e", service_id="s", center_slug="c"))
            row = sqlcore.run(cur, select(waitlist.c.id, waitlist.c.dni, waitlist.c.active)
                              .where(waitlist.c.request_id == 1)).fetchone()
        self.assertEqual({"id", "dni", "active"}, set(row))
        self.assertEqual(("d", True), (row["dni"], row["active"]))

    def test_commits_with_the_with_block_together_with_hand_written_sql(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO searches (q, results) VALUES ('hand', 1)")
            sqlcore.run(cur, insert(searches).values(q="core", results=2))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT q FROM searches ORDER BY q")
            self.assertEqual(["core", "hand"], [r["q"] for r in cur.fetchall()])

    def test_rolls_back_with_the_with_block_together_with_hand_written_sql(self):
        with self.assertRaises(RuntimeError):
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO searches (q, results) VALUES ('hand', 1)")
                sqlcore.run(cur, insert(searches).values(q="core", results=2))
                raise RuntimeError("boom")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM searches")
            self.assertEqual(0, cur.fetchone()["n"])

    def test_run_async_executes_on_an_async_connection_and_returns_its_cursor(self):
        import asyncio

        import psycopg
        from psycopg.rows import dict_row

        async def go():
            conn = await psycopg.AsyncConnection.connect(
                db._db_url(), autocommit=True, row_factory=dict_row, connect_timeout=5)
            try:
                cursor = await sqlcore.run_async(conn, select(literal_column("1").label("one")))
                one = await cursor.fetchone()
                await sqlcore.run_async(conn, text("LISTEN " + sqlcore.quote_identifier("turnos_probe")))
                async with conn.cursor() as cur:
                    await cur.execute("SELECT count(*) AS n FROM pg_listening_channels() AS c "
                                      "WHERE c = 'turnos_probe'")
                    return one, await cur.fetchone()
            finally:
                await conn.close()

        one, listening = asyncio.run(go())
        self.assertEqual({"one": 1}, one)
        self.assertEqual({"n": 1}, listening)

    def test_sees_the_uncommitted_rows_of_its_own_transaction(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO searches (q, results) VALUES ('mine', 7)")
            row = sqlcore.run(cur, select(searches.c.results).where(searches.c.q == "mine")).fetchone()
            self.assertEqual(7, row["results"])

    def test_jsonb_round_trips_and_upserts(self):
        stmt = pg_insert(admin_preferences).values(
            username="ana", key="cols", value={"a": [1, 2], "ñ": "á%"})
        stmt = stmt.on_conflict_do_update(
            index_elements=[admin_preferences.c.username, admin_preferences.c.key],
            set_={"value": stmt.excluded.value})
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, stmt)
            sqlcore.run(cur, pg_insert(admin_preferences).values(
                username="ana", key="cols", value={"b": None}).on_conflict_do_update(
                index_elements=[admin_preferences.c.username, admin_preferences.c.key],
                set_={"value": {"b": None}}))
            cur.execute("SELECT value FROM admin_preferences WHERE username='ana' AND key='cols'")
            self.assertEqual({"b": None}, cur.fetchone()["value"])
            sqlcore.run(cur, update(admin_preferences).values(value={"a": [1, 2], "ñ": "á%"}))
            row = sqlcore.run(cur, select(admin_preferences.c.value)).fetchone()
        self.assertEqual({"a": [1, 2], "ñ": "á%"}, row["value"])

    def test_instants_and_booleans(self):
        at = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, insert(waitlist).values(
                request_id=2, dni="d", email="e", service_id="s", center_slug="c", appointment_at=at))
            row = sqlcore.run(cur, select(waitlist.c.appointment_at).where(
                waitlist.c.appointment_at >= at, waitlist.c.active)).fetchone()
            self.assertEqual(at, row["appointment_at"])
            self.assertIsNone(sqlcore.run(cur, select(waitlist.c.id).where(
                waitlist.c.appointment_at > at)).fetchone())

    def test_in_lists_returning_and_rowcount(self):
        with db._connect() as conn, conn.cursor() as cur:
            ids = [sqlcore.run(cur, insert(waitlist).values(
                request_id=i, dni="d", email="e", service_id="s", center_slug="c")
                .returning(waitlist.c.id)).fetchone()["id"] for i in (1, 2, 3)]
            found = sqlcore.run(cur, select(waitlist.c.id).where(waitlist.c.id.in_(ids[:2]))
                                .order_by(waitlist.c.id)).fetchall()
            self.assertEqual(ids[:2], [r["id"] for r in found])
            self.assertEqual(2, sqlcore.run(cur, update(waitlist).where(
                waitlist.c.id.in_(ids[:2])).values(active=False)).rowcount)
            self.assertEqual([], sqlcore.run(cur, select(waitlist.c.id).where(
                waitlist.c.id.in_([]))).fetchall())

    def test_array_column_round_trips(self):
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, insert(operators).values(
                username="o1", pass_hash="x", role="admin", centers=["a", "b"]))
            row = sqlcore.run(cur, select(operators.c.centers).where(operators.c.username == "o1")).fetchone()
            sqlcore.run(cur, delete(operators).where(operators.c.username == "o1"))
        self.assertEqual(["a", "b"], row["centers"])

    def test_json_in_list_runs(self):
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, insert(admin_preferences).values(username="a", key="k1", value={"x": 1}))
            sqlcore.run(cur, insert(admin_preferences).values(username="a", key="k2", value={"y": 2}))
            rows = sqlcore.run(cur, select(admin_preferences.c.key).where(
                admin_preferences.c.value.in_([{"x": 1}, {"z": 3}]))).fetchall()
        self.assertEqual(["k1"], [r["key"] for r in rows])

    def test_a_literal_percent_runs(self):
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, insert(searches).values(q="50% off", results=1))
            row = sqlcore.run(cur, select(func.replace(searches.c.q, "%", " percent").label("q"))).fetchone()
        self.assertEqual("50 percent off", row["q"])

    def test_run_many_executes_one_statement_for_a_batch_of_values(self):
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run_many(cur, [insert(searches).inline().values(q="q%d" % n, results=n)
                                   for n in range(3)])
            rows = sqlcore.run(cur, select(searches.c.q, searches.c.results)
                               .order_by(searches.c.q)).fetchall()
        self.assertEqual([("q0", 0), ("q1", 1), ("q2", 2)], [(r["q"], r["results"]) for r in rows])

    def test_run_many_refuses_statements_of_different_structure(self):
        with db._connect() as conn, conn.cursor() as cur:
            with self.assertRaises(ValueError):
                sqlcore.run_many(cur, [insert(searches).values(q="a", results=1),
                                       insert(searches).values(q="b")])
            self.assertEqual(0, sqlcore.run(cur, select(func.count().label("n")).select_from(searches))
                             .fetchone()["n"])

    def test_a_prepared_statement_runs_over_a_batch_and_an_empty_batch_writes_nothing(self):
        from sqlalchemy import bindparam
        prepared = sqlcore.prepare(insert(searches).values(q=bindparam("v_q"), results=bindparam("v_r")))
        with db._connect() as conn, conn.cursor() as cur:
            prepared.run_many(cur, [])
            prepared.run_many(cur, [{"v_q": "p%d" % n, "v_r": n} for n in range(3)])
            rows = sqlcore.run(cur, select(searches.c.q, searches.c.results)
                               .order_by(searches.c.q)).fetchall()
        self.assertEqual([("p0", 0), ("p1", 1), ("p2", 2)], [(r["q"], r["results"]) for r in rows])

    def test_a_prepared_batch_whose_rows_render_different_sql_is_refused(self):
        from sqlalchemy import Text, bindparam
        prepared = sqlcore.prepare(delete(searches).where(
            searches.c.q.in_(bindparam("qs", expanding=True, type_=Text))))
        with db._connect() as conn, conn.cursor() as cur:
            with self.assertRaises(ValueError):
                prepared.run_many(cur, [{"qs": ["a"]}, {"qs": ["a", "b"]}])


if __name__ == "__main__":
    unittest.main()
