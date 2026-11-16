"""_clock.py - give a test its own "now" (the application's clock, `core/clock.py`).

A test that depends on the day or the hour is red only at some hours. Freeze the application's clock
at the start of the test and it answers the same at 01:30 UTC and at 15:00 UTC, in any timezone of
the machine. Not named test_*.py on purpose: unittest's discovery must not collect it.

    import _clock

    class TestX(unittest.TestCase):
        def test_a_window_that_starts_now(self):
            clock = _clock.freeze(self, "2026-10-27T01:30:00Z")   # the evening of the 26th in Buenos Aires
            ...                                                   # core.clock.now() / today() / utcnow()
            clock.advance(hours=2)                                 # time passes only when the test says so

`freeze(case)` with no instant freezes at the wall clock read ONCE, whole seconds: a test that builds
rows "two days from now" and queries them a few statements later no longer straddles a second
boundary (the `_at()` flake of test_db_booking_store). `case` is a TestCase instance (undone after the
test) or a TestCase class (undone after the class). The freeze is undone by a cleanup, never left on.

It freezes what goes through `core.clock`. SQL `now()` runs on Postgres' clock, and a module that still
calls `datetime.now(...)` itself keeps reading the machine's; such a module moves to `core.clock` in the
change that pins it (see `modules/turnos/booking_horizon.py`).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import clock as core_clock  # noqa: E402


class FrozenClock:
    """The source `core.clock` reads while frozen: a fixed instant that moves only on `advance` / `set`."""

    def __init__(self, instant):
        self.instant = instant

    def __call__(self):
        return self.instant

    def advance(self, **delta):
        """Move forward (negative values move back): `advance(days=1)`, `advance(minutes=30)`."""
        self.instant += timedelta(**delta)
        return self.instant

    def set(self, at):
        self.instant = _instant(at)
        return self.instant


def _instant(at):
    if at is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    if at.tzinfo is None:
        raise ValueError("freeze needs an aware instant (say 'Z' or an offset): %r" % (at,))
    return at.astimezone(timezone.utc)


def freeze(case, at=None):
    """Freeze `core.clock` for the running test or class; returns the FrozenClock to `advance`."""
    frozen = FrozenClock(_instant(at))
    patcher = mock.patch.object(core_clock, "_source", frozen)
    patcher.start()
    (case.addClassCleanup if isinstance(case, type) else case.addCleanup)(patcher.stop)
    return frozen
