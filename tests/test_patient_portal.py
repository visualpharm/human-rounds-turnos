"""Patient login through the municipal portal (TURNOS_PATIENT_PORTAL_LOGIN).

Real app composition behind `SocketPeerApp`, synthetic gateway peer, real
Postgres for the accounts. Proves: the first portal request finds or creates
the account by the DNI of the validated CUIT and binds the portal account id;
later requests go by that id alone; the refusals (an organization's CUIT, a
disabled account, an account already bound to another portal account); the
bindings list as a pilot limiter; the portal email; the closed native
entries; the screens told to show the portal instead of the forms; and the
boot check. Without the flag nothing is ever provisioned.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_patient_portal.py
"""

import json
import os
import sys
import unittest
import _lifespan

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_patient_portal")
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    _lifespan.no_background_services()
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_PEER = "fd12::10"
_ORIGIN = "https://salud.example.invalid"
_ENV = {
    "TURNOS_PATIENT_PORTAL_LOGIN": "1",
    "TURNOS_STAFF_PORTAL_LOGIN": "",
    "TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1",
    "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": json.dumps([_PEER]),
    "TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "",
    "TURNOS_PORTAL_IDENTITY_BINDINGS": "",
    "TURNOS_PUBLIC_ORIGIN": _ORIGIN,
    "TURNOS_PORTAL_LOGIN_URL": "https://portal.example.invalid/login",
}


def _portal(cuit="20-30111222-5", subject="portal-ana", email="ana@correo.example", **extra):
    headers = {"X-User-Id": subject, "X-User-Cuit": cuit, "X-User-Email": email,
               "X-User-Roles": "admin"}
    headers.update(extra)
    return headers


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de test")
        self.enterContext(_env.patched_env(_ENV))
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sessions")
            cur.execute("DELETE FROM users")
        from app import main as app_module
        from app.cuentas import portal_identity
        from fastapi.testclient import TestClient
        self.app = portal_identity.SocketPeerApp(app_module.app)
        self.client = self.enterContext(TestClient(self.app, client=(_PEER, 50000),
                                                   follow_redirects=False))

    def me(self, headers):
        return self.client.get("/api/account/me", headers=headers)

    def users(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT dni, email, portal_user_id, active, pass_hash FROM users ORDER BY id")
            return [dict(r) for r in cur.fetchall()]


class TestProvisioning(_Base):
    def test_first_request_creates_the_account_from_the_cuit_then_the_portal_id_decides(self):
        response = self.me(_portal())
        self.assertEqual(response.status_code, 200, response.text)
        user = response.json()["usuario"]
        self.assertEqual(user["dni"], "30111222")
        self.assertEqual(user["email"], "ana@correo.example")
        self.assertNotIn("portal_user_id", user)
        self.assertNotIn("set-cookie", response.headers)
        rows = self.users()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["portal_user_id"], "portal-ana")
        self.assertTrue(rows[0]["active"])
        self.assertIsNone(rows[0]["pass_hash"])
        # Later requests: the portal account id alone finds her, even with a
        # new portal email; nothing is created twice.
        again = self.me(_portal(email="ana.nueva@correo.example"))
        self.assertEqual(again.json()["usuario"]["dni"], "30111222")
        self.assertEqual(len(self.users()), 1)
        self.assertEqual(self.users()[0]["email"], "ana@correo.example")

    def test_existing_account_with_that_dni_is_bound_and_keeps_its_email(self):
        db_accounts.create_user("30111222", "Ana", "Pérez", None, None, "+5492254000000",
                       "x" * 12, email="propio@correo.example")
        response = self.me(_portal())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["usuario"]["nombre"], "Ana")
        self.assertEqual(response.json()["usuario"]["email"], "propio@correo.example")
        self.assertEqual(self.users()[0]["portal_user_id"], "portal-ana")

    def test_existing_account_without_email_gets_the_portal_one(self):
        db_accounts.create_user("30111222", "Ana", "Pérez", None, None, None, "x" * 12)
        self.assertEqual(self.me(_portal()).json()["usuario"]["email"], "ana@correo.example")

    def test_seven_digit_dni_comes_without_the_leading_zero(self):
        self.assertEqual(self.me(_portal(cuit="27-01234567-3")).json()["usuario"]["dni"], "1234567")

    def test_cuit_of_a_company_or_organization_is_refused(self):
        for cuit in ("30-71234567-1", "33-71234567-9", "34-71234567-4", "99-30111222-5"):
            with self.subTest(cuit=cuit):
                self.assertEqual(self.me(_portal(cuit=cuit)).status_code, 401)
        self.assertEqual(self.users(), [])

    def test_missing_or_malformed_cuit_is_refused(self):
        for cuit in ("", "123", "20 30111222 5"):
            with self.subTest(cuit=cuit):
                self.assertEqual(self.me(_portal(cuit=cuit)).status_code, 401)
        # The gateway contract says the header is never absent in practice
        # (the portal stops an unvalidated person itself) — fail closed anyway.
        without_header = {k: v for k, v in _portal().items() if k != "X-User-Cuit"}
        self.assertEqual(self.me(without_header).status_code, 401)
        self.assertEqual(self.users(), [])

    def test_disabled_account_is_refused_bound_or_not(self):
        db_accounts.create_user("30111222", "Ana", "Pérez", None, None, None, "x" * 12)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET active=false WHERE dni='30111222'")
        self.assertEqual(self.me(_portal()).status_code, 401)
        self.assertIsNone(self.users()[0]["portal_user_id"])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET active=true WHERE dni='30111222'")
        self.assertEqual(self.me(_portal()).status_code, 200)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET active=false WHERE dni='30111222'")
        self.assertEqual(self.me(_portal()).status_code, 401)

    def test_account_bound_to_another_portal_account_is_not_rebound(self):
        self.assertEqual(self.me(_portal()).status_code, 200)
        intruder = _portal(subject="portal-otra")  # same CUIT, another portal account
        self.assertEqual(self.me(intruder).status_code, 401)
        self.assertEqual(self.users()[0]["portal_user_id"], "portal-ana")
        self.assertEqual(len(self.users()), 1)

    def test_a_bound_account_whose_dni_no_longer_matches_the_cuit_is_refused(self):
        self.assertEqual(self.me(_portal()).status_code, 200)
        self.assertEqual(self.me(_portal(cuit="27-28999888-1")).status_code, 401)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET dni='28999888' WHERE dni='30111222'")
        self.assertEqual(self.me(_portal()).status_code, 401)

    def test_concurrent_first_requests_all_open_the_same_account(self):
        from concurrent.futures import ThreadPoolExecutor
        from fastapi.testclient import TestClient

        def first_request(_):
            # One client per thread: each is its own browser tab.
            client = TestClient(self.app, client=(_PEER, 50000))
            try:
                return client.get("/api/account/me", headers=_portal()).status_code
            finally:
                client.close()
        with ThreadPoolExecutor(6) as pool:
            codes = list(pool.map(first_request, range(6)))
        self.assertEqual(codes, [200] * 6)
        self.assertEqual(len(self.users()), 1)

    def test_native_cookie_never_opens_an_account_here(self):
        db_accounts.create_user("30111222", "Ana", "Pérez", None, None, None, "x" * 12)
        token = db_accounts.create_session("30111222")
        self.client.cookies.set("st_ses", token)
        self.assertEqual(self.client.get("/api/account/me").status_code, 401)


class TestPilotBindings(_Base):
    def test_a_bindings_list_admits_only_the_people_on_it(self):
        bindings = json.dumps([{"subject": "portal-ana", "cuit": "20301112225", "dni": "30111222"}])
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_BINDINGS=bindings):
            self.assertEqual(self.me(_portal()).status_code, 200)
            self.assertEqual(self.me(_portal(subject="portal-beto", cuit="20-28999888-1")).status_code, 401)
            # A bound subject with another CUIT is refused too.
            self.assertEqual(self.me(_portal(cuit="20-28999888-1")).status_code, 401)
        self.assertEqual([r["dni"] for r in self.users()], ["30111222"])


class TestWithoutTheFlag(_Base):
    def test_portal_mode_alone_never_provisions(self):
        bindings = json.dumps([{"subject": "portal-ana", "cuit": "20301112225", "dni": "30111222"}])
        with _env.patched_env(TURNOS_PATIENT_PORTAL_LOGIN="",
                        TURNOS_PORTAL_IDENTITY_BINDINGS=bindings):
            self.assertEqual(self.me(_portal()).status_code, 401)
            self.assertEqual(self.users(), [])
            config_ = self.client.get("/api/account/config").json()
            self.assertNotEqual(config_["auth_mode"], "portal")

    def test_boot_refuses_the_flag_without_the_portal_identity_mode(self):
        from app.cuentas import portal_identity
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_MODE="off"):
            with self.assertRaises(portal_identity.ConfigurationError):
                portal_identity.validate_patient_login()
        portal_identity.validate_patient_login()
        portal_identity.validate_configuration()  # empty bindings are valid with the flag


class TestScreensAndNativeEntries(_Base):
    def test_config_tells_the_screens_to_show_the_portal(self):
        anonymous = self.client.get("/api/account/config").json()
        self.assertEqual(anonymous["auth_mode"], "portal")
        self.assertEqual(anonymous["portal_login_url"], "https://portal.example.invalid/login")
        self.assertFalse(anonymous["portal_identidad"])
        refused = self.client.get("/api/account/config", headers=_portal(cuit="30-71234567-1")).json()
        self.assertTrue(refused["portal_identidad"])
        # The account page's heading is right on first paint, never «Crear cuenta».
        page = self.client.get("/account").text
        self.assertIn('id="cuenta-title">Ingresar<', page)
        self.assertNotIn("Crear cuenta", page)
        page = self.client.get("/account", headers=_portal(cuit="30-71234567-1")).text
        self.assertIn("No pudimos abrir tu cuenta", page)

    def test_password_login_registration_and_recovery_are_closed(self):
        db_accounts.create_user("30111222", "Ana", "Pérez", None, None, None, "x" * 12)
        headers = {"Origin": _ORIGIN}
        for path, body in (("/api/account/login", {"identifier": "30111222", "password": "x" * 12}),
                           ("/api/account/register", {"dni": "28999888", "password": "y" * 12}),
                           ("/api/account/recover", {"identifier": "30111222"}),
                           ("/api/account/reset", {"token": "t", "password": "y" * 12}),
                           ("/api/account/reset_otp", {"contacto": "x", "code": "1", "password": "y" * 12})):
            with self.subTest(path=path):
                response = self.client.post(path, headers=headers, json=body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["portal_login_url"],
                                 "https://portal.example.invalid/login")
                self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(len(self.users()), 1)
        reset_page = self.client.get("/account/reset?token=x")
        self.assertEqual(reset_page.status_code, 302)
        self.assertEqual(reset_page.headers["location"], "/account")

    def test_name_and_phone_entered_at_first_booking_are_saved(self):
        self.assertEqual(self.me(_portal()).status_code, 200)
        response = self.client.post(
            "/api/account/update", headers=dict(_portal(), Origin=_ORIGIN),
            json={"nombre": "Ana", "apellido": "Pérez", "contacto": "2254 401234"})
        self.assertEqual(response.status_code, 200, response.text)
        user = db_accounts.get_user("30111222")
        self.assertEqual((user["nombre"], user["apellido"]), ("Ana", "Pérez"))
        self.assertTrue(user["contacto"])


class TestPortalEntryReturnsToThePage(_Base):
    """«Ingresar» goes to the portal with the page to come back to (the product owner 26.10:
    the portal left him on its home page). The municipal portal reads `return`
    on /sso/login — an absolute https URL under .pinamar.gob.ar — and, when the
    person is already signed in there, sends them straight back."""

    _PORTAL = "https://portal.example.invalid/login"

    def entry(self, nxt=None, headers=None):
        params = {} if nxt is None else {"next": nxt}
        return self.client.get("/account/portal-login", params=params, headers=headers or {})

    def test_without_a_portal_session_it_goes_to_the_portal_with_the_page(self):
        response = self.entry("/service/electrocardiograma-hospital/book")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            self._PORTAL + "?return=https%3A%2F%2Fsalud.example.invalid"
                           "%2Fservice%2Felectrocardiograma-hospital%2Fbook")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_the_configured_query_stays_and_a_stale_return_is_replaced(self):
        with _env.patched_env({"TURNOS_PORTAL_LOGIN_URL":
                                     "https://portal.example.invalid/sso/login?app=salud&return=x"}):
            location = self.entry("/account?tab=turnos").headers["location"]
        self.assertEqual(
            location,
            "https://portal.example.invalid/sso/login?app=salud"
            "&return=https%3A%2F%2Fsalud.example.invalid%2Faccount%3Ftab%3Dturnos")

    def test_a_missing_or_foreign_next_returns_to_the_account(self):
        account = self._PORTAL + "?return=https%3A%2F%2Fsalud.example.invalid%2Faccount"
        for nxt in (None, "", "//evil.example/x", "https://evil.example/x", "/\\evil.example",
                    "account", "/account\nx", "/account/portal-login?next=/"):
            with self.subTest(nxt=nxt):
                self.assertEqual(self.entry(nxt).headers["location"], account)

    def test_a_person_the_gateway_vouched_for_goes_straight_to_the_page(self):
        response = self.entry("/my-requests", headers=_portal())
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/my-requests")
        # Refused (an organization's CUIT): still the page, which shows the
        # remedy — never another trip to the portal.
        refused = self.entry("/account", headers=_portal(cuit="30-71234567-1"))
        self.assertEqual(refused.headers["location"], "/account")

    def test_without_a_portal_url_or_the_portal_login_it_is_the_account_page(self):
        with _env.patched_env({"TURNOS_PORTAL_LOGIN_URL": ""}):
            self.assertEqual(self.entry("/my-requests").headers["location"], "/account")
        with _env.patched_env({"TURNOS_PATIENT_PORTAL_LOGIN": ""}):
            config._reset_cache_para_tests()
            self.assertEqual(self.entry("/my-requests").headers["location"], "/account")

    def test_the_test_portal_reads_the_same_return(self):
        from app.cuentas import test_portal
        from fastapi.testclient import TestClient
        from app import main as app_module
        people = [{"subject": "tp-laura", "cuit": "27990000013", "email": "laura@example.org",
                   "name": "Laura Prueba",
                   "password": test_portal.hash_password("clave", iterations=1000)}]
        self.enterContext(_env.patched_env({
            "TURNOS_PORTAL_IDENTITY_MODE": "test-portal-v1",
            "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": "[]",
            "TURNOS_TEST_PORTAL_SECRET": "k" * 40,
            "TURNOS_TEST_PORTAL_USERS": json.dumps(people),
            "TURNOS_PORTAL_LOGIN_URL": _ORIGIN + "/test-portal/login",
        }))
        client = self.enterContext(TestClient(test_portal.TestPortalGateway(app_module.app),
                                              base_url=_ORIGIN, follow_redirects=False))
        portal = client.get("/account/portal-login", params={"next": "/my-requests"})
        self.assertEqual(portal.headers["location"], _ORIGIN + "/test-portal/login"
                         "?return=https%3A%2F%2Fsalud.example.invalid%2Fmy-requests")
        form = client.get(portal.headers["location"])
        self.assertIn('name="next" value="/my-requests"', form.text)
        # Another site's address is never a way back.
        foreign = client.get("/test-portal/login",
                             params={"return": "https://evil.example/my-requests"})
        self.assertIn('name="next" value="/"', foreign.text)
        login = client.post("/test-portal/login", data={
            "email": "laura@example.org", "password": "clave", "next": "/my-requests"})
        self.assertEqual(login.headers["location"], "/my-requests")
        # Already signed in to the portal: straight back, no page in between
        # (what the municipal portal's /sso/login does).
        again = client.get(portal.headers["location"])
        self.assertEqual(again.status_code, 303)
        self.assertEqual(again.headers["location"], "/my-requests")
        # And our entry, now vouched for, does not leave the site at all.
        entry = client.get("/account/portal-login", params={"next": "/my-requests"})
        self.assertEqual(entry.headers["location"], "/my-requests")


class TestNameFromThePortal(_Base):
    """«Pacientes registrados sin nombre» (the reference-deployment counterpart, 30.10): the first portal
    request created the account from the CUIT alone, so /admin/pacientes
    showed «—» for everyone who signed in and never booked."""

    def names(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT first_name, last_name FROM users ORDER BY id")
            return [(r["first_name"], r["last_name"]) for r in cur.fetchall()]

    def test_root_without_a_name_in_the_assertion_the_account_is_born_nameless(self):
        # The assertion carried id, CUIT, email and roles: nothing to name her by.
        self.assertEqual(self.me(_portal()).status_code, 200)
        self.assertEqual(self.names(), [(None, None)])

    def test_given_and_family_name_create_the_account_with_its_name(self):
        user = self.me(_portal(**{"X-User-Given-Name": "Ana",
                                  "X-User-Family-Name": "P%C3%A9rez"})).json()["usuario"]
        self.assertEqual((user["nombre"], user["apellido"]), ("Ana", "Pérez"))
        self.assertEqual(self.names(), [("Ana", "Pérez")])

    def test_a_shouted_registry_name_is_stored_in_normal_capitalization(self):
        self.me(_portal(**{"X-User-Given-Name": "MARIA JOSE", "X-User-Family-Name": "GARCIA LOPEZ"}))
        self.assertEqual(self.names(), [("Maria Jose", "Garcia Lopez")])

    def test_one_full_name_header_splits_only_when_it_says_where(self):
        self.me(_portal(**{"X-User-Name": "P%C3%89REZ%2C%20ANA%20MAR%C3%8DA"}))
        self.assertEqual(self.names(), [("Ana María", "Pérez")])
        # No comma: nothing says where the surname starts, so it is not guessed;
        # the whole name is still shown (never «—») and Confirmá asks for the rest.
        self.me(_portal(cuit="27-30222333-4", subject="portal-bea", email="bea@correo.example",
                        **{"X-User-Name": "Beatriz G%C3%B3mez"}))
        self.assertEqual(self.names()[1], ("Beatriz Gómez", None))

    def test_utf8_bytes_sent_raw_by_the_gateway_are_read_as_utf8(self):
        self.me(_portal(**{"X-User-Given-Name": "Ana".encode(),
                           "X-User-Family-Name": "Pérez".encode("utf-8")}))
        self.assertEqual(self.names(), [("Ana", "Pérez")])

    def test_a_later_request_fills_a_missing_name_and_never_overwrites_a_saved_one(self):
        self.me(_portal())
        self.assertEqual(self.names(), [(None, None)])
        named = _portal(**{"X-User-Given-Name": "Ana", "X-User-Family-Name": "Pérez".encode()})
        self.me(named)
        self.assertEqual(self.names(), [("Ana", "Pérez")])
        # She corrects it herself: the portal's spelling does not come back over it.
        self.client.post("/api/account/update", headers=dict(_portal(), Origin=_ORIGIN),
                         json={"nombre": "Anita", "apellido": "Pérez", "email": "ana@correo.example"})
        self.me(named)
        self.assertEqual(self.names(), [("Anita", "Pérez")])

    def test_an_existing_account_without_a_name_is_named_when_it_is_bound(self):
        db_accounts.create_user("30111222", None, None, None, None, "+5492254000000", "x" * 12)
        self.me(_portal(**{"X-User-Given-Name": "Ana", "X-User-Family-Name": "Pérez".encode()}))
        self.assertEqual(self.names(), [("Ana", "Pérez")])

    def test_an_unreadable_name_never_decides_who_gets_in(self):
        for headers in ({"X-User-Name": "x" * 4000}, {"X-User-Given-Name": "%FF%FE"},
                        {"X-User-Given-Name": b"Ana\x01"}):
            with self.subTest(headers=list(headers.values())):
                self.assertEqual(self.me(_portal(**headers)).status_code, 200)
        self.assertEqual(self.names(), [(None, None)])


class TestBehindTheTestPortal(_Base):
    """pinamar.humanrounds.org: the same app behind cuentas/test_portal.py."""

    def test_a_test_person_signs_in_and_gets_the_account_of_the_cuit(self):
        from app.cuentas import test_portal
        from fastapi.testclient import TestClient
        from app import main as app_module
        people = [{"subject": "tp-laura", "cuit": "27990000013", "email": "laura@example.org",
                   "name": "Laura Prueba",
                   "password": test_portal.hash_password("clave", iterations=1000)}]
        self.enterContext(_env.patched_env({
            "TURNOS_PORTAL_IDENTITY_MODE": "test-portal-v1",
            "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": "[]",
            "TURNOS_TEST_PORTAL_SECRET": "k" * 40,
            "TURNOS_TEST_PORTAL_USERS": json.dumps(people),
        }))
        client = self.enterContext(TestClient(test_portal.TestPortalGateway(app_module.app),
                                              base_url=_ORIGIN, follow_redirects=False))
        self.assertEqual(client.get("/api/account/config").json()["auth_mode"], "portal")
        self.assertEqual(client.get("/api/account/me").status_code, 401)
        login = client.post("/test-portal/login", data={
            "email": "laura@example.org", "password": "clave", "next": "/account"})
        self.assertEqual(login.status_code, 303)
        user = client.get("/api/account/me").json()["usuario"]
        self.assertEqual(user["dni"], "99000001")
        self.assertEqual(user["email"], "laura@example.org")
        # The test portal knows her name and asserts it like the real one: no «—» in the panel.
        self.assertEqual(user["nombre"], "Laura Prueba")
        self.assertEqual(self.users()[0]["portal_user_id"], "tp-laura")
        # Our own password entry stays closed here too.
        self.assertEqual(client.post("/api/account/login", headers={"origin": _ORIGIN},
                                     json={"dni": "99000001", "password": "x"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
