"""Synthetic source-stack checks for the approved design PULL; no browser/DB."""
import unittest

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1')
from app.admin_panel import admin_content
from app.core import jinja_env


class TestBookingPolicyProjection(unittest.TestCase):
    def test_inherited_doctor_defaults_follow_applicable_services_and_keep_varying_values(self):
        doctor = {'id': 'doctor', 'cluster': 'general', 'centros': [{'centro': 'hospital'}]}
        services = [
            {'id': 'a', 'centro': 'hospital', 'cluster': 'general', 'turnos_activos_max': 10, 'dias_anticipacion': 45},
            {'id': 'b', 'centro': 'other', 'cluster': 'general', 'turnos_activos_max': 60, 'dias_anticipacion': 365},
        ]
        projected = admin_content._booking_policy_projection('medicos', doctor, services=services)
        self.assertEqual(projected['_booking_defaults'], {
            'patient': 10, 'days': 45,
            'patient_from': {'from': 'service', 'name': 'a'}, 'days_from': {'from': 'service', 'name': 'a'}})
        services.append({'id': 'c', 'centro': 'hospital', 'cluster': 'general', 'turnos_activos_max': 5, 'dias_anticipacion': 31})
        projected = admin_content._booking_policy_projection('medicos', doctor, services=services)
        self.assertEqual(projected['_booking_defaults'], {
            'patient': '5–10', 'days': '31–45',
            'patient_from': {'from': 'services'}, 'days_from': {'from': 'services'}})
        self.assertNotIn('_booking_defaults', doctor)

    def test_an_inherited_limit_names_where_it_comes_from(self):
        # pinamar-turnos 2026-11-03: Horarios says whether the number is the
        # doctor's own or inherited, and from the site or which service.
        doctor = {'id': 'doctor', 'cluster': 'general', 'centros': [{'centro': 'hospital'}]}
        plain = {'id': 'a', 'nombre': 'Clínica', 'centro': 'hospital', 'cluster': 'general'}
        kine = {'id': 'k', 'nombre': 'Kinesiología', 'centro': 'hospital', 'cluster': 'general', 'turnos_activos_max': 10}
        defaults = admin_content._booking_policy_projection('medicos', doctor, services=[plain])['_booking_defaults']
        self.assertEqual((defaults['patient_from'], defaults['days_from']), ({'from': 'site'}, {'from': 'site'}))
        defaults = admin_content._booking_policy_projection('medicos', doctor, services=[kine])['_booking_defaults']
        self.assertEqual(defaults['patient_from'], {'from': 'service', 'name': 'Kinesiología'})
        self.assertEqual(defaults['days_from'], {'from': 'site'})
        # One service with its own number and one on the site's: two sources.
        defaults = admin_content._booking_policy_projection('medicos', doctor, services=[plain, kine])['_booking_defaults']
        self.assertEqual(defaults['patient_from'], {'from': 'services'})
        self.assertEqual(admin_content._booking_policy_projection('medicos', doctor, services=[])['_booking_defaults']['patient_from'],
                         {'from': 'site'})

    def test_service_doctor_count_is_the_runtime_pool_shown_as_read_only(self):
        service = {'id': 'a', 'centro': 'hospital', 'cluster': 'general'}
        doctors = [
            {'cluster': 'general', 'centro': 'hospital'},
            {'cluster': 'general', 'centro': 'other'},
            {'cluster': 'general', 'centro': 'hospital', '_borrado': True},
        ]
        projected = admin_content._booking_policy_projection('servicios', service, doctors=doctors)
        self.assertEqual(projected['_schedule_doctor_count'], 1)

    def test_service_lists_who_attends_it_and_is_empty_when_nobody_does(self):
        # «Consultorios sin médico» (the hospital's technical contact, 2026-11-02): Servicios names the
        # doctors of each service (the assignable pool) or shows none.
        service = {'id': 'a', 'centro': 'hospital', 'cluster': 'general'}
        doctors = [
            {'id': 'ana', 'nombre': 'Ana Gómez', 'cluster': 'general', 'centro': 'hospital'},
            {'id': 'luis', 'nombre': 'Luis Paz', 'cluster': 'general', 'centro': 'other'},
            {'id': 'borrado', 'nombre': 'Borrada', 'cluster': 'general', 'centro': 'hospital', '_borrado': True},
        ]
        projected = admin_content._booking_policy_projection('servicios', service, doctors=doctors)
        self.assertEqual(projected['_medicos'], [{'id': 'ana', 'nombre': 'Ana Gómez'}])
        projected = admin_content._booking_policy_projection('servicios', service, doctors=[])
        self.assertEqual(projected['_medicos'], [])


class TestRealWorkspaceTemplate(unittest.TestCase):
    def data(self, empty=False):
        return {'doctors': [{'id': 'doctor', 'nombre_pila': 'Doctor', 'apellido': 'Example', 'especialidad': 'General'}],
                'first_name': 'Doctor', 'last_name': 'Example', 'specialties': [{'nombre': 'General'}], 'editable': True,
                'days': [{'id': d, 'key': 'admin.content.hours.day.' + key, 'label': label}
                         for d, key, label in zip([1,2,3,4,5,6,0], ['mon','tue','wed','thu','fri','sat','sun'], ['Lu','Ma','Mi','Ju','Vi','Sá','Do'])],
                'photo': {'has_upload': False, 'has_original': False},
                'schedules': [{'centro': 'hospital', 'center': 'Hospital', 'consultorio': 'Room 1', 'editable': True, 'doctor_id': 'doctor',
                               'franjas': [] if empty else [{'dias': [3,4], 'desde': '08:00', 'hasta': '14:00', 'cada_min': 20}]}]}

    def render(self, onboarding, empty=False):
        return jinja_env.render('admin/doctor-workspace.html', nav_items=[], active_section='onboarding' if onboarding else 'my-schedules',
                                rol='medico', onboarding=onboarding, data=self.data(empty), normal_landing='/admin/cola')

    def test_both_onboarding_states_have_three_cards_one_primary_save_and_immediate_editing(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                html = self.render(True, empty)
                self.assertEqual(html.count('class="dw-step"'), 3)
                self.assertEqual(html.count('btn btn-accent'), 1)
                self.assertIn('Guardar y empezar', html)
                self.assertEqual(html.count('data-day="'), 7)
                for field in ('desde', 'hasta', 'cada_min'):
                    if empty:
                        self.assertNotIn(f'data-time="{field}"', html)
                    else:
                        # Each chosen weekday has its own independent interval.
                        self.assertEqual(html.count(f'data-time="{field}"'), 2)
                self.assertIn('data-days', html)
                self.assertIn('data-photo-action="original"', html)
                self.assertNotIn('data-photo-action="restore"', html)

    def test_schedule_cards_say_the_hours_once_and_edit_is_the_main_button(self):
        html = self.render(False)
        self.assertIn('dw-center dw-card', html)
        # The sentence names the days; no second row of button-looking chips.
        self.assertNotIn('dw-days-readonly', html)
        self.assertIn('08:00 a 14:00', html)
        self.assertIn('turnos de 20 min', html)
        # The product owner, 2026-10-27: «Editar horarios» is the screen's main button.
        self.assertIn('<a class="btn btn-accent" href="/admin/doctors?item=doctor">Editar horarios</a>', html)
        self.assertEqual(html.count('btn-accent'), 1)
        self.assertNotIn('data-day="', html)

    def test_new_doctor_without_hours_gets_what_appears_here_and_the_button(self):
        html = self.render(False, empty=True)
        self.assertIn('data-no-hours', html)
        self.assertIn('<a class="btn btn-accent" href="/admin/doctors?item=doctor">Cargar horarios</a>', html)
        self.assertNotIn('data-site-holidays', html)

    def test_holidays_are_one_line_and_a_list_on_request(self):
        data = self.data()
        data['holidays'] = {'line': 'En los feriados no se dan turnos. Los próximos: 9 de noviembre y 23 de noviembre.',
                            'more': 'Ver los 2 feriados',
                            'rows': [{'dia': '9 de noviembre', 'nombre': 'A'}, {'dia': '23 de noviembre', 'nombre': 'B'}]}
        html = jinja_env.render('admin/doctor-workspace.html', nav_items=[], active_section='my-schedules', rol='medico',
                                onboarding=False, data=data, normal_landing='/admin/cola')
        self.assertEqual(html.count('data-site-holidays'), 1)
        self.assertIn('Los próximos: 9 de noviembre y 23 de noviembre.', html)
        self.assertIn('<span class="dw-hol-date">9 de noviembre</span><span>A</span>', html)
        self.assertNotIn('Feriado:', html)
        # The edit button comes before the holidays it does not edit.
        self.assertLess(html.index('btn-accent'), html.index('data-site-holidays'))

    def test_database_down_heals_itself_without_an_error(self):
        html = jinja_env.render('admin/doctor-workspace.html', nav_items=[], active_section='my-schedules', rol='medico',
                                onboarding=False, data=None, normal_landing='/admin/cola')
        self.assertIn('data-agendas-waiting', html)
        self.assertIn('location.reload()', html)
        workspace = html[html.index('id="doctor-workspace"'):html.index('id="doctor-recovery"')]
        self.assertNotIn('No se pudo cargar', workspace)
        self.assertNotIn('Recargar', workspace)


class TestScheduleExceptionDisplay(unittest.TestCase):
    def test_real_projection_retains_closures_in_readonly_schedule_cards(self):
        from app.admin_panel import doctor_workspace
        doctor={'id':'doctor','centros':[{'centro':'hospital','franjas':[], 'excepciones':[{'tipo':'cerrado','desde_fecha':'2027-01-01','hasta_fecha':'2027-01-31','motivo':'Vacaciones'}]}]}
        rows=doctor_workspace.schedule_rows(doctor,[],[{'slug':'hospital','name':'Hospital'}])
        self.assertEqual(len(rows[0]['closures']),1)
        self.assertIn('Vacaciones',rows[0]['closures'][0])
        self.assertIn('enero',rows[0]['closures'][0])
        self.assertNotIn('2027-01-31',rows[0]['closures'][0])
