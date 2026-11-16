"""Both core.config and core.db must honor the one effective env-file path."""

import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env  # noqa: E402
from app.core import config  # noqa: E402
from app.core import db_schema  # noqa: E402


class EffectiveEnvPathTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="turnos-effective-env-")
        self.addCleanup(self.dir.cleanup)
        self.fallback = Path(self.dir.name, "fallback.env")
        self.override = Path(self.dir.name, "override.env")
        self.fallback.write_text("DATABASE_URL=postgresql://fixture:fallback@127.0.0.1/fallback\n", encoding="utf-8")
        self.override.write_text("DATABASE_URL=postgresql://fixture:override@127.0.0.1/override\n", encoding="utf-8")
        self.path_patch = patch.object(config, "ENV_PATH", str(self.fallback))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(config._reset_cache_para_tests)   # after the override is put back
        _env.set_env(self, TURNOS_ENV_FILE=None)
        config._reset_cache_para_tests()

    def test_db_uses_config_fallback_path_not_a_second_env_reader(self):
        self.assertEqual(config.env_file_path(), str(self.fallback))
        self.assertEqual(db_schema._load_env_file()["DATABASE_URL"], config.dotenv_values()["DATABASE_URL"])

    def test_explicit_override_replaces_the_fallback_for_both_layers(self):
        _env.set_env(self, TURNOS_ENV_FILE=str(self.override))
        config._reset_cache_para_tests()
        self.assertEqual(config.env_file_path(), str(self.override))
        self.assertEqual(db_schema._load_env_file()["DATABASE_URL"], config.dotenv_values()["DATABASE_URL"])

    def test_missing_explicit_override_never_falls_back_to_checkout_env(self):
        _env.set_env(self, TURNOS_ENV_FILE=str(Path(self.dir.name, "missing.env")))
        config._reset_cache_para_tests()
        self.assertEqual(config.dotenv_values(), {})
        self.assertEqual(db_schema._load_env_file(), {})

    def test_production_direct_environment_injection_still_wins_without_wrapper(self):
        # Deliberately subprocess-isolated: this test never inspects this
        # developer process's environment or a real local .env.
        probe = (
            "from app.core import config, db\n"
            "import os, sys\n"
            "expected = os.environ['DATABASE_URL']\n"
            "sys.exit(0 if config.get('DATABASE_URL') == expected and db._db_url() == expected else 1)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=ROOT,
            env={
                "PATH": os.defpath,
                "DATABASE_URL": "postgresql://fixture:direct-env-only@127.0.0.1/direct",
                "TURNOS_ENV_FILE": os.devnull,
            },
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
