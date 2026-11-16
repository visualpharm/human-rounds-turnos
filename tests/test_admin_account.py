"""Focused contract tests for self-service admin account security.

Drives the pure domain functions (`totp_start`/`totp_confirm`/
`update_profile`/`change_password`) directly — the same functions
`admin_account_router.py` calls; the legacy `handle()` transport is retired.
"""

import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
from app.admin_panel import admin_account  # noqa: E402
from app.admin_panel import admin_account_router  # noqa: E402
from app.core import config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(admin_account_router.router)
_CLIENT = TestClient(_app)

_AUTH_ENV = {
    "TURNOS_ADMIN_SECRET": "test-only-admin-account-signing-key",
    "TURNOS_CONFIG_LOCK": "1",
}


def setUpModule():
    """Keep this module's auth fixture out of sibling test modules."""
    # Module cleanups run last-in first-out: the cache is dropped after the environment is back.
    unittest.addModuleCleanup(config._reset_cache_para_tests)
    _env.set_env_for_module(_AUTH_ENV)
    config._reset_cache_para_tests()


class AdminAccountTest(unittest.TestCase):
    identity = {"usuario": "operadora", "rol": "admisionista"}

    def test_setup_token_is_signed_scoped_and_short_lived(self):
        token = admin_account._setup_token("operadora", "SECRET")
        self.assertEqual(
            admin_account._read_setup_token(token, "operadora"), "SECRET"
        )
        self.assertIsNone(admin_account._read_setup_token(token, "otra"))
        self.assertIsNone(admin_account._read_setup_token(token + "x", "operadora"))

        with patch("itsdangerous.timed.time.time", return_value=1_000):
            issued = admin_account._setup_token("operadora", "SECRET")
        with patch("itsdangerous.timed.time.time", return_value=1_601):
            self.assertIsNone(
                admin_account._read_setup_token(issued, "operadora")
            )

    def test_qr_svg_has_a_responsive_viewbox_and_no_fixed_canvas(self):
        svg = admin_account._qr_svg(
            "otpauth://totp/Human%20Rounds:operadora?secret=ABCDEFGHIJKLMNOP"
        )
        self.assertIsNotNone(svg)
        opening_tag = svg.split(">", 1)[0]
        self.assertRegex(opening_tag, r'\bviewBox="0 0 \d+ \d+"')
        self.assertIsNone(re.search(r'\b(?:width|height)="', opening_tag))

    @patch("app.admin_panel.admin_account._secret", return_value="ABCDEFGHIJKLMNOP")
    @patch("app.admin_panel.admin_account._qr_svg", return_value='<svg viewBox="0 0 45 45"></svg>')
    def test_2fa_start_returns_qr_and_plain_manual_key_fallback(
        self, _qr, _secret_value
    ):
        result = admin_account.totp_start(self.identity)
        self.assertEqual(result["secret"], "ABCDEFGHIJKLMNOP")
        self.assertIn("viewBox", result["qr_svg"])
        self.assertIn("otpauth://totp/", result["otpauth_url"])
        self.assertTrue(result["setup_token"])

    @patch("app.admin_panel.admin_account._change_password")
    @patch("app.admin_panel.admin_account.admin_auth.verify_current_password", return_value=True)
    def test_password_change_requires_eight_characters(
        self, _verify, change
    ):
        with self.assertRaises(admin_account.AccountError):
            admin_account.change_password(
                self.identity, {"actual": "actual", "nueva": "1234567"})
        change.assert_not_called()

        result = admin_account.change_password(
            self.identity, {"actual": "actual", "nueva": "nueva-segura"})
        self.assertEqual(result, {"ok": True})
        change.assert_called_once_with("operadora", "nueva-segura")

    @patch("app.admin_panel.admin_account._set_totp")
    @patch("app.admin_panel.admin_account.admin_auth._totp_valido", return_value=True)
    def test_2fa_is_only_saved_after_confirming_a_valid_code(
        self, _totp, set_totp
    ):
        token = admin_account._setup_token("operadora", "SECRET")
        result = admin_account.totp_confirm(
            self.identity, {"setup_token": token, "codigo": "123456"})
        self.assertEqual(result, {"ok": True})
        set_totp.assert_called_once_with("operadora", "SECRET")

    @patch("app.admin_panel.admin_account._set_email")
    def test_email_is_validated_before_saving(self, set_email):
        with self.assertRaises(admin_account.AccountError):
            admin_account.update_profile(self.identity, {"email": "sin-arroba"})
        set_email.assert_not_called()

        result = admin_account.update_profile(
            self.identity, {"email": "op@patient_site.gob.ar"})
        self.assertEqual(result, {"ok": True, "email": "op@patient_site.gob.ar"})
        set_email.assert_called_once_with("operadora", "op@patient_site.gob.ar")

    @patch("app.admin_panel.admin_account._set_email")
    def test_empty_email_clears_it(self, set_email):
        result = admin_account.update_profile(self.identity, {"email": "  "})
        self.assertEqual(result, {"ok": True, "email": None})
        set_email.assert_called_once_with("operadora", None)

    def test_unknown_route_is_not_claimed(self):
        response = _CLIENT.get("/api/admin/account/unknown")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
