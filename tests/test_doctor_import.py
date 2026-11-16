"""«Cargar lista» in Médicos on a real database: it is the Médicos editor's own create, row by row.

Pins: the preview writes nothing and sends nothing; a created row is ONE doctor with ONE account
(email -> invitation sent, no email -> a link to copy); a repeated upload creates nothing twice;
a bad row never stops the others; another doctor's email is refused; the endpoint needs the
same permission as creating a doctor; the old import page redirects to Médicos.
"""
import unittest
from unittest.mock import patch

from app.admin_panel import doctor_import as importer
from app.auth import admin_auth
from app.core import db
from app.core import db_operators
from app.core import db_auth
from app.core import db_catalog
from app.patient_web import catalog
from fastapi.testclient import TestClient
from app import main as app
import _dbtest
import _env

_db = _dbtest.PerModuleDb('sana_turnos_test_doctor_import')
ENV = {
    'TURNOS_FAKE_AI': '1', 'TURNOS_SKIP_HSI': '1', 'TURNOS_ENV_FILE': '/dev/null',
    'MAIL_ENABLED': '0', 'PACK_CHAIN': 'global,example-town',
    'TURNOS_ADMIN_USER': 'admin', 'TURNOS_ADMIN_PASS': 'test-only-pass',
    'TURNOS_ADMIN_SECRET': 'test-only-signing-key',
}
CENTERS = [{'slug': 'test-clinic', 'name': 'Centro de prueba'}]
HEADER = 'nombre,especialidad,centro,email\n'
ROW = 'Ana Prueba,Clínica médica,Centro de prueba,ana@example.test\n'
ROW_NO_EMAIL = 'Beto Prueba,clinica medica,test-clinic,\n'


def setUpModule():
    _env.set_env_for_module(ENV)    # put back after tearDownModule
    _db.set_up()


def tearDownModule():
    _db.tear_down()


class Import(unittest.TestCase):
    def setUp(self):
        if not _db.have_db:
            self.fail(_db.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE operators, operator_access, catalog, admin_reset_tokens, '
                        'admin_account_security, operator_username_history')
        catalog.invalidate()
        for target in (patch.object(catalog, 'centers', return_value=CENTERS),
                       patch.object(catalog, 'specialties', return_value=[{'nombre': 'Clínica médica'}]),
                       patch.object(catalog, '_specialty_cluster',
                                    side_effect=lambda s: 'test' if s == 'Clínica médica' else None)):
            target.start()
            self.addCleanup(target.stop)
        mail = patch('app.notifications.mail.send', return_value=True)
        self.send = mail.start()
        self.addCleanup(mail.stop)

    def run_import(self, rows=ROW, dry=True):
        return importer.import_file((HEADER + rows).encode(), 'doctors.csv', dry)

    def test_preview_writes_nothing_then_create_is_one_doctor_one_account(self):
        preview = self.run_import()
        self.assertEqual((preview['validas'], preview['rows'][0]['estado']), (1, 'crear'))
        self.assertEqual(db_operators.operators_list(), [])
        self.assertEqual(db_catalog.catalog_list('medicos'), [])
        self.send.assert_not_called()
        result = self.run_import(dry=False)
        self.assertEqual(result['creadas'], 1)
        row = result['rows'][0]
        account = db_operators.operator_by_doctor_id(row['medico_id'])
        self.assertEqual((account['rol'], account['usuario']), ('medico', row['usuario']))
        self.assertEqual(len([o for o in db_operators.operators_list() if o.get('medico_id') == row['medico_id']]), 1)
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[0], 'ana@example.test')
        self.assertNotIn('token=', str(result))

    def test_repeat_creates_nothing_twice(self):
        self.run_import(dry=False)
        again = self.run_import(dry=False)
        self.assertEqual((again['creadas'], again['existentes']), (0, 1))
        self.assertEqual(again['rows'][0]['estado'], 'existente')
        self.assertEqual(len(db_operators.operators_list()), 1)
        self.send.assert_called_once()

    def test_no_email_row_hands_back_a_link_and_sends_nothing(self):
        result = self.run_import(ROW_NO_EMAIL, dry=False)
        self.assertEqual(result['creadas'], 1)
        link = result['rows'][0]['enlace']
        self.assertIn('/admin/reset-password?token=', link)
        self.send.assert_not_called()
        self.assertIsNotNone(db_auth.admin_reset_token_consume(link.split('token=', 1)[1], 'Synthetic-pass-1'))

    def test_bad_rows_do_not_stop_the_others(self):
        rows = ROW + ROW + ROW_NO_EMAIL + 'Cris Prueba,Clínica médica,Centro de prueba,bad\n'
        rows += 'Dora Prueba,Magia,Centro de prueba,\n' + 'Eva Prueba,Clínica médica,Nowhere,\n'
        rows += '=SECRET,Clínica médica,Centro de prueba,\n' + ',Clínica médica,Centro de prueba,\n'
        result = self.run_import(rows, dry=False)
        self.assertEqual((result['creadas'], result['errores']), (2, 6))
        self.assertEqual([r['fila'] for r in result['rows']], [2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(db_operators.operators_list().__len__(), 2)

    def test_a_pasted_list_without_header_creates_incomplete_doctors(self):
        text = 'Gala;Prueba;gala@example.test\nHugo Prueba\n'
        result = importer.import_file(text.encode(), 'lista.txt', False)
        self.assertEqual((result['creadas'], result['errores']), (2, 0), result)
        doctors = {d['doc']['nombre']: d['doc'] for d in db_catalog.catalog_list('medicos')}
        self.assertEqual((doctors['Gala Prueba']['nombre_pila'], doctors['Gala Prueba']['apellido']),
                         ('Gala', 'Prueba'))
        self.assertIn('Hugo Prueba', doctors)
        self.assertIn('enlace', result['rows'][1])
        self.send.assert_called_once()
        again = importer.import_file(text.encode(), 'lista.txt', False)
        self.assertEqual((again['creadas'], again['existentes']), (0, 2))

    def test_another_doctors_email_is_refused(self):
        self.run_import(dry=False)
        other = 'Otra Persona,Clínica médica,Centro de prueba,ana@example.test\n'
        result = self.run_import(other, dry=False)
        self.assertEqual((result['creadas'], result['errores']), (0, 1))
        self.assertEqual(len(db_operators.operators_list()), 1)

    def test_a_failure_in_one_row_is_private_and_leaves_no_half_doctor(self):
        with patch.object(db_operators, 'operator_create', side_effect=RuntimeError('PRIVATE')):
            result = self.run_import(dry=False)
        self.assertEqual(result['errores'], 1)
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(db_catalog.catalog_list('medicos'), [])
        self.assertEqual(db_operators.operators_list(), [])

    def test_endpoint_roles_limit_and_preview_default(self):
        client = TestClient(app.app)
        path = '/api/admin/doctors/import?filename=test.csv'
        body = (HEADER + ROW).encode()
        self.assertEqual(client.post(path, content=body).status_code, 401)
        for role in ('medico', 'admisionista', 'reportes'):
            db_operators.operator_create(role + '-test', 'test-only-pass', role)
            client.cookies.set('st_admin', admin_auth._mint_token(role + '-test', role))
            self.assertEqual(client.post(path, content=body).status_code, 403, role)
        client.cookies.set('st_admin', admin_auth._mint_token('admin', 'admin'))
        # The old import page is «Cargar lista» in Médicos now (2026-11-03).
        old = client.get('/admin/users/import', follow_redirects=False)
        self.assertEqual((old.status_code, old.headers['location']), (308, '/admin/doctors?import=1'))
        self.assertEqual(client.post(path, content=b'x' * (importer.MAX_BYTES + 1)).status_code, 413)
        response = client.post(path, content=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()['dry_run'], response.json()['validas']), (True, 1))
        self.assertEqual(db_catalog.catalog_list('medicos'), [])
        created = client.post(path + '&dry_run=false', content=body)
        self.assertEqual(created.json()['creadas'], 1)


if __name__ == '__main__':
    unittest.main()
