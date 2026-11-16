"""Database-backed runtime proof for the explicit EMR booking boundary.

The tests drive the real approval, patient cancellation, and patient
rescheduling domain paths with a disposable Postgres database. They never
stub the journal or a connector helper: connector selection is read by the
real registry and durable intents are inspected from the actual outbox.
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
from app.core import config, db, outbound_guard  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.modules.turnos import booking_requests, hsi_fallback, hsi_journal  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_emr  # noqa: E402
from app.modules.turnos import booking_patient  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_emr_explicit_paths",
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


class TestExplicitEmrBookingPaths(unittest.TestCase):
    FLAGS = (
        "CONNECTOR_EMR_SYNC", "TURNOS_SKIP_HSI", "TURNOS_OUTBOUND_KILL",
        "TURNOS_CONFIG_LOCK", "TURNOS_DEMO_MODE",
        "TURNOS_HSI_SYNC_OFF", "TURNOS_HSI_RESYNC_OFF",
        "HSI_URL", "EMR_BRIDGE_URL", "EMR_BRIDGE_TOKEN",
    )

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "test database unavailable")
        # These durable EMR scenarios use fixed July 2099 slots. Keep the
        # real 31-day patient horizon active with a matching fixture clock.
        clock = patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 7, 19, 8))
        clock.start()
        self.addCleanup(clock.stop)
        _env.set_env(self, dict.fromkeys(self.FLAGS))
        self.env_path = config.ENV_PATH
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        # This is a disposable module database.  Exercise the real persisted
        # operator setting rather than patching the registry/config seam; a
        # host-level test setting must not leak into the cases below.
        self.saved_config = dict(config._db_overrides())
        config.guardar({key: None for key in (
            "CONNECTOR_EMR_SYNC", "TURNOS_SKIP_HSI", "HSI_URL",
            "EMR_BRIDGE_URL", "EMR_BRIDGE_TOKEN",
        )})
        if outbound_guard.armed():
            outbound_guard.disarm()

    def tearDown(self):
        if outbound_guard.armed():
            outbound_guard.disarm()
        config.guardar({key: self.saved_config.get(key) for key in (
            "CONNECTOR_EMR_SYNC", "TURNOS_SKIP_HSI", "HSI_URL",
            "EMR_BRIDGE_URL", "EMR_BRIDGE_TOKEN",
        )})
        config.ENV_PATH = self.env_path
        config._reset_cache_para_tests()

    @staticmethod
    def _new(dni):
        return db_booking.create_booking_request(
            dni, "Prueba", "EMR", "1982-02-02", None,
            "cardiologia", "hospital", None, solicitante_dni=dni,
        )

    @staticmethod
    def _outbox(sid):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT operation, state FROM hsi_outbox WHERE entity_id=%s ORDER BY id",
                (str(sid),),
            )
            return cur.fetchall()

    @staticmethod
    def _approve(sid):
        row = db_booking.get_booking_request(sid)
        return booking_decisions._approve_core(
            {"usuario": "test-admin", "rol": "admin"},
            row,
            sid,
            {"turno_dt": "2099-07-20T10:30:00-03:00"},
        )

    @staticmethod
    def _linked(dni):
        sid = TestExplicitEmrBookingPaths._new(dni)
        db_booking.approve(
            sid,
            datetime.fromisoformat("2099-07-20T10:30:00-03:00"),
            {"estado": "ok", "patient_id": "patient-1", "appointment_id": "appointment-1"},
        )
        return sid

    def _assert_no_selection_paths(self, connector_value):
        config.guardar({"CONNECTOR_EMR_SYNC": connector_value})
        suffix = {None: "1", "off": "2", "not_a_connector": "3"}[connector_value]
        day = {None: "22", "off": "23", "not_a_connector": "24"}[connector_value]

        approve_dni = "3011000" + suffix
        approve_sid = self._new(approve_dni)
        status, _payload = self._approve(approve_sid)
        self.assertEqual(status, 200)
        self.assertEqual(db_booking.get_booking_request(approve_sid)["hsi_estado"], "skip")
        self.assertEqual(self._outbox(approve_sid), [])
        self.assertEqual(booking_emr._retry_hsi_core(approve_sid)[0], 409)

        cancel_dni = "3011010" + suffix
        cancel_sid = self._linked(cancel_dni)
        status, _payload = booking_patient.cancel_request(cancel_dni, cancel_sid)
        self.assertEqual(status, 200)
        self.assertEqual(db_booking.get_booking_request(cancel_sid)["hsi_estado"], "skip")
        self.assertEqual(self._outbox(cancel_sid), [])

        reassign_dni = "3011020" + suffix
        reassign_sid = self._linked(reassign_dni)
        status, _payload = booking_patient._reschedule_core(
            None, reassign_dni, reassign_sid,
            lambda: {"dia": "2099-07-" + day, "hora": "11:00"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(db_booking.get_booking_request(reassign_sid)["hsi_estado"], "skip")
        self.assertEqual(self._outbox(reassign_sid), [])

    def test_unset_off_and_invalid_never_create_emr_intents(self):
        for connector_value in (None, "off", "not_a_connector"):
            with self.subTest(connector_value=connector_value):
                self._assert_no_selection_paths(connector_value)

    def _assert_selected_paths(self, connector_id):
        config.guardar({"CONNECTOR_EMR_SYNC": connector_id})
        config._reset_cache_para_tests()
        self.assertEqual(
            connectors.resolve(connectors.EMR_SYNC),
            (connector_id, "explicit"),
        )
        self.assertTrue(booking_requests._emr_sync_enabled())
        # This guard observes actual socket attempts without substituting a
        # connector.  Exact HSI must reach its configured vendor path; the
        # selected-but-unconfigured bridge must not reach any path at all.
        _env.set_env(self, TURNOS_OUTBOUND_KILL="1")
        outbound_guard.arm()

        approve_sid = self._new("30110011")
        status, _payload = self._approve(approve_sid)
        self.assertEqual(status, 200)
        self.assertEqual(self._outbox(approve_sid)[0]["operation"], "create")
        self.assertEqual(
            db_booking.get_booking_request(approve_sid)["hsi_estado"],
            "skip" if connector_id == "emr_bridge" else "pendiente",
        )

        cancel_sid = self._linked("30110012")
        status, _payload = booking_patient.cancel_request("30110012", cancel_sid)
        self.assertEqual(status, 200)
        self.assertEqual(self._outbox(cancel_sid)[0]["operation"], "cancel")
        # A linked appointment already exists in the EMR: the unconfigured
        # bridge cannot prove the cancel is safe to drop, so the row stays
        # pending and the intent stays claimable after configuration. Only a
        # first local create degrades to skip (approve above).
        self.assertEqual(db_booking.get_booking_request(cancel_sid)["hsi_estado"], "pendiente")
        self.assertNotEqual(self._outbox(cancel_sid)[0]["state"], "confirmed")

        reassign_sid = self._linked("30110013")
        status, _payload = booking_patient._reschedule_core(
            None, "30110013", reassign_sid,
            lambda: {"dia": "2099-07-" + ("27" if connector_id == "hsi" else "28"),
                     "hora": "11:00"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self._outbox(reassign_sid)[0]["operation"], "reassign")
        self.assertEqual(db_booking.get_booking_request(reassign_sid)["hsi_estado"], "pendiente")
        self.assertNotEqual(self._outbox(reassign_sid)[0]["state"], "confirmed")

    def test_explicit_hsi_uses_durable_runtime_paths(self):
        self._assert_selected_paths("hsi")
        self.assertGreater(outbound_guard.blocked_count(), 0)

    def test_unconfigured_bridge_uses_durable_paths_without_network(self):
        self._assert_selected_paths("emr_bridge")
        self.assertEqual(outbound_guard.blocked_count(), 0)

    def test_runtime_deselection_preserves_pending_rows_without_journal_work(self):
        """A live selection change is not a destructive journal transition.

        The durable row remains pending for a later explicit re-selection;
        both running tick entrypoints return before even adopting it.
        """
        day = 25
        for selected in ("hsi", "emr_bridge"):
            for removed in (None, "off", "not_a_connector"):
                with self.subTest(selected=selected, removed=removed):
                    config.guardar({"CONNECTOR_EMR_SYNC": selected})
                    dni = "30200" + str(day)
                    day += 1
                    sid = self._new(dni)
                    db_booking.approve(
                        sid, datetime.fromisoformat("2099-08-20T10:30:00-03:00"),
                        {"estado": "pendiente"},
                    )
                    config.guardar({"CONNECTOR_EMR_SYNC": removed})
                    self.assertEqual(hsi_journal.tick()["backfilled"], 0)
                    self.assertEqual(hsi_fallback.tick(), 0)
                    self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "pendiente")
                    self.assertEqual(self._outbox(sid), [])


if __name__ == "__main__":
    unittest.main()
