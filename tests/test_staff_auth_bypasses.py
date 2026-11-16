"""Staff-login bypasses found by the independent check of 2026-11 (findings 8, 9, 10, 11, 14, 17, 19).

No live database: the stores the code reads are replaced by small stand-ins.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_staff_auth_bypasses.py
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_SIGNING = "unit-test-signing-key-0123456789"
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1",
                      TURNOS_ADMIN_SECRET=_SIGNING)

from app.admin_panel import admin_account, admin_users  # noqa: E402
from app.admin_panel.native_json import ApiError  # noqa: E402
from app.ai import lab_results, lab_results_router  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.ops import demo_installation  # noqa: E402


class DemoCookieIsNoSessionWithoutDemoMode(unittest.TestCase):
    """Finding 8."""

    def _mint(self, user, rol):
        with patch.object(demo_installation, "enabled", return_value=True), \
                patch.object(admin_auth, "_totp_stamp", return_value=None), \
                patch.object(admin_auth, "_password_stamp", return_value=None):
            return admin_auth._mint_token(user, rol)

    def test_the_demo_admin_cookie_dies_when_demo_mode_goes_off(self):
        token = self._mint(demo_installation.demo_user(), "admin")
        with patch.object(admin_auth, "_totp_stamp", return_value=None), \
                patch.object(admin_auth, "_password_stamp", return_value=None):
            with patch.object(demo_installation, "enabled", return_value=True):
                self.assertIsNotNone(admin_auth._verify_token(token))
            with patch.object(demo_installation, "enabled", return_value=False):
                self.assertIsNone(admin_auth._verify_token(token))

    def test_the_demo_doctor_cookie_dies_too_and_a_real_account_is_untouched(self):
        token = self._mint(demo_installation.demo_doctor_user(), "medico")
        real = admin_auth._mint_token("ana", "admin")
        with patch.object(admin_auth, "_totp_stamp", return_value=None), \
                patch.object(admin_auth, "_password_stamp", return_value=None), \
                patch.object(demo_installation, "enabled", return_value=False):
            self.assertIsNone(admin_auth._verify_token(token))
            self.assertIsNotNone(admin_auth._verify_token(real))

    def test_a_renewal_keeps_the_mark(self):
        with patch.object(demo_installation, "enabled", return_value=True), \
                patch.object(admin_auth, "_totp_stamp", return_value=None), \
                patch.object(admin_auth, "_password_stamp", return_value=None):
            first = admin_auth._mint_token(demo_installation.demo_user(), "admin")
            sid = admin_auth.session_id_of(first)
            again = admin_auth._mint_token(demo_installation.demo_user(), "admin", sid=sid)
        with patch.object(admin_auth, "_totp_stamp", return_value=None), \
                patch.object(admin_auth, "_password_stamp", return_value=None), \
                patch.object(demo_installation, "enabled", return_value=False):
            self.assertIsNone(admin_auth._verify_token(again))


class OwnCredentialsNeedTheCurrentPassword(unittest.TestCase):
    """Finding 10."""
    me = {"usuario": "ana", "rol": "admin"}

    def _check(self, usuario, body, ok):
        with patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                patch.object(admin_auth, "verify_current_password", return_value=ok):
            admin_users._edit_own_credentials_check(usuario, body, self.me, "es")

    def test_changing_my_password_or_authenticator_without_the_current_one_is_refused(self):
        for body in ({"password": "nueva-clave-1"}, {"totp": "generar"}, {"totp": "quitar"}):
            with self.subTest(body=body), self.assertRaises(ApiError) as caught:
                self._check("ana", body, ok=False)
            self.assertEqual(caught.exception.status, 400)

    def test_the_right_password_goes_through_and_other_edits_are_not_asked(self):
        self._check("ana", {"password": "nueva-clave-1", "actual": "bien"}, ok=True)
        self._check("ana", {"rol": "admin"}, ok=False)          # nothing sensitive
        self._check("beto", {"totp": "generar"}, ok=False)      # an admin resetting someone else


    def test_a_differently_spelled_own_name_is_still_asked(self):
        with self.assertRaises(ApiError):
            self._check(" ANA ", {"password": "nueva-clave-1"}, ok=False)

    def test_guessing_the_current_password_is_limited(self):
        admin_auth._ATTEMPTS.clear()
        with patch.object(admin_auth, "_authenticate", return_value=None) as auth:
            for _ in range(admin_auth._MAX_ATTEMPTS):
                self.assertFalse(admin_auth.verify_current_password(self.me, "mal"))
            calls = auth.call_count
            self.assertFalse(admin_auth.verify_current_password(self.me, "mal"))
            self.assertEqual(auth.call_count, calls, "past the limit the password is not even tried")
        admin_auth._ATTEMPTS.clear()


class TotpStartRefusesWhenTheStoreIsUnreadable(unittest.TestCase):
    """Finding 11."""

    def test_a_read_error_is_not_a_first_enrolment(self):
        with patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                patch("app.core.db_operators.operator_get", side_effect=RuntimeError("down")), \
                patch.object(admin_auth, "verify_current_password", return_value=False):
            with self.assertRaises(admin_account.AccountError) as caught:
                admin_account.totp_start({"usuario": "ana", "rol": "admin"}, {})
        self.assertEqual(caught.exception.status, 503)

    def test_the_strict_read_raises_and_the_default_one_still_degrades(self):
        with patch("app.core.db_operators.operator_get", side_effect=RuntimeError("down")):
            self.assertEqual(admin_auth.account_totp_secret("ana"), "")
            with self.assertRaises(RuntimeError):
                admin_auth.account_totp_secret("ana", strict=True)


class LoginIsCountedPerAccountToo(unittest.TestCase):
    """Finding 14."""

    def setUp(self):
        admin_auth._ATTEMPTS.clear()
        self.addCleanup(admin_auth._ATTEMPTS.clear)

    def _login(self, usuario, ip, password="mal"):
        body = json.dumps({"usuario": usuario, "password": password}).encode()
        with patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                patch.object(admin_auth, "_turnstile_ok_from", return_value=True), \
                patch.object(admin_auth, "_authenticate_ex", return_value=(None, False)):
            return admin_auth.login_result({}, ip, body)

    def test_one_account_guessed_from_many_addresses_gets_locked(self):
        for n in range(admin_auth._MAX_ATTEMPTS):
            self.assertEqual(self._login("Ana", "10.0.0.%d" % n).status, 401)
        # a fresh address, the same account: refused
        self.assertEqual(self._login("ana", "10.9.9.9").status, 429)
        # another account from that same fresh address is not
        self.assertEqual(self._login("beto", "10.9.9.9").status, 401)

    def test_an_account_that_does_not_exist_is_locked_the_same_way(self):
        outcomes = [self._login("fantasma", "10.1.0.%d" % n).status
                    for n in range(admin_auth._MAX_ATTEMPTS + 1)]
        self.assertEqual(outcomes[-1], 429)
        self.assertEqual(set(outcomes[:-1]), {401})


class UploadAnswersBeforeParsingTheForm(unittest.TestCase):
    """Finding 17: nothing reads (and spools to disk) the multipart body before the session and
    the declared length have been checked."""

    def _request(self, length):
        request = MagicMock()
        request.headers = {"content-length": length} if length is not None else {}

        async def form(**kwargs):
            raise AssertionError("the form was parsed")
        request.form = form
        return request

    def test_an_anonymous_big_upload_is_a_401_without_reading_the_body(self):
        big = str(lab_results.MAX_UPLOAD_BYTES * 4)
        with patch.object(lab_results_router.accounts, "current_user", return_value=None):
            response = asyncio.run(lab_results_router.account_lab_results_upload(self._request(big)))
        self.assertEqual(response.status_code, 401)

    def test_a_signed_in_oversized_or_unsized_upload_is_refused_before_the_form(self):
        with patch.object(lab_results_router.accounts, "current_user", return_value={"dni": "1"}):
            big = asyncio.run(lab_results_router.account_lab_results_upload(
                self._request(str(lab_results.MAX_UPLOAD_BYTES * 4))))
            unsized = asyncio.run(lab_results_router.account_lab_results_upload(self._request(None)))
        self.assertEqual(big.status_code, 413)
        self.assertEqual(unsized.status_code, 411)

    def test_the_route_declares_no_multipart_body_for_fastapi_to_spool(self):
        from fastapi.routing import APIRoute
        route = next(r for r in lab_results_router.router.routes
                     if isinstance(r, APIRoute) and r.path == "/api/account/lab-results/upload"
                     and "POST" in r.methods)
        self.assertEqual(route.dependant.body_params, [])


class StaffCookieIsSecureOnAnHttpsOrigin(unittest.TestCase):
    """Finding 19."""

    def test_the_public_origin_is_enough_without_the_forwarded_header(self):
        with patch.object(admin_auth.site_identity, "base_url", return_value="https://salud.example"):
            self.assertTrue(admin_auth._secure_cookie_from({}))
            self.assertIn("Secure", admin_auth._set_cookie_from({}, "tok"))
            self.assertIn("Secure", admin_auth._clear_cookie_from(None))

    def test_a_plain_http_origin_without_the_header_stays_without_it(self):
        with patch.object(admin_auth.site_identity, "base_url", return_value="http://127.0.0.1:7700"):
            self.assertFalse(admin_auth._secure_cookie_from({}))
            self.assertTrue(admin_auth._secure_cookie_from({"X-Forwarded-Proto": "https"}))


if __name__ == "__main__":
    unittest.main()
