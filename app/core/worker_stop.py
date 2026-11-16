"""worker_stop.py - the queue worker's stop request, as seen by a long synchronous sweep.

procrastinate runs a synchronous task body in a thread and cannot cancel it: a SIGTERM that lands
while the translation warmup is filling the whole catalog waits for the last string, far past the
supervisor's stop budget (graceful drain + margin, 45 s by default), and the supervisor SIGKILLs the
worker (the `en` sweep ran 190 s under load, 2026-11-07). Cancelling the asyncio task does not reach
the thread, and asyncio.run() then waits for that same thread before the process can exit.

So the long sweeps stop themselves: `worker._boot_and_run` opens one stop per run (`begin`), the
signal callback sets it (`request`) next to `Worker.stop()`, and a sweep body takes `check()` when it
starts and asks `halted(stop)` between two units of work (a translation batch, a cache row, a summary,
a reminder, an HSI phase). A halted sweep returns what it finished; the rest is exactly what its
re-run contract (`worker._JOBS`) already covers - stale cache rows, unclaimed reminders, durable
journal rows - and the next run does it.

Outside the worker nothing passes a stop (`stop=None`): the web process's daemon threads and the
manual scripts run exactly as before.
"""
import os
import threading
import time

_current = threading.Event()


def begin():
    """A fresh, unset stop for one worker run; returns it for `end`."""
    global _current
    _current = threading.Event()
    return _current


def end(event):
    """Close the run that `begin` opened: the next run (an in-process test, a restart) starts unset.
    A body still running keeps the stop it took with `check()`, already set."""
    global _current
    if _current is event:
        _current = threading.Event()


def request():
    """The worker was asked to stop: every sweep of this run halts at its next unit boundary."""
    _current.set()


def check():
    """What a sweep body takes when it starts: a callable that turns True once this run's stop is
    requested. Taken once, at the start, so closing the run never un-stops a body still running."""
    return _current.is_set


def halted(stop):
    """True when the sweep should return now. `stop=None` (every caller outside the worker) never
    halts and costs nothing."""
    if stop is None:
        return False
    _test_step_hold()
    return bool(stop())


def _test_step_hold():
    """Test seam ONLY, double-gated like `queue_infra.test_hold`: with TURNOS_TEST_SEAMS=1 AND
    TURNOS_TEST_SWEEP_STEP_HOLD_S set, every unit boundary of a worker sweep waits that long, so a
    test can stop a real worker in the middle of a long sweep on any machine."""
    from app.core import queue_infra
    if not queue_infra.test_seams_enabled():
        return
    try:
        hold = float(os.environ.get("TURNOS_TEST_SWEEP_STEP_HOLD_S", "0") or 0)
    except ValueError:
        return
    if hold > 0:
        time.sleep(hold)
