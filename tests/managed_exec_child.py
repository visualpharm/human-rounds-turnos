"""Executed only through the synthetic managed-exec profile test."""

import os
import unittest


class ManagedExecChildTest(unittest.TestCase):
    def test_profile_injects_only_its_scoped_fixture_values(self):
        self.assertEqual(
            os.environ.get("DATABASE_URL"),
            "postgresql://fixture:profile-child-sentinel@127.0.0.1/fixture",
        )
        self.assertNotIn("LEAKED_AMBIENT", os.environ)
        self.assertNotIn("TURNOS_SECRET_SOURCE", os.environ)
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", os.environ)
        self.assertNotIn("HOME", os.environ)
        self.assertEqual(os.environ.get("TURNOS_ENV_FILE"), os.devnull)


if __name__ == "__main__":
    unittest.main()
