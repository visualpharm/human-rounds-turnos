"""Executable contract for the Core-owned EMR observation boundary."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.connectors import ProviderUnavailable  # noqa: E402
from app.modules.turnos import emr_observations  # noqa: E402


class TestNormalizedEmrObservations(unittest.TestCase):
    def test_bundle_keeps_only_the_documented_generic_projection(self):
        observed = emr_observations.bundle({
            "schedules": [{"id": "schedule-1", "professional_id": "staff-3",
                           "specialty": "Cardiology", "professional_name": "Dr Example"}],
            "appointments": [{"id": "appointment-1", "schedule_id": "schedule-1",
                              "patient_id": "patient-9",
                              "scheduled_at": "2026-09-02T09:30:00-03:00",
                              "state": "attended",
                              "patient": {"id": "patient-9", "document_number": "30111222",
                                          "given_name": "Ana", "family_name": "Example"}}],
            "patients": [{"id": "patient-9", "document_number": "30111222",
                          "given_name": "Ana", "family_name": "Example"}],
        })
        self.assertEqual(observed["appointments"][0]["state"], "attended")
        self.assertEqual(observed["appointments"][0]["scheduled_at"],
                         "2026-09-02T09:30:00-03:00")
        self.assertEqual(set(observed["appointments"][0]),
                         {"id", "schedule_id", "patient_id", "scheduled_at", "state", "patient"})

    def test_incomplete_or_unknown_shape_is_rejected_not_compatibly_parsed(self):
        with self.assertRaises(ProviderUnavailable):
            emr_observations.bundle({"schedules": [], "appointments": [{"id": "only-id"}],
                                     "patients": []})

    def test_named_state_is_required(self):
        with self.assertRaises(ProviderUnavailable):
            emr_observations.bundle({"schedules": [], "appointments": [{
                "id": "appointment-1", "patient_id": "patient-9",
                "scheduled_at": "2026-09-02T09:30:00-03:00", "state": "unrecognized",
            }], "patients": []})


if __name__ == "__main__":
    unittest.main()
