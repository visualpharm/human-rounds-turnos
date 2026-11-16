"""The low findings of the 2026-11 security review (L-1), one test each.

No live database: the stores the code reads are replaced by small stand-ins.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_security_review_low.py
"""

import asyncio
import io
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_SIGNING = "unit-test-signing-key-0123456789"
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1",
                      TURNOS_ADMIN_SECRET=_SIGNING)

from app.admin_panel import admin_account  # noqa: E402
from app.ai import lab_results, lab_results_router  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.cuentas import accounts  # noqa: E402
from app.modules.intake import printable  # noqa: E402
from app.ops import demo_installation  # noqa: E402


class PrescriptionLinksExpire(unittest.TestCase):
    def _row(self, days):
        return {"created": datetime.now(timezone.utc) - timedelta(days=days), "receta_token": "tok"}

    def test_a_fresh_link_opens_and_an_old_one_does_not(self):
        with patch.object(printable.db_clinical, "consultation_by_token",
                          side_effect=lambda token: self._row(printable.LINK_DAYS - 1)):
            self.assertIsNotNone(printable._consulta_para("tok", "receta"))
        with patch.object(printable.db_clinical, "consultation_by_token",
                          side_effect=lambda token: self._row(printable.LINK_DAYS + 1)):
            self.assertIsNone(printable._consulta_para("tok", "receta"))


class TotpRestartAsksForThePassword(unittest.TestCase):
    identity = {"usuario": "ana", "rol": "admin"}

    def test_an_account_with_an_authenticator_needs_the_password(self):
        with patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                patch.object(admin_auth, "account_totp_secret", return_value="SECRET"), \
                patch.object(admin_auth, "verify_current_password", return_value=False):
            with self.assertRaises(admin_account.AccountError):
                admin_account.totp_start(self.identity, {})
            with self.assertRaises(admin_account.AccountError):
                admin_account.totp_start(self.identity, {"actual": "mal"})

    def test_the_right_password_and_a_first_enrolment_go_through(self):
        with patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                patch.object(admin_auth, "account_totp_secret", return_value="SECRET"), \
                patch.object(admin_auth, "verify_current_password", return_value=True):
            self.assertTrue(admin_account.totp_start(self.identity, {"actual": "bien"})["ok"])
        with patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                patch.object(admin_auth, "account_totp_secret", return_value=None):
            self.assertTrue(admin_account.totp_start(self.identity)["ok"])


class UploadIsReadBounded(unittest.TestCase):
    def test_the_file_is_read_at_most_one_byte_past_the_cap(self):
        asked = []

        class Upload:
            content_type = "application/pdf"
            filename = "a.pdf"

            async def read(self, size=-1):
                asked.append(size)
                return b"x"

        with patch.object(lab_results_router.accounts, "current_user", return_value={"dni": "1"}), \
                patch.object(lab_results_router.lab_results, "subir_resultado",
                             return_value=(200, {"ok": True})):
            class Form(dict):
                async def close(self):
                    pass

            request = MagicMock()
            request.headers = {"content-length": "100"}

            async def form(**kwargs):
                return Form(archivo=Upload(), patient_dni="")
            request.form = form
            asyncio.run(lab_results_router.account_lab_results_upload(request))
        self.assertEqual(asked, [lab_results.MAX_UPLOAD_BYTES + 1])


class ChangedAuthenticatorClosesSessions(unittest.TestCase):
    def test_the_session_dies_when_the_second_factor_changes(self):
        stamp = {"v": "AAAA"}
        with patch.object(admin_auth, "_totp_stamp", side_effect=lambda u: stamp["v"]), \
                patch.object(admin_auth, "_password_stamp", return_value=None):
            token = admin_auth._mint_token("ana", "admin")
            self.assertIsNotNone(admin_auth._verify_token(token))
            stamp["v"] = "BBBB"
            self.assertIsNone(admin_auth._verify_token(token))
            self.assertIsNotNone(admin_auth._verify_token(admin_auth._mint_token("ana", "admin")))

    def test_a_cookie_without_the_stamp_is_no_session_once_the_account_has_one(self):
        # Finding 9 (security review 2026-11, staff auth): a cookie with no `ts` cannot prove the
        # authenticator did not change, so it stops working where a live stamp exists.
        with patch.object(admin_auth, "_totp_stamp", return_value=None), \
                patch.object(admin_auth, "_password_stamp", return_value=None):
            token = admin_auth._mint_token("ana", "admin")
            self.assertIsNotNone(admin_auth._verify_token(token))   # nothing to compare: stays
        with patch.object(admin_auth, "_totp_stamp", return_value="AAAA"), \
                patch.object(admin_auth, "_password_stamp", return_value=None):
            self.assertIsNone(admin_auth._verify_token(token))


class DemoSessionsAreShort(unittest.TestCase):
    def test_the_demo_users_live_hours_and_everybody_else_the_normal_span(self):
        with patch.object(demo_installation, "enabled", return_value=True):
            for user in (demo_installation.demo_user(), demo_installation.demo_doctor_user()):
                self.assertLessEqual(admin_auth._session_seconds(user), admin_auth.DEMO_SESSION_SECONDS)
            self.assertEqual(admin_auth._session_seconds("ana"), admin_auth._max_age())
        with patch.object(demo_installation, "enabled", return_value=False):
            self.assertEqual(admin_auth._session_seconds(demo_installation.demo_user()), admin_auth._max_age())


class RecoveryAnswersWithoutWaitingForTheSend(unittest.TestCase):
    def test_an_existing_account_answers_while_its_mail_is_still_going_out(self):
        release = threading.Event()
        sent = []

        def slow_send(to, subject, body):
            release.wait(5)
            sent.append(to)

        user = {"dni": "30111222", "email": "a@example.invalid", "contacto": None}
        with patch.object(accounts.store, "get_user", return_value=user), \
                patch.object(accounts.store, "create_reset_token", return_value="tok"), \
                patch.object(accounts, "_send_email", slow_send):
            result = accounts._recover_core({"dni": "30111222"})
            answered_before_the_send = not sent
            release.set()
            accounts.drain_detached()
        self.assertEqual(result.json_body, {"ok": True})
        self.assertTrue(answered_before_the_send)
        self.assertEqual(sent, ["a@example.invalid"])

    def test_an_unknown_account_gets_the_same_answer(self):
        with patch.object(accounts.store, "get_user", return_value=None), \
                patch.object(accounts.store, "get_user_by_email", return_value=None):
            known = accounts._recover_core({"dni": "30111999"})
            unknown = accounts._recover_core({"email": "nadie@example.invalid"})
        self.assertEqual((known.status, known.json_body), (unknown.status, unknown.json_body))


if __name__ == "__main__":
    unittest.main()
