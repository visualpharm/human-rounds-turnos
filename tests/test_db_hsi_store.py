"""test_db_hsi_store.py — the HSI statements of `core/db.py` against a real Postgres.

Stage 4 of the SQLAlchemy Core move converts the durable HSI journal of `core/db.py`:
the outbox (`hsi_outbox_*`: enqueue, the `FOR UPDATE SKIP LOCKED` lease, confirm, retry,
conflict), the inbox, the mirror tables and checkpoints, the sync-conflict log read by
the resolver (`hsi_open_conflicts`, `hsi_resolve_conflict`, ...) and the two booking-row
writes the resolver makes (`clear_hsi_link`, `assign_doctor_hsi`). The suites that already
touch these functions patch them out; this file pins each of them row for row — returned
rows, ordering, idempotence, the lease and its locks — so the conversion is proven to
change nothing. It was written and run green on the hand-written SQL first, and passes
unedited on Core.
"""
import itertools
import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_hsi_store")

UTC = timezone.utc


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    """Runs one statement and returns all its rows (or [] for a statement without any)."""
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE hsi_outbox, hsi_inbox, hsi_patients, hsi_doctor_schedules, "
                        "hsi_appointments, hsi_checkpoints, hsi_sync_conflicts RESTART IDENTITY")
            _dbtest.delete_booking_requests(cur)


_ENTITIES = itertools.count(1000)


def _outbox(key="k1", entity_id=None, entity_type="appointment", operation="create", payload=None):
    # A second create of the same appointment is the outstanding one (one
    # remote identity per booking), so rows that must stay apart default to
    # their own appointment.
    if entity_id is None:
        entity_id = next(_ENTITIES)
    return db_hsi.hsi_outbox_enqueue(entity_type, entity_id, operation,
                                 {"a": 1} if payload is None else payload, key)


def _set_outbox(id_, **cols):
    sets = ", ".join(f"{c}=%s" for c in cols)
    _sql(f"UPDATE hsi_outbox SET {sets} WHERE id=%s", (*cols.values(), id_))


def _outbox_row(id_):
    return _sql("SELECT * FROM hsi_outbox WHERE id=%s", (id_,))[0]


def _near(test, moment, expected, seconds=30):
    test.assertLess(abs((moment - expected).total_seconds()), seconds)


# ---- the outbox -----------------------------------------------------------------

class TestOutboxEnqueue(DbCase):
    def test_new_intent_is_pending_and_returned_whole(self):
        row = db_hsi.hsi_outbox_enqueue("appointment", 7, "create",
                                    {"when": datetime(2026, 1, 2, 3, 4, tzinfo=UTC), "n": [1, {"x": 2}]},
                                    "key-a")
        self.assertEqual(row["entity_type"], "appointment")
        self.assertEqual(row["entity_id"], "7")  # str()
        self.assertEqual(row["operation"], "create")
        self.assertEqual(row["idempotency_key"], "key-a")
        self.assertEqual((row["state"], row["attempts"]), ("pending", 0))
        self.assertEqual(row["payload"], {"when": "2026-01-02T03:04:00+00:00", "n": [1, {"x": 2}]})
        self.assertIsNone(row["lease_expires_at"])
        self.assertIsNone(row["last_error"])
        self.assertIsNone(row["hsi_result"])
        self.assertEqual(set(row), {"id", "entity_type", "entity_id", "operation", "payload",
                                    "idempotency_key", "state", "attempts", "next_attempt_at",
                                    "lease_expires_at", "last_error", "hsi_result", "created", "updated"})
        self.assertEqual(_outbox_row(row["id"]), row)

    def test_same_key_is_a_noop_that_only_touches_updated(self):
        first = _outbox("same", entity_id=7, payload={"v": 1})
        again = db_hsi.hsi_outbox_enqueue("other", 99, "cancel", {"v": 2}, "same")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual((again["entity_type"], again["entity_id"], again["operation"]),
                         ("appointment", "7", "create"))
        self.assertEqual(again["payload"], {"v": 1})
        self.assertGreaterEqual(again["updated"], first["updated"])
        self.assertEqual(len(_sql("SELECT 1 FROM hsi_outbox")), 1)

    def test_another_key_is_another_row(self):
        a, b = _outbox("one"), _outbox("two")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(_sql("SELECT 1 FROM hsi_outbox")), 2)


class TestOutboxClaim(DbCase):
    def test_claims_due_rows_in_order_and_leases_them(self):
        now = datetime.now(UTC)
        late, early, mid = _outbox("late"), _outbox("early"), _outbox("mid")
        _set_outbox(late["id"], next_attempt_at=now - timedelta(minutes=1))
        _set_outbox(early["id"], next_attempt_at=now - timedelta(minutes=3))
        _set_outbox(mid["id"], next_attempt_at=now - timedelta(minutes=2))
        rows = db_hsi.hsi_outbox_claim()
        self.assertEqual({r["idempotency_key"] for r in rows}, {"late", "early", "mid"})
        for row in rows:
            self.assertEqual((row["state"], row["attempts"]), ("sending", 1))
            _near(self, row["lease_expires_at"], datetime.now(UTC) + timedelta(minutes=5))
        self.assertEqual(sorted(r["id"] for r in rows), sorted((late["id"], early["id"], mid["id"])))
        self.assertEqual({r["state"] for r in _sql("SELECT state FROM hsi_outbox")}, {"sending"})

    def test_limit_takes_the_oldest_due_rows_first(self):
        now = datetime.now(UTC)
        ids = []
        for n in range(4):
            row = _outbox(f"k{n}")
            _set_outbox(row["id"], next_attempt_at=now - timedelta(minutes=10 - n))
            ids.append(row["id"])
        rows = db_hsi.hsi_outbox_claim(limit=2)
        self.assertEqual(sorted(r["id"] for r in rows), sorted(ids[:2]))
        self.assertEqual(_outbox_row(ids[2])["state"], "pending")

    def test_only_due_work_is_claimed(self):
        now = datetime.now(UTC)
        due = _outbox("due")
        error_due = _outbox("error-due")
        _set_outbox(error_due["id"], state="error", next_attempt_at=now - timedelta(seconds=5))
        future = _outbox("future")
        _set_outbox(future["id"], next_attempt_at=now + timedelta(hours=1))
        future_error = _outbox("future-error")
        _set_outbox(future_error["id"], state="error", next_attempt_at=now + timedelta(hours=1))
        confirmed = _outbox("confirmed")
        _set_outbox(confirmed["id"], state="confirmed")
        conflict = _outbox("conflict")
        _set_outbox(conflict["id"], state="conflict")
        live = _outbox("live-lease")
        _set_outbox(live["id"], state="sending", lease_expires_at=now + timedelta(minutes=2))
        expired = _outbox("expired-lease")
        _set_outbox(expired["id"], state="sending", lease_expires_at=now - timedelta(minutes=2),
                    attempts=3)
        claimed = {r["idempotency_key"]: r for r in db_hsi.hsi_outbox_claim()}
        self.assertEqual(set(claimed), {"due", "error-due", "expired-lease"})
        self.assertEqual(claimed["expired-lease"]["attempts"], 4)
        self.assertEqual(claimed["error-due"]["attempts"], 1)
        self.assertEqual(db_hsi.hsi_outbox_claim(), [])  # all leased now, nothing else is due
        self.assertEqual(_outbox_row(due["id"])["state"], "sending")
        for untouched in (future, future_error, confirmed, conflict, live):
            self.assertEqual(_outbox_row(untouched["id"])["attempts"], 0)

    def test_skips_a_row_another_worker_holds_locked(self):
        """FOR UPDATE SKIP LOCKED: a row locked by a concurrent worker is passed over, not waited for."""
        held, free = _outbox("held"), _outbox("free")
        with db._connect() as other, other.cursor() as ocur:
            ocur.execute("SELECT id FROM hsi_outbox WHERE id=%s FOR UPDATE", (held["id"],))
            started = time.monotonic()
            rows = db_hsi.hsi_outbox_claim()
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual([r["id"] for r in rows], [free["id"]])
        self.assertEqual(_outbox_row(held["id"])["state"], "pending")
        self.assertEqual([r["id"] for r in db_hsi.hsi_outbox_claim()], [held["id"]])

    def test_claim_id_leases_one_specific_row(self):
        a, b = _outbox("a"), _outbox("b")
        row = db_hsi.hsi_outbox_claim_id(a["id"])
        self.assertEqual((row["id"], row["state"], row["attempts"]), (a["id"], "sending", 1))
        _near(self, row["lease_expires_at"], datetime.now(UTC) + timedelta(minutes=5))
        self.assertEqual(_outbox_row(b["id"])["state"], "pending")

    def test_claim_id_takes_an_error_row_and_an_expired_lease_but_not_the_rest(self):
        now = datetime.now(UTC)
        cases = {
            "error-due": (dict(state="error", next_attempt_at=now - timedelta(seconds=1)), True),
            "expired": (dict(state="sending", lease_expires_at=now - timedelta(minutes=1)), True),
            "live": (dict(state="sending", lease_expires_at=now + timedelta(minutes=1)), False),
            "future": (dict(next_attempt_at=now + timedelta(hours=1)), False),
            "confirmed": (dict(state="confirmed"), False),
            "conflict": (dict(state="conflict"), False),
        }
        for key, (cols, claimable) in cases.items():
            with self.subTest(key):
                row = _outbox(key)
                _set_outbox(row["id"], **cols)
                got = db_hsi.hsi_outbox_claim_id(row["id"])
                if claimable:
                    self.assertEqual((got["id"], got["state"]), (row["id"], "sending"))
                else:
                    self.assertIsNone(got)
        self.assertIsNone(db_hsi.hsi_outbox_claim_id(987654))

    def test_claim_id_waits_for_a_row_lock_instead_of_skipping_it(self):
        """FOR UPDATE without SKIP LOCKED: the synchronous handoff queues behind the lock holder."""
        row = _outbox("waits")
        result = {}
        with db._connect() as other, other.cursor() as ocur:
            ocur.execute("SELECT id FROM hsi_outbox WHERE id=%s FOR UPDATE", (row["id"],))
            thread = threading.Thread(
                target=lambda: result.update(row=db_hsi.hsi_outbox_claim_id(row["id"])))
            thread.start()
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "claim_id must wait for the lock")
            other.commit()  # releases the row lock
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((result["row"]["id"], result["row"]["state"]), (row["id"], "sending"))


class TestOutboxTransitions(DbCase):
    def test_reset_rearms_a_retry_now(self):
        for state in ("error", "sending", "conflict", "pending"):
            with self.subTest(state):
                row = _outbox("r-" + state)
                # A live lease still belongs to its worker; only an expired one is rearmed.
                lease = datetime.now(UTC) + (timedelta(minutes=-3) if state == "sending"
                                             else timedelta(minutes=3))
                _set_outbox(row["id"], state=state, attempts=2, last_error="boom",
                            next_attempt_at=datetime.now(UTC) + timedelta(hours=2),
                            lease_expires_at=lease)
                got = db_hsi.hsi_outbox_reset(row["id"])
                self.assertEqual((got["id"], got["state"], got["attempts"]), (row["id"], "pending", 2))
                self.assertIsNone(got["lease_expires_at"])
                self.assertEqual(got["last_error"], "boom")  # the reason of the last failure stays
                _near(self, got["next_attempt_at"], datetime.now(UTC))

    def test_reset_leaves_a_live_lease_with_its_worker(self):
        row = _outbox("live")
        _set_outbox(row["id"], state="sending", attempts=1,
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=3))
        self.assertIsNone(db_hsi.hsi_outbox_reset(row["id"]))
        self.assertEqual(_outbox_row(row["id"])["state"], "sending")

    def test_reset_never_rearms_a_confirmed_row_or_a_missing_one(self):
        row = _outbox("done")
        _set_outbox(row["id"], state="confirmed")
        self.assertIsNone(db_hsi.hsi_outbox_reset(row["id"]))
        self.assertEqual(_outbox_row(row["id"])["state"], "confirmed")
        self.assertIsNone(db_hsi.hsi_outbox_reset(424242))

    def test_inflight_is_pending_sending_or_error_for_that_entity(self):
        self.assertFalse(db_hsi.hsi_outbox_inflight("appointment", 7))
        for n, state in enumerate(("pending", "sending", "error")):
            with self.subTest(state):
                row = _outbox(f"i{n}", entity_id=100 + n)
                _set_outbox(row["id"], state=state)
                self.assertTrue(db_hsi.hsi_outbox_inflight("appointment", 100 + n))
                self.assertTrue(db_hsi.hsi_outbox_inflight("appointment", str(100 + n)))
        for n, state in enumerate(("confirmed", "conflict")):
            with self.subTest(state):
                row = _outbox(f"f{n}", entity_id=200 + n)
                _set_outbox(row["id"], state=state)
                self.assertFalse(db_hsi.hsi_outbox_inflight("appointment", 200 + n))
        self.assertFalse(db_hsi.hsi_outbox_inflight("patient", 100))  # same id, another entity type

    def test_outstanding_is_the_oldest_unsettled_attempt_of_that_operation(self):
        self.assertIsNone(db_hsi.hsi_outbox_outstanding("appointment", 7, "create"))
        settled = _outbox("o0", entity_id=7)
        _set_outbox(settled["id"], state="confirmed")
        self.assertIsNone(db_hsi.hsi_outbox_outstanding("appointment", 7, "create"))
        first = _outbox("o1", entity_id=7)
        _set_outbox(first["id"], state="conflict")
        # A second create of the same appointment is the outstanding one, never a sibling.
        self.assertEqual(_outbox("o2", entity_id=7)["id"], first["id"])
        got = db_hsi.hsi_outbox_outstanding("appointment", "7", "create")
        self.assertEqual(got, _outbox_row(first["id"]))  # the whole row, the oldest one
        self.assertEqual(db_hsi.hsi_outbox_outstanding("appointment", 7, "create")["id"], first["id"])
        _set_outbox(first["id"], state="confirmed")
        second = _outbox("o3", entity_id=7)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(db_hsi.hsi_outbox_outstanding("appointment", 7, "create")["id"], second["id"])
        for n, state in enumerate(("pending", "sending", "error", "conflict")):
            with self.subTest(state):
                row = _outbox(f"s{n}", entity_id=300 + n, operation="reassign")
                _set_outbox(row["id"], state=state)
                self.assertEqual(db_hsi.hsi_outbox_outstanding("appointment", 300 + n, "reassign")["id"], row["id"])
                self.assertIsNone(db_hsi.hsi_outbox_outstanding("appointment", 300 + n, "create"))
                self.assertIsNone(db_hsi.hsi_outbox_outstanding("patient", 300 + n, "reassign"))

    def test_confirm_records_the_result_and_clears_the_error_and_lease(self):
        row = _outbox("c")
        _set_outbox(row["id"], state="sending", last_error="old",
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5))
        got = db_hsi.hsi_outbox_confirm(row["id"], {"id": 55, "at": datetime(2026, 5, 6, tzinfo=UTC)})
        self.assertEqual(got["state"], "confirmed")
        self.assertEqual(got["hsi_result"], {"id": 55, "at": "2026-05-06T00:00:00+00:00"})
        self.assertIsNone(got["last_error"])
        self.assertIsNone(got["lease_expires_at"])
        self.assertEqual(_outbox_row(row["id"]), got)

    def test_confirm_without_a_result_stores_an_empty_object(self):
        row = _outbox("c2")
        self.assertEqual(db_hsi.hsi_outbox_confirm(row["id"], None)["hsi_result"], {})

    def test_confirm_of_a_missing_row_is_none(self):
        self.assertIsNone(db_hsi.hsi_outbox_confirm(31337, {}))

    def test_retry_schedules_the_next_attempt(self):
        row = _outbox("t")
        _set_outbox(row["id"], state="sending", attempts=1,
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5))
        got = db_hsi.hsi_outbox_retry(row["id"], "timeout " + "x" * 3000, 90.7)
        self.assertEqual((got["state"], got["attempts"]), ("error", 1))
        self.assertEqual(got["last_error"], ("timeout " + "x" * 3000)[:2000])
        self.assertIsNone(got["lease_expires_at"])
        _near(self, got["next_attempt_at"], datetime.now(UTC) + timedelta(seconds=90))
        self.assertIsNone(db_hsi.hsi_outbox_retry(31337, "e", 1))

    def test_conflict_marks_the_row_and_logs_an_outbound_conflict(self):
        row = _outbox("x", entity_id=9)
        got = db_hsi.hsi_outbox_conflict(row["id"], "slot taken " + "y" * 3000,
                                     {"turno": datetime(2026, 1, 1, tzinfo=UTC)}, {"remote": [1]})
        self.assertEqual((got["id"], got["state"]), (row["id"], "conflict"))
        self.assertEqual(got["last_error"], ("slot taken " + "y" * 3000)[:2000])
        logged = _sql("SELECT * FROM hsi_sync_conflicts")
        self.assertEqual(len(logged), 1)
        self.assertEqual((logged[0]["entity_type"], logged[0]["entity_id"], logged[0]["direction"]),
                         ("appointment", "9", "outbound"))
        self.assertEqual(logged[0]["reason"], got["last_error"])
        self.assertEqual(logged[0]["local_payload"], {"turno": "2026-01-01T00:00:00+00:00"})
        self.assertEqual(logged[0]["remote_payload"], {"remote": [1]})
        self.assertIsNone(logged[0]["resolved_at"])

    def test_conflict_without_payloads_stores_sql_nulls(self):
        row = _outbox("x2")
        db_hsi.hsi_outbox_conflict(row["id"], "r")
        logged = _sql("SELECT local_payload IS NULL AS l, remote_payload IS NULL AS r "
                      "FROM hsi_sync_conflicts")
        self.assertEqual(logged, [{"l": True, "r": True}])

    def test_conflict_of_a_missing_row_logs_nothing(self):
        self.assertIsNone(db_hsi.hsi_outbox_conflict(31337, "r", {"a": 1}))
        self.assertEqual(_sql("SELECT 1 FROM hsi_sync_conflicts"), [])

    def test_by_entity_lists_the_appointments_rows_in_the_given_states_newest_first(self):
        a = _outbox("e1", entity_id=5, operation="reassign")
        b = _outbox("e2", entity_id=5, operation="reassign")
        c = _outbox("e3", entity_id=5, operation="reassign")
        other = _outbox("e4", entity_id=6)
        patient = _outbox("e5", entity_id=5, entity_type="patient")
        for r in (a, b, other, patient):
            _set_outbox(r["id"], state="conflict")
        _set_outbox(c["id"], state="error")
        self.assertEqual([r["id"] for r in db_hsi.hsi_outbox_by_entity(5)], [b["id"], a["id"]])
        self.assertEqual([r["id"] for r in db_hsi.hsi_outbox_by_entity("5", states=("error", "conflict"))],
                         [c["id"], b["id"], a["id"]])
        self.assertEqual(db_hsi.hsi_outbox_by_entity(5, states=[]), [])
        self.assertEqual(db_hsi.hsi_outbox_by_entity(77), [])


# ---- the inbox, the mirror tables, the checkpoints ---------------------------------

class TestInbox(DbCase):
    def test_record_dedups_by_source_key(self):
        row = db_hsi.hsi_inbox_record("src-1", "appointment", 12, {"at": datetime(2026, 2, 3, tzinfo=UTC)})
        self.assertEqual((row["source_key"], row["entity_type"], row["entity_id"], row["state"]),
                         ("src-1", "appointment", "12", "pending"))
        self.assertEqual(row["payload"], {"at": "2026-02-03T00:00:00+00:00"})
        self.assertIsNone(row["reconciled_at"])
        self.assertIsNone(row["conflict"])
        self.assertIsNone(db_hsi.hsi_inbox_record("src-1", "appointment", 12, {"different": True}))
        self.assertEqual(_sql("SELECT payload FROM hsi_inbox"), [{"payload": row["payload"]}])
        self.assertIsNotNone(db_hsi.hsi_inbox_record("src-2", "appointment", 12, {}))

    def test_finish_defaults_to_confirmed(self):
        db_hsi.hsi_inbox_record("s", "appointment", 1, {})
        row = db_hsi.hsi_inbox_finish("s")
        self.assertEqual((row["state"], row["source_key"]), ("confirmed", "s"))
        self.assertIsNone(row["conflict"])  # SQL NULL, not JSON null
        self.assertEqual(_sql("SELECT conflict IS NULL AS n FROM hsi_inbox"), [{"n": True}])
        _near(self, row["reconciled_at"], datetime.now(UTC))

    def test_finish_records_a_conflict_document(self):
        db_hsi.hsi_inbox_record("s", "appointment", 1, {})
        row = db_hsi.hsi_inbox_finish("s", "conflict", {"why": "dup", "when": datetime(2026, 1, 1, tzinfo=UTC)})
        self.assertEqual(row["state"], "conflict")
        self.assertEqual(row["conflict"], {"why": "dup", "when": "2026-01-01T00:00:00+00:00"})

    def test_finish_of_an_unknown_key_is_none(self):
        self.assertIsNone(db_hsi.hsi_inbox_finish("nope"))


class TestMirrors(DbCase):
    def test_patient_is_inserted_then_only_rewritten_when_it_changed(self):
        row = db_hsi.hsi_mirror_patient(41, "30111222", {"name": "Ana"})
        self.assertEqual((row["hsi_patient_id"], row["dni"], row["payload"]),
                         ("41", "30111222", {"name": "Ana"}))
        self.assertIsNone(db_hsi.hsi_mirror_patient(41, "30111222", {"name": "Ana"}))  # nothing changed
        changed = db_hsi.hsi_mirror_patient("41", "30111222", {"name": "Ana", "x": 1})
        self.assertEqual(changed["payload"], {"name": "Ana", "x": 1})
        self.assertGreaterEqual(changed["observed_at"], row["observed_at"])
        self.assertEqual(db_hsi.hsi_mirror_patient(41, "999", {"name": "Ana", "x": 1})["dni"], "999")
        # Una observación sin DNI (el resto de paciente de un listado de turnos, solo el id) no
        # pisa lo que la consulta de pacientes ya sabe: ni el DNI ni el registro.
        self.assertIsNone(db_hsi.hsi_mirror_patient(41, None, {"id": 41}))
        kept = _sql("SELECT dni, payload FROM hsi_patients WHERE hsi_patient_id='41'")[0]
        self.assertEqual((kept["dni"], kept["payload"]), ("999", {"name": "Ana", "x": 1}))
        # Mientras no se conoce ningún DNI, el resto sí se guarda.
        db_hsi.hsi_mirror_patient(42, None, {"id": 42})
        self.assertEqual(db_hsi.hsi_mirror_patient(42, None, {"id": 42, "x": 1})["payload"], {"id": 42, "x": 1})
        self.assertEqual(len(_sql("SELECT 1 FROM hsi_patients")), 2)

    def test_schedule_is_inserted_then_only_rewritten_when_it_changed(self):
        row = db_hsi.hsi_mirror_schedule(5, 77, {"days": [1]})
        self.assertEqual((row["hsi_diary_id"], row["hsi_professional_id"], row["payload"]),
                         ("5", "77", {"days": [1]}))
        self.assertIsNone(db_hsi.hsi_mirror_schedule(5, 77, {"days": [1]}))
        self.assertEqual(db_hsi.hsi_mirror_schedule(5, 78, {"days": [1]})["hsi_professional_id"], "78")
        self.assertEqual(db_hsi.hsi_mirror_schedule(5, 78, {"days": [2]})["payload"], {"days": [2]})

    def test_schedule_keeps_a_missing_professional_as_null(self):
        row = db_hsi.hsi_mirror_schedule(6, None, {})
        self.assertIsNone(row["hsi_professional_id"])
        self.assertIsNone(db_hsi.hsi_mirror_schedule(6, None, {}))

    def test_appointment_is_mirrored_and_its_checkpoint_follows(self):
        row = db_hsi.hsi_mirror_appointment(90, 5, 41, {"state": "ok"})
        self.assertEqual((row["hsi_appointment_id"], row["hsi_diary_id"], row["hsi_patient_id"],
                          row["payload"]), ("90", "5", "41", {"state": "ok"}))
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", 90), {"state": "ok"})
        # unchanged: no row back, but the checkpoint is still written
        self.assertIsNone(db_hsi.hsi_mirror_appointment(90, 5, 41, {"state": "ok"}))
        # changed payload
        changed = db_hsi.hsi_mirror_appointment(90, 5, 41, {"state": "cancelled"})
        self.assertEqual(changed["payload"], {"state": "cancelled"})
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", "90"), {"state": "cancelled"})
        # changed diary or patient
        self.assertEqual(db_hsi.hsi_mirror_appointment(90, 6, 41, {"state": "cancelled"})["hsi_diary_id"], "6")
        self.assertEqual(db_hsi.hsi_mirror_appointment(90, 6, 42, {"state": "cancelled"})["hsi_patient_id"], "42")

    def test_appointment_without_diary_or_patient_keeps_nulls(self):
        row = db_hsi.hsi_mirror_appointment(91, None, None, {})
        self.assertEqual((row["hsi_diary_id"], row["hsi_patient_id"]), (None, None))
        self.assertIsNone(db_hsi.hsi_mirror_appointment(91, None, None, {}))
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", 91), {})

    def test_appointment_checkpoint_is_refreshed_even_when_the_row_did_not_change(self):
        db_hsi.hsi_mirror_appointment(92, 1, 2, {"a": 1})
        db_hsi.hsi_checkpoint_set("appointment", 92, {"stale": True})
        self.assertIsNone(db_hsi.hsi_mirror_appointment(92, 1, 2, {"a": 1}))
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", 92), {"a": 1})

    def test_checkpoints_get_and_set(self):
        self.assertIsNone(db_hsi.hsi_checkpoint_get("appointment", 1))
        row = db_hsi.hsi_checkpoint_set("appointment", 1, {"v": datetime(2026, 1, 1, tzinfo=UTC)})
        self.assertEqual((row["entity_type"], row["entity_id"], row["remote"]),
                         ("appointment", "1", {"v": "2026-01-01T00:00:00+00:00"}))
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", "1"), row["remote"])
        again = db_hsi.hsi_checkpoint_set("appointment", 1, {"v": 2})
        self.assertEqual(again["remote"], {"v": 2})
        self.assertEqual(len(_sql("SELECT 1 FROM hsi_checkpoints")), 1)
        self.assertIsNone(db_hsi.hsi_checkpoint_get("patient", 1))

    def test_known_patient_ids_are_the_most_recently_observed_first(self):
        for n in range(4):
            db_hsi.hsi_mirror_patient(f"p{n}", None, {})
            _sql("UPDATE hsi_patients SET observed_at = %s WHERE hsi_patient_id=%s",
                 (datetime(2026, 1, 1 + n, tzinfo=UTC), f"p{n}"))
        self.assertEqual(db_hsi.hsi_known_patient_ids(), ["p3", "p2", "p1", "p0"])
        self.assertEqual(db_hsi.hsi_known_patient_ids(limit=2), ["p3", "p2"])


# ---- sync conflicts ------------------------------------------------------------------

class TestSyncConflicts(DbCase):
    def test_hsi_conflict_inserts_and_returns_the_row(self):
        row = db_hsi.hsi_conflict("appointment", 3, "inbound", "r" * 3000, {"l": 1}, {"r": datetime(2026, 1, 1, tzinfo=UTC)})
        self.assertEqual((row["entity_type"], row["entity_id"], row["direction"]),
                         ("appointment", "3", "inbound"))
        self.assertEqual(row["reason"], "r" * 2000)
        self.assertEqual(row["local_payload"], {"l": 1})
        self.assertEqual(row["remote_payload"], {"r": "2026-01-01T00:00:00+00:00"})
        self.assertIsNone(row["resolved_at"])
        self.assertIsNone(row["resolution"])
        self.assertEqual(set(row), {"id", "entity_type", "entity_id", "direction", "reason",
                                    "local_payload", "remote_payload", "resolved_at", "created",
                                    "resolution", "resolved_by"})

    def test_hsi_conflict_payloads_default_to_sql_null(self):
        db_hsi.hsi_conflict("appointment", 3, "outbound", "r")
        self.assertEqual(_sql("SELECT local_payload IS NULL AS l, remote_payload IS NULL AS r "
                              "FROM hsi_sync_conflicts"), [{"l": True, "r": True}])

    def test_hsi_conflict_refuses_an_unknown_direction(self):
        with self.assertRaises(Exception):
            db_hsi.hsi_conflict("appointment", 3, "sideways", "r")

    def test_open_conflicts_newest_first_and_only_unresolved(self):
        ids = [db_hsi.hsi_conflict("appointment", n, "inbound", f"c{n}")["id"] for n in range(4)]
        _sql("UPDATE hsi_sync_conflicts SET created = %s WHERE id = ANY(%s)",
             (datetime(2026, 1, 1, tzinfo=UTC), ids[:3]))  # three tie on `created`: the id breaks it
        db_hsi.hsi_resolve_conflict(ids[0], {"ok": True})
        got = db_hsi.hsi_open_conflicts()
        self.assertEqual([r["id"] for r in got], [ids[3], ids[2], ids[1]])
        self.assertEqual([r["id"] for r in db_hsi.hsi_open_conflicts(limit=1)], [ids[3]])

    def test_resolve_closes_once_and_records_who_and_what(self):
        cid = db_hsi.hsi_conflict("appointment", 1, "inbound", "r")["id"]
        row = db_hsi.hsi_resolve_conflict(cid, {"action": "relink", "at": datetime(2026, 1, 1, tzinfo=UTC)})
        self.assertEqual(row["resolution"], {"action": "relink", "at": "2026-01-01T00:00:00+00:00"})
        self.assertEqual(row["resolved_by"], "resolver")
        _near(self, row["resolved_at"], datetime.now(UTC))
        self.assertIsNone(db_hsi.hsi_resolve_conflict(cid, {"again": True}, "someone"))  # never re-closed
        self.assertEqual(_sql("SELECT resolved_by FROM hsi_sync_conflicts"), [{"resolved_by": "resolver"}])
        self.assertIsNone(db_hsi.hsi_resolve_conflict(31337, {}))

    def test_resolve_with_no_resolution_stores_an_empty_object_and_the_given_actor(self):
        cid = db_hsi.hsi_conflict("appointment", 1, "inbound", "r")["id"]
        row = db_hsi.hsi_resolve_conflict(cid, None, "ana")
        self.assertEqual((row["resolution"], row["resolved_by"]), ({}, "ana"))

    def test_recently_resolved_lists_resolved_rows_with_a_resolution_newest_first(self):
        ids = [db_hsi.hsi_conflict("appointment", n, "inbound", f"c{n}")["id"] for n in range(4)]
        for n, cid in enumerate(ids[:3]):
            db_hsi.hsi_resolve_conflict(cid, {"n": n})
            _sql("UPDATE hsi_sync_conflicts SET resolved_at=%s WHERE id=%s",
                 (datetime(2026, 1, 1 + n, tzinfo=UTC), cid))
        # resolved without a resolution document (NULL) is not listed
        _sql("UPDATE hsi_sync_conflicts SET resolved_at=now(), resolution=NULL WHERE id=%s", (ids[3],))
        self.assertEqual([r["id"] for r in db_hsi.hsi_recently_resolved()], [ids[2], ids[1], ids[0]])
        self.assertEqual([r["id"] for r in db_hsi.hsi_recently_resolved(limit=2)], [ids[2], ids[1]])

    def test_sync_status_counts_the_outbox_and_lists_the_open_work(self):
        a, b, c, d = (_outbox(f"s{n}", entity_id=n) for n in range(4))
        _set_outbox(b["id"], state="error")
        _set_outbox(c["id"], state="confirmed")
        _set_outbox(d["id"], state="error")
        _sql("UPDATE hsi_outbox SET updated = %s WHERE id=%s", (datetime(2026, 1, 1, tzinfo=UTC), a["id"]))
        _sql("UPDATE hsi_outbox SET updated = %s WHERE id=%s", (datetime(2026, 1, 3, tzinfo=UTC), b["id"]))
        _sql("UPDATE hsi_outbox SET updated = %s WHERE id=%s", (datetime(2026, 1, 2, tzinfo=UTC), d["id"]))
        open_one = db_hsi.hsi_conflict("appointment", 1, "inbound", "open")
        closed = db_hsi.hsi_conflict("appointment", 2, "inbound", "closed")
        db_hsi.hsi_resolve_conflict(closed["id"], {})
        status = db_hsi.hsi_sync_status()
        self.assertEqual(status["counts"], {"pending": 1, "error": 2, "confirmed": 1})
        self.assertEqual([r["id"] for r in status["outbox"]], [b["id"], d["id"], a["id"]])
        self.assertEqual([r["id"] for r in status["conflicts"]], [open_one["id"]])
        self.assertEqual(set(status), {"counts", "outbox", "conflicts"})
        limited = db_hsi.hsi_sync_status(limit=1)
        self.assertEqual([r["id"] for r in limited["outbox"]], [b["id"]])
        self.assertEqual(limited["counts"], status["counts"])

    def test_sync_status_of_an_empty_journal(self):
        self.assertEqual(db_hsi.hsi_sync_status(), {"counts": {}, "outbox": [], "conflicts": []})


# ---- the two booking-row writes of the resolver and the panel numbers -----------------

def _booking(dni="30111222", **cols):
    _dbtest.ensure_user(dni)
    sid = db_booking.create_booking_request(dni, "Ana", "Paz", "1990-01-01", "2255123456", "cardio", "hospital")
    if cols:
        sets = ", ".join(f"{c}=%s" for c in cols)
        _sql(f"UPDATE booking_requests SET {sets} WHERE id=%s", (*cols.values(), sid))
    return sid


class TestAppointmentsOfAPatient(DbCase):
    def test_lists_the_remote_appointments_not_yet_linked_to_a_request_newest_observation_first(self):
        db_hsi.hsi_mirror_patient("p1", "30111222", {"name": "Ana"})
        db_hsi.hsi_mirror_patient("p2", "30999888", {"name": "Otro"})
        db_hsi.hsi_mirror_schedule("d1", "77", {"days": ["lun"]})
        db_hsi.hsi_mirror_appointment("a1", "d1", "p1", {"state": "assigned", "at": "2026-02-01"})
        db_hsi.hsi_mirror_appointment("a2", "d-none", "p1", {"at": "2026-02-02"})  # no schedule, no state
        db_hsi.hsi_mirror_appointment("a3", "d1", "p1", {"state": "cancelled"})
        db_hsi.hsi_mirror_appointment("a4", "d1", "p1", {"state": "assigned"})  # linked to a request
        db_hsi.hsi_mirror_appointment("a5", "d1", "p2", {"state": "assigned"})  # another patient
        db_hsi.hsi_mirror_appointment("a6", "d1", None, {"state": "assigned"})  # nobody's
        _booking(hsi_appointment_id="a4")
        for n, key in enumerate(("a1", "a2", "a3", "a4", "a5", "a6")):
            _sql("UPDATE hsi_appointments SET observed_at=%s WHERE hsi_appointment_id=%s",
                 (datetime(2026, 1, 1 + n, tzinfo=UTC), key))
        got = db_hsi.hsi_appointments_by_dni("30111222")
        self.assertEqual([r["hsi_appointment_id"] for r in got], ["a2", "a1"])
        self.assertEqual(set(got[0]), {"hsi_appointment_id", "appointment", "schedule"})
        self.assertEqual(got[0]["appointment"], {"at": "2026-02-02"})
        self.assertIsNone(got[0]["schedule"])
        self.assertEqual(got[1]["schedule"], {"days": ["lun"]})
        self.assertEqual(db_hsi.hsi_appointments_by_dni(30111222), got)  # str()
        self.assertEqual(db_hsi.hsi_appointments_by_dni("30000000"), [])


class TestFutureUnlinkedAppointments(DbCase):
    def test_lists_active_future_unlinked_appointments_with_their_professional(self):
        db_hsi.hsi_mirror_schedule("d1", "77", {"professional_name": "Pérez Juan"})
        at = "2026-12-01T09:00:00-03:00"
        db_hsi.hsi_mirror_appointment("f1", "d1", None, {"state": "scheduled", "scheduled_at": at})
        db_hsi.hsi_mirror_appointment("f2", "d1", None, {"state": "cancelled", "scheduled_at": at})
        db_hsi.hsi_mirror_appointment("f3", "d1", None, {"state": "scheduled", "scheduled_at": at})  # linked
        db_hsi.hsi_mirror_appointment("f4", "d1", None, {"state": "scheduled", "scheduled_at": "2026-01-01T09:00:00-03:00"})
        db_hsi.hsi_mirror_appointment("f5", "d-none", None, {"state": "attended", "scheduled_at": at})
        db_hsi.hsi_mirror_appointment("f6", "d1", None, {"state": "scheduled"})  # no time at all
        _booking(hsi_appointment_id="f3")
        got = db_hsi.hsi_future_unlinked_appointments("2026-11-11")
        self.assertEqual([r["hsi_appointment_id"] for r in got], ["f1", "f5"])
        self.assertEqual(got[0]["schedule"], {"professional_name": "Pérez Juan"})
        self.assertIsNone(got[1]["schedule"])
        self.assertEqual(db_hsi.hsi_future_unlinked_appointments("2027-01-01"), [])


class TestBookingRowWrites(DbCase):
    def test_by_appointment_lists_the_linked_requests_newest_first_wired(self):
        a = _booking(hsi_appointment_id="900", created=datetime(2026, 1, 1, tzinfo=UTC))
        b = _booking("30111223", hsi_appointment_id="900", created=datetime(2026, 1, 2, tzinfo=UTC))
        _booking("30111224", hsi_appointment_id="901")
        rows = db_hsi.hsi_booking_requests_by_appointment(900)
        self.assertEqual([r["id"] for r in rows], [b, a])
        self.assertEqual(rows[0]["nombre"], "Ana")  # wire format: Spanish keys
        self.assertEqual(rows[0]["estado"], "pendiente")
        self.assertEqual(db_hsi.hsi_booking_requests_by_appointment("902"), [])

    def test_clear_link_drops_the_hsi_ids_and_sets_the_status(self):
        sid = _booking(hsi_appointment_id="900", hsi_patient_id="41", hsi_status="ok")
        row = db_hsi.clear_hsi_link(sid, "pendiente")
        self.assertEqual(row["id"], sid)
        self.assertIsNone(row["hsi_appointment_id"])
        self.assertIsNone(row["hsi_patient_id"])
        self.assertEqual(row["hsi_estado"], "pendiente")  # wired
        self.assertNotIn("hsi_status", row)
        stored = _sql("SELECT hsi_appointment_id, hsi_patient_id, hsi_status FROM booking_requests WHERE id=%s", (sid,))
        self.assertEqual(stored, [{"hsi_appointment_id": None, "hsi_patient_id": None,
                                   "hsi_status": "pendiente"}])
        self.assertIsNone(db_hsi.clear_hsi_link(999999, "ok"))

    def test_assign_doctor_sets_name_and_id_together(self):
        sid = _booking()
        row = db_hsi.assign_doctor_hsi(sid, "Dra. Paz", True, " 12 ")
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"], row["sobreturno"]),
                         ("Dra. Paz", "12", True))

    def test_assign_doctor_keeps_overbooking_when_not_told_and_nulls_a_blank_id(self):
        sid = _booking(overbooking=True)
        row = db_hsi.assign_doctor_hsi(sid, "Dr. X", None, None)
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"], row["sobreturno"]),
                         ("Dr. X", None, True))
        row = db_hsi.assign_doctor_hsi(sid, "Dr. Y", False, "   ")
        self.assertEqual((row["medico_asignado_id"], row["sobreturno"]), (None, False))
        self.assertEqual(db_hsi.assign_doctor_hsi(sid, "Dr. Z", None, 7)["medico_asignado_id"], "7")
        self.assertIsNone(db_hsi.assign_doctor_hsi(999999, "Dr. Z"))

    def test_metrics_count_approved_scheduled_requests_and_the_queue(self):
        now = datetime.now(UTC)
        _booking("30000001", status="aprobada", appointment_at=now + timedelta(days=1), hsi_status="ok")
        _booking("30000002", status="aprobada", appointment_at=now + timedelta(days=1), hsi_status="error")
        _booking("30000003", status="aprobada", appointment_at=now - timedelta(days=1), hsi_status="ok")
        _booking("30000004", status="aprobada", appointment_at=now - timedelta(days=2))
        _booking("30000005", status="pendiente", appointment_at=now + timedelta(days=1), hsi_status="ok")
        _booking("30000006", status="aprobada", hsi_status="ok")  # no appointment
        _outbox("m1")
        _outbox("m2")
        confirmed = _outbox("m3")
        _set_outbox(confirmed["id"], state="confirmed")
        self.assertEqual(db_hsi.hsi_metrics(), {
            "futuras": 2, "futuras_ok": 1, "total": 4, "total_ok": 2,
            "outbox": {"pending": 2, "confirmed": 1}})

    def test_metrics_of_an_empty_database(self):
        self.assertEqual(db_hsi.hsi_metrics(), {"futuras": 0, "futuras_ok": 0, "total": 0,
                                            "total_ok": 0, "outbox": {}})

    def test_last_confirmed_is_the_latest_confirmed_transmission_only(self):
        self.assertIsNone(db_hsi.hsi_last_confirmed())
        old = _outbox("lc1")
        _set_outbox(old["id"], state="confirmed", updated=datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
        latest = _outbox("lc2")
        _set_outbox(latest["id"], state="confirmed", updated=datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
        # A newer row that is still waiting (or failed) is not a transmission.
        waiting = _outbox("lc3")
        _set_outbox(waiting["id"], state="error", updated=datetime(2026, 11, 10, 18, 0, tzinfo=UTC))
        self.assertEqual(db_hsi.hsi_last_confirmed(), datetime(2026, 11, 10, 12, 0, tzinfo=UTC))

    def test_last_confirmed_skips_what_was_closed_without_travelling(self):
        """With HSI down, a turno cancelled before its create was sent is closed as confirmed/skip, and
        a replaced intent as confirmed/superseded: neither reached HSI, so neither is «the last one»."""
        sent = _outbox("lc4")
        _set_outbox(sent["id"], state="confirmed", hsi_result=json.dumps({"estado": "ok"}),
                    updated=datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
        for key, outcome, hour in (("lc5", "skip", 12), ("lc6", "superseded", 13)):
            row = _outbox(key)
            _set_outbox(row["id"], state="confirmed", hsi_result=json.dumps({"estado": outcome}),
                        updated=datetime(2026, 11, 10, hour, 0, tzinfo=UTC))
        self.assertEqual(db_hsi.hsi_last_confirmed(), datetime(2026, 11, 9, 10, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
