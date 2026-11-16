"""tests/routes/test_fixture_isolation.py — routes-tier pilot #3: a
regression test for the FIXTURE ITSELF (`_routes_fixture.WorkerDb`), not
for a route. Proves the design doc's disposability claim directly: a test
that leaves data behind must not leak into the next test run against the
SAME worker database.

unittest runs test methods within one TestCase in alphabetical order (no
`@unittest.skip`/random-order plugin in this repo's stdlib-only setup) --
`test_a_...` then `test_b_...` is a real, load-bearing ordering here, not
decoration. If `RouteTestCase.setUp()`'s `truncate_all()` call were ever
removed or short-circuited, `test_b_sees_a_clean_database` would go red
immediately (see docs/roadmap/tier3-harness-design.md's Area 2 test plan:
"a regression test for the fixture mechanism itself, not just for
routes").
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

from app.core import db_booking  # noqa: E402


class TestFixtureIsolation(RouteTestCase):

    def test_a_leaves_a_row_behind_on_purpose(self):
        sid = db_booking.create_booking_request(
            "50333444", "Leak", "Testcase", "1990-01-01",
            None, "cardiologia", "hospital-pinamar", None)
        self.assertIsInstance(sid, int)
        self.assertEqual(len(db_booking.list_admin()), 1)

    def test_b_sees_a_clean_database(self):
        # If setUp's truncate_all() didn't run (or ran against the wrong
        # database), the row test_a left behind would still be here.
        self.assertEqual(db_booking.list_admin(), [])


if __name__ == "__main__":
    unittest.main()
