"""scripts/smoke_demo.py: where it reads its credentials and that it imports from anywhere."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

import _env


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/smoke_demo.py"


class SmokeDemoTest(unittest.TestCase):
    def test_demo_smoke_reads_the_coolify_token_from_its_environment_only(self):
        # Owner rule 2026-10-15: no op/opx CLI from scripts; the vault broker injects
        # COOLIFY_API_TOKEN when the freshness check should run.
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ('"op"', "/op\"", "opx", "--reveal"):
            self.assertNotIn(forbidden, source)
        spec = importlib.util.spec_from_file_location("smoke_demo_token_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with _env.patched_env({"COOLIFY_API_TOKEN": " synthetic-token "}):
            self.assertEqual(module._coolify_token(), "synthetic-token")
        with _env.patched_env(COOLIFY_API_TOKEN=None):
            self.assertIsNone(module._coolify_token())

    def test_demo_smoke_reads_one_request_so_the_access_log_stays_small(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"/api/admin/requests?limit=1"', source)
        self.assertNotIn('"/api/admin/requests"', source)

    def test_demo_smoke_imports_from_outside_the_checkout(self):
        result = subprocess.run(
            [sys.executable, "-c", (
                "import runpy; "
                f"runpy.run_path({str(SCRIPT)!r}, run_name='smoke_demo_import_test')"
            )],
            cwd="/",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
