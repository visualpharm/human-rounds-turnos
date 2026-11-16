"""test_waitlist_store.py — `modules/turnos/waitlist_store.py` against a real Postgres.

The adapter is the only place that holds SQL for the `waitlist` and
`appointment_offers` tables. It is written with SQLAlchemy Core
(`core/schema_model.py` + `core/sqlcore.py`) — the first module converted from
hand-written SQL — and this file pins each of its functions row for row: the
wired (Spanish-key) dicts it returns, the branch each state takes, the ordering,
the idempotence. The orchestrators' suites (`test_waitlist`, `test_waitlist_queue`,
`test_integrity_races`, `test_business_queue_proof`) exercise it indirectly; this
is the direct, per-function proof that the conversion changed nothing.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.modules.turnos import waitlist_store as store  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_waitlist_store")

UTC = timezone.utc
SLOT = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
LATER = SLOT + timedelta(days=2)
EARLIER = SLOT - timedelta(days=1)
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class StoreCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            # the database refuses a waiting-list entry whose request does not exist
            # (foreign key fk_waitlist_request): clear children first, then give
            # requests 1..8 their bare parent rows
            _dbtest.delete_booking_requests(cur)
            for request_id in range(1, 9):
                cur.execute("INSERT INTO booking_requests (id) VALUES (%s)", (request_id,))

    def opt_in(self, request_id, when=LATER, service="kine", center="hospital", dni=None):
        return store.opt_in(request_id, dni or "dni%d" % request_id, "p%d@x.com" % request_id,
                            service, center, when)

    def offer(self, waitlist_id, token, slot=SLOT, service="kine", center="hospital",
              expires=NOW + timedelta(minutes=5)):
        return store.create_offer(slot, service, center, waitlist_id, token, NOW, expires)


class TestWaitlist(StoreCase):
    def test_opt_in_inserts_and_returns_the_new_id(self):
        wid = self.opt_in(1)
        self.assertIsInstance(wid, int)
        row = store.waitlist_row(wid)
        self.assertEqual(
            {k: row[k] for k in ("id", "solicitud_id", "dni", "email", "servicio_id",
                                 "centro_slug", "turno_dt", "activo")},
            {"id": wid, "solicitud_id": 1, "dni": "dni1", "email": "p1@x.com",
             "servicio_id": "kine", "centro_slug": "hospital", "turno_dt": LATER, "activo": True})
        self.assertIsNotNone(row["created"])
        self.assertEqual(
            sorted(row),
            sorted(["id", "solicitud_id", "dni", "email", "servicio_id", "centro_slug",
                    "turno_dt", "activo", "created"]))

    def test_opt_in_is_idempotent_per_active_request(self):
        first = self.opt_in(1)
        self.assertEqual(first, self.opt_in(1))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM waitlist")
            self.assertEqual(1, cur.fetchone()["n"])

    def test_opt_in_keeps_the_instant_of_a_non_utc_datetime(self):
        art = timezone(timedelta(hours=-3))
        wid = store.opt_in(2, "dni2", "p2@x.com", "kine", "hospital",
                           datetime(2026, 11, 5, 10, 0, tzinfo=art))
        self.assertEqual(datetime(2026, 11, 5, 13, 0, tzinfo=UTC), store.waitlist_row(wid)["turno_dt"])

    def test_opt_out_deactivates_and_a_new_opt_in_is_a_new_row(self):
        first = self.opt_in(1)
        store.opt_out(1)
        self.assertFalse(store.waitlist_row(first)["activo"])
        second = self.opt_in(1)
        self.assertNotEqual(first, second)
        self.assertTrue(store.waitlist_row(second)["activo"])

    def test_opt_out_of_an_unknown_request_is_a_no_op(self):
        self.assertIsNone(store.opt_out(999))

    def test_waitlist_row_of_an_unknown_id_is_none(self):
        self.assertIsNone(store.waitlist_row(123456))

    def test_queue_for_slot_is_fifo_active_later_and_same_service_center(self):
        a = self.opt_in(1)
        b = self.opt_in(2)
        self.opt_in(3, when=EARLIER)                      # its own turn is earlier: no improvement
        self.opt_in(4, when=SLOT)                         # same instant: not later
        self.opt_in(5, service="otra")                    # other service
        self.opt_in(6, center="caps")                     # other center
        gone = self.opt_in(7)
        store.opt_out(7)                                  # inactive
        c = self.opt_in(8, when=LATER + timedelta(days=1))
        queue = store.queue_for_slot("kine", "hospital", SLOT, exclude_ids=set())
        self.assertEqual([a, b, c], [r["id"] for r in queue])
        self.assertNotIn(gone, [r["id"] for r in queue])
        self.assertEqual({1, 2, 8}, {r["solicitud_id"] for r in queue})

    def test_queue_for_slot_skips_the_ids_already_offered(self):
        a, b = self.opt_in(1), self.opt_in(2)
        queue = store.queue_for_slot("kine", "hospital", SLOT, exclude_ids={a})
        self.assertEqual([b], [r["id"] for r in queue])
        self.assertEqual([], store.queue_for_slot("kine", "hospital", SLOT, exclude_ids={a, b}))

    def test_queue_for_slot_compares_instants_not_wall_clocks(self):
        art = timezone(timedelta(hours=-3))
        a = self.opt_in(1, when=datetime(2026, 11, 3, 18, 30, tzinfo=UTC))
        # 12:00 -03:00 is 15:00 UTC: the candidate's 18:30 UTC turn is later
        queue = store.queue_for_slot("kine", "hospital", datetime(2026, 11, 3, 12, 0, tzinfo=art),
                                     exclude_ids=set())
        self.assertEqual([a], [r["id"] for r in queue])
        # 17:00 -03:00 is 20:00 UTC: after the candidate's turn
        self.assertEqual([], store.queue_for_slot(
            "kine", "hospital", datetime(2026, 11, 3, 17, 0, tzinfo=art), exclude_ids=set()))


class TestOffers(StoreCase):
    def test_create_offer_returns_the_id_and_a_live_duplicate_returns_none(self):
        w = self.opt_in(1)
        first = self.offer(w, "tok-1")
        self.assertIsInstance(first, int)
        self.assertIsNone(self.offer(w, "tok-2"))        # same slot, still 'ofrecida'

    def test_offer_by_token_wires_the_row(self):
        w = self.opt_in(1)
        oid = self.offer(w, "tok-1")
        o = store.offer_by_token("tok-1")
        self.assertEqual(
            {k: o[k] for k in ("id", "slot_dt", "servicio_id", "centro_slug", "lista_espera_id",
                               "token", "estado", "sent_at", "expires_at")},
            {"id": oid, "slot_dt": SLOT, "servicio_id": "kine", "centro_slug": "hospital",
             "lista_espera_id": w, "token": "tok-1", "estado": "ofrecida", "sent_at": NOW,
             "expires_at": NOW + timedelta(minutes=5)})
        self.assertIsNone(store.offer_by_token("nope"))

    def test_offers_for_slot_are_oldest_first_and_scoped(self):
        w1, w2 = self.opt_in(1), self.opt_in(2)
        o1 = self.offer(w1, "t1")
        store.expire_offer(o1)
        o2 = self.offer(w2, "t2")
        other = self.offer(w1, "t3", slot=SLOT + timedelta(hours=1))
        self.offer(w1, "t4", service="otra")
        rows = store.offers_for_slot("kine", "hospital", SLOT)
        self.assertEqual([o1, o2], [r["id"] for r in rows])
        self.assertEqual(["expirada", "ofrecida"], [r["estado"] for r in rows])
        self.assertEqual([other], [r["id"] for r in store.offers_for_slot(
            "kine", "hospital", SLOT + timedelta(hours=1))])
        self.assertEqual([], store.offers_for_slot("kine", "caps", SLOT))

    def test_live_offer_slots_are_distinct_and_only_ofrecida(self):
        w = self.opt_in(1)
        o = self.offer(w, "t1")
        self.offer(w, "t2", slot=SLOT + timedelta(hours=1), service="otra")
        gone = self.offer(w, "t3", slot=SLOT + timedelta(hours=2))
        store.expire_offer(gone)
        slots = store.live_offer_slots()
        self.assertEqual(
            sorted([(SLOT, "kine", "hospital"), (SLOT + timedelta(hours=1), "otra", "hospital")]),
            sorted((s["slot_dt"], s["servicio_id"], s["centro_slug"]) for s in slots))
        for s in slots:
            self.assertEqual({"slot_dt", "servicio_id", "centro_slug"}, set(s))
        self.assertIsInstance(o, int)

    def test_has_live_offer_counts_ofrecida_and_aceptada_only(self):
        w = self.opt_in(1)
        self.assertFalse(store.has_live_offer("kine", "hospital", SLOT))
        o = self.offer(w, "t1")
        self.assertTrue(store.has_live_offer("kine", "hospital", SLOT))
        store.accept_offer("t1", NOW)
        self.assertTrue(store.has_live_offer("kine", "hospital", SLOT))
        store.expire_offer(o)
        self.assertFalse(store.has_live_offer("kine", "hospital", SLOT))
        self.assertFalse(store.has_live_offer("kine", "caps", SLOT))

    def test_expire_offer_marks_only_that_offer(self):
        w1, w2 = self.opt_in(1), self.opt_in(2)
        o1 = self.offer(w1, "t1")
        o2 = self.offer(w2, "t2", slot=SLOT + timedelta(hours=1))
        store.expire_offer(o1)
        self.assertEqual("expirada", store.offer_by_token("t1")["estado"])
        self.assertEqual("ofrecida", store.offer_by_token("t2")["estado"])
        self.assertIsInstance(o2, int)

    def test_mark_slot_vacant_touches_only_live_offers_of_that_slot(self):
        w = self.opt_in(1)
        self.offer(w, "t1")
        gone = self.offer(w, "t2", slot=SLOT + timedelta(hours=1))
        store.expire_offer(gone)
        elsewhere = self.offer(w, "t3", slot=SLOT + timedelta(hours=2))
        store.mark_slot_vacant("kine", "hospital", SLOT)
        store.mark_slot_vacant("kine", "hospital", SLOT + timedelta(hours=1))
        self.assertEqual("vacante", store.offer_by_token("t1")["estado"])
        self.assertEqual("expirada", store.offer_by_token("t2")["estado"])   # not reopened as vacante
        self.assertEqual("ofrecida", store.offer_by_token("t3")["estado"])
        self.assertIsInstance(elsewhere, int)

    def test_expire_if_live_branches(self):
        w = self.opt_in(1)
        self.assertEqual((None, False), store.expire_if_live("nope"))
        self.offer(w, "t1")
        o, was_live = store.expire_if_live("t1")
        self.assertTrue(was_live)
        self.assertEqual("expirada", o["estado"])
        self.assertEqual("expirada", store.offer_by_token("t1")["estado"])
        o, was_live = store.expire_if_live("t1")                              # already resolved
        self.assertFalse(was_live)
        self.assertEqual("expirada", o["estado"])

    def test_expire_if_live_leaves_an_accepted_offer_alone(self):
        w = self.opt_in(1)
        self.offer(w, "t1")
        store.accept_offer("t1", NOW)
        o, was_live = store.expire_if_live("t1")
        self.assertFalse(was_live)
        self.assertEqual("aceptada", o["estado"])
        self.assertEqual("aceptada", store.offer_by_token("t1")["estado"])

    def test_accept_offer_branches(self):
        w = self.opt_in(1)
        self.assertEqual((False, "no_existe"), store.accept_offer("nope", NOW))
        self.offer(w, "t1", expires=NOW + timedelta(minutes=5))
        ok, o = store.accept_offer("t1", NOW)
        self.assertTrue(ok)
        self.assertEqual("aceptada", o["estado"])
        self.assertEqual("aceptada", store.offer_by_token("t1")["estado"])
        self.assertFalse(store.waitlist_row(w)["activo"])                     # candidate leaves the queue
        ok, again = store.accept_offer("t1", NOW + timedelta(hours=1))        # idempotent
        self.assertTrue(ok)
        self.assertEqual("aceptada", again["estado"])

    def test_accept_offer_after_the_window_is_vencida_and_changes_nothing(self):
        w = self.opt_in(1)
        self.offer(w, "t1", expires=NOW + timedelta(minutes=5))
        self.assertEqual((False, "vencida"), store.accept_offer("t1", NOW + timedelta(minutes=5)))
        self.assertEqual("ofrecida", store.offer_by_token("t1")["estado"])
        self.assertTrue(store.waitlist_row(w)["activo"])

    def test_accept_offer_of_a_resolved_offer_is_vencida(self):
        w = self.opt_in(1)
        o = self.offer(w, "t1")
        store.expire_offer(o)
        self.assertEqual((False, "vencida"), store.accept_offer("t1", NOW))
        self.assertTrue(store.waitlist_row(w)["activo"])


if __name__ == "__main__":
    unittest.main()
