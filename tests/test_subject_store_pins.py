"""Pins for the statements of core/subject_requests.py, core/subject_export.py and
core/subject_erase.py that the suites of those modules do not reach on their own:
the ledger's lists and their order, `erased_subject_hashes` and what it leaves out,
how `resolve_subject` reads the account's phone and email, and the edges of the
erasure blockers (what does NOT block). Written green on the hand-written SQL
before the modules moved to SQLAlchemy Core, and kept as the proof that the move
changed nothing.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import access_log, db, personal_data, subject_erase, subject_export, subject_requests as sr  # noqa: E402
from app.core import db_catalog  # noqa: E402
import _dbtest  # noqa: E402
import _subject_fixture  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_subject_store_pins")
P, Q, R = "20111222", "30222333", "27444555"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        db_catalog.catalog_list("centros")
        self.fx = _subject_fixture.Fixture(_db.test_url, self)
        self.fx.reset()
        self.fx.execute("TRUNCATE data_subject_requests, data_subject_request_events RESTART IDENTITY")


class TestLedgerLists(DbCase):
    def _open(self, kind, dni, on, channel="presencial"):
        return sr.register(kind, dni, "mesa", received_on=on, channel=channel, holidays=())

    def test_an_unknown_request_is_an_error_that_names_it(self):
        with self.assertRaises(sr.RequestError) as ctx:
            sr.get(999)
        self.assertEqual(str(ctx.exception), "no request 999")

    def test_get_returns_the_row_and_its_trail_in_order(self):
        row = self._open("rectification", P, date(2026, 10, 5))
        sr.set_identity_check(row["id"], "DNI en mano", "mesa")
        sr.start_review(row["id"], "mesa", "se revisa")
        sr.note(row["id"], "una nota", "mesa")
        got = sr.get(row["id"])
        self.assertEqual((got["kind"], got["status"], got["identity_check"]),
                         ("rectification", "under_review", "DNI en mano"))
        self.assertEqual([e["event"] for e in got["events"]],
                         ["received", "identity_verified", "under_review", "note"])
        self.assertEqual(set(got["events"][0]), {"ts", "actor", "event", "detail"})
        self.assertEqual(got["events"][2]["detail"], "se revisa")

    def test_register_keeps_the_channel_and_a_missing_one_is_empty(self):
        a = self._open("access", P, date(2026, 10, 1), channel="")
        self.assertEqual(a["channel"], "")
        self.assertEqual(a["identity_check"], "")
        self.assertIsNone(a["answered_on"])
        self.assertIsNone(a["decided_by"])
        self.assertIn("unspecified", sr.get(a["id"])["events"][0]["detail"])

    def test_list_open_is_by_due_day_then_id_and_leaves_the_closed_out(self):
        late = self._open("access", P, date(2026, 9, 1))
        same_a = self._open("access", Q, date(2026, 10, 1))
        same_b = self._open("access", R, date(2026, 10, 1))
        closed = self._open("access", "30999888", date(2026, 8, 1))
        sr.refuse(closed["id"], "no identity", "mesa")
        done = self._open("access", "30999777", date(2026, 8, 2))
        sr.set_identity_check(done["id"], "DNI", "mesa")
        sr.answer(done["id"], "enviado", "mesa")
        under = self._open("erasure", "30999666", date(2026, 10, 1))
        sr.start_review(under["id"], "mesa")
        ids = [r["id"] for r in sr.list_open()]
        # the erasure is due in 5 working days (Oct 8), the accesses in 10 calendar days (Oct 11)
        self.assertEqual(ids, [late["id"], under["id"], same_a["id"], same_b["id"]])
        self.assertEqual([r["id"] for r in sr.list_open(2)], ids[:2])
        self.assertEqual(sr.list_open(0), [])

    def test_overdue_is_strictly_past_the_due_day(self):
        row = self._open("access", P, date(2026, 10, 1))   # due 2026-10-11
        self.assertEqual(sr.overdue(date(2026, 10, 11)), [])
        self.assertEqual([r["id"] for r in sr.overdue(date(2026, 10, 12))], [row["id"]])

    def test_for_subject_is_newest_received_first_then_newest_id(self):
        old = self._open("access", P, date(2026, 9, 1))
        new_a = self._open("access", P, date(2026, 10, 1))
        new_b = self._open("rectification", P, date(2026, 10, 1))
        self._open("access", Q, date(2026, 10, 2))
        self.assertEqual([r["id"] for r in sr.for_subject(P)], [new_b["id"], new_a["id"], old["id"]])
        self.assertEqual(sr.for_subject("11111111"), [])

    def test_next_free_access_counts_only_answered_access_requests(self):
        self.assertIsNone(sr.next_free_access_on(P))
        refused = self._open("access", P, date(2026, 5, 1))
        sr.refuse(refused["id"], "identity not verified", "mesa", on=date(2026, 5, 2))
        erasure = self._open("erasure", P, date(2026, 6, 1))
        sr.set_identity_check(erasure["id"], "DNI", "mesa")
        sr.answer(erasure["id"], "ok", "mesa", on=date(2026, 6, 2))
        self.assertIsNone(sr.next_free_access_on(P))
        for on, answered in ((date(2026, 3, 1), date(2026, 3, 5)), (date(2026, 7, 1), date(2026, 7, 9))):
            row = self._open("access", P, on)
            sr.set_identity_check(row["id"], "DNI", "mesa")
            sr.answer(row["id"], "enviado", "mesa", on=answered)
        self.assertEqual(sr.next_free_access_on(P), date(2027, 1, 9))
        self.assertIsNone(sr.next_free_access_on(Q))

    def test_under_review_is_for_rectification_and_erasure_of_that_person_only(self):
        erasure = self._open("erasure", P, date(2026, 10, 1))
        sr.start_review(erasure["id"], "mesa")
        self.assertTrue(sr.is_under_review(P))
        self.assertFalse(sr.is_under_review(Q))
        # an answered one is no longer under review
        sr.set_identity_check(erasure["id"], "DNI", "mesa")
        sr.answer(erasure["id"], "hecho", "mesa")
        self.assertFalse(sr.is_under_review(P))

    def test_erased_subject_hashes_are_distinct_sorted_and_only_for_carried_out_erasures(self):
        def erasure(dni, erased, kind="erasure"):
            rid = self.fx.insert("data_subject_requests", kind=kind, subject_hash=access_log.dni_hash(dni),
                                 received_on="2026-10-01", due_on="2026-10-08", registered_by="mesa")
            self.fx.insert("data_subject_request_events", request_id=rid, actor="mesa",
                           event="erased" if erased else "received")
            return rid
        erasure(P, True)
        erasure(P, True)                # a second carried-out erasure of the same person: one hash
        erasure(Q, True)
        erasure(R, False)               # registered, never carried out
        erasure("30999111", True, kind="access")   # an 'erased' event on another kind of request
        self.assertEqual(sr.erased_subject_hashes(),
                         sorted({access_log.dni_hash(P), access_log.dni_hash(Q)}))
        self.assertEqual(sr.erased_subject_hashes(), sorted(sr.erased_subject_hashes()))

    def test_record_export_and_append_event_write_into_the_trail(self):
        row = self._open("access", P, date(2026, 10, 1))
        sr.record_export(row["id"], "mesa", "12 rows")
        with db._connect() as conn, conn.cursor() as cur:
            sr.append_event(cur, row["id"], "worker", "note", "")
        events = sr.get(row["id"])["events"]
        self.assertEqual([(e["actor"], e["event"], e["detail"]) for e in events][1:],
                         [("mesa", "export_generated", "12 rows"), ("worker", "note", "")])
        with self.assertRaises(sr.RequestError):
            sr.record_export(999, "mesa")

    def test_check_erasure_request_locks_and_validates(self):
        row = self._open("erasure", P, date(2026, 10, 1))
        sr.set_identity_check(row["id"], "DNI", "mesa")
        h = access_log.dni_hash(P)
        with db._connect() as conn, conn.cursor() as cur:
            self.assertEqual(sr.check_erasure_request(cur, row["id"], h)["id"], row["id"])
            with self.assertRaises(sr.RequestError):
                sr.check_erasure_request(cur, row["id"], access_log.dni_hash(Q))
            sr.append_event(cur, row["id"], "mesa", "erased", "{}")
            with self.assertRaises(sr.RequestError) as ctx:
                sr.check_erasure_request(cur, row["id"], h)
            self.assertIn("already carried out", str(ctx.exception))


class TestResolveSubject(DbCase):
    def test_the_account_supplies_the_phone_and_the_email_in_their_clean_form(self):
        self.fx.insert("users", dni=P, contact="+54 9 2255 123456", email="  Pia.Ejemplo@Example.ORG ")
        s = subject_export.resolve_subject(P)
        self.assertEqual((s.dni, s.phone10, s.emails), (P, "2255123456", ("pia.ejemplo@example.org",)))
        self.assertEqual(s.dni_hash, access_log.dni_hash(P))
        self.assertEqual(s.notes, ())

    def test_a_person_without_an_account_has_neither_phone_nor_email(self):
        s = subject_export.resolve_subject(P)
        self.assertEqual((s.phone10, s.emails, s.notes), ("", (), ()))

    def test_what_the_operator_types_wins_over_the_account(self):
        self.fx.insert("users", dni=P, contact="2255123456", email="a@example.org")
        s = subject_export.resolve_subject(P, phone="(011) 4444-5555", email="B@Example.org")
        self.assertEqual((s.phone10, s.emails), ("1144445555", ("b@example.org",)))

    def test_a_short_phone_is_no_phone(self):
        self.fx.insert("users", dni=P, contact="4444555")
        self.assertEqual(subject_export.resolve_subject(P).phone10, "")

    def test_a_phone_or_email_of_another_account_is_left_out_and_said(self):
        self.fx.insert("users", dni=P, contact="2255 123456", email="shared@example.org")
        self.fx.insert("users", dni=Q, contact="+54 9 2255 123456", email="SHARED@example.org")
        s = subject_export.resolve_subject(P)
        self.assertEqual((s.phone10, s.emails), ("", ()))
        self.assertEqual(len(s.notes), 2)
        self.assertIn("phone", s.notes[0])
        self.assertIn("email", s.notes[1])
        kept = subject_export.resolve_subject(P, confirm_shared=True)
        self.assertEqual((kept.phone10, kept.emails, kept.notes),
                         ("2255123456", ("shared@example.org",), ()))

    def test_another_accounts_different_phone_or_email_changes_nothing(self):
        self.fx.insert("users", dni=P, contact="2255 123456", email="mine@example.org")
        self.fx.insert("users", dni=Q, contact="2255 999888", email="other@example.org")
        s = subject_export.resolve_subject(P)
        self.assertEqual((s.phone10, s.emails, s.notes), ("2255123456", ("mine@example.org",), ()))


class TestErasureBlockerEdges(DbCase):
    """What blocks an erasure, and what does not."""

    def setUp(self):
        super().setUp()
        self.subject = subject_export.resolve_subject(P)

    def _blockers(self):
        with db._connect() as conn, conn.cursor() as cur:
            return subject_erase._blockers(cur, self.subject)

    def _request(self, dni=P, **kw):
        values = dict(dni=dni, requester_dni=dni, first_name="N", last_name="A", service_id="lab",
                      center_slug="c")
        values.update(kw)
        return self.fx.insert("booking_requests", **values)

    def _future(self, days=5):
        return datetime.now(timezone.utc) + timedelta(days=days)

    def test_nothing_blocks_a_person_with_no_rows(self):
        self.assertEqual(self._blockers(), [])

    def test_a_future_approved_and_a_pending_without_a_day_block(self):
        rid = self._request(status="aprobada", appointment_at=self._future())
        self.assertEqual(len(self._blockers()), 1)
        self.fx.execute("UPDATE booking_requests SET status='cancelada' WHERE id=%s", (rid,))
        self.assertEqual(self._blockers(), [])
        self._request(status="pendiente", appointment_at=None)
        self.assertEqual(len(self._blockers()), 1)

    def test_a_past_approved_a_cancelled_one_and_another_persons_do_not_block(self):
        self._request(status="aprobada", appointment_at=datetime.now(timezone.utc) - timedelta(days=3))
        self._request(status="cancelada", appointment_at=self._future())
        self._request(status="rechazada", appointment_at=None)
        self._request(dni=Q, status="aprobada", appointment_at=self._future())
        self.assertEqual(self._blockers(), [])

    def test_a_future_appointment_still_to_be_sent_to_hsi_blocks_but_a_sent_one_does_not(self):
        rid = self._request(status="cancelada", appointment_at=self._future(), hsi_status="error")
        self.assertEqual(len(self._blockers()), 1)
        self.fx.execute("UPDATE booking_requests SET hsi_status='pendiente' WHERE id=%s", (rid,))
        self.assertEqual(len(self._blockers()), 1)
        self.fx.execute("UPDATE booking_requests SET hsi_status='ok' WHERE id=%s", (rid,))
        self.assertEqual(self._blockers(), [])
        self.fx.execute("UPDATE booking_requests SET hsi_status='error', status='aprobada' WHERE id=%s", (rid,))
        # an approved one with a future day is a live appointment AND an HSI one: both reasons
        self.assertEqual(len(self._blockers()), 2)

    def test_an_hsi_intent_that_is_not_confirmed_blocks(self):
        for state, blocks in (("confirmed", 0), ("pending", 1), ("sending", 1), ("error", 1), ("conflict", 1)):
            with self.subTest(state=state):
                self.fx.execute("TRUNCATE hsi_outbox")
                self.fx.insert("hsi_outbox", entity_type="patient", entity_id="1", operation="create",
                               payload={"dni": P}, idempotency_key="ik-" + state, state=state)
                self.assertEqual(len(self._blockers()), blocks)

    def test_an_intent_of_another_person_does_not_block(self):
        self.fx.insert("hsi_outbox", entity_type="patient", entity_id="1", operation="create",
                       payload={"dni": Q}, idempotency_key="ik-q", state="pending")
        self.assertEqual(self._blockers(), [])

    def test_an_unresolved_sync_conflict_blocks_and_a_resolved_one_does_not(self):
        cid = self.fx.insert("hsi_sync_conflicts", entity_type="patient", entity_id="1",
                             direction="inbound", reason="x", local_payload={"documento": P},
                             remote_payload={})
        self.assertEqual(len(self._blockers()), 1)
        self.fx.execute("UPDATE hsi_sync_conflicts SET resolved_at = now() WHERE id=%s", (cid,))
        self.assertEqual(self._blockers(), [])

    def test_the_referral_paths_are_this_persons_non_null_ones(self):
        self._request(referral_path="/data/a.pdf")
        self._request(referral_path=None)
        self._request(dni=Q, referral_path="/data/q.pdf")
        with db._connect() as conn, conn.cursor() as cur:
            self.assertEqual(subject_erase._referral_paths(cur, self.subject), ["/data/a.pdf"])


class TestWhereClauseIsTheWhereSql(DbCase):
    """`personal_data.where_clause` (Core) finds exactly the rows `where_sql` (text) finds, entry by
    entry, for a person with every place filled, one with nothing usable but the DNI, and one
    whose phone and email are shared."""

    def setUp(self):
        super().setUp()
        self.fx.seed_person(P, "Pia")
        self.fx.seed_person(Q, "Quimey", phone="2255999888")
        # a waiting-list row found by e-mail only (no DNI of its own) and a search that types the DNI
        self.fx.insert("waitlist", dni=None, email="pia@example.org", service_id="lab", center_slug="c")
        self.fx.insert("searches", q="turno para " + P + " por favor", results=1, source="web")
        self.fx.insert("searches", q="busco 9" + P + "9", results=1, source="web")
        self.fx.insert("chat_messages", channel="web", key="dni:" + P + "9", role="user",
                       content={"text": "not hers"})

    def _text_ids(self, entry, subject):
        with self.fx.conn() as c, c.cursor() as cur:
            cur.execute(f"SELECT ctid::text AS c FROM {entry.table} WHERE "
                        f"{personal_data.where_sql(entry, subject)}", subject.params)
            return sorted(r["c"] for r in cur.fetchall())

    def _core_ids(self, entry, subject):
        from sqlalchemy import literal_column, select, table
        from app.core import sqlcore
        with db._connect() as conn, conn.cursor() as cur:
            rows = sqlcore.run(cur, select(literal_column("ctid::text").label("c")).select_from(
                table(entry.table)).where(personal_data.where_clause(entry, subject))).fetchall()
            return sorted(r["c"] for r in rows)

    def test_every_entry_finds_the_same_rows(self):
        subjects = {
            "full": subject_export.resolve_subject(P),
            "dni only": personal_data.Subject(P, dni_hash=access_log.dni_hash(P)),
            "other": subject_export.resolve_subject(Q),
            "no one": personal_data.Subject("11111111", dni_hash=access_log.dni_hash("11111111")),
        }
        found = 0
        for name, subject in subjects.items():
            for entry in personal_data.REGISTRY:
                with self.subTest(subject=name, entry=entry.label):
                    want = self._text_ids(entry, subject)
                    self.assertEqual(self._core_ids(entry, subject), want)
                    found += len(want)
        self.assertGreater(found, 40, "the fixture should make most selectors find something")

    def test_the_bare_subject_finds_nothing_by_phone_or_email(self):
        bare = personal_data.Subject(P, dni_hash=access_log.dni_hash(P))
        for entry in personal_data.REGISTRY:
            for sel in entry.find:
                if sel[0] in ("phone", "email", "phone_key"):
                    single = entry._replace(find=(sel,))
                    self.assertEqual(self._core_ids(single, bare), [], entry.label)

    def test_a_registry_table_is_in_the_model(self):
        from app.core.schema_model import metadata
        for entry in personal_data.REGISTRY:
            self.assertIn(entry.table, metadata.tables)


class TestEraseLockKey(DbCase):
    """`apply` takes `pg_advisory_xact_lock(hashtext('subject_erase:<dni>'))` first: a second
    erasure of the same person waits for the first; another person's does not."""

    def setUp(self):
        super().setUp()
        self.fx.seed_person(P, "Pia")
        self.fx.seed_person(Q, "Quimey", phone="2255999888")
        self.fx.execute("UPDATE booking_requests SET status = 'cancelada' WHERE status = 'pendiente'")

    def _erase(self, dni):
        row = sr.register("erasure", dni, "mesa", holidays=())
        sr.set_identity_check(row["id"], "DNI shown in person", "mesa")
        return subject_erase.apply(subject_export.resolve_subject(dni), "block", row["id"],
                                   "counsel, 2026-10-30", "tester")

    def _holding(self, key):
        conn = self.fx.conn()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (key,))
        return conn

    def _run_in_thread(self, dni):
        import threading
        done = threading.Event()
        out = {}

        def work():
            try:
                out["report"] = self._erase(dni)
            except BaseException as exc:  # noqa: BLE001
                out["error"] = exc
            finally:
                done.set()
        threading.Thread(target=work, daemon=True).start()
        return done, out

    def test_the_same_persons_lock_makes_it_wait(self):
        holder = self._holding("subject_erase:" + P)
        done, out = self._run_in_thread(P)
        self.assertFalse(done.wait(1.0), "the erasure ran while its lock was held")
        holder.commit()
        holder.close()
        self.assertTrue(done.wait(20))
        self.assertNotIn("error", out)

    def test_another_persons_lock_does_not(self):
        holder = self._holding("subject_erase:" + Q)
        try:
            done, out = self._run_in_thread(P)
            self.assertTrue(done.wait(20))
            self.assertNotIn("error", out)
        finally:
            holder.rollback()
            holder.close()


if __name__ == "__main__":
    unittest.main()
