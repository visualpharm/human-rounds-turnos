"""A signal that lands while the worker is writing to stderr.

The plain SIGTERM/SIGINT handler of ``worker.main()`` logs through
``worker._log``. When the signal arrives while the main thread is inside its
own ``print`` to stderr, the handler's ``print`` re-enters the same
``BufferedWriter`` and CPython raises ``RuntimeError: reentrant call``; the
exception left the handler and the worker exited 1 instead of shutting down
gracefully (1 of 40 rapid spawn/SIGTERM rounds at load ~200, 2026-11-06).

The stream below re-enters exactly there: its raw ``write`` (called with
the buffer's lock held) runs what a signal handler would run.
"""
import io
import os
import signal
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import worker  # noqa: E402


class _InterruptingRaw(io.RawIOBase):
    """A raw stream whose first write runs ``on_first_write`` mid-write."""

    def __init__(self, on_first_write):
        super().__init__()
        self.on_first_write = on_first_write
        self.chunks = []

    def writable(self):
        return True

    def write(self, b):
        hook, self.on_first_write = self.on_first_write, None
        if hook is not None:
            hook()
        self.chunks.append(bytes(b))
        return len(b)


def _stream(on_first_write):
    raw = _InterruptingRaw(on_first_write)
    text = io.TextIOWrapper(io.BufferedWriter(raw), encoding="utf-8",
                            line_buffering=True)
    return raw, text


class TestLogInsideSignalHandler(unittest.TestCase):
    def setUp(self):
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(os.close, self.read_fd)
        self.addCleanup(os.close, self.write_fd)

    def _fallback_text(self):
        os.set_blocking(self.read_fd, False)
        try:
            return os.read(self.read_fd, 65536).decode("utf-8")
        except BlockingIOError:
            return ""

    def test_first_signal_during_a_stderr_write_records_the_stop(self):
        state = worker._SignalState()
        raw, stream = _stream(
            lambda: state.request_stop(signal.SIGTERM, "the test"))
        with patch.object(worker.sys, "stderr", stream), \
                patch.object(worker, "_STDERR_FD", self.write_fd, create=True):
            worker._log("outer line")
        self.assertTrue(state.stop_requested)
        self.assertEqual(state.signal_count, 1)
        self.assertIn("[worker] outer line\n",
                      b"".join(raw.chunks).decode("utf-8"))
        self.assertIn("SIGTERM/SIGINT received during the test",
                      self._fallback_text())

    def test_log_outside_a_handler_still_goes_through_the_stream(self):
        raw, stream = _stream(None)
        with patch.object(worker.sys, "stderr", stream), \
                patch.object(worker, "_STDERR_FD", self.write_fd, create=True):
            worker._log("plain line")
        self.assertEqual(b"".join(raw.chunks), b"[worker] plain line\n")
        self.assertEqual(self._fallback_text(), "")


if __name__ == "__main__":
    unittest.main()
