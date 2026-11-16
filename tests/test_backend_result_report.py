"""Black-box contracts for scripts/run_backend_tests.py."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_backend_tests.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.check_unittest_coverage import inspect_log, inspect_result_report  # noqa: E402


class TestBackendResultReport(unittest.TestCase):
    def _run(self, source):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = root / "suite"
            suite.mkdir()
            (suite / "test_synthetic.py").write_text(source, encoding="utf-8")
            report = root / "result.json"
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--report", str(report),
                 "--start-directory", str(suite)],
                text=True, capture_output=True, timeout=15)
            self.assertTrue(report.exists(), proc.stderr)
            return proc, json.loads(report.read_text(encoding="utf-8")), report.stat()

    def test_real_skip_and_console_noise_are_reported_without_parsing_output(self):
        proc, report, metadata = self._run('''
import sys
import unittest
class Synthetic(unittest.TestCase):
    def test_skip(self):
        print("stdout noise")
        print("stderr noise", file=sys.stderr)
        self.skipTest("external private Pinamar pack is not configured")
''')
        self.assertEqual(proc.returncode, 0)
        self.assertIn("stdout noise", proc.stdout)
        self.assertIn("stderr noise", proc.stderr)
        self.assertEqual(report, {
            "schema_version": 1, "tests_run": 1, "failures": 0, "errors": 0,
            "unexpected_successes": 0, "expected_failures": 0,
            "skipped": [{"test_id": "test_synthetic.Synthetic.test_skip",
                         "reason": "external private Pinamar pack is not configured"}],
        })
        self.assertTrue(inspect_log(proc.stderr, minimum=1)["problems"],
                        "real stderr noise must reproduce the old parser failure")
        self.assertEqual(inspect_result_report(report, minimum=1)["problems"], [])
        self.assertEqual(metadata.st_mode & 0o777, 0o600)

    def test_setup_class_skip_is_a_single_zero_run_result(self):
        proc, report, _metadata = self._run('''
import unittest
class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise unittest.SkipTest("class unavailable")
    def test_one(self): pass
    def test_two(self): pass
''')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["tests_run"], 0)
        self.assertEqual(report["skipped"], [{
            "test_id": "setUpClass (test_synthetic.Synthetic)",
            "reason": "class unavailable",
        }])

    def test_failure_is_nonzero_and_report_contains_only_result_counts(self):
        proc, report, _metadata = self._run('''
import unittest
class Synthetic(unittest.TestCase):
    def test_failure(self): self.fail("private failure detail")
''')
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(report, {
            "schema_version": 1, "tests_run": 1, "failures": 1, "errors": 0,
            "unexpected_successes": 0, "expected_failures": 0, "skipped": [],
        })
        self.assertNotIn("private failure detail", json.dumps(report))

    def _run_two_modules(self, first, second):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = root / "suite"
            suite.mkdir()
            shutil.copy(ROOT / "tests" / "_lifespan.py", suite / "_lifespan.py")
            (suite / "test_a_first.py").write_text(first, encoding="utf-8")
            (suite / "test_b_second.py").write_text(second, encoding="utf-8")
            report = root / "result.json"
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--report", str(report),
                 "--start-directory", str(suite)],
                text=True, capture_output=True, timeout=30)
            self.assertTrue(report.exists(), proc.stderr)
            return proc, json.loads(report.read_text(encoding="utf-8"))

    _LEAKING = '''
import threading
import unittest
_STOP = threading.Event()
class Leaky(unittest.TestCase):
    def test_starts_a_daemon(self):
        threading.Thread(target=_STOP.wait, name="waitlist", daemon=True).start()
'''
    _CLEAN = '''
import unittest
class Clean(unittest.TestCase):
    def test_nothing(self): pass
'''

    def test_a_module_that_leaves_an_app_daemon_running_fails_the_run_by_name(self):
        proc, report = self._run_two_modules(self._LEAKING, self._CLEAN)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(report["errors"], 1, proc.stderr)
        self.assertIn("module test_a_first left app background threads running: waitlist",
                      proc.stderr)
        # The later, clean module is not blamed for the first one's thread.
        self.assertNotIn("module test_b_second left", proc.stderr)

    def test_a_module_that_stops_its_daemon_in_teardown_module_passes(self):
        clean_up = self._LEAKING + '''
def tearDownModule():
    _STOP.set()
    for thread in threading.enumerate():
        if thread.name == "waitlist":
            thread.join(5)
'''
        proc, report = self._run_two_modules(clean_up, self._CLEAN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["errors"], 0)


if __name__ == "__main__":
    unittest.main()
