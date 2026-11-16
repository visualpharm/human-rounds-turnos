"""Synthetic regressions for the doctor's first-login and portrait workflow."""
import base64
import io
import os
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL='', TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1')

from PIL import Image, ImageStat
from app.core import permissions, screen_nav
from app.admin_panel import admin_navigation
from app.patient_web import doctor_photos


def image_bytes(color=(25, 25, 25), size=(120, 200), exif=None):
    buf = io.BytesIO()
    kwargs = {'exif': exif} if exif else {}
    Image.new('RGB', size, color).save(buf, format='JPEG', **kwargs)
    return buf.getvalue()


class TestDoctorEntry(unittest.TestCase):
    def test_medico_keeps_request_data_access_by_default(self):
        self.assertIn('solicitudes', permissions.BUILTIN_ROLES['medico']['permissions'])
        self.assertIn('solicitudes', permissions.BUILTIN_ROLES['admisionista']['permissions'])
        self.assertIn('solicitudes', permissions.BUILTIN_ROLES['admin']['permissions'])

    def test_navigation_has_own_agendas_without_requests(self):
        items = screen_nav.nav_items('medico')
        self.assertNotIn('/admin/solicitudes', [x['href'] for x in items])
        self.assertIn('/admin/my-schedules', [x['href'] for x in items])

    def test_first_login_precedes_saved_or_explicit_screen(self):
        with patch('app.core.db_operators.operator_get', return_value={'medico_id': 'doctor', 'onboarding_completed_at': None}), patch.object(permissions, 'access_for', side_effect=Exception('offline')):
            self.assertEqual(admin_navigation.resolve_target({'rol': 'medico', 'usuario': 'doctor'}, '/admin/cola'), '/admin/onboarding')

    def test_completed_login_uses_normal_landing(self):
        with patch('app.core.db_operators.operator_get', return_value={'medico_id': 'doctor', 'onboarding_completed_at': '2026-10-14'}), patch.object(permissions, 'access_for', side_effect=Exception('offline')):
            self.assertEqual(admin_navigation.resolve_target({'rol': 'medico', 'usuario': 'doctor'}, '/admin/cola'), '/admin/cola')

    def test_requests_permission_does_not_restore_doctor_nav_or_landing(self):
        held = frozenset(permissions.BUILTIN_ROLES['medico']['permissions'])
        with patch('app.core.db_operators.operator_get', return_value={'medico_id': 'doctor', 'onboarding_completed_at': '2026-10-14'}), \
                patch.object(permissions, 'access_for', return_value={'state': 'ok', 'permissions': held}), \
                patch('app.admin_panel.admin_preferences.get_items', return_value={admin_navigation.NAV_LAST_SCREEN: '/admin/solicitudes'}):
            for target in ('', '/admin/solicitudes'):
                with self.subTest(target=target):
                    self.assertEqual(admin_navigation.resolve_target({'rol': 'medico', 'usuario': 'doctor'}, target), '/admin/cola')
            self.assertIsNone(admin_navigation.saved_path_for_role('medico', '/admin/solicitudes', held))
            self.assertNotIn('/admin/solicitudes', [item['href'] for item in screen_nav.nav_items('medico', permissions=held)])

    def test_center_schedules_include_service_owner(self):
        from app.admin_panel import doctor_workspace
        doctor = {'id': 'doctor', 'centro': 'ostende', 'cluster': 'general', 'centros': [{'centro': 'ostende', 'franjas': []}, {'centro': 'hospital', 'franjas': [{'dias': [5], 'desde': '09:00', 'hasta': '12:00', 'cada_min': 20}]}]}
        service = {'id': 'general', 'centro': 'ostende', 'cluster': 'general', 'nombre': 'Consultorio general', 'agenda': {'dias': [2], 'desde': '08:00', 'hasta': '10:00', 'cada_min': 20}}
        rows = doctor_workspace.schedule_rows(doctor, [service], [{'slug': 'ostende', 'name': 'Ostende'}, {'slug': 'hospital', 'name': 'Hospital'}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['franjas'][0]['dias'], [2])
        self.assertFalse(rows[0]['editable'])
        # Slice4 currently makes a managed doctor's whole editor read-only.
        self.assertFalse(rows[1]['editable'])


class TestPortraitPipeline(unittest.TestCase):
    def test_square_dark_image_gets_lighter_and_input_stays_identical(self):
        from app.patient_web.portrait_processing import portrait
        raw = image_bytes()
        before = bytes(raw)
        output = portrait(raw, enhance=True)
        self.assertEqual(raw, before)
        with Image.open(io.BytesIO(output)) as result:
            self.assertEqual(result.size, (512, 512))
            self.assertGreater(ImageStat.Stat(result).mean[0], 25)

    def test_exif_transpose_and_user_rotation(self):
        from app.patient_web.portrait_processing import oriented
        exif = Image.Exif(); exif[274] = 6
        raw = image_bytes(size=(80, 120), exif=exif)
        self.assertEqual(oriented(raw, 0).size, (120, 80))
        self.assertEqual(oriented(raw, 90).size, (80, 120))

    def test_upload_preserves_private_original_and_can_restore(self):
        raw = image_bytes()
        with tempfile.TemporaryDirectory() as folder, patch.object(doctor_photos, 'PHOTOS_DIR', folder), patch.object(doctor_photos, '_doc', return_value={'id': 'doctor'}):
            result = doctor_photos.upload_payload('doctor', {'data_b64': base64.b64encode(raw).decode()})
            self.assertEqual(result['status'], 'procesada')
            self.assertEqual(doctor_photos.original_bytes('doctor'), raw)
            self.assertNotEqual(doctor_photos.photo_bytes('doctor')[0], raw)
            restored = doctor_photos.upload_payload('doctor', {'action': 'restore'})
            self.assertEqual(restored['status'], 'original')
            self.assertEqual(doctor_photos.original_bytes('doctor'), raw)
            reprocessed = doctor_photos.upload_payload('doctor', {'action': 'process', 'rotation': 90})
            self.assertEqual(reprocessed['status'], 'procesada')
            self.assertEqual(doctor_photos.original_bytes('doctor'), raw)

    def test_invalid_image_does_not_replace_existing_photo(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(doctor_photos, 'PHOTOS_DIR', folder), patch.object(doctor_photos, '_doc', return_value={'id': 'doctor'}):
            with self.assertRaises(doctor_photos.DoctorPhotoError):
                doctor_photos.upload_payload('doctor', {'data_b64': base64.b64encode(b'\xff\xd8\xffgarbage').decode()})

    def test_setting_off_uses_resized_original(self):
        from app.patient_web.portrait_processing import portrait
        raw = image_bytes()
        with Image.open(io.BytesIO(portrait(raw, enhance=False))) as result:
            self.assertLess(abs(ImageStat.Stat(result).mean[0] - 25), 3)

    def test_failed_publish_keeps_original_and_public_copy_together(self):
        raw=image_bytes()
        with tempfile.TemporaryDirectory() as folder, patch.object(doctor_photos,'PHOTOS_DIR',folder), patch.object(doctor_photos,'_doc',return_value={'id':'doctor'}):
            doctor_photos.upload_payload('doctor',{'data_b64':base64.b64encode(raw).decode()})
            old_public=doctor_photos.photo_bytes('doctor')[0]
            real_replace=os.replace
            def fail_public(src,dest):
                if str(dest).endswith('doctor.webp'):
                    raise OSError('disk failure')
                return real_replace(src,dest)
            with patch.object(doctor_photos.os,'replace',side_effect=fail_public), self.assertRaises(doctor_photos.DoctorPhotoError):
                doctor_photos.upload_payload('doctor',{'data_b64':base64.b64encode(image_bytes((150,150,150))).decode()})
            self.assertEqual(doctor_photos.original_bytes('doctor'),raw)
            self.assertEqual(doctor_photos.photo_bytes('doctor')[0],old_public)

    def test_legacy_upload_is_preserved_when_reprocessed(self):
        raw=image_bytes()
        with tempfile.TemporaryDirectory() as folder,patch.object(doctor_photos,'PHOTOS_DIR',folder),patch.object(doctor_photos,'_doc',return_value={'id':'doctor'}):
            with open(os.path.join(folder,'doctor.jpg'),'wb') as dest: dest.write(raw)
            result=doctor_photos.upload_payload('doctor',{'action':'process'})
            self.assertEqual(result['status'],'procesada')
            self.assertEqual(doctor_photos.original_bytes('doctor'),raw)

    def test_site_setting_controls_actual_upload_and_invalid_setting_writes_nothing(self):
        from app.admin_panel import settings_admin
        with patch.object(settings_admin.config,'guardar') as save:
            result,error=settings_admin._guardar_turnos({'TURNOS_PHOTO_AUTO_ENHANCE':'invalid'})
            self.assertIsNone(result);self.assertTrue(error);save.assert_not_called()
        raw=image_bytes()
        with tempfile.TemporaryDirectory() as folder,patch.object(doctor_photos,'PHOTOS_DIR',folder),patch.object(doctor_photos,'_doc',return_value={'id':'doctor'}),patch('app.core.config.get',side_effect=lambda key,default=None: '0' if key=='TURNOS_PHOTO_AUTO_ENHANCE' else default):
            result=doctor_photos.upload_payload('doctor',{'data_b64':base64.b64encode(raw).decode()})
            self.assertEqual(result['status'],'original')
            with Image.open(io.BytesIO(doctor_photos.photo_bytes('doctor')[0])) as portrait:
                self.assertLess(abs(ImageStat.Stat(portrait).mean[0]-25),3)
            self.assertEqual(doctor_photos.original_bytes('doctor'),raw)

    def test_remove_also_removes_private_original_bundles(self):
        raw=image_bytes()
        with tempfile.TemporaryDirectory() as folder,patch.object(doctor_photos,'PHOTOS_DIR',folder),patch.object(doctor_photos,'_doc',return_value={'id':'doctor'}):
            doctor_photos.upload_payload('doctor',{'data_b64':base64.b64encode(raw).decode()})
            doctor_photos.remove_payload('doctor')
            private=os.path.join(folder,'.originals','doctor')
            self.assertEqual(os.listdir(private),['lock'])
            with self.assertRaises(doctor_photos.DoctorPhotoError):doctor_photos.original_bytes('doctor')


class TestWorkspaceTransport(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.auth import admin_auth
        from app import main as app
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)
        for target, value in [
                ('app.auth.admin_auth._verify_token', lambda token: {'usuario': token, 'rol': token} if token in ('medico','admin','admisionista','reportes') else None),
                ('app.auth.admin_auth.account_security', lambda *args, **kwargs: {'tiene_2fa': True, '2fa_deadline': None, '2fa_vencido': False}),
                ('app.core.permissions.access_for', lambda user, role: {'slug': role, 'state': 'ok', 'permissions': frozenset(permissions.BUILTIN_ROLES[role]['permissions'])}),
                ('app.core.db_operators.operator_doctor_id', lambda usuario: 'doctor' if usuario == 'medico' else None)]:
            mocked = patch(target, side_effect=value); mocked.start(); self.addCleanup(mocked.stop)

    def test_own_photo_allowed_and_foreign_or_center_photo_denied(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(doctor_photos, 'PHOTOS_DIR', folder), patch.object(doctor_photos, '_doc', return_value={'id':'doctor'}):
            raw = image_bytes()
            body = {'data_b64': base64.b64encode(raw).decode()}
            own = self.client.post('/api/admin/doctors/doctor/photo', json=body, cookies={'st_admin':'medico'})
            self.assertEqual(own.status_code, 200, own.text)
            self.assertEqual(own.json()['status'], 'procesada')
            for path in ('/api/admin/doctors/foreign/photo', '/api/admin/centers/doctor/photo'):
                for method in ('GET', 'POST', 'DELETE'):
                    response = self.client.request(method, path, json=body if method=='POST' else None, cookies={'st_admin':'medico'})
                    self.assertIn(response.status_code, (403,404))
            self.assertEqual(doctor_photos.original_bytes('doctor'), raw)
            # The public route cannot resolve the private original's path.
            self.assertIn(self.client.get('/doctor-photo/.originals/doctor/original').status_code, (403,404))


    def test_original_preview_preserves_bytes_and_requires_own_identity(self):
        raw = image_bytes()
        with tempfile.TemporaryDirectory() as folder, patch.object(doctor_photos,'PHOTOS_DIR',folder), patch.object(doctor_photos,'_doc',return_value={'id':'doctor'}):
            doctor_photos.upload_payload('doctor',{'data_b64':base64.b64encode(raw).decode()})
            before=doctor_photos.photo_bytes('doctor')[0]
            response=self.client.get('/api/admin/doctors/doctor/photo?original=1',cookies={'st_admin':'medico'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.content,raw)
            self.assertEqual(response.headers['content-type'],'image/jpeg')
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(doctor_photos.photo_bytes('doctor')[0],before)
            for path in ('/api/admin/doctors/foreign/photo?original=1','/api/admin/centers/doctor/photo?original=1'):
                self.assertIn(self.client.get(path,cookies={'st_admin':'medico'}).status_code,(403,404))
            self.assertEqual(self.client.get('/api/admin/doctors/doctor/photo?original=1').status_code,401)

    def test_completion_acknowledges_persisted_operator_only(self):
        from app.admin_panel import doctor_workspace
        with patch.object(doctor_workspace, 'onboarding_missing', return_value=['email']), \
                patch('app.core.db_operators.operator_complete_onboarding') as save:
            response = self.client.post('/api/admin/onboarding/complete', cookies={'st_admin':'medico'})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['faltan'], ['email'])
            save.assert_not_called()
        self.enterContext(patch.object(doctor_workspace, 'onboarding_missing', return_value=[]))
        with patch('app.core.db_operators.operator_complete_onboarding', return_value=True) as save:
            response = self.client.post('/api/admin/onboarding/complete', cookies={'st_admin':'medico'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['ok']); save.assert_called_once_with('medico')
        with patch('app.core.db_operators.operator_complete_onboarding', return_value=False):
            response = self.client.post('/api/admin/onboarding/complete', cookies={'st_admin':'medico'})
            self.assertEqual(response.status_code, 400)
        with patch('app.core.db_operators.operator_complete_onboarding') as save:
            for role in ('admin','admisionista','reportes'):
                self.assertEqual(self.client.post('/api/admin/onboarding/complete',cookies={'st_admin':role}).status_code,403)
            save.assert_not_called()

    def test_completion_routes_to_the_security_step_without_totp(self):
        # Sin TOTP vigente (la política actual cuenta sólo el TOTP) el alta
        # manda al paso de seguridad de la cuenta; con 2FA activa conserva el
        # destino normal del rol. La lectura de estado nunca sustituye al alta.
        # En portal sin nuestro TOTP el server marca «2fa_no_aplica»: el portal
        # es el segundo factor, así que el alta NO entra al paso de seguridad.
        from app.admin_panel import admin_navigation, doctor_workspace
        # Como su prueba hermana de arriba: el alta de onboarding se prueba en
        # el nivel de transporte, con los datos del médico ya completos.
        self.enterContext(patch.object(doctor_workspace, 'onboarding_missing', return_value=[]))
        with patch('app.core.db_operators.operator_complete_onboarding', return_value=True), \
                patch('app.auth.admin_auth.account_security', return_value={'tiene_2fa': False, '2fa_deadline': None, '2fa_vencido': False}):
            response = self.client.post('/api/admin/onboarding/complete', cookies={'st_admin':'medico'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['next'], '/admin/account')
        with patch('app.core.db_operators.operator_complete_onboarding', return_value=True), \
                patch('app.auth.admin_auth.account_security', return_value={'tiene_2fa': False, '2fa_deadline': None, '2fa_vencido': False, '2fa_no_aplica': True}):
            response = self.client.post('/api/admin/onboarding/complete', cookies={'st_admin':'medico'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['next'], admin_navigation.default_path_for_role('medico') or '/admin/account')
        with patch('app.core.db_operators.operator_complete_onboarding', return_value=True):
            response = self.client.post('/api/admin/onboarding/complete', cookies={'st_admin':'medico'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['next'], admin_navigation.default_path_for_role('medico') or '/admin/account')

    def test_requests_doctor_reachable_even_when_permission_store_down(self):
        from app.core import db
        for unavailable in (False, True):
            with patch.object(permissions,'access_for', side_effect=db.DbUnavailable('offline')) if unavailable else nullcontext():
                response = self.client.get('/admin/solicitudes', cookies={'st_admin':'medico'})
            self.assertEqual(response.status_code,200)
            self.assertNotIn('data-nav="solicitudes"', response.text)

    def test_medico_nav_hides_requests_but_api_returns_only_own_scope(self):
        from app.core import db
        from app.core import db_clinical
        from app.modules.turnos import booking_requests, doctor_appointments, patient_ranking
        from app.modules.turnos import booking_queue
        rows = [
            {'id': 1, 'dni': '11111111', 'medico_asignado_id': 'doctor', 'estado': 'pendiente'},
            {'id': 2, 'dni': '22222222', 'medico_asignado_id': 'other', 'estado': 'pendiente'},
        ]
        held = frozenset(permissions.BUILTIN_ROLES['medico']['permissions'])
        for unavailable in (False, True):
            with self.subTest(permission_store_down=unavailable):
                # Exercise the real ASGI gate and the real doctor's row filter;
                # only persistence and auxiliary projections are synthetic.
                permission_store = (patch.object(permissions, 'access_for', side_effect=db.DbUnavailable('offline'))
                                    if unavailable else nullcontext())
                with permission_store, \
                        patch.object(booking_requests.store, 'list_admin', return_value=rows), \
                        patch.object(booking_requests.store, 'count_admin', return_value=2), \
                        patch.object(doctor_appointments, 'doctor_of', return_value={'id': 'doctor', 'nombre': 'Doctor Example'}), \
                        patch.object(booking_requests, '_admin_row', side_effect=dict), \
                        patch.object(booking_requests.store, 'attendance_summary_by_dnis', return_value={}), \
                        patch.object(patient_ranking, 'by_dnis', return_value={}), \
                        patch.object(booking_queue, '_approved_by_doctor_art', return_value={}), \
                        patch.object(booking_queue, '_suggested_doctor', return_value=None), \
                        patch.object(db_clinical, 'insert_clinical_access_many'), \
                        patch('app.auth.admin_auth.centros_permitidos', return_value=None):
                    self.assertNotIn('/admin/solicitudes', [item['href'] for item in screen_nav.nav_items('medico', permissions=None if unavailable else held)])
                    response = self.client.get('/api/admin/requests', cookies={'st_admin': 'medico'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([row['id'] for row in response.json()['solicitudes']], [1])

    def test_onboarding_phone_has_day_controls_and_service_readonly(self):
        from app.admin_panel import doctor_workspace
        base = {'doctors':[{'id':'doctor','nombre':'Doctor Example','especialidad':'General'}], 'first_name':'Doctor','last_name':'Example', 'editable':True,
                'specialties':[{'nombre':'General'}], 'days':[{'id':d,'key':'day.'+str(d),'label':str(d)} for d in (1,2,3,4,5,6,0)],
                'schedules':[{'centro':'ostende','center':'Ostende','doctor_id':'doctor','consultorio':'','franjas':[],'editable':True}],
                'photo':{'style':"background-image:url('/doctor-photo/doctor?v=123')",'has_upload':True,'has_original':True,'status':'procesada'}}
        with patch.object(doctor_workspace,'workspace_data',return_value=base):
            response=self.client.get('/admin/onboarding',cookies={'st_admin':'medico'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.text.count('data-day="'),7)
        self.assertIn('Ahora ingresá tu agenda',response.text)
        self.assertRegex(response.text,r'<div data-photo-preview[^>]+style="[^"]*/doctor-photo/doctor')
        self.assertNotRegex(response.text,r'<div data-photo-preview[^>]+ hidden')
        self.assertIn('href="/admin/cola"',response.text)
        self.assertNotIn('data-nav="solicitudes"',response.text)
        base['schedules'][0].update(editable=False,franjas=[{'dias':[2,5],'desde':'08:00','hasta':'12:00'}],message='El horario ya está definido para el servicio')
        base['editable']=False
        with patch.object(doctor_workspace,'workspace_data',return_value=base):
            response=self.client.get('/admin/onboarding',cookies={'st_admin':'medico'})
        self.assertNotIn('data-day="',response.text)
        self.assertIn('08:00',response.text)
        self.assertIn('El horario ya está definido',response.text)

    def test_onboarding_asks_email_specialty_and_center_of_an_invited_doctor(self):
        # Médico = usuario (responsable del producto, 2026-10-19): invited with every field empty,
        # the onboarding asks for the email, the specialty and where they work.
        from app.admin_panel import doctor_workspace
        base = {'doctors':[{'id':'medico-ab12','nombre':'','incompleto':True,'centros':[]}], 'first_name':'','last_name':'', 'editable':False,
                'specialties':[{'nombre':'General'}], 'days':[{'id':d,'key':'day.'+str(d),'label':str(d)} for d in (1,2,3,4,5,6,0)],
                'schedules':[], 'photo':None, 'needs_center':True,
                'center_options':[{'slug':'ostende','name':'CAPS Ostende'},{'slug':'hospital','name':'Hospital'}]}
        with patch.object(doctor_workspace,'workspace_data',return_value=base):
            response=self.client.get('/admin/onboarding',cookies={'st_admin':'medico'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertRegex(response.text,r'<input type="email" name="email"[^>]* required>')
        self.assertRegex(response.text,r'<select name="especialidad" required>\s*<option value="" selected>')
        self.assertRegex(response.text,r'<select name="centro" data-center-select required>')
        self.assertIn('<option value="ostende">CAPS Ostende</option>',response.text)
        self.assertIn('data-center-new',response.text)
        self.assertEqual(response.text.count('data-day="'),7)
        base['needs_center']=False
        with patch.object(doctor_workspace,'workspace_data',return_value=base):
            response=self.client.get('/admin/onboarding',cookies={'st_admin':'medico'})
        self.assertNotIn('data-center-new',response.text)

    def test_schedule_screen_role_matrix(self):
        from app.admin_panel import doctor_workspace
        with patch.object(doctor_workspace,'workspace_data',return_value={'doctors':[], 'schedules':[]}):
            for role, status in [('medico',200),('admin',200),('admisionista',403),('reportes',403)]:
                self.assertEqual(self.client.get('/admin/my-schedules',cookies={'st_admin':role}).status_code,status)

    def test_my_schedules_service_note_is_a_direct_child_of_the_bare_form(self):
        # Phone alignment (audit 2026-10-21): at 390px the bare form's note
        # started at x0 while the h1 and the dw-center cards kept their 16px
        # gutter. The fix is a structural contract — the note must be a direct
        # child of the non-card schedule form, which the <=720px gutter rule
        # (.dw-workspace>form:not(.dw-card)>p{padding-left:16px;padding-right:16px})
        # then aligns; onboarding keeps the dw-card form and its own padding.
        from app.admin_panel import doctor_workspace
        base = {'doctors':[{'id':'doctor','nombre':'Doctor Example','especialidad':'General'}], 'editable':False,
                'days':[{'id':d,'key':'day.'+str(d),'label':str(d)} for d in (1,2,3,4,5,6,0)],
                'schedules':[{'centro':'ostende','center':'Ostende','doctor_id':'doctor','consultorio':'',
                              'franjas':[{'dias':[2,5],'desde':'08:00','hasta':'12:00'}],'editable':False,
                              'message':'El horario ya está definido para el servicio'}]}
        with patch.object(doctor_workspace,'workspace_data',return_value=base):
            response=self.client.get('/admin/my-schedules',cookies={'st_admin':'medico'})
        self.assertEqual(response.status_code,200,response.text)
        # Mis agendas (the product owner, 2026-10-27): the note now lives INSIDE the card
        # of the service-owned schedule it explains, on the card's own left
        # edge — said once, next to the hours it is about.
        card = response.text[response.text.index('class="dw-center dw-card"'):]
        card = card[:card.index('</section>')]
        self.assertIn('<p>El horario ya está definido para el servicio</p>', card)
        self.assertEqual(response.text.count('El horario ya está definido para el servicio'), 1)
        self.assertIn('dw-workspace--agendas', response.text)

    def test_admin_sees_every_agenda_as_one_list_and_the_doctor_keeps_cards(self):
        # Critical review 08.11, D1 (the product owner R29 «it shows it in the boxes», R33
        # «way less bounding boxes»): with several doctors the admin reads one
        # table, a row per agenda, the doctor named once per run of rows and
        # linked to the doctor's card; the doctor's own view keeps its cards
        # (decision 27.10).
        from app.admin_panel import doctor_workspace
        band = {'dias': [1, 3], 'desde': '08:00', 'hasta': '12:00'}
        rows = [
            {'centro': 'ostende', 'center': 'Ostende', 'doctor_id': 'ana', 'doctor_name': 'Ana Paz', 'consultorio': '', 'franjas': [band], 'editable': False},
            {'centro': 'valeria', 'center': 'Valeria', 'doctor_id': 'ana', 'doctor_name': 'Ana Paz', 'consultorio': '', 'franjas': [band], 'editable': False},
            {'centro': 'ostende', 'center': 'Ostende', 'doctor_id': 'beto', 'doctor_name': 'Beto Sol', 'consultorio': '', 'franjas': [band], 'editable': False},
        ]
        base = {'doctors': [{'id': 'ana', 'nombre': 'Ana Paz'}, {'id': 'beto', 'nombre': 'Beto Sol'}], 'editable': False,
                'days': [{'id': d, 'key': 'day.' + str(d), 'label': str(d)} for d in (1, 2, 3, 4, 5, 6, 0)],
                'schedules': rows}
        with patch.object(doctor_workspace, 'workspace_data', return_value=base):
            admin = self.client.get('/admin/my-schedules', cookies={'st_admin': 'admin'})
            doctor = self.client.get('/admin/my-schedules', cookies={'st_admin': 'medico'})
        self.assertEqual(admin.status_code, 200, admin.text)
        self.assertIn('<table class="dw-list" data-agenda-list>', admin.text)
        self.assertNotIn('dw-card', admin.text)
        self.assertEqual(admin.text.count('<tr class="dw-same"'), 1)
        self.assertEqual(admin.text.count('href="/admin/doctors?item=ana"'), 1)
        self.assertEqual(admin.text.count('href="/admin/doctors?item=beto"'), 1)
        self.assertEqual(doctor.status_code, 200, doctor.text)
        self.assertNotIn('data-agenda-list', doctor.text)
        self.assertIn('dw-card', doctor.text)

    def test_unavailable_onboarding_keeps_its_reload_control_interactive(self):
        from app.admin_panel import doctor_workspace
        for data in (None, {'doctors': [], 'schedules': []}):
            with self.subTest(data=data), patch.object(doctor_workspace, 'workspace_data', return_value=data):
                response = self.client.get('/admin/onboarding', cookies={'st_admin': 'medico'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('id="doctor-workspace" inert', response.text)


class TestOwnProfileSave(unittest.TestCase):
    def test_profile_uses_validator_and_preserves_admin_owned_fields(self):
        from app.admin_panel import admin_content
        doctor={'id':'doctor','nombre':'Doctor Example','nombre_pila':'Doctor','apellido':'Example','especialidad':'General','cluster':'general','centro':'ostende',
                'centros':[{'centro':'ostende','franjas':[],'excepciones':[]}], 'matricula':'1234','turnos_activos_max':2}
        def items(tipo):
            return [doctor] if tipo=='medicos' else []
        with patch('app.core.db_catalog.catalog_schedule_lock',return_value=nullcontext(None)), patch('app.core.db_operators.operator_doctor_id',return_value='doctor'), \
             patch('app.core.db_catalog.catalog_get',return_value=None), patch('app.core.db_catalog.catalog_upsert') as write, \
             patch('app.patient_web.catalog.admin_list',side_effect=items),patch('app.patient_web.catalog.services',return_value=[]), \
             patch('app.patient_web.catalog.centers',return_value=[{'slug':'ostende'}]), patch('app.patient_web.catalog.specialties',return_value=[{'nombre':'General','cluster':'general'}]), \
             patch('app.modules.turnos.doctor_appointments.compatible_services_data',return_value={'items':[]}), \
             patch('app.admin_panel.admin_users.doctor_email_changed') as account_sync:
            result=admin_content.edit_item('medicos','doctor',{'nombre_pila':'Ana','apellido':'Example','especialidad':'General','email':'ANA@example.org','telefono':'2254 111111','matricula':'9999','turnos_activos_max':60}, {'usuario':'medico','rol':'medico'})
            saved=write.call_args.args[2]
            self.assertEqual(saved['nombre'],'Ana Example')
            self.assertEqual(saved['email'],'ana@example.org')
            account_sync.assert_called_once()
            self.assertEqual(saved['matricula'],'1234')
            self.assertEqual(saved['turnos_activos_max'],2)
            with self.assertRaises(admin_content.ContentError):
                admin_content.edit_item('medicos','doctor',{'email':'invalid'}, {'usuario':'medico','rol':'medico'})
            with self.assertRaises(admin_content.ContentError):
                admin_content.edit_item('medicos','foreign',{}, {'usuario':'medico','rol':'medico'})
