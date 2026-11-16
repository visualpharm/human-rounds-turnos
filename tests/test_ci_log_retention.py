"""Exercise the real CI shell orchestration with offline fake surface runners."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestCiLogRetention(unittest.TestCase):
    def _run(self, unexpected_skip, tools=True, env_extra=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts = root / "scripts"
            scripts.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            shutil.copyfile(ROOT / "scripts/ci_local.sh", scripts / "ci_local.sh")
            runners = {
                "python3": '''#!/bin/bash
if [[ "$1" == "-W" ]]; then shift 2; fi
case "$1" in
  -c) exit "${PY_COVERAGE_EXIT:-0}" ;;  # python3 -c "import coverage"
  scripts/check_unittest_coverage.py)
    shift; exec "$REAL_PYTHON" "$REAL_GUARD" "$@" ;;
  scripts/audit_cassettes.py) echo "cassette audit passed"; exit 0 ;;
  scripts/coverage_floor.py) echo "coverage floor OK (fake): $*"; exit 0 ;;
esac
if [[ "$*" == *test_*_cassette.py* ]]; then
  echo "Ran 31 tests in 0.1s"; echo OK; exit 0
fi
printf '{"schema_version":1,"tests_run":2000,"failures":0,"errors":0,"unexpected_successes":0,"expected_failures":0,"skipped":[' > "$3"
if [[ "$UNEXPECTED_SKIP" == 1 ]]; then
  printf '{"test_id":"sample.Case.test_missing","reason":"missing required tool"}' >> "$3"
fi
printf ']}\\n' >> "$3"
echo FIRST_BACKEND_EVIDENCE
if [[ "$UNEXPECTED_SKIP" == 1 ]]; then
  echo "test_missing (sample.Case.test_missing) ... skipped 'missing required tool'"
fi
for ((i=0; i<100; i++)); do echo "test_$i ... ok"; done
echo "Ran 2000 tests in 0.1s"
if [[ "$UNEXPECTED_SKIP" == 1 ]]; then echo "OK (skipped=1)"; else echo OK; fi
''',
                "node": "#!/bin/bash\necho '# tests 1'; echo '# pass 1'; echo '# fail 0'\n",
                # c8 wraps the frontend run: skip its own options, run what follows `node`.
                "c8": "#!/bin/bash\necho FAKE_C8\nwhile [ \"$1\" != node ]; do shift; done\nexec \"$@\"\n",
                "npm": "#!/bin/bash\necho '# tests 1'; echo '# pass 1'; echo '# fail 0'\n",
            }
            (root / "node_modules" / ".bin").mkdir(parents=True)
            for name, content in runners.items():
                if name == "c8" and not tools:
                    continue  # a machine without `npm ci`
                path = (root / "node_modules" / ".bin" / name) if name == "c8" else bin_dir / name
                path.write_text(content)
                path.chmod(0o700)
            # The real guard reads min_tests from the floor file beside it: a copy with a
            # floor that fits the fake run's 2000 tests.
            guard = root / "guard"
            guard.mkdir()
            shutil.copyfile(ROOT / "scripts/check_unittest_coverage.py",
                            guard / "check_unittest_coverage.py")
            (guard / "coverage_floor.json").write_text(
                '{"schema_version": 1, "min_tests": 1950, "python": {}}\n')
            routes = scripts / "run_routes_tests.sh"
            routes.write_text("#!/bin/bash\necho 'routes passed'\n")
            routes.chmod(0o700)
            env = {
                "PATH": str(bin_dir) + ":/usr/bin:/bin",
                "DATABASE_URL": "postgresql://synthetic.invalid/offline",
                "TURNOS_CI_ARTIFACTS_DIR": str(root / "evidence"),
                "REAL_PYTHON": sys.executable,
                "REAL_GUARD": str(guard / "check_unittest_coverage.py"),
                "UNEXPECTED_SKIP": "1" if unexpected_skip else "0",
                "PY_COVERAGE_EXIT": "0" if tools else "1",  # coverage.py importable or not
                **(env_extra or {}),
            }
            result = subprocess.run(["/bin/bash", str(scripts / "ci_local.sh")],
                                    env=env, text=True, capture_output=True, timeout=15)
            runs = list((root / "evidence").iterdir())
            self.assertEqual(len(runs), 1)
            self.assertIn(str(runs[0]), result.stdout)
            required = any(k in (env_extra or {}) for k in
                           ("CI", "GITLAB_CI", "GITHUB_ACTIONS", "TURNOS_REQUIRE_COVERAGE"))
            backend_ran = tools or not required  # required + no coverage.py: the surface fails first
            expected = {"backend.log", "frontend.log", "routes.log", "cassette-replay.log",
                        "cassette-audit.log", "e2e.log"}
            if backend_ran:
                expected.add("backend-results.json")
                self.assertIn("FIRST_BACKEND_EVIDENCE", (runs[0] / "backend.log").read_text())
            self.assertEqual({p.name for p in runs[0].iterdir()}, expected)
            frontend = self.frontend_log = (runs[0] / "frontend.log").read_text()
            if tools and (required or "TURNOS_COVERAGE" not in (env_extra or {})):
                self.assertIn("FAKE_C8", frontend)  # the frontend ran under c8
                self.assertIn("coverage floor OK (fake): scripts/coverage_floor.py check --kind js",
                              frontend)
            else:
                self.assertNotIn("FAKE_C8", frontend)
            self.assertEqual(runs[0].stat().st_mode & 0o077, 0)
            return result

    def test_a_machine_without_the_tools_skips_coverage_with_a_loud_warning(self):
        # the dev machine: no coverage.py, no `npm ci` — pre-commit and local-ci must not break.
        result = self._run(unexpected_skip=False, tools=False)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("GREEN", result.stdout)
        self.assertIn("backend coverage NOT MEASURED", out)
        self.assertIn("frontend coverage NOT MEASURED", out)
        self.assertIn("coverage was NOT measured", result.stdout)
        self.assertIn("WARNING:", result.stdout)  # also in the summary table
        self.assertNotIn("coverage_floor.py check", out + self.frontend_log)
        # the test-count floors need no tool and still run
        self.assertIn("coverage_floor.py count --log", self.frontend_log)
        self.assertIn("coverage guard OK", out)
        row = [l for l in result.stdout.splitlines() if l.startswith("frontend (OK)")][0]
        self.assertIn("frontend-coverage-skipped", row)
        self.assertNotIn("backend-coverage-skipped", row)

    def test_ci_requires_the_tools(self):
        for variable in ("CI", "GITLAB_CI", "GITHUB_ACTIONS", "TURNOS_REQUIRE_COVERAGE"):
            with self.subTest(variable):
                result = self._run(unexpected_skip=False, tools=False,
                                   env_extra={variable: "true" if variable != "TURNOS_REQUIRE_COVERAGE" else "1"})
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, out)
                self.assertIn("backend (FAIL)", result.stdout)
                self.assertIn("frontend (FAIL)", result.stdout)
                self.assertIn("coverage REQUIRED here (CI)", out)
                self.assertNotIn("GREEN", result.stdout)

    def test_ci_cannot_switch_the_measurement_off(self):
        result = self._run(unexpected_skip=False,
                           env_extra={"CI": "true", "TURNOS_COVERAGE": "0"})
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertNotIn("NOT MEASURED", out)
        self.assertIn("coverage_floor.py check --summary", out)
        self.assertIn("coverage_floor.py check --kind js", self.frontend_log)

    def test_a_developer_can_switch_the_measurement_off_on_purpose(self):
        result = self._run(unexpected_skip=False, env_extra={"TURNOS_COVERAGE": "0"})
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("backend coverage NOT MEASURED", out)
        self.assertIn("frontend coverage NOT MEASURED", out)
        self.assertNotIn("coverage_floor.py check", out + self.frontend_log)

    def test_unexpected_skip_still_fails_and_preserves_entire_report(self):
        result = self._run(unexpected_skip=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 unexpected test skip", result.stdout)
        self.assertIn("backend (FAIL)", result.stdout)
        self.assertIn("e2e (OK)", result.stdout)
        self.assertNotIn("FIRST_BACKEND_EVIDENCE", result.stdout)
        # the backend floor is not reached after a red guard
        self.assertNotIn("coverage floor OK (fake): scripts/coverage_floor.py check --summary", result.stdout)

    def test_green_run_also_retains_every_surface_report(self):
        result = self._run(unexpected_skip=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GREEN", result.stdout)
        self.assertIn("coverage floor OK (fake): scripts/coverage_floor.py check --summary", result.stdout)


if __name__ == "__main__":
    unittest.main()
