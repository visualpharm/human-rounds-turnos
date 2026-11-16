"""Focused unit tests for the HSI thread/manual fallback.

No Postgres or remote HSI is used here. The fallback list and the narrow
``hsi_journal`` boundary are patched; the journal remains the sole owner of
remote replay. Other test modules can set ``TURNOS_SKIP_HSI`` process-wide,
so every test handles that flag explicitly.
"""

import os
import sys
import unittest
from unittest import mock
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.modules.turnos import hsi_fallback    # noqa: E402
from app.connectors.hsi import hsi_resolver  # noqa: E402
from app.modules.turnos import hsi_journal      # noqa: E402


def _row(sid, hsi_estado="error", en_horas=24):
    return {
        "id": sid, "estado": "aprobada", "hsi_estado": hsi_estado,
        "turno_dt": datetime.now(timezone.utc) + timedelta(hours=en_horas),
        "dni": "30111222", "nombre": "Ana", "apellido": "Prueba",
        "nacimiento": "1990-05-01", "sexo": "F",
        "servicio_id": "laboratorio-hospital",
    }


class Base(unittest.TestCase):
    def setUp(self):
        # HSI is an explicit opt-in (CONNECTOR_EMR_SYNC=hsi) — without it
        # hsi_journal._emr_selected() is False and hsi_fallback.tick() is a
        # no-op, so every test here that expects a real tick must select it.
        _env.set_env(self, TURNOS_SKIP_HSI=None, CONNECTOR_EMR_SYNC="hsi")
        self._orig_pend = db_booking.booking_requests_hsi_pending
        self.pendientes = []
        db_booking.booking_requests_hsi_pending = lambda desde=None: list(self.pendientes)

    def tearDown(self):
        db_booking.booking_requests_hsi_pending = self._orig_pend


class TestTick(Base):
    def _durable(self, results):
        """Patch only the journal boundary; never the old direct push path."""
        calls = []

        def replay_row(row):
            calls.append(row["id"])
            return results[len(calls) - 1]

        patcher = mock.patch.object(hsi_journal, "enqueue_and_replay_row", replay_row)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_skip_hsi_es_no_op(self):
        _env.set_env(self, TURNOS_SKIP_HSI="1")
        self.pendientes = [_row(1)]
        db_booking.booking_requests_hsi_pending = lambda desde=None: self.fail("no debe leer la DB")
        self.assertEqual(hsi_fallback.tick(), 0)

    def test_reintenta_por_el_journal_y_confirma_dos_filas(self):
        self.pendientes = [_row(1), _row(2, hsi_estado="pendiente")]
        calls = self._durable([{"state": "confirmed"}, {"state": "confirmed"}])
        self.assertEqual(hsi_fallback.tick(), 2)
        self.assertEqual(calls, [1, 2])

    def test_journal_sin_confirmar_corta_el_barrido(self):
        self.pendientes = [_row(1), _row(2)]
        calls = self._durable([{"state": "error"}, {"state": "confirmed"}])
        self.assertEqual(hsi_fallback.tick(), 0)
        self.assertEqual(calls, [1])

    def test_cancelled_unlinked_snapshot_does_not_starve_actionable_rows(self):
        cancelled_unlinked = dict(_row(10), estado="cancelada",
                                  hsi_appointment_id=None)
        linked_cancel = dict(_row(12), estado="cancelada",
                             hsi_appointment_id="remote-12")
        self.pendientes = [cancelled_unlinked, _row(11), linked_cancel]
        calls = []

        def replay_row(row):
            calls.append(row["id"])
            return None if row["id"] == 10 else {"state": "confirmed"}

        with mock.patch.object(hsi_journal, "enqueue_and_replay_row", replay_row):
            self.assertEqual(hsi_fallback.tick(), 2)
        self.assertEqual(calls, [11, 12])

    def test_cancelled_unlinked_skip_keeps_stop_first_for_attempted_intent(self):
        self.pendientes = [dict(_row(20), estado="cancelada",
                                hsi_appointment_id=None), _row(21), _row(22)]
        calls = self._durable([{"state": "error"}, {"state": "confirmed"}])

        self.assertEqual(hsi_fallback.tick(), 0)
        self.assertEqual(calls, [21])

    def test_stale_linked_snapshot_does_not_starve_later_row(self):
        self.pendientes = [_row(31), _row(32)]
        calls = []

        def replay_row(row):
            calls.append(row["id"])
            if row["id"] == 31:
                raise db_hsi.StaleHsiReassignTarget("stale local target")
            return {"state": "confirmed"}

        with mock.patch.object(hsi_journal, "enqueue_and_replay_row", replay_row):
            self.assertEqual(hsi_fallback.tick(), 1)
        self.assertEqual(calls, [31, 32])

    def test_remote_accepted_local_uncommitted_reuses_one_durable_intent(self):
        self.pendientes = [_row(3)]
        remote_creates, replays = [], []

        def replay_row(row):
            self.assertEqual(row["id"], 3)
            replays.append(row["id"])
            if len(replays) == 1:
                remote_creates.append("accepted")
                raise RuntimeError("simulated death after remote acceptance")
            return {"state": "confirmed"}

        with mock.patch.object(hsi_journal, "enqueue_and_replay_row", replay_row):
            self.assertEqual(hsi_fallback.tick(), 0)
            self.assertEqual(hsi_fallback.tick(), 1)
        self.assertEqual(remote_creates, ["accepted"])
        self.assertEqual(replays, [3, 3])

    def test_two_fallback_runs_have_one_effect_when_a_stale_row_reappears(self):
        self.pendientes = [_row(5)]
        effects = []

        def replay_row(_row):
            if not effects:
                effects.append("remote-and-local-confirmed")
                return {"state": "confirmed"}
            return None

        with mock.patch.object(hsi_journal, "enqueue_and_replay_row", side_effect=replay_row):
            self.assertEqual(hsi_fallback.tick(), 1)
            self.assertEqual(hsi_fallback.tick(), 0)
        self.assertEqual(effects, ["remote-and-local-confirmed"])

    def test_never_calls_the_historical_direct_push_path(self):
        self.pendientes = [_row(6)]
        calls = self._durable([{"state": "confirmed"}])
        # The historical direct push (`_push_hsi`) was deleted on 2026-11-06: the exact-item replay of the
        # journal is the only way this tick reaches the EMR.
        self.assertEqual(hsi_fallback.tick(), 1)
        self.assertEqual(calls, [6])

    def test_sync_off_uses_only_exact_item_replay_not_full_sync(self):
        self.pendientes = [_row(61)]
        _env.set_env(self, TURNOS_HSI_SYNC_OFF="1")
        with mock.patch.object(hsi_journal, "enqueue_and_replay_row", return_value={"state": "confirmed"}) as exact, \
             mock.patch.object(hsi_journal, "tick", side_effect=AssertionError("full sync must stay off")) as full, \
             mock.patch.object(hsi_journal, "reconcile", side_effect=AssertionError("reconciliation must stay off")) as reconcile, \
             mock.patch.object(hsi_resolver, "resolver_pass", side_effect=AssertionError("resolver must stay off")) as resolver:
            self.assertEqual(hsi_fallback.tick(), 1)
        exact.assert_called_once_with(self.pendientes[0])
        full.assert_not_called()
        reconcile.assert_not_called()
        resolver.assert_not_called()

    def test_db_caida_devuelve_cero(self):
        def explota(desde=None):
            raise RuntimeError("db down")
        db_booking.booking_requests_hsi_pending = explota
        self.assertEqual(hsi_fallback.tick(), 0)

    def test_journal_exception_never_escapes(self):
        self.pendientes = [_row(7)]
        with mock.patch.object(hsi_journal, "enqueue_and_replay_row",
                               side_effect=RuntimeError("journal db down")):
            self.assertEqual(hsi_fallback.tick(), 0)

    def test_no_connector_selected_es_no_op(self):
        """The gate: without an explicit CONNECTOR_EMR_SYNC selection,
        _emr_selected() is False and the fallback never ticks — even with
        pending rows and a working journal."""
        _env.set_env(self, CONNECTOR_EMR_SYNC=None)
        self.pendientes = [_row(1)]
        db_booking.booking_requests_hsi_pending = lambda desde=None: self.fail(
            "no debe leer la DB sin conector EMR seleccionado")
        self.assertEqual(hsi_fallback.tick(), 0)


class TestStart(unittest.TestCase):
    def test_off_por_env_no_arranca(self):
        _env.set_env(self, TURNOS_HSI_RESYNC_OFF="1")
        self.assertIsNone(hsi_fallback.start())


if __name__ == "__main__":
    unittest.main()
