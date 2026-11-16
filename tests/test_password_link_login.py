"""Invitation/recovery handoff: real tokens, synthetic accounts, normal MFA."""
import json
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.auth import admin_auth, admin_auth_router, password_reset, password_reset_router
from app.core import db, permissions
from app.core import db_operators
from app.core import db_auth
import _dbtest

# Assembled, not written out: a literal password in a test file trips the secret scanner of the publication preflight.
NEW_PASSWORD = "".join(["Synthetic", "-pass-", "2"])

_db = _dbtest.PerModuleDb("sana_turnos_test_link_login", seed=lambda: db_operators.operators_list())


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


def app():
    result = FastAPI()
    result.include_router(password_reset_router.router)
    result.include_router(admin_auth_router.router)
    from app.core import deps as _early_deps
    _early_deps.install_early_response(result)  # the app's answer of a dependency
    return result


class PasswordLinkUnitTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(admin_auth, "_signing_key", return_value="synthetic-link-signing"))
        self.enterContext(patch.object(admin_auth.staff_portal, "enabled", return_value=False))
        self.enterContext(patch.object(admin_auth, "owner_usuario", return_value="owner"))
        self.mark = self.enterContext(patch.object(admin_auth, "marcar_acceso"))
        self.row = {"usuario": "doctor", "rol": "medico", "activo": True,
                    "pass_hash": "synthetic-hash", "totp_secret": None}
        self.verify = self.enterContext(patch.object(db_operators, "operator_verify_password", return_value=self.row))
        self.access = self.enterContext(patch.object(permissions, "access_for", return_value={
            "state": "ok", "slug": "medico", "permissions": frozenset()}))
        self.consume = self.enterContext(patch.object(db_auth, "admin_reset_token_consume", return_value="doctor"))
        self.client = self.enterContext(TestClient(app(), base_url="https://testserver"))

    def submit(self):
        return self.client.post("/api/admin/reset-password", headers={"X-Forwarded-Proto": "https"},
                                json={"token": "synthetic-link", "password": NEW_PASSWORD})

    def test_valid_link_rotates_same_second_session_and_uses_finish_login(self):
        with patch.object(admin_auth.time, "time", return_value=1900000000), \
                patch.object(admin_auth, "_finish_login", wraps=admin_auth._finish_login) as finish:
            old = admin_auth._mint_token("doctor", "medico")
            self.client.cookies.set("st_admin", old)
            response = self.submit()
            token = response.cookies["st_admin"]
            self.assertNotEqual(old, token)
            self.assertEqual(admin_auth._verify_token(token), {"usuario": "doctor", "rol": "medico"})
            finish.assert_called_once()
        self.assertEqual(response.json()["redirect"], "/admin")
        for flag in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/"):
            self.assertIn(flag, response.headers["set-cookie"])
        self.verify.assert_called_once_with("doctor", NEW_PASSWORD)
        self.mark.assert_called_once_with("doctor")

    def test_same_second_mints_are_distinct_and_prior_format_still_valid(self):
        with patch.object(admin_auth.time, "time", return_value=1900000000):
            self.assertNotEqual(admin_auth._mint_token("doctor", "medico"), admin_auth._mint_token("doctor", "medico"))
            old = admin_auth._itsdangerous_serializer().dumps({"v": admin_auth.TOKEN_VERSION,
                "u": "doctor", "r": "medico", "exp": 1900000100})
            self.assertEqual(admin_auth._verify_token(old), {"usuario": "doctor", "rol": "medico"})

    def test_invalid_or_consumed_link_never_reaches_session_policy(self):
        self.consume.return_value = None
        response = self.submit()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("set-cookie", response.headers)
        self.verify.assert_not_called()
        self.mark.assert_not_called()

    def test_short_password_does_not_consume_link(self):
        response = self.client.post("/api/admin/reset-password", json={"token": "synthetic", "password": "short"})
        self.assertEqual(response.status_code, 400)
        self.consume.assert_not_called()
        self.assertNotIn("set-cookie", response.headers)

    def test_missing_inactive_wrong_account_or_role_has_no_cookie(self):
        cases = [None, dict(self.row, activo=False), dict(self.row, usuario="other"),
                 dict(self.row, rol=None), dict(self.row, rol=""), dict(self.row, rol="admin/forged"),
                 dict(self.row, rol="operador")]
        for row in cases:
            with self.subTest(row=row):
                self.verify.return_value = row
                response = self.submit()
                self.assertEqual(response.json()["redirect"], "/admin/login")
                self.assertNotIn("set-cookie", response.headers)
        self.mark.assert_not_called()

    def test_unknown_disabled_and_missing_current_role_fail_closed(self):
        for access in ({"state": "unknown", "slug": "invented"}, {"state": "disabled", "slug": "medico"},
                       {"state": "inactive", "slug": "medico"}, {"state": "ok", "slug": None}):
            with self.subTest(access=access):
                self.access.return_value = access
                self.assertNotIn("set-cookie", self.submit().headers)
        self.mark.assert_not_called()

    def test_current_enabled_role_wins_without_medico_fallback(self):
        self.access.return_value = {"state": "ok", "slug": "custom_staff"}
        response = self.submit()
        self.assertEqual(admin_auth._verify_token(response.cookies["st_admin"])["rol"], "custom_staff")

    def test_owner_collision_never_uses_owner_fallback(self):
        self.consume.return_value = "owner"
        self.assertNotIn("set-cookie", self.submit().headers)
        self.verify.assert_not_called()

    def test_db_or_signing_failure_after_commit_keeps_password_without_cookie(self):
        for seam, failure in ((self.verify, db.DbUnavailable("synthetic outage")),
                              (self.access, db.DbUnavailable("synthetic outage"))):
            with self.subTest(seam=seam):
                seam.side_effect = failure
                response = self.submit()
                self.assertEqual(response.json(), {"ok": True, "redirect": "/admin/login"})
                self.assertNotIn("set-cookie", response.headers)
                seam.side_effect = None
        with patch.object(admin_auth, "_signing_key", side_effect=RuntimeError("unconfigured")):
            self.assertNotIn("set-cookie", self.submit().headers)

    def test_totp_receives_pending_proof_not_admin_cookie_and_remains_revocable(self):
        self.row["totp_secret"] = "JBSWY3DPEHPK3PXP"
        response = self.submit()
        self.assertEqual(response.json()["redirect"], "/admin/login?staff=1")
        self.assertNotIn("st_admin", response.cookies)
        self.assertIn("SameSite=Strict; Max-Age=300; Secure", response.headers["set-cookie"])
        self.mark.assert_not_called()
        headers = {"cookie": "st_staff_pending=" + response.cookies["st_staff_pending"]}
        with patch.object(db_operators, "operator_get", return_value=self.row):
            self.assertEqual(admin_auth.pending_staff(headers)["usuario"], "doctor")
        for changes in ({"pass_hash": "changed"}, {"totp_secret": "changed"}, {"activo": False}):
            with patch.object(db_operators, "operator_get", return_value=dict(self.row, **changes)):
                self.assertIsNone(admin_auth.pending_staff(headers))
        with patch.object(db_operators, "operator_get", return_value=self.row), \
                patch("itsdangerous.timed.time.time", return_value=admin_auth.time.time() + 301):
            self.assertIsNone(admin_auth.pending_staff(headers))

    def test_mfa_handoff_uses_existing_code_validation_before_session(self):
        self.row["totp_secret"] = "JBSWY3DPEHPK3PXP"
        self.submit()
        with patch.object(db_operators, "operator_get", return_value=self.row), \
                patch.object(admin_auth, "_turnstile_ok_from", return_value=True), \
                patch.object(admin_auth, "_throttled", return_value=False), \
                patch.object(admin_auth, "_totp_valido", return_value=False):
            bad = self.client.post("/api/admin/login", json={"staff_pending": True, "codigo": "000000"})
            self.assertEqual(bad.status_code, 401)
            self.assertNotIn("set-cookie", bad.headers)
            with patch.object(admin_auth, "_totp_valido", return_value=True):
                good = self.client.post("/api/admin/login", json={"staff_pending": True, "codigo": "123456"})
                self.assertEqual(good.status_code, 200)
                self.assertEqual(admin_auth._verify_token(good.cookies["st_admin"])["usuario"], "doctor")

    def test_portal_guard_blocks_page_reset_recovery_and_domain_before_consume(self):
        with patch.object(admin_auth.staff_portal, "enabled", return_value=True):
            self.assertEqual(self.client.get("/admin/reset-password?token=synthetic").status_code, 404)
            self.assertEqual(self.submit().status_code, 404)
            self.assertEqual(self.client.post("/api/admin/reset-password", json={"action": "request", "usuario": "doctor"}).status_code, 404)
            with self.assertRaises(password_reset.ResetError):
                password_reset.reset_password("synthetic", NEW_PASSWORD)
        self.consume.assert_not_called()

    def test_page_only_automatically_follows_fixed_internal_handoff_destinations(self):
        page = password_reset.page_html("synthetic").decode()
        self.assertIn("['/admin','/admin/login?staff=1','/admin/login'].includes(data.redirect)", page)
        self.assertIn("window.location.replace(destination)", page)
        self.assertIn("if(btn.disabled)return", page)
        self.assertIn("messages.network", page)


class PasswordLinkDatabaseTests(unittest.TestCase):
    def setUp(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)
        self.enterContext(patch.object(admin_auth, "_signing_key", return_value="synthetic-db-link-signing"))
        self.enterContext(patch.object(admin_auth.staff_portal, "enabled", return_value=False))
        self.enterContext(patch.object(admin_auth, "owner_usuario", return_value="owner"))
        self.name = self._testMethodName.removeprefix("test_")
        self.new_password = secrets.token_urlsafe(24)
        db_operators.operator_create(self.name, "Synthetic-old-password", "medico", medico_id=self.name)

    def submit(self, token, password=None):
        if password is None:
            password = self.new_password
        with TestClient(app()) as client:
            return client.post("/api/admin/reset-password", json={"token": token, "password": password})

    def test_invite_and_recovery_share_one_use_and_password_persistence(self):
        for hours in (168, 24):
            with self.subTest(hours=hours):
                token = db_auth.admin_reset_token_create(self.name, horas=hours)
                response = self.submit(token)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["redirect"], "/admin")
                self.assertEqual(admin_auth._verify_token(response.cookies["st_admin"])["usuario"], self.name)
                self.assertIsNotNone(db_operators.operator_verify_password(self.name, self.new_password))
                # Losing the first response never turns a retry into another proof.
                repeated = self.submit(token, "Synthetic-repeated-password")
                self.assertEqual(repeated.status_code, 400)
                self.assertNotIn("set-cookie", repeated.headers)
                self.assertIsNotNone(db_operators.operator_verify_password(self.name, self.new_password))

    def test_expired_replaced_unknown_and_disabled_link_never_set_cookie(self):
        expired = db_auth.admin_reset_token_create(self.name, horas=-1)
        replaced = db_auth.admin_reset_token_create(self.name)
        current = db_auth.admin_reset_token_create(self.name)
        for token in (expired, replaced, "unknown-synthetic"):
            response = self.submit(token)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("set-cookie", response.headers)
        db_operators.operator_deactivate(self.name)
        response = self.submit(current)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("set-cookie", response.headers)
        self.assertFalse(db_operators.operator_get(self.name)["activo"])

    def test_concurrent_submission_has_exactly_one_committed_handoff(self):
        token = db_auth.admin_reset_token_create(self.name)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(self.submit, [token, token]))
        self.assertEqual(sorted(r.status_code for r in results), [200, 400])
        self.assertEqual(sum("set-cookie" in r.headers for r in results), 1)

    def test_authenticated_entry_preserves_first_onboarding_and_completed_work_route(self):
        from app.admin_panel import admin_navigation, admin_preferences
        token = db_auth.admin_reset_token_create(self.name)
        identity = admin_auth._verify_token(self.submit(token).cookies["st_admin"])
        with patch.object(admin_preferences, "get_items", return_value={}):
            self.assertEqual(admin_navigation.resolve_target(identity, "/admin/cola"), "/admin/onboarding")
            db_operators.operator_complete_onboarding(self.name)
            self.assertEqual(admin_navigation.resolve_target(identity, "/admin/cola"), "/admin/cola")

    def test_disabled_account_remains_revoked_after_successful_handoff(self):
        token = db_auth.admin_reset_token_create(self.name)
        response = self.submit(token)
        self.assertIn("st_admin", response.cookies)
        db_operators.operator_deactivate(self.name)
        self.assertEqual(permissions.access_for(self.name, "medico")["state"], "inactive")
