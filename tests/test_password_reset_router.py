"""test_password_reset_router.py — native ASGI transport parity for
/admin/reset-password + /api/admin/reset-password (docs/roadmap/native-
migration-campaign-20260826.md, family F9). No existing test covered the
legacy handle() directly. Page validity and escaping use a real token in an
isolated Postgres database; request validation and recovery also have offline
coverage."""

import os
import secrets
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_reset_router", seed=lambda: db_operators.operators_list())


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


class TestPasswordResetRouter(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_page_renders_html_with_token_escaped(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)
        db_operators.operator_create("reset-page-doctor", "fixture-password", "medico",
                           email="reset-page@example.test")
        # Keep the real DB create/lookup path while exercising HTML escaping;
        # ordinary URL-safe randomness cannot include these characters.
        with patch.object(secrets, "token_urlsafe", return_value='<b>"x'):
            token = db_auth.admin_reset_token_create("reset-page-doctor")
        self.assertTrue(db_auth.admin_reset_token_status(token))
        r = self.client.get(
            '/admin/reset-password', params={"token": token})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertIn("&lt;b&gt;&quot;x", r.text)
        self.assertNotIn("<b>\"x", r.text)
        self.assertIn('id="reset-form"', r.text)
        self.assertNotIn("El enlace venció", r.text)

    def test_valid_link_names_the_username_from_the_token_never_from_the_query(self):
        # A doctor invited by a copied link has no email to log in with: the
        # page says which username the new password belongs to.
        if not _db.have_db:
            self.skipTest(_db.skip_reason)
        db_operators.operator_create("diego.rivera", "fixture-password", "medico")
        token = db_auth.admin_reset_token_create("diego.rivera", horas=168)
        r = self.client.get('/admin/reset-password',
                            params={"token": token, "usuario": "intruso.falso"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("diego.rivera", r.text)
        self.assertIn("Tu usuario", r.text)
        self.assertNotIn("intruso.falso", r.text)
        db_auth.admin_reset_token_consume(token, "Synthetic-pass-1")
        used = self.client.get('/admin/reset-password', params={"token": token})
        self.assertIn("El enlace venció", used.text)
        self.assertNotIn("diego.rivera", used.text)

    def test_unknown_token_is_expired_and_never_echoed_unescaped(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)
        token = 'unknown-<b>"x'
        self.assertFalse(db_auth.admin_reset_token_status(token))
        r = self.client.get('/admin/reset-password', params={"token": token})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertIn("El enlace venció", r.text)
        self.assertNotIn('id="reset-form"', r.text)
        self.assertNotIn('id="reset-token"', r.text)
        self.assertNotIn('<b>"x', r.text)

    def test_short_password_rejected_with_exact_message(self):
        r = self.client.post(
            "/api/admin/reset-password", json={"token": "t", "password": "short"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(
            r.json(),
            {"error": "La contraseña tiene que tener al menos 8 caracteres."})

    def test_valid_length_without_db_returns_503_or_expired(self):
        # No DATABASE_URL configured -> db_auth.admin_reset_token_consume raises
        # DbUnavailable -> 503, same contract as the legacy handle().
        r = self.client.post(
            "/api/admin/reset-password",
            json={"token": "nonexistent-token", "password": "longenoughpw"})
        self.assertIn(r.status_code, (400, 503))
        self.assertIn("error", r.json())

    def test_bad_json_body_returns_400_with_exact_prefix(self):
        r = self.client.post(
            "/api/admin/reset-password", content=b"{not json",
            headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json()["error"].startswith("JSON inválido"))

    def test_recovery_response_does_not_reveal_account_or_delivery_state(self):
        from app.auth import password_reset
        replies = []
        for account in (None, {"usuario": "synthetic", "activo": False, "email": "test@example.test"},
                        {"usuario": "synthetic", "activo": True, "email": "test@example.test"}):
            with patch.object(password_reset, "_allow_request", return_value=True), \
                    patch("app.core.db_operators.operator_get_public", return_value=account), \
                    patch("app.core.db_auth.admin_reset_token_create", return_value="synthetic-token"), \
                    patch("app.notifications.mailgun.send", side_effect=RuntimeError("provider down")):
                r = self.client.post("/api/admin/reset-password", json={"action": "request", "usuario": "synthetic"})
                self.assertEqual(r.status_code, 200)
                replies.append(r.json())
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(replies[1], replies[2])
        self.assertTrue(replies[0]["ok"])

    def test_recovery_throttles_ip_and_identifier_without_account_lookup(self):
        from app.auth import password_reset
        password_reset._REQUESTS.clear()
        with patch("app.core.db_operators.operator_get_public", return_value=None) as lookup:
            for _ in range(12):
                r = self.client.post("/api/admin/reset-password", json={"action": "request", "usuario": "synthetic"})
                self.assertEqual(r.status_code, 200)
            self.assertEqual(lookup.call_count, 3)
        password_reset._REQUESTS.clear()
        self.assertTrue(password_reset._allow_request("ip-1", "same"))
        self.assertTrue(password_reset._allow_request("ip-2", "same"))
        self.assertTrue(password_reset._allow_request("ip-3", "same"))
        self.assertFalse(password_reset._allow_request("ip-4", "same"))
        password_reset._REQUESTS.clear()
        for n in range(5):
            self.assertTrue(password_reset._allow_request("one-ip", str(n)))
        self.assertFalse(password_reset._allow_request("one-ip", "sixth"))
        password_reset._REQUESTS.clear()

    def test_non_object_reset_body_is_rejected(self):
        for value in ([], ["x"], "text", 1, None):
            self.assertEqual(self.client.post("/api/admin/reset-password", json=value).status_code, 400)

    def test_recovery_by_email_uses_only_the_unique_active_match(self):
        from app.auth import password_reset
        item = {"usuario": "canonical-doctor", "activo": True, "email": "doctor@example.test"}
        with patch.object(password_reset, "_allow_request", return_value=True), \
                patch("app.core.db_operators.operator_by_email", return_value=item) as lookup, \
                patch("app.core.staff_access_email.send_access_link", return_value=(True, None)) as send:
            r = self.client.post("/api/admin/reset-password", json={"action": "request", "usuario": " Doctor@Example.test "})
        self.assertEqual(r.status_code, 200)
        lookup.assert_called_once_with("Doctor@Example.test")
        send.assert_called_once_with(item, {})
        for account in (None, dict(item, activo=False), dict(item, email=None)):
            with patch.object(password_reset, "_allow_request", return_value=True), \
                    patch("app.core.db_operators.operator_by_email", return_value=account), \
                    patch("app.core.staff_access_email.send_access_link") as send:
                self.client.post("/api/admin/reset-password", json={"action": "request", "usuario": "Doctor@Example.test"})
            send.assert_not_called()

    def test_recovery_without_mail_provider_is_a_neutral_no_op(self):
        from app.auth import password_reset
        with patch.object(password_reset, "_allow_request", return_value=True), \
                patch("app.core.db_operators.operator_get_public", return_value={
                    "usuario": "synthetic", "activo": True, "email": "doctor@example.test"}), \
                patch("app.core.db_auth.admin_reset_token_create", return_value="synthetic-token"), \
                patch("app.notifications.mailgun._cfg", return_value=("", "", "", True)), \
                patch("app.notifications.mailgun._log"), \
                patch("app.notifications.mailgun.urllib.request.build_opener") as network:
            response = self.client.post("/api/admin/reset-password", json={"action": "request", "usuario": "synthetic"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), password_reset.request_response())
        network.assert_not_called()

    def test_throttle_expiry_and_capacity_are_bounded(self):
        from app.auth import password_reset
        password_reset._REQUESTS.clear()
        self.addCleanup(password_reset._REQUESTS.clear)
        with patch.object(password_reset.time, "monotonic", return_value=1):
            for _ in range(3):
                self.assertTrue(password_reset._allow_request("ip", "synthetic"))
            self.assertFalse(password_reset._allow_request("ip", "synthetic"))
        with patch.object(password_reset.time, "monotonic", return_value=902):
            self.assertTrue(password_reset._allow_request("ip", "synthetic"))
        with patch.object(password_reset, "_MAX_REQUEST_KEYS", 2), \
                patch.object(password_reset.time, "monotonic", return_value=902):
            self.assertFalse(password_reset._allow_request("another-ip", "another"))
            self.assertEqual(len(password_reset._REQUESTS), 2)

    def test_reset_page_translates_and_keeps_login_destination(self):
        from app.core import i18n
        from app.auth import password_reset
        with patch.object(i18n, "active", return_value="en"):
            page = password_reset.page_html("synthetic-token").decode()
        self.assertIn('lang="en"', page)
        self.assertIn("Choose a new password", page)
        self.assertIn("/admin/login", page)
        self.assertNotIn("Elegí una nueva contraseña", page)


if __name__ == "__main__":
    unittest.main()
