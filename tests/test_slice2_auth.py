"""Synthetic, DB-independent slice 2 security regressions."""
import json
import unittest
from unittest.mock import patch
from app.auth import admin_auth
from app.admin_panel import admin_account
from app.cuentas import accounts
from app.core import db_operators

class RenameTests(unittest.TestCase):
    def test_rename_requires_current_password(self):
        with patch.object(admin_auth, 'owner_usuario', return_value='owner'), patch.object(admin_auth, 'verify_current_password', return_value=False):
            with self.assertRaises(admin_account.AccountError):
                admin_account.change_password({'usuario':'old','rol':'medico'}, {'usuario':'new','actual':'wrong'})

    def test_rename_without_password_change(self):
        with patch.object(admin_auth, 'owner_usuario', return_value='owner'), patch.object(admin_auth, 'verify_current_password', return_value=True), patch.object(db_operators, 'operator_rename', create=True) as rename:
            result = admin_account.change_password({'usuario':'old','rol':'medico'}, {'usuario':'new','actual':'correct'})
            self.assertEqual(result['usuario'], 'new')
            rename.assert_called_once_with('old', 'new', password=None, current_password='correct')

class StaffTests(unittest.TestCase):
    def test_patient_failure_can_handoff_to_staff(self):
        with patch.object(accounts, '_effective_auth_mode', return_value='password'), patch.object(accounts.turnstile, 'verificar_from', return_value=True), patch.object(accounts.store, 'verify_password', return_value=None), patch.object(admin_auth, 'patient_staff_login', create=True, return_value=admin_auth.AuthResult(200, {'ok':True,'redirect':'/admin'}, set_cookie='st_admin=synthetic')):
            result = accounts._login_core({}, '127.0.0.1', {'dni':'12345678','password':'synthetic'}, 'slice2')
            self.assertEqual(result.status, 200)
            self.assertEqual(result.json_body['redirect'], '/admin')
            self.assertEqual(result.extra_headers['Set-Cookie'], 'st_admin=synthetic')


class StaffDomainTests(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(admin_auth, '_signing_key', return_value='synthetic-slice2-signing'))
        self.stack.enter_context(patch.object(admin_auth, 'owner_usuario', return_value='owner'))
        self.stack.enter_context(patch.object(admin_auth, 'marcar_acceso'))
        self.stack.enter_context(patch.object(admin_auth, '_turnstile_ok_from', return_value=True))
        admin_auth._ATTEMPTS.clear()
        accounts._LOGIN_ATTEMPTS.clear()
        self.row = {'usuario':'staff','rol':'medico','activo':True,'pass_hash':'synthetic-hash', 'totp_secret':'JBSWY3DPEHPK3PXP'}
        self.identity = {'usuario':'staff','rol':'medico','totp_secret':self.row['totp_secret'], 'credential_stamp':admin_auth._credential_stamp(self.row)}

    def handoff(self):
        with patch.object(admin_auth, '_staff_operator', return_value=self.row), patch.object(admin_auth, '_authenticate_ex', return_value=(self.identity, False)):
            return admin_auth.patient_staff_login({}, '192.0.2.1', '12345678', 'synthetic-password')

    def test_totp_handoff_never_mints_admin_session_and_completes_after_code(self):
        with patch.object(admin_auth, '_mint_token', wraps=admin_auth._mint_token) as mint:
            result = self.handoff()
            mint.assert_not_called()
        self.assertEqual(result.body['redirect'], '/admin/login?staff=1')
        self.assertNotIn('st_admin=', result.set_cookie)
        self.assertIn('HttpOnly; SameSite=Strict; Max-Age=300', result.set_cookie)
        headers = {'cookie':result.set_cookie.split(';')[0]}
        with patch('app.core.db_operators.operator_get', return_value=self.row):
            pending = admin_auth.pending_staff(headers)
            self.assertEqual(pending['identifier'], '12345678')
            result = admin_auth.login_result(headers, '192.0.2.1', json.dumps({'staff_pending':True}).encode())
            self.assertEqual(result.body, {'need_totp':True})
            self.assertIsNone(result.set_cookie)
            with patch.object(admin_auth, '_totp_valido', return_value=False):
                result = admin_auth.login_result(headers, '192.0.2.1', b'{"staff_pending":true,"codigo":"000000"}')
                self.assertEqual(result.status, 401)
                self.assertIsNone(result.set_cookie)
            with patch.object(admin_auth, '_totp_valido', return_value=True):
                result = admin_auth.login_result(headers, '192.0.2.1', b'{"staff_pending":true,"codigo":"123456"}')
                self.assertEqual(result.status, 200)
                self.assertIn('st_admin=', result.set_cookie)

    def test_password_change_disable_and_expiry_invalidate_pending_proof(self):
        result = self.handoff()
        headers = {'cookie':result.set_cookie.split(';')[0]}
        for changes in ({'pass_hash':'changed'}, {'activo':False}, {'totp_secret':None}, {'usuario':'renamed'}):
            row = dict(self.row, **changes)
            with patch('app.core.db_operators.operator_get', return_value=row if not changes.get('usuario') else None):
                self.assertIsNone(admin_auth.pending_staff(headers))
        with patch('app.core.db_operators.operator_get', return_value=self.row), patch('itsdangerous.timed.time.time', return_value=admin_auth.time.time()+301):
            self.assertIsNone(admin_auth.pending_staff(headers))
        with patch('app.core.db_operators.operator_get', return_value=self.row):
            self.assertIsNone(admin_auth.pending_staff({'cookie':headers['cookie']+'tampered'}))

    def test_no_totp_uses_same_session_completion_and_admin_landing(self):
        self.identity['totp_secret'] = None
        result = self.handoff()
        self.assertEqual(result.body['redirect'], '/admin')
        self.assertIn('st_admin=', result.set_cookie)
        cookie = result.set_cookie.split(';')[0].split('=',1)[1]
        self.assertEqual(admin_auth._verify_token(cookie), {'usuario':'staff','rol':'medico'})

    def test_ambiguous_missing_inactive_and_wrong_password_share_failure_and_throttle(self):
        for account in (None, dict(self.row, activo=False), self.row):
            with patch.object(admin_auth, '_staff_operator', return_value=account), patch.object(admin_auth, '_authenticate_ex', return_value=(None,False)), patch('app.auth.password_hashing.dummy_verify'):
                result = admin_auth.patient_staff_login({}, '192.0.2.1', '12345678', 'wrong')
                self.assertEqual((result.status,result.body,result.set_cookie), (401,{'error':'credenciales'},None))
        self.assertEqual(len(admin_auth._ATTEMPTS['192.0.2.1']),3)
        with patch.object(admin_auth, '_throttled', return_value=True), patch.object(admin_auth, '_staff_operator') as lookup:
            result = admin_auth.patient_staff_login({}, '192.0.2.1', '12345678', 'wrong')
            lookup.assert_not_called()
            self.assertIsNone(result.set_cookie)

    def test_dni_resolution_counts_inactive_duplicates(self):
        # The catalog reaches auth through the provider registered by the
        # composition root (app.py); a test supplies it the same way.
        from app.core import db_operators
        doctors = [{'id':'doc','dni':'12.345.678'}]
        prev = admin_auth._staff_doctors_provider
        admin_auth.register_staff_doctors_provider(lambda: doctors)
        try:
            with patch.object(db_operators,'operators_list',return_value=[dict(self.row,medico_id='doc')]):
                self.assertEqual(admin_auth._staff_operator('12.345.678')['usuario'],'staff')
            with patch.object(db_operators,'operators_list',return_value=[dict(self.row,medico_id='doc'),dict(self.row,usuario='other',medico_id='doc',activo=False)]):
                self.assertIsNone(admin_auth._staff_operator('12345678'))
        finally:
            admin_auth.register_staff_doctors_provider(prev)

    def test_valid_patient_always_wins_and_creates_only_patient_session(self):
        with patch.object(accounts,'_effective_auth_mode',return_value='password'), patch.object(accounts.turnstile,'verificar_from',return_value=True), patch.object(accounts.store,'verify_password',return_value={'dni':'12345678'}), patch.object(accounts.store,'create_session',return_value='patient-token'), patch.object(accounts,'_user_json',return_value={'dni':'12345678'}), patch.object(admin_auth,'patient_staff_login') as staff:
            result=accounts._login_core({},'192.0.2.1',{'dni':'12345678','password':'synthetic'},'192.0.2.1')
            self.assertEqual(result.status,200)
            self.assertNotIn('redirect',result.json_body)
            staff.assert_not_called()

    def test_patient_login_honors_admin_throttle_before_password_check(self):
        with patch.object(admin_auth,'_throttled',return_value=True), patch.object(accounts.store,'verify_password') as verify:
            result=accounts._login_core({},'192.0.2.1',{'dni':'12345678','password':'synthetic'},'192.0.2.1')
            self.assertEqual(result.status,429)
            verify.assert_not_called()

class RenameValidationTests(unittest.TestCase):
    def test_owner_and_invalid_name_cannot_rename(self):
        with patch.object(admin_auth,'owner_usuario',return_value='owner'), patch.object(admin_auth,'verify_current_password',return_value=True), patch.object(db_operators,'operator_rename') as rename:
            for old,new in [('owner','new'),('staff','OWNER'),('staff','a b'),('staff','x')]:
                with self.assertRaises(admin_account.AccountError):
                    admin_account.change_password({'usuario':old,'rol':'admin'}, {'usuario':new,'actual':'synthetic'})
            rename.assert_not_called()

    def test_conflict_is_localized_and_does_not_change_password_separately(self):
        with patch.object(admin_auth,'owner_usuario',return_value='owner'), patch.object(admin_auth,'verify_current_password',return_value=True), patch.object(db_operators,'operator_rename',side_effect=db_operators.OperatorConflict('nombre_ya_existe')), patch.object(admin_account,'_change_password') as change:
            with self.assertRaises(admin_account.AccountError) as caught:
                admin_account.change_password({'usuario':'staff','rol':'medico'}, {'usuario':'other','actual':'synthetic','nueva':'new-password'})
            self.assertEqual(caught.exception.status,409)
            change.assert_not_called()

    def test_route_reissues_session_for_new_name(self):
        from app.admin_panel import admin_account_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app=FastAPI()
        app.include_router(admin_account_router.router)
        from app.core import deps as _early_deps
        _early_deps.install_early_response(app)  # the app's answer of a dependency
        # The gate is a dependency of the route's signature: overridden the FastAPI way.
        app.dependency_overrides[admin_account_router._GATE]=lambda:{'usuario':'old','rol':'medico'}
        with patch.object(admin_account,'change_password',return_value={'ok':True,'usuario':'new'}), patch.object(admin_auth,'_signing_key',return_value='synthetic-signing'):
            with TestClient(app) as client:
                response=client.post('/api/admin/account/password',json={'usuario':'new','actual':'synthetic'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(admin_auth._verify_token(response.cookies['st_admin']),{'usuario':'new','rol':'medico'})
