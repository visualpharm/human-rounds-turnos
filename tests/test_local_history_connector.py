"""Private local clinical-history archive: normalized input, safe degradation."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.connectors.local_history import local_history  # noqa: E402
import _env  # noqa: E402


class LocalHistoryTest(unittest.TestCase):
    def test_missing_path_and_empty_database_is_empty(self):
        with _env.patched_env(TURNOS_CLINICAL_HISTORY_PATH=None), \
                mock.patch("app.core.db_clinical.clinical_history_by_dni", return_value=[]):
            self.assertEqual(local_history.lookup_by_dni("99000001"), [])

    def test_database_records_take_precedence_over_file(self):
        stored = [{"fecha": "2025-01-01", "tipo": "estudio", "texto": "DB"}]
        with mock.patch("app.core.db_clinical.clinical_history_by_dni", return_value=stored), \
                _env.patched_env({"TURNOS_CLINICAL_HISTORY_PATH": "/missing"}):
            rows = local_history.lookup_by_dni("99000001")
        self.assertEqual(rows[0]["texto"], "DB")
        self.assertEqual(rows[0]["sistema"], "local_history")

    def test_reads_same_normalized_shape_as_legacy_connectors(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "patients": {"99.000.001": [], "99000001": [
                {"fecha": "2025-02-03", "tipo": "laboratorio",
                 "valores": {"glucemia_mg_dl": 101}}
            ]}}, f)
            f.flush()
            with _env.patched_env({"TURNOS_CLINICAL_HISTORY_PATH": f.name}), \
                    mock.patch("app.core.db_clinical.clinical_history_by_dni", return_value=[]):
                rows = local_history.lookup_by_dni("99.000.001")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sistema"], "local_history")
        self.assertEqual(rows[0]["sistema_label"], "Archivo clínico")

    def test_unknown_dni_is_a_real_empty_result(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "patients": {}}, f)
            f.flush()
            with _env.patched_env({"TURNOS_CLINICAL_HISTORY_PATH": f.name}), \
                    mock.patch("app.core.db_clinical.clinical_history_by_dni", return_value=[]):
                self.assertEqual(local_history.lookup_by_dni("12345678"), [])

    def test_persistent_volume_default_is_auto_detected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "patients": {"99000001": [
                {"fecha": "2024-01-01", "tipo": "estudio", "texto": "Control"}
            ]}}, f)
            f.flush()
            with _env.patched_env(TURNOS_CLINICAL_HISTORY_PATH=None), \
                    mock.patch("app.core.db_clinical.clinical_history_by_dni", return_value=[]), \
                    mock.patch.object(local_history, "_PERSISTENT_DEFAULT", f.name):
                self.assertTrue(local_history.configured())
                self.assertEqual(len(local_history.lookup_by_dni("99000001")), 1)


if __name__ == "__main__":
    unittest.main()
