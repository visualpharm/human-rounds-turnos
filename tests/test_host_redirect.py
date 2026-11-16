"""Canonical-domain behavior for the production hostname migration."""

import unittest

from app.core import host_redirect


class HostRedirectTest(unittest.TestCase):
    def test_old_host_preserves_path_and_query(self):
        location = host_redirect.target(
            "sana-turnos.brieflysites.com",
            "/service/ecografia?source=old",
        )
        self.assertEqual(
            location,
            "https://pinamar.humanrounds.org/service/ecografia?source=old",
        )

    def test_new_and_local_hosts_are_not_redirected(self):
        self.assertIsNone(host_redirect.target("pinamar.humanrounds.org", "/"))
        self.assertIsNone(host_redirect.target("127.0.0.1:7700", "/"))


if __name__ == "__main__":
    unittest.main()
