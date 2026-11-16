"""A worker that overruns its shutdown budget leaves the stack of every thread
in the test failure.

`WorkerProc.stop()` used to report only the tail of the worker's own log: a
worker that logged "requesting worker shutdown" and then never exited (seen
once in 20 runs of test_queue_worker.py under load, 2026-11-07) left nothing
that said where it hung. Now the harness sends SIGUSR2 to the worker before
the SIGKILL and faulthandler writes all thread stacks into the failure.

The child here is a stand-in `worker.py` in a temporary directory (no
database, no procrastinate): it ignores SIGTERM and blocks its main thread on
a lock another thread holds — the shape of a shutdown deadlock.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _workerproc  # noqa: E402

_STAND_IN = '''
import signal
import sys
import threading

signal.signal(signal.SIGTERM, signal.SIG_IGN)
held = threading.Lock()
held.acquire()
taken = threading.Event()


def keeper_holds_the_lock():
    taken.set()
    threading.Event().wait()


threading.Thread(target=keeper_holds_the_lock, daemon=True).start()
taken.wait()


def shutdown_waits_for_the_lock():
    print("[worker] ready to hang", file=sys.stderr, flush=True)
    held.acquire()


shutdown_waits_for_the_lock()
'''


class TestThreadDumpBeforeKill(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.mkdir(os.path.join(tmp.name, "app"))
        with open(os.path.join(tmp.name, "app", "worker.py"), "w", encoding="utf-8") as f:
            f.write(_STAND_IN)
        real_root = _workerproc.ROOT
        _workerproc.ROOT = tmp.name
        self.addCleanup(setattr, _workerproc, "ROOT", real_root)
        self.env = {"PATH": os.environ.get("PATH", "")}
        self.worker = _workerproc.WorkerProc(self.env)
        self.addCleanup(self.worker.cleanup)

    def test_overrun_reports_every_thread_stack(self):
        self.worker.wait_line("ready to hang", timeout=20)
        with self.assertRaises(AssertionError) as caught:
            self.worker.stop(timeout=1)
        message = str(caught.exception)
        self.assertIn("exceeded the 1s shutdown budget", message)
        self.assertIn(_workerproc.WorkerProc.THREAD_DUMP_HEADER, message)
        # The hung frame of the main thread and the frame of the other thread.
        self.assertIn("shutdown_waits_for_the_lock", message)
        self.assertIn("keeper_holds_the_lock", message)
        self.assertIsNotNone(self.worker.proc.poll(), "the worker must be gone after stop()")

    def test_the_callers_env_is_not_changed(self):
        # The flag goes into the child's copy; a suite reusing its env dict
        # for the next spawn or an assertion sees exactly what it built.
        self.worker.wait_line("ready to hang", timeout=20)
        self.assertNotIn("PYTHONPATH", self.env)


if __name__ == "__main__":
    unittest.main()
