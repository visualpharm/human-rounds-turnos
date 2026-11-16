"""doctor_appointments.resolver_names: the doctor's name behind «medico:<user>»
(the «Resuelta por» display), nothing for the other kinds of value."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.turnos import doctor_appointments as da  # noqa: E402


class TestResolverNames(unittest.TestCase):
    def test_only_medico_values_get_a_name(self):
        ids = {"drperez": "m7", "sinficha": None}
        docs = {"m7": {"id": "m7", "nombre": "PEREZ JUAN"}}
        with mock.patch.object(da.db_operators, "operator_doctor_id", side_effect=lambda u: ids.get(u)), \
                mock.patch.object(da, "doctor_of", side_effect=lambda i, incluir_incompletos=False: docs.get(i)):
            out = da.resolver_names(["medico:drperez", "medico:sinficha", "mesa:lucia", "auto:web", "", None])
        self.assertEqual(out, {"medico:drperez": "PEREZ JUAN"})

    def test_a_lookup_failure_leaves_the_login(self):
        with mock.patch.object(da.db_operators, "operator_doctor_id", side_effect=RuntimeError("db")):
            self.assertEqual(da.resolver_names(["medico:drperez"]), {})


if __name__ == "__main__":
    unittest.main()
