"""test_wait.py - tests/_wait.py waits for a condition and says what it waited for."""
import threading
import time
import unittest
from unittest.mock import patch

import _wait


class TestUntil(unittest.TestCase):
    def test_returns_the_truthy_value_as_soon_as_it_appears(self):
        calls = []

        def condition():
            calls.append(1)
            return "ready" if len(calls) == 3 else None
        self.assertEqual("ready", _wait.until(condition, "three calls"))
        self.assertEqual(3, len(calls))

    def test_waits_for_another_thread(self):
        done = threading.Event()
        worker = threading.Thread(target=done.set)
        worker.start()
        self.assertTrue(_wait.until(done.is_set, "the thread finishes"))
        worker.join()

    def test_names_what_it_waited_for_when_the_ceiling_is_reached(self):
        with self.assertRaises(AssertionError) as caught:
            _wait.until(lambda: False, "a thing that never happens", timeout=0.05)
        self.assertIn("a thing that never happens", str(caught.exception))


class TestLoadScaledCeiling(unittest.TestCase):
    """`scale_with_load=True`: the ceiling is the quiet-machine one times the current load per CPU."""

    def test_load_factor_is_load_per_cpu_clamped_between_one_and_the_cap(self):
        for load, cpus, expected in ((3.0, 18, 1.0), (90.0, 18, 5.0), (900.0, 18, _wait.MAX_LOAD_FACTOR)):
            with patch("os.getloadavg", return_value=(load, 0.0, 0.0)), patch("os.cpu_count", return_value=cpus):
                self.assertEqual(expected, _wait.load_factor())

    def test_load_factor_is_one_without_a_load_average(self):
        with patch("os.getloadavg", side_effect=OSError):
            self.assertEqual(1.0, _wait.load_factor())

    def test_a_busy_machine_keeps_waiting_past_the_quiet_ceiling(self):
        start = time.monotonic()

        def booted():
            return time.monotonic() - start >= 0.1    # slower than the 0.05 s quiet ceiling
        with patch.object(_wait, "load_factor", return_value=4.0):
            self.assertTrue(_wait.until(booted, "the boot", timeout=0.05, scale_with_load=True))

    def test_a_busy_machine_still_fails_a_real_hang_and_names_the_load(self):
        start = time.monotonic()
        with patch.object(_wait, "load_factor", return_value=3.0):
            with self.assertRaises(AssertionError) as caught:
                _wait.until(lambda: False, "a hang", timeout=0.02, scale_with_load=True)
        self.assertGreaterEqual(time.monotonic() - start, 0.06)
        self.assertTrue(str(caught.exception).startswith("timed out after"))
        self.assertIn("x load 3.0", str(caught.exception))
        self.assertIn("a hang", str(caught.exception))

    def test_without_the_flag_the_load_is_ignored(self):
        with patch.object(_wait, "load_factor", return_value=5.0) as factor:
            with self.assertRaises(AssertionError) as caught:
                _wait.until(lambda: False, "fixed", timeout=0.02)
        factor.assert_not_called()
        self.assertNotIn("load", str(caught.exception))


class TestStays(unittest.TestCase):
    def test_passes_when_the_condition_holds_for_the_whole_window(self):
        _wait.stays(lambda: True, "always true", for_s=0.03)

    def test_fails_as_soon_as_the_condition_stops_holding(self):
        calls = []

        def condition():
            calls.append(1)
            return len(calls) < 3
        with self.assertRaises(AssertionError) as caught:
            _wait.stays(condition, "two calls only", for_s=5)
        self.assertIn("two calls only", str(caught.exception))
        self.assertEqual(3, len(calls))


if __name__ == "__main__":
    unittest.main()
