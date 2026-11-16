"""clock.py - the one place the application asks what time it is.

    from core import clock
    clock.now()      # an aware datetime in the installation timezone (core/tz.py)
    clock.utcnow()   # the same instant, in UTC
    clock.today()    # the installation's calendar day

Why one place: a module that calls `datetime.now(...)` itself reads the machine's clock at the moment
it runs, so a test that depends on the hour or on the day (a window that starts "now", a day that ends
at the installation's midnight, 01:30 UTC being the evening before in Buenos Aires) is red only at
some hours - the flaky kind. A module that asks `clock.now()` can be given a fixed instant by the
test (`tests/_clock.py`: `freeze(case, "2026-10-27T01:30:00Z")`) and then answers the same at every
hour, in any timezone of the machine.

What it is not: a clock for the database. SQL that says `now()` runs on Postgres' clock, which a
test cannot freeze from Python; mask or compute such a value in the test (see
`tests/routes/_body_contract.py`). Nor is it a monotonic timer - deadlines and timeouts keep using
`time.monotonic()`.

A module moves to `clock` when a test needs to pin it (the seam appears where the flakiness is,
not by a sweep through the ~60 modules that read `datetime.now(tz.tzinfo())`); the first one is
`modules/turnos/booking_horizon.py`. Nothing in the application replaces `_source`: it is the test
seam, and `tests/_clock.py` is its only user.
"""
from datetime import date, datetime, timezone

from app.core import tz


def _wall_clock():
    return datetime.now(timezone.utc)


# The seam: tests replace this one name; every function below reads it at call time.
_source = _wall_clock


def utcnow() -> datetime:
    """The current instant, timezone-aware, in UTC."""
    instant = _source()
    if instant.tzinfo is None:
        raise ValueError("core.clock source returned a naive datetime: %r" % (instant,))
    return instant.astimezone(timezone.utc)


def now() -> datetime:
    """The current instant, timezone-aware, in the installation timezone."""
    return utcnow().astimezone(tz.tzinfo())


def today() -> date:
    """The installation's current calendar day."""
    return now().date()
