"""test_run_backend_shards.py — the sharded backend runner (scripts/run_backend_shards.py).

Pins what makes a parallel run trustworthy: every test module runs exactly
once, the plan is a pure function of its inputs (same partition on every
machine), the merged report has the serial schema so the coverage guard gates
it unchanged, each shard gets its OWN database (dropped afterwards), a crashed
shard fails the run, and the worker-leak gate looks only at this process's
workers (a live worker of a sibling shard is not a leak).
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import check_unittest_coverage  # noqa: E402
import run_backend_shards as shards  # noqa: E402
import _workerproc  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "run_backend_shards.py")


class TestPlan(unittest.TestCase):
    MODULES = [f"test_m{i:02d}" for i in range(23)]
    TIMINGS = {m: float((i * 7) % 11 + 1) for i, m in enumerate(MODULES)}

    def plan(self, n=4, serial_only=None, timings=None):
        return shards.plan_shards(self.MODULES, self.TIMINGS if timings is None else timings,
                                  n, serial_only={} if serial_only is None else serial_only)

    def test_every_module_runs_exactly_once(self):
        buckets, serial, _ = self.plan(serial_only={"test_m03": "why"})
        flat = [m for b in buckets for m in b] + serial
        self.assertEqual(sorted(flat), sorted(self.MODULES))
        self.assertEqual(serial, ["test_m03"])

    def test_plan_is_deterministic_and_modules_run_in_name_order(self):
        first = self.plan()
        self.assertEqual(first, self.plan())
        for bucket in first[0]:
            self.assertEqual(bucket, sorted(bucket))

    def test_load_is_balanced(self):
        buckets, _, loads = self.plan(n=4)
        self.assertLessEqual(max(loads) - min(loads), max(self.TIMINGS.values()))

    def test_untimed_module_still_lands_in_a_shard(self):
        buckets, _, _ = self.plan(timings={})
        self.assertEqual(sorted(m for b in buckets for m in b), sorted(self.MODULES))

    def test_more_shards_than_modules_does_not_make_empty_shards(self):
        buckets, _, _ = shards.plan_shards(["test_a", "test_b"], {}, 9, serial_only={})
        self.assertEqual(len(buckets), 2)

    def test_real_suite_plan_covers_every_discovered_module(self):
        modules = shards.discover_modules()
        self.assertGreater(len(modules), 300)
        buckets, serial, _ = shards.plan_shards(modules, shards.load_timings(shards.SEED_TIMINGS),
                                                shards.DEFAULT_SHARDS)
        self.assertEqual(sorted([m for b in buckets for m in b] + serial), modules)
        self.assertTrue(set(shards.SERIAL_ONLY) <= set(modules),
                        "SERIAL_ONLY names a module that does not exist")
        self.assertTrue(all(reason.strip() for reason in shards.SERIAL_ONLY.values()),
                        "every serial-only module must say why")

    def test_seed_timings_are_usable_and_not_far_behind_the_suite(self):
        # A module missing from the seed is balanced by file size, so a new
        # test file never needs a seed edit — but a seed that has lost most of
        # the suite would silently turn the balancing into a guess.
        timings = shards.load_timings(shards.SEED_TIMINGS)
        modules = shards.discover_modules()
        self.assertLessEqual(
            shards.unseeded_share(modules, timings), shards.MAX_UNSEEDED_SHARE,
            "too many test files without seconds in scripts/backend_test_timings.json: copy the "
            "CI artifact backend-timings-seed.json of the last green main pipeline over it "
            "(or python3 scripts/update_backend_timings.py)")

    def test_next_seed_folds_a_run_in_and_drops_what_is_gone(self):
        seed = shards.next_seed({"a": 1.0, "b": 2.0, "gone": 9.0}, {"b": 3.0, "c": 4.0, "x": 5.0},
                                ["a", "b", "c"])
        self.assertEqual(seed, {"a": 1.0, "b": 3.0, "c": 4.0})

    def test_next_seed_without_modules_keeps_everything(self):
        # the shape --update-seed always had
        self.assertEqual(shards.next_seed({"gone": 9.0, "b": 2.0}, {"b": 3.0}), {"b": 3.0, "gone": 9.0})

    def test_unseeded_share(self):
        self.assertEqual(shards.unseeded_share([], {}), 0.0)
        self.assertEqual(shards.unseeded_share(["a", "b", "c", "d"], {"a": 1.0}), 0.75)


class TestMerge(unittest.TestCase):
    def report(self, run, skipped=()):
        return {"schema_version": 1, "tests_run": run, "failures": 0, "errors": 0,
                "unexpected_successes": 0, "expected_failures": 0,
                "skipped": [{"test_id": t, "reason": r} for t, r in skipped]}

    def test_merge_sums_counters_and_concatenates_skips(self):
        merged = shards.merge_reports([
            self.report(10, [("a.A.test_x", "external private Pinamar pack is not configured")]),
            self.report(5)])
        self.assertEqual(merged["tests_run"], 15)
        self.assertEqual(len(merged["skipped"]), 1)

    def test_merged_report_passes_the_unchanged_coverage_guard(self):
        merged = shards.merge_reports([self.report(1000), self.report(1000)])
        facts = check_unittest_coverage.inspect_result_report(merged, minimum=1950)
        self.assertEqual(facts["problems"], [])

    def test_a_shard_without_a_report_fails_the_merge(self):
        self.assertIsNone(shards.merge_reports([self.report(3), None]))

    def test_failure_sections_cut_the_traceback_blocks(self):
        log = ("test_a (m.A) ... ok\n" + "=" * 70 + "\nFAIL: test_b (m.B)\ntrace\n"
               + "-" * 70 + "\nRan 2 tests in 0.1s\n\nFAILED (failures=1)\n")
        text = shards.failure_sections(log)
        self.assertIn("FAIL: test_b", text)
        self.assertNotIn("Ran 2 tests", text)
        self.assertEqual(shards.failure_sections("test_a ... ok\nRan 1 test\nOK\n"), "")


def _run(args, env_extra=None, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True,
                          text=True, env=env, cwd=ROOT, timeout=300)


class TestEndToEnd(unittest.TestCase):
    """The real runner over a throwaway test directory."""

    def make_suite(self, body_by_module):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        suite = Path(tmp.name, "suite")
        suite.mkdir()
        for name, body in body_by_module.items():
            suite.joinpath(f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
        return tmp.name, str(suite)

    PASSING = """
        import unittest
        class T(unittest.TestCase):
            def test_a(self): pass
            def test_b(self): pass
        """

    def test_pass_merges_into_one_serial_schema_report(self):
        tmp, suite = self.make_suite({"test_one": self.PASSING, "test_two": self.PASSING,
                                      "test_three": self.PASSING})
        report = os.path.join(tmp, "r.json")
        timings = os.path.join(tmp, "t.json")
        seed_out = os.path.join(tmp, "seed.json")
        proc = _run(["--shards", "2", "--start-directory", suite, "--report", report,
                     "--timings-out", timings, "--seed-out", seed_out,
                     "--log-dir", os.path.join(tmp, "logs")],
                    drop=("TEST_DATABASE_URL", "DATABASE_URL"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(sorted(json.loads(Path(seed_out).read_text())["modules"]),
                         ["test_one", "test_three", "test_two"])
        data = json.loads(Path(report).read_text())
        self.assertEqual(data["tests_run"], 6)
        self.assertEqual(check_unittest_coverage.inspect_result_report(data, minimum=6)["problems"], [])
        self.assertIn("Ran 6 tests", proc.stdout)
        self.assertEqual(sorted(json.loads(Path(timings).read_text())["modules"]),
                         ["test_one", "test_three", "test_two"])

    def test_each_file_runs_in_a_fresh_interpreter(self):
        # Two files in ONE lane: what the first leaves in os.environ or in a
        # module global must not exist for the second.
        tmp, suite = self.make_suite({
            "test_a_leaks": """
                import os, unittest
                os.environ["SHARD_LEAK_PROBE"] = "from-a"
                class T(unittest.TestCase):
                    def test_leak(self): os.environ["SHARD_LEAK_RUNTIME"] = "from-a"
                """,
            "test_b_probes": """
                import os, unittest
                class T(unittest.TestCase):
                    def test_clean(self):
                        self.assertNotIn("SHARD_LEAK_PROBE", os.environ)
                        self.assertNotIn("SHARD_LEAK_RUNTIME", os.environ)
                """})
        proc = _run(["--shards", "1", "--start-directory", suite,
                     "--report", os.path.join(tmp, "r.json")],
                    drop=("TEST_DATABASE_URL", "DATABASE_URL", "SHARD_LEAK_PROBE"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_failing_shard_fails_the_run_and_is_named(self):
        tmp, suite = self.make_suite({"test_ok": self.PASSING, "test_bad": """
            import unittest
            class T(unittest.TestCase):
                def test_boom(self): self.fail("boom-marker")
            """})
        proc = _run(["--shards", "2", "--start-directory", suite,
                     "--report", os.path.join(tmp, "r.json")],
                    drop=("TEST_DATABASE_URL", "DATABASE_URL"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAILURES OF ALL SHARDS", proc.stdout)
        self.assertIn("boom-marker", proc.stdout)
        self.assertIn("FAILED (failures=1)", proc.stdout)

    def test_a_shard_that_dies_without_a_report_fails_the_run(self):
        tmp, suite = self.make_suite({"test_die": """
            import os, unittest
            class T(unittest.TestCase):
                def test_die(self): os._exit(3)
            """})
        proc = _run(["--shards", "1", "--start-directory", suite,
                     "--report", os.path.join(tmp, "r.json")],
                    drop=("TEST_DATABASE_URL", "DATABASE_URL"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("shard1: test_die died: exit code 3", proc.stdout)
        self.assertIn("--- test_die: no result report (exit code 3) ---", proc.stdout)

    def test_sigterm_into_a_log_file_still_names_what_was_running(self):
        # 05.11: ci_local.sh sends the runner's output to a file; a SIGTERM from the outer timeout
        # used to leave that file without a word of why (block buffer, databases dropped first).
        tmp, suite = self.make_suite({"test_a_sleeper": """
            import time, unittest
            class T(unittest.TestCase):
                def test_wait(self): time.sleep(120)
            """, "test_b_after": self.PASSING})
        log = Path(tmp, "backend.log")
        env = {k: v for k, v in os.environ.items() if k not in ("TEST_DATABASE_URL", "DATABASE_URL")}
        with log.open("wb") as stream:
            proc = subprocess.Popen(
                [sys.executable, SCRIPT, "--shards", "1", "--heartbeat", "1",
                 "--start-directory", suite, "--report", os.path.join(tmp, "r.json")],
                stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
            self.addCleanup(proc.kill)
            deadline = 60
            while "heartbeat" not in log.read_text(encoding="utf-8", errors="replace"):
                try:
                    proc.wait(timeout=0.2)
                    self.fail("the runner ended before its first heartbeat: " + log.read_text())
                except subprocess.TimeoutExpired:
                    deadline -= 0.2
                    self.assertGreater(deadline, 0, log.read_text())
            proc.terminate()
            code = proc.wait(timeout=60)
        text = log.read_text(encoding="utf-8", errors="replace")
        self.assertEqual(code, 1, text)
        self.assertIn("stopping every lane after", text)
        self.assertIn("interrupted by SIGTERM; running: shard1 test_a_sleeper", text)
        self.assertIn("--- test_a_sleeper: stopped while running ---", text)
        self.assertIn("--- test_b_after: never ran ---", text)
        self.assertIn("FAILED (run stopped: interrupted by SIGTERM)", text)

    def test_every_shard_gets_its_own_database_and_it_is_dropped_after(self):
        base = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not base:
            self.skipTest("TEST_DATABASE_URL no configurada")
        probe = tempfile.TemporaryDirectory()
        self.addCleanup(probe.cleanup)
        body = """
            import os, unittest
            from urllib.parse import urlsplit
            class T(unittest.TestCase):
                def test_url(self):
                    a, b = os.environ["DATABASE_URL"], os.environ["TEST_DATABASE_URL"]
                    self.assertEqual(a, b)
                    import psycopg
                    with psycopg.connect(a) as c, c.cursor() as cur:
                        cur.execute("SELECT current_database()")
                        name = cur.fetchone()[0]
                    with open(os.path.join(os.environ["SHARD_PROBE_DIR"], __name__), "w") as f:
                        f.write(name + "\\n" + os.environ["TMPDIR"])
            """
        tmp, suite = self.make_suite({"test_p1": body, "test_p2": body})
        proc = _run(["--shards", "2", "--start-directory", suite,
                     "--report", os.path.join(tmp, "r.json")],
                    env_extra={"SHARD_PROBE_DIR": probe.name})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        seen = [Path(probe.name, n).read_text().split("\n") for n in ("test_p1", "test_p2")]
        self.assertNotEqual(seen[0][0], seen[1][0], "two shards shared one database")
        self.assertNotEqual(seen[0][1], seen[1][1], "two shards shared one TMPDIR")
        import psycopg
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(base)
        maint = urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))
        with psycopg.connect(maint, autocommit=True) as c, c.cursor() as cur:
            for name, _tmp in seen:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
                self.assertIsNone(cur.fetchone(), f"{name} was not dropped")


    def test_the_second_file_of_a_lane_sees_an_empty_database_and_temp_directory(self):
        # Two files in ONE lane: what the first leaves in the lane's database
        # or temp directory is not there for the second.
        if not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")):
            self.skipTest("TEST_DATABASE_URL no configurada")
        tmp, suite = self.make_suite({
            "test_a_writes": """
                import os, unittest
                class T(unittest.TestCase):
                    def test_write(self):
                        import psycopg
                        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
                            c.execute("CREATE TABLE left_behind (id int)")
                        open(os.path.join(os.environ["TMPDIR"], "left-behind.txt"), "w").close()
                """,
            "test_b_looks": """
                import os, unittest
                class T(unittest.TestCase):
                    def test_empty(self):
                        import psycopg
                        with psycopg.connect(os.environ["DATABASE_URL"]) as c:
                            n = c.execute("SELECT count(*) FROM pg_tables WHERE tablename = 'left_behind'").fetchone()[0]
                        self.assertEqual(n, 0)
                        self.assertEqual(os.listdir(os.environ["TMPDIR"]), [])
                """})
        proc = _run(["--shards", "1", "--start-directory", suite,
                     "--report", os.path.join(tmp, "r.json")])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Ran 2 tests", proc.stdout)


class TestCiWiring(unittest.TestCase):
    """Both CI entry points default to the sharded runner, keep a one-process
    switch, and still gate the SAME report with the SAME minimum."""

    def test_entry_points_shard_by_default_and_keep_a_serial_switch(self):
        for name in ("scripts/ci_local.sh", ".gitlab-ci.yml"):
            text = Path(ROOT, name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertIn("run_backend_shards.py", text)
                self.assertIn("TURNOS_BACKEND_SERIAL", text)
                self.assertIn("run_backend_tests.py", text)
                self.assertIn("check_unittest_coverage.py", text)
                # the minimum test count is min_tests in scripts/coverage_floor.json
                self.assertNotIn("--minimum", text)


class TestWorkerLeakGate(unittest.TestCase):
    """`leaked_worker_pids` replaced a checkout-wide `pgrep -f worker.py`."""

    def spawn(self, **kwargs):
        # argv carries <ROOT>/worker.py, which is all the gate looks at.
        return subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)",
             os.path.join(ROOT, "app", "worker.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)

    def test_a_child_of_this_process_is_a_leak(self):
        child = self.spawn()
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertIn(child.pid, _workerproc.leaked_worker_pids())

    def test_only_descendants_and_orphans_count(self):
        marker = os.path.join(ROOT, "app", "worker.py")
        table = "\n".join([
            f"  100     9 python3 run_backend_tests.py",       # this process
            f"  200   100 python3 {marker}",                    # its worker: a leak
            f"  210   200 python3 {marker}",                    # its worker's child: a leak
            f"  300     1 python3 {marker}",                    # orphan: a leak
            f"  500     2 python3 run_backend_tests.py",        # a sibling shard
            f"  400   500 python3 {marker}",                    # ITS live worker: not ours
            f"  410   400 python3 {marker}",                    # ITS worker's child: not ours
            f"  600   100 python3 other_worker.py",             # not this checkout's worker
        ])
        self.assertEqual(_workerproc.leaked_worker_pids(ps_output=table, me=100),
                         [200, 210, 300])

    def test_a_parent_loop_in_the_table_does_not_hang(self):
        marker = os.path.join(ROOT, "app", "worker.py")
        table = f"  7  8 python3 {marker}\n  8  7 sh\n"
        self.assertEqual(_workerproc.leaked_worker_pids(ps_output=table, me=100), [])


if __name__ == "__main__":
    unittest.main()
