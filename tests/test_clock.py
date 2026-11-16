"""test_clock.py - core/clock.py, the application's one "now", and tests/_clock.py, which freezes it.

The application asks `core.clock` what time it is (booking_horizon is the first module that does); a test
pins that answer instead of reading the machine's clock, so it is the same at every hour and in every
timezone of the machine.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _clock  # noqa: E402
from app.core import clock, tz  # noqa: E402
from app.modules.turnos import booking_horizon  # noqa: E402

LATE_EVENING_BA = "2026-10-27T01:30:00Z"     # 22:30 on the 26th in Buenos Aires
MIDDAY_BA = "2026-10-27T15:00:00Z"            # 12:00 on the 27th


class TestTheApplicationClock(unittest.TestCase):
    def test_unfrozen_it_is_the_wall_clock_and_aware(self):
        before = datetime.now(timezone.utc)
        read = clock.utcnow()
        after = datetime.now(timezone.utc)
        self.assertEqual(timedelta(0), read.utcoffset())
        self.assertTrue(before <= read <= after)
        self.assertEqual(tz.tzinfo().utcoffset(read.replace(tzinfo=None)), clock.now().utcoffset())

    def test_now_and_today_use_the_installation_day_not_the_machines(self):
        _clock.freeze(self, LATE_EVENING_BA)
        self.assertEqual(datetime(2026, 10, 26, 22, 30, tzinfo=tz.tzinfo()), clock.now())
        self.assertEqual(date(2026, 10, 26), clock.today())
        self.assertEqual(datetime(2026, 10, 27, 1, 30, tzinfo=timezone.utc), clock.utcnow())

    def test_a_naive_source_is_refused(self):
        frozen = _clock.freeze(self, MIDDAY_BA)
        frozen.instant = datetime(2026, 10, 27, 15, 0)
        with self.assertRaises(ValueError):
            clock.utcnow()


class TestFreezing(unittest.TestCase):
    def test_a_frozen_clock_moves_only_when_told(self):
        frozen = _clock.freeze(self, MIDDAY_BA)
        first = clock.utcnow()
        self.assertEqual(first, clock.utcnow())
        frozen.advance(hours=13)
        self.assertEqual(first + timedelta(hours=13), clock.utcnow())
        self.assertEqual(date(2026, 10, 28), clock.today())
        frozen.set("2026-01-01T00:00:00-03:00")
        self.assertEqual(datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc), clock.utcnow())

    def test_without_an_instant_it_freezes_the_wall_clock_once_in_whole_seconds(self):
        before = datetime.now(timezone.utc)
        _clock.freeze(self)
        read = clock.utcnow()
        self.assertEqual(0, read.microsecond)
        self.assertTrue(before - timedelta(seconds=1) <= read <= datetime.now(timezone.utc))
        self.assertEqual(read, clock.utcnow())

    def test_a_naive_instant_is_refused(self):
        with self.assertRaises(ValueError):
            _clock.freeze(self, "2026-10-27T01:30:00")

    def test_the_freeze_is_undone_with_the_test(self):
        class Holder(unittest.TestCase):
            def runTest(self):
                pass
        case = Holder()
        source = clock._source
        _clock.freeze(case, MIDDAY_BA)
        self.assertIsNot(source, clock._source)
        case.doCleanups()
        self.assertIs(source, clock._source)

    def test_a_class_freeze_is_undone_with_the_class(self):
        class Holder(unittest.TestCase):
            def runTest(self):
                pass
        source = clock._source
        _clock.freeze(Holder, MIDDAY_BA)
        self.assertIsNot(source, clock._source)
        Holder.doClassCleanups()
        self.assertIs(source, clock._source)


class TestBookingHorizonFollowsTheClock(unittest.TestCase):
    """The first module on the clock: the last day of the window opens at HH:MM of the site's local clock."""

    DOCTOR = {"agenda": {"dias_anticipacion": 7, "apertura_turnos": "08:00"}}

    def test_the_last_day_opens_when_the_installation_clock_reaches_the_hour(self):
        frozen = _clock.freeze(self, "2026-10-27T10:30:00Z")           # 07:30 in Buenos Aires
        self.assertEqual(6, booking_horizon.available_days(None, self.DOCTOR))
        frozen.advance(minutes=45)                                      # 08:15
        self.assertEqual(7, booking_horizon.available_days(None, self.DOCTOR))

    def test_an_explicit_now_still_wins(self):
        _clock.freeze(self, "2026-10-27T10:30:00Z")
        self.assertEqual(7, booking_horizon.available_days(None, self.DOCTOR, datetime(2026, 10, 27, 9, 0)))


if __name__ == "__main__":
    unittest.main()
