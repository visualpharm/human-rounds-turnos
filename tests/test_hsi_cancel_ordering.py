"""Linked cancellation ordering against a disposable PostgreSQL database."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from psycopg.types.json import Json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import connectors
from app.core import db
from app.core import db_booking
from app.core import db_hsi
from app.modules.turnos import hsi_journal
import _dbtest

_database = _dbtest.PerModuleDb("sana_turnos_test_hsi_cancel_order")


def setUpModule():
    _database.set_up()
    if not _database.have_db:
        raise RuntimeError("Disposable PostgreSQL is required for cancel ordering proof")


def tearDownModule():
    _database.tear_down()


class _Adapter:
    def __init__(self, outcome):
        self.outcome = outcome

    def degrades_when_unconfigured(self):
        return False

    def execute_intent(self, *_args):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return dict(self.outcome)


class _ProbeAdapter(_Adapter):
    def __init__(self, probe):
        super().__init__({"estado": "ok"})
        self.probe = probe
        self.executions = 0
        self.probes = 0

    def probe_intent(self, _key):
        self.probes += 1
        if isinstance(self.probe, BaseException):
            raise self.probe
        return self.probe

    def execute_intent(self, *_args):
        self.executions += 1
        raise AssertionError("cancelled reassign must never execute")


class _ObservationAdapter(_ProbeAdapter):
    def __init__(self, observation):
        super().__init__(None)  # Legacy connector: no key probe.
        self.observation = observation
        self.observations = 0

    def observe_intent(self, operation, payload):
        self.observations += 1
        if isinstance(self.observation, BaseException):
            raise self.observation
        if operation != "reassign":
            raise AssertionError("unexpected observation operation")
        return self.observation


class TestLinkedCancelOrdering(unittest.TestCase):
    def setUp(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE booking_requests CASCADE")
            cur.execute("TRUNCATE hsi_outbox,hsi_sync_conflicts,hsi_checkpoints,hsi_appointments RESTART IDENTITY")
        self.a_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(second=0, microsecond=0)
        self.b_time = self.a_time + timedelta(days=1)
        self.a = {"appointment_id": "remote-12", "schedule_id": "diary-9",
                  "patient_id": "patient-55", "scheduled_at": self.a_time.isoformat(),
                  "state": "scheduled", "version": "a"}
        self.b = dict(self.a, scheduled_at=self.b_time.isoformat(), version="b")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO booking_requests
                           (dni,first_name,last_name,status,appointment_at,hsi_status,
                            hsi_appointment_id)
                           VALUES ('99900018','Synthetic','Ordering','aprobada',%s,'pendiente',
                                   'remote-12') RETURNING id""", (self.b_time,))
            self.sid = cur.fetchone()["id"]
            cur.execute("""INSERT INTO hsi_checkpoints(entity_type,entity_id,remote)
                           VALUES ('appointment','remote-12',%s)""",
                        (Json(self.a),))
        self.r = db_hsi.hsi_outbox_enqueue("appointment", str(self.sid), "reassign",
             {"solicitud_id": self.sid, "appointment_id": "remote-12",
              "checkpoint": self.a, "when": self.b_time.isoformat()}, f"R-{self.sid}")
        self.r = db_hsi.hsi_outbox_claim_id(self.r["id"])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE booking_requests SET status='cancelada' WHERE id=%s", (self.sid,))
        self.c1 = db_hsi.hsi_outbox_enqueue("appointment", str(self.sid), "cancel",
             {"solicitud_id": self.sid, "appointment_id": "remote-12",
              "checkpoint": self.a, "operation": "cancel"}, f"C1-{self.sid}")
        self.c1 = db_hsi.hsi_outbox_claim_id(self.c1["id"])

    def replay(self, item, outcome):
        with patch.object(hsi_journal.connectors, "resolve", return_value=("fake", "explicit")), \
             patch.object(hsi_journal.connectors, "adapter_for", return_value=_Adapter(outcome)):
            return hsi_journal._replay_one(item)

    def replay_with_adapter(self, item, adapter):
        with patch.object(hsi_journal.connectors, "resolve", return_value=("fake", "explicit")), \
             patch.object(hsi_journal.connectors, "adapter_for", return_value=adapter):
            return hsi_journal._replay_one(item)

    def reclaim_r(self):
        # The first R lease may have been killed after remote acceptance.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE hsi_outbox SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                        (self.r["id"],))
        reclaimed = db_hsi.hsi_outbox_claim_id(self.r["id"])
        self.assertEqual(reclaimed["attempts"], 2)
        return reclaimed

    def remote_b_result(self):
        return {"estado": "ok", "appointment_id": "remote-12",
                "patient_id": "patient-55", "actual_turno_dt": self.b_time.isoformat(),
                "checkpoint": self.b}

    def ack_r(self):
        result = {"estado": "ok", "appointment_id": "remote-12", "patient_id": "patient-55",
                  "actual_turno_dt": self.b_time.isoformat(), "checkpoint": self.b}
        db_hsi.hsi_outbox_ack_appointment(self.r, result,
             lambda row, op, checkpoint: hsi_journal._ack_followup(self.r, row, op, checkpoint))
        rows = db_hsi.hsi_outbox_by_entity(str(self.sid), states=("pending", "sending", "error", "conflict", "confirmed"))
        return next(row for row in rows if row["operation"] == "cancel" and row["id"] != self.c1["id"])

    def observed(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status,hsi_status,hsi_appointment_id FROM booking_requests WHERE id=%s", (self.sid,))
            booking = cur.fetchone()
            cur.execute("SELECT remote FROM hsi_checkpoints WHERE entity_id='remote-12'")
            checkpoint = cur.fetchone()["remote"]
            cur.execute("SELECT payload FROM hsi_appointments WHERE hsi_appointment_id='remote-12'")
            mirror = cur.fetchone()
            cur.execute("SELECT count(*) AS n FROM hsi_sync_conflicts WHERE resolved_at IS NULL")
            open_conflicts = cur.fetchone()["n"]
        return booking, checkpoint, mirror, open_conflicts

    def assert_pending_b(self):
        booking, checkpoint, mirror, _ = self.observed()
        self.assertEqual((booking["status"], booking["hsi_appointment_id"], booking["hsi_status"]),
                         ("cancelada", "remote-12", "pendiente"))
        self.assertEqual(checkpoint["scheduled_at"], self.b["scheduled_at"])
        self.assertEqual(mirror["payload"]["scheduled_at"], self.b["scheduled_at"])

    def test_c1_success_before_r_ack_never_reports_ok_and_c2_is_durable(self):
        self.assertEqual(self.replay(self.c1, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.a, state="cancelled")})["state"], "error")
        self.assertEqual(self.observed()[0]["hsi_status"], "pendiente")
        self.assertEqual(self.observed()[1]["state"], "scheduled")
        c2 = self.ack_r()
        self.assertEqual(c2["state"], "pending")
        self.assert_pending_b()

    def test_r_ack_before_c1_success_cannot_regress_b_or_c2(self):
        c2 = self.ack_r()
        self.assertEqual(self.replay(self.c1, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.a, state="cancelled")})["state"], "confirmed")
        self.assert_pending_b()
        self.assertEqual(db_hsi.hsi_outbox_get(self.c1["id"])["hsi_result"]["estado"], "superseded")
        self.assertEqual(db_hsi.hsi_outbox_get(c2["id"])["state"], "pending")

    def test_c2_error_retry_then_success_and_late_c1(self):
        c2 = self.ack_r()
        claimed = db_hsi.hsi_outbox_claim_id(c2["id"])
        self.assertEqual(self.replay(claimed, OSError("synthetic transport outage"))["state"], "error")
        self.assert_pending_b()
        db_hsi.hsi_outbox_reset(c2["id"])
        claimed = db_hsi.hsi_outbox_claim_id(c2["id"])
        self.assertEqual(self.replay(claimed, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.b, state="cancelled")})["state"], "confirmed")
        self.assertEqual(self.observed()[0]["hsi_status"], "ok")
        self.assertEqual(self.replay(self.c1, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.a, state="cancelled")})["state"], "confirmed")
        booking, checkpoint, mirror, _ = self.observed()
        self.assertEqual((booking["status"], booking["hsi_status"]), ("cancelada", "ok"))
        self.assertEqual((checkpoint["scheduled_at"], checkpoint["state"]), (self.b["scheduled_at"], "cancelled"))
        self.assertEqual(mirror["payload"]["scheduled_at"], self.b["scheduled_at"])

    def test_c2_conflict_is_visible_and_late_c1_cannot_clear_it(self):
        c2 = self.ack_r()
        claimed = db_hsi.hsi_outbox_claim_id(c2["id"])
        self.assertEqual(self.replay(claimed, connectors.EmrConflict("synthetic drift"))["state"], "conflict")
        self.assertEqual(self.observed()[0]["hsi_status"], "error")
        self.replay(self.c1, {"estado": "ok", "appointment_id": "remote-12",
                              "checkpoint": dict(self.a, state="cancelled")})
        self.assertEqual(self.observed()[0]["hsi_status"], "error")
        self.assertEqual(db_hsi.hsi_outbox_get(c2["id"])["state"], "conflict")
        self.assertEqual(self.observed()[1]["scheduled_at"], self.b["scheduled_at"])

    def test_c1_conflict_before_r_ack_is_superseded_with_queued_c2(self):
        self.assertEqual(self.replay(self.c1, connectors.EmrConflict("hsi_checkpoint_drift"))["state"], "conflict")
        self.assertEqual(self.observed()[3], 1)
        c2 = self.ack_r()
        self.assert_pending_b()
        self.assertEqual(db_hsi.hsi_outbox_get(self.c1["id"])["hsi_result"]["estado"], "superseded")
        self.assertEqual(db_hsi.hsi_outbox_get(c2["id"])["state"], "pending")
        self.assertEqual(self.observed()[3], 0)

    def test_later_inserted_stale_cancel_cannot_supersede_current_correction(self):
        c2 = self.ack_r()
        c3 = db_hsi.hsi_outbox_enqueue("appointment", str(self.sid), "cancel",
             {"solicitud_id": self.sid, "appointment_id": "remote-12",
              "checkpoint": self.a, "operation": "cancel"}, f"C3-stale-{self.sid}")
        c3 = db_hsi.hsi_outbox_claim_id(c3["id"])
        self.assertGreater(c3["id"], c2["id"])
        self.assertEqual(self.replay(c3, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.a, state="cancelled")})["state"], "confirmed")
        self.assertEqual(db_hsi.hsi_outbox_get(c3["id"])["hsi_result"]["estado"], "superseded")
        self.assertEqual(db_hsi.hsi_outbox_get(c2["id"])["state"], "pending")
        self.assert_pending_b()
        claimed = db_hsi.hsi_outbox_claim_id(c2["id"])
        self.assertEqual(self.replay(claimed, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.b, state="cancelled")})["state"], "confirmed")
        self.assertEqual(self.observed()[0]["hsi_status"], "ok")
        self.assertEqual(self.observed()[1]["scheduled_at"], self.b["scheduled_at"])

    def _late_c1_outcome_after_confirmed_c2(self, outcome):
        c2 = self.ack_r()
        claimed = db_hsi.hsi_outbox_claim_id(c2["id"])
        self.replay(claimed, {"estado": "ok", "appointment_id": "remote-12",
                              "checkpoint": dict(self.b, state="cancelled")})
        self.assertEqual(self.replay(self.c1, outcome)["state"], "confirmed")
        self.assertEqual(self.observed()[0]["hsi_status"], "ok")
        self.assertEqual(self.observed()[1]["scheduled_at"], self.b["scheduled_at"])
        self.assertEqual(self.observed()[3], 0)

    def test_late_c1_failure_cannot_reopen_confirmed_c2(self):
        self._late_c1_outcome_after_confirmed_c2(OSError("synthetic late failure"))

    def test_late_c1_conflict_cannot_reopen_confirmed_c2(self):
        self._late_c1_outcome_after_confirmed_c2(connectors.EmrConflict("synthetic late drift"))

    def test_same_slot_new_version_correction_remains_authoritative(self):
        self.b_time = self.a_time
        self.b = dict(self.a, version="b")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE booking_requests SET appointment_at=%s WHERE id=%s", (self.b_time, self.sid))
        c2 = self.ack_r()
        self.replay(self.c1, {"estado": "ok", "appointment_id": "remote-12",
                              "checkpoint": dict(self.a, state="cancelled")})
        self.assertEqual(db_hsi.hsi_outbox_get(self.c1["id"])["hsi_result"]["estado"], "superseded")
        self.assertEqual(db_hsi.hsi_outbox_get(c2["id"])["state"], "pending")
        self.assertEqual(self.observed()[1]["version"], "b")

    def test_real_replay_holds_remote_r_before_patient_cancel_and_ack(self):
        # Hold an actual replay after the fake remote accepted R, before its
        # local acknowledgement. C1 then settles while R still owns its lease.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM hsi_outbox WHERE id=%s", (self.c1["id"],))
            cur.execute("UPDATE booking_requests SET status='aprobada' WHERE id=%s", (self.sid,))
        accepted, release = threading.Event(), threading.Event()
        worker = [None]

        def hold():
            if threading.current_thread() is worker[0]:
                accepted.set()
                if not release.wait(10):
                    raise AssertionError("synthetic R release timed out")

        with patch.object(hsi_journal, "_test_hold_after_remote_accept", side_effect=hold):
            with ThreadPoolExecutor(max_workers=1) as pool:
                def run_r():
                    worker[0] = threading.current_thread()
                    return self.replay(self.r, {"estado": "ok", "appointment_id": "remote-12",
                                                "patient_id": "patient-55",
                                                "actual_turno_dt": self.b_time.isoformat(),
                                                "checkpoint": self.b})
                future = pool.submit(run_r)
                try:
                    self.assertTrue(accepted.wait(10))
                    with db._connect() as conn, conn.cursor() as cur:
                        cur.execute("UPDATE booking_requests SET status='cancelada' WHERE id=%s", (self.sid,))
                    c1 = db_hsi.hsi_outbox_enqueue("appointment", str(self.sid), "cancel",
                         {"solicitud_id": self.sid, "appointment_id": "remote-12",
                          "checkpoint": self.a, "operation": "cancel"}, f"late-C1-{self.sid}")
                    c1 = db_hsi.hsi_outbox_claim_id(c1["id"])
                    self.assertEqual(self.replay(c1, {"estado": "ok", "appointment_id": "remote-12",
                                     "checkpoint": dict(self.a, state="cancelled")})["state"], "error")
                    self.assertEqual(self.observed()[0]["hsi_status"], "pendiente")
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10)["state"], "confirmed")
        self.assert_pending_b()
        rows = db_hsi.hsi_outbox_by_entity(str(self.sid), states=("pending", "sending", "error", "confirmed"))
        self.assertEqual({row["payload"].get("appointment_id") for row in rows}, {"remote-12"})
        self.assertNotIn("create", {row["operation"] for row in rows})

    def test_c1_success_converges_when_queued_r_skips_without_remote_move(self):
        self.assertEqual(self.replay(self.c1, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.a, state="cancelled")})["state"], "error")
        self.assertEqual(self.replay(self.r, {"estado": "ok"})["state"], "confirmed")
        self.assertEqual(db_hsi.hsi_outbox_get(self.r["id"])["hsi_result"]["estado"], "skip")
        self.assertEqual(self.observed()[0]["hsi_status"], "pendiente")
        db_hsi.hsi_outbox_reset(self.c1["id"])
        retry = db_hsi.hsi_outbox_claim_id(self.c1["id"])
        self.assertEqual(self.replay(retry, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.a, state="cancelled")})["state"], "confirmed")
        self.assertEqual(self.observed()[0]["hsi_status"], "ok")
        self.assertEqual(self.observed()[1]["state"], "cancelled")

    def test_reclaimed_r_adopts_remote_b_and_queues_c2_without_replaying_move(self):
        # Simulate the real boundary: the fake remote accepts R, then the
        # process dies before local acknowledgement. C1 follows cancellation.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE booking_requests SET status='aprobada' WHERE id=%s", (self.sid,))
        accepted = self.remote_b_result()
        with patch.object(hsi_journal, "_test_hold_after_remote_accept", side_effect=KeyboardInterrupt), \
             patch.object(hsi_journal.connectors, "resolve", return_value=("fake", "explicit")), \
             patch.object(hsi_journal.connectors, "adapter_for", return_value=_Adapter(accepted)):
            with self.assertRaises(KeyboardInterrupt):
                hsi_journal._replay_one(self.r)
        self.assertEqual(db_hsi.hsi_outbox_get(self.r["id"])["state"], "sending")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE booking_requests SET status='cancelada' WHERE id=%s", (self.sid,))
        self.assertEqual(self.replay(self.c1, {"estado": "ok", "appointment_id": "remote-12",
                                      "checkpoint": dict(self.a, state="cancelled")})["state"], "error")
        adapter = _ProbeAdapter({"found": True, "result": accepted})
        reclaimed = self.reclaim_r()
        self.assertEqual(self.replay_with_adapter(reclaimed, adapter)["state"], "confirmed")
        self.assertEqual((adapter.probes, adapter.executions), (1, 0))
        self.assert_pending_b()
        rows = db_hsi.hsi_outbox_by_entity(str(self.sid), states=("pending", "confirmed"))
        c2 = next(row for row in rows if row["operation"] == "cancel" and row["id"] != self.c1["id"])
        self.assertEqual(c2["payload"]["checkpoint"], self.b)
        self.assertEqual(c2["payload"]["predecessor_intent_id"], self.r["id"])
        self.assertEqual(db_hsi.hsi_outbox_get(self.c1["id"])["hsi_result"]["estado"], "superseded")

    def test_legacy_observation_adopts_b_and_queues_corrective_cancel(self):
        adapter = _ObservationAdapter(self.remote_b_result())
        self.assertEqual(self.replay_with_adapter(self.reclaim_r(), adapter)["state"], "confirmed")
        self.assertEqual((adapter.probes, adapter.observations, adapter.executions), (1, 1, 0))
        self.assert_pending_b()
        rows = db_hsi.hsi_outbox_by_entity(str(self.sid), states=("pending", "confirmed"))
        c2 = next(row for row in rows if row["operation"] == "cancel" and row["id"] != self.c1["id"])
        self.assertEqual(c2["payload"]["checkpoint"], self.b)
        self.assertEqual(c2["payload"]["predecessor_intent_id"], self.r["id"])

    def test_legacy_absence_and_drift_never_skip_or_repeat_move(self):
        invalid = (None, {"found": False}, dict(self.remote_b_result(), checkpoint=self.a),
                   OSError("synthetic diary outage"))
        for answer in invalid:
            with self.subTest(answer=answer):
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE hsi_outbox SET state='sending',attempts=2 WHERE id=%s", (self.r["id"],))
                adapter = _ObservationAdapter(answer)
                self.assertEqual(self.replay_with_adapter(db_hsi.hsi_outbox_get(self.r["id"]), adapter)["state"], "error")
                self.assertEqual((adapter.probes, adapter.observations, adapter.executions), (1, 1, 0))
                self.assertEqual(self.observed()[1], self.a)

    def test_key_probe_error_or_malformed_response_never_uses_observation(self):
        for answer in (OSError("synthetic probe outage"), {}, {"found": True}):
            with self.subTest(answer=answer):
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE hsi_outbox SET state='sending',attempts=2 WHERE id=%s", (self.r["id"],))
                adapter = _ObservationAdapter(self.remote_b_result())
                adapter.probe = answer
                self.assertEqual(self.replay_with_adapter(db_hsi.hsi_outbox_get(self.r["id"]), adapter)["state"], "error")
                self.assertEqual((adapter.probes, adapter.observations, adapter.executions), (1, 0, 0))

    def test_real_legacy_adapter_read_adopts_b_under_fenced_ack(self):
        from app.connectors.hsi import adapter as legacy

        target = self.b_time.astimezone(timezone(timedelta(hours=-3)))
        raw = {"id": "remote-12", "diaryId": "diary-9", "patientId": "patient-55",
               "date": target.date().isoformat(), "hour": target.strftime("%H:%M:%S"),
               "appointmentStateId": 1, "updatedOn": "b"}

        class Wire:
            class HsiClient:
                def login(self):
                    pass

                def appointments_for_diary(self, diary_id, start, end):
                    if (diary_id, start, end) != ("diary-9", raw["date"], raw["date"]):
                        raise AssertionError("wrong target diary read")
                    return [raw]

                def reassign_appointment(self, *_args):
                    raise AssertionError("update-date must never be repeated")

        with patch.object(legacy, "_impl_module", return_value=Wire):
            settled = self.replay_with_adapter(self.reclaim_r(), legacy.HsiConnector())
        self.assertEqual(settled["state"], "confirmed")
        booking, checkpoint, mirror, _ = self.observed()
        self.assertEqual((booking["status"], booking["hsi_status"]), ("cancelada", "pendiente"))
        self.assertEqual(datetime.fromisoformat(checkpoint["scheduled_at"]), self.b_time)
        self.assertEqual(datetime.fromisoformat(mirror["payload"]["scheduled_at"]), self.b_time)
        rows = db_hsi.hsi_outbox_by_entity(str(self.sid), states=("pending", "confirmed"))
        c2 = next(row for row in rows if row["operation"] == "cancel" and row["id"] != self.c1["id"])
        self.assertEqual(c2["payload"]["checkpoint"]["scheduled_at"], target.isoformat())

    def test_reclaimed_r_definitive_negative_skips_without_move(self):
        adapter = _ProbeAdapter({"found": False})
        self.assertEqual(self.replay_with_adapter(self.reclaim_r(), adapter)["state"], "confirmed")
        self.assertEqual((adapter.probes, adapter.executions), (1, 0))
        self.assertEqual(db_hsi.hsi_outbox_get(self.r["id"])["hsi_result"]["estado"], "skip")
        self.assertEqual(self.observed()[1], self.a)
        self.assertIsNone(self.observed()[2])

    def test_reclaimed_r_accepts_equivalent_utc_slot(self):
        result = self.remote_b_result()
        result["actual_turno_dt"] = self.b_time.astimezone(timezone(timedelta(hours=-3))).isoformat()
        result["checkpoint"] = dict(self.b, scheduled_at=self.b_time.astimezone(timezone.utc).isoformat())
        adapter = _ProbeAdapter({"found": True, "result": result})
        self.assertEqual(self.replay_with_adapter(self.reclaim_r(), adapter)["state"], "confirmed")
        self.assertEqual(adapter.executions, 0)
        self.assertEqual(self.observed()[1]["scheduled_at"], result["checkpoint"]["scheduled_at"])

    def test_reclaimed_r_unknown_and_invalid_probes_remain_retryable(self):
        invalid = [None, {}, {"found": True, "result": {"estado": "ok"}},
                   {"found": True, "result": dict(self.remote_b_result(), checkpoint=self.a)},
                   {"found": True, "result": dict(self.remote_b_result(), appointment_id="other")},
                   {"found": True, "result": dict(self.remote_b_result(), checkpoint=dict(self.b, state="cancelled"))},
                   {"found": True, "result": dict(self.remote_b_result(), checkpoint=dict(self.b, state="blocked"))},
                   {"found": True, "result": dict(self.remote_b_result(), checkpoint=dict(self.b, schedule_id="other"))},
                   {"found": True, "result": dict(self.remote_b_result(), checkpoint=dict(self.b, patient_id="other"))},
                   {"found": True, "result": dict(self.remote_b_result(), actual_turno_dt=self.a_time.isoformat())},
                   OSError("probe down")]
        for answer in invalid:
            with self.subTest(answer=answer):
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE hsi_outbox SET state='sending',attempts=2 WHERE id=%s", (self.r["id"],))
                adapter = _ProbeAdapter(answer)
                row = db_hsi.hsi_outbox_get(self.r["id"])
                self.assertEqual(self.replay_with_adapter(row, adapter)["state"], "error")
                self.assertEqual((adapter.probes, adapter.executions), (1, 0))
                self.assertEqual(self.observed()[1], self.a)
                self.assertIsNone(self.observed()[2])
                self.assertFalse(any(item["operation"] == "cancel" and item["id"] != self.c1["id"]
                                     for item in db_hsi.hsi_outbox_by_entity(str(self.sid),
                                                                           states=("pending", "sending", "error"))))

    def test_reclaimed_r_unreadable_or_missing_booking_never_replays(self):
        for unavailable in (OSError("synthetic booking read outage"), None):
            with self.subTest(unavailable=unavailable):
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE hsi_outbox SET state='sending',attempts=2 WHERE id=%s", (self.r["id"],))
                adapter = _ProbeAdapter(None)
                local_read = (patch.object(db_booking, "get_booking_request", side_effect=unavailable)
                              if unavailable else patch.object(db_booking, "get_booking_request", return_value=None))
                with local_read:
                    row = db_hsi.hsi_outbox_get(self.r["id"])
                    settled = self.replay_with_adapter(row, adapter)
                self.assertEqual(settled["state"], "error")
                self.assertIn("local booking unavailable", settled["last_error"])
                self.assertEqual((adapter.probes, adapter.executions), (0, 0))
                self.assertEqual(self.observed()[1], self.a)
                self.assertIsNone(self.observed()[2])

    def test_first_r_missing_booking_never_replays(self):
        adapter = _ProbeAdapter(None)
        with patch.object(db_booking, "get_booking_request", return_value=None):
            settled = self.replay_with_adapter(self.r, adapter)
        self.assertEqual(settled["state"], "error")
        self.assertEqual((adapter.probes, adapter.executions), (0, 0))

    def test_reclaimed_r_ack_failure_rolls_back_b_and_c2(self):
        reclaimed = self.reclaim_r()
        adapter = _ProbeAdapter({"found": True, "result": self.remote_b_result()})
        with patch.object(hsi_journal, "_ack_followup", side_effect=RuntimeError("insert failed")):
            self.assertEqual(self.replay_with_adapter(reclaimed, adapter)["state"], "error")
        self.assertEqual(adapter.executions, 0)
        self.assertEqual(self.observed()[1], self.a)
        self.assertIsNone(self.observed()[2])
        self.assertFalse(any(item["operation"] == "cancel" and item["id"] != self.c1["id"]
                             for item in db_hsi.hsi_outbox_by_entity(str(self.sid), states=("pending", "sending", "error"))))

    def test_reclaimed_r_stale_lease_cannot_adopt_probe_or_queue_c2(self):
        old = self.reclaim_r()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE hsi_outbox SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                        (self.r["id"],))
        newer = db_hsi.hsi_outbox_claim_id(self.r["id"])
        self.assertEqual(newer["attempts"], 3)
        adapter = _ProbeAdapter({"found": True, "result": self.remote_b_result()})
        self.assertEqual(self.replay_with_adapter(old, adapter)["attempts"], 3)
        self.assertEqual((adapter.probes, adapter.executions), (1, 0))
        self.assertEqual(db_hsi.hsi_outbox_get(self.r["id"])["state"], "sending")
        self.assertEqual(self.observed()[1], self.a)
        self.assertIsNone(self.observed()[2])
