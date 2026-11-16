"""tests/routes/test_admin_reject.py — routes-tier coverage: POST
/api/admin/requests/<id>/reject (`booking_requests._reject`). Covers the
missing-motivo guard, the durable state transition (status -> 'rechazada'
with `rejection_reason` persisted, read back via `db_booking.get_booking_request`),
the status-CAS 409 on an already-resolved row, and the `medico` role's
narrower gate: `_request_staff` lets a doctor work only requests assigned to
THEIR OWN catalog id -- one that belongs to nobody gets the same 404 a
missing id would (never a 403, so a doctor probing ids can't learn which
ones exist). See docs/roadmap/tier3-harness-design.md, slice 4.
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

_SERVICIO = "cardiologia-hospital"
_CENTRO = "hospital-pinamar"


class TestAdminReject(RouteTestCase):

    def test_missing_motivo_400(self):
        sid = db_booking.create_booking_request(
            "30999551", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        status, _ = self._post_json(
            f"/api/admin/requests/{sid}/reject", {}, headers=admin_cookie())
        self.assertEqual(status, 400)

    def test_reject_sets_estado_and_reason(self):
        sid = db_booking.create_booking_request(
            "30999552", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        status, body = self._post_json(
            f"/api/admin/requests/{sid}/reject",
            {"motivo": "Estudio ya realizado en otro centro"},
            headers=admin_cookie(rol="admisionista"))
        self.assertEqual(status, 200)
        self.assertEqual(body["solicitud"]["estado"], "rechazada")

        row = db_booking.get_booking_request(sid)
        self.assertEqual(row.get("estado"), "rechazada")
        self.assertEqual(row.get("motivo_rechazo"), "Estudio ya realizado en otro centro")

    def test_second_reject_on_resolved_row_409(self):
        sid = db_booking.create_booking_request(
            "30999553", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        status1, _ = self._post_json(
            f"/api/admin/requests/{sid}/reject", {"motivo": "primer rechazo"},
            headers=admin_cookie())
        self.assertEqual(status1, 200)

        status2, _ = self._post_json(
            f"/api/admin/requests/{sid}/reject", {"motivo": "segundo intento"},
            headers=admin_cookie())
        self.assertEqual(status2, 409)

    def test_medico_cannot_reject_a_request_not_assigned_to_them_404(self):
        sid = db_booking.create_booking_request(
            "30999554", "Pide", "Turno", "1990-01-01", None, _SERVICIO, _CENTRO)
        status, _ = self._post_json(
            f"/api/admin/requests/{sid}/reject", {"motivo": "no me pertenece"},
            headers=admin_cookie(rol="medico"))
        self.assertEqual(status, 404)

        row = db_booking.get_booking_request(sid)
        self.assertEqual(row.get("estado"), "pendiente")  # untouched


if __name__ == "__main__":
    unittest.main()
