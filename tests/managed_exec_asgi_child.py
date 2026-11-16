"""Synthetic child for the fixed full-ASGI local-CI profile."""

import os
import unittest


class ManagedExecAsgiChildTest(unittest.TestCase):
    def test_profile_injects_fixed_asgi_without_parent_controls(self):
        self.assertEqual(
            os.environ.get("DATABASE_URL"),
            "postgresql://fixture:asgi-profile-child-sentinel@127.0.0.1/fixture",
        )
        self.assertEqual(os.environ.get("TURNOS_ASGI"), "1")
        self.assertEqual(os.environ.get("TURNOS_ENV_FILE"), os.devnull)
        for key in (
            "TEST_DATABASE_URL", "TURNOS_SECRET_SOURCE", "TURNOS_ASGI_SOURCE",
            "OP_SERVICE_ACCOUNT_TOKEN", "HOME", "PYTHONPATH", "PYTHONHOME",
            "VIRTUAL_ENV", "LEAKED_AMBIENT",
        ):
            self.assertNotIn(key, os.environ)


if __name__ == "__main__":
    unittest.main()
