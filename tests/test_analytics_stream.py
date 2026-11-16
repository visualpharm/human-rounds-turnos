"""test_analytics_stream.py — the anonymous analytics stream end to end: ingest -> own Postgres -> export API ->
report payload. Own throwaway database (like test_searches.py).
"""

import datetime
import json
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")

import sqlalchemy as sa  # noqa: E402
from app.core import config, db, db_schema, db_stream, sqlcore  # noqa: E402
from app.core import schema_model as sm  # noqa: E402
config.ENV_PATH = ""
from app.auth import admin_auth  # noqa: E402
from app.modules.turnos import analytics_api_router, analytics_events, analytics_export, analytics_report  # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_analytics_stream",
                          seed=lambda: db_stream.has_snapshot(datetime.date(2000, 1, 1)))
CHROME = "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36"
JID = "visit0001abc"
PL = "load00001abc"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _wipe():
    with db._connect() as conn, conn.cursor() as cur:
        for t in (sm.analytics_events, sm.analytics_schedule, sm.analytics_keys):
            sqlcore.run(cur, sa.delete(t))


def _event(kind, **kw):
    e = {"id": analytics_events.new_id(), "ts": int(time.time()), "type": kind}
    e.update(kw)
    return e


class Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        _wipe()
        _env.set_env(self, TURNOS_ANALYTICS="1")
        analytics_export._HITS.clear()
        analytics_api_router._HITS.clear()
        analytics_export._VOCAB.update(at=time.time(), words=frozenset(("cardiologia", "perez")))
        self.client = TestClient(app_module.app)
        # The export holds back the last seconds (a row still being committed); these tests read at once.
        self.patch(analytics_api_router, "_LAG_S", 0)

    def patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value, create=True)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        analytics_export._VOCAB.update(at=0.0, words=frozenset())


class TestIngest(Base):
    def _batch(self, n=1, jid=JID):
        return {"events": [{"t": "page_view", "path": "/doctor/dr-perez", "jid": jid, "pl": PL, "lang": "es",
                            "ref": "https://www.google.com/search?q=dni+30123456"} for _ in range(n)]}

    def test_events_are_written_without_personal_fields(self):
        self.assertEqual(analytics_export.ingest(self._batch(), ip="203.0.113.5", user_agent=CHROME), 1)
        row = db_stream.events_page()[0]
        self.assertEqual((row["type"], row["route"], row["rid"], row["ref"]),
                         ("page_view", "/doctor/:id", "dr-perez", "www.google.com"))
        self.assertEqual(len(row["id"]), 23)
        blob = json.dumps({k: str(v) for k, v in row.items()})
        self.assertNotIn("203.0.113.5", blob)
        self.assertNotIn("30123456", blob)

    def test_off_means_nothing_is_written(self):
        _env.set_env(self, TURNOS_ANALYTICS="0")
        self.assertEqual(analytics_export.ingest(self._batch(), ip="203.0.113.5", user_agent=CHROME), 0)
        self.assertEqual(db_stream.events_page(), [])

    def test_rate_limit_per_ip_is_silent(self):
        # A shared address (the hospital's NAT) is many visits: the ceiling per address is high, the one per
        # visit (jid) is what stops a single browser that loops.
        self.patch(analytics_export, "_RATE_PER_MIN", 30)
        written = sum(analytics_export.ingest(self._batch(jid="visit%04d" % i + "abc"), ip="203.0.113.6",
                                              user_agent=CHROME) for i in range(40))
        self.assertEqual(written, 30)

    def test_one_visit_cannot_flood_even_under_the_address_ceiling(self):
        written = sum(analytics_export.ingest(self._batch(), ip="203.0.113.7", user_agent=CHROME)
                      for _ in range(analytics_export._RATE_PER_VISIT + 10))
        self.assertEqual(written, analytics_export._RATE_PER_VISIT)
        self.assertEqual(analytics_export.ingest(self._batch(jid="othervisit01"), ip="203.0.113.7",
                                                 user_agent=CHROME), 1)

    def test_a_hospital_sized_nat_is_not_cut_at_the_old_limit(self):
        # Before: 120 batches a minute per address, whoever sent them.
        written = sum(analytics_export.ingest(self._batch(jid="visit%04d" % i + "abc"), ip="203.0.113.8",
                                              user_agent=CHROME) for i in range(150))
        self.assertEqual(written, 150)

    def test_the_rate_table_never_holds_the_address(self):
        analytics_export.ingest(self._batch(), ip="203.0.113.9", user_agent=CHROME)
        self.assertNotIn("203.0.113.9", json.dumps([str(k) for k in analytics_export._HITS]))

    def test_the_rate_table_stays_bounded_under_a_flood_of_new_visit_ids(self):
        self.patch(analytics_export, "_MAX_KEYS", 40)
        for i in range(300):
            analytics_export.ingest(self._batch(jid="flood%05d" % i + "abc"), ip="203.0.113.20", user_agent=CHROME)
            self.assertLessEqual(len(analytics_export._HITS), 41)
        self.assertEqual(analytics_export.ingest(self._batch(jid="afterflood01"), ip="203.0.113.20",
                                                 user_agent=CHROME), 1)

    def test_repeated_id_is_ignored(self):
        e = _event("step", step="done", service=None, jid=JID, pl=PL)
        self.assertEqual(db_stream.insert_events([e]), 1)
        self.assertEqual(db_stream.insert_events([e]), 0)

    def test_search_text_is_reduced_to_catalog_words(self):
        self.assertTrue(analytics_export.record_search("cardiologia Juan Gomez", 3, "/doctors", PL, JID,
                                                       ip="203.0.113.5", user_agent=CHROME))
        row = db_stream.events_page(kind="search")[0]
        self.assertNotIn("juan", row["q"])
        self.assertNotIn("gomez", row["q"])
        self.assertFalse(analytics_export.record_search("Juan Gomez", 0, "/doctors", PL, JID,
                                                        ip="203.0.113.5", user_agent=CHROME))

    def test_search_beacon_has_the_same_robot_filter_and_limit_as_the_event_beacon(self):
        for ua in ("Googlebot/2.1", "curl/8.0", "HeadlessChrome/120", ""):
            self.assertFalse(analytics_export.record_search("cardiologia", 3, "/doctors", PL, JID,
                                                            ip="203.0.113.5", user_agent=ua), ua)
        self.assertEqual(db_stream.events_page(kind="search"), [])
        self.patch(analytics_export, "_RATE_PER_VISIT", 3)
        wrote = [analytics_export.record_search("cardiologia", 3, "/doctors", PL, JID,
                                                ip="203.0.113.5", user_agent=CHROME) for _ in range(6)]
        self.assertEqual(wrote, [True] * 3 + [False] * 3)

    def test_search_log_endpoint_passes_the_robot_filter_through(self):
        body = {"q": "cardiologia", "resultados": 2, "origen": "/doctors", "sid": PL, "jid": JID}
        self.assertEqual(self.client.post("/api/search-log", json=body,
                                          headers={"User-Agent": "Googlebot/2.1"}).status_code, 204)
        self.assertEqual(db_stream.events_page(kind="search"), [])
        self.assertEqual(self.client.post("/api/search-log", json=body,
                                          headers={"User-Agent": CHROME}).status_code, 204)
        self.assertEqual(len(db_stream.events_page(kind="search")), 1)

    def test_prune_removes_old_events_only(self):
        old = _event("step", step="done", service=None, jid=JID, pl=PL)
        old["ts"] = int(time.time()) - 500 * 86400
        db_stream.insert_events([old, _event("step", step="done", service=None, jid=JID, pl=PL)])
        db_stream.prune(datetime.datetime.now(datetime.timezone.utc), 400)
        self.assertEqual(len(db_stream.events_page()), 1)


class TestKeys(Base):
    def test_only_the_hash_is_stored_and_revocation_works(self):
        kid, plain = db_stream.create_key("e2e-reader")
        self.assertTrue(plain.startswith("hra_"))
        with db._connect() as conn, conn.cursor() as cur:
            row = sqlcore.run(cur, sa.select(*sm.analytics_keys.c)).fetchone()
        self.assertNotIn(plain, json.dumps({k: str(v) for k, v in row.items()}))
        self.assertTrue(db_stream.verify_key(plain))
        self.assertFalse(db_stream.verify_key(plain + "x"))
        self.assertFalse(db_stream.verify_key(""))
        self.assertTrue(db_stream.revoke_key(kid))
        self.assertFalse(db_stream.verify_key(plain))
        self.assertFalse(db_stream.revoke_key(kid))


class TestExportApi(Base):
    def setUp(self):
        super().setUp()
        _, self.key = db_stream.create_key("test")
        self.auth = {"Authorization": "Bearer " + self.key}
        db_stream.insert_events([_event("step", step="done", service="cardiologia", jid=JID, pl=PL)
                                 for _ in range(5)])
        db_stream.replace_schedule(datetime.date.today(), [
            {"day": datetime.date.today(), "doctor": "dr-perez", "center": "pinamar", "service": "cardiologia",
             "hours": 4, "slots_total": 8, "slots_web": 6, "taken": 2, "free": 4}])

    def _lines(self, response):
        return [json.loads(line) for line in response.text.splitlines() if line]

    def test_no_key_wrong_key_and_off_are_bare(self):
        for headers in ({}, {"Authorization": "Bearer hra_nope"}, {"Authorization": "Basic abc"}):
            r = self.client.get("/api/analytics/events", headers=headers)
            self.assertEqual((r.status_code, r.content), (401, b""))
        _env.set_env(self, TURNOS_ANALYTICS="0")
        self.assertEqual(self.client.get("/api/analytics/events", headers=self.auth).status_code, 404)

    def test_events_ndjson_and_allowlisted_fields(self):
        r = self.client.get("/api/analytics/events?type=step", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/x-ndjson"))
        rows = self._lines(r)
        self.assertEqual(len(rows), 5)
        self.assertEqual(set(rows[0]), set(analytics_api_router._EVENT_FIELDS))
        self.assertEqual(rows[0]["step"], "done")

    def test_paging_by_cursor_covers_everything_once(self):
        seen, after = [], ""
        for _ in range(10):
            r = self.client.get("/api/analytics/events?limit=2&after=" + after, headers=self.auth)
            seen += [row["id"] for row in self._lines(r)]
            after = r.headers.get("X-Next-After", "")
            if not after:
                break
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)
        self.assertEqual(seen, sorted(seen))

    def test_malformed_cursor_or_type_is_400(self):
        for url in ("/api/analytics/events?after=zz", "/api/analytics/events?type=login",
                    "/api/analytics/schedule?after=zz"):
            self.assertEqual(self.client.get(url, headers=self.auth).status_code, 400, url)

    def test_the_schedule_cursor_survives_a_pipe_in_a_name(self):
        day = datetime.date.today()
        db_stream.replace_schedule(day, [
            {"day": day, "doctor": "dr-a|b", "center": "centro|norte", "service": "cardio|logia",
             "hours": 1, "slots_total": 2, "slots_web": 2, "taken": 0, "free": 2},
            {"day": day, "doctor": "dr-perez", "center": "pinamar", "service": "cardiologia",
             "hours": 1, "slots_total": 2, "slots_web": 2, "taken": 0, "free": 2},
            {"day": day, "doctor": "dr-z|", "center": "|", "service": "|x|",
             "hours": 1, "slots_total": 2, "slots_web": 2, "taken": 0, "free": 2}])
        seen, after = [], ""
        for _ in range(6):
            r = self.client.get("/api/analytics/schedule?limit=1&after=" + after, headers=self.auth)
            self.assertEqual(r.status_code, 200)
            seen += [(row["doctor"], row["center"], row["service"]) for row in self._lines(r)]
            after = r.headers.get("X-Next-After", "")
            if not after:
                break
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 3)

    def test_date_window(self):
        tomorrow = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        self.assertEqual(self._lines(self.client.get("/api/analytics/events?since=" + tomorrow,
                                                     headers=self.auth)), [])

    def test_schedule_feed(self):
        rows = self._lines(self.client.get("/api/analytics/schedule", headers=self.auth))
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), set(analytics_api_router._SCHEDULE_FIELDS))
        self.assertEqual((rows[0]["doctor"], rows[0]["free"], rows[0]["hours"]), ("dr-perez", 4, 4.0))

    def test_page_flag_is_a_separate_tag_and_only_when_on(self):
        import re
        from app.core import static_fingerprint
        on = static_fingerprint.manifest_script()
        self.assertIn("<script>window.SANA_EV=1;</script>", on)
        self.assertTrue(re.search(r"window\.SANA_STATIC=(\{.*?\});</script>", on))  # what smoke_prod reads
        _env.set_env(self, TURNOS_ANALYTICS="0")
        self.assertNotIn("SANA_EV", static_fingerprint.manifest_script())

    def test_snapshot_builder_runs_on_the_real_catalog(self):
        # The daily thread swallows every error, so a broken import here once meant an empty feed in prod.
        from app.modules.turnos import analytics_schedule
        rows = analytics_schedule.build()
        self.assertTrue(rows)
        self.assertEqual(set(rows[0]), {"day", "doctor", "center", "service", "hours", "slots_total",
                                        "slots_web", "taken", "free"})

    def test_rate_limit_is_429(self):
        for _ in range(analytics_api_router._RATE_PER_MIN):
            self.client.get("/api/analytics/events?limit=1", headers=self.auth)
        self.assertEqual(self.client.get("/api/analytics/events?limit=1", headers=self.auth).status_code, 429)

    def test_guessing_keys_is_limited_by_address_before_the_database_is_asked(self):
        self.patch(analytics_api_router, "_IP_RATE_PER_MIN", 5)
        with mock.patch.object(db_stream, "verify_key", wraps=db_stream.verify_key) as verify:
            codes = [self.client.get("/api/analytics/events", headers={"Authorization": "Bearer hra_guess%d" % i}
                                     ).status_code for i in range(8)]
        self.assertEqual(codes, [401] * 5 + [429] * 3)
        self.assertEqual(verify.call_count, 5)

    def test_events_still_being_committed_are_held_back_so_a_cursor_never_skips_one(self):
        # Ids are made when the event is cleaned and the row commits a moment later: a newer id can be
        # visible before an older one. Serving only what is older than the lag closes that window.
        self.patch(analytics_api_router, "_LAG_S", 10)
        db_stream.insert_events([_event("step", step="done", service=None, jid=JID, pl=PL)])
        r = self.client.get("/api/analytics/events?type=step", headers=self.auth)
        self.assertEqual(len(self._lines(r)), 0)  # the five of setUp and this one are all younger than 10 s
        old = _event("step", step="done", service=None, jid=JID, pl=PL)
        old["id"] = "%013x" % (int(time.time() * 1000) - 60000) + "abcdef0123"
        db_stream.insert_events([old])
        rows = self._lines(self.client.get("/api/analytics/events?type=step", headers=self.auth))
        self.assertEqual([x["id"] for x in rows], [old["id"]])

    def test_revoked_key_stops_at_once(self):
        self.assertEqual(self.client.get("/api/analytics/events", headers=self.auth).status_code, 200)
        db_stream.revoke_key(db_stream.list_keys()[0]["id"])
        self.assertEqual(self.client.get("/api/analytics/events", headers=self.auth).status_code, 401)


class TestKeyAdmin(Base):
    def test_admin_issues_lists_and_revokes_and_others_cannot(self):
        self.assertEqual(self.client.get("/api/admin/analytics/keys").status_code, 401)
        cookie = {"Cookie": "st_admin=" + admin_auth._mint_token("analytics.test", "admin")}
        made = self.client.post("/api/admin/analytics/keys", json={"label": "pinamar"}, headers=cookie)
        self.assertEqual(made.status_code, 201)
        body = made.json()
        self.assertTrue(body["key"].startswith("hra_"))
        listed = self.client.get("/api/admin/analytics/keys", headers=cookie).json()
        self.assertNotIn(body["key"], json.dumps(listed))
        self.assertEqual([k["label"] for k in listed["keys"]], ["pinamar"])
        gone = self.client.delete("/api/admin/analytics/keys?id=%d" % body["id"], headers=cookie)
        self.assertEqual(gone.status_code, 200)
        self.assertFalse(db_stream.verify_key(body["key"]))
        staff = {"Cookie": "st_admin=" + admin_auth._mint_token("operator.test", "operador")}
        self.assertIn(self.client.get("/api/admin/analytics/keys", headers=staff).status_code, (401, 403))


class TestReport(Base):
    def test_funnel_counts_visits_through_each_step(self):
        def pv(jid, pl, route, rid=None):
            return _event("page_view", route=route, rid=rid, ref="direct", lang="es", device="mobile", jid=jid, pl=pl)

        def st(jid, pl, step):
            return _event("step", step=step, service="cardiologia", jid=jid, pl=pl)

        def se(jid, pl, q):
            return _event("search", q=q, n=2, origin="/doctors", jid=jid, pl=pl)

        db_stream.insert_events([
            pv("v1aaaaaaaa", "p1aaaaaaaa", "/"), se("v1aaaaaaaa", "p2aaaaaaaa", "cardiologia"),
            pv("v1aaaaaaaa", "p3aaaaaaaa", "/doctor/:id", "dr-perez"),
            st("v1aaaaaaaa", "p4aaaaaaaa", "slots_shown"), st("v1aaaaaaaa", "p4aaaaaaaa", "submit"),
            st("v1aaaaaaaa", "p4aaaaaaaa", "done"),
            pv("v2aaaaaaaa", "p5aaaaaaaa", "/"), se("v2aaaaaaaa", "p6aaaaaaaa", "pediatria"),
            pv("v3aaaaaaaa", "p7aaaaaaaa", "/"),
        ])
        out = analytics_report.payload(30)
        self.assertEqual(out["visitas"], 3)
        by = {s["paso"]: s["n"] for s in out["embudo"]}
        self.assertEqual((by["visit"], by["search"], by["done"]), (3, 2, 1))
        self.assertIn("cardiologia", json.dumps(out["busquedas"]))

    def test_funnel_never_grows_along_the_booking_path(self):
        def pv(jid, route, rid=None):
            return _event("page_view", route=route, rid=rid, ref="direct", lang="es", device="mobile",
                          jid=jid, pl="p" + jid)

        def st(jid, step):
            return _event("step", step=step, service="cardiologia", jid=jid, pl="p" + jid)

        db_stream.insert_events([
            pv("idle00001a", "/"),
            # the same person came back after 30 idle minutes: a new visit id with no page view of its own
            # in the window of the funnel (it opened the wizard from a saved tab)
            st("idle00001b", "slots_shown"), st("idle00001b", "submit"), st("idle00001b", "done"),
            # a lost beacon: «done» arrived, the steps before it never did
            st("lost0000001", "done"),
            pv("plain000001", "/"),
        ])
        steps = ("visit", "profile", "slots_shown", "submit", "done")
        by = {s["paso"]: s["n"] for s in analytics_report.payload(30)["embudo"]}
        counts = [by[k] for k in steps]
        self.assertEqual(counts, sorted(counts, reverse=True), by)
        self.assertEqual(counts, [4, 2, 2, 2, 2])
        self.assertLessEqual(by["search"], by["visit"])

    def test_report_reads_the_window_in_pages_not_all_at_once(self):
        db_stream.insert_events([_event("page_view", route="/", rid=None, ref="direct", lang="es",
                                        device="mobile", jid="v%09d" % (i % 3), pl="p%09d" % i)
                                 for i in range(11)])
        paged = [e["id"] for e in db_stream.events_iter(datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc),
                                                        chunk=2)]
        self.assertEqual(len(paged), 11)
        self.assertEqual(len(set(paged)), 11)
        out = analytics_report.payload(30)
        self.assertEqual((out["eventos"], out["vistas"], out["visitas"]), (11, 11, 3))


class TestSchedule(Base):
    def test_an_empty_snapshot_is_not_rebuilt_every_ten_minutes(self):
        analytics_export._EMPTY.clear()
        now = datetime.datetime(2026, 11, 13, 5, 0, tzinfo=datetime.timezone.utc)
        from app.modules.turnos import analytics_schedule
        with mock.patch.object(analytics_schedule, "build", return_value=[]) as build:
            for minutes in (0, 10, 20, 30, 40):
                analytics_export.snapshot_once(now + datetime.timedelta(minutes=minutes))
            self.assertEqual(build.call_count, 1)
            analytics_export.snapshot_once(now + datetime.timedelta(hours=2))
            self.assertEqual(build.call_count, 2)  # it does try again later: an empty catalog may be a blip


if __name__ == "__main__":
    unittest.main()
