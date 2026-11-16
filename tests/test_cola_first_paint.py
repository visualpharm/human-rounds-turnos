"""The first paint of /admin/cola is the same screen the page settles on.

The product owner, 2026-10-26 (salud /admin/cola under an admin): «the page loads w the list of all
doctors on the screen briefly». The server table (templates/admin/partials/
day_queue_initial.html) drew a header row for EVERY doctor with an agenda that day —
53 names — and admin-queue.mjs then hid the agendas with no appointment behind
«Ver agendas sin turnos». The first paint now shows what the controller keeps: only
doctors with appointments, no header with the doctor's own name on the doctor's
screen, and the «no appointments» line only when there is not even a free slot
(otherwise the controller shows the free agendas, not that sentence).
"""
import unittest

from app.core import i18n, jinja_env
from app.core import format as display_format
from app.admin_panel import admin_cola_screen


def _render(grupos, rol="admin"):
    _lang, token = i18n.bind_lang("es")
    try:
        return jinja_env.render(
            "admin/partials/day_queue_initial.html",
            queue_data={"grupos": grupos, "total": sum(len(g.get("turnos") or []) for g in grupos)},
            queue_display_name=display_format.person_name,
            queue_name_age=admin_cola_screen.name_age, rol=rol)
    finally:
        i18n.unbind(token)


TURNO = {"hora": "10:00", "paciente": "Ana Delhoy", "servicio": "Cardiología", "asistencia": None}


class TestFirstPaintMatchesTheSettledScreen(unittest.TestCase):
    def test_doctors_without_appointments_are_not_listed(self):
        html = _render([
            {"medico": "Camila Levin", "turnos": [TURNO], "libres": ["11:00"]},
            {"medico": "Sergio Ruiz", "turnos": [], "libres": ["09:00", "09:20"]},
            {"medico": "Mario Gómez", "turnos": [], "libres": ["08:00"]},
        ])
        self.assertIn("Camila Levin", html)
        self.assertIn("Ana Delhoy", html)
        self.assertNotIn("Sergio Ruiz", html)
        self.assertNotIn("Mario Gómez", html)

    def test_a_day_of_free_agendas_only_says_so_and_points_at_the_checkbox(self):
        # The product owner 27.10 (q1044534): free hours are hidden until «Mostrar horarios libres».
        html = _render([{"medico": "Sergio Ruiz", "turnos": [], "libres": ["09:00"]}])
        self.assertNotIn("Sergio Ruiz", html)
        self.assertNotIn("09:00", html)
        self.assertIn("No hay turnos aprobados para hoy.", html)
        self.assertIn("Mostrar horarios libres", html)

    def test_free_hours_are_not_painted_next_to_appointments(self):
        html = _render([{"medico": "Camila Levin", "turnos": [TURNO], "libres": ["08:00", "11:00"]}])
        self.assertIn("Ana Delhoy", html)
        self.assertNotIn("08:00", html)
        self.assertNotIn("Mostrar horarios libres", html)

    def test_a_day_with_nothing_says_so(self):
        html = _render([])
        # the sentence names the day — it used to print a raw «{fecha}»
        self.assertIn("No hay turnos aprobados para hoy.", html)
        self.assertNotIn("<table", html)

    def test_the_doctor_does_not_see_a_row_with_his_own_name(self):
        html = _render([{"medico": "Camila Levin", "turnos": [TURNO]}], rol="medico")
        self.assertIn("Ana Delhoy", html)
        self.assertNotIn("Camila Levin", html)
        self.assertNotIn(">Médico<", html)

    def test_one_flat_list_by_hour_with_the_doctor_in_each_row(self):
        # The product owner 2026-11-08: today's patients in one list, each signed with its
        # doctor, never grouped under the doctors' names.
        html = _render([
            {"medico": "Camila Levin", "turnos": [
                {"hora": "11:00", "paciente": "Bruno Paz", "servicio": "Cardiología", "asistencia": None}]},
            {"medico": None, "turnos": [
                {"hora": "09:30", "paciente": "Ana Delhoy", "servicio": "Clínica", "asistencia": None}]},
        ])
        self.assertEqual(html.count("<tbody>"), 1)
        self.assertIn(">Médico<", html)
        self.assertLess(html.index("Ana Delhoy"), html.index("Bruno Paz"))
        self.assertLess(html.index("Sin médico asignado"), html.index("Camila Levin"))
        self.assertNotIn("queue-server-group", html)

    def test_the_row_is_name_and_age_and_the_doctor_with_the_service_under_it(self):
        # The product owner 2026-11-09: «Name, N años» in one link, the doctor's name with
        # the service under it, no DNI and no attendance in the row (the card
        # holds them) — the first paint already draws that row.
        html = _render([{"medico": "Camila Levin", "turnos": [
            {"hora": "10:00", "paciente": "ANA DELHOY", "servicio": "Cardiología", "nacimiento": "1950-03-04",
             "dni": "30111222", "asistencia": "presente"}]}])
        self.assertRegex(html, r'class="queue-pat-link">Ana Delhoy, \d+ años<')
        self.assertRegex(html, r'Camila Levin<span class="queue-sub">Cardiología</span>')
        self.assertNotIn("30111222", html)
        self.assertNotIn("Atendió", html)
        self.assertNotIn(">Servicio<", html)
        self.assertNotIn(">Asistencia<", html)

    def test_no_birth_date_leaves_the_bare_name(self):
        html = _render([{"medico": "Camila Levin", "turnos": [TURNO]}])
        self.assertIn('class="queue-pat-link">Ana Delhoy<', html)

    def test_the_doctor_sees_the_service_under_the_patient_only_when_the_day_mixes_them(self):
        one = _render([{"medico": "Camila Levin", "turnos": [TURNO]}], rol="medico")
        self.assertNotIn("queue-sub", one)
        mixed = _render([{"medico": "Camila Levin", "turnos": [
            TURNO, {"hora": "11:00", "paciente": "Bruno Paz", "servicio": "Ecografía", "asistencia": None}]}],
            rol="medico")
        self.assertIn('<span class="queue-sub">Ecografía</span>', mixed)
        self.assertNotIn(">Médico<", mixed)


if __name__ == "__main__":
    unittest.main()
