"""test_worker_stop.py - the queue worker's stop reaches the long synchronous sweeps.

procrastinate cannot cancel a sync task body, so each long sweep asks `core.worker_stop` between two
units of work and returns what it finished (the subprocess proof - SIGTERM in the middle of the
translation sweep, exit 0 inside the budget, the next run finishes - is
`test_queue_worker.TestSweepStopsOnSignal`). Here, without a database or a worker process: the stop's
own lifecycle, and that every sweep halts at its boundary and leaves the rest untouched.
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import worker_stop  # noqa: E402


class _Countdown:
    """A stop that turns True after `n` checks: the sweep did n units, then the signal came."""

    def __init__(self, n):
        self.left = n
        self.asked = 0

    def __call__(self):
        self.asked += 1
        if self.left <= 0:
            return True
        self.left -= 1
        return False


class TestStopLifecycle(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_TEST_SEAMS=None, TURNOS_TEST_SWEEP_STEP_HOLD_S=None)
        self.run = worker_stop.begin()
        self.addCleanup(worker_stop.end, self.run)

    def test_none_never_halts(self):
        worker_stop.request()
        self.assertFalse(worker_stop.halted(None),
                         "outside the worker (threads, scripts) nothing passes a stop")

    def test_request_reaches_a_stop_taken_before_it(self):
        stop = worker_stop.check()
        self.assertFalse(worker_stop.halted(stop))
        worker_stop.request()
        self.assertTrue(worker_stop.halted(stop))

    def test_closing_the_run_never_unstops_a_running_body(self):
        stop = worker_stop.check()
        worker_stop.request()
        worker_stop.end(self.run)
        self.assertTrue(stop(), "the body still running keeps its set stop")
        self.assertFalse(worker_stop.check()(), "the next run starts unset")

    def test_end_of_an_older_run_leaves_the_current_one(self):
        older = self.run
        current = worker_stop.begin()
        self.addCleanup(worker_stop.end, current)
        worker_stop.end(older)
        worker_stop.request()
        self.assertTrue(current.is_set())

    def test_step_hold_needs_both_test_gates(self):
        _env.set_env(self, TURNOS_TEST_SWEEP_STEP_HOLD_S="30")
        with mock.patch.object(worker_stop.time, "sleep") as sleep:
            worker_stop.halted(lambda: False)
            sleep.assert_not_called()
            _env.set_env(self, TURNOS_TEST_SEAMS="1")
            worker_stop.halted(lambda: False)
            sleep.assert_called_once_with(30.0)


class TestTranslateBatchHalts(unittest.TestCase):
    def setUp(self):
        from app.core import translate, db_catalog
        self.translate = translate
        self.writes = []
        p = mock.patch.object(db_catalog, "translation_upsert",
                              side_effect=lambda key, *_a, **_k: self.writes.append(key))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(translate, "invalidate_rows")
        p.start()
        self.addCleanup(p.stop)
        n = translate.BATCH_SIZE * 3
        self.items = [(f"k{i}", f"texto {i}") for i in range(n)]

    def test_stops_between_batches_and_keeps_what_it_wrote(self):
        stop = _Countdown(2)    # two batches, then the signal
        out = self.translate.translate_batch(self.items, "en", stop=stop)
        size = self.translate.BATCH_SIZE
        self.assertEqual(len(out), 2 * size)
        self.assertEqual(self.writes, [k for k, _t in self.items[:2 * size]],
                         "the third batch is never asked: it stays stale for the next sweep")

    def test_without_a_stop_it_does_everything(self):
        out = self.translate.translate_batch(self.items, "en")
        self.assertEqual(len(out), len(self.items))

    def test_one_by_one_fallback_also_stops(self):
        size = self.translate.BATCH_SIZE
        with mock.patch.object(self.translate, "_machine_translate_batch", return_value=None), \
                mock.patch.object(self.translate, "translate",
                                  side_effect=lambda key, text, lang: f"en:{text}") as one:
            out = self.translate.translate_batch(self.items[:size], "en", stop=_Countdown(4))
        # one check before the batch, none before its first string, one before each next string:
        # strings 0..3 are done, the check before string 4 halts
        self.assertEqual(one.call_count, 4)
        self.assertEqual(len(out), 4)


class TestChartSummaryHalts(unittest.TestCase):
    def test_stops_before_the_next_summary(self):
        from app.ai import chart_summary
        from app.core import db_clinical
        pares = [(f"9990{i:04d}", "medico-x") for i in range(5)]
        with mock.patch.object(db_clinical, "upcoming_for_summaries", return_value=pares), \
                mock.patch.object(db_clinical, "summary_get", return_value=None), \
                mock.patch.object(chart_summary, "generate",
                                  return_value={"generado": "x"}) as gen:
            hechos = chart_summary.tick(stop=_Countdown(2))
        self.assertEqual(gen.call_count, 2)
        self.assertEqual(hechos, 2)


class TestRemindersHalt(unittest.TestCase):
    _NOON_ART = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)   # 12:00 in Buenos Aires

    def _rows(self):
        return [{"id": i} for i in range(5)]

    def test_tick_leaves_unclaimed_rows_for_the_next_sweep(self):
        from app.core import db_booking
        from app.notifications import reminders
        from app.notifications import send as notifications
        with mock.patch.object(db_booking, "booking_requests_to_remind", return_value=self._rows()), \
                mock.patch.object(db_booking, "claim_reminder", return_value=True) as claim, \
                mock.patch.object(notifications, "notify_reminder"):
            n = reminders.tick(now_utc=self._NOON_ART, stop=_Countdown(3))
        self.assertEqual(n, 3)
        self.assertEqual([c.args[0] for c in claim.call_args_list], [0, 1, 2],
                         "rows 3 and 4 are never claimed: the next sweep sends them")

    def test_tick_interview_too(self):
        from app.core import app_modules, db_booking
        from app.notifications import reminders
        with mock.patch.object(db_booking, "booking_requests_to_remind_interview",
                               return_value=self._rows()), \
                mock.patch.object(db_booking, "claim_interview_reminder", return_value=True) as claim, \
                mock.patch.object(app_modules.hooks, "call"):
            n = reminders.tick_interview(now_utc=self._NOON_ART, stop=_Countdown(1))
        self.assertEqual(n, 1)
        self.assertEqual(claim.call_count, 1)


class TestHsiJournalHalts(unittest.TestCase):
    def test_stops_between_phases(self):
        from app.modules.turnos import hsi_journal
        with mock.patch.object(hsi_journal, "_off", return_value=False), \
                mock.patch.object(hsi_journal, "_emr_selected", return_value=True), \
                mock.patch.object(hsi_journal, "backfill_pending", return_value=1) as backfill, \
                mock.patch.object(hsi_journal, "replay", return_value=2) as replay, \
                mock.patch.object(hsi_journal, "reconcile") as reconcile, \
                mock.patch.object(hsi_journal.connectors, "adapter_for") as adapter:
            out = hsi_journal.tick(stop=_Countdown(2))
        backfill.assert_called_once()
        replay.assert_called_once()
        reconcile.assert_not_called()
        adapter.assert_not_called()
        self.assertEqual((out["backfilled"], out["replayed"], out["appointments"]), (1, 2, 0))


class TestSignalCallbackRequestsTheStop(unittest.TestCase):
    def test_first_signal_sets_the_run_stop_next_to_worker_stop(self):
        import signal
        from app import worker
        run = worker_stop.begin()
        self.addCleanup(worker_stop.end, run)
        stop = worker_stop.check()
        fake = mock.Mock()
        callback = worker._make_async_signal_callback(worker._SignalState(), {"worker": fake})
        callback(signal.SIGTERM)
        fake.stop.assert_called_once_with()
        self.assertTrue(stop(), "the sync sweep in flight sees the stop")


if __name__ == "__main__":
    unittest.main()
