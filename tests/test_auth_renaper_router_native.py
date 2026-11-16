"""test_auth_renaper_router_native.py — dark-vs-native byte parity for
`cuentas/auth_renaper_router.py` (native-migration campaign, family W3b —
the dark-only lifecycle modules,
docs/roadmap/native-migration-campaign-20260826.md).

`cuentas.auth_renaper` was entirely absent from the native ASGI call graph
before this slice — the architecture census's `_DARK_ROUTE_REPRESENTATIVES`
probed `GET /auth/renaper` and found only the terminal. Both transports now
call the SAME `start_flow_decision()`/`callback_decision()` functions
`test_auth_renaper.py` already exercises exhaustively (anti-lockout, rate
limit, DNI extraction, session minting) — this file proves the TRANSPORT
wiring (cookie read/write through a real `Request`/`Response`, the dual
Set-Cookie on a successful callback) matches the legacy `handle()` path
byte for byte, with `TURNOS_FAKE_RENAPER=1` (no network).

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_auth_renaper_router_native.py"
"""
import os
import sys
import unittest
import _lifespan
from unittest import mock

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TEST_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", DATABASE_URL="")

from app.core import config  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.cuentas import auth_renaper  # noqa: E402

auth_renaper.ENV_PATH = ""
config.ENV_PATH = ""

from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402


def setUpModule():
    _lifespan.no_background_services()


class AuthRenaperRouterTestCase(unittest.TestCase):
    KEYS = ("TURNOS_AUTH_METHODS", "TURNOS_RENAPER_CLIENT_ID",
            "TURNOS_RENAPER_CLIENT_SECRET", "TURNOS_FAKE_RENAPER",
            "TURNOS_FAKE_RENAPER_DNI", "TURNOS_RENAPER_PER_HOUR")

    def setUp(self):
        _env.set_env(self, dict.fromkeys(self.KEYS), TURNOS_AUTH_METHODS="password,renaper",
                     TURNOS_FAKE_RENAPER="1")
        auth_renaper._HITS.clear()
        self.client = TestClient(app_module.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        auth_renaper._HITS.clear()


class TestStartFlowParity(AuthRenaperRouterTestCase):
    def test_start_sets_state_cookie_and_redirects_to_fake_callback(self):
        resp = self.client.get("/auth/renaper?next=/my-requests", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["location"]
        self.assertTrue(location.startswith("/auth/renaper/callback?"))
        self.assertIn(auth_renaper.STATE_COOKIE, resp.cookies)

    def test_renaper_disabled_redirects_to_common_login(self):
        _env.set_env(self, TURNOS_FAKE_RENAPER=None)  # no creds -> renaper off (anti-lockout)
        resp = self.client.get("/auth/renaper", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "/account?mode=login")

    def test_rate_limit_matches_dark_mode(self):
        _env.set_env(self, TURNOS_RENAPER_PER_HOUR="2")
        self.client.get("/auth/renaper")
        self.client.get("/auth/renaper")
        resp = self.client.get("/auth/renaper", follow_redirects=False)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Demasiados intentos", resp.text)


class TestCallbackParity(AuthRenaperRouterTestCase):
    def test_full_fake_flow_creates_session_and_clears_state_cookie(self):
        start = self.client.get("/auth/renaper?next=/my-requests", follow_redirects=False)
        callback_url = start.headers["location"]

        with mock.patch.object(db_accounts, "login_renaper", return_value=1) as ln, \
             mock.patch.object(db_accounts, "create_session", return_value="native-tok") as cs:
            resp = self.client.get(callback_url, follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "/my-requests")
        set_cookie_headers = resp.headers.get_list("set-cookie")
        self.assertEqual(len(set_cookie_headers), 2, set_cookie_headers)
        joined = " ".join(set_cookie_headers)
        self.assertIn("st_ses=", joined)
        self.assertIn("native-tok", joined)
        self.assertIn("HttpOnly", joined)
        self.assertTrue(any(
            h.startswith(auth_renaper.STATE_COOKIE + "=") and "Max-Age=0" in h
            for h in set_cookie_headers
        ))
        dni, datos, claims = ln.call_args[0]
        self.assertEqual(dni, "99000001")
        self.assertTrue(claims.get("fake"))
        cs.assert_called_once_with("99000001")

    def test_callback_without_state_cookie_is_400(self):
        start = self.client.get("/auth/renaper", follow_redirects=False)
        callback_url = start.headers["location"]

        self.client.cookies.clear()
        resp = self.client.get(callback_url, follow_redirects=False)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No pudimos completar el ingreso", resp.text)


if __name__ == "__main__":
    unittest.main()
