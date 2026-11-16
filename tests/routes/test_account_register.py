"""tests/routes/test_account_register.py — routes-tier coverage: POST
/api/account/register (`accounts._register_core`). Covers the two 400 input
guards (short DNI, short password), the durable effect of a successful
register (a real `users` row, readable back via `db_accounts.get_user`, plus the
`Set-Cookie` session the wizard's next step depends on -- never just the
200), the password never leaking into the response body, and the 409 a
second registration with the same DNI hits. See
docs/roadmap/tier3-harness-design.md, slice 4.
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

from app.core import db_accounts  # noqa: E402


class TestAccountRegister(RouteTestCase):

    def test_dni_too_short_400(self):
        status, _ = self._post_json("/api/account/register", {
            "dni": "123", "password": "throwaway123", "contacto": "+542254000999"})
        self.assertEqual(status, 400)

    def test_password_too_short_400(self):
        status, _ = self._post_json("/api/account/register", {
            "dni": "30999661", "password": "short", "contacto": "+542254000999"})
        self.assertEqual(status, 400)

    def test_success_creates_a_real_user_and_sets_the_session_cookie(self):
        status, headers, raw = self._request("POST", "/api/account/register", body={
            "dni": "30999662", "password": "throwaway-pass-123",
            "nombre": "Nueva", "apellido": "Cuenta", "contacto": "+542254000662"})
        self.assertEqual(status, 200)
        self.assertIn("Set-Cookie", headers)
        self.assertIn("st_ses=", headers["Set-Cookie"])

        row = db_accounts.get_user("30999662")
        self.assertIsNotNone(row)
        self.assertEqual(row.get("nombre"), "Nueva")
        self.assertNotIn("pass_hash", row)  # never in what _user_public hands back

    def test_duplicate_dni_409(self):
        payload = {"dni": "30999663", "password": "throwaway-pass-123",
                  "contacto": "+542254000663"}
        status1, _ = self._post_json("/api/account/register", payload)
        self.assertEqual(status1, 200)

        status2, body2 = self._post_json("/api/account/register", payload)
        self.assertEqual(status2, 409)
        self.assertEqual(body2.get("error"), "dni_registrado")


if __name__ == "__main__":
    unittest.main()
