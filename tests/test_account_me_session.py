"""Native account bootstrap distinguishes unavailable storage from missing auth.

All identities and cookies here are fixtures; no database or provider is used.
"""

import os
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.cuentas import accounts, accounts_router  # noqa: E402


class TestAccountMeSession(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(accounts_router.router)
        self.client = self.enterContext(TestClient(app))
        self.store = self.enterContext(patch.object(accounts.store, "get_session"))

    def test_no_cookie_is_unauthenticated_without_storage_lookup(self):
        response = self.client.get("/api/account/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "no_autenticado"})
        self.store.assert_not_called()

    def test_portal_identity_headers_alone_do_not_authenticate(self):
        response = self.client.get("/api/account/me", headers={
            "X-User-Id": "portal-fixture",
            "X-User-Email": "fixture@example.invalid",
            "X-User-Roles": "admin",
            "X-User-Cuit": "20000000001",
        })
        self.assertEqual(response.status_code, 401)
        self.store.assert_not_called()

    def test_unknown_cookie_remains_unauthenticated(self):
        self.store.return_value = None
        response = self.client.get("/api/account/me", headers={
            "Cookie": "st_ses=unknown-fixture",
        })
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "no_autenticado"})
        self.store.assert_called_once_with("unknown-fixture")

    def test_valid_native_session_returns_account_without_password_hash(self):
        self.store.return_value = {"usuario": {
            "dni": "12345678", "nombre": "Fixture", "pass_hash": "redacted",
        }}
        response = self.client.get("/api/account/me", headers={
            "Cookie": "st_ses=valid-fixture",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"usuario": {
            "dni": "12345678", "nombre": "Fixture",
        }})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", response.headers)

    def test_storage_outage_is_503_without_exposing_details_or_clearing_cookie(self):
        self.store.side_effect = accounts.store.DbUnavailable("private connection detail")
        response = self.client.get("/api/account/me", headers={
            "Cookie": "st_ses=valid-fixture",
        })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "base de datos no disponible"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", response.headers)

    def test_other_current_user_callers_still_fail_closed_during_outage(self):
        self.store.side_effect = accounts.store.DbUnavailable("unavailable")
        request = Request({
            "type": "http", "headers": [(b"cookie", b"st_ses=valid-fixture")],
        })
        self.assertIsNone(accounts.current_user(request))
