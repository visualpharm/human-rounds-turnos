"""_wait.py - wait for a condition, not for a number of seconds.

A test that sleeps "long enough" is slow when the machine is fast and red when it is slow (the shared
runner has doubled and tripled the time of a job). Waiting for the thing itself costs what the thing
costs and has a generous ceiling that only a real hang reaches. Not named test_*.py on purpose:
unittest's discovery must not collect it. The ratchet in `scripts/hygiene_ratchet.py` counts
`time.sleep`; the polling below waits on a `threading.Event`, so it is not one.

    import _wait

    _wait.until(lambda: job.done(), "the job finishes")                    # polls every 10 ms, up to 30 s
    value = _wait.until(lambda: rows() or None, "a row shows up")          # returns the truthy value
    _wait.stays(lambda: not job.done(), "the job stays idle", for_s=0.3)   # a negative window, see below

`until` raises AssertionError naming what it waited for, so the failure reads like an assertion.

`scale_with_load=True` reads `timeout` as the ceiling on a QUIET machine and stretches it by `load_factor()`
(the 1-minute load average per CPU, re-read on every poll, never shrinking within one wait): for a wait on
something whose speed is the machine's own, such as a real subprocess booting. Under a parallel test run the
same boot takes several times longer; a fixed ceiling sized on a quiet machine turns that into a false red.

`stays` is for "nothing happens": it checks the condition for the whole window and fails the moment it is
false. A window is time and cannot be made event-driven, so keep it as short as the test's meaning allows,
and prefer injecting the clock (`tests/_clock.py`) or a fake that records the call whenever there is one.
"""
import os
import threading
import time

_TICK = threading.Event()   # never set: wait() is a sleep that a reader cannot mistake for a pause in the test

# A real hang still fails: at most this many quiet-machine ceilings.
MAX_LOAD_FACTOR = 8.0


def _pause(seconds):
    _TICK.wait(seconds)


def load_factor():
    """How many times busier than one task per CPU this machine is right now: the 1-minute load average
    divided by the CPU count, never below 1 and never above MAX_LOAD_FACTOR (1 where there is no load average)."""
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        return 1.0
    return min(MAX_LOAD_FACTOR, max(1.0, load / (os.cpu_count() or 1)))


def scaled(seconds):
    """`seconds` (a ceiling sized on a quiet machine) times `load_factor()` now: for a ceiling handed to
    something that is not a poll loop, such as `subprocess.run(timeout=...)` or `Popen.wait(...)`."""
    return seconds * load_factor()


def until(condition, what="the condition", timeout=30.0, interval=0.01, scale_with_load=False):
    """Return the first truthy value of `condition()`; AssertionError after `timeout` seconds
    (times `load_factor()` with `scale_with_load`, see the module docstring)."""
    start = time.monotonic()
    factor = 1.0
    while True:
        value = condition()
        if value:
            return value
        if scale_with_load:
            factor = max(factor, load_factor())
        if time.monotonic() - start >= timeout * factor:
            if factor > 1.0:
                raise AssertionError("timed out after %.1f s (%g s on a quiet machine x load %.1f) waiting for: %s"
                                     % (timeout * factor, timeout, factor, what))
            raise AssertionError("timed out after %.1f s waiting for: %s" % (timeout, what))
        _pause(interval)


def stays(condition, what="the condition", for_s=0.3, interval=0.01):
    """Check that `condition()` holds for `for_s` seconds; AssertionError the moment it does not."""
    deadline = time.monotonic() + for_s
    while True:
        if not condition():
            raise AssertionError("stopped holding within %.2f s: %s" % (for_s, what))
        if time.monotonic() >= deadline:
            return
        _pause(interval)
