"""Offer links fail as a useful bounded 503 when the database is unavailable.

Native route contract (2026-08-30): the outage translation is the router's
own job now (`appointment_offer_router.py` catches `db.DbUnavailable`), so
both cases drive the real composition via TestClient and only the domain
seam is mocked offline.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.modules.turnos import appointment_offer  # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class TestAppointmentOfferOffline(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_offer_actions_translate_db_outage_to_json_503(self):
        cases = (
            ("POST", "/api/offer/token/accept", appointment_offer.waitlist, "claim"),
            ("POST", "/api/offer/token/reject", appointment_offer.waitlist, "reject_offer"),
        )
        for method, path, owner, attribute in cases:
            with self.subTest(path=path):
                with mock.patch.object(
                    owner,
                    attribute,
                    side_effect=db.DbUnavailable("offline"),
                ):
                    r = self.client.post(path)
                self.assertEqual(r.status_code, 503)
                self.assertEqual(r.json(), {"error": "base de datos no disponible"})

    def test_patient_offer_page_translates_db_outage_to_html_503(self):
        with mock.patch.object(
            appointment_offer.store,
            "offer_by_token",
            side_effect=db.DbUnavailable("offline"),
        ):
            r = self.client.get("/appointment/offer/token")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("No pudimos cargar este turno", r.text)
        self.assertIn("Servicios y centros", r.text)


if __name__ == "__main__":
    unittest.main()
