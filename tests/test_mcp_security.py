"""Security audit of the patient MCP connector (/mcp, /api/account/mcp).

The connector is the patient's own door for an AI assistant: it may do what the
patient can do in the site, for that patient only, and the token that opens it
is issued only after the patient's own login. Each test pins one claim:

  1 parity       a tool is an action of the token's owner, never more
  2 issuing      only after the person's own sign-in, only for themselves
  3 every call   the token is the identity; fields of the request never are
  4 transport    no token in a URL or a log, no CORS, only the hash is stored
  5 portal       where the portal is the only way in (salud), no token without it

Real app composition behind `SocketPeerApp` (as test_patient_portal.py), real
Postgres. Run:
  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_mcp_security.py
"""

import hashlib
import json
import logging
import os
import sys
import unittest
import _lifespan

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest.mock import patch  # noqa: E402

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.auth import mcp_guard  # noqa: E402
from app.modules.chat import mcp_api  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_mcp_security")
_REAL_CONNECTION_CLOSED = mcp_api.connection_closed
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
_PORTAL_ENV = {
    "TURNOS_PATIENT_PORTAL_LOGIN": "1",
    "TURNOS_STAFF_PORTAL_LOGIN": "",
    "TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1",
    "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": json.dumps([_PEER]),
    "TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "",
    "TURNOS_PORTAL_IDENTITY_BINDINGS": "",
    "TURNOS_PUBLIC_ORIGIN": _ORIGIN,
    "TURNOS_PORTAL_LOGIN_URL": "https://portal.example.invalid/login",
}
_PASSWORD_ENV = {
    "TURNOS_PATIENT_PORTAL_LOGIN": "",
    "TURNOS_STAFF_PORTAL_LOGIN": "",
    "TURNOS_PORTAL_IDENTITY_MODE": "off",
    "TURNOS_PORTAL_IDENTITY_BINDINGS": "",
    "TURNOS_PUBLIC_ORIGIN": _ORIGIN,
}


def _portal(cuit, subject):
    return {"X-User-Id": subject, "X-User-Cuit": cuit, "X-User-Email": subject + "@correo.example"}


ANA = _portal("20-30111222-5", "portal-ana")      # DNI 30111222
BETO = _portal("20-28999888-1", "portal-beto")    # DNI 28999888
ANA_DNI, BETO_DNI = "30111222", "28999888"


def _rpc(client, method, params=None, headers=None, url="/mcp"):
    msg = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        msg["params"] = params
    return client.post(url, json=msg, headers=headers or {})


def _call(client, token, name, arguments=None):
    return _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}},
                headers={"Authorization": f"Bearer {token}"})


def _payload(response):
    return json.loads(response.json()["result"]["content"][0]["text"])


class _Base(unittest.TestCase):
    ENV = _PORTAL_ENV
    CLIENT_PEER = (_PEER, 50000)

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de test")
        self.enterContext(_env.patched_env(self.ENV))
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        # Since 73af054f5 the municipal gateway installation issues no assistant token at all
        # (test_mcp_oauth pins that); these tests audit the token path itself, which the
        # test-portal installation (pinamar) and tokens issued earlier still use.
        self.enterContext(patch.object(mcp_api, "connection_closed", return_value=False))
        mcp_guard._reset_for_tests()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sessions")
            cur.execute("DELETE FROM appointment_events")
            cur.execute("DELETE FROM booking_requests")
            cur.execute("DELETE FROM users")
        from app import main as app_module
        from app.cuentas import portal_identity
        from fastapi.testclient import TestClient
        self.app = portal_identity.SocketPeerApp(app_module.app)
        self.client = self.enterContext(TestClient(self.app, client=self.CLIENT_PEER,
                                                   follow_redirects=False))

    def issue(self, headers):
        """The person's own «Mi cuenta» sign-in, then the token it shows once."""
        self.assertEqual(self.client.get("/api/account/me", headers=headers).status_code, 200)
        r = self.client.post("/api/account/mcp/regenerate",
                             headers={**headers, "Origin": _ORIGIN})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["token"]

    def make_request(self, dni, nombre="Nombre"):
        from app.core import db_booking
        return db_booking.create_booking_request(
            dni, nombre, "Apellido", "1990-01-01", None, "servicio-x", "centro-x")


class TestIssuingNeedsTheOwnSignIn(_Base):
    """2 + 5: a token exists only after the person signed in, for themselves."""

    def test_no_token_without_the_portal_even_with_a_native_session_cookie(self):
        db_accounts.create_user(ANA_DNI, "Ana", "Pérez", None, None, None, "x" * 12)
        self.client.cookies.set("st_ses", db_accounts.create_session(ANA_DNI))
        for method, path in (("get", "/api/account/mcp"), ("post", "/api/account/mcp/regenerate")):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT mcp_token FROM users WHERE dni=%s", (ANA_DNI,))
            self.assertIsNone(cur.fetchone()["mcp_token"])

    def test_anonymous_gets_nothing(self):
        for method, path in (("get", "/api/account/mcp"), ("post", "/api/account/mcp/regenerate")):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401)

    def test_the_token_is_the_signed_in_persons_and_nobody_elses(self):
        token = self.issue(ANA)
        who = _payload(_call(self.client, token, "whoami"))
        self.assertEqual(who["dni"], ANA_DNI)

    def test_a_dni_or_username_in_the_request_cannot_aim_the_token_at_someone_else(self):
        beto_before = self.issue(BETO)
        self.assertEqual(self.client.get("/api/account/me", headers=ANA).status_code, 200)
        r = self.client.post(f"/api/account/mcp/regenerate?dni={BETO_DNI}&usuario=beto",
                             headers={**ANA, "Origin": _ORIGIN}, json={"dni": BETO_DNI, "user": "beto", "usuario": {"dni": BETO_DNI}})
        self.assertEqual(r.status_code, 200, r.text)
        ana_token = r.json()["token"]
        # Beto's token still opens Beto; the new one opens Ana.
        self.assertEqual(_payload(_call(self.client, beto_before, "whoami"))["dni"], BETO_DNI)
        self.assertEqual(_payload(_call(self.client, ana_token, "whoami"))["dni"], ANA_DNI)

    def test_a_forged_gateway_header_from_an_untrusted_peer_gets_nothing(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app, client=("203.0.113.9", 50000), follow_redirects=False) as outsider:
            r = outsider.post("/api/account/mcp/regenerate", headers=ANA)
            self.assertEqual(r.status_code, 401)

    def test_regenerating_kills_the_old_token(self):
        old = self.issue(ANA)
        new = self.issue(ANA)
        self.assertEqual(_rpc(self.client, "tools/call", {"name": "whoami"},
                              headers={"Authorization": f"Bearer {old}"}).status_code, 401)
        self.assertEqual(_payload(_call(self.client, new, "whoami"))["dni"], ANA_DNI)

    def test_the_token_is_shown_once_and_only_its_hash_is_stored(self):
        token = self.issue(ANA)
        shown_again = self.client.get("/api/account/mcp", headers=ANA).json()
        self.assertIsNone(shown_again["token"])
        self.assertEqual(shown_again["hint"], token[-4:])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT mcp_token, mcp_token_hint FROM users WHERE dni=%s", (ANA_DNI,))
            row = cur.fetchone()
        self.assertNotEqual(row["mcp_token"], token)
        self.assertEqual(row["mcp_token"], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(row["mcp_token_hint"], token[-4:])

    def test_a_cross_site_regenerate_is_refused_and_a_same_site_one_is_not(self):
        self.issue(ANA)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT mcp_token FROM users WHERE dni=%s", (ANA_DNI,))
            before = cur.fetchone()["mcp_token"]
        for hdrs in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"},
                     {"Origin": "null"}):
            with self.subTest(hdrs=hdrs):
                r = self.client.post("/api/account/mcp/regenerate", headers={**ANA, **hdrs})
                self.assertEqual(r.status_code, 403, r.text)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT mcp_token FROM users WHERE dni=%s", (ANA_DNI,))
            self.assertEqual(cur.fetchone()["mcp_token"], before)
        ok = self.client.post("/api/account/mcp/regenerate",
                              headers={**ANA, "Origin": _ORIGIN, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(ok.status_code, 200, ok.text)


class TestEveryCallIsTheTokenOwner(_Base):
    """1 + 3: parity and IDOR."""

    def setUp(self):
        super().setUp()
        self.ana = self.issue(ANA)
        self.beto = self.issue(BETO)
        self.beto_request = self.make_request(BETO_DNI, "Beto")
        self.ana_request = self.make_request(ANA_DNI, "Ana")

    def test_my_requests_lists_only_the_owners(self):
        mine = _payload(_call(self.client, self.ana, "my_requests"))["solicitudes"]
        self.assertEqual([r["id"] for r in mine], [self.ana_request])
        theirs = _payload(_call(self.client, self.beto, "my_requests"))["solicitudes"]
        self.assertEqual([r["id"] for r in theirs], [self.beto_request])

    def test_identity_fields_in_the_arguments_are_refused_not_obeyed(self):
        for tool, extra in (("whoami", {"dni": BETO_DNI}), ("my_requests", {"user": "beto"}),
                            ("cancel_request", {"id": self.beto_request, "dni": BETO_DNI}),
                            ("available_appointments", {"service_id": "x", "dni": BETO_DNI})):
            with self.subTest(tool=tool):
                res = _call(self.client, self.ana, tool, extra).json()["result"]
                self.assertTrue(res["isError"], res)
                self.assertIn("argumentos no permitidos", res["content"][0]["text"])

    def test_cancel_of_a_strangers_request_cancels_nothing_and_tells_nothing(self):
        out = _payload(_call(self.client, self.ana, "cancel_request", {"id": self.beto_request}))
        self.assertTrue(out["confirmation_required"])
        self.assertNotIn("Beto", json.dumps(out))
        from app.core import db_booking
        self.assertEqual(db_booking.get_booking_request(self.beto_request)["estado"], "pendiente")

    def test_a_request_page_of_a_stranger_is_not_open_to_ana(self):
        # The link the cancel tool hands out is only a link: the page itself
        # decides, with the same login as everywhere.
        r = self.client.get(f"/request/{self.beto_request}", headers=ANA)
        self.assertNotIn("Beto", r.text)

    def test_booking_over_mcp_only_proposes_and_never_writes_for_a_stranger(self):
        service = {"id": "servicio-x", "nombre": "Servicio X", "modo": "solicitud", "centro": "centro-x"}
        with patch("app.patient_web.site_catalog._service_by_id", return_value=service), \
                patch("app.patient_web.site_availability._offers_appointments", return_value=True):
            for patient in (None, BETO_DNI, "11111111"):
                with self.subTest(patient=patient):
                    args = {"service_id": "servicio-x"}
                    if patient:
                        args["patient_dni"] = patient
                    out = _payload(_call(self.client, self.ana, "request_appointment", args))
                    self.assertTrue(out.get("confirmation_required"), out)
                    self.assertNotIn("solicitud_id", out)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM booking_requests")
            self.assertEqual(cur.fetchone()["n"], 2)  # only the two the test made

    def test_lab_results_and_other_unlisted_tools_are_not_reachable(self):
        for name in ("my_lab_results", "search_doctors", "admin_anything", ""):
            with self.subTest(tool=name):
                res = _call(self.client, self.ana, name).json()
                body = json.dumps(res)
                self.assertNotIn("informes", body)
                if "result" in res:
                    self.assertTrue(res["result"]["isError"])

    def test_tools_list_advertises_only_what_a_call_can_use(self):
        advertised = {t["name"] for t in _rpc(self.client, "tools/list").json()["result"]["tools"]}
        self.assertEqual(advertised - set(mcp_guard.WEIGHTS), set())
        self.assertNotIn("my_lab_results", advertised)

    def test_a_staff_login_is_not_a_patient_token(self):
        # The connector resolves only a patient's `users` row: an operator's
        # username, password or session has no token to present.
        for bogus in ("admin", "operator-token", "Bearer", "0" * 64, "a" * 4000):
            with self.subTest(bogus=bogus[:12]):
                r = _rpc(self.client, "tools/call", {"name": "whoami"},
                         headers={"Authorization": f"Bearer {bogus}"})
                self.assertEqual(r.status_code, 401)

    def test_the_stored_hash_is_not_a_token(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT mcp_token FROM users WHERE dni=%s", (ANA_DNI,))
            digest = cur.fetchone()["mcp_token"]
        r = _rpc(self.client, "tools/call", {"name": "whoami"},
                 headers={"Authorization": f"Bearer {digest}"})
        self.assertEqual(r.status_code, 401)

    def test_a_disabled_account_token_stops_working(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET active=false WHERE dni=%s", (ANA_DNI,))
        r = _rpc(self.client, "tools/call", {"name": "whoami"},
                 headers={"Authorization": f"Bearer {self.ana}"})
        self.assertEqual(r.status_code, 401)

    def test_a_tool_failure_says_nothing_about_the_data(self):
        def boom(usuario, args, invocation):
            raise ValueError("secret dni 28999888 row 77")
        with patch.object(mcp_api, "_TOOL_FNS", {**mcp_api._TOOL_FNS, "whoami": boom}):
            res = _call(self.client, self.ana, "whoami")
        text = res.text
        self.assertNotIn("28999888", text)
        self.assertNotIn("secret", text)
        self.assertTrue(res.json()["result"]["isError"])

    def test_limits_are_per_token_and_input_is_bounded(self):
        with _env.patched_env(TURNOS_MCP_PER_MINUTE="3"):
            codes = [_call(self.client, self.ana, "whoami").status_code for _ in range(5)]
            self.assertEqual(codes[:3], [200, 200, 200])
            self.assertEqual(codes[3:], [429, 429])
            self.assertEqual(_call(self.client, self.beto, "whoami").status_code, 200)
        big = _call(self.client, self.beto, "search_services", {"query": "a" * 5000}).json()["result"]
        self.assertTrue(big["isError"])
        huge = self.client.post("/mcp", content=b"x" * (300 * 1024),
                                headers={"Authorization": f"Bearer {self.beto}"})
        self.assertEqual(huge.status_code, 413)


class TestTransport(_Base):
    """4: nothing in a URL or a log, no cross-origin door."""

    def setUp(self):
        super().setUp()
        self.ana = self.issue(ANA)

    def test_a_token_in_the_url_is_not_a_credential(self):
        for url in (f"/mcp?token={self.ana}", f"/mcp?access_token={self.ana}&api_key={self.ana}",
                    f"/mcp/{self.ana}"):
            with self.subTest(url=url):
                r = _rpc(self.client, "tools/call", {"name": "whoami"}, url=url)
                self.assertIn(r.status_code, (401, 404, 405))

    def test_no_cors_headers_and_no_preflight_door(self):
        pre = self.client.options("/mcp", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in pre.headers})
        self.assertNotEqual(pre.status_code, 200)
        r = _call(self.client, self.ana, "whoami")
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in r.headers})
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_the_token_is_never_logged(self):
        records = []

        class Grab(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        root = logging.getLogger()
        handler, old = Grab(level=logging.DEBUG), root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            _call(self.client, self.ana, "whoami")
            _call(self.client, self.ana, "search_services", {"query": "a" * 5000})
            _rpc(self.client, "tools/call", {"name": "whoami"},
                 headers={"Authorization": "Bearer not-a-token-xyz"})

            def boom(usuario, args, invocation):
                raise RuntimeError("fallo")
            with patch.object(mcp_api, "_TOOL_FNS", {**mcp_api._TOOL_FNS, "whoami": boom}):
                _call(self.client, self.ana, "whoami")
        finally:
            root.removeHandler(handler)
            root.setLevel(old)
        joined = "\n".join(records)
        self.assertNotIn(self.ana, joined)
        self.assertNotIn("not-a-token-xyz", joined)

    def test_the_api_responses_that_carry_a_token_are_not_cacheable(self):
        self.client.get("/api/account/me", headers=ANA)
        r = self.client.post("/api/account/mcp/regenerate", headers={**ANA, "Origin": _ORIGIN})
        self.assertEqual(r.headers["cache-control"], "no-store")


class TestPasswordMode(_Base):
    """Where patients still have their own password (pinamar test site, demo)."""
    ENV = _PASSWORD_ENV
    CLIENT_PEER = ("127.0.0.1", 50000)

    def test_a_token_needs_the_native_session_cookie(self):
        db_accounts.create_user(ANA_DNI, "Ana", "Pérez", None, None, None, "x" * 12)
        self.assertEqual(self.client.get("/api/account/mcp").status_code, 401)
        self.client.cookies.set("st_ses", db_accounts.create_session(ANA_DNI))
        r = self.client.post("/api/account/mcp/regenerate")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(_payload(_call(self.client, r.json()["token"], "whoami"))["dni"], ANA_DNI)

    def test_changing_the_password_kills_the_token(self):
        """A recovery after a break-in must not leave the intruder's token alive."""
        db_accounts.create_user(ANA_DNI, "Ana", "Pérez", None, None, None, "x" * 12)
        token = db_accounts.mcp_token_regenerate(ANA_DNI)
        self.assertIsNotNone(db_accounts.user_by_mcp_token(token))
        db_accounts.set_password(ANA_DNI, "otra-clave-larga-1")
        self.assertIsNone(db_accounts.user_by_mcp_token(token))
        self.assertEqual(_rpc(self.client, "tools/call", {"name": "whoami"},
                              headers={"Authorization": f"Bearer {token}"}).status_code, 401)
        # and she can connect again from Mi cuenta
        self.assertIsNotNone(db_accounts.mcp_token_issue(ANA_DNI)["token"])


class TestTokenLifetime(_Base):
    """Owner's security decision (2026-11-13): the token dies after 30 days
    WITHOUT USE (sliding), and dies at once on a password change, an unbound
    portal identity, an erased account or an explicit revoke. Logging out of
    the browser does not touch it (it is a separate connected app); «Mi cuenta»
    shows when it was last used and offers the revoke."""

    def _age(self, dni, *, created_days=0, used_days=None):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET mcp_token_at = now() - make_interval(days => %s),"
                " mcp_token_used = CASE WHEN %s::int IS NULL THEN NULL"
                " ELSE now() - make_interval(days => %s::int) END WHERE dni = %s",
                (created_days, used_days, used_days, dni))

    def _used(self, dni):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT mcp_token_used FROM users WHERE dni = %s", (dni,))
            return cur.fetchone()["mcp_token_used"]

    def _token(self):
        token = self.issue(ANA)
        return token

    def _status(self, token):
        return _call(self.client, token, "whoami").status_code

    def test_unused_for_31_days_is_dead(self):
        token = self._token()
        self._age(ANA_DNI, created_days=31)
        self.assertEqual(self._status(token), 401)
        self.assertIsNone(db_accounts.user_by_mcp_token(token))

    def test_a_token_never_used_lives_30_days_from_creation(self):
        token = self._token()
        self._age(ANA_DNI, created_days=29)
        self.assertEqual(self._status(token), 200)

    def test_use_slides_the_window(self):
        token = self._token()
        self._age(ANA_DNI, created_days=90, used_days=29)
        self.assertEqual(self._status(token), 200)
        self.assertLess((db_accounts_now() - self._used(ANA_DNI)).total_seconds(), 120)

    def test_a_token_from_a_build_without_the_clock_lives_and_gets_one(self):
        """Rolling deploy / rollback: an older build issues a token with no
        clock; it must not be cut off, its first use starts the clock."""
        token = self._token()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET mcp_token_at = NULL, mcp_token_used = NULL WHERE dni = %s",
                        (ANA_DNI,))
        self.assertEqual(self._status(token), 200)
        self.assertIsNotNone(self._used(ANA_DNI))

    def test_old_use_is_dead_even_if_created_long_ago(self):
        token = self._token()
        self._age(ANA_DNI, created_days=90, used_days=31)
        self.assertEqual(self._status(token), 401)

    def test_last_use_is_written_at_most_every_ten_minutes(self):
        token = self._token()
        self.assertEqual(self._status(token), 200)
        first = self._used(ANA_DNI)
        self.assertIsNotNone(first)
        self.assertEqual(self._status(token), 200)
        self.assertEqual(self._used(ANA_DNI), first)

    def test_account_screen_shows_the_last_use(self):
        token = self._token()
        r = self.client.get("/api/account/mcp", headers=ANA)
        self.assertIsNone(r.json()["last_used"])
        self.assertEqual(self._status(token), 200)
        r = self.client.get("/api/account/mcp", headers=ANA)
        self.assertTrue(r.json()["last_used"])

    def test_last_use_is_written_in_the_installation_timezone(self):
        """00:30 UTC on the 14th is 21:30 of the 13th in Buenos Aires: the
        screen must show the installation's day and hour, never the server's."""
        self._token()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET mcp_token_used = %s WHERE dni = %s",
                        ("2026-11-14 00:30:00+00", ANA_DNI))
        # A use stamped in the future is newer than any 30-day cut, so the token stays alive.
        got = self.client.get("/api/account/mcp", headers=ANA).json()
        self.assertEqual(got["last_used"], "2026-11-13T21:30:00-03:00")

    def test_an_expired_token_is_replaced_by_the_next_visit(self):
        self._token()
        self._age(ANA_DNI, created_days=40)
        r = self.client.get("/api/account/mcp", headers=ANA)
        self.assertTrue(r.json()["token"])

    def test_revoke_kills_the_token(self):
        token = self._token()
        r = self.client.post("/api/account/mcp/revoke", headers={**ANA, "Origin": _ORIGIN})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(token), 401)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT mcp_token, mcp_token_hint, mcp_token_used FROM users WHERE dni = %s",
                        (ANA_DNI,))
            row = cur.fetchone()
        self.assertEqual((row["mcp_token"], row["mcp_token_hint"], row["mcp_token_used"]),
                         (None, None, None))

    def test_revoke_is_only_the_own_token(self):
        ana, beto = self.issue(ANA), self.issue(BETO)
        r = self.client.post("/api/account/mcp/revoke", headers={**ANA, "Origin": _ORIGIN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._status(ana), 401)
        self.assertEqual(self._status(beto), 200)

    def test_revoke_needs_sign_in_and_a_same_site_origin(self):
        anon = self.client.post("/api/account/mcp/revoke", headers={"Origin": _ORIGIN})
        self.assertEqual(anon.status_code, 401)
        token = self._token()
        forged = self.client.post("/api/account/mcp/revoke",
                                  headers={**ANA, "Origin": "https://evil.example"})
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(self._status(token), 200)

    def test_a_portal_identity_that_stops_matching_kills_the_token(self):
        """The account of a portal id answers to ITS CUIT: when the portal now
        asserts another DNI for that id, the token of the old binding dies."""
        token = self._token()
        moved = {**ANA, "X-User-Cuit": BETO["X-User-Cuit"]}
        self.assertEqual(self.client.get("/api/account/me", headers=moved).status_code, 401)
        self.assertEqual(self._status(token), 401)


class TestClosedInstallationAnswersNothing(_Base):
    """5: where the assistant connection is closed (salud: patients enter through the
    municipal portal) the application itself refuses the whole /mcp, as if the path did
    not exist: no handshake, no tool list, and a token issued before the closure is dead."""

    def setUp(self):
        super().setUp()
        self.ana = self.issue(ANA)
        self.make_request(ANA_DNI, "Ana")

    def _closed(self):
        self.assertTrue(_REAL_CONNECTION_CLOSED())
        return patch.object(mcp_api, "connection_closed", _REAL_CONNECTION_CLOSED)

    def test_a_token_issued_before_the_closure_calls_nothing(self):
        self.assertEqual(_call(self.client, self.ana, "whoami").status_code, 200)
        with self._closed():
            for tool in ("whoami", "my_requests"):
                r = _call(self.client, self.ana, tool)
                self.assertNotIn(ANA_DNI, r.text)
                self._absent(r, "POST", {"Authorization": f"Bearer {self.ana}"})

    def _absent(self, response, verb, headers):
        """The answer of /mcp is the answer of a path that never existed, byte for byte."""
        absent = self.client.request(verb, "/mcp-absent-probe", headers=headers)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual((response.content, response.headers.get("content-type")),
                         (absent.content, absent.headers.get("content-type")), verb)

    def test_handshake_and_tool_list_are_refused_with_or_without_a_token(self):
        bearer = {"Authorization": f"Bearer {self.ana}"}
        with self._closed():
            for headers in ({}, bearer):
                for method, params in (("initialize", {"protocolVersion": "2025-06-18"}),
                                       ("tools/list", None), ("ping", None)):
                    for url in ("/mcp", "/mcp/"):
                        r = _rpc(self.client, method, params, headers=headers, url=url)
                        self._absent(r, "POST", headers)
                for verb in ("GET", "HEAD", "DELETE", "PUT"):
                    self._absent(self.client.request(verb, "/mcp", headers=headers), verb, headers)

    def test_the_test_portal_and_password_installations_keep_the_connection(self):
        for env in ({"TURNOS_PORTAL_IDENTITY_MODE": "test-portal-v1"}, _PASSWORD_ENV):
            with _env.patched_env(env), \
                    patch.object(mcp_api, "connection_closed", _REAL_CONNECTION_CLOSED):
                config._reset_cache_para_tests()
                self.assertFalse(_REAL_CONNECTION_CLOSED())
                self.assertEqual(_rpc(self.client, "tools/list").status_code, 200)
                self.assertEqual(_rpc(self.client, "initialize", {}).status_code, 200)
                self.assertEqual(_payload(_call(self.client, self.ana, "whoami"))["dni"], ANA_DNI)
        config._reset_cache_para_tests()


def db_accounts_now():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT now() AS n")
        return cur.fetchone()["n"]


if __name__ == "__main__":
    unittest.main()
