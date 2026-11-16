"""Test municipal portal (cuentas/test_portal.py): the in-process gateway of
pinamar.humanrounds.org speaks the real gateway's protocol.

Proves: a person signs in with the password of the installation's list and
from then on every request reaches the app with that person's X-User-* headers
and the gateway's peer, so portal_identity.authenticate takes the DNI from the
validated CUIT exactly as on salud.pinamar.gob.ar; client-supplied identity
headers never pass; a forged, expired or absent session carries no identity;
the app never sees the portal's cookie; logout ends the identity; a write
still needs the exact Origin; the boot refuses a government origin, a short
secret, an empty list and the staff portal login.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_test_portal.py
"""

import json
import os
import sys
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from app.cuentas import portal_identity, test_portal
import _env  # noqa: E402

_ORIGIN = "https://testserver"
_SECRET = "s" * 40
_HASH = test_portal.hash_password("clave-de-prueba", iterations=1000)
_PEOPLE = [{"subject": "tp-laura", "cuit": "27990000013", "email": "laura@example.org",
            "name": "Laura Prueba", "password": _HASH}]
_ENV = {
    "TURNOS_PORTAL_IDENTITY_MODE": "test-portal-v1",
    "TURNOS_PATIENT_PORTAL_LOGIN": "1",
    "TURNOS_STAFF_PORTAL_LOGIN": "",
    "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": "[]",
    "TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "",
    "TURNOS_PORTAL_IDENTITY_BINDINGS": "",
    "TURNOS_PUBLIC_ORIGIN": _ORIGIN,
    "TURNOS_TEST_PORTAL_SECRET": _SECRET,
    "TURNOS_TEST_PORTAL_USERS": json.dumps(_PEOPLE),
    "TURNOS_BASIC_AUTH_USER": "",
    "TURNOS_BASIC_AUTH_PASS": "",
}


def _app():
    app = FastAPI()

    @app.api_route("/whoami", methods=["GET", "POST"])
    async def whoami(request: Request):
        identity = portal_identity.authenticate(request)
        return {
            "identity": identity and {"subject": identity.subject, "dni": identity.dni,
                                      "email": identity.email},
            "cookie": request.headers.get("cookie", ""),
            "roles": request.headers.get("x-user-roles", ""),
        }

    return app


class _Base(unittest.TestCase):
    def setUp(self):
        self.enterContext(_env.patched_env(_ENV))
        self.enterContext(patch.object(portal_identity.config, "dotenv_values", return_value={}))
        self.enterContext(patch.object(test_portal.config, "dotenv_values", return_value={}))
        self.client = self.enterContext(TestClient(
            test_portal.TestPortalGateway(_app()), base_url=_ORIGIN, follow_redirects=False))

    def login(self, password="clave-de-prueba", email="Laura@Example.org", nxt="/reservar?x=1"):
        return self.client.post("/test-portal/login",
                                data={"email": email, "password": password, "next": nxt})

    def whoami(self, **kwargs):
        return self.client.get("/whoami", **kwargs).json()


class TestSignIn(_Base):
    def test_signed_in_person_reaches_the_app_with_the_cuit_dni(self):
        self.assertIsNone(self.whoami()["identity"])
        response = self.login()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/reservar?x=1")
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(self.whoami()["identity"],
                         {"subject": "tp-laura", "dni": "99000001", "email": "laura@example.org"})

    def test_wrong_password_or_unknown_email_gets_no_session(self):
        for response in (self.login(password="otra"), self.login(email="nadie@example.org")):
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("set-cookie", response.headers)
            self.assertIn("no coinciden", response.text)
        self.assertIsNone(self.whoami()["identity"])

    def test_login_page_returns_to_the_page_that_sent_the_person(self):
        page = self.client.get("/test-portal/login",
                               headers={"referer": _ORIGIN + "/account?from=wizard"})
        self.assertEqual(page.status_code, 200)
        self.assertIn('value="/account?from=wizard"', page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        other = self.client.get("/test-portal/login", headers={"referer": "https://evil.example/x"})
        self.assertIn('name="next" value="/"', other.text)

    def test_next_never_leaves_the_site(self):
        for bad in ("//evil.example/", "https://evil.example/", "/\\evil", "/test-portal/logout"):
            response = self.login(nxt=bad)
            self.assertEqual(response.headers["location"], "/", bad)

    def test_logout_ends_the_identity(self):
        self.login()
        self.assertIsNotNone(self.whoami()["identity"])
        response = self.client.get("/test-portal/logout")
        self.assertEqual(response.status_code, 303)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertIsNone(self.whoami()["identity"])

    def test_signed_in_page_names_the_person(self):
        self.login()
        page = self.client.get("/test-portal/login")
        self.assertIn("Laura Prueba", page.text)
        self.assertIn("27-99000001-3", page.text)


class TestSiteBasicAuth(_Base):
    """pinamar.humanrounds.org sits behind the site's Basic Auth; the portal's
    pages are outside the app, so the gateway asks for it itself (2026-10-26)."""

    def setUp(self):
        super().setUp()
        self.enterContext(_env.patched_env({"TURNOS_BASIC_AUTH_USER": "prueba",
                                            "TURNOS_BASIC_AUTH_PASS": "faro-duna-1234"}))

    def test_portal_pages_ask_for_the_site_credential(self):
        for response in (self.client.get("/test-portal/login"),
                         self.client.get("/test-portal/logout"),
                         self.login(),
                         self.client.get("/test-portal/login", auth=("prueba", "otra"))):
            self.assertEqual(response.status_code, 401)
            self.assertIn("Basic realm=", response.headers["www-authenticate"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("set-cookie", response.headers)

    def test_with_the_site_credential_the_portal_works(self):
        auth = ("prueba", "faro-duna-1234")
        self.assertEqual(self.client.get("/test-portal/login", auth=auth).status_code, 200)
        response = self.client.post("/test-portal/login", auth=auth, data={
            "email": "laura@example.org", "password": "clave-de-prueba", "next": "/"})
        self.assertEqual(response.status_code, 303)
        self.assertIn("hr_test_portal=", response.headers["set-cookie"])


class TestBoundary(_Base):
    def test_client_identity_headers_never_pass(self):
        forged = {"X-User-Id": "tp-laura", "X-User-Cuit": "27990000013",
                  "X-User-Email": "laura@example.org", "X-User-Roles": "admin"}
        body = self.whoami(headers=forged)
        self.assertIsNone(body["identity"])
        self.assertEqual(body["roles"], "")
        self.login()
        body = self.whoami(headers={"X-User-Cuit": "20123456788", "X-User-Roles": "admin"})
        self.assertEqual(body["identity"]["dni"], "99000001")
        self.assertEqual(body["roles"], "")

    def test_forged_or_expired_session_carries_no_identity(self):
        expired = test_portal._sign(_SECRET.encode(), "tp-laura", int(time.time()) - 1)
        other_key = test_portal._sign(b"x" * 40, "tp-laura", int(time.time()) + 60)
        for value in (expired, other_key, "garbage", ""):
            self.client.cookies.set("hr_test_portal", value)
            self.assertIsNone(self.whoami()["identity"], value)

    def test_the_app_never_sees_the_portal_cookie(self):
        self.login()
        self.client.cookies.set("lang", "es")
        body = self.whoami()
        self.assertNotIn("hr_test_portal", body["cookie"])
        self.assertIn("lang=es", body["cookie"])

    def test_a_write_still_needs_the_exact_origin(self):
        self.login()
        ok = self.client.post("/whoami", headers={"origin": _ORIGIN}).json()
        self.assertIsNotNone(ok["identity"])
        for origin in ("https://evil.example", None):
            headers = {"origin": origin} if origin else {}
            self.assertIsNone(self.client.post("/whoami", headers=headers).json()["identity"])

    def test_names_shared_with_portal_identity_stay_in_step(self):
        self.assertEqual(portal_identity._TEST_MODE, test_portal.MODE)
        self.assertEqual(portal_identity._TEST_PEER, test_portal.TEST_PEER)
        self.assertEqual(portal_identity._PEER, test_portal._PEER_KEY)

    def test_real_gateway_mode_never_trusts_the_test_peer(self):
        with _env.patched_env({"TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1",
                                     "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": json.dumps(["fd12::10"])}):
            self.login()
            self.assertIsNone(self.whoami()["identity"])


class TestBoot(_Base):
    def test_valid_configuration_boots(self):
        test_portal.validate_configuration()
        portal_identity.validate_configuration()
        portal_identity.validate_patient_login()

    def test_refusals(self):
        cases = [
            {"TURNOS_PUBLIC_ORIGIN": "https://salud.pinamar.gob.ar"},
            {"TURNOS_TEST_PORTAL_SECRET": "short"},
            {"TURNOS_TEST_PORTAL_USERS": "[]"},
            {"TURNOS_TEST_PORTAL_USERS": json.dumps([dict(_PEOPLE[0], cuit="2799")])},
            {"TURNOS_TEST_PORTAL_USERS": json.dumps([dict(_PEOPLE[0], password="plain")])},
            {"TURNOS_TEST_PORTAL_USERS": json.dumps(_PEOPLE * 2)},
            {"TURNOS_STAFF_PORTAL_LOGIN": "1"},
            {"TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1"},
        ]
        for extra in cases:
            with self.subTest(extra=extra), _env.patched_env(extra):
                with self.assertRaises(test_portal.ConfigurationError):
                    test_portal.validate_configuration()

    def test_mode_comes_from_the_process_environment_only(self):
        with _env.patched_env({"TURNOS_PORTAL_IDENTITY_MODE": ""}), \
                patch.object(test_portal.config, "dotenv_values",
                             return_value={"TURNOS_PORTAL_IDENTITY_MODE": "test-portal-v1"}):
            self.assertFalse(test_portal.enabled())


if __name__ == "__main__":
    unittest.main()
