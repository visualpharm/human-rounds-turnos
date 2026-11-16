"""Database-backed proof that a patient move never opens a second EMR turno.

The trace the 2026-09-24 formal sweep left open (hsi.md «Known residual»):
the approval's create reaches the EMR but its response is lost (a crash or a
transport failure after acceptance), so the local row stays unlinked; then
the patient reschedules, or accepts a waitlist offer, on that same row. A
direct create on the move opened a second clinical appointment beside the
first one. The move now goes through the journal: the outstanding create is
the only one, and when it lands on a row the patient already moved, the
journal links it without dragging the time back and reassigns it onto the
patient's slot.

The EMR is an in-memory fake with the real connector contract: a keyed
``execute_intent`` deduplicates by idempotency key (as the bridge does), the
legacy ``push_appointment`` books a new appointment on every call.
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app import connectors  # noqa: E402
from app.core import config, db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.modules.turnos import appointment_offer, booking_emr, hsi_journal  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_patient  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_hsi_unlinked_move",
    seed=lambda: db_booking.get_booking_request(-1),
)
HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_APPROVED_AT = "2099-07-20T10:30:00-03:00"


class _FakeEmr:
    """One EMR diary in memory. ``lose_next_response`` makes the next keyed
    create land remotely and then fail, the crash window under test."""

    def __init__(self):
        self.appointments = {}
        self.by_key = {}
        self.lose_next_response = False

    def degrades_when_unconfigured(self):
        return False

    def live(self):
        return {aid: when for aid, (when, cancelled) in self.appointments.items()
                if not cancelled}

    def _book(self, when):
        aid = "A-%d" % (len(self.appointments) + 1)
        self.appointments[aid] = (when, False)
        return aid

    def execute_intent(self, operation, payload, idempotency_key):
        if idempotency_key in self.by_key:
            result = self.by_key[idempotency_key]
        else:
            result = self._apply(operation, payload)
            self.by_key[idempotency_key] = result
        if self.lose_next_response:
            self.lose_next_response = False
            raise ConnectionError("response lost after the EMR accepted the intent")
        return dict(result)

    def _apply(self, operation, payload):
        if operation == "create":
            aid = self._book(payload["when"])
        elif operation == "reassign":
            aid = payload["appointment_id"]
            self.appointments[aid] = (payload["when"], False)
        elif operation == "cancel":
            aid = payload["appointment_id"]
            self.appointments[aid] = (self.appointments[aid][0], True)
        else:
            aid = payload.get("appointment_id")
        return {"estado": "ok", "patient_id": "P-1", "appointment_id": aid}

    def probe_intent(self, idempotency_key):
        hit = self.by_key.get(idempotency_key)
        return {"found": True, "result": dict(hit)} if hit else {"found": False}

    def push_appointment(self, dni, nombre, apellido, nacimiento, sexo,
                         especialidad, turno_iso, medico=None, sobreturno=False):
        aid = self._book(turno_iso)
        return {"estado": "ok", "patient_id": "P-1", "appointment_id": aid,
                "actual_turno_dt": turno_iso}


class TestUnlinkedMoveNeverDuplicates(unittest.TestCase):
    FLAGS = ("CONNECTOR_EMR_SYNC", "TURNOS_SKIP_HSI", "TURNOS_OUTBOUND_KILL",
             "TURNOS_CONFIG_LOCK", "TURNOS_DEMO_MODE")

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "test database unavailable")
        clock = patch("app.modules.turnos.booking_horizon._local_now",
                      new=lambda now: datetime(2099, 7, 19, 8))
        clock.start()
        self.addCleanup(clock.stop)
        _env.set_env(self, dict.fromkeys(self.FLAGS))
        self.env_path = config.ENV_PATH
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        self.saved_config = dict(config._db_overrides())
        config.guardar({"CONNECTOR_EMR_SYNC": "hsi", "TURNOS_SKIP_HSI": None})
        config._reset_cache_para_tests()
        self.addCleanup(self._restore_config)
        self.emr = _FakeEmr()
        adapter = patch.object(connectors, "adapter_for", return_value=self.emr)
        adapter.start()
        self.addCleanup(adapter.stop)

    def _restore_config(self):
        config.guardar({key: self.saved_config.get(key)
                        for key in ("CONNECTOR_EMR_SYNC", "TURNOS_SKIP_HSI")})
        config.ENV_PATH = self.env_path
        config._reset_cache_para_tests()

    def _approved_with_lost_response(self, dni):
        sid = db_booking.create_booking_request(
            dni, "Prueba", "EMR", "1982-02-02", None,
            "cardiologia", "hospital", None, solicitante_dni=dni)
        self.emr.lose_next_response = True
        status, _payload = booking_decisions._approve_core(
            {"usuario": "test-admin", "rol": "admin"}, db_booking.get_booking_request(sid),
            sid, {"turno_dt": _APPROVED_AT})
        self.assertEqual(status, 200)
        row = db_booking.get_booking_request(sid)
        self.assertIsNone(row["hsi_appointment_id"], "the lost response leaves it unlinked")
        self.assertEqual(len(self.emr.live()), 1, "the EMR did book the approval")
        return sid

    def _settle(self, sid):
        """Every later recovery path: backfill plus due replays, as the worker
        runs them, until nothing is left to send."""
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE hsi_outbox SET next_attempt_at=now() WHERE entity_id=%s",
                        (str(sid),))
        for _ in range(4):
            hsi_journal.backfill_pending()
            hsi_journal.replay(limit=20)

    def _assert_one_live_at(self, sid, when_iso):
        live = self.emr.live()
        self.assertEqual(len(live), 1, "exactly one EMR appointment: %r" % live)
        (aid, remote_when), = live.items()
        want = datetime.fromisoformat(when_iso)
        self.assertEqual(datetime.fromisoformat(remote_when), want)
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["hsi_appointment_id"], aid)
        self.assertEqual(row["turno_dt"], want, "the patient's slot stands")
        self.assertEqual(row["hsi_estado"], "ok")

    def test_patient_reschedule_after_a_lost_create_response(self):
        dni = "30120001"
        sid = self._approved_with_lost_response(dni)
        status, _payload = booking_patient._reschedule_core(
            None, dni, sid, lambda: {"dia": "2099-07-27", "hora": "11:00"})
        self.assertEqual(status, 200)
        self._settle(sid)
        self._assert_one_live_at(sid, "2099-07-27T11:00:00-03:00")

    def test_waitlist_accept_after_a_lost_create_response(self):
        sid = self._approved_with_lost_response("30120002")
        slot = datetime.fromisoformat("2099-07-28T09:00:00-03:00")
        oferta = {"id": None, "lista_espera_id": 1, "slot_dt": slot}
        with patch.object(appointment_offer.waitlist, "claim", return_value=(True, oferta)), \
                patch.object(appointment_offer.store, "waitlist_row",
                             return_value={"solicitud_id": sid}):
            self.assertEqual(appointment_offer.accept_offer(None, "tok-unlinked"), sid)
        self._settle(sid)
        self._assert_one_live_at(sid, "2099-07-28T09:00:00-03:00")

    def test_retry_after_a_move_reuses_the_outstanding_create(self):
        """«Reintentar» on a moved, still-unlinked row retries the create that
        may already exist in the EMR — never a fresh one at the new slot."""
        dni = "30120003"
        sid = self._approved_with_lost_response(dni)
        self.emr.lose_next_response = True  # the move's own replay is lost too
        status, _payload = booking_patient._reschedule_core(
            None, dni, sid, lambda: {"dia": "2099-07-29", "hora": "11:00"})
        self.assertEqual(status, 200)
        booking_emr._retry_hsi_core(sid)
        self._settle(sid)
        self._assert_one_live_at(sid, "2099-07-29T11:00:00-03:00")


if __name__ == "__main__":
    unittest.main()
