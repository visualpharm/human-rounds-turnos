"""tests/routes/test_admin_approve.py — routes-tier coverage: POST
/api/admin/requests/<id>/approve (`booking_requests._approve`), the
operator-side status transition. Covers the missing-turno_dt guard, the
role gate (reportes is not opted into `_request_staff`'s
`permitir_medico`/`permitir_admisionista`, so it gets the same 403 the
default require_admin flags would give), a real durable state change
(`booking_requests.status` -> 'aprobada' with `turno_dt` set, read back via
`db_booking.get_booking_request`, not just the 200), and the status-CAS 409 a
second approve on an already-resolved row hits
(`db_booking.AlreadyProcessed`). See docs/roadmap/tier3-harness-design.md, slice 4.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402

from app.core import db_booking  # noqa: E402

_SERVICIO = "cardiologia-hospital"
_CENTRO = "hospital-pinamar"


def _turno_futuro_iso():
    return (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()


class TestAdminApprove(RouteTestCase):

    def test_missing_turno_dt_400(self):
        sid = db_booking.create_booking_request(
            "30999441", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        status, _ = self._post_json(
            f"/api/admin/requests/{sid}/approve", {}, headers=admin_cookie())
        self.assertEqual(status, 400)

    def test_reportes_role_403(self):
        sid = db_booking.create_booking_request(
            "30999442", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        status, body = self._post_json(
            f"/api/admin/requests/{sid}/approve", {"turno_dt": _turno_futuro_iso()},
            headers=admin_cookie(rol="reportes"))
        self.assertEqual(status, 403)
        self.assertEqual(body.get("error"), "rol_no_autorizado")

    def test_approve_sets_estado_and_turno_dt(self):
        sid = db_booking.create_booking_request(
            "30999443", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        turno_iso = _turno_futuro_iso()
        status, body = self._post_json(
            f"/api/admin/requests/{sid}/approve", {"turno_dt": turno_iso},
            headers=admin_cookie(rol="admin"))
        self.assertEqual(status, 200)
        self.assertEqual(body["solicitud"]["estado"], "aprobada")

        row = db_booking.get_booking_request(sid)
        self.assertEqual(row.get("estado"), "aprobada")
        self.assertIsNotNone(row.get("turno_dt"))

    def test_second_approve_on_resolved_row_409(self):
        sid = db_booking.create_booking_request(
            "30999444", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        turno_iso = _turno_futuro_iso()
        status1, _ = self._post_json(
            f"/api/admin/requests/{sid}/approve", {"turno_dt": turno_iso},
            headers=admin_cookie(rol="admin"))
        self.assertEqual(status1, 200)

        status2, _ = self._post_json(
            f"/api/admin/requests/{sid}/approve", {"turno_dt": turno_iso},
            headers=admin_cookie(rol="admin"))
        self.assertEqual(status2, 409)


if __name__ == "__main__":
    unittest.main()
