"""The guard that stops the two ways of running tests that never run this repository's tests:
`-m unittest test.<x>` (the stdlib package) and `discover -s test` (the directory is `tests/`)."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude/hooks/unittest-discover-guard.py"


def _decide(command):
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                         text=True, capture_output=True, check=True).stdout
    return json.loads(out)["hookSpecificOutput"] if out.strip() else None


class UnittestDiscoverGuardTests(unittest.TestCase):
    def test_the_old_directory_name_is_refused_with_the_new_command(self):
        for command, hint in (
                ('python3 -m unittest discover -s test -p "test_x.py"', '-s tests -p "test_x.py"'),
                ("python3 -m unittest discover --start-directory=test", "--start-directory=tests"),
                ("python3 -m unittest discover -s test/routes -p test_y.py", "-s tests/routes")):
            with self.subTest(command=command):
                decision = _decide(command)
                self.assertEqual(decision["permissionDecision"], "deny")
                self.assertIn(hint, decision["permissionDecisionReason"])

    def test_a_dotted_module_run_is_refused_with_discover(self):
        for command in ("python3 -m unittest test.test_x", "python3 -m unittest tests.test_x"):
            with self.subTest(command=command):
                decision = _decide(command)
                self.assertEqual(decision["permissionDecision"], "deny")
                self.assertIn('discover -s tests -p "test_x.py"', decision["permissionDecisionReason"])

    def test_the_right_forms_pass(self):
        for command in ('python3 -m unittest discover -s tests -p "test_x.py"',
                        "python3 -m unittest discover -s tests/routes -p test_y.py",
                        "node --test tests/x.test.mjs", "npx playwright test"):
            with self.subTest(command=command):
                self.assertIsNone(_decide(command))

    def test_the_hook_is_registered(self):
        settings = (ROOT / ".claude/settings.json").read_text(encoding="utf-8")
        self.assertIn("unittest-discover-guard.py", settings)


if __name__ == "__main__":
    unittest.main()
