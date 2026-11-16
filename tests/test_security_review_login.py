"""test_security_review_login.py — finding M-5 of the 2026-11 security review
(docs/compliance/security-review-2026-11-surfaces.md): login and recovery.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
      -m unittest discover -s tests -p "test_security_review_login.py"
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402

config.ENV_PATH = ""


class TestTurnstileHostIsNotTrusted(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_TURNSTILE_SECRET="s3", TURNOS_TURNSTILE_SITEKEY="k3",
                     TURNOS_TURNSTILE_OFF=None, TURNOS_ASGI=None)
        turnstile.invalidar_cache()
        self.addCleanup(turnstile.invalidar_cache)

    def test_a_public_peer_claiming_a_dev_host_still_faces_the_gate(self):
        for host in ("localhost", "devbox", "127.0.0.1:7700", "x.local"):
            self.assertTrue(turnstile.activo_from({"Host": host}, "8.8.8.8"), host)

    def test_a_private_peer_on_a_dev_host_is_exempt(self):
        self.assertFalse(turnstile.activo_from({"Host": "devbox:7700"}, "100.64.1.2"))
        self.assertFalse(turnstile.activo_from({"Host": "localhost"}, "127.0.0.1"))

    def test_an_unparseable_or_mapped_public_peer_still_faces_the_gate(self):
        self.assertTrue(turnstile.activo_from({"Host": "localhost"}, "not-an-ip"))
        self.assertTrue(turnstile.activo_from({"Host": "localhost"}, "::ffff:8.8.8.8"))
        self.assertFalse(turnstile.activo_from({"Host": "localhost"}, "::ffff:127.0.0.1"))

    def test_a_public_host_is_checked_for_any_peer(self):
        self.assertTrue(turnstile.activo_from({"Host": "salud.pinamar.gob.ar"}, "127.0.0.1"))

    def test_without_a_peer_the_host_alone_decides_as_before(self):
        self.assertFalse(turnstile.activo_from({"Host": "localhost"}))

    def test_verify_demands_the_token_from_a_public_peer_on_a_dev_host(self):
        self.assertFalse(turnstile.verificar_from({"Host": "localhost"}, "8.8.8.8", ""))
        self.assertTrue(turnstile.verificar_from({"Host": "localhost"}, "127.0.0.1", ""))


class TestLockoutByDni(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from app.cuentas import accounts
        self.accounts = accounts
        accounts._LOGIN_ATTEMPTS.clear()
        accounts._DNI_FAILS.clear()
        self.addCleanup(accounts._LOGIN_ATTEMPTS.clear)
        self.addCleanup(accounts._DNI_FAILS.clear)
        for target, kw in (("app.cuentas.accounts._effective_auth_mode", {"return_value": "password"}),
                           ("app.cuentas.accounts.turnstile.verificar_from", {"return_value": True}),
                           ("app.cuentas.accounts.store.verify_password", {"return_value": None}),
                           ("app.cuentas.accounts.store.get_user_by_email", {"return_value": None}),
                           ("app.auth.admin_auth._throttled", {"return_value": False}),
                           ("app.auth.admin_auth.patient_staff_login",
                            {"return_value": accounts.Result.json({}, status=401)})):
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def _try(self, ip, dni="30111222"):
        return self.accounts._login_core({}, "1.2.3.4", {"dni": dni, "password": "x"}, ip).status

    def test_rotating_ips_cannot_guess_one_account_forever(self):
        codes = [self._try("10.0.0.%d" % i) for i in range(self.accounts._DNI_MAX + 2)]
        self.assertEqual(codes[0], 401)
        self.assertEqual(codes[-1], 429)

    def test_other_accounts_are_not_locked(self):
        for i in range(self.accounts._DNI_MAX + 1):
            self._try("10.0.0.%d" % i)
        self.assertEqual(self._try("10.9.9.9", dni="30999888"), 401)


class TestResetByCodeIsLimited(unittest.TestCase):
    def test_wrong_codes_for_one_dni_stop_being_checked(self):
        from unittest import mock
        from app.cuentas import accounts
        for d in (accounts._DNI_FAILS, accounts._RESET_FAILS, accounts._RESET_IP):
            d.clear()
            self.addCleanup(d.clear)
        calls = []
        user = {"dni": "30111222", "contacto": "+5491100000000"}
        with mock.patch("app.cuentas.accounts.store.get_user", return_value=user), \
                mock.patch("app.cuentas.accounts.store.verify_otp",
                           side_effect=lambda *a: calls.append(a) or False):
            body = {"dni": "30111222", "code": "000000", "password": "clave-larga-1"}
            statuses = [accounts._reset_otp_core(body).status for _ in range(accounts._DNI_MAX + 3)]
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(len(calls), accounts._DNI_MAX)

    def test_wrong_codes_do_not_lock_the_victim_out_of_the_login(self):
        # Security review 2026-11, item 12: the recovery shared the login's count.
        from unittest import mock
        from app.cuentas import accounts
        for d in (accounts._DNI_FAILS, accounts._RESET_FAILS, accounts._RESET_IP):
            d.clear()
            self.addCleanup(d.clear)
        user = {"dni": "30111222", "contacto": "+5491100000000"}
        with mock.patch("app.cuentas.accounts.store.get_user", return_value=user), \
                mock.patch("app.cuentas.accounts.store.verify_otp", return_value=False):
            body = {"dni": "30111222", "code": "000000", "password": "clave-larga-1"}
            for i in range(accounts._DNI_MAX + 2):
                accounts._reset_otp_core(body, "10.0.0.%d" % i)
        self.assertEqual(accounts._DNI_FAILS.get("30111222", []), [])

    def test_one_address_is_limited_across_many_dnis(self):
        from unittest import mock
        from app.cuentas import accounts
        for d in (accounts._DNI_FAILS, accounts._RESET_FAILS, accounts._RESET_IP):
            d.clear()
            self.addCleanup(d.clear)
        with mock.patch("app.cuentas.accounts.store.get_user", return_value=None):
            statuses = [accounts._reset_otp_core(
                {"dni": "3011%04d" % i, "code": "0", "password": "clave-larga-1"}, "9.9.9.9").status
                for i in range(accounts._RESET_IP_MAX + 2)]
        self.assertEqual(statuses[0], 400)
        self.assertEqual(statuses[-1], 429)


class TestLoginAttemptsAreReservedAtomically(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from app.cuentas import accounts
        self.accounts = accounts
        for d in (accounts._LOGIN_ATTEMPTS, accounts._DNI_FAILS):
            d.clear()
            self.addCleanup(d.clear)
        for target, kw in (("app.cuentas.accounts._effective_auth_mode", {"return_value": "password"}),
                           ("app.cuentas.accounts.turnstile.verificar_from", {"return_value": True}),
                           ("app.cuentas.accounts.store.pending_claimants", {"return_value": []}),
                           ("app.auth.admin_auth._throttled", {"return_value": False}),
                           ("app.auth.admin_auth.patient_staff_login",
                            {"return_value": accounts.Result.json({}, status=401)})):
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def test_parallel_guesses_cannot_all_pass_the_question_first(self):
        # Item 13: the attempt used to be counted only after the password was checked, so a burst in
        # parallel all passed the «locked?» question. Now the count happens before the check.
        from unittest import mock
        seen = []

        def slow_verify(dni, password):
            seen.append(len(self.accounts._DNI_FAILS.get(dni, [])))
            return None

        with mock.patch("app.cuentas.accounts.store.verify_password", side_effect=slow_verify):
            for _ in range(self.accounts._DNI_MAX - 1):
                self.accounts._login_core({}, "1.2.3.4", {"dni": "30111222", "password": "x"}, "1.1.1.1")
            seen.clear()
            self.accounts._login_core({}, "1.2.3.4", {"dni": "30111222", "password": "x"}, "1.1.1.2")
        # While the 10th password is being checked its attempt is already counted.
        self.assertEqual(seen, [self.accounts._DNI_MAX])

    def test_an_unknown_email_is_locked_like_a_known_one(self):
        # Item 15: eleven failures answered 429 for a known email and 401 for an unknown one.
        from unittest import mock
        known = {"dni": "30111222"}
        out = {}
        for name, email, user in (("known", "a@x.org", known), ("unknown", "b@x.org", None)):
            with mock.patch("app.cuentas.accounts.store.get_user_by_email", return_value=user), \
                    mock.patch("app.cuentas.accounts.store.verify_password", return_value=None):
                out[name] = [self.accounts._login_core(
                    {}, "1.2.3.4", {"email": email, "password": "x"}, "7.7.7.%d" % i).status
                    for i in range(self.accounts._DNI_MAX + 2)]
        self.assertEqual(out["known"], out["unknown"])
        self.assertEqual(out["unknown"][-1], 429)


class TestRecoveryTimingWhenThePoolIsFull(unittest.TestCase):
    def test_a_full_pool_never_sends_on_the_request_for_either_kind(self):
        # Item 16: with 32 busy senders the existing account was delivered inline (slower), the unknown one not.
        import threading
        from unittest import mock
        from app.cuentas import accounts
        stop = threading.Event()
        busy = [threading.Thread(target=stop.wait, daemon=True) for _ in range(accounts._DETACHED_MAX)]
        for t in busy:
            t.start()
        self.addCleanup(stop.set)
        self.addCleanup(accounts._DETACHED.clear)
        accounts._DETACHED[:] = busy
        inline = []
        with mock.patch.object(accounts, "_DETACH_WAIT", 0.01), \
                mock.patch.object(accounts, "_deliver_recovery", side_effect=lambda *a: inline.append(a)), \
                mock.patch("app.cuentas.accounts.store.get_user",
                           side_effect=lambda dni: {"dni": dni, "email": "a@x.org"} if dni == "30111222" else None):
            accounts._recover_core({"dni": "30111222"})
            accounts._recover_core({"dni": "30999999"})
        self.assertEqual(inline, [])


class TestSessionCookieSecure(unittest.TestCase):
    def test_secure_on_an_https_installation_only(self):
        from app.cuentas import accounts
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="https://salud.pinamar.gob.ar")
        self.assertIn("Secure", accounts._set_cookie("abc").split("; "))
        self.assertIn("Secure", accounts._clear_cookie().split("; "))
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="http://127.0.0.1:7700")
        self.assertNotIn("Secure", accounts._set_cookie("abc").split("; "))


if __name__ == "__main__":
    unittest.main()
