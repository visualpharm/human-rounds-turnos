"""tests/routes/test_booking_limits.py — routes-tier coverage: the
booking_limits.py guardrail as it's actually enforced, on the patient's own
POST /api/requests (`booking_requests._create`). The fictional public-pack
general-medicine service carries no `turnos_activos_max` override, so the
effective limit is the site default (`booking_limits.DEFAULT_LIMIT == 1`) --
a second request for the same DNI+service while the first is still
pending/approved must be rejected, and rejected WITHOUT creating a second
row (the durable-effect assertion the design doc calls for, not just the
409 status). See docs/roadmap/tier3-harness-design.md, slice 4.
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
_DNI = "30999221"


def _crear_cuenta(dni):
    db_accounts.create_user(dni, "Rodrigo", "Paz", "1975-02-02", "M",
                    "+542254000222", "throwaway-pass-12345")
    token = db_accounts.create_session(dni)
    return {"Cookie": "st_ses=" + token}


class TestBookingLimits(RouteTestCase):

    def test_second_request_same_service_hits_the_limit(self):
        cookie = _crear_cuenta(_DNI)
        payload = {"servicio_id": _SERVICIO, "centro_slug": _CENTRO}

        status1, body1 = self._post_json("/api/requests", payload, headers=cookie)
        self.assertEqual(status1, 200)
        sid1 = body1["id"]

        status2, body2 = self._post_json("/api/requests", payload, headers=cookie)
        self.assertEqual(status2, 409)
        self.assertEqual(body2.get("error"), "limite_turnos_activos")
        self.assertEqual(body2.get("limite"), 1)

        # Durable effect: still exactly ONE pending row for this dni+service
        # -- the rejected call must not have inserted a second one.
        filas = [r for r in db_booking.list_admin() if r.get("dni") == _DNI]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["id"], sid1)

    def test_idem_key_replay_returns_the_same_success_not_a_409(self):
        cookie = _crear_cuenta("30999222")
        payload = {"servicio_id": _SERVICIO, "centro_slug": _CENTRO,
                   "idem_key": "routes-tier-idem-1"}

        status1, body1 = self._post_json("/api/requests", payload, headers=cookie)
        self.assertEqual(status1, 200)
        sid1 = body1["id"]

        # A doubled tap with the SAME idem_key must resolve to the same
        # success, never the 409 a naive re-count would produce.
        status2, body2 = self._post_json("/api/requests", payload, headers=cookie)
        self.assertEqual(status2, 200)
        self.assertEqual(body2["id"], sid1)

        filas = [r for r in db_booking.list_admin() if r.get("dni") == "30999222"]
        self.assertEqual(len(filas), 1)


if __name__ == "__main__":
    unittest.main()
