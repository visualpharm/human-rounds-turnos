"""test_update_backend_timings.py — scripts/update_backend_timings.py and the lane-balance note of
scripts/run_backend_shards.py: the seed the shard planner balances by is refreshed from CI artifacts
with one command, and a run whose lanes finished unevenly says the seed is stale."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import run_backend_shards as shards  # noqa: E402
import update_backend_timings as upd  # noqa: E402

A, B, C = (f"test_{x}" for x in ("aaa", "bbb", "ccc"))


class TestMerge(unittest.TestCase):
    def test_the_median_of_the_runs_wins_over_one_busy_runner(self):
        runs = [{A: 10.0}, {A: 12.0}, {A: 100.0}]
        self.assertEqual(upd.merge_timings(runs, {A: 1.0}, [A]), {A: 12.0})

    def test_a_file_no_run_measured_keeps_its_old_seconds(self):
        self.assertEqual(upd.merge_timings([{A: 5.0}], {B: 7.5}, [A, B]), {A: 5.0, B: 7.5})

    def test_a_file_that_no_longer_exists_is_dropped(self):
        self.assertEqual(upd.merge_timings([{A: 5.0, B: 9.0}], {B: 7.5}, [A]), {A: 5.0})

    def test_a_new_file_nobody_measured_stays_out_so_the_planner_weighs_it_by_size(self):
        self.assertNotIn(C, upd.merge_timings([{A: 5.0}], {A: 1.0}, [A, C]))


class TestSlowestLane(unittest.TestCase):
    def test_a_fresh_seed_plans_better_than_a_stale_one_on_what_the_files_really_took(self):
        mods = [f"test_m{i}" for i in range(8)]
        real = {m: 10.0 for m in mods}
        real.update({"test_m1": 100.0, "test_m2": 100.0})
        stale = {m: 1.0 for m in mods}
        stale["test_m0"] = 10.0   # the stale seed thinks one file outweighs all the others together
        stale_slowest, even = upd.slowest_lane(stale, real, mods, 2)
        fresh_slowest, _ = upd.slowest_lane(real, real, mods, 2)
        self.assertGreater(stale_slowest, fresh_slowest)
        self.assertEqual(fresh_slowest, even)


class TestSince(unittest.TestCase):
    def git(self, *args):
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.repo,
                              capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def test_only_the_commit_and_what_came_after_it_count(self):
        self.repo = tempfile.mkdtemp(prefix="since-")
        self.addCleanup(lambda: shutil.rmtree(self.repo, ignore_errors=True))
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "one")
        first = self.git("rev-parse", "HEAD")
        self.git("commit", "-q", "--allow-empty", "-m", "two")
        second = self.git("rev-parse", "HEAD")
        with mock.patch.object(shards, "REPO", Path(self.repo)):
            self.assertTrue(upd.is_descendant(first, second))
            self.assertTrue(upd.is_descendant(second, second))
            self.assertFalse(upd.is_descendant(second, first))
            self.assertIsNone(upd.is_descendant(first, "0" * 40))


class TestAssume(unittest.TestCase):
    def test_a_file_name_with_or_without_py_and_its_seconds(self):
        mods = [A, B]
        self.assertEqual(upd.parse_assumptions([f"{A}=12.5", f"{B}.py=3"], mods), {A: 12.5, B: 3.0})

    def test_a_name_that_is_not_a_test_file_or_a_bad_number_is_refused(self):
        for bad in ("test_nope=1", f"{A}=fast", f"{A}", f"{A}=-1"):
            with self.assertRaises(SystemExit, msg=bad):
                upd.parse_assumptions([bad], [A])


class TestRemote(unittest.TestCase):
    def test_ssh_and_https_remotes_give_host_and_project(self):
        self.assertEqual(upd.parse_remote("git@gitlab.example.test:grp/repo.git"),
                         ("gitlab.example.test", "grp/repo"))
        self.assertEqual(upd.parse_remote("https://oauth2:tok@gitlab.example.test/grp/repo.git"),
                         ("gitlab.example.test", "grp/repo"))


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="timings-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.mods = shards.discover_modules()[:2]
        self.seed = self.tmp / "seed.json"
        self.seed.write_text(json.dumps({"schema_version": 1, "modules": {self.mods[0]: 1.0}}))
        patcher = mock.patch.object(shards, "SEED_TIMINGS", self.seed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def artifact(self, name, seconds):
        path = self.tmp / name
        path.write_text(json.dumps({"schema_version": 1, "modules": dict(zip(self.mods, seconds))}))
        return str(path)

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return upd.main(list(argv))

    def test_artifact_files_are_folded_into_the_seed(self):
        files = [self.artifact("a.json", (10, 20)), self.artifact("b.json", (30, 40)),
                 self.artifact("c.json", (50, 60))]
        self.assertEqual(self.run_main(*files), 0)
        written = json.loads(self.seed.read_text())
        self.assertEqual(written["schema_version"], 1)
        self.assertEqual(written["modules"][self.mods[0]], 30.0)
        self.assertEqual(written["modules"][self.mods[1]], 40.0)

    def test_assume_sets_a_value_no_run_could_have_measured(self):
        files = [self.artifact("a.json", (10, 20))]
        self.assertEqual(self.run_main(f"--assume={self.mods[1]}=1.5", *files), 0)
        written = json.loads(self.seed.read_text())["modules"]
        self.assertEqual(written[self.mods[0]], 10.0)
        self.assertEqual(written[self.mods[1]], 1.5)

    def test_dry_run_prints_and_writes_nothing(self):
        before = self.seed.read_text()
        self.assertEqual(self.run_main("--dry-run", self.artifact("a.json", (10, 20))), 0)
        self.assertEqual(self.seed.read_text(), before)

    def test_unreadable_artifacts_fail_and_leave_the_seed_alone(self):
        before = self.seed.read_text()
        bad = self.tmp / "bad.json"
        bad.write_text("not json")
        self.assertEqual(self.run_main(str(bad)), 1)
        self.assertEqual(self.seed.read_text(), before)


class TestBalanceNote(unittest.TestCase):
    def test_even_lanes_say_nothing_to_do(self):
        note = shards.balance_note([100, 105, 98, 102], 40)
        self.assertIn("lane balance", note)
        self.assertNotIn("update_backend_timings", note)

    def test_uneven_lanes_point_at_the_refresh_command(self):
        self.assertIn("scripts/update_backend_timings.py", shards.balance_note([100, 100, 100, 400], 40))

    def test_a_lane_slow_only_because_of_the_longest_file_is_not_the_seeds_fault(self):
        self.assertNotIn("update_backend_timings", shards.balance_note([240, 100, 100, 100], 238))

    def test_a_single_lane_or_a_run_that_measured_nothing_has_no_note(self):
        self.assertEqual(shards.balance_note([50], 50), "")
        self.assertEqual(shards.balance_note([0, 0], 0), "")


if __name__ == "__main__":
    unittest.main()
