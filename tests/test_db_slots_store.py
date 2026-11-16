"""test_db_slots_store.py — the slot / day-queue / dependents statements of `core/db.py` against a real Postgres.

Stage 6 of the SQLAlchemy Core move converts the booking part of `core/db.py`. This file pins the
slice that is NOT the request lifecycle: the soft-lock holds on a slot (`hold_*`, `holds_*`, with
the race of two tokens for one slot and the fail-closed `DbUnavailable` contract), the durable
day-queue events behind the SSE stream, the waitlist lookups, the booking-limit counters and the
«can this doctor/service/center be deleted» counters, the account's dependents, the day queue read
(`list_approved_day`, `approved_calendar_groups`, `count_appointments_on_dates`) and the chat
retention purge. It was written and run green on the hand-written SQL first, and passes unedited
on Core.

The `None` cases matter: the old `col = %s` with a Python `None` is `col = NULL` and finds nothing,
Core's `col == None` is `IS NULL` and would find every row whose column is NULL.
"""
import os
import sys
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

import psycopg  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_wire  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_slots_store")

UTC = timezone.utc
ART = ZoneInfo("America/Argentina/Buenos_Aires")

FUTURE = datetime.now(UTC) + timedelta(days=30)
PAST = datetime.now(UTC) - timedelta(days=30)
DIA = (datetime.now(ART) + timedelta(days=7)).date().isoformat()


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _now():
    return _sql("SELECT now() AS n")[0]["n"]


def _insert(table, **cols):
    names = list(cols)
    rows = _sql("INSERT INTO %s (%s) VALUES (%s) RETURNING *"
                % (table, ", ".join(names), ", ".join(["%s"] * len(names))), list(cols.values()))
    return rows[0]


def _req(dni="30000001", servicio="clinica", estado="pendiente", turno=None, **cols):
    return _insert("booking_requests", dni=dni, service_id=servicio, status=estado,
                   appointment_at=turno, **cols)["id"]


def _user(dni, first="Ana", last="Perez", coverage=None):
    from psycopg.types.json import Jsonb
    _insert("users", dni=dni, first_name=first, last_name=last,
            coverage=Jsonb(coverage) if coverage is not None else None)


def _holds():
    return _sql("SELECT service_id, day, time, token, request_id, expires FROM slot_holds ORDER BY id")


def _art(y, m, d, hh=0, mm=0):
    """An aware instant for a wall-clock time in the installation timezone."""
    return datetime(y, m, d, hh, mm, tzinfo=ART)


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _sql("TRUNCATE booking_requests, slot_holds, waitlist, dependents, users, day_queue_events, "
             "chat_messages, chat_summaries, chat_compactions, interviews, consultations, "
             "patient_summaries, operators RESTART IDENTITY CASCADE")


# ---- day queue events (SSE cursor) --------------------------------------------------

class TestDayQueueEvents(DbCase):
    def _events(self, n):
        for i in range(n):
            _insert("day_queue_events", appointment_day=date(2030, 1, 1 + i % 28),
                    center_slug="c%d" % i, assigned_doctor_id="d%d" % i, assigned_doctor="Doc %d" % i)

    def test_high_water_is_zero_on_a_new_installation(self):
        self.assertEqual(db_slots.day_queue_event_high_water(), 0)

    def test_high_water_is_the_largest_id_as_an_int(self):
        self._events(3)
        got = db_slots.day_queue_event_high_water()
        self.assertIs(type(got), int)
        self.assertEqual(got, max(r["id"] for r in _sql("SELECT id FROM day_queue_events")))

    def test_events_after_the_cursor_in_id_order_with_the_five_columns(self):
        self._events(4)
        ids = [r["id"] for r in _sql("SELECT id FROM day_queue_events ORDER BY id")]
        got = db_slots.day_queue_events_after(ids[1])
        self.assertEqual([e["id"] for e in got], ids[2:])
        self.assertEqual(list(got[0]), ["id", "appointment_day", "center_slug",
                                        "assigned_doctor_id", "assigned_doctor"])
        self.assertEqual(got[0]["center_slug"], "c2")
        self.assertEqual(got[0]["assigned_doctor_id"], "d2")
        self.assertEqual(got[0]["assigned_doctor"], "Doc 2")
        self.assertIsInstance(got[0]["appointment_day"], date)
        self.assertIsInstance(got, list)

    def test_cursor_is_exclusive_and_the_tail_is_empty(self):
        self._events(2)
        last = db_slots.day_queue_event_high_water()
        self.assertEqual(db_slots.day_queue_events_after(last), [])
        self.assertEqual(len(db_slots.day_queue_events_after(last - 1)), 1)

    def test_missing_or_negative_cursor_reads_from_the_start(self):
        self._events(3)
        for cursor in (None, 0, "", -7, "0"):
            with self.subTest(cursor=cursor):
                self.assertEqual(len(db_slots.day_queue_events_after(cursor)), 3)

    def test_string_cursor_is_coerced(self):
        self._events(3)
        first = _sql("SELECT min(id) AS i FROM day_queue_events")[0]["i"]
        self.assertEqual(len(db_slots.day_queue_events_after(str(first))), 2)

    def test_limit_is_bounded_between_one_and_512(self):
        self._events(3)
        self.assertEqual(len(db_slots.day_queue_events_after(0, limit=2)), 2)
        self.assertEqual(len(db_slots.day_queue_events_after(0, limit=-5)), 1)
        self.assertEqual(len(db_slots.day_queue_events_after(0, limit=0)), 3)       # 0 = the default 128
        self.assertEqual(len(db_slots.day_queue_events_after(0, limit=None)), 3)
        self.assertEqual(len(db_slots.day_queue_events_after(0, limit="1")), 1)
        _sql("INSERT INTO day_queue_events (appointment_day) SELECT date '2030-01-01' FROM generate_series(1, 600)")
        self.assertEqual(len(db_slots.day_queue_events_after(0)), 128)
        self.assertEqual(len(db_slots.day_queue_events_after(0, limit=100000)), 512)

    def test_null_optional_columns_are_returned_as_none(self):
        _insert("day_queue_events", appointment_day=date(2030, 2, 2))
        row = db_slots.day_queue_events_after(0)[0]
        self.assertIsNone(row["center_slug"])
        self.assertIsNone(row["assigned_doctor_id"])
        self.assertIsNone(row["assigned_doctor"])


# ---- waitlist lookups -----------------------------------------------------------------

class TestWaitlistLookups(DbCase):
    def test_get_returns_the_wire_row_by_its_own_id(self):
        sid = _req()
        w = _insert("waitlist", request_id=sid, dni="30000001", email="a@example.test",
                    service_id="clinica", center_slug="hosp", appointment_at=FUTURE)
        row = db_slots.waitlist_get(w["id"])
        self.assertEqual(row["id"], w["id"])
        self.assertEqual(row["solicitud_id"], sid)
        self.assertEqual(row["servicio_id"], "clinica")
        self.assertEqual(row["centro_slug"], "hosp")
        self.assertEqual(row["turno_dt"], FUTURE)
        self.assertIs(row["activo"], True)
        self.assertEqual(row["dni"], "30000001")
        self.assertEqual(row["email"], "a@example.test")
        self.assertNotIn("request_id", row)

    def test_get_of_an_unknown_or_missing_id_is_none(self):
        _insert("waitlist", service_id="clinica")
        self.assertIsNone(db_slots.waitlist_get(987654))
        self.assertIsNone(db_slots.waitlist_get(None))

    def test_active_is_true_only_for_an_active_entry_of_that_request(self):
        a, b, c = _req(), _req(), _req()
        _insert("waitlist", request_id=a, active=True)
        _insert("waitlist", request_id=b, active=False)
        self.assertTrue(db_slots.waitlist_active(a))
        self.assertFalse(db_slots.waitlist_active(b))
        self.assertFalse(db_slots.waitlist_active(c))

    def test_a_null_active_flag_is_not_active(self):
        a = _req()
        _sql("INSERT INTO waitlist (request_id, active) VALUES (%s, NULL)", (a,))
        self.assertFalse(db_slots.waitlist_active(a))

    def test_one_active_entry_among_inactive_ones_is_enough(self):
        a = _req()
        _insert("waitlist", request_id=a, active=False)
        _insert("waitlist", request_id=a, active=True)
        self.assertTrue(db_slots.waitlist_active(a))

    def test_none_finds_nothing_even_over_rows_without_a_request(self):
        _insert("waitlist", service_id="clinica", active=True)     # request_id IS NULL
        self.assertFalse(db_slots.waitlist_active(None))
        self.assertIsNone(db_slots.waitlist_get(None))


# ---- holds ---------------------------------------------------------------------------

class TestHoldAcquire(DbCase):
    def test_a_free_slot_is_taken_with_the_ttl_from_now(self):
        before = _now()
        self.assertIs(db_slots.hold_acquire("cardio", DIA, "09:00", "tokA", ttl_min=15), True)
        after = _now()
        (h,) = _holds()
        self.assertEqual((h["service_id"], h["day"].isoformat(), h["time"].strftime("%H:%M"), h["token"]),
                         ("cardio", DIA, "09:00", "tokA"))
        self.assertIsNone(h["request_id"])
        self.assertGreaterEqual(h["expires"], before + timedelta(minutes=15))
        self.assertLessEqual(h["expires"], after + timedelta(minutes=15))

    def test_ttl_is_in_minutes_and_defaults_to_fifteen(self):
        before = _now()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_acquire("cardio", DIA, "09:20", "tokB", ttl_min=2)
        rows = {h["token"]: h["expires"] for h in _holds()}
        self.assertGreaterEqual(rows["tokA"], before + timedelta(minutes=15))
        self.assertLess(rows["tokB"], before + timedelta(minutes=3))

    def test_the_same_token_renews_and_extends_the_expiry(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA", ttl_min=1)
        first = _holds()[0]["expires"]
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:00", "tokA", ttl_min=30))
        rows = _holds()
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["expires"], first + timedelta(minutes=20))

    def test_another_token_cannot_take_a_live_hold(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        before = _holds()
        self.assertIs(db_slots.hold_acquire("cardio", DIA, "09:00", "tokB"), False)
        self.assertEqual(_holds(), before)

    def test_another_token_takes_over_an_expired_hold(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        _sql("UPDATE slot_holds SET expires = now() - interval '1 second'")
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:00", "tokB"))
        (h,) = _holds()
        self.assertEqual(h["token"], "tokB")
        self.assertGreater(h["expires"], _now())

    def test_an_expired_hold_linked_to_a_request_is_not_swept_but_is_taken_over_by_the_upsert(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_link_booking_request("tokA", sid, PAST)
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "10:00", "tokC"))
        # the linked row of the other slot survives the sweep (it has a request)
        self.assertEqual(sorted(h["time"].strftime("%H:%M") for h in _holds()), ["09:00", "10:00"])
        # the expired linked hold of THIS slot is taken over by the upsert
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:00", "tokB"))
        mine = [h for h in _holds() if h["time"].strftime("%H:%M") == "09:00"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["token"], "tokB")

    def test_a_linked_live_hold_blocks_every_other_token(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_link_booking_request("tokA", sid, FUTURE)
        self.assertFalse(db_slots.hold_acquire("cardio", DIA, "09:00", "tokB"))

    def test_choosing_a_new_slot_releases_the_previous_loose_hold_of_the_token(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_acquire("otro", DIA, "11:00", "tokA")
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:20", "tokA"))
        self.assertEqual(sorted((h["service_id"], h["time"].strftime("%H:%M")) for h in _holds()),
                         [("cardio", "09:20")])

    def test_move_semantics_do_not_touch_a_linked_hold_of_the_same_token(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_link_booking_request("tokA", sid, FUTURE)
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "10:00", "tokA"))
        self.assertEqual(sorted(h["time"].strftime("%H:%M") for h in _holds()), ["09:00", "10:00"])

    def test_move_semantics_do_not_touch_other_tokens(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokB")
        db_slots.hold_acquire("cardio", DIA, "09:20", "tokA")
        self.assertEqual(sorted(h["token"] for h in _holds()), ["tokA", "tokB"])

    def test_re_acquiring_the_same_slot_keeps_it_not_deleting_it(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:00", "tokA"))
        self.assertEqual(len(_holds()), 1)

    def test_every_acquire_sweeps_expired_loose_holds_of_any_token(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "old1")
        db_slots.hold_acquire("otro", DIA, "09:00", "old2")
        db_slots.hold_acquire("otro", DIA, "12:00", "live")
        _sql("UPDATE slot_holds SET expires = now() - interval '1 minute' WHERE token IN ('old1', 'old2')")
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "15:00", "new"))
        self.assertEqual(sorted(h["token"] for h in _holds()), ["live", "new"])

    def test_a_slot_is_per_service_day_and_time(self):
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:00", "tokA"))
        self.assertTrue(db_slots.hold_acquire("otro", DIA, "09:00", "tokB"))
        other_day = (date.fromisoformat(DIA) + timedelta(days=1)).isoformat()
        self.assertTrue(db_slots.hold_acquire("cardio", other_day, "09:00", "tokC"))
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:30", "tokD"))
        self.assertEqual(len(_holds()), 4)

    def test_nine_and_zero_nine_are_the_same_slot(self):
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "9:00", "tokA"))
        self.assertFalse(db_slots.hold_acquire("cardio", DIA, "09:00", "tokB"))
        self.assertEqual(_holds()[0]["time"].strftime("%H:%M"), "09:00")

    def test_surrounding_blanks_are_ignored(self):
        self.assertTrue(db_slots.hold_acquire("cardio", " %s " % DIA, " 09:00 ", "tokA"))
        self.assertEqual(len(_holds()), 1)

    def test_an_impossible_day_or_time_takes_nothing_and_writes_nothing(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        before = _holds()
        for dia, hora in (("2024-13-45", "09:00"), (DIA, "25:61"), ("", "09:00"), (DIA, ""),
                          (None, None), ("manana", "temprano"), ("2024-1-1", "9:00")):
            with self.subTest(dia=dia, hora=hora):
                self.assertIs(db_slots.hold_acquire("cardio", dia, hora, "tokA"), False)
        self.assertEqual(_holds(), before)         # not even the move-semantics delete ran

    def test_unavailable_database_fails_closed(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
            self.assertIs(db_slots.hold_acquire("cardio", DIA, "09:00", "tokA"), False)

    def test_a_none_service_or_token_violates_not_null_and_leaves_the_holds_alone(self):
        # `NOT (service_id = NULL AND ...)` is NULL: the move deletes nothing; `IS NULL` would delete
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        before = _holds()
        with self.assertRaises(psycopg.errors.NotNullViolation):
            db_slots.hold_acquire(None, DIA, "10:00", "tokA")
        with self.assertRaises(psycopg.errors.NotNullViolation):
            db_slots.hold_acquire("cardio", DIA, "10:00", None)
        self.assertEqual(_holds(), before)

    def test_other_errors_are_not_swallowed(self):
        with patch.object(db, "_connect", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")

    def test_the_three_statements_commit_together(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_acquire("cardio", DIA, "10:00", "tokA")      # delete 09:00 + insert 10:00
        self.assertEqual([h["time"].strftime("%H:%M") for h in _holds()], ["10:00"])


class TestHoldRaces(DbCase):
    def _race(self, tokens, slot):
        barrier = threading.Barrier(len(tokens))
        wins, errors, lock = [], [], threading.Lock()

        def go(tok):
            try:
                barrier.wait(timeout=10)
                if db_slots.hold_acquire("cardio", DIA, slot, tok):
                    with lock:
                        wins.append(tok)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(repr(exc))

        threads = [threading.Thread(target=go, args=(t,)) for t in tokens]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        return wins

    def test_two_tokens_one_free_slot_exactly_one_winner_every_round(self):
        for rnd in range(20):
            slot = "%02d:%02d" % (8 + rnd // 3, (rnd % 3) * 20)
            with self.subTest(slot=slot):
                wins = self._race(["A%d" % rnd, "B%d" % rnd], slot)
                self.assertEqual(len(wins), 1)
                rows = [h for h in _holds() if h["time"].strftime("%H:%M") == slot]
                self.assertEqual([h["token"] for h in rows], wins)

    def test_many_tokens_one_free_slot_exactly_one_winner(self):
        wins = self._race(["T%d" % i for i in range(10)], "10:00")
        self.assertEqual(len(wins), 1)
        self.assertEqual([h["token"] for h in _holds()], wins)

    def test_two_tokens_racing_for_an_expired_slot_exactly_one_takes_it(self):
        for rnd in range(10):
            slot = "%02d:00" % (8 + rnd)
            db_slots.hold_acquire("cardio", DIA, slot, "stale")
            _sql("UPDATE slot_holds SET expires = now() - interval '1 minute' WHERE token='stale'")
            with self.subTest(slot=slot):
                wins = self._race(["A%d" % rnd, "B%d" % rnd], slot)
                self.assertEqual(len(wins), 1)
                rows = [h for h in _holds() if h["time"].strftime("%H:%M") == slot]
                self.assertEqual([h["token"] for h in rows], wins)

    def test_the_same_token_twice_both_succeed(self):
        wins = self._race(["same", "same"], "11:00")
        self.assertEqual(wins, ["same", "same"])
        self.assertEqual(len(_holds()), 1)


class TestHoldRelease(DbCase):
    def test_releases_only_the_loose_holds_of_that_token(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_acquire("cardio", DIA, "10:00", "tokB")
        db_slots.hold_acquire("otro", DIA, "10:00", "tokA")        # moves tokA, only this one remains
        db_slots.hold_link_booking_request("tokA", sid, FUTURE)    # now linked
        db_slots.hold_acquire("cardio", DIA, "11:00", "tokA")      # a new loose one next to the linked
        self.assertIsNone(db_slots.hold_release("tokA"))
        self.assertEqual(sorted((h["token"], h["request_id"] is not None) for h in _holds()),
                         [("tokA", True), ("tokB", False)])

    def test_unknown_token_none_and_empty_release_nothing(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        for tok in ("nadie", "", None):
            db_slots.hold_release(tok)
        self.assertEqual(len(_holds()), 1)

    def test_unavailable_database_is_a_no_op(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
            self.assertIsNone(db_slots.hold_release("tokA"))

    def test_the_slot_is_free_again_for_another_token(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_release("tokA")
        self.assertTrue(db_slots.hold_acquire("cardio", DIA, "09:00", "tokB"))


class TestHoldsActive(DbCase):
    def test_only_unexpired_holds_loose_or_linked_in_wire_shape(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:05", "live")
        db_slots.hold_acquire("otro", DIA, "14:30", "linked")
        db_slots.hold_link_booking_request("linked", sid, FUTURE)
        _insert("slot_holds", service_id="viejo", day=date.fromisoformat(DIA), time="08:00",
                token="dead", expires=PAST)
        got = sorted(db_slots.holds_active(), key=lambda h: h["token"])
        self.assertEqual(got, [
            {"servicio_id": "otro", "dia": DIA, "hora": "14:30", "token": "linked", "solicitud_id": sid},
            {"servicio_id": "cardio", "dia": DIA, "hora": "09:05", "token": "live", "solicitud_id": None},
        ])
        self.assertEqual(list(got[0]), ["servicio_id", "dia", "hora", "token", "solicitud_id"])

    def test_the_expiry_boundary_is_now(self):
        _insert("slot_holds", service_id="a", day=date(2031, 1, 1), time="08:00", token="x",
                expires=_now() - timedelta(seconds=1))
        _insert("slot_holds", service_id="a", day=date(2031, 1, 1), time="09:00", token="y",
                expires=_now() + timedelta(minutes=5))
        self.assertEqual([h["token"] for h in db_slots.holds_active()], ["y"])

    def test_empty_table(self):
        self.assertEqual(db_slots.holds_active(), [])

    def test_unavailable_database_is_an_empty_list(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
            self.assertEqual(db_slots.holds_active(), [])


class TestHoldLinkAndRelease(DbCase):
    def test_link_binds_the_loose_hold_of_the_token_and_sets_the_expiry(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_acquire("cardio", DIA, "10:00", "tokB")
        when = FUTURE.replace(microsecond=0)
        self.assertIsNone(db_slots.hold_link_booking_request("tokA", sid, when))
        rows = {h["token"]: h for h in _holds()}
        self.assertEqual(rows["tokA"]["request_id"], sid)
        self.assertEqual(rows["tokA"]["expires"], when)
        self.assertIsNone(rows["tokB"]["request_id"])

    def test_link_ignores_an_already_linked_hold(self):
        a, b = _req(), _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_link_booking_request("tokA", a, FUTURE)
        db_slots.hold_link_booking_request("tokA", b, PAST)
        (h,) = _holds()
        self.assertEqual(h["request_id"], a)
        self.assertEqual(h["expires"], FUTURE.replace(microsecond=h["expires"].microsecond))

    def test_link_with_an_unknown_token_changes_nothing(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        before = _holds()
        db_slots.hold_link_booking_request("nadie", sid, FUTURE)
        db_slots.hold_link_booking_request(None, sid, FUTURE)
        self.assertEqual(_holds(), before)

    def test_a_past_expiry_makes_the_hold_stop_counting_at_once(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_link_booking_request("tokA", sid, PAST)
        self.assertEqual(db_slots.holds_active(), [])

    def test_link_unavailable_database_is_a_no_op(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
            self.assertIsNone(db_slots.hold_link_booking_request("tokA", 1, FUTURE))

    def test_release_deletes_the_holds_of_that_request_only(self):
        a, b = _req(), _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")
        db_slots.hold_link_booking_request("tokA", a, FUTURE)
        db_slots.hold_acquire("cardio", DIA, "10:00", "tokB")
        db_slots.hold_link_booking_request("tokB", b, FUTURE)
        db_slots.hold_acquire("cardio", DIA, "11:00", "tokC")        # loose
        self.assertIsNone(db_slots.holds_release_booking_request(a))
        self.assertEqual(sorted(h["token"] for h in _holds()), ["tokB", "tokC"])

    def test_release_of_none_or_unknown_deletes_nothing_even_with_loose_holds(self):
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")          # request_id IS NULL
        db_slots.holds_release_booking_request(None)
        db_slots.holds_release_booking_request(987654)
        self.assertEqual(len(_holds()), 1)

    def test_release_never_raises(self):
        for exc in (db.DbUnavailable("boom"), RuntimeError("boom")):
            with patch.object(db, "_connect", side_effect=exc):
                self.assertIsNone(db_slots.holds_release_booking_request(1))


class TestHoldOfBookingRequest(DbCase):
    def test_returns_dia_and_hora_whatever_the_expiry(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:05", "tokA")
        db_slots.hold_link_booking_request("tokA", sid, PAST)
        self.assertEqual(db_slots.hold_of_booking_request(sid), {"dia": DIA, "hora": "09:05"})

    def test_no_hold_is_none(self):
        sid = _req()
        db_slots.hold_acquire("cardio", DIA, "09:00", "tokA")           # loose, not linked
        self.assertIsNone(db_slots.hold_of_booking_request(sid))
        self.assertIsNone(db_slots.hold_of_booking_request(None))
        self.assertIsNone(db_slots.hold_of_booking_request(987654))

    def test_unavailable_database_is_none(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
            self.assertIsNone(db_slots.hold_of_booking_request(1))


# ---- the patient's request for an exact slot ------------------------------------------

class TestActiveBookingRequestBySlot(DbCase):
    def _with_hold(self, sid, hora="09:00", dia=DIA, servicio="clinica"):
        db_slots.hold_acquire(servicio, dia, hora, "tok%d" % sid)
        db_slots.hold_link_booking_request("tok%d" % sid, sid, FUTURE)

    def test_found_through_the_linked_hold(self):
        sid = _req()
        self._with_hold(sid)
        got = db_slots.active_booking_request_by_slot("30000001", "clinica", DIA, "09:00")
        self.assertEqual(got, {"id": sid, "estado": "pendiente"})

    def test_hora_without_leading_zero_matches_the_hold(self):
        sid = _req()
        self._with_hold(sid, "09:00")
        self.assertEqual(db_slots.active_booking_request_by_slot("30000001", "clinica", DIA, "9:00")["id"], sid)

    def test_found_through_the_appointment_wall_clock_in_the_installation_timezone(self):
        sid = _req(estado="aprobada", turno=_art(2031, 3, 4, 23, 30))   # 02:30 UTC of the 5th
        got = db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "23:30")
        self.assertEqual(got, {"id": sid, "estado": "aprobada"})
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-05", "02:30"))

    def test_the_appointment_branch_compares_the_text_as_given(self):
        _req(estado="aprobada", turno=_art(2031, 3, 4, 9, 0))
        # "9:00" is a valid slot for the hold branch but is not the text 09:00 of the appointment branch
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "9:00"))
        self.assertIsNotNone(db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "09:00"))

    def test_only_pending_and_approved_count(self):
        for estado in ("rechazada", "cancelada", "atendida"):
            _req(estado=estado, turno=_art(2031, 3, 4, 9, 0))
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "09:00"))

    def test_patient_and_service_must_match(self):
        _req(dni="30000001", servicio="clinica", estado="aprobada", turno=_art(2031, 3, 4, 9, 0))
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000002", "clinica", "2031-03-04", "09:00"))
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "otro", "2031-03-04", "09:00"))
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "09:20"))

    def test_the_most_recent_request_wins(self):
        old = _req(estado="aprobada", turno=_art(2031, 3, 4, 9, 0),
                   created=datetime(2031, 1, 1, tzinfo=UTC))
        new = _req(estado="pendiente", turno=_art(2031, 3, 4, 9, 0),
                   created=datetime(2031, 2, 1, tzinfo=UTC))
        got = db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "09:00")
        self.assertEqual(got["id"], new)
        self.assertNotEqual(got["id"], old)

    def test_a_request_matching_by_hold_and_by_clock_is_returned_once(self):
        sid = _req(estado="aprobada", turno=_art(2031, 3, 4, 9, 0))
        self._with_hold(sid, "09:00", "2031-03-04")
        self.assertEqual(db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "09:00")["id"], sid)

    def test_an_invalid_slot_finds_nothing_even_for_a_request_without_hold(self):
        # slot_key() gives None -> (None, None): the old `h.day = NULL` matched no one, while
        # `h.day IS NULL` would match every request of the patient that has no hold row.
        _req(estado="pendiente")                                  # no hold, no appointment
        _req(estado="pendiente", turno=_art(2031, 3, 4, 9, 0))    # no hold
        for dia, hora in (("basura", "09:00"), (DIA, "25:99"), (None, None), ("", ""), (DIA, None)):
            with self.subTest(dia=dia, hora=hora):
                self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "clinica", dia, hora))

    def test_an_invalid_slot_still_finds_a_request_by_the_clock_text_if_it_is_literally_equal(self):
        _req(estado="aprobada", turno=_art(2031, 3, 4, 9, 0))
        # day valid as text but the hour is not a real time: slot_key -> None, the clock branch compares text
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "clinica", "2031-03-04", "9h"))

    def test_none_patient_or_service_finds_nothing(self):
        _insert("booking_requests", service_id="clinica", status="pendiente",
                appointment_at=_art(2031, 3, 4, 9, 0))              # dni IS NULL
        _insert("booking_requests", dni="30000001", status="pendiente",
                appointment_at=_art(2031, 3, 4, 9, 0))              # service_id IS NULL
        self.assertIsNone(db_slots.active_booking_request_by_slot(None, "clinica", "2031-03-04", "09:00"))
        self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", None, "2031-03-04", "09:00"))

    def test_unavailable_database_is_none(self):
        with patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
            self.assertIsNone(db_slots.active_booking_request_by_slot("30000001", "clinica", DIA, "09:00"))

    def test_the_row_is_the_two_keys_of_the_select(self):
        sid = _req()
        self._with_hold(sid)
        self.assertEqual(list(db_slots.active_booking_request_by_slot("30000001", "clinica", DIA, "09:00")),
                         ["id", "estado"])


# ---- live requests and counters -------------------------------------------------------

class TestLiveRequestsOf(DbCase):
    def test_pending_with_or_without_time_and_approved_in_the_future(self):
        a = _req(estado="aprobada", turno=_art(2032, 5, 2, 9))
        b = _req(estado="pendiente", turno=_art(2032, 5, 1, 9))
        c = _req(estado="pendiente")
        d = _req(estado="aprobada", turno=_art(2032, 5, 1, 9))
        rows = db_slots.live_requests_of("30000001")
        self.assertEqual([r["id"] for r in rows], [b, d, a, c])     # time ascending, NULL last, then id

    def test_excludes_the_past_the_other_states_other_patients_and_approved_without_time(self):
        _req(estado="aprobada", turno=PAST)
        _req(estado="pendiente", turno=PAST)
        _req(estado="aprobada")                                     # approved, no time
        _req(estado="rechazada", turno=FUTURE)
        _req(estado="cancelada")
        _req(dni="30000002", estado="pendiente")
        self.assertEqual(db_slots.live_requests_of("30000001"), [])

    def test_a_requester_is_not_the_patient(self):
        _req(dni="30000009", estado="pendiente", requester_dni="30000001")
        self.assertEqual(db_slots.live_requests_of("30000001"), [])

    def test_rows_are_wire_dicts(self):
        _req(estado="pendiente", first_name="Ana", last_name="Gomez")
        (row,) = db_slots.live_requests_of("30000001")
        self.assertEqual(row["estado"], "pendiente")
        self.assertEqual(row["nombre"], "Ana")
        self.assertEqual(row["servicio_id"], "clinica")
        self.assertIn("turno_dt", row)
        self.assertNotIn("status", row)

    def test_none_finds_nothing_even_over_rows_without_dni(self):
        _insert("booking_requests", service_id="clinica", status="pendiente")
        self.assertEqual(db_slots.live_requests_of(None), [])


class TestCountAppointmentsOf(DbCase):
    def test_services_and_centers_count_every_state(self):
        for estado in ("pendiente", "aprobada", "rechazada", "cancelada"):
            _req(estado=estado, servicio="clinica", center_slug="hosp")
        _req(servicio="otro", center_slug="caps")
        self.assertEqual(db_slots.count_appointments_of("servicios", "clinica"), 4)
        self.assertEqual(db_slots.count_appointments_of("servicios", "otro"), 1)
        self.assertEqual(db_slots.count_appointments_of("servicios", "nada"), 0)
        self.assertEqual(db_slots.count_appointments_of("centros", "hosp"), 4)
        self.assertEqual(db_slots.count_appointments_of("centros", "caps"), 1)
        self.assertIs(type(db_slots.count_appointments_of("centros", "caps")), int)

    def test_other_kinds_are_zero(self):
        _req(servicio="clinica")
        self.assertEqual(db_slots.count_appointments_of("obras_sociales", "clinica"), 0)
        self.assertEqual(db_slots.count_appointments_of(None, "clinica"), 0)
        self.assertEqual(db_slots.count_appointments_of("", "clinica"), 0)

    def test_none_id_counts_nothing_even_over_rows_with_a_null_column(self):
        _insert("booking_requests", dni="1", status="pendiente")    # service_id and center_slug NULL
        self.assertEqual(db_slots.count_appointments_of("servicios", None), 0)
        self.assertEqual(db_slots.count_appointments_of("centros", None), 0)

    def test_a_doctor_is_counted_by_assigned_id_or_by_the_agenda_he_was_booked_in(self):
        _req(assigned_doctor_id="med-1")
        _req(slot_doctor_id="med-1")
        _req(assigned_doctor_id="med-1", slot_doctor_id="med-1")      # one row, one count
        _req(assigned_doctor_id="med-2", slot_doctor_id="med-1")
        _req(assigned_doctor_id="med-2")
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1"), 4)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-2"), 2)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-3"), 0)

    def test_the_name_counts_only_for_rows_without_an_id(self):
        _req(assigned_doctor="Nora Panel")                                       # old row: name only
        _req(assigned_doctor="  nora PANEL ")                                    # case + blanks
        _req(assigned_doctor="Nora Panel", assigned_doctor_id="med-otro")        # someone else's
        _req(assigned_doctor="Nora Panel", assigned_doctor_id="med-1")           # his, by id
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1", "Nora Panel"), 3)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1"), 1)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1", None), 1)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1", ""), 1)

    def test_a_blank_name_matches_blank_names_of_id_less_rows(self):
        # `%s <> ''` is true for a name of spaces and lower(btrim()) then equals the blank stored name
        _req(assigned_doctor="")
        _req(assigned_doctor=None)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1", "   "), 1)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1", ""), 0)

    def test_none_doctor_id_counts_only_by_name(self):
        _insert("booking_requests", dni="1", status="pendiente")    # every doctor column NULL
        _req(assigned_doctor="Nora Panel")
        self.assertEqual(db_slots.count_appointments_of("medicos", None), 0)
        self.assertEqual(db_slots.count_appointments_of("medicos", None, "nora panel"), 1)


class TestDoctorOtherReferences(DbCase):
    def test_zero_when_nobody_names_him(self):
        self.assertEqual(db_slots.doctor_other_references("med-1"), 0)

    def test_sums_every_table_of_doctor_id_columns_for_that_id_only(self):
        _insert("interviews", doctor_id="med-1", session_key="k1")
        _insert("interviews", doctor_id="med-1", session_key="k2")
        iv = _insert("interviews", doctor_id="med-2", session_key="k3")
        _insert("consultations", interview_id=iv["id"], doctor_id="med-1")
        _insert("patient_summaries", dni="1", doctor_id="med-1", resumen="{}")
        _insert("day_queue_events", appointment_day=date(2030, 1, 1), assigned_doctor_id="med-1")
        _insert("operators", username="nora", pass_hash="x", role="medico", doctor_id="med-1")
        _insert("day_queue_events", appointment_day=date(2030, 1, 1), assigned_doctor_id="med-2")
        got = db_slots.doctor_other_references("med-1")
        self.assertIs(type(got), int)
        self.assertEqual(got, 6)
        self.assertEqual(db_slots.doctor_other_references("med-2"), 2)

    def test_booking_requests_are_not_part_of_it(self):
        _req(assigned_doctor_id="med-1")
        self.assertEqual(db_slots.doctor_other_references("med-1"), 0)

    def test_none_counts_nothing_even_over_null_columns(self):
        _insert("interviews", session_key="k1")                       # doctor_id IS NULL
        _insert("day_queue_events", appointment_day=date(2030, 1, 1))  # assigned_doctor_id IS NULL
        _insert("operators", username="ana", pass_hash="x")            # doctor_id IS NULL
        self.assertEqual(db_slots.doctor_other_references(None), 0)

    def test_the_columns_are_the_published_constant(self):
        self.assertEqual(len(db_slots.DOCTOR_ID_COLUMNS), 5)


class TestDoctorUpcomingAppointments(DbCase):
    def test_counts_approved_future_and_pending_booked_in_his_agenda(self):
        _req(estado="aprobada", turno=FUTURE, assigned_doctor_id="med-1")
        _req(estado="aprobada", turno=FUTURE, slot_doctor_id="med-1")
        _req(estado="pendiente", turno=FUTURE, slot_doctor_id="med-1")
        self.assertEqual(db_slots.doctor_upcoming_appointments("med-1"), 3)

    def test_a_pending_that_only_has_him_assigned_does_not_count(self):
        _req(estado="pendiente", turno=FUTURE, assigned_doctor_id="med-1")
        _req(estado="pendiente", turno=FUTURE, assigned_doctor_id="med-1", slot_doctor_id="med-2")
        self.assertEqual(db_slots.doctor_upcoming_appointments("med-1"), 0)

    def test_past_unscheduled_and_closed_requests_do_not_count(self):
        _req(estado="aprobada", turno=PAST, assigned_doctor_id="med-1")
        _req(estado="aprobada", assigned_doctor_id="med-1")
        _req(estado="rechazada", turno=FUTURE, assigned_doctor_id="med-1")
        _req(estado="cancelada", turno=FUTURE, slot_doctor_id="med-1")
        _req(estado="aprobada", turno=FUTURE, assigned_doctor_id="med-2")
        self.assertEqual(db_slots.doctor_upcoming_appointments("med-1"), 0)

    def test_old_rows_are_found_by_name_only_when_they_have_no_id(self):
        _req(estado="aprobada", turno=FUTURE, assigned_doctor="Nora Panel")
        _req(estado="aprobada", turno=FUTURE, assigned_doctor="Nora Panel", assigned_doctor_id="med-2")
        self.assertEqual(db_slots.doctor_upcoming_appointments("med-1", "nora panel"), 1)
        self.assertEqual(db_slots.doctor_upcoming_appointments("med-1"), 0)
        self.assertEqual(db_slots.doctor_upcoming_appointments("med-1", None), 0)

    def test_a_pending_with_a_name_only_match_is_not_booked_in_his_agenda(self):
        _req(estado="pendiente", turno=FUTURE, assigned_doctor="Nora Panel")
        self.assertEqual(db_slots.doctor_upcoming_appointments("med-1", "Nora Panel"), 0)

    def test_none_id_finds_nothing_by_id_or_agenda(self):
        _insert("booking_requests", dni="1", status="aprobada", appointment_at=FUTURE)
        _insert("booking_requests", dni="1", status="pendiente", appointment_at=FUTURE)
        self.assertEqual(db_slots.doctor_upcoming_appointments(None), 0)


class TestCountAppointmentsOnDates(DbCase):
    def test_empty_input_is_an_empty_dict_without_touching_the_database(self):
        with patch.object(db, "_connect", side_effect=AssertionError("no query")):
            self.assertEqual(db_slots.count_appointments_on_dates([]), {})
            self.assertEqual(db_slots.count_appointments_on_dates(None), {})
            self.assertEqual(db_slots.count_appointments_on_dates(()), {})

    def test_counts_pending_and_approved_per_installation_day(self):
        _req(estado="pendiente", turno=_art(2032, 6, 1, 23, 30))      # 02:30 UTC of the 2nd
        _req(estado="aprobada", turno=_art(2032, 6, 1, 0, 10))
        _req(estado="aprobada", turno=_art(2032, 6, 2, 9, 0))
        _req(estado="aprobada", turno=_art(2032, 6, 3, 9, 0))
        got = db_slots.count_appointments_on_dates(["2032-06-01", "2032-06-02"])
        self.assertEqual(got, {"2032-06-01": 2, "2032-06-02": 1})
        self.assertTrue(all(type(v) is int for v in got.values()))

    def test_other_states_unscheduled_and_unlisted_days_are_left_out(self):
        _req(estado="rechazada", turno=_art(2032, 6, 1, 9))
        _req(estado="cancelada", turno=_art(2032, 6, 1, 9))
        _req(estado="atendida", turno=_art(2032, 6, 1, 9))
        _req(estado="pendiente")
        _req(estado="aprobada", turno=_art(2032, 6, 5, 9))
        self.assertEqual(db_slots.count_appointments_on_dates(["2032-06-01", "2032-06-02"]), {})

    def test_dates_may_be_text_or_date_objects(self):
        _req(estado="aprobada", turno=_art(2032, 6, 1, 9))
        self.assertEqual(db_slots.count_appointments_on_dates([date(2032, 6, 1)]), {"2032-06-01": 1})
        self.assertEqual(db_slots.count_appointments_on_dates(("2032-06-01",)), {"2032-06-01": 1})

    def test_the_day_boundaries_are_midnight_of_the_installation(self):
        _req(estado="aprobada", turno=_art(2032, 6, 1, 0, 0))
        _req(estado="aprobada", turno=_art(2032, 6, 1, 23, 59))
        _req(estado="aprobada", turno=_art(2032, 6, 2, 0, 0))
        self.assertEqual(db_slots.count_appointments_on_dates(["2032-06-01"]), {"2032-06-01": 2})


class TestCountActiveRequests(DbCase):
    def test_pending_and_approved_not_yet_past_count_for_that_patient_and_service(self):
        _req(estado="pendiente")
        _req(estado="pendiente", turno=PAST)                       # pending counts whatever its time
        _req(estado="aprobada", turno=FUTURE)
        _req(estado="aprobada")                                    # approved without time counts
        _req(estado="aprobada", turno=PAST)                        # already happened
        _req(estado="rechazada")
        _req(estado="cancelada")
        _req(estado="atendida")
        _req(dni="30000002")
        _req(servicio="otro")
        self.assertEqual(db_slots.count_active_requests("30000001", "clinica"), 4)
        self.assertIs(type(db_slots.count_active_requests("30000001", "clinica")), int)

    def test_estados_narrow_the_count(self):
        _req(estado="pendiente")
        _req(estado="aprobada", turno=FUTURE)
        self.assertEqual(db_slots.count_active_requests("30000001", "clinica", ("pendiente",)), 1)
        self.assertEqual(db_slots.count_active_requests("30000001", "clinica", ["aprobada"]), 1)
        self.assertEqual(db_slots.count_active_requests("30000001", "clinica", ("rechazada",)), 0)

    def test_estados_may_be_any_state_the_caller_asks_for(self):
        _req(estado="rechazada")
        self.assertEqual(db_slots.count_active_requests("30000001", "clinica", ("rechazada",)), 1)
        # the approved-in-the-past exclusion only applies to the approved state
        _req(estado="aprobada", turno=PAST)
        self.assertEqual(db_slots.count_active_requests("30000001", "clinica", ("rechazada", "aprobada")), 1)

    def test_empty_estados_is_zero_without_a_query(self):
        with patch.object(db, "_connect", side_effect=AssertionError("no query")):
            self.assertEqual(db_slots.count_active_requests("30000001", "clinica", ()), 0)
            self.assertEqual(db_slots.count_active_requests("30000001", "clinica", None), 0)
            self.assertEqual(db_slots.count_active_requests("30000001", "clinica", []), 0)

    def test_none_patient_or_service_counts_nothing(self):
        _insert("booking_requests", service_id="clinica", status="pendiente")
        _insert("booking_requests", dni="30000001", status="pendiente")
        self.assertEqual(db_slots.count_active_requests(None, "clinica"), 0)
        self.assertEqual(db_slots.count_active_requests("30000001", None), 0)

    def test_the_cursor_variant_runs_on_the_callers_transaction(self):
        _req(estado="pendiente")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO booking_requests (dni, service_id, status) "
                        "VALUES ('30000001', 'clinica', 'pendiente')")
            self.assertEqual(db_slots._count_active_requests_cur(cur, "30000001", "clinica",
                                                           ("pendiente", "aprobada")), 2)
            conn.rollback()
        self.assertEqual(db_slots.count_active_requests("30000001", "clinica"), 1)

    def test_the_cursor_variant_accepts_a_list_and_an_empty_one_matches_nothing(self):
        _req(estado="pendiente")
        with db._connect() as conn, conn.cursor() as cur:
            self.assertEqual(db_slots._count_active_requests_cur(cur, "30000001", "clinica", ["pendiente"]), 1)
            self.assertEqual(db_slots._count_active_requests_cur(cur, "30000001", "clinica", []), 0)
            self.assertEqual(db_slots._count_active_requests_cur(cur, "30000001", "clinica", iter(["pendiente"])), 1)


# ---- dependents ------------------------------------------------------------------------

class TestDependents(DbCase):
    def _dep(self, adult="30000001", dni="50000001", first="Luz", last="Gomez", born="2018-04-05",
             sex="F", rel="hija"):
        return db_slots.save_dependent(adult, dni, first, last, born, sex, rel)

    def setUp(self):
        super().setUp()
        _user("30000001", "Ana", "Perez")
        _user("30000002", "Beto", "Gomez")

    def test_save_inserts_and_returns_the_wire_row(self):
        before = _now()
        row = self._dep()
        self.assertEqual(sorted(row), sorted([
            "id", "dni", "nombre", "apellido", "nacimiento", "sexo", "vinculo",
            "declaracion_jurada_at", "created", "updated"]))
        self.assertEqual((row["dni"], row["nombre"], row["apellido"], row["sexo"], row["vinculo"]),
                         ("50000001", "Luz", "Gomez", "F", "hija"))
        self.assertEqual(row["nacimiento"], date(2018, 4, 5))
        self.assertGreaterEqual(row["declaracion_jurada_at"], before)
        self.assertEqual(_sql("SELECT count(*) AS n FROM dependents")[0]["n"], 1)

    def test_save_accepts_a_date_object_and_a_datetime_text(self):
        self.assertEqual(self._dep(dni="1", born=date(2019, 1, 2))["nacimiento"], date(2019, 1, 2))
        self.assertEqual(self._dep(dni="2", born="2019-03-04T10:00:00")["nacimiento"], date(2019, 3, 4))

    def test_save_rejects_an_unreadable_birth_date_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            self._dep(born="nunca")
        with self.assertRaises(psycopg.errors.NotNullViolation):
            self._dep(born=None)
        self.assertEqual(_sql("SELECT count(*) AS n FROM dependents")[0]["n"], 0)

    def test_save_again_updates_in_place_and_renews_the_affidavit(self):
        first = self._dep()
        _sql("UPDATE dependents SET affidavit_at = now() - interval '1 day', "
             "updated = now() - interval '1 day'")
        second = self._dep(first="Lucia", last="Gomez Perez", born="2018-04-06", sex="X", rel="sobrina")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual((second["nombre"], second["apellido"], second["nacimiento"], second["sexo"],
                          second["vinculo"]), ("Lucia", "Gomez Perez", date(2018, 4, 6), "X", "sobrina"))
        self.assertEqual(second["created"], first["created"])
        self.assertGreater(second["declaracion_jurada_at"], _now() - timedelta(minutes=1))
        self.assertGreater(second["updated"], _now() - timedelta(minutes=1))
        self.assertEqual(_sql("SELECT count(*) AS n FROM dependents")[0]["n"], 1)

    def test_the_same_child_can_depend_on_two_adults(self):
        self._dep(adult="30000001")
        self._dep(adult="30000002", rel="hija")
        self.assertEqual(_sql("SELECT count(*) AS n FROM dependents")[0]["n"], 2)

    def test_save_for_an_unknown_adult_violates_the_foreign_key(self):
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self._dep(adult="99999999")

    def test_list_is_per_adult_ordered_by_birth_desc_then_surname_then_name(self):
        self._dep(dni="1", first="Zoe", last="Bravo", born="2015-01-01")
        self._dep(dni="2", first="Ana", last="Bravo", born="2015-01-01")
        self._dep(dni="3", first="Ana", last="Alto", born="2015-01-01")
        self._dep(dni="4", first="Teo", last="Cano", born="2020-06-01")
        self._dep(adult="30000002", dni="5", first="Otro", last="Chico", born="2021-01-01")
        rows = db_slots.list_dependents("30000001")
        self.assertEqual([r["dni"] for r in rows], ["4", "3", "2", "1"])
        self.assertEqual(list(rows[0]), ["id", "dni", "nombre", "apellido", "nacimiento", "sexo",
                                         "vinculo", "declaracion_jurada_at", "created", "updated"])
        self.assertEqual(db_slots.list_dependents("30000009"), [])
        self.assertEqual(db_slots.list_dependents(None), [])

    def test_get_is_by_adult_and_dni(self):
        self._dep()
        row = db_slots.get_dependent("30000001", "50000001")
        self.assertEqual((row["nombre"], row["apellido"]), ("Luz", "Gomez"))
        self.assertEqual(list(row), ["id", "dni", "nombre", "apellido", "nacimiento", "sexo",
                                     "vinculo", "declaracion_jurada_at", "created", "updated"])
        self.assertIsNone(db_slots.get_dependent("30000002", "50000001"))
        self.assertIsNone(db_slots.get_dependent("30000001", "otro"))
        self.assertIsNone(db_slots.get_dependent(None, "50000001"))
        self.assertIsNone(db_slots.get_dependent("30000001", None))

    def test_delete_removes_only_that_pair_and_says_whether_it_did(self):
        self._dep(adult="30000001")
        self._dep(adult="30000002")
        self.assertIs(db_slots.delete_dependent("30000001", "50000001"), True)
        self.assertIs(db_slots.delete_dependent("30000001", "50000001"), False)
        self.assertIsNone(db_slots.get_dependent("30000001", "50000001"))
        self.assertIsNotNone(db_slots.get_dependent("30000002", "50000001"))
        self.assertIs(db_slots.delete_dependent(None, None), False)

    def test_adults_of_a_dependent_with_name_ordered_surname_nulls_last(self):
        _user("30000003", "Carla", None)
        _user("30000004", "Dani", "Alvarez")
        for adult, rel in (("30000001", "padre"), ("30000002", "tutor"), ("30000003", "madre"),
                           ("30000004", "abuelo")):
            self._dep(adult=adult, rel=rel)
        _sql("INSERT INTO users (dni) VALUES ('30000005')")
        self._dep(adult="30000005", rel="otro")
        rows = db_slots.adults_of_dependent("50000001")
        self.assertEqual([(r["dni"], r["nombre"], r["apellido"], r["vinculo"]) for r in rows], [
            ("30000004", "Dani", "Alvarez", "abuelo"),
            ("30000002", "Beto", "Gomez", "tutor"),
            ("30000001", "Ana", "Perez", "padre"),
            ("30000003", "Carla", None, "madre"),
            ("30000005", None, None, "otro"),
        ])
        self.assertEqual(list(rows[0]), ["dni", "nombre", "apellido", "vinculo"])
        self.assertEqual(db_slots.adults_of_dependent("sin-hijos"), [])
        self.assertEqual(db_slots.adults_of_dependent(None), [])


# ---- chat retention ---------------------------------------------------------------------

class TestPurgeSummarizedChatMessages(DbCase):
    OLD = datetime.now(UTC) - timedelta(days=200)
    COVER = datetime.now(UTC) - timedelta(days=150)

    def _msg(self, key, created, channel="web"):
        from psycopg.types.json import Jsonb
        return _insert("chat_messages", channel=channel, key=key, role="user",
                       content=Jsonb({"t": "hola"}), created=created)["id"]

    def _summary(self, key, covers):
        _insert("chat_summaries", patient_key=key, summary="s", covers_until=covers)

    def _compaction(self, key, covers):
        _insert("chat_compactions", patient_key=key, summary="s", covers_until=covers)

    def _ids(self):
        return [r["id"] for r in _sql("SELECT id FROM chat_messages ORDER BY id")]

    def test_deletes_old_messages_covered_by_a_summary_or_a_compaction_only(self):
        a = self._msg("dni:1", self.OLD)
        b = self._msg("dni:2", self.OLD)
        c = self._msg("dni:3", self.OLD)                       # no summary: never touched
        self._summary("dni:1", self.COVER)
        self._compaction("dni:2", self.COVER)
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 2)
        self.assertEqual(self._ids(), [c])
        self.assertNotIn(a, self._ids())
        self.assertNotIn(b, self._ids())

    def test_a_message_newer_than_the_window_is_kept(self):
        recent = datetime.now(UTC) - timedelta(days=10)
        keep = self._msg("dni:1", recent)
        self._summary("dni:1", datetime.now(UTC))
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 0)
        self.assertEqual(self._ids(), [keep])

    def test_the_window_is_in_days_and_configurable(self):
        keep = self._msg("dni:1", datetime.now(UTC) - timedelta(days=40))
        self._summary("dni:1", datetime.now(UTC))
        self.assertEqual(db_slots.purge_summarized_chat_messages(days=90), 0)
        self.assertEqual(db_slots.purge_summarized_chat_messages(days=30), 1)
        self.assertNotIn(keep, self._ids())

    def test_the_window_accepts_text(self):
        self._msg("dni:1", datetime.now(UTC) - timedelta(days=40))
        self._summary("dni:1", datetime.now(UTC))
        self.assertEqual(db_slots.purge_summarized_chat_messages(days="30"), 1)

    def test_a_message_after_what_the_summary_covers_is_kept(self):
        keep = self._msg("dni:1", self.OLD)
        self._summary("dni:1", self.OLD - timedelta(days=1))
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 0)
        self.assertEqual(self._ids(), [keep])

    def test_the_cover_boundary_is_inclusive(self):
        at = self.OLD
        self._msg("dni:1", at)
        self._summary("dni:1", at)
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 1)

    def test_a_summary_of_another_patient_key_covers_nothing(self):
        keep = self._msg("dni:1", self.OLD)
        self._summary("dni:2", self.COVER)
        self._compaction("web:abc", self.COVER)
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 0)
        self.assertEqual(self._ids(), [keep])

    def test_both_a_summary_and_a_compaction_delete_the_message_once(self):
        self._msg("dni:1", self.OLD)
        self._summary("dni:1", self.COVER)
        self._compaction("dni:1", self.COVER)
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 1)

    def test_the_limit_caps_one_pass(self):
        for _ in range(5):
            self._msg("dni:1", self.OLD)
        self._summary("dni:1", self.COVER)
        self.assertEqual(db_slots.purge_summarized_chat_messages(limit=2), 2)
        self.assertEqual(len(self._ids()), 3)
        self.assertEqual(db_slots.purge_summarized_chat_messages(limit="10"), 3)
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 0)

    def test_every_channel_is_purged(self):
        self._msg("dni:1", self.OLD, channel="whatsapp")
        self._msg("dni:1", self.OLD, channel="web")
        self._summary("dni:1", self.COVER)
        self.assertEqual(db_slots.purge_summarized_chat_messages(), 2)


# ---- interviews of a patient ----------------------------------------------------------

class TestInterviewsByDni(DbCase):
    def test_blank_input_is_empty_without_a_query(self):
        with patch.object(db, "_connect", side_effect=AssertionError("no query")):
            for value in (None, "", "   ", 0):
                self.assertEqual(db_slots.interviews_by_dni(value), [])

    def test_newest_first_with_the_table_columns_unrenamed(self):
        old = _insert("interviews", dni="30000001", session_key="a", doctor_id="d1",
                      created=datetime(2031, 1, 1, tzinfo=UTC))["id"]
        new = _insert("interviews", dni="30000001", session_key="b", doctor_id="d2",
                      created=datetime(2031, 2, 1, tzinfo=UTC))["id"]
        _insert("interviews", dni="30000002", session_key="c")
        _insert("interviews", session_key="d")                         # dni IS NULL
        rows = db_slots.interviews_by_dni("30000001")
        self.assertEqual([r["id"] for r in rows], [new, old])
        self.assertIn("doctor_id", rows[0])
        self.assertIn("session_key", rows[0])
        self.assertIn("created", rows[0])
        self.assertIsInstance(rows, list)

    def test_the_dni_is_stripped_and_coerced_to_text(self):
        _insert("interviews", dni="30000001", session_key="a")
        self.assertEqual(len(db_slots.interviews_by_dni("  30000001 ")), 1)
        self.assertEqual(len(db_slots.interviews_by_dni(30000001)), 1)
        self.assertEqual(db_slots.interviews_by_dni("30000009"), [])


# ---- approved requests: calendar groups and the day queue -------------------------------

class TestApprovedCalendarGroups(DbCase):
    def test_empty(self):
        self.assertEqual(db_slots.approved_calendar_groups(), [])

    def test_groups_by_installation_day_doctor_and_center_ordered_by_day(self):
        _req(estado="aprobada", turno=_art(2032, 6, 2, 9), assigned_doctor="Doc A", assigned_doctor_id="a",
             center_slug="hosp")
        _req(estado="aprobada", turno=_art(2032, 6, 2, 10), assigned_doctor="Doc A", assigned_doctor_id="a",
             center_slug="hosp")
        _req(estado="aprobada", turno=_art(2032, 6, 1, 23, 30), assigned_doctor="Doc A",
             assigned_doctor_id="a", center_slug="hosp")                 # 02:30 UTC of the 2nd
        _req(estado="aprobada", turno=_art(2032, 6, 1, 8), assigned_doctor="Doc B", assigned_doctor_id="b",
             center_slug="caps")
        _req(estado="aprobada", turno=_art(2032, 6, 1, 9))               # no doctor, no center
        got = db_slots.approved_calendar_groups()
        self.assertEqual([g["dia"] for g in got], sorted(g["dia"] for g in got))
        self.assertEqual(sorted((g["dia"], g["medico_asignado"], g["medico_asignado_id"],
                                 g["centro_slug"], g["cantidad"]) for g in got
                                if g["medico_asignado"] is not None),
                         [("2032-06-01", "Doc A", "a", "hosp", 1),
                          ("2032-06-01", "Doc B", "b", "caps", 1),
                          ("2032-06-02", "Doc A", "a", "hosp", 2)])
        blank = [g for g in got if g["medico_asignado"] is None]
        self.assertEqual([(g["dia"], g["medico_asignado_id"], g["centro_slug"], g["cantidad"]) for g in blank],
                         [("2032-06-01", None, None, 1)])

    def test_keys_and_types(self):
        _req(estado="aprobada", turno=_art(2032, 6, 1, 9), assigned_doctor="Doc A")
        (g,) = db_slots.approved_calendar_groups()
        self.assertEqual(list(g), ["dia", "medico_asignado", "medico_asignado_id", "centro_slug", "cantidad"])
        self.assertIsInstance(g["dia"], str)
        self.assertEqual(g["cantidad"], 1)

    def test_other_states_and_unscheduled_rows_are_left_out_past_ones_stay(self):
        _req(estado="pendiente", turno=_art(2032, 6, 1, 9))
        _req(estado="rechazada", turno=_art(2032, 6, 1, 9))
        _req(estado="cancelada", turno=_art(2032, 6, 1, 9))
        _req(estado="aprobada")
        _req(estado="aprobada", turno=_art(2020, 1, 1, 9))
        got = db_slots.approved_calendar_groups()
        self.assertEqual([(g["dia"], g["cantidad"]) for g in got], [("2020-01-01", 1)])


class TestListApprovedDay(DbCase):
    def test_the_whole_installation_day_in_time_order_past_and_future(self):
        late = _req(dni="2", estado="aprobada", turno=_art(2032, 6, 1, 23, 59))
        early = _req(dni="3", estado="aprobada", turno=_art(2032, 6, 1, 0, 0))
        mid = _req(dni="4", estado="aprobada", turno=_art(2032, 6, 1, 9, 30))
        _req(dni="5", estado="aprobada", turno=_art(2032, 5, 31, 23, 59))
        _req(dni="6", estado="aprobada", turno=_art(2032, 6, 2, 0, 0))
        rows = db_slots.list_approved_day("2032-06-01")
        self.assertEqual([r["id"] for r in rows], [early, mid, late])

    def test_only_approved_with_a_time(self):
        _req(estado="pendiente", turno=_art(2032, 6, 1, 9))
        _req(estado="rechazada", turno=_art(2032, 6, 1, 9))
        _req(estado="cancelada", turno=_art(2032, 6, 1, 9))
        _req(estado="aprobada")
        self.assertEqual(db_slots.list_approved_day("2032-06-01"), [])

    def test_the_wire_row_carries_the_users_coverage(self):
        _user("30000001", "Ana", "Perez", coverage={"obra": "PAMI", "afiliado": "123"})
        sid = _req(estado="aprobada", turno=_art(2032, 6, 1, 9), first_name="Ana", assigned_doctor="Doc A")
        _req(dni="30000099", estado="aprobada", turno=_art(2032, 6, 1, 10))          # no account
        mine, ghost = db_slots.list_approved_day("2032-06-01")
        self.assertEqual(mine["id"], sid)
        self.assertEqual(mine["cobertura_usuario"], {"obra": "PAMI", "afiliado": "123"})
        self.assertEqual(mine["estado"], "aprobada")
        self.assertEqual(mine["nombre"], "Ana")
        self.assertEqual(mine["medico_asignado"], "Doc A")
        self.assertEqual(mine["turno_dt"], _art(2032, 6, 1, 9))
        self.assertIs(mine["sobreturno"], False)
        self.assertIsNone(ghost["cobertura_usuario"])
        self.assertNotIn("status", mine)
        self.assertNotIn("coverage", mine)

    def test_a_user_with_null_coverage_gives_none_and_one_row_per_request(self):
        _user("30000001")
        _req(estado="aprobada", turno=_art(2032, 6, 1, 9))
        _req(estado="aprobada", turno=_art(2032, 6, 1, 10))
        rows = db_slots.list_approved_day("2032-06-01")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["cobertura_usuario"] for r in rows], [None, None])

    def test_every_booking_column_is_in_the_row(self):
        _req(estado="aprobada", turno=_art(2032, 6, 1, 9))
        (row,) = db_slots.list_approved_day("2032-06-01")
        cols = {c["column_name"] for c in _sql(
            "SELECT column_name FROM information_schema.columns WHERE table_name='booking_requests'")}
        hidden = db_wire._WIRE_HIDDEN["booking_requests"]   # the case chat: a logged read of its own
        wired = {db_wire._WIRE_BACK["booking_requests"].get(c, c) for c in cols if c not in hidden}
        self.assertEqual(set(row) - {"cobertura_usuario"}, wired)
        self.assertNotIn("case_chat", row)

    def test_on_one_minute_the_ordinary_appointment_comes_before_the_overbooking(self):
        extra = _req(dni="2", estado="aprobada", turno=_art(2032, 6, 1, 10), overbooking=True)
        first = _req(dni="3", estado="aprobada", turno=_art(2032, 6, 1, 10))
        later = _req(dni="4", estado="aprobada", turno=_art(2032, 6, 1, 10))
        _sql("UPDATE booking_requests SET dni = dni WHERE id = %s", (first,))   # an UPDATE moves the row on disk
        rows = db_slots.list_approved_day("2032-06-01")
        self.assertEqual([r["id"] for r in rows], [first, later, extra])

    def test_an_unreadable_day_is_a_database_error(self):
        with self.assertRaises(psycopg.Error):
            db_slots.list_approved_day("no-es-un-dia")

    def test_a_day_without_requests_is_empty(self):
        self.assertEqual(db_slots.list_approved_day("2032-06-01"), [])

    def test_none_is_an_empty_day(self):
        _req(estado="aprobada", turno=_art(2032, 6, 1, 9))
        self.assertEqual(db_slots.list_approved_day(None), [])


# ---- idempotency key --------------------------------------------------------------------

class TestGetBookingRequestByIdemKey(DbCase):
    def test_blank_keys_are_none_without_a_query(self):
        with patch.object(db, "_connect", side_effect=AssertionError("no query")):
            for key in (None, "", "   "):
                self.assertIsNone(db_slots.get_booking_request_by_idem_key(key))

    def test_found_as_the_wire_row_and_stripped(self):
        sid = _req(idem_key="abc-123", first_name="Ana")
        _req(idem_key="other")
        _req()                                                  # idem_key NULL
        for key in ("abc-123", "  abc-123\n"):
            row = db_slots.get_booking_request_by_idem_key(key)
            self.assertEqual(row["id"], sid)
            self.assertEqual(row["nombre"], "Ana")
            self.assertEqual(row["estado"], "pendiente")
            self.assertEqual(row["idem_key"], "abc-123")

    def test_unknown_key_is_none(self):
        _req(idem_key="abc")
        self.assertIsNone(db_slots.get_booking_request_by_idem_key("abd"))
        self.assertIsNone(db_slots.get_booking_request_by_idem_key("ABC"))


# ---- DbUnavailable propagates where nothing swallows it ----------------------------------

class TestUnavailableDatabaseContract(unittest.TestCase):
    SWALLOWED = {
        "hold_acquire": (("cardio", DIA, "09:00", "t"), False),
        "hold_release": (("t",), None),
        "holds_active": ((), []),
        "hold_link_booking_request": (("t", 1, FUTURE), None),
        "holds_release_booking_request": ((1,), None),
        "hold_of_booking_request": ((1,), None),
        "active_booking_request_by_slot": (("1", "s", DIA, "09:00"), None),
    }
    PROPAGATED = {
        "day_queue_event_high_water": (),
        "day_queue_events_after": (0,),
        "waitlist_get": (1,),
        "waitlist_active": (1,),
        "live_requests_of": ("1",),
        "count_appointments_of": ("servicios", "x"),
        "doctor_other_references": ("x",),
        "doctor_upcoming_appointments": ("x",),
        "count_appointments_on_dates": (["2032-06-01"],),
        "count_active_requests": ("1", "s"),
        "list_dependents": ("1",),
        "get_dependent": ("1", "2"),
        "adults_of_dependent": ("1",),
        "save_dependent": ("1", "2", "A", "B", "2020-01-01", "F", "hijo"),
        "delete_dependent": ("1", "2"),
        "purge_summarized_chat_messages": (),
        "interviews_by_dni": ("1",),
        "approved_calendar_groups": (),
        "list_approved_day": ("2032-06-01",),
        "get_booking_request_by_idem_key": ("k",),
    }

    def test_the_best_effort_functions_degrade_without_raising(self):
        for name, (args, expected) in self.SWALLOWED.items():
            with self.subTest(name), patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
                self.assertEqual(getattr(db_slots, name)(*args), expected)

    def test_the_rest_let_the_error_through(self):
        for name, args in self.PROPAGATED.items():
            with self.subTest(name), patch.object(db, "_connect", side_effect=db.DbUnavailable("boom")):
                with self.assertRaises(db.DbUnavailable):
                    getattr(db_slots, name)(*args)

    def test_names_are_the_stage_6_slots_surface(self):
        for name in list(self.SWALLOWED) + list(self.PROPAGATED):
            self.assertTrue(callable(getattr(db_slots, name)), name)


if __name__ == "__main__":
    unittest.main()
