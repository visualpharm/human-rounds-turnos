"""EMR Bridge Protocol v1 conformance — executable, unmocked proofs.

A stdlib loopback server implements the bridge side of
``docs/architecture/emr-bridge-protocol.md`` — all five v1 endpoints, bearer
auth, a durable idempotency store — and Core talks to it through the REAL
``EnterpriseBridgeConnector`` over its real HTTP transport, selected the way
production selects it (``CONNECTOR_EMR_SYNC=emr_bridge`` plus the two bridge
keys, resolved through config), driven by the REAL durable journal
(``modules/turnos/hsi_journal.py`` — the Core-owned journal; its filename is
a legacy internal name) on a REAL disposable Postgres database
(``_dbtest.PerModuleDb``).

No vendor import, endpoint, credential or wire shape appears anywhere in
this file: the fixture speaks only the normalized v1 protocol. Nothing here
mocks the connector, the journal or the database. The one monkeypatch in
the file routes ``adapter_for`` to an in-process connector for the
"connectors without probe support keep today's behavior" proof — the same
seam ``test_emr_sync_boundary.py`` uses, and that proof's subject is the
ABC default, which has no HTTP side to fake.

If the disposable-Postgres harness is unavailable, this module FAILS — it
never skips and never passes vacuously. A conformance suite that silently
degrades on a missing database is worse than none.
"""
import json
import secrets
import io
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

import _dbtest  # noqa: E402
from app import connectors  # noqa: E402
from app.connectors.emr_bridge.adapter import EnterpriseBridgeConnector  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.modules.turnos import booking_requests, appointment_offer, hsi_journal, waitlist_store  # noqa: E402
from app.modules.turnos import booking_patient  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_emr_bridge")

HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    if not HAVE_DB:
        # Honest failure, never a skip: these proofs are only meaningful on
        # a real journal database, and a green run without one would be a
        # lie about exactly-once recovery.
        raise RuntimeError(
            "EMR bridge conformance REQUIRES the disposable-Postgres harness "
            f"and it is unavailable: {DB_SKIP_REASON or 'unknown reason'}")


def tearDownModule():
    _db.tear_down()


def _rows(sql, params=()):
    """One-shot query against this module's throwaway database (the same
    pattern test_queue_worker's table resets use) — for asserting journal
    tables the admin surface does not expose a reader for."""
    import psycopg
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, \
            c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


class BridgeV1Fixture:
    """Loopback reference implementation of the bridge side of protocol v1.

    Vendor-agnostic by construction: it knows the five endpoints, the
    bearer token and the normalized envelope — nothing else. Its idempotency
    store is the protocol's own contract: a repeated ``idempotency_key``
    gets the SAME settled result and never executes a second mutation. The
    ``drop_after_accept`` / ``refuse_before_execute`` keys reproduce the two
    halves of the crash window on the real wire — an intent durably
    executed whose response never reaches Core, and a lease that died
    before the bridge ever saw it.
    """

    def __init__(self, token="conf-token1"):
        self.token = token
        self.requests = []            # every wire exchange, for envelope asserts
        self.settled = {}             # idempotency_key -> settled result (durable)
        self.appointments = []        # the bridge's clinical appointment universe
        self.executed = 0             # intents that actually ran (not re-answers)
        self.drop_after_accept = set()      # execute, then die before responding
        self.refuse_before_execute = set()  # die before even reading the intent
        self.conflict_keys = set()          # answer 409 checkpoint conflicts
        self.enforce_checkpoint = False
        self.stable_checkpoint = False
        self.probe_behaviors = {}           # key -> malformed | outage | non_ok
        self.observations = {"schedules": [], "appointments": [], "patients": []}
        self.observation_requests = []
        state = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorized(self):
                return self.headers.get("Authorization") == f"Bearer {state.token}"

            def _deny(self):
                self._json(401, {"error": "unauthorized"})

            def _json(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read_body(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                return json.loads(raw) if raw else {}

            def _hang_up(self):
                # A real mid-exchange death on the wire: no status line, no
                # body, connection closed. The client cannot distinguish
                # this from the bridge crashing after doing its work.
                self.close_connection = True

            def do_GET(self):
                state.requests.append({"method": "GET", "path": self.path,
                                       "authorization": self.headers.get("Authorization"),
                                       "body": None})
                if not self._authorized():
                    self._deny()
                elif self.path == "/v1/health":
                    self._json(200, {"status": "ok", "protocol": "v1"})
                elif self.path == "/v1/capabilities":
                    self._json(200, {"protocol": "v1",
                                     "operations": ["create", "cancel", "reassign",
                                                    "attendance"],
                                     "probe": True})
                else:
                    self._json(404, {"error": "unknown endpoint " + self.path})

            def do_POST(self):
                body = self._read_body()
                state.requests.append({"method": "POST", "path": self.path,
                                       "authorization": self.headers.get("Authorization"),
                                       "body": body})
                if not self._authorized():
                    self._deny()
                elif self.path == "/v1/intents":
                    self._intent(body)
                elif self.path == "/v1/intents/probe":
                    key = str(body.get("idempotency_key") or "")
                    behavior = state.probe_behaviors.get(key)
                    if behavior == "malformed":
                        self._json(200, {"unexpected": True})
                    elif behavior == "outage":
                        self._json(503, {"error": "probe temporarily unavailable"})
                    elif behavior == "non_ok":
                        self._json(200, {"found": True,
                                         "result": {"status": "rejected"}})
                    elif key in state.settled:
                        self._json(200, {"found": True, "result": state.settled[key]})
                    else:
                        self._json(200, {"found": False})
                elif self.path == "/v1/observations":
                    state.observation_requests.append(body)
                    self._json(200, {"observations": state.observations})
                else:
                    self._json(404, {"error": "unknown endpoint " + self.path})

            def _intent(self, body):
                key = str(body.get("idempotency_key") or "")
                operation = body.get("operation")
                payload = body.get("payload") or {}
                if key in state.refuse_before_execute:
                    # one lease dies before the bridge ever sees it; the next
                    # attempt reaches a healthy bridge
                    state.refuse_before_execute.discard(key)
                    self._hang_up()
                    return
                if key in state.conflict_keys:
                    remote = {"id": 4091, "reason": "checkpoint_changed",
                              "held_by": "enterprise"}
                    self._json(409, {"error": "remote checkpoint changed",
                                     "remote": remote})
                    return
                if key in state.settled:  # protocol idempotency, not a new mutation
                    self._json(200, {"result": state.settled[key]})
                    return
                if operation == "reassign" and state.enforce_checkpoint:
                    appointment = next((a for a in state.appointments
                                        if str(a["id"]) == str(payload.get("appointment_id"))), None)
                    checkpoint = payload.get("checkpoint") or {}
                    if (appointment is None or
                            checkpoint.get("scheduled_at") != appointment["scheduled_at"]):
                        self._json(409, {"error": "remote checkpoint changed",
                                         "remote": {"reason": "checkpoint_changed"}})
                        return
                state.executed += 1
                result = {"status": "confirmed"}
                if operation == "create":
                    appointment_id = 5000 + state.executed
                    patient_id = 7000 + state.executed
                    state.appointments.append({
                        "id": appointment_id, "operation": operation, "key": key,
                        "national_id": (payload.get("patient") or {}).get("national_id"),
                        "scheduled_at": payload.get("scheduled_at"), "patient_id": patient_id,
                    })
                    result.update({
                        "appointment_id": appointment_id, "patient_id": patient_id,
                        "schedule_id": "schedule-31",
                        "scheduled_at": payload.get("scheduled_at"),
                        "checkpoint": {"appointment_id": appointment_id,
                                       "schedule_id": "schedule-31",
                                       "patient_id": patient_id,
                                       "scheduled_at": payload.get("scheduled_at"),
                                       "state": "scheduled", "version": "1"},
                    })
                elif operation == "reassign":
                    appointment_id = str(payload.get("appointment_id"))
                    appointment = next((a for a in state.appointments
                                        if str(a["id"]) == appointment_id), None)
                    if appointment is None:
                        self._json(404, {"error": "appointment not found"})
                        return
                    appointment["scheduled_at"] = payload.get("scheduled_at")
                    result.update({
                        "appointment_id": appointment_id,
                        "scheduled_at": payload.get("scheduled_at"),
                        "checkpoint": {"appointment_id": (appointment["id"] if state.stable_checkpoint
                                                          else appointment_id),
                                       "schedule_id": "schedule-31",
                                       "patient_id": (appointment["patient_id"] if state.stable_checkpoint
                                                      else "7001"),
                                       "scheduled_at": payload.get("scheduled_at"),
                                       "state": "scheduled",
                                       "version": "1" if state.stable_checkpoint else "2"},
                    })
                elif operation == "cancel":
                    appointment_id = str(payload.get("appointment_id"))
                    for appointment in state.appointments:
                        if str(appointment["id"]) == appointment_id:
                            appointment["state"] = "cancelled"
                            break
                    result["appointment_id"] = appointment_id
                elif operation == "attendance":
                    appointment_id = str(payload.get("appointment_id"))
                    result["appointment_id"] = appointment_id
                state.settled[key] = result
                if key in state.drop_after_accept:
                    self._hang_up()
                    return
                self._json(200, {"result": result})

        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def posts(self, path):
        return [r for r in self.requests
                if r["method"] == "POST" and r["path"] == path]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


# What each test states explicitly, never inherits from the developer shell.
# TURNOS_CONFIG_LOCK pins env over any DB-stored config (config.py's own ops
# escape hatch): it kills the 3 s cross-module bleed of config._db_cache in a
# full discover run, where a sibling module's throwaway DB could otherwise
# shadow this module's env routing. TURNOS_DEMO_MODE would make
# demo_installation's safety locks (skip-sync flags) non-overridable.
_ENV_KEYS = ("CONNECTOR_EMR_SYNC", "EMR_BRIDGE_URL", "EMR_BRIDGE_TOKEN",
             "TURNOS_CONFIG_LOCK", "TURNOS_SKIP_HSI", "TURNOS_HSI_SYNC_OFF",
             "TURNOS_DEMO_MODE")


class _ConformanceTest(unittest.TestCase):
    """Real disposable Postgres + the real bridge connector over real HTTP."""

    def setUp(self):
        if not HAVE_DB:  # setUpModule already failed the module; belt for direct runs
            self.fail("disposable Postgres harness unavailable: "
                      f"{DB_SKIP_REASON or 'unknown reason'}")
        self.fixture = BridgeV1Fixture()
        self.addCleanup(self.fixture.stop)
        _env.set_env(self, {k: None for k in _ENV_KEYS},
                     CONNECTOR_EMR_SYNC="emr_bridge", EMR_BRIDGE_URL=self.fixture.url,
                     EMR_BRIDGE_TOKEN=self.fixture.token, TURNOS_CONFIG_LOCK="1")

    def _adapter(self):
        adapter = connectors.adapter_for(connectors.EMR_SYNC)
        self.assertIsInstance(adapter, EnterpriseBridgeConnector,
                              "EMR_SYNC must resolve to the real Enterprise "
                              "bridge adapter through the registry")
        return adapter

    def _approved_request(self, when_iso):
        """A locally approved appointment in the exact production shape:
        persisted locally first, EMR status 'pendiente', journal pending."""
        sid = db_booking.create_booking_request(
            "30111222", "Probe", "Fernández", "1990-05-04", "+54 2255 555001",
            "protocolo", "hospital")
        db_booking.approve(sid, datetime.fromisoformat(when_iso), {"estado": "pendiente"})
        return db_booking.get_booking_request(sid)


class TestV1Surface(_ConformanceTest):
    def test_health_and_capabilities_answer_the_declared_protocol(self):
        adapter = self._adapter()
        self.assertEqual(adapter.health(), {"status": "ok", "protocol": "v1"})
        capabilities = adapter.capabilities()
        self.assertIn("create", capabilities["operations"])
        self.assertTrue(capabilities["probe"],
                        "a bridge without the probe endpoint cannot support the "
                        "journal's crash-window reclaim")

    def test_every_endpoint_requires_the_bearer_token(self):
        cases = (
            ("GET", "/v1/health", None),
            ("GET", "/v1/capabilities", None),
            ("POST", "/v1/intents", {"operation": "create",
                                      "idempotency_key": "unauthorized-intent",
                                      "payload": {}}),
            ("POST", "/v1/intents/probe", {"idempotency_key": "unauthorized-probe"}),
            ("POST", "/v1/observations", {"from_date": "2026-09-01",
                                           "to_date": "2026-09-02",
                                           "known_patient_ids": []}),
        )
        for method, path, body in cases:
            with self.subTest(path=path):
                data = json.dumps(body).encode() if body is not None else None
                request = urllib.request.Request(
                    self.fixture.url + path, data=data, method=method,
                    headers={"Content-Type": "application/json"} if data else {},
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(raised.exception.code, 401)
                raised.exception.read()
                raised.exception.close()
                self.assertIsNone(self.fixture.requests[-1]["authorization"])


class TestCreateThenConfirm(_ConformanceTest):
    def test_first_lease_executes_once_and_confirms_the_journal(self):
        row = self._approved_request("2026-09-02T09:30:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)

        out = hsi_journal.replay_item(intent["id"])

        self.assertEqual(out["state"], "confirmed")
        self.assertEqual(out["hsi_result"]["estado"], "ok")
        # One intent over the wire, no probe needed on a first lease
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 1)
        self.assertEqual(self.fixture.executed, 1)
        self.assertEqual(len(self.fixture.appointments), 1)
        self.assertEqual(self.fixture.posts("/v1/intents/probe"), [])
        # The envelope is exactly the protocol triple — no vendor leakage
        body = self.fixture.posts("/v1/intents")[0]["body"]
        self.assertEqual(set(body), {"operation", "idempotency_key", "payload"})
        self.assertEqual(body["operation"], "create")
        self.assertEqual(body["idempotency_key"], intent["idempotency_key"])
        self.assertEqual(body["payload"]["patient"]["national_id"], "30111222")
        self.assertEqual(body["payload"]["scheduled_at"],
                         "2026-09-02T09:30:00-03:00")
        self.assertEqual(body["payload"]["local_request_id"], str(row["id"]))
        # Core adopted the remote identifiers, checkpoint and mirror
        after = db_booking.get_booking_request(row["id"])
        remote_id = str(self.fixture.appointments[0]["id"])
        self.assertEqual(str(after["hsi_appointment_id"]), remote_id)
        self.assertEqual(after["hsi_estado"], "ok")
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", remote_id)["schedule_id"], "schedule-31")
        self.assertEqual(_rows("SELECT count(*) FROM hsi_appointments "
                               "WHERE hsi_appointment_id=%s", (remote_id,))[0][0], 1)


class TestMoveGeneration(_ConformanceTest):
    def _linked(self, at):
        row = self._approved_request(at)
        created = hsi_journal.enqueue_appointment(row)
        self.assertEqual(hsi_journal.replay_item(created["id"])["state"], "confirmed")
        return db_booking.get_booking_request(row["id"])

    def _move(self, sid, at):
        target = datetime.fromisoformat(at)
        row = db_booking.reassign_appointment(sid, target, {"estado": "pendiente"},
                                      actor="paciente")
        return row, hsi_journal.enqueue_reassign(row, target)

    def test_repeated_checkpoint_cycle_executes_last_b_and_retries_one_move(self):
        self.fixture.stable_checkpoint = True
        self.fixture.enforce_checkpoint = True
        a = "2026-10-12T09:00:00-03:00"
        b = "2026-10-13T09:00:00-03:00"
        row = self._linked(a)
        sid = row["id"]
        intents = []
        for target in (b, a, b):
            moved, intent = self._move(sid, target)
            intents.append(intent)
            self.assertEqual(hsi_journal.enqueue_reassign(moved, datetime.fromisoformat(target))["id"],
                             intent["id"])
            self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "confirmed")
        self.assertEqual(len({i["idempotency_key"] for i in intents}), 3)
        self.assertEqual(intents[0]["payload"]["checkpoint"],
                         intents[2]["payload"]["checkpoint"])
        self.assertNotEqual(intents[0]["payload"]["checkpoint"],
                            intents[1]["payload"]["checkpoint"])
        self.assertEqual(self.fixture.appointments[0]["scheduled_at"], b)
        self.assertEqual(db_booking.get_booking_request(sid)["turno_dt"], datetime.fromisoformat(b))
        self.assertEqual(self.fixture.executed, 4)  # one create, three moves
        self.assertEqual([db_hsi.hsi_outbox_get(i["id"])["state"] for i in intents],
                         ["confirmed"] * 3)

    def test_same_move_keeps_key_across_states_and_noop_event(self):
        a = "2026-10-14T09:00:00-03:00"
        b = "2026-10-15T09:00:00-03:00"
        row = self._linked(a)
        sid = row["id"]
        moved, intent = self._move(sid, b)
        count = len(db_booking.appointment_events(sid))
        db_booking.reassign_appointment(sid, datetime.fromisoformat(b), actor="paciente")
        self.assertEqual(len(db_booking.appointment_events(sid)), count)
        for state in ("pending", "sending", "error", "conflict"):
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE hsi_outbox SET state=%s WHERE id=%s", (state, intent["id"]))
            retry = hsi_journal.enqueue_reassign(moved, datetime.fromisoformat(b))
            self.assertEqual((retry["id"], retry["state"], retry["payload"]),
                             (intent["id"], state, intent["payload"]))

    def test_confirmed_move_noop_reuses_key_and_restores_status(self):
        a = "2026-10-26T09:00:00-03:00"
        b = "2026-10-27T09:00:00-03:00"
        row = self._linked(a)
        sid = row["id"]
        moved, intent = self._move(sid, b)
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "confirmed")
        before = len(db_booking.appointment_events(sid))
        db_booking.reassign_appointment(sid, datetime.fromisoformat(b),
                                {"estado": "pendiente"}, actor="paciente")
        self.assertEqual(len(db_booking.appointment_events(sid)), before)
        retry = hsi_journal.enqueue_reassign(moved, datetime.fromisoformat(b))
        self.assertEqual(retry["id"], intent["id"])
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")
        self.assertEqual(self.fixture.executed, 2)  # create and one move

    def test_relink_same_slot_does_not_reuse_old_remote_identity(self):
        a = "2026-10-31T09:00:00-03:00"
        row = self._linked(a)
        old = hsi_journal.enqueue_reassign(row, datetime.fromisoformat(a))
        db_hsi.hsi_outbox_confirm(old["id"], {"appointment_id": row["hsi_appointment_id"],
                                             "actual_turno_dt": a,
                                             "checkpoint": old["payload"]["checkpoint"]})
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE booking_requests SET hsi_appointment_id=%s, "
                        "hsi_status='pendiente' WHERE id=%s", ("replacement-5001", row["id"]))
        renewed = hsi_journal.enqueue_reassign(row, datetime.fromisoformat(a))
        self.assertNotEqual(renewed["idempotency_key"], old["idempotency_key"])
        self.assertEqual(renewed["payload"]["appointment_id"], "replacement-5001")

    def test_confirmed_without_checkpoint_does_not_certify_noop_status(self):
        a = "2026-11-01T09:00:00-03:00"
        row = self._linked(a)
        old = hsi_journal.enqueue_reassign(row, datetime.fromisoformat(a))
        db_hsi.hsi_outbox_confirm(old["id"], {"appointment_id": row["hsi_appointment_id"],
                                             "actual_turno_dt": a})
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE booking_requests SET hsi_status='pendiente' WHERE id=%s",
                        (row["id"],))
        hsi_journal.enqueue_reassign(row, datetime.fromisoformat(a))
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_estado"], "pendiente")

    def test_delayed_b_enqueue_refuses_newer_c_and_approval_fallback_is_stable(self):
        a = "2026-10-16T09:00:00-03:00"
        b = "2026-10-17T09:00:00-03:00"
        c = "2026-10-18T09:00:00-03:00"
        row = self._linked(a)
        sid = row["id"]
        fallback = hsi_journal.enqueue_reassign(row, datetime.fromisoformat(a))
        self.assertEqual(fallback["payload"]["move_generation"],
                         db_booking.appointment_events(sid)[-1]["id"])
        old = db_booking.reassign_appointment(sid, datetime.fromisoformat(b), actor="paciente")
        db_booking.reassign_appointment(sid, datetime.fromisoformat(c), actor="paciente")
        with self.assertRaisesRegex(ValueError, "stale"):
            hsi_journal.enqueue_reassign(old, datetime.fromisoformat(b))
        self.assertEqual(_rows("SELECT count(*) FROM hsi_outbox WHERE entity_id=%s "
                               "AND operation='reassign'", (str(sid),))[0][0], 1)

    def test_delayed_producer_after_local_b_cannot_enqueue_b_after_c_commits(self):
        a = "2026-10-28T09:00:00-03:00"
        b = "2026-10-29T09:00:00-03:00"
        c = "2026-10-30T09:00:00-03:00"
        row = self._linked(a)
        sid = row["id"]
        old = db_booking.reassign_appointment(sid, datetime.fromisoformat(b), actor="paciente")
        reached, release = threading.Event(), threading.Event()
        real_enqueue = db_hsi.hsi_outbox_enqueue_reassign

        def held_enqueue(*args):
            reached.set()
            self.assertTrue(release.wait(10), "producer hold was not released")
            return real_enqueue(*args)

        with mock.patch.object(db_hsi, "hsi_outbox_enqueue_reassign", held_enqueue):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(hsi_journal.enqueue_reassign,
                                     old, datetime.fromisoformat(b))
                try:
                    self.assertTrue(reached.wait(10), "old producer did not reach enqueue")
                    db_booking.reassign_appointment(sid, datetime.fromisoformat(c), actor="paciente")
                finally:
                    release.set()
                with self.assertRaises(db_hsi.StaleHsiReassignTarget):
                    future.result(timeout=10)
        self.assertEqual(_rows("SELECT count(*) FROM hsi_outbox WHERE entity_id=%s "
                               "AND operation='reassign'", (str(sid),))[0][0], 0)

    def test_legacy_no_event_baseline_then_real_move_has_new_generation(self):
        a = "2026-10-22T09:00:00-03:00"
        b = "2026-10-23T09:00:00-03:00"
        row = self._linked(a)
        sid = row["id"]
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM appointment_events WHERE request_id=%s", (sid,))
        baseline = hsi_journal.enqueue_reassign(row, datetime.fromisoformat(a))
        self.assertEqual(baseline["payload"]["move_generation"], 0)
        self.assertEqual(hsi_journal.enqueue_reassign(row, datetime.fromisoformat(a))["id"],
                         baseline["id"])
        db_hsi.hsi_outbox_confirm(baseline["id"], {"estado": "ok"})
        moved, newer = self._move(sid, b)
        self.assertNotEqual(newer["idempotency_key"], baseline["idempotency_key"])
        self.assertEqual(newer["payload"]["move_generation"],
                         db_booking.appointment_events(sid)[-1]["id"])

    def test_terminal_checkpoint_conflict_allows_corrected_same_generation(self):
        a = "2026-10-24T09:00:00-03:00"
        b = "2026-10-25T09:00:00-03:00"
        row = self._linked(a)
        moved, first = self._move(row["id"], b)
        self.fixture.conflict_keys.add(first["idempotency_key"])
        self.assertEqual(hsi_journal.replay_item(first["id"])["state"], "conflict")
        refreshed = {**first["payload"]["checkpoint"], "version": "remote-refresh"}
        with db._connect() as conn, conn.cursor() as cur:
            from psycopg.types.json import Json
            cur.execute("UPDATE hsi_checkpoints SET remote=%s WHERE "
                        "entity_type='appointment' AND entity_id=%s",
                        (Json(refreshed), str(row["hsi_appointment_id"])))
        corrected = hsi_journal.enqueue_reassign(moved, datetime.fromisoformat(b))
        self.assertNotEqual(corrected["idempotency_key"], first["idempotency_key"])
        self.assertEqual(corrected["payload"]["move_generation"],
                         first["payload"]["move_generation"])
        self.assertEqual(hsi_journal.replay_item(corrected["id"])["state"], "confirmed")
        self.assertEqual(self.fixture.appointments[0]["scheduled_at"], b)

    def test_ack_followup_and_direct_enqueue_converge_on_remote_latest_slot(self):
        self.fixture.enforce_checkpoint = True
        a = "2026-10-19T09:00:00-03:00"
        b = "2026-10-20T09:00:00-03:00"
        c = "2026-10-21T09:00:00-03:00"
        for direct_first in (True, False):
            with self.subTest(direct_first=direct_first):
                row = self._linked(a)
                sid = row["id"]
                _, first = self._move(sid, b)
                remote_done, release_ack = threading.Event(), threading.Event()
                real_ack = db_hsi.hsi_outbox_ack_appointment

                def hold_ack(*args, **kwargs):
                    remote_done.set()
                    self.assertTrue(release_ack.wait(10), "ack hold was not released")
                    return real_ack(*args, **kwargs)

                with mock.patch.object(db_hsi, "hsi_outbox_ack_appointment", hold_ack):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(hsi_journal.replay_item, first["id"])
                        try:
                            self.assertTrue(remote_done.wait(10), "remote B did not settle")
                            newer = db_booking.reassign_appointment(sid, datetime.fromisoformat(c),
                                                            {"estado": "pendiente"}, actor="paciente")
                            if direct_first:
                                deferred = hsi_journal.enqueue_reassign(newer, datetime.fromisoformat(c))
                                self.assertEqual(deferred["id"], first["id"])
                        finally:
                            release_ack.set()
                        self.assertEqual(future.result(timeout=10)["state"], "confirmed")
                direct = hsi_journal.enqueue_reassign(newer, datetime.fromisoformat(c))
                rows = _rows("SELECT id,idempotency_key,payload FROM hsi_outbox "
                             "WHERE entity_id=%s AND operation='reassign' ORDER BY id", (str(sid),))
                self.assertEqual(len(rows), 2)  # B predecessor and one C followup
                self.assertEqual(rows[-1][0], direct["id"])
                self.assertEqual(rows[-1][1], direct["idempotency_key"])
                self.assertEqual(rows[-1][2], direct["payload"])
                self.assertEqual(rows[-1][2]["checkpoint"]["scheduled_at"], b)
                self.assertEqual(hsi_journal.replay_item(direct["id"])["state"], "confirmed")
                self.assertEqual(self.fixture.appointments[-1]["scheduled_at"], c)
                self.assertEqual(db_booking.get_booking_request(sid)["turno_dt"], datetime.fromisoformat(c))

    def test_historical_confirmed_b_cannot_swallow_late_a_correction(self):
        self.fixture.enforce_checkpoint = True
        a = "2026-11-02T09:00:00-03:00"
        b = "2026-11-03T09:00:00-03:00"
        row = self._linked(a)
        sid = row["id"]
        _, first_b = self._move(sid, b)
        self.assertEqual(hsi_journal.replay_item(first_b["id"])["state"], "confirmed")
        _, old_a = self._move(sid, a)
        current_b = db_booking.reassign_appointment(sid, datetime.fromisoformat(b),
                                            {"estado": "pendiente"}, actor="paciente")
        checkpoint_b = db_hsi.hsi_checkpoint_get("appointment", row["hsi_appointment_id"])
        # Represent a pre-fix confirmed direct B that existed while A was
        # unsettled. New producers defer this ordering, but upgrades may
        # inherit it; the late A acknowledgement must still correct B.
        historical = {"solicitud_id": sid,
                      "appointment_id": str(row["hsi_appointment_id"]),
                      "checkpoint": {**checkpoint_b, "scheduled_at": a}, "when": b,
                      "operation": "reassign",
                      "move_generation": db_booking.appointment_events(sid)[-1]["id"]}
        confirmed_b = db_hsi.hsi_outbox_enqueue("appointment", str(sid), "reassign",
                                            historical, hsi_journal._reassign_key(historical))
        db_hsi.hsi_outbox_confirm(confirmed_b["id"],
            {"estado": "ok", "appointment_id": row["hsi_appointment_id"],
             "actual_turno_dt": b, "checkpoint": checkpoint_b})
        claimed_a = db_hsi.hsi_outbox_claim_id(old_a["id"])
        result_a = self._adapter().execute_intent("reassign", claimed_a["payload"],
                                                  claimed_a["idempotency_key"])
        self.assertEqual(self.fixture.appointments[0]["scheduled_at"], a)
        db_hsi.hsi_outbox_ack_appointment(claimed_a, result_a,
            lambda current, op, checkpoint: hsi_journal._ack_followup(
                claimed_a, current, op, checkpoint))
        correction = _rows("SELECT id,idempotency_key,payload FROM hsi_outbox WHERE entity_id=%s "
                           "AND operation='reassign' ORDER BY id DESC LIMIT 1", (str(sid),))[0]
        self.assertEqual({k: v for k, v in correction[2].items()
                          if k != "predecessor_intent_id"}, historical)
        self.assertNotEqual(correction[1], confirmed_b["idempotency_key"])
        self.assertEqual(correction[2]["predecessor_intent_id"], old_a["id"])
        self.assertEqual(hsi_journal.replay_item(correction[0])["state"], "confirmed")
        self.assertEqual(self.fixture.appointments[0]["scheduled_at"], b)
        self.assertEqual(db_booking.get_booking_request(sid)["turno_dt"], datetime.fromisoformat(b))


class TestUnconfiguredReplay(_ConformanceTest):
    def _offline(self, intent_id, *, deselect=False):
        # Claim while selected, then lose the selection before the leased
        # worker enters _replay_one. replay_item itself correctly refuses to
        # claim anything if the installation is already deselected.
        claimed = db_hsi.hsi_outbox_claim_id(intent_id) if deselect else None
        lost = {"CONNECTOR_EMR_SYNC": None} if deselect else {"EMR_BRIDGE_TOKEN": None}
        with _env.patched_env(lost):
            return (hsi_journal._replay_one(claimed) if deselect
                    else hsi_journal.replay_item(intent_id))

    def test_first_unlinked_create_can_degrade_locally_without_remote_effect(self):
        row = self._approved_request("2026-10-25T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        skipped = self._offline(intent["id"])
        self.assertEqual((skipped["state"], skipped["attempts"]), ("confirmed", 1))
        self.assertEqual(skipped["hsi_result"]["estado"], "skip")
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_estado"], "skip")
        self.assertEqual(self.fixture.requests, [])
        self.assertEqual(self.fixture.appointments, [])

    def test_concurrent_link_fences_first_create_local_skip(self):
        row = self._approved_request("2026-10-25T11:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        read_done, release = threading.Event(), threading.Event()
        original = hsi_journal._local_row

        def hold_after_local_read(payload):
            local = original(payload)
            read_done.set()
            self.assertTrue(release.wait(10), "local-only decision was not released")
            return local

        with mock.patch.object(hsi_journal, "_local_row", hold_after_local_read):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._offline, intent["id"])
                try:
                    self.assertTrue(read_done.wait(10), "local booking was not read")
                    with db._connect() as conn, conn.cursor() as cur:
                        cur.execute("UPDATE booking_requests SET hsi_appointment_id=%s, "
                                    "hsi_status='ok' WHERE id=%s",
                                    ("synthetic-concurrent-link", row["id"]))
                finally:
                    release.set()
                outcome = future.result(timeout=10)
        self.assertEqual(outcome["state"], "error")
        self.assertIsNone(outcome["hsi_result"])
        linked = db_booking.get_booking_request(row["id"])
        self.assertEqual(linked["hsi_appointment_id"], "synthetic-concurrent-link")
        self.assertEqual(linked["hsi_estado"], "ok")
        self.assertEqual(self.fixture.requests, [])

    def test_reclaimed_create_survives_deselection_and_adopts_prior_acceptance(self):
        row = self._approved_request("2026-10-26T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.drop_after_accept.add(intent["idempotency_key"])
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        self.assertEqual(self.fixture.executed, 1)
        db_hsi.hsi_outbox_reset(intent["id"])
        before = len(self.fixture.requests)
        checkpoints_before = _rows("SELECT count(*) FROM hsi_checkpoints")[0][0]

        unavailable = self._offline(intent["id"], deselect=True)
        self.assertEqual(unavailable["state"], "error")
        self.assertEqual(unavailable["attempts"], 2)
        self.assertIsNone(unavailable["hsi_result"])
        self.assertEqual(len(self.fixture.requests), before)
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_estado"], "pendiente")
        self.assertIsNone(db_booking.get_booking_request(row["id"])["hsi_appointment_id"])
        self.assertEqual(_rows("SELECT count(*) FROM hsi_checkpoints")[0][0],
                         checkpoints_before)

        db_hsi.hsi_outbox_reset(intent["id"])
        recovered = hsi_journal.replay_item(intent["id"])
        self.assertEqual(recovered["state"], "confirmed")
        self.assertEqual(self.fixture.executed, 1)
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 1)
        self.assertEqual(len(self.fixture.posts("/v1/intents/probe")), 1)
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_estado"], "ok")

    def test_linked_first_leases_wait_for_bridge_reconfiguration(self):
        for operation in ("cancel", "reassign", "attendance"):
            with self.subTest(operation=operation):
                row = self._approved_request("2026-10-27T10:00:00-03:00")
                created = hsi_journal.enqueue_appointment(row)
                self.assertEqual(hsi_journal.replay_item(created["id"])["state"], "confirmed")
                linked = db_booking.get_booking_request(row["id"])
                appointment_id = str(linked["hsi_appointment_id"])
                checkpoint = db_hsi.hsi_checkpoint_get("appointment", appointment_id)
                if operation == "cancel":
                    linked = db_booking.cancel(row["id"], actor="paciente")
                    intent = hsi_journal.enqueue_cancel(linked)
                elif operation == "reassign":
                    when = datetime.fromisoformat("2026-10-28T11:00:00-03:00")
                    linked = db_booking.reassign_appointment(row["id"], when,
                                                     {"estado": "pendiente"}, actor="paciente")
                    intent = hsi_journal.enqueue_reassign(linked, when)
                else:
                    intent = hsi_journal.enqueue_attendance(linked, "presente")
                before = len(self.fixture.requests)

                unavailable = self._offline(intent["id"])
                self.assertEqual((unavailable["state"], unavailable["attempts"]),
                                 ("error", 1))
                self.assertIsNone(unavailable["hsi_result"])
                self.assertEqual(len(self.fixture.requests), before)
                self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", appointment_id), checkpoint)
                self.assertEqual(str(db_booking.get_booking_request(row["id"])["hsi_appointment_id"]),
                                 appointment_id)

                db_hsi.hsi_outbox_reset(intent["id"])
                recovered = hsi_journal.replay_item(intent["id"])
                self.assertEqual(recovered["state"], "confirmed")
                self.assertEqual(self.fixture.posts("/v1/intents")[-1]["body"]["operation"],
                                 operation)

    def test_every_reclaimed_operation_keeps_uncertain_remote_result(self):
        for operation in ("create", "cancel", "reassign", "attendance"):
            with self.subTest(operation=operation):
                row = self._approved_request("2026-10-29T10:00:00-03:00")
                if operation == "create":
                    intent = hsi_journal.enqueue_appointment(row)
                    checkpoint_before = None
                    remote_id = None
                else:
                    created = hsi_journal.enqueue_appointment(row)
                    self.assertEqual(hsi_journal.replay_item(created["id"])["state"], "confirmed")
                    linked = db_booking.get_booking_request(row["id"])
                    remote_id = str(linked["hsi_appointment_id"])
                    checkpoint_before = db_hsi.hsi_checkpoint_get("appointment", remote_id)
                    if operation == "cancel":
                        intent = hsi_journal.enqueue_cancel(db_booking.cancel(row["id"], actor="paciente"))
                    elif operation == "reassign":
                        when = datetime.fromisoformat("2026-10-30T11:00:00-03:00")
                        moved = db_booking.reassign_appointment(row["id"], when,
                                                        {"estado": "pendiente"}, actor="paciente")
                        intent = hsi_journal.enqueue_reassign(moved, when)
                    else:
                        intent = hsi_journal.enqueue_attendance(linked, "presente")
                self.fixture.drop_after_accept.add(intent["idempotency_key"])
                self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
                executed = self.fixture.executed
                requests = len(self.fixture.requests)
                booking_before = db_booking.get_booking_request(row["id"])
                db_hsi.hsi_outbox_reset(intent["id"])

                uncertain = self._offline(intent["id"])
                self.assertEqual((uncertain["state"], uncertain["attempts"]), ("error", 2))
                self.assertIsNone(uncertain["hsi_result"])
                self.assertEqual(len(self.fixture.requests), requests)
                self.assertEqual(self.fixture.executed, executed)
                booking_after = db_booking.get_booking_request(row["id"])
                self.assertEqual(booking_after["hsi_estado"], booking_before["hsi_estado"])
                self.assertEqual(booking_after["hsi_appointment_id"],
                                 booking_before["hsi_appointment_id"])
                if remote_id:
                    self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", remote_id),
                                     checkpoint_before)

                db_hsi.hsi_outbox_reset(intent["id"])
                self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "confirmed")
                self.assertEqual(self.fixture.executed, executed)
                self.assertEqual(len([r for r in self.fixture.posts("/v1/intents")
                                      if r["body"]["idempotency_key"] == intent["idempotency_key"]]), 1)


class TestCrashWindowProbeRecovery(_ConformanceTest):
    """The crash window between remote acceptance and the local commit —
    both halves: the intent DID land remotely, and it did NOT."""

    def test_response_loss_after_accept_recovers_by_probe_with_one_appointment(self):
        row = self._approved_request("2026-09-04T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        key = intent["idempotency_key"]
        self.fixture.drop_after_accept = {key}

        first = hsi_journal.replay_item(intent["id"])
        # Core never heard the acceptance; the bridge durably executed it
        self.assertEqual(first["state"], "error")
        self.assertEqual(self.fixture.executed, 1)
        self.assertEqual(len(self.fixture.appointments), 1)

        db_hsi.hsi_outbox_reset(intent["id"])  # due again now («Reintentar» / due clock)
        second = hsi_journal.replay_item(intent["id"])

        self.assertEqual(second["state"], "confirmed")
        # Exactly one remote appointment, ever: the reclaim PROBED instead of
        # re-sending the mutation
        self.assertEqual(self.fixture.executed, 1)
        self.assertEqual(len(self.fixture.appointments), 1)
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 1)
        self.assertEqual(len(self.fixture.posts("/v1/intents/probe")), 1)
        self.assertEqual(self.fixture.posts("/v1/intents/probe")[0]["body"]
                         ["idempotency_key"], key)
        # And the adopted result is the one settled on the first attempt
        after = db_booking.get_booking_request(row["id"])
        self.assertEqual(str(after["hsi_appointment_id"]),
                         str(self.fixture.appointments[0]["id"]))
        self.assertEqual(after["hsi_estado"], "ok")
        remote_id = str(self.fixture.appointments[0]["id"])
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", remote_id)["schedule_id"], "schedule-31")
        self.assertEqual(_rows("SELECT count(*) FROM hsi_appointments "
                               "WHERE hsi_appointment_id=%s", (remote_id,))[0][0], 1)

    def test_cancelled_unlinked_backfill_keeps_lost_create_visible_until_recovery(self):
        row = self._approved_request("2026-10-20T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.drop_after_accept.add(intent["idempotency_key"])
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        db_booking.cancel(row["id"], actor="paciente")

        hsi_journal.backfill_pending()
        pending = db_booking.get_booking_request(row["id"])
        self.assertEqual(pending["estado"], "cancelada")
        self.assertIsNone(pending["hsi_appointment_id"])
        self.assertIn(pending["hsi_estado"], ("pendiente", "error"))
        self.assertEqual(db_hsi.hsi_outbox_get(intent["id"])["state"], "error")
        self.assertEqual(self.fixture.executed, 1)

        db_hsi.hsi_outbox_reset(intent["id"])
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "confirmed")
        follow = _rows("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                       "AND operation='cancel'", (str(row["id"]),))
        self.assertEqual(len(follow), 1)
        self.assertEqual(hsi_journal.replay_item(follow[0][0])["state"], "confirmed")
        self.assertEqual(self.fixture.executed, 2)
        self.assertEqual(len(self.fixture.appointments), 1)
        self.assertEqual(self.fixture.appointments[0]["state"], "cancelled")

    def test_held_cancelled_backfill_snapshot_cannot_overwrite_link_ack(self):
        # The backfill scan only reads upcoming appointments, so the date is far ahead.
        row = self._approved_request("2099-10-21T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.drop_after_accept.add(intent["idempotency_key"])
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        db_booking.cancel(row["id"], actor="paciente")
        scanned, release = threading.Event(), threading.Event()
        original_scan = db_booking.booking_requests_hsi_pending

        def held_scan(*args, **kwargs):
            rows = original_scan(*args, **kwargs)
            self.assertTrue(any(item["id"] == row["id"] and
                                item["hsi_appointment_id"] is None for item in rows))
            scanned.set()
            self.assertTrue(release.wait(10), "backfill snapshot was never released")
            return rows

        with mock.patch.object(db_booking, "booking_requests_hsi_pending", held_scan):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(hsi_journal.backfill_pending)
                try:
                    self.assertTrue(scanned.wait(10), "backfill did not scan")
                    db_hsi.hsi_outbox_reset(intent["id"])
                    self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "confirmed")
                    acknowledged = db_booking.get_booking_request(row["id"])
                finally:
                    release.set()
                self.assertIsInstance(future.result(timeout=10), int)

        after = db_booking.get_booking_request(row["id"])
        self.assertEqual(after["hsi_appointment_id"], acknowledged["hsi_appointment_id"])
        self.assertEqual(after["hsi_estado"], acknowledged["hsi_estado"])
        self.assertNotEqual(after["hsi_estado"], "skip")
        self.assertEqual(db_hsi.hsi_outbox_get(intent["id"])["state"], "confirmed")
        self.assertEqual(len(_rows("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                                   "AND operation='cancel'", (str(row["id"]),))), 1)

    def test_backfill_preserves_every_unsettled_create_and_no_outbox_row(self):
        for state in ("pending", "error", "sending", None):
            with self.subTest(outbox_state=state):
                row = self._approved_request("2099-10-24T10:00:00-03:00")
                intent = hsi_journal.enqueue_appointment(row) if state else None
                if state == "sending":
                    self.assertEqual(db_hsi.hsi_outbox_claim_id(intent["id"])["state"], "sending")
                elif state == "error":
                    claim = db_hsi.hsi_outbox_claim_id(intent["id"])
                    db_hsi.hsi_outbox_settle(claim, "error", error="outcome unknown")
                db_booking.cancel(row["id"], actor="paciente")
                before = db_booking.get_booking_request(row["id"])
                self.assertIn(before["hsi_estado"], ("pendiente", "error"))

                hsi_journal.backfill_pending()

                after = db_booking.get_booking_request(row["id"])
                self.assertEqual(after["hsi_estado"], before["hsi_estado"])
                self.assertIsNone(after["hsi_appointment_id"])
                if intent:
                    self.assertEqual(db_hsi.hsi_outbox_get(intent["id"])["state"], state)
                else:
                    self.assertEqual(_rows("SELECT count(*) FROM hsi_outbox "
                                            "WHERE entity_id=%s", (str(row["id"]),))[0][0], 0)

    def test_cancelled_reassign_response_loss_adopts_b_and_queues_corrective_cancel(self):
        a = "2026-10-09T10:00:00-03:00"
        b = "2026-10-10T11:00:00-03:00"
        row = self._approved_request(a)
        created = hsi_journal.enqueue_appointment(row)
        self.assertEqual(hsi_journal.replay_item(created["id"])["state"], "confirmed")
        moved = db_booking.reassign_appointment(row["id"], datetime.fromisoformat(b),
                                        {"estado": "pendiente"}, actor="paciente")
        reassign = hsi_journal.enqueue_reassign(moved, datetime.fromisoformat(b))
        self.fixture.drop_after_accept = {reassign["idempotency_key"]}
        self.assertEqual(hsi_journal.replay_item(reassign["id"])["state"], "error")
        self.assertEqual(self.fixture.appointments[0]["scheduled_at"], b)
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", "5001")["scheduled_at"], a)

        db_booking.cancel(row["id"], actor="paciente")
        c1 = hsi_journal.enqueue_cancel(db_booking.get_booking_request(row["id"]))
        self.assertEqual(c1["payload"]["checkpoint"]["scheduled_at"], a)
        db_hsi.hsi_outbox_reset(reassign["id"])
        adopted = hsi_journal.replay_item(reassign["id"])

        self.assertEqual(adopted["state"], "confirmed")
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 2)  # create + one R
        self.assertEqual(len(self.fixture.posts("/v1/intents/probe")), 1)
        self.assertEqual(self.fixture.executed, 2)
        self.assertEqual(db_booking.get_booking_request(row["id"])["estado"], "cancelada")
        self.assertEqual(db_hsi.hsi_checkpoint_get("appointment", "5001")["scheduled_at"], b)
        corrections = _rows("SELECT payload,state FROM hsi_outbox WHERE entity_id=%s "
                            "AND operation='cancel' AND id<>%s", (str(row["id"]), c1["id"]))
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0][0]["checkpoint"]["scheduled_at"], b)
        self.assertEqual(corrections[0][1], "pending")

    def test_remote_a_then_unlinked_move_to_other_day_reuses_create_and_reassigns(self):
        a = "2026-10-09T10:00:00-03:00"
        b = "2026-10-10T11:00:00-03:00"
        row = self._approved_request(a)
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.drop_after_accept = {intent["idempotency_key"]}
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        self.assertEqual(len(self.fixture.appointments), 1)
        moved = db_booking.reassign_appointment(row["id"], datetime.fromisoformat(b),
                                        {"estado": "pendiente"}, actor="paciente")
        self.assertEqual(hsi_journal.enqueue_appointment(moved)["id"], intent["id"])
        db_hsi.hsi_outbox_reset(intent["id"])
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "confirmed")
        linked = db_booking.get_booking_request(row["id"])
        self.assertEqual(linked["turno_dt"], datetime.fromisoformat(b))
        self.assertEqual(str(linked["hsi_appointment_id"]),
                         str(self.fixture.appointments[0]["id"]))
        follow = _rows("SELECT id,operation,state FROM hsi_outbox "
                       "WHERE entity_id=%s AND operation='reassign'", (str(row["id"]),))
        self.assertEqual(len(follow), 1)
        self.assertEqual(follow[0][2], "pending")
        self.assertEqual(hsi_journal.replay_item(follow[0][0])["state"], "confirmed")
        self.assertEqual(self.fixture.appointments[0]["scheduled_at"], b)
        self.assertEqual(len(self.fixture.appointments), 1)

    def test_public_reschedule_while_create_unlinked_never_direct_creates(self):
        row = self._approved_request("2099-10-16T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.drop_after_accept = {intent["idempotency_key"]}
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        with mock.patch("app.modules.turnos.slot_holds.try_take", return_value=True), \
             mock.patch("app.modules.turnos.booking_horizon.check_slot", return_value=None), \
             mock.patch("app.notifications.send.notify_approved"), \
             mock.patch("app.modules.turnos.waitlist.release_slot"):
            status, body = booking_patient._reschedule_core(
                None, row["dni"], row["id"],
                lambda: {"dia": "2099-10-17", "hora": "11:00"})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.fixture.appointments), 1)
        self.assertEqual(len(_rows("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                                   "AND operation='create'", (str(row["id"]),))), 1)

    def test_waitlist_accept_while_create_unlinked_never_direct_creates(self):
        row = self._approved_request("2026-10-18T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.drop_after_accept = {intent["idempotency_key"]}
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        offered = {"id": 91, "lista_espera_id": 92,
                   "slot_dt": datetime.fromisoformat("2026-10-19T11:00:00-03:00")}
        with mock.patch.object(appointment_offer.waitlist, "claim",
                               return_value=(True, offered)), \
             mock.patch.object(appointment_offer.store, "waitlist_row",
                               return_value={"solicitud_id": row["id"]}), \
             mock.patch("app.notifications.send.notify_approved"):
            self.assertEqual(appointment_offer.accept_offer(None, "synthetic-offer"),
                             row["id"])
        self.assertEqual(len(self.fixture.appointments), 1)
        self.assertEqual(len(_rows("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                                   "AND operation='create'", (str(row["id"]),))), 1)

    def test_patient_move_during_remote_call_preserves_latest_time(self):
        row = self._approved_request("2026-10-20T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        accepted = threading.Event()
        release = threading.Event()

        def hold():
            accepted.set()
            self.assertTrue(release.wait(10), "patient move never released replay")

        with mock.patch.object(hsi_journal, "_test_hold_after_remote_accept", hold):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(hsi_journal.replay_item, intent["id"])
                self.assertTrue(accepted.wait(10), "remote accept not reached")
                b = datetime.fromisoformat("2026-10-21T11:00:00-03:00")
                db_booking.reassign_appointment(row["id"], b, {"estado": "pendiente"},
                                        actor="paciente")
                release.set()
                self.assertEqual(future.result(timeout=10)["state"], "confirmed")
        self.assertEqual(db_booking.get_booking_request(row["id"])["turno_dt"], b)
        self.assertEqual(len(_rows("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                                   "AND operation='reassign'", (str(row["id"]),))), 1)

    def test_cancel_during_remote_call_keeps_terminal_time_and_queues_cancel(self):
        row = self._approved_request("2026-10-22T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        accepted, release = threading.Event(), threading.Event()

        def hold():
            accepted.set()
            self.assertTrue(release.wait(10), "cancellation never released replay")

        with mock.patch.object(hsi_journal, "_test_hold_after_remote_accept", hold):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(hsi_journal.replay_item, intent["id"])
                try:
                    self.assertTrue(accepted.wait(10), "remote accept not reached")
                    cancelled = db_booking.cancel(row["id"], actor="paciente")
                    with self.assertRaises(db_booking.SlotConflict):
                        db_booking.reassign_appointment(
                            row["id"], datetime.fromisoformat("2026-10-23T11:00:00-03:00"),
                            {"estado": "pendiente"}, actor="paciente")
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10)["state"], "confirmed")
        saved = db_booking.get_booking_request(row["id"])
        self.assertEqual(saved["estado"], "cancelada")
        self.assertEqual(saved["turno_dt"], cancelled["turno_dt"])
        self.assertEqual(saved["hsi_estado"], "pendiente")
        follow = _rows("SELECT id,state FROM hsi_outbox WHERE entity_id=%s "
                       "AND operation='cancel'", (str(row["id"]),))
        self.assertEqual(len(follow), 1)
        self.assertEqual(follow[0][1], "pending")
        self.assertEqual(hsi_journal.replay_item(follow[0][0])["state"], "confirmed")
        self.assertEqual(self.fixture.appointments[0]["state"], "cancelled")
        self.assertEqual(len(self.fixture.appointments), 1)

    def test_remote_a_then_cancel_ack_and_cancel_intent_commit_together(self):
        row = self._approved_request("2026-10-11T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.drop_after_accept = {intent["idempotency_key"]}
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        db_booking.cancel(row["id"], actor="paciente")
        db_hsi.hsi_outbox_reset(intent["id"])
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "confirmed")
        self.assertEqual(db_booking.get_booking_request(row["id"])["estado"], "cancelada")
        follow = _rows("SELECT id,operation FROM hsi_outbox "
                       "WHERE entity_id=%s AND operation='cancel'", (str(row["id"]),))
        self.assertEqual(len(follow), 1)
        self.assertEqual(hsi_journal.replay_item(follow[0][0])["state"], "confirmed")
        self.assertEqual(self.fixture.appointments[0]["state"], "cancelled")

    def test_followup_insert_failure_rolls_back_link_and_create_ack(self):
        row = self._approved_request("2026-10-12T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        claimed = db_hsi.hsi_outbox_claim_id(intent["id"])
        db_booking.reassign_appointment(row["id"],
                                datetime.fromisoformat("2026-10-13T11:00:00-03:00"),
                                {"estado": "pendiente"}, actor="paciente")
        result = {"estado": "ok", "appointment_id": "rollback-1",
                  "patient_id": "rollback-p", "actual_turno_dt": intent["payload"]["when"],
                  "checkpoint": {"schedule_id": "synthetic-schedule",
                                 "patient_id": "rollback-p"}}
        import psycopg
        with self.assertRaises(psycopg.errors.NotNullViolation):
            db_hsi.hsi_outbox_ack_appointment(
                claimed, result,
                lambda *_: ("reassign", {"solicitud_id": row["id"]}, None))
        self.assertIsNone(db_booking.get_booking_request(row["id"])["hsi_appointment_id"])
        self.assertEqual(_rows("SELECT state FROM hsi_outbox WHERE id=%s",
                               (intent["id"],))[0][0], "sending")
        self.assertEqual(_rows("SELECT count(*) FROM hsi_checkpoints "
                               "WHERE entity_id='rollback-1'")[0][0], 0)
        self.assertEqual(_rows("SELECT count(*) FROM hsi_appointments "
                               "WHERE hsi_appointment_id='rollback-1'")[0][0], 0)

    def test_stale_worker_ack_cannot_clear_new_lease(self):
        row = self._approved_request("2026-10-14T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        old = db_hsi.hsi_outbox_claim_id(intent["id"])
        _rows("UPDATE hsi_outbox SET lease_expires_at=now()-interval '1 second' "
              "WHERE id=%s RETURNING id", (intent["id"],))
        current = db_hsi.hsi_outbox_claim_id(intent["id"])
        self.assertEqual(current["attempts"], old["attempts"] + 1)
        with self.assertRaises(db_hsi.StaleHsiLease):
            db_hsi.hsi_outbox_ack_appointment(
                old, {"estado": "ok", "appointment_id": "stale-1"},
                lambda *_: self.fail("stale callback must not run"))
        saved = db_hsi.hsi_outbox_get(intent["id"])
        self.assertEqual(saved["state"], "sending")
        self.assertEqual(saved["attempts"], current["attempts"])
        self.assertIsNone(db_booking.get_booking_request(row["id"])["hsi_appointment_id"])
        class SettledAdapter:
            def degrades_when_unconfigured(self):
                return False

            def execute_intent(self, operation, payload, key):
                return {"estado": "ok", "appointment_id": "stale-1",
                        "actual_turno_dt": payload["when"]}

        with mock.patch.object(hsi_journal.connectors, "adapter_for",
                               return_value=SettledAdapter()):
            observed = hsi_journal._replay_one(old)
        self.assertEqual(observed["state"], "sending")
        self.assertEqual(db_hsi.hsi_outbox_get(intent["id"])["attempts"], current["attempts"])
        self.assertIsNone(db_booking.get_booking_request(row["id"])["hsi_appointment_id"])

    def test_cancelled_unlinked_create_waits_for_probe_then_cancels_known_remote(self):
        row = self._approved_request("2026-10-04T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        key = intent["idempotency_key"]
        self.fixture.drop_after_accept = {key}

        first = hsi_journal.replay_item(intent["id"])
        self.assertEqual(first["state"], "error")
        self.assertEqual(len(self.fixture.appointments), 1)
        self.assertIsNone(db_booking.get_booking_request(row["id"])["hsi_appointment_id"])
        db_booking.cancel(row["id"], actor="paciente")

        # The bridge accepted A, but its probe is temporarily unavailable.
        # Core must retain the create and must not re-send it after cancellation.
        self.fixture.probe_behaviors[key] = "outage"
        db_hsi.hsi_outbox_reset(intent["id"])
        uncertain = hsi_journal.replay_item(intent["id"])
        self.assertEqual(uncertain["state"], "error")
        self.assertIn("outcome unknown", uncertain["last_error"])
        self.assertEqual(db_booking.get_booking_request(row["id"])["estado"], "cancelada")
        self.assertIsNone(db_booking.get_booking_request(row["id"])["hsi_appointment_id"])
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 1)
        self.assertEqual(len(self.fixture.appointments), 1)

        # Once the probe recovers, the existing path adopts A and queues its
        # cancellation; no second create is sent.
        del self.fixture.probe_behaviors[key]
        db_hsi.hsi_outbox_reset(intent["id"])
        adopted = hsi_journal.replay_item(intent["id"])
        self.assertEqual(adopted["state"], "confirmed")
        self.assertEqual(str(db_booking.get_booking_request(row["id"])["hsi_appointment_id"]),
                         str(self.fixture.appointments[0]["id"]))
        self.assertEqual(db_booking.get_booking_request(row["id"])["estado"], "cancelada")
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 1)
        cancel_id = _rows(
            "SELECT id FROM hsi_outbox WHERE entity_id=%s AND operation='cancel'",
            (str(row["id"]),))[0][0]
        cancelled = hsi_journal.replay_item(cancel_id)
        self.assertEqual(cancelled["state"], "confirmed")
        self.assertEqual(self.fixture.appointments[0]["state"], "cancelled")
        self.assertEqual(self.fixture.executed, 2)  # one create, one cancel

    def test_cancelled_unlinked_create_skips_only_after_definitive_negative_probe(self):
        row = self._approved_request("2026-10-05T10:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.refuse_before_execute = {intent["idempotency_key"]}
        self.assertEqual(hsi_journal.replay_item(intent["id"])["state"], "error")
        db_booking.cancel(row["id"], actor="paciente")
        db_hsi.hsi_outbox_reset(intent["id"])

        skipped = hsi_journal.replay_item(intent["id"])
        self.assertEqual(skipped["state"], "confirmed")
        self.assertEqual(skipped["hsi_result"]["estado"], "skip")
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 1)
        self.assertEqual(self.fixture.executed, 0)
        self.assertEqual(self.fixture.appointments, [])

    def test_probe_not_found_falls_through_to_a_keyed_resend(self):
        row = self._approved_request("2026-09-05T11:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.refuse_before_execute = {intent["idempotency_key"]}

        first = hsi_journal.replay_item(intent["id"])
        self.assertEqual(first["state"], "error")
        self.assertEqual(self.fixture.executed, 0)  # the bridge never saw it

        db_hsi.hsi_outbox_reset(intent["id"])
        second = hsi_journal.replay_item(intent["id"])

        # found=False is a definite "never executed": the journal re-sends the
        # intent under the same stable key, which is safe by contract
        self.assertEqual(second["state"], "confirmed")
        self.assertEqual(len(self.fixture.posts("/v1/intents/probe")), 1)
        self.assertEqual(self.fixture.executed, 1)
        self.assertEqual(len(self.fixture.appointments), 1)
        after = db_booking.get_booking_request(row["id"])
        self.assertEqual(str(after["hsi_appointment_id"]),
                         str(self.fixture.appointments[0]["id"]))

    def _settle_remotely_then_lose_response(self, when_iso):
        row = self._approved_request(when_iso)
        intent = hsi_journal.enqueue_appointment(row)
        key = intent["idempotency_key"]
        self.fixture.drop_after_accept = {key}
        first = hsi_journal.replay_item(intent["id"])
        self.assertEqual(first["state"], "error")
        self.assertEqual(self.fixture.executed, 1)
        db_hsi.hsi_outbox_reset(intent["id"])
        return row, intent, key

    def test_probe_outage_logs_and_falls_back_to_a_deduplicated_resend(self):
        _row, intent, key = self._settle_remotely_then_lose_response(
            "2026-09-08T12:00:00-03:00")
        self.fixture.probe_behaviors[key] = "outage"
        captured = io.StringIO()
        with redirect_stderr(captured):
            second = hsi_journal.replay_item(intent["id"])

        self.assertEqual(second["state"], "confirmed")
        self.assertIn("intent probe failed", captured.getvalue())
        self.assertIn("keyed resend", captured.getvalue())
        self.assertEqual(self.fixture.executed, 1,
                         "a failed probe must not duplicate the settled mutation")
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 2,
                         "fallback must re-send the same key through the real wire")

    def test_malformed_probe_logs_and_falls_back_to_a_deduplicated_resend(self):
        _row, intent, key = self._settle_remotely_then_lose_response(
            "2026-09-09T12:00:00-03:00")
        self.fixture.probe_behaviors[key] = "malformed"
        captured = io.StringIO()
        with redirect_stderr(captured):
            second = hsi_journal.replay_item(intent["id"])

        self.assertEqual(second["state"], "confirmed")
        # The type, never the message (core/log_pii): a provider's error
        # text can echo what it was sent.
        self.assertIn("intent probe failed for outbox item", captured.getvalue())
        self.assertIn("ProviderUnavailable", captured.getvalue())
        self.assertNotIn("probe response is not v1-shaped", captured.getvalue())
        self.assertEqual(self.fixture.executed, 1)
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 2)

    def test_non_ok_settled_probe_is_not_adopted_and_is_observable(self):
        _row, intent, key = self._settle_remotely_then_lose_response(
            "2026-09-10T12:00:00-03:00")
        self.fixture.probe_behaviors[key] = "non_ok"
        captured = io.StringIO()
        with redirect_stderr(captured):
            second = hsi_journal.replay_item(intent["id"])

        self.assertEqual(second["state"], "confirmed")
        self.assertIn("intent probe failed", captured.getvalue())
        self.assertIn("ProviderUnavailable", captured.getvalue())
        self.assertNotIn("invalid intent result", captured.getvalue())
        self.assertIn("keyed resend", captured.getvalue())
        self.assertEqual(self.fixture.executed, 1)
        self.assertEqual(len(self.fixture.posts("/v1/intents")), 2)


class TestConflictIsDurable(_ConformanceTest):
    def test_checkpoint_conflict_lands_in_the_journal_and_conflict_table(self):
        row = self._approved_request("2026-09-06T08:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        self.fixture.conflict_keys = {intent["idempotency_key"]}

        out = hsi_journal.replay_item(intent["id"])

        self.assertEqual(out["state"], "conflict")
        # The request itself turns visibly errored: a create that never
        # reached the EMR must not read "pendiente" forever
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_estado"], "error")
        conflicts = _rows(
            "SELECT direction, reason, remote_payload FROM hsi_sync_conflicts "
            "WHERE entity_type='appointment' AND entity_id=%s", (str(row["id"]),))
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][0], "outbound")
        self.assertEqual(conflicts[0][1], "remote checkpoint changed")
        self.assertEqual(conflicts[0][2]["id"], 4091)  # remote snapshot, verbatim
        # Nothing was executed and no probe was wasted on a first lease
        self.assertEqual(self.fixture.executed, 0)
        self.assertEqual(self.fixture.posts("/v1/intents/probe"), [])


class TestObservationsReconcile(_ConformanceTest):
    def test_bounded_observation_pull_mirrors_into_core(self):
        db_hsi.hsi_mirror_patient("90001", "30111222",
                              {"id": "90001", "document_number": "30111222"})
        self.fixture.observations = {
            "schedules": [{"id": "schedule-31", "professional_id": "staff-7",
                           "specialty": "Cardiology", "professional_name": "Dr Probe"}],
            "appointments": [{
                "id": "5001", "schedule_id": "schedule-31", "patient_id": "90001",
                "scheduled_at": "2026-09-02T09:30:00-03:00", "state": "scheduled",
                "patient": {"id": "90001", "document_number": "30111222"}}],
            "patients": [{"id": "90001", "document_number": "30111222",
                          "given_name": "Probe"}],
        }

        counts = hsi_journal.reconcile()

        self.assertGreaterEqual(counts["schedules"], 1)
        self.assertGreaterEqual(counts["appointments"], 1)
        self.assertGreaterEqual(counts["patients"], 1)
        # The pull is bounded: previously mirrored patient IDs and a date
        # window only — never a global enumeration
        body = self.fixture.observation_requests[0]
        self.assertEqual(body["known_patient_ids"], ["90001"])
        self.assertIn("from_date", body)
        self.assertIn("to_date", body)
        self.assertEqual(
            self.fixture.requests[-1]["authorization"], f"Bearer {self.fixture.token}")
        # Mirrors and the deduplicated inbox landed
        self.assertEqual(_rows("SELECT count(*) FROM hsi_appointments "
                               "WHERE hsi_appointment_id='5001'")[0][0], 1)
        self.assertEqual(_rows("SELECT count(*) FROM hsi_doctor_schedules "
                               "WHERE hsi_diary_id='schedule-31'")[0][0], 1)
        # ...and a second identical pull is inert (no new inbox work)
        again = hsi_journal.reconcile()
        self.assertEqual(again["schedules"], 0)
        self.assertEqual(again["appointments"], 0)
        self.assertEqual(_rows("SELECT count(*) FROM hsi_inbox")[0][0], 2)


class TestLegacyConnectorWithoutProbeSupport(_ConformanceTest):
    """The safe ABC default: a connector that never heard of ``probe_intent``
    keeps the pre-probe replay behavior bit for bit — a reclaimed lease
    re-executes the intent under the same stable key. That is the legacy
    in-process adapter's documented crash-recovery contract, so wiring probe
    support into the journal cannot change it."""

    def test_reclaimed_lease_re_executes_with_the_same_key_and_no_probe(self):
        class LegacyShapeConnector(connectors.EmrSyncConnector):
            """The generic minimal connector shape: create only."""

            def __init__(self):
                self.calls = []

            def push_appointment(self, *args, **kwargs):
                return {"estado": "ok", "appointment_id": "77"}

            def execute_intent(self, operation, payload, idempotency_key):
                self.calls.append((operation, idempotency_key))
                if len(self.calls) == 1:
                    raise connectors.ProviderUnavailable("legacy transport blip")
                return {"estado": "ok", "appointment_id": "77"}

        legacy = LegacyShapeConnector()
        # probe support is REAL ABC code resolved on this instance: "cannot
        # probe", never an exception, never a network call
        self.assertIsNone(connectors.EmrSyncConnector.probe_intent(legacy, "any-key"))

        row = self._approved_request("2026-09-07T09:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        real_adapter_for = connectors.adapter_for

        def _route(capability):
            return legacy if capability == connectors.EMR_SYNC \
                else real_adapter_for(capability)

        with mock.patch.object(connectors, "adapter_for", _route):
            first = hsi_journal.replay_item(intent["id"])
            self.assertEqual(first["state"], "error")
            db_hsi.hsi_outbox_reset(intent["id"])
            second = hsi_journal.replay_item(intent["id"])

        self.assertEqual(second["state"], "confirmed")
        self.assertEqual([call[1] for call in legacy.calls],
                         [intent["idempotency_key"]] * 2,
                         "both leases must carry the SAME stable key")
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_appointment_id"], "77")


class TestDeselectedLinkedIntent(_ConformanceTest):
    """Direct patient writes keep older unsettled bridge work visible."""

    def _linked_move(self):
        a = "2099-10-12T09:00:00-03:00"
        b = "2099-10-13T09:00:00-03:00"
        row = self._approved_request(a)
        created = hsi_journal.enqueue_appointment(row)
        self.assertEqual(hsi_journal.replay_item(created["id"])["state"], "confirmed")
        sid = row["id"]
        moved = db_booking.reassign_appointment(sid, datetime.fromisoformat(b),
                                        {"estado": "pendiente"}, actor="paciente")
        intent = hsi_journal.enqueue_reassign(moved, datetime.fromisoformat(b))
        return sid, intent, a, b

    def _deselect(self):
        before = len(self.fixture.posts("/v1/intents"))
        self._offline_request_count = len(self.fixture.requests)
        _env.set_env(self, CONNECTOR_EMR_SYNC=None)
        return before

    def _accepted_unacked_move(self, intent):
        first = db_hsi.hsi_outbox_claim_id(intent["id"])
        self.assertIsNotNone(first)
        self._adapter().execute_intent("reassign", first["payload"],
                                       first["idempotency_key"])
        # Simulate process loss after bridge acceptance, before local ack.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE hsi_outbox SET lease_expires_at=now()-interval '1 second' "
                        "WHERE id=%s", (intent["id"],))
        reclaimed = db_hsi.hsi_outbox_claim_id(intent["id"])
        self.assertEqual(reclaimed["attempts"], first["attempts"] + 1)
        return reclaimed

    def _assert_no_egress_or_new_intent(self, sid, count):
        self.assertEqual(len(self.fixture.posts("/v1/intents")), count)
        self.assertEqual(len(self.fixture.requests), self._offline_request_count)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM hsi_outbox WHERE entity_id=%s", (str(sid),))
            self.assertEqual(cur.fetchone()["n"], 2)

    def test_cancel_keeps_sending_move_visible_until_same_bridge_recovery(self):
        sid, intent, _a, b = self._linked_move()
        claim = self._accepted_unacked_move(intent)
        count = self._deselect()
        status, _ = booking_patient.cancel_request("30111222", sid)
        self.assertEqual(status, 200)
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "pendiente")
        self._assert_no_egress_or_new_intent(sid, count)
        _env.set_env(self, CONNECTOR_EMR_SYNC="emr_bridge")
        self.assertEqual(hsi_journal._replay_one(claim)["state"], "confirmed")
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "pendiente")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM hsi_outbox WHERE entity_id=%s AND operation='cancel'",
                        (str(sid),))
            correction = cur.fetchone()["id"]
        self.assertEqual(hsi_journal.replay_item(correction)["state"], "confirmed")
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")
        # Reclaimed B is recovered by probe; only C2 makes a new write.
        self.assertEqual(len(self.fixture.posts("/v1/intents")), count + 1)
        self.assertEqual(len(self.fixture.appointments), 1)
        self.assertEqual(self.fixture.appointments[0]["state"], "cancelled")

    def test_reschedule_keeps_error_move_visible_without_new_journal(self):
        sid, intent, _a, _b = self._linked_move()
        self.assertIsNotNone(db_hsi.hsi_outbox_claim_id(intent["id"]))
        db_hsi.hsi_outbox_retry(intent["id"], "synthetic bridge timeout", 30)
        db_booking.update_hsi(sid, {"estado": "error"})
        count = self._deselect()
        with mock.patch("app.modules.turnos.booking_horizon.check_slot", return_value=None):
            status, body = booking_patient._reschedule_core(
                None, "30111222", sid, lambda: {"dia": "2099-10-14", "hora": "09:00"})
        self.assertEqual(status, 200, body)
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "error")
        self._assert_no_egress_or_new_intent(sid, count)

    def test_waitlist_accept_keeps_pending_move_visible_without_new_journal(self):
        sid, _intent, _a, b = self._linked_move()
        row = db_booking.get_booking_request(sid)
        wait_id = waitlist_store.opt_in(sid, row["dni"], "patient@example.test",
                                       row["servicio_id"], row["centro_slug"], row["turno_dt"])
        now = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(24)
        offered = db_slots.create_appointment_offer(
            datetime.fromisoformat("2099-10-15T09:00:00-03:00"), row["servicio_id"],
            row["centro_slug"], wait_id, token, now, now + timedelta(hours=1))
        self.assertIsNotNone(offered)
        count = self._deselect()
        self.assertEqual(appointment_offer.accept_offer(None, token), sid)
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "pendiente")
        self._assert_no_egress_or_new_intent(sid, count)

    def test_unlinked_uncertain_create_is_not_hidden_by_cancel(self):
        row = self._approved_request("2099-10-16T09:00:00-03:00")
        intent = hsi_journal.enqueue_appointment(row)
        claim = db_hsi.hsi_outbox_claim_id(intent["id"])
        self.assertIsNotNone(claim)
        db_booking.update_hsi(row["id"], {"estado": "skip"})
        count = self._deselect()
        status, _ = booking_patient.cancel_request("30111222", row["id"])
        self.assertEqual(status, 200)
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_estado"], "pendiente")
        self.assertEqual(len(self.fixture.posts("/v1/intents")), count)
        self.assertEqual(len(self.fixture.requests), self._offline_request_count)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM hsi_outbox WHERE entity_id=%s", (str(row["id"]),))
            self.assertEqual([r["id"] for r in cur.fetchall()], [intent["id"]])

    def test_unlinked_legacy_pending_without_outbox_stays_pending(self):
        row = self._approved_request("2099-10-17T09:00:00-03:00")
        self._deselect()
        status, _ = booking_patient.cancel_request("30111222", row["id"])
        self.assertEqual(status, 200)
        self.assertEqual(db_booking.get_booking_request(row["id"])["hsi_estado"], "pendiente")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM hsi_outbox WHERE entity_id=%s",
                        (str(row["id"]),))
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_delayed_cancel_continuation_cannot_replace_completed_correction(self):
        sid, intent, _a, _b = self._linked_move()
        claim = self._accepted_unacked_move(intent)
        self._deselect()
        cancelled, release = threading.Event(), threading.Event()
        actual_cancel = booking_requests.store.cancel

        def pause_after_cancel(*args, **kwargs):
            result = actual_cancel(*args, **kwargs)
            cancelled.set()
            self.assertTrue(release.wait(10))
            return result

        with mock.patch.object(booking_requests.store, "cancel", pause_after_cancel):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(booking_patient.cancel_request, "30111222", sid)
                try:
                    self.assertTrue(cancelled.wait(10))
                    _env.set_env(self, CONNECTOR_EMR_SYNC="emr_bridge")
                    self.assertEqual(hsi_journal._replay_one(claim)["state"], "confirmed")
                    with db._connect() as conn, conn.cursor() as cur:
                        cur.execute("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                                    "AND operation='cancel'", (str(sid),))
                        correction = cur.fetchone()["id"]
                    self.assertEqual(hsi_journal.replay_item(correction)["state"], "confirmed")
                finally:
                    # Restore deselection before the delayed direct caller resumes.
                    _env.set_env(self, CONNECTOR_EMR_SYNC=None)
                    release.set()
                self.assertEqual(future.result(timeout=10)[0], 200)
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")

    def test_selected_cancel_continuation_cannot_replace_completed_correction(self):
        sid, intent, _a, b = self._linked_move()
        claim = self._accepted_unacked_move(intent)
        cancelled, release = threading.Event(), threading.Event()
        actual_cancel = booking_requests.store.cancel

        def pause_after_cancel(*args, **kwargs):
            result = actual_cancel(*args, **kwargs)
            cancelled.set()
            self.assertTrue(release.wait(10))
            return result

        with mock.patch.object(booking_requests.store, "cancel", pause_after_cancel):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(booking_patient.cancel_request, "30111222", sid)
                try:
                    self.assertTrue(cancelled.wait(10))
                    self.assertEqual(db_booking.get_booking_request(sid)["estado"], "cancelada")
                    self.assertEqual(hsi_journal._replay_one(claim)["state"], "confirmed")
                    corrections = _rows(
                        "SELECT id,payload FROM hsi_outbox WHERE entity_id=%s "
                        "AND operation='cancel'", (str(sid),))
                    self.assertEqual(len(corrections), 1)
                    self.assertEqual(corrections[0][1]["checkpoint"]["scheduled_at"], b)
                    self.assertEqual(hsi_journal.replay_item(corrections[0][0])["state"],
                                     "confirmed")
                    self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")
                    posts_after_correction = len(self.fixture.posts("/v1/intents"))
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10)[0], 200)

        immediate_status = db_booking.get_booking_request(sid)["hsi_estado"]
        before_backfill = _rows(
            "SELECT operation,state,payload,hsi_result FROM hsi_outbox "
            "WHERE entity_id=%s ORDER BY id", (str(sid),))
        hsi_journal.backfill_pending()
        pending = _rows("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                        "AND state IN ('pending','error') ORDER BY id", (str(sid),))
        for (item_id,) in pending:
            hsi_journal.replay_item(item_id)
        final_status = db_booking.get_booking_request(sid)["hsi_estado"]
        self.assertEqual(str(self.fixture.appointments[0]["id"]),
                         str(db_booking.get_booking_request(sid)["hsi_appointment_id"]))
        self.assertEqual(self.fixture.appointments[0]["state"], "cancelled")
        outbox = _rows("SELECT operation,state,payload,hsi_result FROM hsi_outbox "
                       "WHERE entity_id=%s ORDER BY id", (str(sid),))
        summary = [(operation, state, bool(payload.get("predecessor_intent_id")),
                    (result or {}).get("estado"))
                   for operation, state, payload, result in outbox]
        prior_summary = [(operation, state, bool(payload.get("predecessor_intent_id")),
                          (result or {}).get("estado"))
                         for operation, state, payload, result in before_backfill]
        expected_prior = [
            ("create", "confirmed", False, "ok"),
            ("reassign", "confirmed", False, "ok"),
            ("cancel", "confirmed", True, "ok"),
            ("cancel", "pending", False, None),
        ]
        expected_final = expected_prior[:-1] + [
            ("cancel", "confirmed", False, "superseded")]
        # The public caller replays one globally ready intent. C3 may already
        # settle here or remain pending behind other bookings; both orders
        # must converge without regressing this booking's confirmed status.
        self.assertIn(prior_summary, (expected_prior, expected_final))
        self.assertEqual(
            (immediate_status, final_status, summary,
             len(self.fixture.posts("/v1/intents"))),
            ("ok", "ok", expected_final, posts_after_correction + 1),
            "a confirmed corrective cancel must not regress after continuation or backfill")
        self.assertEqual(self.fixture.posts("/v1/intents")[-1]["body"]["operation"],
                         "cancel")
        self.assertTrue(all(state == "confirmed" for _, state, _, _ in outbox))

    def test_create_link_before_cancel_lock_survives_interrupted_enqueue(self):
        row = self._approved_request("2099-10-18T09:00:00-03:00")
        sid = row["id"]
        create = hsi_journal.enqueue_appointment(row)
        actual_cancel = booking_requests.store.cancel

        def link_before_cancel(*args, **kwargs):
            self.assertEqual(hsi_journal.replay_item(create["id"])["state"],
                             "confirmed")
            return actual_cancel(*args, **kwargs)

        with mock.patch.object(booking_requests.store, "cancel", link_before_cancel), \
                mock.patch.object(hsi_journal, "enqueue_cancel",
                                  side_effect=RuntimeError("synthetic enqueue interruption")):
            status, _ = booking_patient.cancel_request("30111222", sid)
        self.assertEqual(status, 200)
        linked = db_booking.get_booking_request(sid)
        self.assertEqual((linked["estado"], linked["hsi_estado"]),
                         ("cancelada", "pendiente"))
        self.assertIsNotNone(linked["hsi_appointment_id"])
        self.assertEqual(_rows("SELECT count(*) FROM hsi_outbox WHERE entity_id=%s "
                               "AND operation='cancel'", (str(sid),))[0][0], 0)

        hsi_journal.backfill_pending()
        cancel_id = _rows("SELECT id FROM hsi_outbox WHERE entity_id=%s "
                          "AND operation='cancel'", (str(sid),))[0][0]
        self.assertEqual(hsi_journal.replay_item(cancel_id)["state"], "confirmed")
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")
        self.assertEqual(str(self.fixture.appointments[0]["id"]),
                         str(db_booking.get_booking_request(sid)["hsi_appointment_id"]))
        self.assertEqual(self.fixture.appointments[0]["state"], "cancelled")



if __name__ == "__main__":
    unittest.main()
