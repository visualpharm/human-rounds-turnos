"""Source-level guard for the Core/Enterprise EMR payload boundary.

Raw HSI spellings may exist in the vendor adapter and the one-way migration,
but never in Core runtime code or the public bridge contract.  This catches the
renamed-wrapper failure mode that behavioral happy-path tests previously hid.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_HSI_FIELDS = (
    "appointmentStateId",
    "appointmentDuration",
    "diaryId",
    "patientId",
    "identificationNumber",
)
CORE_BOUNDARY_FILES = (
    "app/connectors/__init__.py",
    "app/connectors/emr_bridge/adapter.py",
    "app/modules/turnos/emr_observations.py",
    "app/modules/turnos/hsi_journal.py",
    "app/modules/turnos/booking_requests.py",
    "app/modules/turnos/booking_create.py",
    "app/modules/turnos/booking_referrals.py",
    "app/modules/turnos/booking_queue.py",
    "app/modules/turnos/booking_decisions.py",
    "app/modules/turnos/booking_patient.py",
    "app/modules/turnos/booking_capacity.py",
    "app/modules/turnos/booking_emr.py",
    "app/modules/turnos/panel_settings.py",
    "app/core/db.py",
    "tests/test_emr_bridge_conformance.py",
    "tests/test_emr_bridge_connector.py",
    "tests/test_emr_observations.py",
)


class TestEmrPayloadBoundary(unittest.TestCase):
    def test_core_and_public_bridge_do_not_know_hsi_wire_fields(self):
        for relative in CORE_BOUNDARY_FILES:
            source = (ROOT / relative).read_text(encoding="utf-8")
            for field in FORBIDDEN_HSI_FIELDS:
                with self.subTest(path=relative, field=field):
                    self.assertNotIn(field, source)

    def test_core_journal_does_not_import_the_vendor_adapter(self):
        source = (ROOT / "app/modules/turnos/hsi_journal.py").read_text(
            encoding="utf-8")
        self.assertNotRegex(
            source, r"(?m)^\s*(?:from|import)\s+connectors\.hsi(?:\.|\s|$)")
        self.assertNotRegex(
            source, r"(?m)^\s*(?:from|import)\s+.*hsi_client(?:\.|\s|$)")


if __name__ == "__main__":
    unittest.main()
