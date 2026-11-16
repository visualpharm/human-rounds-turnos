"""test_run_backend_shards_run.py — `run_backend_shards.run` in THIS process (the steps it is cut into).

`test_run_backend_shards.py` drives the runner as a subprocess, which the coverage of `scripts/` cannot see. These
tests call `main()` over throwaway suites in-process and pin what the run prints, returns and writes: the plan, the
no-modules and no-coverage refusals, the verdict lines, the failure block with its three kinds of entry, the merged
report with its `sharding` block, the timings files and the exit code.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import _env  # noqa: E402
import run_backend_shards as shards  # noqa: E402

PASSING = """
    import unittest
    class T(unittest.TestCase):
        def test_a(self): pass
        def test_b(self): pass
    """
FAILING = """
    import unittest
    class T(unittest.TestCase):
        def test_boom(self): self.fail("boom-marker")
        def test_ok(self): pass
    """
DYING = """
    import os, unittest
    class T(unittest.TestCase):
        def test_die(self): os._exit(3)
    """
SKIPPING = """
    import unittest
    class T(unittest.TestCase):
        @unittest.skip("why")
        def test_s(self): pass
        @unittest.expectedFailure
        def test_x(self): self.fail("x")
        def test_p(self): pass
    """
KILLED = """
    import os, signal, unittest
    class T(unittest.TestCase):
        def test_gone(self):
            print("last-words-marker", flush=True)
            os.kill(os.getpid(), signal.SIGKILL)
    """
# Long enough that waiting for it would blow every bound the tests below set.
SLEEPER = """
    import time, unittest
    class T(unittest.TestCase):
        def test_wait(self): time.sleep(120)
    """
SHORT = """
    import time, unittest
    class T(unittest.TestCase):
        def test_wait(self): time.sleep(2.5)
    """


class TestRunInProcess(unittest.TestCase):
    def setUp(self):
        self.enterContext(_env.patched_env({"TEST_DATABASE_URL": None, "DATABASE_URL": None}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def suite(self, bodies):
        suite = Path(self.tmp.name, "suite")
        suite.mkdir(exist_ok=True)
        for name, body in bodies.items():
            suite.joinpath(f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
        return str(suite)

    def run_main(self, bodies, *args):
        suite = self.suite(bodies)
        report = os.path.join(self.tmp.name, "r.json")
        argv = ["--start-directory", suite, "--report", report,
                "--log-dir", os.path.join(self.tmp.name, "logs"),
                "--timings", os.path.join(self.tmp.name, "none.json"), *args]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = shards.main(argv)
        return code, out.getvalue(), err.getvalue(), report

    def test_a_clean_run_prints_the_verdict_and_writes_the_merged_report(self):
        timings = os.path.join(self.tmp.name, "t.json")
        code, out, _err, report = self.run_main(
            {"test_one": PASSING, "test_two": PASSING, "test_three": PASSING},
            "--shards", "2", "--timings-out", timings)
        self.assertEqual(code, 0, out)
        self.assertIn("run_backend_shards: 3 modules, 2 shards (+0 serial-only)", out)
        self.assertIn("shard1: ", out)
        self.assertIn("Ran 6 tests in ", out)
        self.assertIn("(2 shards)", out)
        self.assertTrue(out.rstrip().endswith("OK"), out[-200:])
        self.assertNotIn("FAILURES OF ALL SHARDS", out)
        data = json.loads(Path(report).read_text())
        self.assertEqual(data["tests_run"], 6)
        self.assertEqual(data["sharding"]["shards"], 2)
        self.assertEqual(data["sharding"]["serial_modules"], 0)
        self.assertEqual(sorted(data["sharding"]["shard_seconds"]), ["shard1", "shard2"])
        self.assertEqual(sorted(json.loads(Path(timings).read_text())["modules"]),
                         ["test_one", "test_three", "test_two"])

    def test_a_failing_file_is_named_in_the_block_of_all_shards(self):
        code, out, _err, _report = self.run_main({"test_one": PASSING, "test_two": FAILING}, "--shards", "2")
        self.assertEqual(code, 1)
        self.assertIn("FAILURES OF ALL SHARDS", out)
        self.assertIn("boom-marker", out)
        self.assertIn("FAILED (failures=1)", out)

    def test_a_file_that_dies_has_no_report_and_fails_the_run(self):
        code, out, _err, _report = self.run_main({"test_die": DYING}, "--shards", "1")
        self.assertEqual(code, 1)
        self.assertIn("--- test_die: no result report (exit code 3) ---", out)
        self.assertIn("FAILED (run stopped: shard1: test_die died: exit code 3)", out)

    def test_a_file_killed_by_sigkill_stops_the_run_at_once_and_is_named_with_its_signal(self):
        # 05.11: a lane that loses a file must not leave the run silent until every other lane ends.
        began = time.monotonic()
        code, out, _err, _report = self.run_main(
            {"test_a_killed": KILLED, "test_b_sleeper": SLEEPER}, "--shards", "2")
        self.assertLess(time.monotonic() - began, 60, out)
        self.assertEqual(code, 1, out)
        self.assertIn("shard1: test_a_killed died: killed by SIGKILL", out)
        self.assertIn("last-words-marker", out)  # the tail of what the file printed
        self.assertIn("--- test_a_killed: no result report (killed by SIGKILL) ---", out)
        self.assertIn("--- test_b_sleeper: stopped while running ---", out)
        self.assertNotIn("killed on timeout", out)

    def test_the_run_deadline_stops_every_lane_and_lists_what_never_ran(self):
        code, out, _err, _report = self.run_main(
            {"test_a_sleeper": SLEEPER, "test_b_after": PASSING}, "--shards", "1", "--deadline", "3")
        self.assertEqual(code, 1, out)
        self.assertIn("deadline of 3s reached", out)
        self.assertIn("--- test_a_sleeper: stopped while running ---", out)
        self.assertIn("--- test_b_after: never ran ---", out)

    def test_heartbeats_go_to_the_log_and_to_the_progress_stream(self):
        progress = Path(self.tmp.name, "progress.txt")
        with progress.open("w", encoding="utf-8") as stream:
            code, out, _err, _report = self.run_main(
                {"test_one": SHORT}, "--shards", "1", "--heartbeat", "1",
                "--progress-fd", str(stream.fileno()))
        self.assertEqual(code, 0, out)
        self.assertIn("heartbeat", out)
        self.assertIn("shard1 1/1 test_one", out)
        seen = progress.read_text(encoding="utf-8")
        self.assertIn("heartbeat", seen)
        self.assertIn("run_backend_shards: OK", seen)

    def test_a_lane_that_cannot_reset_its_database_stops_the_run_with_the_reason(self):
        calls = []

        def maint(_url, statements):
            calls.append(statements)
            if len(calls) > 1:  # the reset between the lane's two files
                raise RuntimeError("database-reset-marker")

        self.enterContext(_env.patched_env({"TEST_DATABASE_URL": "postgresql://u@127.0.0.1:9/x"}))
        self.enterContext(mock.patch.object(shards, "_maint_exec", maint))
        code, out, _err, _report = self.run_main(
            {"test_a": PASSING, "test_b": PASSING}, "--shards", "1")
        self.assertEqual(code, 1, out)
        self.assertIn("shard1 stopped: RuntimeError('database-reset-marker')", out)
        self.assertIn("--- test_b: never ran ---", out)

    def test_skips_and_expected_failures_are_flagged_on_the_verdict_line(self):
        code, out, _err, _report = self.run_main({"test_one": SKIPPING}, "--shards", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("OK (skipped=1, expected failures=1)", out)

    def test_plan_prints_the_partition_and_runs_nothing(self):
        code, out, _err, report = self.run_main(
            {"test_one": PASSING, "test_two": PASSING, "test_three": PASSING}, "--shards", "2", "--plan")
        self.assertEqual(code, 0)
        self.assertIn("shard 1: ", out)
        self.assertIn("serial group: 0 modules", out)
        self.assertFalse(os.path.exists(report))

    def test_no_test_modules_is_a_refusal(self):
        code, out, err, _report = self.run_main({}, "--shards", "2")
        self.assertEqual(code, 2)
        self.assertIn("no test modules found", err)
        self.assertEqual(out, "")

    def test_coverage_dir_without_coverage_py_is_a_refusal(self):
        previous = sys.modules.get("coverage", shards)
        sys.modules["coverage"] = None  # an import of it raises ImportError
        try:
            code, _out, err, _report = self.run_main(
                {"test_one": PASSING}, "--shards", "1",
                "--coverage-dir", os.path.join(self.tmp.name, "cov"))
        finally:
            if previous is shards:
                sys.modules.pop("coverage", None)
            else:
                sys.modules["coverage"] = previous
        self.assertEqual(code, 2)
        self.assertIn("--coverage-dir needs coverage.py", err)

    def test_update_seed_folds_the_measured_seconds_into_the_seed_file(self):
        seed = Path(self.tmp.name, "seed.json")
        seed.write_text(json.dumps({"schema_version": 1, "modules": {"test_old": 3.0}}), encoding="utf-8")
        original = shards.SEED_TIMINGS
        shards.SEED_TIMINGS = seed
        self.addCleanup(setattr, shards, "SEED_TIMINGS", original)
        code, out, _err, _report = self.run_main({"test_one": PASSING}, "--shards", "1", "--update-seed",
                                         "--timings", str(seed))
        self.assertEqual(code, 0, out)
        modules = json.loads(seed.read_text())["modules"]
        self.assertEqual(sorted(modules), ["test_old", "test_one"])

    def test_a_plain_run_leaves_the_seed_alone_and_says_nothing_new(self):
        seed = Path(self.tmp.name, "seed.json")
        seed.write_text(json.dumps({"schema_version": 1, "modules": {"test_old": 3.0}}), encoding="utf-8")
        code, out, _err, _report = self.run_main({"test_one": PASSING}, "--shards", "1", "--timings", str(seed))
        self.assertEqual(code, 0, out)
        self.assertNotIn("seed:", out)
        self.assertEqual(json.loads(seed.read_text())["modules"], {"test_old": 3.0})

    def test_seed_out_writes_the_refreshed_seed_drops_gone_files_and_warns(self):
        seed = Path(self.tmp.name, "seed.json")
        seed.write_text(json.dumps({"schema_version": 1, "modules": {"test_old": 3.0}}), encoding="utf-8")
        out_path = Path(self.tmp.name, "out", "seed.json")
        code, out, _err, _report = self.run_main({"test_one": PASSING}, "--shards", "1", "--timings", str(seed),
                                                 "--seed-out", str(out_path))
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(json.loads(out_path.read_text())["modules"]), ["test_one"])
        self.assertIn("backend-timings-seed.json", out)
        self.assertEqual(json.loads(seed.read_text())["modules"], {"test_old": 3.0})


if __name__ == "__main__":
    unittest.main()
