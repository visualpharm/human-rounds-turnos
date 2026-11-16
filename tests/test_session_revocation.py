"""«Cerrar sesión» ends the session for good: a copy of the cookie made before signing out is refused.

Every kind of session the product hands out is covered here against the real app composition and a
real Postgres:

* the staff cookie `st_admin` (owner, admin, médico, admisionista, reportes - they share one cookie),
  including its renewal and the one a password change re-issues;
* the patient cookie `st_ses` (a server-side row, deleted by the logout);
* a staff member who signed in through the patient login screen (both cookies in one browser);
* an installation with the municipal portal login, where the cookie is not a session at all.

A "copy" is simulated the way an attacker holds it: the cookie value is sent by hand in a request that
shares nothing with the browser that signed out (the client's own jar is emptied before every call).
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as app_module  # noqa: E402
from app.auth import admin_auth, turnstile  # noqa: E402
from app.core import config, db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
import _dbtest  # noqa: E402
import _lifespan  # noqa: E402

turnstile.ENV_PATH = ""   # no leer el .env real del checkout (mismo truco que test_admin_users.py)
config.ENV_PATH = ""

_db = _dbtest.PerModuleDb("sana_turnos_test_session_revocation", seed=lambda: db_operators.operators_list())
HAVE_DB, DB_SKIP_REASON = False, None

_OWNER = "admin"
_OWNER_PASS = "owner-pass-revocation-1"
_OP_PASS = "operator-pass-1234"
_PATIENT_DNI = "29123456"
_PATIENT_PASS = "paciente-1234"
_CLIENT = TestClient(app_module.app, base_url="http://127.0.0.1:7700", follow_redirects=False,
                     client=("127.0.0.1", 54321))


def setUpModule():
    _lifespan.no_background_services()
    global HAVE_DB, DB_SKIP_REASON
    _env.set_env_for_module(
        TURNOS_ADMIN_USER=_OWNER, TURNOS_ADMIN_PASS=_OWNER_PASS, TURNOS_ADMIN_SECRET="revocation-key",
        TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="", TURNOS_CONFIG_LOCK="1",
        TURNOS_AUTH_MODE="password", TURNOS_STAFF_PORTAL_LOGIN=None)
    config._reset_cache_para_tests()
    unittest.addModuleCleanup(config._reset_cache_para_tests)
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _call(method, path, cookie=None, **kw):
    """One request that carries ONLY the cookie it is given (a copy held by somebody else)."""
    _CLIENT.cookies.clear()
    headers = dict(kw.pop("headers", None) or {})
    if cookie:
        headers["Cookie"] = cookie
    return _CLIENT.request(method, path, headers=headers, **kw)


def _cookie_of(response, name):
    """`name=value` taken from the response's Set-Cookie, or None when it did not set one."""
    for line in response.headers.get_list("set-cookie"):
        first = line.split(";", 1)[0]
        if first.startswith(name + "=") and len(first) > len(name) + 1:
            return first
    return None


def _staff_login(usuario, password):
    response = _call("POST", "/api/admin/login", json={"usuario": usuario, "password": password})
    assert response.status_code == 200, response.text
    return _cookie_of(response, "st_admin")


def _whoami(cookie):
    return _call("GET", "/api/admin/whoami", cookie)


def _screen(cookie):
    return _call("GET", "/admin/account", cookie, headers={"accept": "text/html"})


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE admin_account_security")
            cur.execute("TRUNCATE users, sessions CASCADE")
            cur.execute("TRUNCATE admin_session_revocations")
        admin_auth._ATTEMPTS.clear()


class TestStaffLogoutRevokes(_Base):
    def test_every_staff_role_refuses_the_copied_cookie_after_logout(self):
        roles = ("admin", "medico", "admisionista", "reportes", "gestor_agendas")
        for rol in roles:
            db_operators.operator_create("op-" + rol, _OP_PASS, rol=rol)
        accounts = [(_OWNER, _OWNER_PASS)] + [("op-" + rol, _OP_PASS) for rol in roles]
        for usuario, password in accounts:
            with self.subTest(usuario=usuario):
                copy = _staff_login(usuario, password)
                self.assertEqual(_whoami(copy).status_code, 200)
                self.assertEqual(_screen(copy).status_code, 200)

                out = _call("POST", "/api/admin/logout", copy)
                self.assertEqual(out.status_code, 200)
                self.assertIn("Max-Age=0", out.headers["set-cookie"])

                dead = _whoami(copy)
                self.assertEqual((dead.status_code, dead.json()), (401, {"error": "no_autenticado"}))
                screen = _screen(copy)
                self.assertEqual((screen.status_code, screen.headers["location"]),
                                 (303, "/admin/login?next=%2Fadmin%2Faccount"))

    def test_signing_out_one_device_leaves_the_other_devices_of_the_account(self):
        db_operators.operator_create("recepcion", _OP_PASS, rol="admisionista")
        desk_pc, other_pc = _staff_login("recepcion", _OP_PASS), _staff_login("recepcion", _OP_PASS)
        self.assertNotEqual(desk_pc, other_pc)
        _call("POST", "/api/admin/logout", desk_pc)
        self.assertEqual(_whoami(desk_pc).status_code, 401)
        self.assertEqual(_whoami(other_pc).status_code, 200)

    def test_a_cookie_that_was_not_signed_out_keeps_working(self):
        copy = _staff_login(_OWNER, _OWNER_PASS)
        for _ in range(3):
            self.assertEqual(_whoami(copy).status_code, 200)

    def test_logout_without_a_usable_cookie_is_still_a_clean_200(self):
        expired = admin_auth._itsdangerous_serializer().dumps(
            {"v": admin_auth.TOKEN_VERSION, "u": _OWNER, "r": "admin", "exp": int(time.time()) - 5, "nonce": "n"})
        for cookie in (None, "st_admin=", "st_admin=not-a-session", "st_admin=" + expired, "other=1"):
            with self.subTest(cookie=cookie):
                out = _call("POST", "/api/admin/logout", cookie)
                self.assertEqual((out.status_code, out.json()), (200, {"ok": True}))
                self.assertIn("Max-Age=0", out.headers["set-cookie"])


def _payload(**over):
    base = {"v": admin_auth.TOKEN_VERSION, "u": _OWNER, "r": "admin", "exp": int(time.time()) + 3600, "nonce": "n-old"}
    # The authenticator stamp is required since the staff-auth review (finding 9); the tests below
    # are about what is missing from a cookie OTHER than it (session id, password stamp).
    stamp = admin_auth._totp_stamp(base["u"])
    if stamp:
        base["ts"] = stamp
    base.update(over)
    return base


def _cookie_for(payload):
    return "st_admin=" + admin_auth._itsdangerous_serializer().dumps(payload)


class TestSessionLineage(_Base):
    """One session, however many times its cookie was renewed or re-issued."""

    def test_a_cookie_issued_before_session_ids_existed_is_closed_by_its_nonce(self):
        old = _cookie_for(_payload(nonce="nonce-of-a-cookie-from-before-the-change"))
        self.assertNotIn("sid", admin_auth._signed_payload(old.split("=", 1)[1]))
        self.assertEqual(_whoami(old).status_code, 200, "nobody is signed out by the deploy")
        _call("POST", "/api/admin/logout", old)
        self.assertEqual(_whoami(old).status_code, 401)

    def test_the_renewal_a_thief_made_before_the_logout_is_closed_too(self):
        # Half the life is gone: whoami re-issues the cookie (the panel does it on load).
        stale = _cookie_for(_payload(nonce="session-start", exp=int(time.time()) + 600))
        renewed_response = _whoami(stale)
        self.assertEqual(renewed_response.status_code, 200)
        renewed = _cookie_of(renewed_response, "st_admin")
        self.assertIsNotNone(renewed, "the cookie was renewed")
        self.assertNotEqual(renewed, stale)
        self.assertEqual(admin_auth.session_id_of(renewed.split("=", 1)[1]), "session-start")

        _call("POST", "/api/admin/logout", renewed)   # the victim signs out with the newest cookie
        self.assertEqual(_whoami(renewed).status_code, 401)
        self.assertEqual(_whoami(stale).status_code, 401, "the copy made before the renewal is closed too")

    def test_a_renewal_after_the_logout_is_not_possible(self):
        stale = _cookie_for(_payload(nonce="s2", exp=int(time.time()) + 600))
        _call("POST", "/api/admin/logout", stale)
        refused = _whoami(stale)
        self.assertEqual(refused.status_code, 401)
        self.assertIsNone(_cookie_of(refused, "st_admin"))

    def test_the_cookie_a_rename_re_issues_stays_the_same_session(self):
        # Renaming the account is the one account change that signs the browser in again (new username).
        db_operators.operator_create("mesa-1", _OP_PASS, rol="admisionista")
        before = _staff_login("mesa-1", _OP_PASS)
        changed = _call("POST", "/api/admin/account/password", before,
                        json={"actual": _OP_PASS, "usuario": "mesa-uno", "nueva": ""})
        self.assertEqual(changed.status_code, 200, changed.text)
        after = _cookie_of(changed, "st_admin")
        self.assertIsNotNone(after)
        self.assertEqual(admin_auth.session_id_of(after.split("=", 1)[1]),
                         admin_auth.session_id_of(before.split("=", 1)[1]))
        _call("POST", "/api/admin/logout", after)
        self.assertEqual(_whoami(after).status_code, 401)
        self.assertEqual(_whoami(before).status_code, 401, "the cookie from before the rename is closed too")

    def test_a_fresh_cookie_keeps_its_old_shape_and_its_nonce_is_the_session_id(self):
        # tests/e2e J28 reads the first segment of a fresh cookie as plain base64 JSON: a duplicate random
        # string in the payload would make itsdangerous compress it and break that reading.
        db_operators.operator_create("e2e-desk", _OP_PASS, rol="admisionista")   # the desk account J28 signs in with
        fresh = _staff_login("e2e-desk", _OP_PASS).split("=", 1)[1]
        self.assertNotIn("sid", admin_auth._signed_payload(fresh))
        self.assertTrue(fresh.split(".")[0], "the payload is not compressed")
        self.assertEqual(admin_auth.session_id_of(fresh), admin_auth._signed_payload(fresh)["nonce"])

    def test_a_fresh_cookie_stays_plain_base64_json_even_when_its_nonce_would_compress(self):
        # The nonce is random, so about one login in 170 of this account drew one that repeats enough of
        # the payload for zlib to win, and itsdangerous then compressed the cookie (the test above failed
        # at random). A nonce that repeats the username makes that case happen on every run.
        db_operators.operator_create("e2e-desk", _OP_PASS, rol="admisionista")
        with patch.object(admin_auth.secrets, "token_urlsafe", return_value="e2e-desk" * 4):
            fresh = _staff_login("e2e-desk", _OP_PASS).split("=", 1)[1]
        segment = fresh.split(".")[0]
        self.assertTrue(segment, "the payload is not compressed")
        import base64
        import json
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        self.assertEqual((claims["u"], claims["nonce"]), ("e2e-desk", "e2e-desk" * 4))
        self.assertEqual(admin_auth.session_id_of(fresh), "e2e-desk" * 4)
        self.assertEqual(_whoami("st_admin=" + fresh).status_code, 200)

    def test_the_owner_s_cookie_and_a_renewed_one_are_plain_too(self):
        # `admin`/`admin` repeats itself and a renewal carries `sid` beside the `nonce`: both always compressed.
        import base64
        import json

        def plain(cookie):
            segment = cookie.split("=", 1)[1].split(".")[0]
            return bool(segment) and isinstance(
                json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))), dict)
        self.assertTrue(plain(_staff_login(_OWNER, _OWNER_PASS)))
        renewed = _cookie_of(_whoami(_cookie_for(_payload(nonce="half-life", exp=int(time.time()) + 600))), "st_admin")
        self.assertIsNotNone(renewed)
        self.assertIn("sid", admin_auth._signed_payload(renewed.split("=", 1)[1]))
        self.assertTrue(plain(renewed))

    def test_a_compressed_cookie_handed_out_before_still_verifies(self):
        from itsdangerous import URLSafeTimedSerializer
        old = URLSafeTimedSerializer(admin_auth._signing_key(), salt=admin_auth._ITSD_SALT).dumps(
            _payload(nonce="ab" * 40))
        self.assertTrue(old.startswith("."), "itsdangerous compressed it")
        self.assertEqual(admin_auth.session_id_of(old), "ab" * 40)
        self.assertEqual(_whoami("st_admin=" + old).status_code, 200)

    def test_every_new_login_is_its_own_session(self):
        first, second = _staff_login(_OWNER, _OWNER_PASS), _staff_login(_OWNER, _OWNER_PASS)
        self.assertNotEqual(admin_auth.session_id_of(first.split("=", 1)[1]),
                            admin_auth.session_id_of(second.split("=", 1)[1]))


class TestRevocationStore(_Base):
    def _rows(self):
        import psycopg
        with psycopg.connect(_db.test_url) as c, c.cursor() as cur:
            cur.execute("SELECT sid, username, extract(epoch FROM expires_at) FROM admin_session_revocations ORDER BY sid")
            return cur.fetchall()

    def test_the_listing_outlives_every_copy_of_the_session(self):
        # A token issued under a 90-day setting, with the setting lowered to 30 afterwards.
        far = int(time.time()) + 90 * 86400
        cookie = _cookie_for(_payload(nonce="long-lived", exp=far))
        _call("POST", "/api/admin/logout", cookie)
        ((sid, usuario, expires),) = self._rows()
        self.assertEqual((sid, usuario), ("long-lived", _OWNER))
        self.assertGreaterEqual(expires, far, "never earlier than the cookie's own expiry")
        short = _cookie_for(_payload(nonce="short-lived", exp=int(time.time()) + 60))
        _call("POST", "/api/admin/logout", short)
        rows = dict((sid, expires) for sid, _, expires in self._rows())
        self.assertGreaterEqual(rows["short-lived"], time.time() + admin_auth.COOKIE_MAX_AGE,
                                "a copy renewed just before the logout lives this long")

    def test_rows_that_can_no_longer_matter_are_deleted_on_the_next_write(self):
        db_auth.admin_session_revoke("stale", _OWNER, time.time() - 10)
        self.assertEqual([r[0] for r in self._rows()], ["stale"])
        _call("POST", "/api/admin/logout", _cookie_for(_payload(nonce="fresh")))
        self.assertEqual([r[0] for r in self._rows()], ["fresh"])

    def test_listing_a_session_twice_keeps_the_later_expiry(self):
        now = time.time()
        db_auth.admin_session_revoke("twice", _OWNER, now + 1000)
        db_auth.admin_session_revoke("twice", _OWNER, now + 10)
        ((_, _, expires),) = self._rows()
        self.assertGreater(expires, now + 900)

    def test_a_deactivated_account_loses_its_open_session_on_the_next_request(self):
        db_operators.operator_create("baja-1", _OP_PASS, rol="admisionista")
        cookie = _staff_login("baja-1", _OP_PASS)
        self.assertEqual(_whoami(cookie).status_code, 200)
        owner = _staff_login(_OWNER, _OWNER_PASS)
        self.assertEqual(_call("DELETE", "/api/admin/users/baja-1", owner).status_code, 200)
        self.assertEqual(_whoami(cookie).status_code, 401)


class TestPasswordChangeClosesOtherSessions(_Base):
    """A new password ends every session opened with the old one (security review 2026-11-04):
    whoever copied a cookie, or knew the old password, is out; the browser that changed it is not."""

    def test_a_copied_cookie_dies_when_the_account_changes_its_password(self):
        db_operators.operator_create("mesa-pw", _OP_PASS, rol="admisionista")
        mine, thief = _staff_login("mesa-pw", _OP_PASS), _staff_login("mesa-pw", _OP_PASS)
        changed = _call("POST", "/api/admin/account/password", mine,
                        json={"actual": _OP_PASS, "nueva": "otra-clave-9876"})
        self.assertEqual(changed.status_code, 200, changed.text)
        fresh = _cookie_of(changed, "st_admin")
        self.assertIsNotNone(fresh, "the browser that changed it is signed in again")
        self.assertEqual(_whoami(fresh).status_code, 200)
        self.assertEqual(_whoami(thief).status_code, 401)
        self.assertEqual(_whoami(mine).status_code, 401, "the cookie from before the change is closed too")

    def test_a_reset_by_an_admin_closes_the_sessions_of_that_account(self):
        db_operators.operator_create("mesa-reset", _OP_PASS, rol="admisionista")
        cookie = _staff_login("mesa-reset", _OP_PASS)
        self.assertEqual(_whoami(cookie).status_code, 200)
        reset_to = "clave-nueva-5555"
        db_operators.operator_update("mesa-reset", password=reset_to)
        self.assertEqual(_whoami(cookie).status_code, 401)
        self.assertEqual(_whoami(_staff_login("mesa-reset", reset_to)).status_code, 200)

    def test_the_owner_who_changed_the_password_in_the_panel_closes_the_old_sessions(self):
        first, second = "owner-nueva-1234", "owner-nueva-5678"
        db_auth.admin_security_owner_update(_OWNER, password=first)
        old_style = _cookie_for(_payload(nonce="owner-before"))   # no stamp: from before this change
        self.assertEqual(_whoami(old_style).status_code, 200, "nobody is signed out by the deploy")
        before = _staff_login(_OWNER, first)
        self.assertEqual(_whoami(before).status_code, 200)
        db_auth.admin_security_owner_update(_OWNER, password=second)
        self.assertEqual(_whoami(before).status_code, 401)

    def test_a_session_keeps_working_while_the_password_is_unchanged(self):
        db_operators.operator_create("mesa-ok", _OP_PASS, rol="admisionista")
        cookie = _staff_login("mesa-ok", _OP_PASS)
        for _ in range(3):
            self.assertEqual(_whoami(cookie).status_code, 200)


class TestStoreFailures(_Base):
    def test_an_unreadable_store_does_not_lock_the_panel_out_but_a_known_signed_out_session_stays_closed(self):
        known = _staff_login(_OWNER, _OWNER_PASS)
        unknown = _staff_login(_OWNER, _OWNER_PASS)
        _call("POST", "/api/admin/logout", known)
        with patch.object(db_auth, "admin_session_is_revoked", side_effect=db.DbUnavailable("down")):
            self.assertEqual(_whoami(unknown).status_code, 200)
            self.assertEqual(_whoami(known).status_code, 401, "this process remembers what it closed")

    def test_any_other_failure_of_the_check_refuses_the_cookie(self):
        cookie = _staff_login(_OWNER, _OWNER_PASS)
        with self.assertLogs(admin_auth._LOG, "ERROR"), \
                patch.object(db_auth, "admin_session_is_revoked", side_effect=RuntimeError("boom")):
            self.assertEqual(_whoami(cookie).status_code, 401)
            self.assertIsNone(admin_auth._verify_token(cookie.split("=", 1)[1]))
        self.assertIsNotNone(admin_auth._verify_token(cookie.split("=", 1)[1]))

    def test_when_the_signing_out_cannot_be_recorded_the_answer_says_so_and_the_cookie_is_cleared(self):
        cookie = _staff_login(_OWNER, _OWNER_PASS)
        with self.assertLogs(admin_auth._LOG, "ERROR"), \
                patch.object(db_auth, "admin_session_revoke", side_effect=db.DbUnavailable("down")):
            out = _call("POST", "/api/admin/logout", cookie)
        self.assertEqual((out.status_code, out.json()["revoked"]), (503, False))
        self.assertIn("Max-Age=0", out.headers["set-cookie"])
        self.assertEqual(_whoami(cookie).status_code, 200, "the copy is NOT closed: this is why the answer is a 503")


class TestQueriesPerRequest(_Base):
    def test_the_check_runs_once_per_api_request_and_at_most_twice_per_screen(self):
        cookie = _staff_login(_OWNER, _OWNER_PASS)
        with patch.object(db_auth, "admin_session_is_revoked", wraps=db_auth.admin_session_is_revoked) as spy:
            _whoami(cookie)
            api_calls = spy.call_count
            spy.reset_mock()
            self.assertEqual(_screen(cookie).status_code, 200)
            screen_calls = spy.call_count
        self.assertEqual(api_calls, 1)
        self.assertLessEqual(screen_calls, 2)


class TestPortalInstallation(_Base):
    def test_a_signed_cookie_is_not_a_session_there_and_the_logout_has_nothing_to_close(self):
        cookie = _staff_login(_OWNER, _OWNER_PASS)
        _env.set_env(self, TURNOS_STAFF_PORTAL_LOGIN="1")
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        self.assertIsNone(admin_auth._verify_token(cookie.split("=", 1)[1]))
        with patch.object(db_auth, "admin_session_revoke") as writer:
            out = _call("POST", "/api/admin/logout", cookie)
        self.assertEqual((out.status_code, out.json()), (200, {"ok": True}))
        self.assertIn("Max-Age=0", out.headers["set-cookie"])
        writer.assert_not_called()


def _clears(response, name):
    return [line for line in response.headers.get_list("set-cookie")
            if line.startswith(name + "=;") and "Max-Age=0" in line]


class TestSignOutClosesEverySessionOfTheBrowser(_Base):
    """A browser can hold both cookies: the patient's and the panel's (staff sign in through the patient screen too)."""

    def _both(self):
        db_accounts.create_user(_PATIENT_DNI, "Paciente", "Prueba", "1980-01-01", "F", "+542254000000", _PATIENT_PASS)
        db_operators.operator_create("doble", _OP_PASS, rol="admisionista")
        staff = _staff_login("doble", _OP_PASS)
        patient = "st_ses=" + db_accounts.create_session(_PATIENT_DNI)
        return staff, patient, "%s; %s" % (staff, patient)

    def _alive(self, staff, patient):
        return (_whoami(staff).status_code, _call("GET", "/api/account/me", patient).status_code)

    def test_the_patients_sign_out_also_closes_the_panel_cookie(self):
        staff, patient, both = self._both()
        self.assertEqual(self._alive(staff, patient), (200, 200))
        out = _call("POST", "/api/account/logout", both)
        self.assertEqual(out.status_code, 200)
        self.assertEqual((len(_clears(out, "st_ses")), len(_clears(out, "st_admin"))), (1, 1))
        self.assertEqual(self._alive(staff, patient), (401, 401))

    def test_the_panels_sign_out_also_closes_the_patient_cookie(self):
        staff, patient, both = self._both()
        out = _call("POST", "/api/admin/logout", both)
        self.assertEqual(out.status_code, 200)
        self.assertEqual((len(_clears(out, "st_ses")), len(_clears(out, "st_admin"))), (1, 1))
        self.assertEqual(self._alive(staff, patient), (401, 401))

    def test_a_staff_member_who_signed_in_on_the_patient_screen_is_signed_out_by_its_button(self):
        db_operators.operator_create("por-paciente", _OP_PASS, rol="admisionista", email="por.paciente@example.invalid")
        login = _call("POST", "/api/account/login",
                      json={"email": "por.paciente@example.invalid", "password": _OP_PASS})
        self.assertEqual(login.status_code, 200, login.text)
        staff = _cookie_of(login, "st_admin")
        self.assertIsNotNone(staff, "the patient screen signs the staff member into the panel")
        self.assertEqual(_whoami(staff).status_code, 200)
        _call("POST", "/api/account/logout", staff)
        self.assertEqual(_whoami(staff).status_code, 401)

    def test_a_sign_out_with_only_one_kind_of_cookie_touches_nothing_else(self):
        staff, patient, _ = self._both()
        out = _call("POST", "/api/account/logout", patient)
        self.assertEqual((len(_clears(out, "st_ses")), len(_clears(out, "st_admin"))), (1, 0))
        self.assertEqual(self._alive(staff, patient), (200, 401))
        out = _call("POST", "/api/admin/logout", staff)
        self.assertEqual((len(_clears(out, "st_ses")), len(_clears(out, "st_admin"))), (0, 1))


class TestPatientLogoutRevokes(_Base):
    """The patient cookie is a server-side row: signing out deletes it (pinned again here, next to the staff cookie)."""

    def _patient(self):
        db_accounts.create_user(_PATIENT_DNI, "Paciente", "Prueba", "1980-01-01", "F", "+542254000000", _PATIENT_PASS)
        return "st_ses=" + db_accounts.create_session(_PATIENT_DNI)

    def test_the_copied_patient_cookie_is_refused_after_logout(self):
        copy = self._patient()
        self.assertEqual(_call("GET", "/api/account/me", copy).status_code, 200)
        out = _call("POST", "/api/account/logout", copy)
        self.assertEqual(out.status_code, 200)
        self.assertIn("Max-Age=0", out.headers["set-cookie"])
        dead = _call("GET", "/api/account/me", copy)
        self.assertEqual((dead.status_code, dead.json()), (401, {"error": "no_autenticado"}))
