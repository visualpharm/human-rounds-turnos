"""Focused durable-journal tests for hsi_sync (no real HSI/Postgres needed)."""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config, db  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.core import db_values  # noqa: E402
import _clock  # noqa: E402
import _dbowner  # noqa: E402
from app.modules.turnos import hsi_journal as hsi_sync  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
import _env  # noqa: E402


def tearDownModule():
    # "No Postgres needed" must also mean "no pool left open": a stray real
    # db call (the 2026-08-09 bug below) or a future one must never leave a
    # ConnectionPool for interpreter shutdown to trip over (Python 3.14's
    # PythonFinalizationError in ConnectionPool.__del__).
    db.close_pools()


class JournalBase(unittest.TestCase):
    def setUp(self):
        self.originals = {}
        self.addCleanup(config._reset_cache_para_tests)    # after the environment is put back
        _env.set_env(self, TURNOS_SKIP_HSI=None, CONNECTOR_EMR_SYNC="hsi")
        config._reset_cache_para_tests()
        # No earlier create in flight unless a test says so: enqueue_appointment
        # looks one up before minting a key (test_hsi_unlinked_move proves it
        # on a real database).
        self.patch("hsi_outbox_outstanding", lambda *args: None)

    def patch(self, name, value):
        owner = _dbowner.owner_of(name)
        self.originals.setdefault(name, (owner, getattr(owner, name)))
        setattr(owner, name, value)

    def capture_settlement(self, calls):
        def settle(item, state, **kwargs):
            calls.append((item, state, kwargs))
            return {"state": state, **(kwargs.get("result") or {})}
        self.patch("hsi_outbox_settle", settle)

    def tearDown(self):
        for name, (owner, value) in self.originals.items():
            setattr(owner, name, value)


class TestOutboxReplay(JournalBase):
    def test_unconfigured_enterprise_bridge_confirms_a_skip_without_retrying(self):
        """An intentional but incomplete Enterprise selection is degradation,
        not an HSI-shaped network error that fills the durable retry queue."""
        settled = []
        self.capture_settlement(settled)

        class Bridge:
            def degrades_when_unconfigured(self):
                return True

        item = {"id": 123, "operation": "create", "attempts": 1,
                "payload": {"solicitud_id": 88},
                "idempotency_key": "core-key"}
        self.patch("get_booking_request", lambda sid: {
            "id": sid, "estado": "aprobada", "hsi_appointment_id": None})
        with patch.object(hsi_sync.connectors, "resolve", return_value=("emr_bridge", "explicit")), \
             patch.object(hsi_sync.connectors, "adapter_for", return_value=Bridge()), \
             patch.object(hsi_sync.connectors, "is_configured", return_value=False):
            self.assertEqual(hsi_sync._replay_one(item)["state"], "confirmed")
        self.assertEqual(settled[0][0]["id"], 123)
        self.assertEqual(settled[0][1], "confirmed")
        self.assertEqual(settled[0][2]["booking_status"], "skip")
        self.assertEqual(settled[0][2]["result"]["estado"], "skip")

    def test_idempotency_time_is_normalized_to_hospital_timezone(self):
        same = [
            hsi_sync._jsonable(datetime.fromisoformat("2026-08-01T09:00:00-03:00")),
            hsi_sync._jsonable(datetime.fromisoformat("2026-08-01T08:00:00-04:00")),
            hsi_sync._jsonable(datetime.fromisoformat("2026-08-01T12:00:00+00:00")),
        ]
        self.assertEqual(len(set(same)), 1)
        self.assertEqual(same[0], "2026-08-01T09:00:00-03:00")

    def test_deterministic_bridge_rejection_becomes_a_conflict_not_a_retry(self):
        from app import connectors

        item = {"id": 124, "operation": "create", "attempts": 1,
                "payload": {"solicitud_id": 89}, "idempotency_key": "core-key"}
        settled = []
        self.capture_settlement(settled)

        class Bridge:
            def degrades_when_unconfigured(self):
                return False

            def execute_intent(self, *_args):
                raise connectors.EmrRejected(
                    "slot unavailable", code="slot_unavailable")

        with patch.object(hsi_sync.connectors, "resolve",
                          return_value=("emr_bridge", "explicit")), \
             patch.object(hsi_sync.connectors, "adapter_for", return_value=Bridge()):
            self.assertEqual(hsi_sync._replay_one(item), {"state": "conflict"})
        self.assertEqual(len(settled), 1)
        self.assertEqual((settled[0][0]["id"], settled[0][1],
                          settled[0][2]["booking_status"]), (124, "conflict", "error"))

    def test_backfill_adopts_legacy_pending_rows_with_deterministic_intents(self):
        rows = [{"id": 70, "turno_dt": datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
                 "dni": "303", "nombre": "C", "apellido": "Tres", "nacimiento": "1990-01-01",
                 "sexo": "X", "servicio_id": "cardio", "hsi_estado": "error"},
                {"id": 71, "turno_dt": datetime(2026, 8, 1, 10, tzinfo=timezone.utc),
                 "dni": "304", "nombre": "D", "apellido": "Cuatro", "nacimiento": "1991-01-01",
                 "sexo": "F", "servicio_id": "cardio", "hsi_estado": "pendiente"}]
        self.patch("booking_requests_hsi_pending", lambda desde=None: rows)
        queued = []
        self.patch("hsi_outbox_enqueue", lambda *args: queued.append(args))
        # The real seam: appointment_payload() resolves the specialty via
        # booking_requests._service_name (the old sys.modules["solicitudes"]
        # stub had gone dead — hsi_sync stopped reading it, so the REAL
        # lookup ran, walked catalog.services() and silently opened a
        # ConnectionPool against the dev DATABASE_URL from .env in a suite
        # that promises "no real Postgres needed").
        with patch.object(booking_requests, "_service_name",
                          lambda x: "Cardiología"):
            self.assertEqual(hsi_sync.backfill_pending(), 2)
        self.assertEqual([q[1] for q in queued], ["70", "71"])
        self.assertEqual(len({q[4] for q in queued}), 2)

    def test_backfill_never_recreates_a_linked_hsi_appointment(self):
        rows = [
            {"id": 72, "estado": "aprobada", "turno_dt": "2026-08-01T11:00:00-03:00",
             "hsi_appointment_id": "501"},
            {"id": 73, "estado": "cancelada", "turno_dt": "2026-08-01T11:30:00-03:00",
             "hsi_appointment_id": "502"},
        ]
        self.patch("booking_requests_hsi_pending", lambda desde=None: rows)
        self.patch("hsi_checkpoint_get", lambda *args: {"appointment_id": args[1],
                                                         "schedule_id": "schedule-1",
                                                         "scheduled_at": "2026-08-01T09:00:00-03:00",
                                                         "state": "scheduled"})
        queued = []
        self.patch("hsi_outbox_enqueue", lambda *args: queued.append(("cancel", args)))
        def locked_reassign(sid, when, build):
            payload, key = build({"id": sid, "hsi_appointment_id": "501",
                                  "turno_dt": when},
                                 {"appointment_id": "501", "schedule_id": "schedule-1"}, 72)
            queued.append(("reassign", payload, key))
        self.patch("hsi_outbox_enqueue_reassign", locked_reassign)
        self.assertEqual(hsi_sync.backfill_pending(), 2)
        self.assertEqual([q[0] for q in queued], ["reassign", "cancel"])
        self.assertEqual(queued[0][1]["appointment_id"], "501")
        self.assertEqual(queued[0][1]["move_generation"], 72)
        self.assertEqual(queued[1][1][2], "cancel")

    def test_one_row_to_intent_mapping_is_shared_by_backfill_and_fallback(self):
        """Linked fallback rows must not fork into a separate create policy."""
        rows = [
            {"id": 74, "estado": "aprobada", "turno_dt": "2026-08-01T11:00:00-03:00",
             "hsi_appointment_id": "503"},
            {"id": 75, "estado": "cancelada", "turno_dt": "2026-08-01T11:30:00-03:00",
             "hsi_appointment_id": "504"},
        ]
        self.patch("hsi_checkpoint_get", lambda *args: {"id": args[1]})
        queued = []
        def enqueue(*args):
            queued.append((args[2], args[3]))
            return {"id": len(queued)}
        self.patch("hsi_outbox_enqueue", enqueue)
        def locked_reassign(sid, when, build):
            payload, key = build({"id": sid, "hsi_appointment_id": "503",
                                  "turno_dt": when}, {"id": "503"}, 74)
            queued.append(("reassign", payload))
            return {"id": len(queued), "idempotency_key": key}
        self.patch("hsi_outbox_enqueue_reassign", locked_reassign)
        self.patch("booking_requests_hsi_pending", lambda desde=None: rows)
        self.assertEqual(hsi_sync.backfill_pending(), 2)
        self.assertEqual([entry[0] for entry in queued], ["reassign", "cancel"])
        self.assertEqual(queued[0][1]["appointment_id"], "503")
        self.assertEqual(queued[0][1]["move_generation"], 74)
        self.assertEqual(queued[1][1]["appointment_id"], "504")

    def test_enqueue_and_replay_row_uses_one_exact_intent(self):
        row = {"id": 76, "turno_dt": "2026-08-01T11:00:00-03:00"}
        self.patch("hsi_outbox_enqueue", lambda *args: {"id": 987})
        replayed = []
        original = hsi_sync.replay_item
        hsi_sync.replay_item = lambda id_: replayed.append(id_) or {"state": "confirmed"}
        self.addCleanup(lambda: setattr(hsi_sync, "replay_item", original))
        with patch.object(booking_requests, "_service_name", lambda x: x):
            self.assertEqual(hsi_sync.enqueue_and_replay_row(row), {"state": "confirmed"})
        self.assertEqual(replayed, [987])

    def test_linked_cancelled_fallback_selects_cancel_never_create(self):
        row = {"id": 77, "estado": "cancelada", "hsi_estado": "error",
               "hsi_appointment_id": "505", "turno_dt": "2026-08-01T12:00:00-03:00"}
        with patch.object(hsi_sync, "enqueue_cancel", return_value={"id": 991}) as cancel, \
             patch.object(hsi_sync, "enqueue_reassign") as reassign, \
             patch.object(hsi_sync, "enqueue_appointment",
                          side_effect=AssertionError("linked cancel must never create")) as create:
            self.assertEqual(hsi_sync.intent_for_row(row), {"id": 991})
        cancel.assert_called_once_with(row)
        reassign.assert_not_called()
        create.assert_not_called()

    def test_linked_approved_error_fallback_selects_reassign_never_create(self):
        row = {"id": 78, "estado": "aprobada", "hsi_estado": "error",
               "hsi_appointment_id": "506", "turno_dt": "2026-08-01T12:30:00-03:00"}
        with patch.object(hsi_sync, "enqueue_reassign", return_value={"id": 992}) as reassign, \
             patch.object(hsi_sync, "enqueue_cancel") as cancel, \
             patch.object(hsi_sync, "enqueue_appointment",
                          side_effect=AssertionError("linked reassign must never create")) as create:
            self.assertEqual(hsi_sync.intent_for_row(row), {"id": 992})
        reassign.assert_called_once_with(row, row["turno_dt"])
        cancel.assert_not_called()
        create.assert_not_called()

    def test_schema_reclaims_expired_sending_lease_after_worker_death(self):
        # The durable DB claim is the process-restart boundary: a live lease
        # stays owned, an expired `sending` row becomes eligible again.
        # (the row-level behavior is pinned against a real database by test_db_hsi_store)
        from sqlalchemy.dialects import postgresql
        from app.core import sqlcore

        statements = []

        def capture(cur, stmt):
            statements.append(stmt)
            return type("Cursor", (), {"fetchall": lambda self: []})()

        with patch.object(db, "_connect"), patch.object(sqlcore, "run", side_effect=capture):
            db_hsi.hsi_outbox_claim()
        sql = str(statements[0].compile(dialect=postgresql.dialect(),
                                        compile_kwargs={"literal_binds": True}))
        self.assertIn("hsi_outbox.state = 'sending' AND hsi_outbox.lease_expires_at < now()", sql)
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)

    def test_datetime_conflict_payload_is_json_safe(self):
        cleaned = db_values._json_clean({"turno_dt": datetime(2026, 8, 1, 9, tzinfo=timezone.utc)})
        self.assertEqual(cleaned["turno_dt"], "2026-08-01T09:00:00+00:00")

    def test_offline_multi_record_replay_confirms_each_once(self):
        calls, acknowledged, retried = [], [], []
        items = [
            {"id": 1, "entity_type": "appointment", "entity_id": "11", "operation": "create",
             "attempts": 1, "payload": {"solicitud_id": 11, "dni": "301", "nombre": "A", "apellido": "Uno",
                                          "nacimiento": "1990-01-01", "sexo": "F", "servicio_id": "cardio",
                                          "when": "2026-08-01T09:00:00-03:00", "medico": None, "sobreturno": False}},
            {"id": 2, "entity_type": "appointment", "entity_id": "12", "operation": "create",
             "attempts": 1, "payload": {"solicitud_id": 12, "dni": "302", "nombre": "B", "apellido": "Dos",
                                          "nacimiento": "1991-01-01", "sexo": "M", "servicio_id": "cardio",
                                          "when": "2026-08-01T09:30:00-03:00", "medico": None, "sobreturno": False}},
        ]
        self.patch("hsi_outbox_claim", lambda limit: list(items))
        self.patch("hsi_outbox_ack_appointment",
                   lambda item, result, followup:
                   (calls.append((item["payload"]["solicitud_id"],
                                  item["payload"]["when"], result)),
                    acknowledged.append(item["id"]), {"state": "confirmed"})[2])
        self.patch("hsi_outbox_retry", lambda id_, error, delay: (retried.append(id_), {"state": "error"})[1])

        class FakeHsi:
            @staticmethod
            def push_turno(*args, **kwargs):
                return {"estado": "ok", "patient_id": 9, "appointment_id": len(calls) + 1,
                        "actual_turno_dt": kwargs.get("when_iso") or args[6]}

        # Batch L retiró el shim raíz: _replay_one hace
        # `from connectors.hsi import hsi_client`, así que el fake se inyecta
        # como atributo del paquete (lo que ese from-import lee), no en
        # sys.modules["hsi_client"].
        # service name is deliberately looked up at replay time, not trusted
        # from client payload — booking_requests._service_name is that seam.
        with patch.object(booking_requests, "_service_name",
                          lambda x: "Cardiología"), \
             patch("app.connectors.hsi.hsi_client", FakeHsi, create=True):
            self.assertEqual(hsi_sync.replay(), 2)
        self.assertEqual([x[0] for x in calls], [11, 12])
        self.assertTrue(all(isinstance(x[2]["patient_id"], str) for x in calls))
        self.assertTrue(all(isinstance(x[2]["appointment_id"], str) for x in calls))
        self.assertEqual(acknowledged, [1, 2])
        self.assertEqual(retried, [])

    def test_remote_failure_stays_durably_retryable(self):
        item = {"id": 9, "operation": "create", "attempts": 3,
                "payload": {"solicitud_id": 15, "dni": "303", "nombre": "C", "apellido": "Tres",
                            "nacimiento": "1990-01-01", "sexo": "X", "servicio_id": "cardio",
                            "when": "2026-08-01T10:00:00-03:00"}}
        self.patch("hsi_outbox_claim", lambda limit: [item])
        settled = []
        self.capture_settlement(settled)
        class Down:
            @staticmethod
            def push_turno(*args, **kwargs):
                raise ConnectionError("offline")
        with patch.object(booking_requests, "_service_name", lambda x: x), \
             patch("app.connectors.hsi.hsi_client", Down, create=True):
            self.assertEqual(hsi_sync.replay(), 0)
        self.assertEqual((settled[0][0]["id"], settled[0][1]), (9, "error"))
        self.assertGreater(settled[0][2]["delay_seconds"], 0)

    def test_missing_hsi_diary_becomes_visible_conflict(self):
        item = {"id": 10, "operation": "create", "attempts": 2,
                "payload": {"solicitud_id": 16, "dni": "305", "nombre": "D", "apellido": "Cuatro",
                            "nacimiento": "1990-01-01", "sexo": "X", "servicio_id": "medicina",
                            "when": "2026-08-01T10:00:00-03:00"}}
        self.patch("hsi_outbox_claim", lambda limit: [item])
        settled = []
        self.capture_settlement(settled)

        class NoRoute:
            class HsiConflict(Exception):
                pass

            @staticmethod
            def push_turno(*args, **kwargs):
                raise RuntimeError("no HSI diary matches professional 'Hugo Montes'")

        with patch.object(booking_requests, "_service_name", lambda x: x), \
             patch("app.connectors.hsi.hsi_client", NoRoute, create=True):
            self.assertEqual(hsi_sync.replay(), 0)
        self.assertEqual((settled[0][0]["id"], settled[0][1]), (10, "conflict"))
        self.assertEqual(len(settled), 1)

    def test_intent_for_row_cancelled_unlinked_returns_none(self):
        """Cancelled unlinked request should return None, not create intent."""
        row = {"id": 79, "estado": "cancelada", "hsi_estado": "error",
               "hsi_appointment_id": None, "turno_dt": "2026-08-01T12:00:00-03:00"}
        with patch.object(hsi_sync, "enqueue_appointment",
                          side_effect=AssertionError("cancelled must not create")) as create:
            self.assertIsNone(hsi_sync.intent_for_row(row))
        create.assert_not_called()

    def test_replay_one_cancelled_before_create_confirms_skip(self):
        """Request cancelled locally before EMR create must confirm as skip."""
        settled = []
        self.capture_settlement(settled)

        row = {"id": 90, "estado": "cancelada", "hsi_appointment_id": None}
        self.patch("get_booking_request", lambda sid: row if sid == 90 else None)

        class Adapter:
            def degrades_when_unconfigured(self):
                return False

            def execute_intent(self, *args, **kwargs):
                raise AssertionError("cancelled should not execute")

        item = {"id": 130, "operation": "create", "attempts": 1,
                "payload": {"solicitud_id": 90}, "idempotency_key": "k"}
        with patch.object(hsi_sync.connectors, "resolve", return_value=("hsi", "explicit")), \
             patch.object(hsi_sync.connectors, "adapter_for", return_value=Adapter()), \
             patch.object(hsi_sync.connectors, "is_configured", return_value=True):
            result = hsi_sync._replay_one(item)
        self.assertEqual(result.get("estado"), "skip")
        self.assertEqual(settled[0][2]["booking_status"], "skip")

    def _replay_against(self, row, item, adapter, settled=None):
        if settled is None:
            settled = []
        self.capture_settlement(settled)
        self.patch("get_booking_request", lambda sid: row if sid == row["id"] else None)
        with patch.object(hsi_sync.connectors, "resolve", return_value=("hsi", "explicit")), \
             patch.object(hsi_sync.connectors, "adapter_for", return_value=adapter), \
             patch.object(hsi_sync.connectors, "is_configured", return_value=True):
            result = hsi_sync._replay_one(item)
            return result, [(claim["id"], kwargs.get("result"))
                            for claim, state, kwargs in settled if state == "confirmed"]

    class _NoExecute:
        def degrades_when_unconfigured(self):
            return False

        def execute_intent(self, *args, **kwargs):
            raise AssertionError("must not reach the EMR")

    def test_queued_create_for_an_already_linked_request_is_skipped(self):
        """TLC AppointmentHsiFixed AtMostOneLiveRemote: an unlinked waitlist
        accept pushed its own create while the approval's create was still
        queued; replaying the stale one opened a second EMR appointment and
        dragged the local time back."""
        row = {"id": 91, "estado": "aprobada", "hsi_appointment_id": "A-2"}
        item = {"id": 131, "operation": "create", "attempts": 1,
                "payload": {"solicitud_id": 91, "when": "2026-10-01T09:00:00-03:00"},
                "idempotency_key": "k"}
        self.patch("reassign_appointment", lambda *a, **k: self.fail("local time must not move"))
        result, confirmed = self._replay_against(row, item, self._NoExecute())
        self.assertEqual(result["estado"], "skip")
        self.assertEqual(confirmed[0][1]["reason"], "already linked in the EMR")

    def test_queued_reassign_for_a_cancelled_request_is_skipped(self):
        """TLC AppointmentHsiFixed TerminalNeverMoved: a reassign replayed after
        the patient cancelled moved the cancelled row's time."""
        row = {"id": 92, "estado": "cancelada", "hsi_appointment_id": "A-1"}
        item = {"id": 132, "operation": "reassign", "attempts": 1,
                "payload": {"solicitud_id": 92, "when": "2026-10-01T09:00:00-03:00"},
                "idempotency_key": "k"}
        self.patch("reassign_appointment", lambda *a, **k: self.fail("cancelled row must not move"))
        result, _ = self._replay_against(row, item, self._NoExecute())
        self.assertEqual(result["estado"], "skip")

    def test_cancelled_create_that_already_landed_is_adopted_and_cancelled(self):
        """TLC AppointmentHsiFixed NoLiveRemoteForCancelledSettled: an earlier
        lease created the EMR appointment and crashed before the local write;
        the patient then cancelled. Skipping would orphan a live appointment."""
        row = {"id": 93, "estado": "cancelada", "hsi_appointment_id": None}
        item = {"id": 133, "operation": "create", "attempts": 2,
                "payload": {"solicitud_id": 93}, "idempotency_key": "k-93"}
        adopted = []
        def ack(claim, result, followup):
            self.assertEqual(claim["id"], item["id"])
            adopted.append((result, followup))
            return result
        self.patch("hsi_outbox_ack_appointment", ack)

        class Adapter(self._NoExecute):
            def probe_intent(self, key):
                assert key == "k-93"
                return {"found": True, "result": {"estado": "ok", "appointment_id": 777,
                                                  "patient_id": 5}}
        result, _ = self._replay_against(row, item, Adapter())
        self.assertEqual(result["appointment_id"], "777")
        self.assertEqual(adopted[0][0]["appointment_id"], "777")
        linked = dict(row, hsi_appointment_id="777")
        operation, payload, _key = adopted[0][1](linked, "cancel", None)
        self.assertEqual(operation, "cancel")
        self.assertEqual(payload["appointment_id"], "777")

    def test_cancelled_create_with_uncertain_probe_stays_retryable(self):
        row = {"id": 94, "estado": "cancelada", "hsi_appointment_id": None}
        item = {"id": 134, "operation": "create", "attempts": 2,
                "payload": {"solicitud_id": 94}, "idempotency_key": "k-94"}
        settled = []
        self.capture_settlement(settled)

        class Adapter(self._NoExecute):
            execute_calls = 0

            def probe_intent(self, key):
                return self.probe_result

            def execute_intent(self, *args, **kwargs):
                self.execute_calls += 1
                raise AssertionError("cancelled create must never be resent")

        for response in (None, {}, {"found": 0}, {"found": "false"},
                         {"found": True}, {"found": True, "result": {}},
                         {"found": True, "result": {"estado": "ok"}}):
            with self.subTest(probe=response):
                settled.clear()
                adapter = Adapter()
                adapter.probe_result = response
                result, confirmed = self._replay_against(row, item, adapter, settled)
                self.assertEqual(result["state"], "error")
                self.assertEqual(len(settled), 1)
                self.assertEqual(settled[0][1], "error")
                self.assertIn("outcome unknown", settled[0][2]["error"])
                self.assertNotIn("booking_status", settled[0][2])
                self.assertEqual(adapter.execute_calls, 0)
                self.assertEqual(confirmed, [])

    def test_backfill_pending_cancelled_unlinked_preserves_uncertainty(self):
        """Only fenced outbox settlement can resolve a cancelled create."""
        rows = [{"id": 130, "estado": "cancelada", "hsi_appointment_id": None,
                 "turno_dt": "2026-08-01T12:00:00-03:00"}]
        self.patch("booking_requests_hsi_pending", lambda desde=None: rows)
        updates = []
        self.patch("update_hsi", lambda *args: updates.append(args))

        self.assertEqual(hsi_sync.backfill_pending(), 0)
        self.assertEqual(updates, [])


class TestInboundReconcile(JournalBase):
    def test_default_reconcile_window_includes_recent_past_attendance(self):
        self.patch("hsi_known_patient_ids", lambda: [])
        requested = []

        class Client:
            def pull_observations(self, start, end, known_patient_ids):
                requested.append((start, end))
                return {"schedules": [], "appointments": [], "patients": []}

        # The window is anchored on the INSTALLATION's day (ART = UTC-3): every instant of
        # 2026-08-10 in Buenos Aires gives the same window, including 21:00-24:00 ART, which is
        # already 08-11 in UTC.
        clock = _clock.freeze(self, "2026-08-10T03:00:00Z")
        for moment in ("2026-08-10T03:00:00Z", "2026-08-10T12:00:00Z", "2026-08-11T00:30:00Z",
                       "2026-08-11T02:59:59Z"):
            with self.subTest(moment=moment):
                clock.set(moment)
                requested.clear()
                with _env.patched_env({"TURNOS_HSI_LOOKBACK_DAYS": "90"}, clear=False):
                    hsi_sync.reconcile(Client())
                self.assertEqual(requested, [
                    ((date(2026, 8, 10) - timedelta(days=90)).isoformat(), "2026-11-08")])

    def test_schedule_revert_to_a_previously_seen_value_updates_the_mirror(self):
        seen, mirror = set(), {"payload": None}
        self.patch("hsi_inbox_record", lambda key, *rest: None if key in seen else (seen.add(key) or {"source_key": key}))
        self.patch("hsi_inbox_finish", lambda *args: None)
        def mirror_schedule(_id, _professional, payload):
            changed = mirror["payload"] != payload
            mirror["payload"] = payload
            return {"changed": True} if changed else None
        self.patch("hsi_mirror_schedule", mirror_schedule)
        self.patch("hsi_known_patient_ids", lambda: [])

        class Client:
            alias = "A"
            def pull_observations(self, _start, _end, _known_patient_ids):
                return {"schedules": [{"id": "schedule-4", "professional_id": "staff-7",
                                       "specialty": self.alias}],
                        "appointments": [], "patients": []}
        client = Client()
        self.assertEqual(hsi_sync.reconcile(client, "2026-08-01", "2026-08-01")["schedules"], 1)
        client.alias = "B"
        self.assertEqual(hsi_sync.reconcile(client, "2026-08-01", "2026-08-01")["schedules"], 1)
        client.alias = "A"
        self.assertEqual(hsi_sync.reconcile(client, "2026-08-01", "2026-08-01")["schedules"], 1)
        self.assertEqual(mirror["payload"]["specialty"], "A")

    def test_hsi_schedule_patient_and_appointment_import_are_deduplicated(self):
        inbox, schedules, appointments, patients, finish = [], [], [], [], []
        seen = set()
        self.patch("hsi_inbox_record", lambda key, *rest: (inbox.append(key), None)[1]
                   if key in seen else (seen.add(key), inbox.append(key), {"source_key": key})[2])
        self.patch("hsi_inbox_finish", lambda key, state="confirmed", conflict=None: finish.append((key, state)))
        self.patch("hsi_mirror_schedule", lambda *a: schedules.append(a))
        self.patch("hsi_mirror_appointment", lambda *a: appointments.append(a))
        self.patch("hsi_mirror_patient", lambda *a: patients.append(a))
        self.patch("hsi_booking_requests_by_appointment", lambda id_: [])
        self.patch("hsi_unlinked_approved_at", lambda dni, when: [])
        self.patch("hsi_known_patient_ids", lambda: [])

        class Client:
            def pull_observations(self, _start, _end, _known_patient_ids):
                return {"schedules": [{"id": "schedule-4", "professional_id": "staff-7",
                                       "specialty": "Cardiología"}],
                        "appointments": [{"id": "99", "schedule_id": "schedule-4",
                                          "patient_id": "51", "scheduled_at": "2026-08-01T09:00:00-03:00",
                                          "state": "scheduled", "patient": {"id": "51", "document_number": "301"}}],
                        "patients": []}
        self.assertEqual(hsi_sync.reconcile(Client(), "2026-08-01", "2026-08-01"),
                         {"schedules": 1, "appointments": 1, "patients": 0, "conflicts": 0})
        self.assertEqual(len(schedules), 1)
        self.assertEqual(len(appointments), 1)
        self.assertEqual(len(patients), 1)
        # Same HSI facts re-read after reconnect do not produce a second import.
        self.assertEqual(hsi_sync.reconcile(Client(), "2026-08-01", "2026-08-01"),
                         {"schedules": 0, "appointments": 0, "patients": 0, "conflicts": 0})

    def test_concurrent_remote_change_is_visible_conflict(self):
        self.patch("hsi_mirror_appointment", lambda *a: None)
        self.patch("hsi_mirror_patient", lambda *a: None)
        self.patch("hsi_booking_requests_by_appointment", lambda id_: [{"id": 22, "turno_dt": datetime(2026, 8, 1, 9, tzinfo=timezone.utc)}])
        self.patch("hsi_outbox_inflight", lambda *a: True)
        conflict, finish = [], []
        self.patch("hsi_conflict", lambda *a: conflict.append(a))
        self.patch("hsi_inbox_finish", lambda *a: finish.append(a))
        status = hsi_sync._reconcile_appointment({"id": "44", "schedule_id": "schedule-4", "patient_id": "3",
                                                   "scheduled_at": "2026-08-01T10:00:00-03:00", "state": "scheduled",
                                                   "patient": {"id": "3"}}, "remote:44")
        self.assertEqual(status, "conflict")
        self.assertEqual(conflict[0][3], "concurrent_local_and_hsi_appointment_change")
        self.assertEqual(finish[0][1], "conflict")

    def test_inbound_cancel_confirms_a_local_cancel_not_a_conflict(self):
        self.patch("hsi_mirror_appointment", lambda *a: None)
        self.patch("hsi_mirror_patient", lambda *a: None)
        self.patch("hsi_booking_requests_by_appointment", lambda id_: [{"id": 22, "estado": "cancelada"}])
        finish, conflict = [], []
        self.patch("hsi_inbox_finish", lambda *a: finish.append(a))
        self.patch("hsi_conflict", lambda *a: conflict.append(a))
        status = hsi_sync._reconcile_appointment({"id": "44", "schedule_id": "schedule-4", "patient_id": "3",
                                                   "scheduled_at": "2026-08-01T10:00:00-03:00", "state": "cancelled",
                                                   "patient": {"id": "3"}}, "remote:44")
        self.assertEqual(status, "confirmed")
        self.assertEqual(finish[0][1], "confirmed")
        self.assertEqual(conflict, [])

    def test_inbound_hsi_absence_updates_linked_attendance_when_no_local_intent(self):
        self.patch("hsi_mirror_appointment", lambda *a: None)
        self.patch("hsi_mirror_patient", lambda *a: None)
        self.patch("hsi_booking_requests_by_appointment", lambda id_: [
            {"id": 22, "estado": "aprobada", "turno_dt": datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
             "asistencia": None}])
        self.patch("hsi_outbox_inflight", lambda *a: False)
        marked, finish = [], []
        self.patch("mark_attendance", lambda *a, **kw: marked.append((a, kw)))
        self.patch("hsi_inbox_finish", lambda *a: finish.append(a))
        status = hsi_sync._reconcile_appointment(
            {"id": "44", "schedule_id": "schedule-4", "patient_id": "3",
             "scheduled_at": "2026-08-01T06:00:00-03:00", "state": "no_show",
             "patient": {"id": "3"}}, "remote:44")
        self.assertEqual(status, "confirmed")
        self.assertEqual(marked[0][0][:3], (22, "ausente", "HSI"))
        self.assertEqual(marked[0][1], {"origen": "hsi", "exigir_no_futuro": False})
        self.assertEqual(finish[0][1], "confirmed")

    def test_inbound_hsi_attendance_does_not_overwrite_pending_local_mark(self):
        self.patch("hsi_mirror_appointment", lambda *a: None)
        self.patch("hsi_mirror_patient", lambda *a: None)
        self.patch("hsi_booking_requests_by_appointment", lambda id_: [
            {"id": 22, "estado": "aprobada", "turno_dt": datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
             "asistencia": "presente"}])
        self.patch("hsi_outbox_inflight", lambda *a: True)
        conflict, finish = [], []
        self.patch("hsi_conflict", lambda *a: conflict.append(a))
        self.patch("hsi_inbox_finish", lambda *a: finish.append(a))
        status = hsi_sync._reconcile_appointment(
            {"id": "44", "schedule_id": "schedule-4", "patient_id": "3",
             "scheduled_at": "2026-08-01T06:00:00-03:00", "state": "no_show",
             "patient": {"id": "3"}}, "remote:44")
        self.assertEqual(status, "conflict")
        self.assertEqual(conflict[0][3], "concurrent_local_and_hsi_attendance_change")
        self.assertEqual(finish[0][1], "conflict")


class TestTickResilience(unittest.TestCase):
    """tick()'s own documented contract ("never raises") must hold for
    EVERY phase, not just backfill/reconcile/resolver. Batch-4 fix
    (2026-08-09): `replay()` used to be the one unguarded call inside
    tick() — a DB outage there raised straight out, which on the thread
    topology kills the hsi-sync daemon thread permanently (_loop() does
    not catch either) until the process restarts."""

    def setUp(self):
        _env.set_env(self, TURNOS_SKIP_HSI=None)

    def test_replay_failure_does_not_escape_tick(self):
        with patch.object(hsi_sync, "backfill_pending", return_value=0), \
             patch.object(hsi_sync, "replay",
                          side_effect=RuntimeError("db down mid-replay")), \
             patch.object(hsi_sync, "reconcile",
                          return_value={"schedules": 0, "appointments": 0,
                                        "conflicts": 0}), \
             patch("app.connectors.hsi.hsi_resolver.resolver_pass",
                  return_value={}):
            resultado = hsi_sync.tick()  # must not raise
        self.assertEqual(resultado["replayed"], 0)
        self.assertEqual(resultado["backfilled"], 0)

    def test_every_phase_failing_still_returns_a_result(self):
        with patch.object(hsi_sync, "backfill_pending",
                          side_effect=RuntimeError("a")), \
             patch.object(hsi_sync, "replay", side_effect=RuntimeError("b")), \
             patch.object(hsi_sync, "reconcile", side_effect=RuntimeError("c")), \
             patch("app.connectors.hsi.hsi_resolver.resolver_pass",
                  side_effect=RuntimeError("d")):
            resultado = hsi_sync.tick()
        self.assertEqual(resultado["backfilled"], 0)
        self.assertEqual(resultado["replayed"], 0)
        self.assertEqual(resultado["resolved"], {})
        self.assertEqual(resultado["schedules"], 0)
