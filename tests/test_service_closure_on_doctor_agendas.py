"""A closure set on a service that has no schedule of its own still closes it.

Ergometría del Hospital (2026-10-23): the hospital closed the service from 15/10
to 14/11 in the panel («ya dieron esos turnos», reference-deployment counterpart), but its hours come from
the doctor's own window tagged with it, and the desk offered 29/10. The
service's closures apply on top of every doctor agenda that feeds it.

Synthetic catalog only; no database.
"""
import unittest

from app.modules.turnos import schedule_policy

CLOSED = {"tipo": "cerrado", "desde_fecha": "2026-10-15", "hasta_fecha": "2026-11-14"}
OWN = {"tipo": "cerrado", "motivo": "Vacaciones", "desde_fecha": "2027-01-02", "hasta_fecha": "2027-01-16"}
WINDOW = {"dias": [4, 5], "desde": "08:00", "hasta": "11:00", "cada_min": 20, "servicio": "ergometria-hospital"}


def service(**extra):
    return dict({"id": "ergometria-hospital", "nombre": "Ergometría", "cluster": "diagnostico",
                 "centro": "hospital-pinamar", "modo": "solicitud_con_orden"}, **extra)


def doctor(excepciones=None, **extra):
    return dict({"id": "berisso", "nombre": "Berisso", "especialidad": "Cardiología", "cluster": "cardiologia",
                 "centro": "hospital-pinamar",
                 "centros": [{"centro": "hospital-pinamar", "franjas": [dict(WINDOW)],
                              "excepciones": list(excepciones or [])}]}, **extra)


class ServiceClosureReachesDoctorAgendas(unittest.TestCase):
    def test_the_service_closure_closes_the_doctor_window(self):
        closed = service(agenda={"franjas": [], "excepciones": [dict(CLOSED)]})
        agendas = schedule_policy.doctor_agendas(doctor(), closed)
        self.assertEqual(len(agendas), 1)
        self.assertEqual(agendas[0]["franjas"], [WINDOW])
        self.assertIn(CLOSED, agendas[0]["excepciones"])

    def test_the_doctor_keeps_its_own_closures_too(self):
        closed = service(agenda={"franjas": [], "excepciones": [dict(CLOSED)]})
        agendas = schedule_policy.doctor_agendas(doctor([OWN]), closed)
        self.assertEqual(agendas[0]["excepciones"], [OWN, CLOSED])

    def test_the_flat_agenda_of_a_single_center_doctor_too(self):
        flat = {"id": "x", "centro": "hospital-pinamar", "agenda": {"franjas": [dict(WINDOW)], "excepciones": []}}
        closed = service(agenda={"franjas": [], "excepciones": [dict(CLOSED)]})
        self.assertEqual(schedule_policy.doctor_agendas(flat, closed)[0]["excepciones"], [CLOSED])

    def test_the_catalog_document_is_not_touched(self):
        doc = doctor([OWN])
        schedule_policy.doctor_agendas(doc, service(agenda={"franjas": [], "excepciones": [dict(CLOSED)]}))
        self.assertEqual(doc["centros"][0]["excepciones"], [OWN])

    def test_without_a_service_closure_nothing_changes(self):
        self.assertEqual(schedule_policy.doctor_agendas(doctor([OWN]), service()),
                         [{"franjas": [WINDOW], "excepciones": [OWN]}])


if __name__ == "__main__":
    unittest.main()
