"""The doctor's own profile on Cuenta (/api/admin/doctor-profile, 2026-10-27):
a medico reads and saves name, specialty and phone of the doctor linked to
the session — never another doctor's record, never a schedule, and no other
role reaches it."""
import unittest
from unittest.mock import patch

import _env

_env.process_defaults(DATABASE_URL='', TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1')

from app.core import permissions  # noqa: E402

DOCTORS = [
    {'id': 'doctor', 'nombre': 'ALICIA MÉNDEZ', 'nombre_pila': 'Alicia', 'apellido': 'Méndez',
     'especialidad': 'Pediatría', 'telefono': '', 'centros': [{'centro': 'ostende', 'franjas': []}]},
    {'id': 'foreign', 'nombre': 'Otro Médico', 'especialidad': 'Cardiología', 'telefono': '2254 00-0000'},
]
SPECIALTIES = [{'nombre': 'Pediatría', 'cluster': 'ped'}, {'nombre': 'Cardiología', 'cluster': 'car'}]


class TestDoctorOwnProfile(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import main as app
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)
        for target, value in [
                ('app.auth.admin_auth._verify_token', lambda token: {'usuario': token, 'rol': 'medico' if token.startswith('medico') else token}
                 if token in ('medico', 'medico-sin-ficha', 'admin', 'admisionista', 'reportes') else None),
                ('app.auth.admin_auth.account_security', lambda *args, **kwargs: {'tiene_2fa': True, '2fa_deadline': None, '2fa_vencido': False}),
                ('app.core.permissions.access_for', lambda user, role: {'slug': role, 'state': 'ok', 'permissions': frozenset(permissions.BUILTIN_ROLES[role]['permissions'])}),
                ('app.core.db_operators.operator_doctor_id', lambda usuario: 'doctor' if usuario == 'medico' else None),
                ('app.patient_web.catalog.admin_list', lambda tipo: [dict(d) for d in DOCTORS] if tipo == 'medicos' else []),
                ('app.patient_web.catalog.specialties', lambda: SPECIALTIES)]:
            mocked = patch(target, side_effect=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def get(self, who):
        return self.client.get('/api/admin/doctor-profile', cookies={'st_admin': who})

    def post(self, who, body):
        return self.client.post('/api/admin/doctor-profile', json=body, cookies={'st_admin': who})

    def test_medico_reads_only_the_doctor_linked_to_the_session(self):
        response = self.get('medico')
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['item'], {'nombre': 'Alicia Méndez', 'nombre_pila': 'Alicia', 'apellido': 'Méndez',
                                        'especialidad': 'Pediatría', 'telefono': ''})
        self.assertEqual(body['especialidades'], ['Pediatría', 'Cardiología'])
        self.assertNotIn('Otro', response.text)

    def test_other_roles_never_reach_a_doctor_profile(self):
        with patch('app.admin_panel.admin_content.edit_item') as edit:
            for role in ('admin', 'admisionista', 'reportes'):
                with self.subTest(role=role):
                    self.assertEqual(self.get(role).status_code, 403)
                    self.assertEqual(self.post(role, {'nombre_pila': 'X', 'apellido': 'Y', 'especialidad': 'Pediatría'}).status_code, 403)
            edit.assert_not_called()
        self.assertEqual(self.client.get('/api/admin/doctor-profile').status_code, 401)

    def test_save_edits_the_own_doctor_with_the_four_fields_and_the_schedule_untouched(self):
        with patch('app.admin_panel.admin_content.edit_item', return_value={'ok': True}) as edit:
            response = self.post('medico', {'nombre_pila': ' Alicia ', 'apellido': 'Méndez', 'especialidad': 'Pediatría',
                                            'telefono': '2254 41-2233', 'id': 'foreign', 'centros': []})
        self.assertEqual(response.status_code, 200, response.text)
        edit.assert_called_once()
        tipo, doctor_id, body, identity = edit.call_args.args
        self.assertEqual((tipo, doctor_id), ('medicos', 'doctor'), 'the session decides the record, never the body')
        self.assertEqual(body, {'nombre_pila': 'Alicia', 'apellido': 'Méndez', 'especialidad': 'Pediatría',
                                'telefono': '2254 41-2233', '_agenda_sin_cambios': True})
        self.assertEqual(identity['usuario'], 'medico')

    def test_save_refuses_an_incomplete_or_unknown_profile_with_its_remedy(self):
        with patch('app.admin_panel.admin_content.edit_item') as edit:
            empty = self.post('medico', {'nombre_pila': '', 'apellido': 'Méndez', 'especialidad': 'Pediatría'})
            unknown = self.post('medico', {'nombre_pila': 'Alicia', 'apellido': 'Méndez', 'especialidad': 'Astrología'})
            long_phone = self.post('medico', {'nombre_pila': 'Alicia', 'apellido': 'Méndez', 'especialidad': 'Pediatría', 'telefono': '1' * 41})
            edit.assert_not_called()
        self.assertEqual(empty.status_code, 400)
        self.assertIn('nombre', empty.json()['error'])
        self.assertEqual(unknown.status_code, 400)
        self.assertIn('especialidad', unknown.json()['error'])
        self.assertEqual(long_phone.status_code, 400)

    def test_an_account_without_a_linked_doctor_has_no_profile_to_save(self):
        response = self.get('medico-sin-ficha')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['item'])
        with patch('app.admin_panel.admin_content.edit_item') as edit:
            self.assertEqual(self.post('medico-sin-ficha', {'nombre_pila': 'A', 'apellido': 'B', 'especialidad': 'Pediatría'}).status_code, 404)
            edit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
