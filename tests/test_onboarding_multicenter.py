"""Multi-center onboarding (2026-10-21): every assigned center edits its own
schedule with a per-band room, other network centers stay visible with the
admin next step, and the self-service save still cannot grant a new center."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL='', TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1')

from app.core import permissions

CENTERS = [{'slug': 'hospital', 'name': 'Hospital de Pinamar'},
           {'slug': 'ostende', 'name': 'Ostende'},
           {'slug': 'valeria', 'name': 'Valeria del Mar'}]

# Pool services carry the room but no schedule of their own — an unscheduled
# pool service never claims the doctor's hours (schedule_policy.has_schedule).
# SCHEDULED is the one service that owns its hours and makes a form read-only.
SERVICES = [
    {'id': 'general-h', 'centro': 'hospital', 'cluster': 'general', 'nombre': 'Clínica general',
     'consultorio': 'Consultorio 1'},
    {'id': 'derma-h', 'centro': 'hospital', 'cluster': 'general', 'nombre': 'Dermatología',
     'consultorio': 'Consultorio 3'},
    {'id': 'pediatric-h', 'centro': 'hospital', 'cluster': 'pediatria', 'nombre': 'Pediatría',
     'consultorio': 'Consultorio 2'},
    {'id': 'general-o', 'centro': 'ostende', 'cluster': 'general', 'nombre': 'Clínica general',
     'consultorio': 'Sala 5'},
    # A legacy agenda tag pointing here is outside this doctor's pool (other
    # cluster, other center) yet still offerable on the doctor's own bands.
    {'id': 'old-tag', 'centro': 'hospital', 'cluster': 'cardiologia', 'nombre': 'Tag viejo',
     'consultorio': 'Sala 7'},
    {'id': 'gone-o', 'centro': 'ostende', 'cluster': 'general', 'nombre': 'Borrado',
     'consultorio': 'Sala 9', '_borrado': True},
]
SCHEDULED = [{'id': 'managed-h', 'centro': 'hospital', 'cluster': 'general', 'nombre': 'Clínica general',
              'consultorio': 'Consultorio 1',
              'agenda': {'dias': [2], 'desde': '08:00', 'hasta': '10:00', 'cada_min': 20}}]

TWO_CENTERS = {'id': 'doctor', 'nombre': 'Ana Example', 'especialidad': 'General', 'centro': 'hospital', 'cluster': 'general',
               'centros': [{'centro': 'hospital', 'franjas': [{'dias': [1], 'desde': '08:00', 'hasta': '12:00',
                                                                'cada_min': 20, 'servicio': 'general-h'}]},
                           {'centro': 'ostende', 'franjas': []}]}


class TestScheduleRowsMultiCenter(unittest.TestCase):
    def test_each_assigned_center_is_editable_with_its_own_rooms(self):
        from app.admin_panel import doctor_workspace
        rows = doctor_workspace.schedule_rows(TWO_CENTERS, SERVICES, CENTERS)
        self.assertEqual([r['centro'] for r in rows], ['hospital', 'ostende'])
        self.assertTrue(all(r['editable'] for r in rows))
        # Own tagged service first, then the rest of the pool by name; a
        # service of another cluster (pediatría), a foreign one (old-tag) and
        # the deleted one never join this general-clinic doctor's pool.
        self.assertEqual([s['id'] for s in rows[0]['servicios']], ['general-h', 'derma-h'])
        self.assertEqual(rows[0]['servicios'][0]['consultorio'], 'Consultorio 1')
        self.assertEqual([s['id'] for s in rows[1]['servicios']], ['general-o'])
        self.assertEqual(rows[1]['servicios'][0]['consultorio'], 'Sala 5')

    def test_legacy_agenda_tag_outside_today_pool_stays_offerable(self):
        from app.admin_panel import doctor_workspace
        doctor = {'id': 'doctor', 'centro': 'ostende', 'cluster': 'general',
                  'agenda': {'dias': [4], 'desde': '08:00', 'hasta': '12:00', 'servicio': 'old-tag'}}
        rows = doctor_workspace.schedule_rows(doctor, SERVICES, CENTERS)
        self.assertEqual(rows[0]['editable'], True)
        self.assertEqual([s['id'] for s in rows[0]['servicios']], ['general-o', 'old-tag'])

    def test_service_owned_center_offers_no_editable_band_services(self):
        from app.admin_panel import doctor_workspace
        doctor = {'id': 'doctor', 'centro': 'hospital', 'cluster': 'general',
                  'centros': [{'centro': 'hospital', 'franjas': []}]}
        rows = doctor_workspace.schedule_rows(doctor, SCHEDULED, CENTERS)
        self.assertFalse(rows[0]['editable'])
        self.assertEqual(rows[0]['servicios'], [])


class TestOtherCenters(unittest.TestCase):
    def test_medico_sees_unassigned_network_centers_with_names(self):
        from app.admin_panel import doctor_workspace
        with patch('app.core.db_operators.operator_doctor_id', return_value='doctor'), \
             patch('app.patient_web.catalog.admin_list', side_effect=lambda tipo: {
                 'medicos': [TWO_CENTERS], 'servicios': SERVICES, 'centros': CENTERS}[tipo]), \
             patch('app.patient_web.catalog.specialties', return_value=[]), \
             patch('app.patient_web.doctor_photos.preview_payload', return_value=None), \
             patch.object(doctor_workspace, 'upcoming_holidays', return_value={}):
            data = doctor_workspace.workspace_data({'usuario': 'medico', 'rol': 'medico'})
        self.assertEqual([c['slug'] for c in data['other_centers']], ['valeria'])
        self.assertEqual(data['other_centers'][0]['name'], 'Valeria del Mar')
        self.assertEqual([r['centro'] for r in data['schedules']], ['hospital', 'ostende'])

    def test_deleted_centers_are_never_suggested(self):
        from app.admin_panel import doctor_workspace
        centers = CENTERS + [{'slug': 'ghost', 'name': 'Ghost', '_borrado': True}]
        with patch('app.core.db_operators.operator_doctor_id', return_value='doctor'), \
             patch('app.patient_web.catalog.admin_list', side_effect=lambda tipo: {
                 'medicos': [TWO_CENTERS], 'servicios': SERVICES, 'centros': centers}[tipo]), \
             patch('app.patient_web.catalog.specialties', return_value=[]), \
             patch('app.patient_web.doctor_photos.preview_payload', return_value=None), \
             patch.object(doctor_workspace, 'upcoming_holidays', return_value={}):
            data = doctor_workspace.workspace_data({'usuario': 'medico', 'rol': 'medico'})
        self.assertEqual([c['slug'] for c in data['other_centers']], ['valeria'])

    def test_admin_reads_the_workspace_without_the_onboarding_list(self):
        from app.admin_panel import doctor_workspace
        with patch('app.patient_web.catalog.admin_list', side_effect=lambda tipo: {
                'medicos': [TWO_CENTERS], 'servicios': SERVICES, 'centros': CENTERS}[tipo]), \
             patch('app.patient_web.catalog.specialties', return_value=[]), \
             patch.object(doctor_workspace, 'upcoming_holidays', return_value={}):
            data = doctor_workspace.workspace_data({'usuario': 'admin', 'rol': 'admin'})
        self.assertEqual(data['other_centers'], [])


class TestOnboardingRender(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.auth import admin_auth
        from app import main as app
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)
        for target, value in [
                ('app.auth.admin_auth._verify_token', lambda token: {'usuario': token, 'rol': token} if token in ('medico', 'admin', 'admisionista', 'reportes') else None),
                ('app.auth.admin_auth.account_security', lambda *args, **kwargs: {'tiene_2fa': True, '2fa_deadline': None, '2fa_vencido': False}),
                ('app.core.permissions.access_for', lambda user, role: {'slug': role, 'state': 'ok', 'permissions': frozenset(permissions.BUILTIN_ROLES[role]['permissions'])}),
                ('app.core.db_operators.operator_doctor_id', lambda usuario: 'doctor' if usuario == 'medico' else None)]:
            mocked = patch(target, side_effect=value); mocked.start(); self.addCleanup(mocked.stop)

    def _data(self):
        return {'doctors': [TWO_CENTERS], 'first_name': 'Ana', 'last_name': 'Example', 'editable': True,
                'specialties': [{'nombre': 'General'}],
                'days': [{'id': d, 'key': 'admin.content.hours.day.' + key, 'name_key': 'date.day.' + key,
                          'label': label, 'name': name}
                         for d, key, label, name in zip((1, 2, 3, 4, 5, 6, 0),
                                                        ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'),
                                                        ('Lu', 'Ma', 'Mi', 'Ju', 'Vi', 'Sá', 'Do'),
                                                        ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'))],
                'schedules': [
                    {'centro': 'hospital', 'center': 'Hospital de Pinamar', 'doctor_id': 'doctor', 'consultorio': '',
                     'franjas': [{'dias': [4], 'desde': '08:00', 'hasta': '12:00', 'cada_min': 20, 'servicio': 'general-h'},
                                 {'dias': [5], 'desde': '14:00', 'hasta': '18:00', 'cada_min': 20}],
                     'editable': True,
                     'servicios': [{'id': 'general-h', 'nombre': 'Clínica general', 'consultorio': 'Consultorio 1'},
                                   {'id': 'pediatric-h', 'nombre': 'Pediatría', 'consultorio': 'Consultorio 2'}]},
                    {'centro': 'ostende', 'center': 'Ostende', 'doctor_id': 'doctor', 'consultorio': '',
                     'franjas': [], 'editable': True,
                     'servicios': [{'id': 'general-o', 'nombre': 'Clínica general', 'consultorio': 'Sala 5'}]}],
                'other_centers': [{'slug': 'valeria', 'name': 'Valeria del Mar'}]}

    def test_both_centers_render_per_day_cards_with_24h_controls_rooms_and_next_step(self):
        from app.admin_panel import doctor_workspace
        with patch.object(doctor_workspace, 'workspace_data', return_value=self._data()):
            response = self.client.get('/admin/onboarding', cookies={'st_admin': 'medico'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text.count('data-editable data-services='), 2)
        self.assertIn('data-other-centers', response.text)
        self.assertNotRegex(response.text, r'type="time"')
        self.assertRegex(response.text, r'class="dw-time" type="text" inputmode="numeric"')
        # Thursday 08:00–12:00 and Friday 14:00–18:00 are each their own card:
        # immediately visible and separately editable, with per-day add and
        # copy actions — never one grouped row with a shared time range.
        self.assertIn('class="dw-day" data-day-row="4"', response.text)
        self.assertIn('class="dw-day" data-day-row="5"', response.text)
        self.assertIn('Jueves', response.text)
        self.assertIn('Viernes', response.text)
        self.assertEqual(response.text.count('data-remove-slot'), 2)
        self.assertEqual(response.text.count('data-add-slot='), 2)
        self.assertEqual(response.text.count('data-copy-day='), 2)
        self.assertIn('data-day-pick', response.text)
        self.assertNotIn('data-add-band', response.text)
        # The chooser marks exactly the days that carry hours.
        self.assertEqual(response.text.count('data-day="4" checked'), 1)
        self.assertEqual(response.text.count('data-day="5" checked'), 1)
        self.assertEqual(response.text.count('data-day="1"'), 2)
        # The saved Thursday interval keeps its service selected and names the room.
        self.assertIn('value="general-h" selected>Clínica general · Consultorio 1', response.text)
        # The other network center is visible with the concrete admin step.
        self.assertIn('Valeria del Mar', response.text)
        # The admin step is one sentence under the list, not repeated per center.
        self.assertEqual(response.text.count('pedile a administración que lo agregue a tu perfil'), 1)

    def test_onboarding_is_medico_only(self):
        from app.admin_panel import doctor_workspace
        with patch.object(doctor_workspace, 'workspace_data', return_value=self._data()):
            for role in ('admin', 'admisionista', 'reportes'):
                self.assertEqual(self.client.get('/admin/onboarding', cookies={'st_admin': role}).status_code, 403)
            self.assertEqual(self.client.get('/admin/onboarding', cookies={'st_admin': 'medico'}).status_code, 200)


class TestMedicoSaveRules(unittest.TestCase):
    def _save(self, body, doctor=TWO_CENTERS):
        from app.admin_panel import admin_content
        def items(tipo):
            return [doctor] if tipo == 'medicos' else SERVICES
        with patch('app.core.db_catalog.catalog_schedule_lock', return_value=nullcontext(None)), \
             patch('app.core.db_operators.operator_doctor_id', return_value='doctor'), \
             patch('app.core.db_catalog.catalog_get', return_value=None), patch('app.core.db_catalog.catalog_upsert') as write, \
             patch('app.patient_web.catalog.admin_list', side_effect=items), \
             patch('app.patient_web.catalog.services', return_value=SERVICES), \
             patch('app.patient_web.catalog.centers', return_value=CENTERS), \
             patch('app.patient_web.catalog.specialties', return_value=[{'nombre': 'General', 'cluster': 'general'}]), \
             patch('app.modules.turnos.doctor_appointments.compatible_services_data', return_value={'items': []}):
            admin_content.edit_item('medicos', 'doctor', body, {'usuario': 'medico', 'rol': 'medico'})
        return write.call_args.args[2]

    def test_new_center_in_save_is_rejected_not_granted(self):
        from app.admin_panel import admin_content
        self_service = TWO_CENTERS['centros'] + [{'centro': 'valeria', 'franjas': []}]
        with self.assertRaises(admin_content.ContentError) as denied:
            self._save({'centros': self_service})
        self.assertEqual(denied.exception.status, 403)
        self.assertEqual(str(denied.exception), 'centro_no_permitido')

    def test_invitation_without_center_keeps_its_initial_catalog_choice(self):
        empty = dict(TWO_CENTERS, centro=None, centros=[])
        first = [{'centro': 'valeria', 'franjas': [
            {'dias': [4], 'desde': '08:00', 'hasta': '12:00', 'cada_min': 20}]}]
        saved = self._save({'centros': first}, doctor=empty)
        self.assertEqual(saved['centros'], [dict(first[0], excepciones=[])])
        # The initial choice never turns into permission to add more centers.
        from app.admin_panel import admin_content
        with self.assertRaises(admin_content.ContentError) as denied:
            self._save({'centros': first + [{'centro': 'hospital', 'franjas': []}]}, doctor=dict(empty, centros=first))
        self.assertEqual(denied.exception.status, 403)

    def test_same_center_overlap_in_save_is_rejected_with_the_remedy(self):
        from app.admin_panel import admin_content
        overlapping = [
            {'centro': 'hospital', 'franjas': [{'dias': [1], 'desde': '08:00', 'hasta': '12:00'},
                                               {'dias': [1], 'desde': '10:00', 'hasta': '13:00'}]},
            {'centro': 'ostende', 'franjas': []}]
        with self.assertRaises(admin_content.ContentError) as denied:
            self._save({'centros': overlapping})
        self.assertEqual(denied.exception.status, 409)
        self.assertEqual(denied.exception.payload['error'], 'agenda_superpuesta')
        self.assertIn('Hospital de Pinamar', denied.exception.payload['detalle'])
        self.assertIn('se superponen', denied.exception.payload['detalle'])

    def test_cross_center_overlap_on_one_weekday_is_rejected(self):
        from app.admin_panel import admin_content
        # One doctor, one body: Monday morning in the hospital cannot also be
        # Monday mid-morning in Ostende.
        cross = [
            {'centro': 'hospital', 'franjas': [{'dias': [1], 'desde': '08:00', 'hasta': '12:00'}]},
            {'centro': 'ostende', 'franjas': [{'dias': [1], 'desde': '10:00', 'hasta': '13:00'}]}]
        with self.assertRaises(admin_content.ContentError) as denied:
            self._save({'centros': cross})
        self.assertEqual(denied.exception.status, 409)
        self.assertEqual(denied.exception.payload['error'], 'agenda_superpuesta')
        detalle = denied.exception.payload['detalle']
        self.assertIn('Hospital de Pinamar', detalle)
        self.assertIn('Ostende', detalle)

    def test_disjoint_cross_center_split_shift_is_accepted(self):
        # Monday morning in the hospital and Monday afternoon in Ostende is a
        # real split day across the network: the save goes through.
        split = [
            {'centro': 'hospital', 'franjas': [{'dias': [1], 'desde': '08:00', 'hasta': '12:00'}]},
            {'centro': 'ostende', 'franjas': [{'dias': [1], 'desde': '14:00', 'hasta': '18:00'}]}]
        saved = self._save({'centros': split})
        self.assertEqual([c['centro'] for c in saved['centros']], ['hospital', 'ostende'])

    def test_malformed_intervals_keep_the_catalog_validation_error(self):
        from app.admin_panel import admin_content
        for invalid in ([None], {'dias': [1]}, [{'dias': 1, 'desde': '08:00', 'hasta': '12:00'}]):
            with self.subTest(invalid=invalid), self.assertRaises(admin_content.ContentError) as denied:
                self._save({'centros': [{'centro': 'hospital', 'franjas': invalid}]})
            self.assertEqual(denied.exception.status, 400)

    def test_save_then_reload_keeps_both_centers_bands_and_rooms(self):
        from app.admin_panel import doctor_workspace
        centros = [
            {'centro': 'hospital', 'franjas': [{'dias': [1, 4], 'desde': '08:00', 'hasta': '12:00', 'cada_min': 20, 'servicio': 'general-h'}]},
            {'centro': 'ostende', 'franjas': [{'dias': [3], 'desde': '09:00', 'hasta': '13:00', 'cada_min': 30}]}]
        saved = self._save({'centros': centros})
        # What the writer received is what a reload re-projects: both centers,
        # both bands, and the room choices unchanged for the next visit.
        self.assertEqual([c['centro'] for c in saved['centros']], ['hospital', 'ostende'])
        self.assertEqual(saved['centros'][1]['franjas'][0]['cada_min'], 30)
        rows = doctor_workspace.schedule_rows(saved, SERVICES, CENTERS)
        self.assertEqual([r['center'] for r in rows], ['Hospital de Pinamar', 'Ostende'])
        self.assertEqual(rows[0]['franjas'][0]['servicio'], 'general-h')
        self.assertIn('Clínica general · Consultorio 1', [f"{s['nombre']} · {s['consultorio']}" for s in rows[0]['servicios']])
        self.assertEqual([f"{s['nombre']} · {s['consultorio']}" for s in rows[1]['servicios']],
                         ['Clínica general · Sala 5'])


if __name__ == '__main__':
    unittest.main()
