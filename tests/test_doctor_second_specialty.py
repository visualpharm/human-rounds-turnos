"""A doctor's optional second specialty (hospital TASK-35): the cluster
membership, the schedule policy and the listing facet all read BOTH."""
import unittest

from app.modules.turnos import schedule_policy as policy
from app.patient_web import catalog

DOCTOR = {"id": "d1", "especialidad": "Pediatría", "cluster": "pediatria",
          "especialidad_2": "Cardiología", "cluster_2": "cardiologia",
          "centro": "hospital", "centros": [{"centro": "hospital"}]}
SINGLE = {k: v for k, v in DOCTOR.items() if k not in ("especialidad_2", "cluster_2")}
HEART = {"id": "eco-cardio", "nombre": "Ecocardiograma", "centro": "hospital", "cluster": "cardiologia"}
CHILD = {"id": "control-nino", "nombre": "Control del niño", "centro": "hospital", "cluster": "pediatria"}
OTHER_CENTER = dict(HEART, centro="caps-norte")
CATCHALL = {"id": "otro", "nombre": "Estudio raro", "centro": "hospital", "cluster": "otras"}


class SecondSpecialtyMembership(unittest.TestCase):
    def test_the_doctor_belongs_to_both_clusters(self):
        self.assertEqual(catalog.doctor_clusters(DOCTOR), ["pediatria", "cardiologia"])
        self.assertEqual(catalog.doctor_clusters(SINGLE), ["pediatria"])

    def test_cluster_member_through_either_specialty(self):
        self.assertTrue(policy._cluster_member(DOCTOR, HEART))
        self.assertTrue(policy._cluster_member(DOCTOR, CHILD))

    def test_without_the_second_specialty_the_other_cluster_stays_out(self):
        self.assertFalse(policy._cluster_member(SINGLE, HEART))
        self.assertTrue(policy._cluster_member(SINGLE, CHILD))

    def test_a_center_the_doctor_does_not_attend_stays_out(self):
        self.assertFalse(policy._cluster_member(DOCTOR, OTHER_CENTER))

    def test_catch_all_cluster_needs_the_specialty_to_match_the_service(self):
        doctor = dict(DOCTOR, cluster_2="otras", especialidad_2="Estudio raro")
        self.assertTrue(policy._cluster_member(doctor, CATCHALL))
        doctor = dict(DOCTOR, cluster_2="otras", especialidad_2="Otra cosa")
        self.assertFalse(policy._cluster_member(doctor, CATCHALL))

    def test_serves_by_the_name_of_either_specialty(self):
        self.assertEqual(policy._specialties(DOCTOR), ["Pediatría", "Cardiología"])
        self.assertEqual(policy._specialties(SINGLE), ["Pediatría"])


if __name__ == "__main__":
    unittest.main()
