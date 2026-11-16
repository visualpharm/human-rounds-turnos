"""tests/routes/test_nav_api.py — routes-tier coverage: GET /api/nav
(`app_modules._get_nav`), deliberately PUBLIC and platform-core per its own
comment in app_modules.py -- no session, no role, works even with every app
module disabled. Contrasts with test_admin_modules_role_matrix.py's GET
/api/admin/modules, the admin-gated sibling that serves the SAME
module-derived data behind a role matrix. See
docs/roadmap/tier3-harness-design.md, slice 4 ("/api/nav + modules API role
matrix" is one of the named coverage targets).
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase  # noqa: E402


class TestNavApi(RouteTestCase):

    def test_public_no_session_required(self):
        status, body = self._get_json("/api/nav")
        self.assertEqual(status, 200)
        self.assertIsInstance((body or {}).get("nav_entries"), list)

    def test_each_entry_has_path_and_label(self):
        status, body = self._get_json("/api/nav")
        self.assertEqual(status, 200)
        for entry in body["nav_entries"]:
            self.assertIn("path", entry)
            self.assertIn("label", entry)
            self.assertTrue(entry["path"].startswith("/"))

    def test_only_get_is_handled(self):
        status, _ = self._post_json("/api/nav", {})
        # app_modules_router registers GET only; POST falls through to the
        # terminal's 404 for an unmatched route.
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
