"""test_security_review_access_log.py — findings M-2 and M-3 of the 2026-11 security
review (docs/compliance/security-review-2026-11-surfaces.md):

* M-3 — the billing CSV (`cost-recovery.csv`), the SUMAR+ CSV and the clinical
  history import (`POST /api/admin/patients/{dni}/clinical-history`) read or write
  clinical data and left no row in `clinical_access_log`.
* M-2 — a doctor reached a patient through any booking request, whatever its
  state, and the assignment itself left no access row.

No database: the gate, the data and the access log are replaced at the router's
edges; the code under test runs for real.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
      -m unittest discover -s tests -p "test_security_review_access_log.py"
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin_panel import admin_patients, reports, reports_router  # noqa: E402
from app.admin_panel.native_json import ApiError  # noqa: E402
from app.core import access_log, config, deps  # noqa: E402

config.ENV_PATH = ""
ADMIN = {"usuario": "admin", "rol": "admin"}


class _GateMixin:
    def setUp(self):
        app = FastAPI()
        app.include_router(reports_router.router)

        async def let_through():
            return ADMIN

        # The router-level gate and the by-hand one both let the admin through.
        for dep in reports_router.router.dependencies:
            if dep.dependency.__name__ == "_dep":
                app.dependency_overrides[dep.dependency] = let_through

        async def check_admin(request, **flags):
            return ADMIN

        mock.patch.object(deps, "check_admin", check_admin).start()
        self.addCleanup(mock.patch.stopall)
        self.record = mock.patch.object(access_log, "record_many").start()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)


class TestPiiCsvLeavesAccessRows(_GateMixin, unittest.TestCase):
    def _get(self, path, rows_attr, rows):
        with mock.patch.object(reports, rows_attr, return_value=rows):
            return self.client.get(path)

    def test_billing_csv_records_each_patient_once_before_answering(self):
        rows = [["2026-11-01", "30111222", "Ana Perez", "IOMA", "1", "x", "", "", "", ""],
                ["2026-11-02", "30111222", "Ana Perez", "IOMA", "1", "x", "", "", "", ""],
                ["2026-11-03", "28333444", "Juan Gomez", "", "", "x", "", "", "", ""]]
        r = self._get("/api/admin/reports/cost-recovery.csv", "_cost_recovery_csv_rows", rows)
        self.assertEqual(r.status_code, 200)
        self.record.assert_called_once()
        identity, action, items = self.record.call_args.args
        self.assertEqual((identity, action), (ADMIN, "descarga"))
        self.assertEqual(sorted(i[0] for i in items), ["28333444", "30111222"])

    def test_sumar_csv_records_too(self):
        rows = [["2026-11-01", "40200001", "Lucia Diaz", "s", "c", "z", "", "", "Embarazo"]]
        r = self._get("/api/admin/reports/sumar.csv", "_sumar_csv_rows", rows)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i[0] for i in self.record.call_args.args[2]], ["40200001"])

    def test_file_is_not_given_when_the_row_cannot_be_written(self):
        self.record.side_effect = access_log.AccessLogUnavailable("OperationalError")
        rows = [["2026-11-01", "30111222", "Ana Perez", "", "", "", "", "", "", ""]]
        r = self._get("/api/admin/reports/cost-recovery.csv", "_cost_recovery_csv_rows", rows)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(b"30111222", r.content)

    def test_aggregate_csv_without_dni_records_nothing(self):
        rows = [["2026-11-01", "A.P.", "s", "c", "z", "x", "", "", "", ""]]
        r = self._get("/api/admin/reports/requests.csv", "_requests_csv_rows", rows)
        self.assertEqual(r.status_code, 200)
        self.record.assert_not_called()


class TestClinicalHistoryImportIsRecorded(unittest.TestCase):
    def test_import_leaves_an_access_row_before_writing(self):
        calls = []
        with mock.patch.object(admin_patients.db_accounts, "get_user", return_value={"dni": "30111222"}), \
                mock.patch.object(admin_patients.db_clinical, "clinical_history_replace",
                                  side_effect=lambda *a: calls.append("write") or 1), \
                mock.patch.object(access_log, "record",
                                  side_effect=lambda *a, **k: calls.append("record")) as rec:
            admin_patients.save_clinical_history("30111222", {"records": []}, identity=ADMIN)
        self.assertEqual(calls, ["record", "write"])
        self.assertEqual(rec.call_args.args[0], ADMIN)
        self.assertEqual(rec.call_args.args[2], "30111222")

    def test_nothing_is_written_when_the_row_cannot_be_written(self):
        with mock.patch.object(admin_patients.db_accounts, "get_user", return_value={"dni": "30111222"}), \
                mock.patch.object(admin_patients.db_clinical, "clinical_history_replace") as write, \
                mock.patch.object(access_log, "record",
                                  side_effect=access_log.AccessLogUnavailable("x")):
            with self.assertRaises(ApiError) as cm:
                admin_patients.save_clinical_history("30111222", {"records": []}, identity=ADMIN)
        self.assertEqual(cm.exception.status, 503)
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
