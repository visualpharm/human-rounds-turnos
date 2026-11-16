"""Real disposable-Postgres replay proofs for stale HSI worker settlement."""

import concurrent.futures
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import db
from app.core import db_hsi
from app.modules.turnos import hsi_journal
import _dbtest

_database = _dbtest.PerModuleDb("sana_turnos_test_hsi_fence")


def setUpModule():
    _database.set_up()
    if not _database.have_db:
        raise RuntimeError("Disposable PostgreSQL is required for HSI worker fencing proof")


def tearDownModule():
    _database.tear_down()


class _Adapter:
    def __init__(self, outcome):
        self.outcome = outcome

    def degrades_when_unconfigured(self):
        return False

    def execute_intent(self, operation, payload, key):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class TestHsiWorkerFence(unittest.TestCase):
    def setUp(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE booking_requests CASCADE")
            cur.execute("TRUNCATE hsi_outbox,hsi_sync_conflicts,hsi_checkpoints,hsi_appointments RESTART IDENTITY")

    def booking(self, status="aprobada", hsi_status="pendiente"):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO booking_requests
                           (dni,first_name,last_name,status,appointment_at,hsi_status)
                           VALUES ('99900017','Synthetic','Fence',%s,
                                   now()+interval '1 day',%s) RETURNING id""",
                        (status, hsi_status))
            return cur.fetchone()["id"]

    def claim(self, sid, operation="cancel"):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT appointment_at FROM booking_requests WHERE id=%s", (sid,))
            when = cur.fetchone()["appointment_at"].isoformat()
        item = db_hsi.hsi_outbox_enqueue("appointment", str(sid), operation,
                                     {"solicitud_id": sid, "appointment_id": "synthetic-a",
                                      "when": when},
                                     f"fence-{sid}-{operation}")
        return db_hsi.hsi_outbox_claim_id(item["id"])

    def claim_unlinked_create(self, sid):
        item = db_hsi.hsi_outbox_enqueue("appointment", str(sid), "create",
                                     {"solicitud_id": sid}, f"fence-create-{sid}")
        return db_hsi.hsi_outbox_claim_id(item["id"])

    def expire(self, item):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE hsi_outbox SET lease_expires_at=now()-interval '1 second'
                           WHERE id=%s""", (item["id"],))

    def replay(self, item, outcome):
        with patch.object(hsi_journal.connectors, "resolve", return_value=("fake", "explicit")), \
             patch.object(hsi_journal.connectors, "adapter_for", return_value=_Adapter(outcome)):
            return hsi_journal._replay_one(item)

    def observed(self, sid, item):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM booking_requests WHERE id=%s", (sid,))
            booking = cur.fetchone()
            cur.execute("SELECT * FROM hsi_outbox WHERE id=%s", (item["id"],))
            outbox = cur.fetchone()
            cur.execute("SELECT count(*) AS n FROM hsi_sync_conflicts")
            conflicts = cur.fetchone()["n"]
            cur.execute("SELECT remote FROM hsi_checkpoints WHERE entity_id='synthetic-a'")
            checkpoint = cur.fetchone()
            cur.execute("SELECT payload FROM hsi_appointments WHERE hsi_appointment_id='synthetic-a'")
            mirror = cur.fetchone()
        return booking, outbox, conflicts, checkpoint, mirror

    def test_delayed_transient_failure_cannot_reset_newer_sending_lease(self):
        sid = self.booking()
        old = self.claim(sid)
        entered, release = threading.Event(), threading.Event()

        class Delayed(_Adapter):
            def execute_intent(self, *_args):
                entered.set()
                if not release.wait(10):
                    raise AssertionError("test release timed out")
                raise OSError("synthetic network outage")

        def old_replay():
            with patch.object(hsi_journal.connectors, "resolve", return_value=("fake", "explicit")), \
                 patch.object(hsi_journal.connectors, "adapter_for", return_value=Delayed(None)):
                return hsi_journal._replay_one(old)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(old_replay)
            try:
                self.assertTrue(entered.wait(10))
                self.expire(old)
                current = db_hsi.hsi_outbox_claim_id(old["id"])
                self.assertEqual(current["attempts"], old["attempts"] + 1)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10)["state"], "sending")
        booking, outbox, conflicts, _, _ = self.observed(sid, old)
        self.assertEqual((outbox["state"], outbox["attempts"]), ("sending", 2))
        self.assertIsNotNone(outbox["lease_expires_at"])
        self.assertEqual(booking["hsi_status"], "pendiente")
        self.assertEqual(conflicts, 0)
        self.assertEqual(self.replay(current, {"estado": "ok"})["state"], "confirmed")

    def test_unconfigured_old_first_lease_cannot_skip_newer_claim(self):
        sid = self.booking()
        old = self.claim_unlinked_create(sid)
        self.expire(old)
        current = db_hsi.hsi_outbox_claim_id(old["id"])
        with patch.object(hsi_journal.connectors, "resolve", return_value=(None, "off")), \
             patch.object(hsi_journal.connectors, "adapter_for", return_value=None):
            stale = hsi_journal._replay_one(old)
        booking, outbox, conflicts, checkpoint, mirror = self.observed(sid, old)
        self.assertEqual(stale["state"], "sending")
        self.assertEqual((outbox["state"], outbox["attempts"]), ("sending", 2))
        self.assertEqual(booking["hsi_status"], "pendiente")
        self.assertEqual(conflicts, 0)
        self.assertIsNone(checkpoint)
        self.assertIsNone(mirror)

    def test_unconfigured_first_create_with_missing_booking_remains_retryable(self):
        item = db_hsi.hsi_outbox_enqueue("appointment", "999991", "create",
                                     {"solicitud_id": 999991}, "fence-missing-booking")
        claim = db_hsi.hsi_outbox_claim_id(item["id"])
        with patch.object(hsi_journal.connectors, "resolve", return_value=(None, "off")), \
             patch.object(hsi_journal.connectors, "adapter_for", return_value=None):
            outcome = hsi_journal._replay_one(claim)
        self.assertEqual((outcome["state"], outcome["attempts"]), ("error", 1))
        self.assertIsNone(outcome["hsi_result"])

    def test_delayed_permanent_failure_cannot_create_conflict_after_newer_confirm(self):
        from app import connectors
        sid = self.booking()
        old = self.claim(sid, "reassign")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT appointment_at FROM booking_requests WHERE id=%s", (sid,))
            expected_time = cur.fetchone()["appointment_at"]
        self.expire(old)
        current = db_hsi.hsi_outbox_claim_id(old["id"])
        # The current worker's remote success is acknowledged through the real
        # create/reassign transaction, then the old worker returns rejection.
        db_hsi.hsi_outbox_ack_appointment(current,
            {"estado": "ok", "appointment_id": "synthetic-a", "patient_id": "synthetic-p",
             "actual_turno_dt": expected_time},
            lambda *_: self.fail("no follow-up expected"))
        before = self.observed(sid, old)
        stale = self.replay(old, connectors.EmrRejected("synthetic slot unavailable",
                                                        code="slot_unavailable"))
        after = self.observed(sid, old)
        self.assertEqual(stale["state"], "confirmed")
        self.assertEqual(after, before)
        self.assertEqual(after[0]["status"], "aprobada")
        self.assertEqual(after[0]["appointment_at"], expected_time)

    def test_reset_before_reclaim_fences_skip_and_success_with_same_attempt_number(self):
        for outcome in (None, {"estado": "ok", "appointment_id": "synthetic-a",
                               "checkpoint": {"schedule_id": "synthetic-d"}}):
            with self.subTest(outcome=outcome):
                sid = self.booking()
                old = self.claim(sid, "cancel")
                self.expire(old)
                self.assertEqual(db_hsi.hsi_outbox_reset(old["id"])["state"], "pending")
                if outcome is None:
                    class Unconfigured(_Adapter):
                        def degrades_when_unconfigured(self):
                            return True
                    with patch.object(hsi_journal.connectors, "resolve", return_value=("fake", "explicit")), \
                         patch.object(hsi_journal.connectors, "adapter_for", return_value=Unconfigured(None)), \
                         patch.object(hsi_journal.connectors, "is_configured", return_value=False):
                        stale = hsi_journal._replay_one(old)
                else:
                    stale = self.replay(old, outcome)
                booking, outbox, conflicts, checkpoint, mirror = self.observed(sid, old)
                self.assertEqual(stale["state"], "pending")
                self.assertEqual((outbox["state"], outbox["attempts"]), ("pending", 1))
                self.assertEqual(booking["hsi_status"], "pendiente")
                self.assertEqual(conflicts, 0)
                self.assertIsNone(checkpoint)
                self.assertIsNone(mirror)

    def test_old_retry_skip_or_success_cannot_overwrite_newer_confirmed_effects(self):
        for old_outcome in (OSError("synthetic delayed outage"), None,
                            {"estado": "ok", "appointment_id": "synthetic-a",
                             "checkpoint": {"schedule_id": "stale-d"}}):
            with self.subTest(old_outcome=old_outcome):
                sid = self.booking()
                old = self.claim(sid, "cancel")
                self.expire(old)
                current = db_hsi.hsi_outbox_claim_id(old["id"])
                db_hsi.hsi_outbox_settle(current, "confirmed",
                    result={"estado": "ok", "appointment_id": "synthetic-a",
                            "checkpoint": {"schedule_id": "current-d"}},
                    booking_status="ok")
                before = self.observed(sid, old)
                if old_outcome is None:
                    class Unconfigured(_Adapter):
                        def degrades_when_unconfigured(self):
                            return True
                    with patch.object(hsi_journal.connectors, "resolve", return_value=("fake", "explicit")), \
                         patch.object(hsi_journal.connectors, "adapter_for", return_value=Unconfigured(None)), \
                         patch.object(hsi_journal.connectors, "is_configured", return_value=False):
                        stale = hsi_journal._replay_one(old)
                else:
                    stale = self.replay(old, old_outcome)
                self.assertEqual(stale["state"], "confirmed")
                self.assertEqual(self.observed(sid, old), before)
                self.assertEqual(before[3]["remote"]["schedule_id"], "current-d")
                self.assertEqual(before[4]["payload"]["schedule_id"], "current-d")

    def test_current_owner_conflict_and_attendance_success_settle_together(self):
        from app import connectors
        sid = self.booking()
        conflict = self.claim(sid, "reassign")
        self.assertEqual(self.replay(conflict, connectors.EmrRejected("synthetic rejection",
                       code="slot_unavailable"))["state"], "conflict")
        booking, outbox, conflicts, _, _ = self.observed(sid, conflict)
        self.assertEqual((booking["hsi_status"], outbox["state"], conflicts),
                         ("error", "conflict", 1))
        attendance = self.claim(sid, "attendance")
        result = {"estado": "ok", "patient_id": "synthetic-p",
                  "appointment_id": "synthetic-a",
                  "checkpoint": {"schedule_id": "synthetic-d", "patient_id": "synthetic-p"}}
        self.assertEqual(self.replay(attendance, result)["state"], "confirmed")
        booking, outbox, conflicts, checkpoint, mirror = self.observed(sid, attendance)
        self.assertEqual((booking["hsi_status"], booking["hsi_patient_id"],
                          booking["hsi_appointment_id"], outbox["state"]),
                         ("ok", "synthetic-p", "synthetic-a", "confirmed"))
        self.assertEqual(checkpoint["remote"], result["checkpoint"])
        self.assertEqual(mirror["payload"], result["checkpoint"])

    def test_settlement_sql_failure_rolls_back_booking_checkpoint_and_conflict(self):
        sid = self.booking()
        item = self.claim(sid, "attendance")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("""CREATE FUNCTION hsi_fence_fail() RETURNS trigger AS $$
                           BEGIN RAISE EXCEPTION 'synthetic settlement failure'; END;
                           $$ LANGUAGE plpgsql""")
            cur.execute("""CREATE TRIGGER hsi_fence_fail_update BEFORE UPDATE ON hsi_outbox
                           FOR EACH ROW EXECUTE FUNCTION hsi_fence_fail()""")
        try:
            result = {"estado": "ok", "appointment_id": "synthetic-a",
                      "checkpoint": {"schedule_id": "synthetic-d"}}
            with self.assertRaisesRegex(Exception, "synthetic settlement failure"):
                db_hsi.hsi_outbox_settle(item, "confirmed", result=result, booking_status="ok")
            booking, outbox, conflicts, checkpoint, mirror = self.observed(sid, item)
            self.assertEqual((booking["hsi_status"], outbox["state"], conflicts),
                             ("pendiente", "sending", 0))
            self.assertIsNone(checkpoint)
            self.assertIsNone(mirror)
        finally:
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("DROP TRIGGER hsi_fence_fail_update ON hsi_outbox")
                cur.execute("DROP FUNCTION hsi_fence_fail()")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("""CREATE FUNCTION hsi_fence_fail() RETURNS trigger AS $$
                           BEGIN RAISE EXCEPTION 'synthetic conflict failure'; END;
                           $$ LANGUAGE plpgsql""")
            cur.execute("""CREATE TRIGGER hsi_fence_fail_insert BEFORE INSERT ON hsi_sync_conflicts
                           FOR EACH ROW EXECUTE FUNCTION hsi_fence_fail()""")
        try:
            with self.assertRaisesRegex(Exception, "synthetic conflict failure"):
                db_hsi.hsi_outbox_settle(item, "conflict", error="synthetic rejection",
                                     booking_status="error")
            booking, outbox, conflicts, _, _ = self.observed(sid, item)
            self.assertEqual((booking["hsi_status"], outbox["state"], conflicts),
                             ("pendiente", "sending", 0))
        finally:
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("DROP TRIGGER hsi_fence_fail_insert ON hsi_sync_conflicts")
                cur.execute("DROP FUNCTION hsi_fence_fail()")


if __name__ == "__main__":
    unittest.main()
