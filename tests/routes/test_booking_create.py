"""tests/routes/test_booking_create.py — routes-tier coverage: the patient's
own POST /api/requests (`booking_requests._create`), the real HTTP entry
point behind the wizard's "Confirmá" step. Covers the auth gate, the
missing/invalid servicio_id guard, and the durable effect of a successful
POST -- a real `booking_requests` row, readable back through `db.
get_booking_request`, not just a 200. See
docs/roadmap/tier3-harness-design.md, slice 4 (routes tier coverage
expansion).

The selected IDs belong to the fictional public example-town pack. The
service uses `modo: solicitud` (no referral upload and no auto-confirmation),
so the test remains self-contained in a fresh public checkout.
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
from app.core import db_accounts  # noqa: E402

_SERVICIO = "medicina-general-riverside-clinic"
_CENTRO = "riverside-clinic"
_DNI = "30999111"


def _crear_cuenta(dni=_DNI):
    db_accounts.create_user(dni, "Marta", "Gómez", "1980-05-10", "F",
                    "+542254000111", "throwaway-pass-12345")
    token = db_accounts.create_session(dni)
    return {"Cookie": "st_ses=" + token}


class TestBookingCreate(RouteTestCase):

    def test_no_session_401(self):
        status, body = self._post_json(
            "/api/requests", {"servicio_id": _SERVICIO, "centro_slug": _CENTRO})
        self.assertEqual(status, 401)
        self.assertEqual((body or {}).get("error"), "auth_requerida")

    def test_missing_servicio_id_400(self):
        cookie = _crear_cuenta("30999112")
        status, _ = self._post_json("/api/requests", {"centro_slug": _CENTRO},
                                     headers=cookie)
        self.assertEqual(status, 400)

    def test_success_creates_a_real_row(self):
        cookie = _crear_cuenta("30999113")
        status, body = self._post_json(
            "/api/requests",
            {"servicio_id": _SERVICIO, "centro_slug": _CENTRO,
             "preferencia": "mañana"},
            headers=cookie)
        self.assertEqual(status, 200)
        sid = (body or {}).get("id")
        self.assertIsInstance(sid, int)

        row = db_booking.get_booking_request(sid)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("dni"), "30999113")
        self.assertEqual(row.get("servicio_id"), _SERVICIO)
        self.assertEqual(row.get("estado"), "pendiente")


if __name__ == "__main__":
    unittest.main()
