"""test_coverage_floor.py — the coverage floors (scripts/coverage_floor.py) and their wiring.

Pins the ratchet's arithmetic (floor = measured minus the margin, rounded down, never lowered
by `write`), what turns a surface red (a package below its floor, a package without one, a run
with fewer tests than `min_tests`), that the real floor file covers every importable package of
the repository, and that both CI entry points measure and gate the sharded backend run.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_unittest_coverage  # noqa: E402
import coverage_floor  # noqa: E402


def summary(**packages):
    """{name: (lines covered, lines total, branches covered, branches total)} -> a summary."""
    rows = {}
    for name, (lc, lt, bc, bt) in packages.items():
        rows[name] = {"lines": {"covered": lc, "total": lt},
                      "branches": {"covered": bc, "total": bt}, "files": 1}
    total = {k: {"covered": sum(r[k]["covered"] for r in rows.values()),
                 "total": sum(r[k]["total"] for r in rows.values())} for k in coverage_floor.KINDS}
    total["files"] = len(rows)
    return {"schema_version": 1, "packages": rows, "total": total}


def floor_file(min_tests=100, js=None, min_js_tests=50, **packages):
    return {"schema_version": 1, "min_tests": min_tests, "min_js_tests": min_js_tests,
            "python": {n: {"lines": l, "branches": b} for n, (l, b) in packages.items()},
            "js": {n: {"lines": l, "branches": b} for n, (l, b) in (js or {}).items()}}


def c8_entry(lines, branches):
    return {"lines": {"total": lines[1], "covered": lines[0]},
            "branches": {"total": branches[1], "covered": branches[0]}}


class TestArithmetic(unittest.TestCase):
    def test_floor_is_the_measured_value_minus_the_margin_rounded_down(self):
        self.assertEqual(coverage_floor.floor_for(85.04), 84.5)
        self.assertEqual(coverage_floor.floor_for(85.0), 84.5)
        self.assertEqual(coverage_floor.floor_for(84.99), 84.4)
        self.assertEqual(coverage_floor.floor_for(100.0), 99.5)
        self.assertEqual(coverage_floor.floor_for(0.2), 0.0)

    def test_nothing_to_cover_is_one_hundred_percent(self):
        self.assertEqual(coverage_floor.percent(0, 0), 100.0)
        self.assertEqual(coverage_floor.percent(1, 4), 25.0)

    def test_package_is_the_first_path_component(self):
        repo = Path("/r")
        self.assertEqual(coverage_floor.package_of("pkg/db.py", repo), "pkg")
        self.assertEqual(coverage_floor.package_of("/r/mods/chat/x.py", repo), "mods")
        self.assertEqual(coverage_floor.package_of("entry.py", repo), "(root)")
        self.assertIsNone(coverage_floor.package_of("/usr/lib/python3/x.py", repo))
        self.assertIsNone(coverage_floor.package_of("../x.py", repo))
        # the application package: one floor per subpackage, its own modules as `app`
        nested = coverage_floor.PY_NESTED
        self.assertEqual(coverage_floor.package_of("app/pkg/db.py", repo, nested=nested), "app/pkg")
        self.assertEqual(coverage_floor.package_of("/r/app/mods/chat/x.py", repo, nested=nested), "app/mods")
        self.assertEqual(coverage_floor.package_of("app/entry.py", repo, nested=nested), "app")

    def test_files_are_summed_per_package(self):
        files = {
            "pkg/a.py": {"summary": {"covered_lines": 8, "num_statements": 10,
                                     "covered_branches": 3, "num_branches": 4}},
            "pkg/b.py": {"summary": {"covered_lines": 2, "num_statements": 10,
                                     "covered_branches": 0, "num_branches": 2}},
            "entry.py": {"summary": {"covered_lines": 1, "num_statements": 1}},
            "app/sub/c.py": {"summary": {"covered_lines": 1, "num_statements": 2}},
            "app/main.py": {"summary": {"covered_lines": 1, "num_statements": 1}},
        }
        got = coverage_floor.summarize_files(files, Path("/r"))
        self.assertEqual(got["packages"]["pkg"]["lines"], {"covered": 10, "total": 20})
        self.assertEqual(got["packages"]["pkg"]["branches"], {"covered": 3, "total": 6})
        self.assertEqual(got["packages"]["pkg"]["files"], 2)
        self.assertEqual(got["packages"]["(root)"]["branches"], {"covered": 0, "total": 0})
        self.assertEqual(got["packages"]["app/sub"]["lines"], {"covered": 1, "total": 2})
        self.assertEqual(got["packages"]["app"]["files"], 1)
        self.assertEqual(got["total"]["lines"], {"covered": 13, "total": 24})


class TestFrontendSummary(unittest.TestCase):
    def test_c8_files_are_summed_per_directory_with_static_split_one_level_down(self):
        repo = Path("/r")
        files = {
            "total": c8_entry((9, 10), (1, 2)),
            "/r/static/platform/a.mjs": c8_entry((8, 10), (3, 4)),
            "/r/static/platform/b.mjs": c8_entry((2, 10), (0, 2)),
            "/r/static/islands/c.mjs": c8_entry((1, 1), (0, 0)),
            "/r/modules/turnos/offer.mjs": c8_entry((5, 5), (1, 1)),
            "/r/admin-app.js": c8_entry((0, 100), (0, 0)),
            "/elsewhere/x.js": c8_entry((1, 1), (0, 0)),
        }
        got = coverage_floor.summarize_c8(files, repo)
        self.assertEqual(sorted(got["packages"]),
                         ["(root)", "modules", "static/islands", "static/platform"])
        self.assertEqual(got["packages"]["static/platform"]["lines"], {"covered": 10, "total": 20})
        self.assertEqual(got["packages"]["static/platform"]["branches"], {"covered": 3, "total": 6})
        self.assertEqual(got["packages"]["(root)"]["lines"], {"covered": 0, "total": 100})
        self.assertEqual(got["total"]["files"], 5)  # "total" and the file outside the repo are skipped

    def test_js_and_python_floors_are_independent_sections_of_one_file(self):
        old = floor_file(min_tests=100, core=(80.0, 60.0), js={"modules": (10.0, 10.0)})
        new = coverage_floor.updated_floor(old, summary(modules=(50, 100, 50, 100)), section="js")
        self.assertEqual(new["js"]["modules"], {"lines": 49.5, "branches": 49.5})
        self.assertEqual(new["python"], old["python"])
        self.assertEqual(new["min_tests"], 100)
        problems, _ = coverage_floor.inspect(summary(modules=(5, 100, 5, 100)), old, "js")
        self.assertEqual(len(problems), 2)


class TestFrontendTestCount(unittest.TestCase):
    TAP = "# tests 2613\n# suites 0\n# pass 2595\n# fail 18\n"

    def test_both_node_reporters_are_counted_and_lanes_add_up(self):
        self.assertEqual(coverage_floor.js_tests_in_log(self.TAP), 2613)
        spec = "ℹ tests 2492\nℹ suites 3\nℹ pass 2492\n"
        self.assertEqual(coverage_floor.js_tests_in_log(spec), 2492)
        both = self.TAP + "noise\n# tests 303\n# pass 302\n"
        self.assertEqual(coverage_floor.js_tests_in_log(both), 2916)
        self.assertEqual(coverage_floor.js_tests_in_log("a test named '# tests 5' is not a summary\n"), 0)
        self.assertEqual(coverage_floor.js_tests_in_log(""), 0)

    def test_write_raises_min_js_tests_to_97_percent_and_never_lowers_it(self):
        old = floor_file(min_js_tests=100)
        up = coverage_floor.updated_floor(old, summary(), js_tests_run=1000, section="js")
        self.assertEqual(up["min_js_tests"], 970)
        self.assertEqual(coverage_floor.updated_floor(old, summary(), js_tests_run=50,
                                                      section="js")["min_js_tests"], 100)
        self.assertEqual(coverage_floor.updated_floor(old, summary(), js_tests_run=50, lower=True,
                                                      section="js")["min_js_tests"], 48)
        self.assertEqual(up["min_tests"], old["min_tests"])  # the backend count is not touched

    def test_count_command_is_red_below_the_floor_and_when_the_log_has_no_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            floor = Path(tmp, "floor.json")
            floor.write_text(json.dumps(floor_file(min_js_tests=970)), encoding="utf-8")
            log = Path(tmp, "frontend.log")
            buffer = io.StringIO()
            for text, code in (("# tests 1000\n", 0), ("# tests 970\n", 0), ("# tests 969\n", 1),
                               ("# tests 600\n# tests 400\n", 0), ("no summary\n", 1)):
                log.write_text(text, encoding="utf-8")
                with self.subTest(text), contextlib.redirect_stdout(buffer):
                    self.assertEqual(coverage_floor.main(
                        ["count", "--log", str(log), "--floor", str(floor)]), code)
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(coverage_floor.main(
                    ["count", "--log", str(Path(tmp, "absent.log")), "--floor", str(floor)]), 1)

    def test_write_with_a_log_sets_min_js_tests_from_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            c8 = Path(tmp, "c8.json")
            c8.write_text(json.dumps({"total": c8_entry((1, 1), (0, 0)),
                                      str(Path(ROOT, "app", "modules", "x.mjs")): c8_entry((1, 1), (0, 0))}),
                          encoding="utf-8")
            log = Path(tmp, "frontend.log")
            log.write_text("# tests 2000\n", encoding="utf-8")
            floor = Path(tmp, "floor.json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(coverage_floor.main(
                    ["write", "--kind", "js", "--summary", str(c8), "--log", str(log),
                     "--floor", str(floor)]), 0)
            self.assertEqual(json.loads(floor.read_text())["min_js_tests"], 1940)


class TestInspect(unittest.TestCase):
    def test_at_the_floor_is_green(self):
        problems, _ = coverage_floor.inspect(summary(core=(85, 100, 70, 100)),
                                             floor_file(core=(85.0, 70.0)))
        self.assertEqual(problems, [])

    def test_a_drop_below_the_floor_is_named_with_its_numbers(self):
        problems, _ = coverage_floor.inspect(summary(core=(849, 1000, 70, 100)),
                                             floor_file(core=(85.0, 70.0)))
        self.assertEqual(len(problems), 1)
        self.assertIn("core", problems[0])
        self.assertIn("lines", problems[0])
        self.assertIn("84.90", problems[0])

    def test_branches_have_their_own_floor(self):
        problems, _ = coverage_floor.inspect(summary(core=(100, 100, 60, 100)),
                                             floor_file(core=(85.0, 70.0)))
        self.assertEqual(len(problems), 1)
        self.assertIn("branches", problems[0])

    def test_a_package_without_a_floor_and_a_floor_without_a_package_are_red(self):
        problems, _ = coverage_floor.inspect(summary(core=(1, 1, 0, 0), new=(1, 1, 0, 0)),
                                             floor_file(core=(0.0, 0.0), gone=(0.0, 0.0)))
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("new" in p and "no floor" in p for p in problems))
        self.assertTrue(any("gone" in p and "not in the run" in p for p in problems))


class TestRatchet(unittest.TestCase):
    def test_write_raises_floors_and_never_lowers_them(self):
        old = floor_file(min_tests=100, core=(80.0, 60.0), ops=(90.0, 90.0))
        new = coverage_floor.updated_floor(old, summary(core=(90, 100, 70, 100),
                                                        ops=(50, 100, 50, 100)))
        self.assertEqual(new["python"]["core"], {"lines": 89.5, "branches": 69.5})
        self.assertEqual(new["python"]["ops"], {"lines": 90.0, "branches": 90.0})

    def test_lower_follows_the_measured_value_down(self):
        old = floor_file(core=(80.0, 60.0), gone=(10.0, 10.0))
        new = coverage_floor.updated_floor(old, summary(core=(50, 100, 50, 100)), lower=True)
        self.assertEqual(new["python"], {"core": {"lines": 49.5, "branches": 49.5}})

    def test_a_package_that_left_the_run_stays_until_lower(self):
        old = floor_file(core=(80.0, 60.0), gone=(10.0, 10.0))
        new = coverage_floor.updated_floor(old, summary(core=(80, 100, 60, 100)))
        self.assertIn("gone", new["python"])

    def test_min_tests_is_three_percent_under_the_run_and_only_goes_up(self):
        old = floor_file(min_tests=100)
        self.assertEqual(coverage_floor.updated_floor(old, summary(), tests_run=1000)["min_tests"], 970)
        self.assertEqual(coverage_floor.updated_floor(old, summary(), tests_run=50)["min_tests"], 100)
        self.assertEqual(coverage_floor.updated_floor(old, summary(), tests_run=50,
                                                      lower=True)["min_tests"], 48)


class TestCommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def put(self, name, payload):
        path = self.dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def main(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = coverage_floor.main(list(argv))
        return code, buffer.getvalue()

    def test_write_then_check_is_green_and_a_drop_afterwards_is_red(self):
        run = self.put("s.json", summary(core=(90, 100, 70, 100)))
        report = self.put("r.json", {"tests_run": 1000})
        floor = str(self.dir / "floor.json")
        code, _ = self.main("write", "--summary", run, "--report", report, "--floor", floor)
        self.assertEqual(code, 0)
        code, text = self.main("check", "--summary", run, "--report", report, "--floor", floor)
        self.assertEqual(code, 0, text)
        worse = self.put("w.json", summary(core=(88, 100, 70, 100)))
        code, text = self.main("check", "--summary", worse, "--report", report, "--floor", floor)
        self.assertEqual(code, 1)
        self.assertIn("below the floor", text)

    def test_the_js_kind_reads_c8_output_and_gates_its_own_section(self):
        c8 = self.put("c8.json", {"total": c8_entry((90, 100), (70, 100)),
                                  str(Path(ROOT, "app", "modules", "x.mjs")): c8_entry((90, 100), (70, 100))})
        floor = str(self.dir / "floor.json")
        self.assertEqual(self.main("write", "--kind", "js", "--summary", c8, "--floor", floor)[0], 0)
        code, text = self.main("check", "--kind", "js", "--summary", c8, "--floor", floor)
        self.assertEqual(code, 0, text)
        worse = self.put("w.json", {"total": c8_entry((0, 0), (0, 0)),
                                    str(Path(ROOT, "app", "modules", "x.mjs")): c8_entry((80, 100), (70, 100))})
        code, text = self.main("check", "--kind", "js", "--summary", worse, "--floor", floor)
        self.assertEqual(code, 1)
        self.assertIn("modules: lines coverage 80.00 %", text)
        # a python-schema summary is not accepted as c8 output
        code, _ = self.main("check", "--kind", "js", "--summary",
                            self.put("s.json", summary(core=(1, 1, 0, 0))), "--floor", floor)
        self.assertEqual(code, 1)

    def test_too_few_tests_is_red_even_with_the_coverage_intact(self):
        run = self.put("s.json", summary(core=(90, 100, 70, 100)))
        floor = self.put("f.json", floor_file(min_tests=970, core=(89.5, 69.5)))
        code, text = self.main("check", "--summary", run,
                               "--report", self.put("r.json", {"tests_run": 300}), "--floor", floor)
        self.assertEqual(code, 1)
        self.assertIn("only 300 tests ran", text)

    def test_missing_or_broken_evidence_is_red_not_green(self):
        floor = self.put("f.json", floor_file(core=(0.0, 0.0)))
        for summary_path in (str(self.dir / "absent.json"), self.put("bad.json", {"x": 1})):
            code, text = self.main("check", "--summary", summary_path, "--floor", floor)
            self.assertEqual(code, 1)
            self.assertIn("evidence unavailable or invalid", text)
        run = self.put("s.json", summary(core=(1, 1, 0, 0)))
        code, _ = self.main("check", "--summary", run, "--floor", str(self.dir / "no-floor.json"))
        self.assertEqual(code, 1)


class TestRealFloorFile(unittest.TestCase):
    def setUp(self):
        self.floor = coverage_floor.load_floor()

    def test_min_tests_is_not_below_the_gate_it_replaced(self):
        self.assertGreaterEqual(self.floor["min_tests"], 1950)

    def test_every_importable_package_has_a_floor(self):
        # A directory with an __init__.py is measured even when no test imports it, so a
        # package missing from the floor file would make the surface red on its first run.
        packages = {p.parent.name for p in Path(ROOT).glob("*/__init__.py")}
        for nested in coverage_floor.PY_NESTED:
            # the application package is measured per subpackage (scripts/coverage_floor.py)
            if nested in packages:
                packages |= {"%s/%s" % (nested, p.parent.name)
                             for p in Path(ROOT, nested).glob("*/__init__.py")}
        self.assertTrue(packages)
        self.assertEqual(sorted(packages - set(self.floor["python"])), [])

    def test_min_js_tests_is_recorded(self):
        self.assertGreater(self.floor["min_js_tests"], 1000)

    def test_floors_are_percentages(self):
        for section in coverage_floor.SECTIONS:
            for package, floors in self.floor[section].items():
                for kind in coverage_floor.KINDS:
                    self.assertTrue(0.0 <= floors[kind] <= 100.0, (section, package, kind))

    def test_every_first_party_frontend_directory_has_a_floor(self):
        # c8 measures every .js/.mjs under these directories (scripts/c8rc.json) even when no
        # test loads it; a directory with sources and no floor makes the surface red.
        tracked = subprocess.run(["git", "ls-files", "*.js", "*.mjs"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.split()
        if not tracked:
            self.skipTest("not a git checkout")
        excluded = {"tests", "vendor", "stories", ".storybook", ".claude", "docs"}
        wanted = set()
        for name in tracked:
            parts = Path(name).parts
            if parts[0] in excluded or name == "playwright.config.mjs" \
                    or name.startswith("static/vendor/"):
                continue
            wanted.add(coverage_floor.package_of(name, Path(ROOT), nested=coverage_floor.JS_NESTED))
        self.assertEqual(sorted(wanted - set(self.floor["js"])), [])


class TestCiWiring(unittest.TestCase):
    def read(self, name):
        return Path(ROOT, name).read_text(encoding="utf-8")

    def test_both_entry_points_measure_and_gate_the_sharded_run(self):
        for name in ("scripts/ci_local.sh", ".gitlab-ci.yml"):
            text = self.read(name)
            with self.subTest(name):
                self.assertIn("--coverage-dir", text)
                self.assertIn("coverage_floor.py check", text)
                self.assertIn("backend-coverage", text)
                self.assertIn("coverage-summary.json", text)
                self.assertIn("--report", text)

    def test_both_entry_points_measure_the_frontend_with_c8(self):
        for name in ("scripts/ci_local.sh", ".gitlab-ci.yml"):
            text = self.read(name)
            with self.subTest(name):
                self.assertIn("c8 --config scripts/c8rc.json", text)
                self.assertIn("coverage_floor.py check --kind js", text)
                self.assertIn("coverage_floor.py count --log", text)
                self.assertIn("frontend-coverage", text)
        self.assertIn('"c8"', self.read("package.json"))

    def test_the_test_count_floor_lives_in_the_floor_file_only(self):
        for name in ("scripts/ci_local.sh", ".gitlab-ci.yml", ".github/workflows/ci.yml"):
            with self.subTest(name):
                self.assertNotIn("--minimum", self.read(name))

    def test_coverage_is_installed_by_the_ci_requirements_not_the_image(self):
        self.assertIn("scripts/requirements-ci.txt", self.read(".gitlab-ci.yml"))
        self.assertIn("coverage==", self.read("scripts/requirements-ci.txt"))
        self.assertNotIn("\ncoverage==", "\n" + self.read("requirements.txt"))

    def test_the_summary_is_kept_as_an_artifact(self):
        text = self.read(".gitlab-ci.yml")
        self.assertIn("backend-coverage/coverage-summary.txt", text)
        self.assertIn("backend-coverage/coverage-summary.json", text)


@unittest.skipUnless(
    importlib.util.find_spec("coverage"),
    "coverage.py is not installed (pip install -r scripts/requirements-ci.txt)")
class TestShardedRunMeasures(unittest.TestCase):
    def test_a_sharded_run_leaves_the_combined_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp, "suite")
            suite.mkdir()
            (suite / "test_one.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n",
                encoding="utf-8")
            (suite / "test_two.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_y(self): pass\n",
                encoding="utf-8")
            cov = Path(tmp, "cov")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("TEST_DATABASE_URL", "DATABASE_URL")}
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "run_backend_shards.py"),
                 "--shards", "2", "--start-directory", str(suite),
                 "--report", os.path.join(tmp, "r.json"), "--coverage-dir", str(cov)],
                capture_output=True, text=True, env=env, cwd=ROOT)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads((cov / coverage_floor.SUMMARY_JSON).read_text(encoding="utf-8"))
            self.assertIn("scripts", data["packages"])  # the worker script was measured
            self.assertGreater(data["packages"]["scripts"]["lines"]["covered"], 0)
            self.assertTrue((cov / coverage_floor.SUMMARY_TEXT).is_file())
            self.assertTrue((cov / coverage_floor.FILES_JSON).is_file())
            self.assertIn("coverage (combined", proc.stdout)


if __name__ == "__main__":
    unittest.main()
