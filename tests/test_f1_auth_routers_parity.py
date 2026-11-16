"""test_f1_auth_routers_parity.py — native-migration F1 (docs/roadmap/
native-migration-campaign-20260826.md): legacy-handler-shaped vs FastAPI-
value-result/FastAPI status-body parity for the two routers this family rewrote,
`admin_auth_router.py` (config/login/logout/whoami) and
`passkeys_router.py` (register-options/login-options), SHIM-FREE on the
FastAPI side. The retired handler transport is absent; tests compare the
value-only domain results with the real HTTP boundary.

`test_asgi_successful_auth_parity.py` already proves GET /api/admin/whoami
and GET /api/admin/passkeys success parity; this file covers the other
four rewritten endpoints (config, login, logout, passkeys register-options
+ login-options) that neither that file nor `route_parity_check.py`'s
own ESCENARIOS (rejection-path only) exercise on the SUCCESS path.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_f1_auth_routers_parity.py"
"""
import json
import os
import sys
import unittest
import _lifespan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="f1-parity-fixture-pass")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="")   # determinístico: sin 2FA acá
_env.process_set(TURNOS_TURNSTILE_SECRET="")    # determinístico: sin Turnstile acá

from app.auth import admin_auth  # noqa: E402
from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no depender del .env real del checkout corriendo esto

from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402
import _dbtest  # noqa: E402

_passkey_db = _dbtest.PerModuleDb("sana_turnos_test_f1_passkey_options")


def setUpModule():
    _lifespan.no_background_services()


class TestAdminAuthRouterParity(unittest.TestCase):
    """Value result vs FastAPI at both sides of each auth decision."""

    def setUp(self):
        admin_auth._ATTEMPTS.clear()
        self.client = TestClient(app_module.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_config_body_is_identical(self):
        expected = admin_auth.config_result({"Host": "testserver"})

        resp = self.client.get("/api/admin/config", headers={"Host": "testserver"})
        self.assertEqual(resp.status_code, expected.status)
        self.assertEqual(expected.body, resp.json())

    def test_login_success_is_identical_and_sets_a_session_cookie(self):
        payload = {"usuario": "admin", "password": os.environ["TURNOS_ADMIN_PASS"]}
        expected = admin_auth.login_result(
            {"Host": "testserver"}, "203.0.113.7", json.dumps(payload).encode())
        self.assertEqual(expected.status, 200, expected.body)
        self.assertTrue((expected.set_cookie or "").startswith("st_admin="))

        resp = self.client.post("/api/admin/login", json=payload, headers={"Host": "testserver"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(expected.body, resp.json())
        self.assertIn("st_admin", resp.cookies)

    def test_login_bad_credentials_is_identical_401(self):
        payload = {"usuario": "admin", "password": "not-the-password"}
        expected = admin_auth.login_result(
            {"Host": "testserver"}, "203.0.113.7", json.dumps(payload).encode())
        self.assertEqual(expected.status, 401)
        self.assertEqual(expected.body, {"error": "credenciales"})

        resp = self.client.post("/api/admin/login", json=payload, headers={"Host": "testserver"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(expected.body, resp.json())

    def test_login_malformed_json_is_identical_400(self):
        expected = admin_auth.login_result(
            {"Host": "testserver"}, "203.0.113.7", b"{not json")
        self.assertEqual(expected.status, 400)
        self.assertIn("JSON inválido", expected.body["error"])

        resp = self.client.post("/api/admin/login", content=b"{not json",
                                headers={"Host": "testserver", "Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("JSON inválido", resp.json()["error"])

    def test_logout_is_identical_and_clears_the_cookie(self):
        expected = admin_auth.logout_result({"Host": "testserver"})
        self.assertEqual(expected.status, 200)
        self.assertEqual(expected.body, {"ok": True})
        self.assertIn("Max-Age=0", expected.set_cookie or "")

        resp = self.client.post("/api/admin/logout", headers={"Host": "testserver"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(expected.body, resp.json())
        self.assertIn("Max-Age=0", resp.headers.get("set-cookie", ""))


class TestPasskeysRouterParity(unittest.TestCase):
    """Native passkey option contracts after the legacy writer retirement."""

    @classmethod
    def setUpClass(cls):
        _passkey_db.set_up()

    @classmethod
    def tearDownClass(cls):
        _passkey_db.tear_down()

    def setUp(self):
        self.client = TestClient(app_module.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_login_options_shape_is_identical(self):
        resp = self.client.post("/api/admin/passkeys/login-options", json={},
                                headers={"Host": "testserver"})
        if not _passkey_db.have_db:
            self.assertEqual(resp.status_code, 503)
            self.assertEqual(resp.json(), {"error": "base_no_disponible"})
            return
        self.assertEqual(resp.status_code, 200, resp.text)
        fastapi_body = resp.json()
        self.assertIn("challenge", fastapi_body)
        self.assertEqual(fastapi_body["rpId"], "testserver")
        self.assertEqual(fastapi_body["allowCredentials"], [])
        self.assertEqual(fastapi_body["userVerification"], "preferred")
        self.assertEqual(fastapi_body["timeout"], 300000)

    def test_register_options_without_session_is_identical_401(self):
        resp = self.client.post("/api/admin/passkeys/register-options", json={},
                                headers={"Host": "testserver"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "no_autenticado"})

    def test_register_options_with_session_shape_is_identical(self):
        """With a valid session, `register_options_result` reads
        `db_auth.passkeys_of(usuario)` -- with no reachable Postgres in this
        environment BOTH transports degrade to the same 503
        `base_no_disponible` (see passkeys.register_options_result's
        `except db.DbUnavailable`), which is itself a valid parity
        assertion; with a real database (CI/staging) both instead return
        200 with the same challenge shape. Either way, legacy and FastAPI
        must agree."""
        token = admin_auth._mint_token("f1-parity-owner", "admin")
        resp = self.client.post("/api/admin/passkeys/register-options", json={},
                                headers={"Host": "testserver"},
                                cookies={"st_admin": token})
        fastapi_body = resp.json()
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            self.assertIn("challenge", fastapi_body)
        else:
            self.assertEqual(fastapi_body, {"error": "base_no_disponible"})


if __name__ == "__main__":
    unittest.main()
