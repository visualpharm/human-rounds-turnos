"""tests/routes/test_admin_requests_roundtrip.py — routes-tier pilot #2: a
REAL database write (via `db_booking.create_booking_request`, the same function the
public booking flow calls -- never a hand-built row) followed by a REAL
HTTP read (GET /api/admin/requests) through the live `server.Handler`,
proving the worker's disposable Postgres database is genuinely wired end to
end, not just present. See docs/roadmap/tier3-harness-design.md, Area 2's
"Pilot scope".
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

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402

from app.core import db_booking  # noqa: E402


class TestAdminRequestsRoundtrip(RouteTestCase):

    def test_seeded_request_appears_in_admin_list(self):
        sid = db_booking.create_booking_request(
            "30111222", "Juan", "Pérez", "1985-03-20",
            "351-555-0000", "cardiologia", "hospital-pinamar", "image/png")
        self.assertIsInstance(sid, int)

        status, body = self._get_json(
            "/api/admin/requests", headers=admin_cookie(rol="admin"))
        self.assertEqual(status, 200)
        rows = body.get("solicitudes") if isinstance(body, dict) else body
        self.assertIsInstance(rows, list)
        ids = [r.get("id") for r in rows]
        self.assertIn(sid, ids)
        row = next(r for r in rows if r.get("id") == sid)
        self.assertEqual(row.get("dni"), "30111222")
        self.assertEqual(row.get("estado"), "pendiente")

    def test_medico_and_admisionista_also_see_the_queue(self):
        # Unlike /api/admin/hsi-sync (default flags), _admin_list opts BOTH
        # medico and admisionista in -- this is the "different endpoint,
        # different gate" contrast the design doc's pilot calls for.
        db_booking.create_booking_request(
            "40222333", "Rita", "Vega", "1988-01-01",
            None, "dermatologia", "riverside-clinic", None)
        for rol in ("medico", "admisionista"):
            with self.subTest(rol=rol):
                status, _ = self._get_json(
                    "/api/admin/requests", headers=admin_cookie(rol=rol))
                self.assertEqual(status, 200)

    def test_reportes_role_rejected(self):
        status, body = self._get_json(
            "/api/admin/requests", headers=admin_cookie(rol="reportes"))
        self.assertEqual(status, 403)
        self.assertEqual((body or {}).get("error"), "rol_no_autorizado")


if __name__ == "__main__":
    unittest.main()
