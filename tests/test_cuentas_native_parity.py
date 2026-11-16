"""Native ASGI contract for the F6 cuentas family
(accounts_router.py / account_photo_router.py / ocr_dni_router.py) —
native-route migration campaign (docs/roadmap/native-migration-campaign-
20260826.md).

NATIVE-MIGRATION FINAL WAVE + HANDLER RETIREMENT (2026-08-27/30): the legacy
`handle(handler, method)` transports this file used to replay every request
against (the "dark rollback" half of each parity assertion) are deleted from
`accounts.py`/`account_photo.py`/`ocr_dni.py`. This suite no longer proves
legacy-vs-native EQUALITY; it pins the NATIVE (TestClient over the module's
own router) response absolutely — status, body and cache-control — with the
exact values the retired parity assertions proved were shared. Every
identity/token/payload here is synthetic and needs no live Postgres.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config  # noqa: E402

_ORIGINAL_ENV_PATH = config.ENV_PATH
config.ENV_PATH = ""

from app.auth import turnstile  # noqa: E402
turnstile.ENV_PATH = ""

from app.auth import mode as auth_mode  # noqa: E402
from app.cuentas import accounts, accounts_router  # noqa: E402
from app.cuentas import account_photo, account_photo_router  # noqa: E402
from app.cuentas import ocr_dni, ocr_dni_router  # noqa: E402
from app.core import deps  # noqa: E402


def _reset_shared_caches():
    """`auth.mode.current()` (used by `_test_auth_enabled`/`_effective_auth_
    mode`) has its own short-TTL in-process cache — leaving it populated
    after this module's tests would make a SIBLING test module (e.g.
    test_accounts.py's TestI18nWiring, which flips TURNOS_AUTH_MODE via env)
    see a stale value for up to that TTL when run in the same pytest
    session. Same reset test_accounts_jinja.py's own `_reset_caches()` does."""
    auth_mode._cache["v"] = None
    auth_mode._cache["t"] = 0.0
    config._reset_cache_para_tests()


def tearDownModule():
    config.ENV_PATH = _ORIGINAL_ENV_PATH
    _reset_shared_caches()


class TestAccountsRouterNative(unittest.TestCase):
    def setUp(self):
        _reset_shared_caches()
        accounts._REGISTERED.clear()
        accounts._LOGIN_ATTEMPTS.clear()
        app = FastAPI()
        deps.install_gate_handlers(app)  # the app's handlers (a body that does not decode)
        app.include_router(accounts_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        _reset_shared_caches()

    def test_config_shape_and_no_store(self):
        native = self.client.get("/api/account/config")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.headers.get("cache-control"), "no-store")
        self.assertEqual(native.json().keys(), {
            "turnstile_sitekey", "otp_registro_requerido", "auth_mode",
            "renaper_provider", "test_identity_provider",
            # Where to ask for help, in every mode (TASK-50).
            "ayuda_whatsapp", "ayuda_whatsapp_texto", "centro_principal"})

    def test_me_without_session_is_401(self):
        native = self.client.get("/api/account/me")
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json(), {"error": "no_autenticado"})
        self.assertEqual(native.headers.get("cache-control"), "no-store")

    def test_register_short_password_is_400(self):
        native = self.client.post(
            "/api/account/register", json={"dni": "30111222", "password": "short"})
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json()["error"],
                         "La contraseña tiene que tener al menos 8 caracteres.")

    def test_register_invalid_json_is_400(self):
        native = self.client.post(
            "/api/account/register", content=b"{not json",
            headers={"Content-Type": "application/json"})
        self.assertEqual(native.status_code, 400)
        self.assertTrue(native.json()["error"].startswith("JSON inválido"))

    def test_logout_without_cookie_clears_session_cookie(self):
        native = self.client.post("/api/account/logout")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json(), {"ok": True})
        self.assertIn("st_ses=", native.headers.get("set-cookie", ""))

    def test_otp_verify_missing_contact_is_400_no_db(self):
        native = self.client.post("/api/account/otp/verify",
                                  json={"proposito": "registro"})
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"ok": False})

    def test_reset_otp_short_password_is_400_no_db(self):
        native = self.client.post(
            "/api/account/reset_otp",
            json={"dni": "30111222", "code": "000000", "password": "short"})
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json()["error"],
                         "La contraseña tiene que tener al menos 8 caracteres.")

    def test_test_identities_gate_off_is_404(self):
        native = self.client.get("/api/account/test-identities")
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "test_auth_no_disponible"})

    def test_test_login_gate_off_is_404(self):
        native = self.client.post(
            "/api/account/test-login", json={"provider": "renaper", "dni": "30111222"})
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "test_auth_no_disponible"})

    def test_dependents_without_session_is_401_both_methods(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                if method == "GET":
                    native = self.client.get("/api/account/dependents")
                else:
                    native = self.client.post(
                        "/api/account/dependents", json={"dni": "30111222"})
                self.assertEqual(native.status_code, 401)
                self.assertEqual(native.json(), {"error": "no_autenticado"})


class TestAccountPhotoRouterNative(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_reset_shared_caches)  # registered first: runs once the env is back
        _env.set_env(self, ENABLED_MODULES="turnos")
        _reset_shared_caches()
        self._orig_user = account_photo._user
        account_photo._user = lambda handler: None  # no session
        app = FastAPI()
        deps.install_gate_handlers(app)  # the app's handlers (a body that does not decode)
        app.include_router(account_photo_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        account_photo._user = self._orig_user

    def test_no_session_is_401(self):
        native = self.client.get("/api/account/photo")
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json(), {"error": "no_autenticado"})


class TestOcrDniRouterNative(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_reset_shared_caches)  # registered first: runs once the env is back
        _env.set_env(self, ENABLED_MODULES="turnos")
        _reset_shared_caches()
        app = FastAPI()
        deps.install_gate_handlers(app)  # the app's handlers (a body that does not decode)
        app.include_router(ocr_dni_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        ocr_dni._HITS.clear()

    def test_invalid_image_is_400(self):
        native = self.client.post(
            "/api/ocr-dni", json={"lado": "frente", "imageDataUrl": "not-a-data-url"})
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"error": "imagen inválida"})

    def test_invalid_json_is_400(self):
        native = self.client.post(
            "/api/ocr-dni", content=b"{not json",
            headers={"Content-Type": "application/json"})
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"error": "JSON inválido"})


if __name__ == "__main__":
    unittest.main()
