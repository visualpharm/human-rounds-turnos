"""tests/routes/test_account_login.py — routes-tier coverage: POST
/api/account/login (`accounts._login`). Seeds a real account through
`db_accounts.create_user` (the same function `_register` calls -- never a
hand-built `pass_hash`), then drives the real HTTP login: right password ->
Set-Cookie session that `accounts.current_user` resolves back to the SAME
account (round-tripped through GET /api/account/me, not just trusted by
inspection); wrong password -> 401 without a cookie; unknown DNI -> the
SAME 401 shape (never leaks which branch failed). See
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

_DNI = "30999771"
_LOGIN_VALUE = "throwaway-pass-771"


class TestAccountLogin(RouteTestCase):

    def setUp(self):
        super().setUp()
        db_accounts.create_user(_DNI, "Login", "DePrueba", "1992-03-03", "F",
                        "+542254000771", _LOGIN_VALUE)

    def test_wrong_password_401_no_cookie(self):
        status, headers, _ = self._request(
            "POST", "/api/account/login",
            body={"dni": _DNI, "password": "esto-no-es"})
        self.assertEqual(status, 401)
        self.assertNotIn("Set-Cookie", headers)

    def test_unknown_dni_401_same_error_shape(self):
        status, body = self._post_json(
            "/api/account/login", {"dni": "30999772", "password": "lo-que-sea"})
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "credenciales")

    def test_right_password_sets_cookie_that_resolves_the_account(self):
        status, headers, raw = self._request(
            "POST", "/api/account/login",
            body={"dni": _DNI, "password": _LOGIN_VALUE})
        self.assertEqual(status, 200)
        set_cookie = headers.get("Set-Cookie", "")
        self.assertIn("st_ses=", set_cookie)
        token = set_cookie.split("st_ses=", 1)[1].split(";", 1)[0]

        status2, body2 = self._get_json(
            "/api/account/me", headers={"Cookie": f"st_ses={token}"})
        self.assertEqual(status2, 200)
        self.assertEqual((body2 or {}).get("usuario", {}).get("dni"), _DNI)


if __name__ == "__main__":
    unittest.main()
