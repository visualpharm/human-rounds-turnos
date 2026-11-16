from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from app.auth import admin_auth
from app.ops import demo_installation
from app.core import db_auth
import _env  # noqa: E402


class DemoDoctorSecurityTests(unittest.TestCase):
    def security(self, identity, demo):
        with _env.patched_env({'TURNOS_DEMO_MODE': '1' if demo else '0'}), \
             patch.object(admin_auth, 'account_totp_secret', return_value=''), \
             patch.object(db_auth, 'admin_security_ensure_deadline', return_value={'totp_deadline': datetime(2020, 1, 1, tzinfo=timezone.utc)}):
            return admin_auth.account_security(identity, ensure_deadline=True)

    def test_shared_demo_doctor_does_not_require_private_factor(self):
        who = {'usuario': demo_installation.demo_doctor_user(), 'rol': 'medico'}
        self.assertFalse(self.security(who, True)['2fa_vencido'])
        self.assertTrue(self.security(who, False)['2fa_vencido'])

    def test_other_staff_and_wrong_roles_still_require_factor(self):
        self.assertTrue(self.security({'usuario': 'another-doctor', 'rol': 'medico'}, True)['2fa_vencido'])
        self.assertTrue(self.security({'usuario': demo_installation.demo_doctor_user(), 'rol': 'admin'}, True)['2fa_vencido'])

    def test_shared_demo_doctor_is_read_only_for_every_mutating_verb(self):
        from app.core import permissions
        who = {'usuario': demo_installation.demo_doctor_user(), 'rol': 'medico'}
        with _env.patched_env({'TURNOS_DEMO_MODE': '1'}), \
             patch.object(admin_auth, '_verify_token', return_value=who), \
             patch.object(admin_auth, 'account_security', return_value={}), \
             patch.object(permissions, 'access_for', return_value={
                 'slug': 'medico', 'state': 'ok', 'permissions': permissions.ALL}):
            self.assertTrue(admin_auth.evaluate_admin_gate('token', 'GET', '/api/mi/foto', permitir_medico=True).ok)
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                gate = admin_auth.evaluate_admin_gate('token', method, '/api/mi/foto', permitir_medico=True)
                self.assertEqual(gate.status, 403)
                self.assertEqual(gate.body['error'], 'demo_solo_lectura')
