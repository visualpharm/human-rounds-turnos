"""Adversarial contract for the managed unittest skip allowlist."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.check_unittest_coverage import (  # noqa: E402
    ARCHIVE_COPY_SKIP, GITLEAKS_ABSENT_SKIP, floor_minimum, inspect_log, inspect_result_report)
from scripts.check_unittest_coverage import main as guard_main  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _archive  # noqa: E402


class UnittestCoverageGuardTest(unittest.TestCase):
    def _report(self, reason="external private Pinamar pack is not configured"):
        return {"schema_version": 1, "tests_run": 2000, "failures": 0, "errors": 0,
                "unexpected_successes": 0, "expected_failures": 0,
                "skipped": [{"test_id": "sample.Case.test_pack", "reason": reason}]}

    def test_structured_skip_survives_noisy_split_console_result(self):
        noisy = ("test_pack (sample.Case.test_pack) ... background log\n"
                 "skipped 'external private Pinamar pack is not configured'\n"
                 "Ran 2000 tests in 1s\nOK (skipped=1)\n")
        self.assertTrue(inspect_log(noisy, minimum=1950)["problems"])
        self.assertEqual(inspect_result_report(self._report(), minimum=1950)["problems"], [])

    def test_structured_unknown_reason_still_rejected_with_test_identity(self):
        facts = inspect_result_report(self._report("database missing"), minimum=1950)
        self.assertTrue(facts["problems"])
        self.assertIn("sample.Case.test_pack", facts["unexpected_lines"][0])

    def test_structured_failures_and_malformed_evidence_fail_closed(self):
        report = self._report()
        report["failures"] = 1
        self.assertTrue(inspect_result_report(report, minimum=1950)["problems"])
        for value in (None, {}, {**report, "tests_run": True},
                      {**report, "skipped": [{"reason": "database missing"}]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                inspect_result_report(value, minimum=1950)

    def test_exact_allowlisted_reason_passes(self):
        log = (
            "test_reference (sample.Case.test_reference) ... skipped "
            "'external private Pinamar pack is not configured'\n"
            "Ran 2000 tests in 1.0s\n\nOK (skipped=1)\n"
        )
        self.assertEqual(inspect_log(log, minimum=1950)["problems"], [])

    def test_reason_containing_allowlisted_substring_fails(self):
        log = (
            "test_bad (sample.Case.test_bad) ... skipped "
            "'external private Pinamar pack is not configured; database missing'\n"
            "Ran 2000 tests in 1.0s\n\nOK (skipped=1)\n"
        )
        facts = inspect_log(log, minimum=1950)
        self.assertEqual(facts["allowed"], 0)
        self.assertEqual(len(facts["problems"]), 1)
        self.assertEqual(facts["unexpected_lines"], [
            "test_bad (sample.Case.test_bad) ... skipped "
            "'external private Pinamar pack is not configured; database missing'"
        ])

    def test_final_summary_wins_over_preliminary_text(self):
        log = (
            "Ran 2 tests in 0.1s\nOK (skipped=2)\n"
            "Ran 2001 tests in 1.0s\nOK\n"
        )
        facts = inspect_log(log, minimum=1950)
        self.assertEqual((facts["ran"], facts["skipped"], facts["allowed"]),
                         (2001, 0, 0))
        self.assertEqual(facts["problems"], [])

    def test_preliminary_allowlisted_skip_cannot_mask_final_unexpected_skip(self):
        log = (
            "old (sample.Case.old) ... skipped "
            "'external private Pinamar pack is not configured'\n"
            "Ran 1 test in 0.1s\nOK (skipped=1)\n"
            "new (sample.Case.new) ... skipped 'database missing'\n"
            "Ran 2000 tests in 1.0s\nOK (skipped=1)\n"
        )
        facts = inspect_log(log, minimum=1950)
        self.assertEqual((facts["ran"], facts["skipped"], facts["allowed"]),
                         (2000, 1, 0))
        self.assertEqual(len(facts["problems"]), 1)


class CommandTest(unittest.TestCase):
    """`main()` in this process: what the guard prints and returns for each kind of evidence. (The
    floor tests below run it as a subprocess, which the coverage measurement does not follow.)"""

    ALLOWED = "external private Pinamar pack is not configured"

    def _report(self, tests_run=2000, skipped=()):
        return {"schema_version": 1, "tests_run": tests_run, "failures": 0, "errors": 0,
                "unexpected_successes": 0, "expected_failures": 0,
                "skipped": [{"test_id": test_id, "reason": reason} for test_id, reason in skipped]}

    def _main(self, tmp, *args, report=None, log=None):
        argv = []
        if report is not None:
            path = Path(tmp, "result.json")
            path.write_text(json.dumps(report), encoding="utf-8")
            argv += ["--result-json", str(path)]
        path = Path(tmp, "run.log")
        path.write_text(log or "", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = guard_main([str(path), *argv, *args])
        return code, out.getvalue()

    def test_a_clean_result_report_passes_and_says_what_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main(
                tmp, "--minimum", "1950",
                report=self._report(skipped=[("a.B.test_pack", self.ALLOWED)]))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "coverage guard OK: 2000 tests ran; 1 exact external-pack skips")

    def test_a_run_below_the_minimum_fails_and_says_by_how_much(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main(tmp, "--minimum", "1950", report=self._report(tests_run=100))
        self.assertEqual(code, 1)
        self.assertIn("coverage guard: only 100 tests ran (expected at least 1950)", out)

    def test_an_unexpected_skip_is_reported_with_the_test_that_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main(
                tmp, "--minimum", "1950",
                report=self._report(skipped=[("a.B.test_db", "database missing")]))
        self.assertEqual(code, 1)
        self.assertIn("1 unexpected test skip(s)", out)
        self.assertIn("coverage evidence: a.B.test_db: 'database missing'", out)

    def test_without_a_result_report_the_console_log_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main(tmp, "--minimum", "1950", log="Ran 100 tests in 1.0s\n\nOK\n")
        self.assertEqual(code, 1)
        self.assertIn("only 100 tests ran (expected at least 1950); discovery may be broken", out)

    def test_an_allowlisted_skip_line_the_summary_does_not_count_fails(self):
        log = ("test_pack (a.B.test_pack) ... skipped %r\n"
               "Ran 2000 tests in 1.0s\n\nOK\n" % self.ALLOWED)
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main(tmp, "--minimum", "1950", log=log)
        self.assertEqual(code, 1)
        self.assertIn("found 1 allowlisted result lines but summary reports 0 skips", out)

    def test_gitleaks_and_archive_copy_skips_pass_and_are_named(self):
        # A runner without gitleaks, or a `git archive` copy without the private files,
        # is an honest skip: green, with each reason counted on the verdict line.
        skipped = [("a.B.test_pack", self.ALLOWED),
                   ("a.B.test_leaks", GITLEAKS_ABSENT_SKIP),
                   ("a.C.test_hook", ARCHIVE_COPY_SKIP), ("a.D.test_size", ARCHIVE_COPY_SKIP)]
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main(tmp, "--minimum", "1950", report=self._report(skipped=skipped))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), (
            "coverage guard OK: 2000 tests ran; 1 exact external-pack skips"
            f"; 1 skipped: {GITLEAKS_ABSENT_SKIP}; 2 skipped: {ARCHIVE_COPY_SKIP}"))
        log = ("test_leaks (a.B.test_leaks) ... skipped %r\n"
               "Ran 2000 tests in 1.0s\n\nOK (skipped=1)\n" % GITLEAKS_ABSENT_SKIP)
        self.assertEqual(inspect_log(log, minimum=1950)["problems"], [])

    def test_the_archive_helper_and_the_guard_share_one_reason(self):
        self.assertEqual(_archive.REASON, ARCHIVE_COPY_SKIP)

    def test_the_archive_marker_is_tracked_and_export_ignored(self):
        # In the working repository the marker exists, so nothing here skips for an
        # archived copy; `git archive` leaves it out, so the copy is recognised.
        _archive.skip_in_archive_copy(self)
        attr = subprocess.run(["git", "check-attr", "export-ignore", "--", _archive.MARKER],
                              cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertTrue(attr.strip().endswith(": set"), attr)
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", _archive.MARKER],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(tracked.returncode, 0, tracked.stderr)

    def test_evidence_that_cannot_be_read_is_a_failure_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = guard_main([str(Path(tmp, "missing.log")), "--minimum", "1"])
        self.assertEqual(code, 1)
        self.assertIn("result evidence unavailable or invalid (FileNotFoundError)", out.getvalue())


class MinimumTestsFloorTest(unittest.TestCase):
    """The minimum test count is `min_tests` of scripts/coverage_floor.json, not a literal in
    every CI file: a suite that silently loses even a few percent of its tests fails."""

    GUARD = os.path.join(ROOT, "scripts", "check_unittest_coverage.py")

    def _run(self, floor, tests_run, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp, "scripts")
            scripts.mkdir()
            guard = scripts / "check_unittest_coverage.py"
            guard.write_bytes(Path(self.GUARD).read_bytes())
            if floor is not None:
                (scripts / "coverage_floor.json").write_text(floor, encoding="utf-8")
            report = Path(tmp, "r.json")
            report.write_text(json.dumps({
                "schema_version": 1, "tests_run": tests_run, "failures": 0, "errors": 0,
                "unexpected_successes": 0, "expected_failures": 0, "skipped": []}), encoding="utf-8")
            return subprocess.run([sys.executable, "-I", str(guard), "unused.log",
                                   "--result-json", str(report), *extra],
                                  capture_output=True, text=True, cwd=tmp)

    def test_minimum_is_read_from_the_floor_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "floor.json")
            path.write_text(json.dumps({"min_tests": 777}), encoding="utf-8")
            self.assertEqual(floor_minimum(path), 777)
            for bad in ("{}", '{"min_tests": 0}', '{"min_tests": "8000"}', '{"min_tests": true}'):
                path.write_text(bad, encoding="utf-8")
                with self.subTest(bad), self.assertRaises(ValueError):
                    floor_minimum(path)

    def test_a_run_that_lost_a_few_percent_of_its_tests_fails(self):
        floor = json.dumps({"min_tests": 970})
        self.assertEqual(self._run(floor, 1000).returncode, 0)
        self.assertEqual(self._run(floor, 970).returncode, 0)
        proc = self._run(floor, 969)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("only 969 tests ran (expected at least 970)", proc.stdout)

    def test_the_flag_still_overrides_the_floor_file(self):
        floor = json.dumps({"min_tests": 970})
        self.assertEqual(self._run(floor, 500, "--minimum", "400").returncode, 0)
        self.assertEqual(self._run(floor, 5000, "--minimum", "6000").returncode, 1)

    def test_a_missing_or_broken_floor_file_fails_closed(self):
        for floor in (None, "not json", "{}"):
            with self.subTest(floor):
                proc = self._run(floor, 100000)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("evidence unavailable or invalid", proc.stdout)

    def test_the_real_floor_is_not_below_the_gate_it_replaced(self):
        self.assertGreaterEqual(floor_minimum(), 1950)


if __name__ == "__main__":
    unittest.main()
