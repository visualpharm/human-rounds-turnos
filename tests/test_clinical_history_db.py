"""Normalized clinical-history persistence: source-scoped, idempotent replace."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db  # noqa: E402
from app.core import db_clinical  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_clinical_history",
                          seed=lambda: db_clinical.clinical_history_by_dni("0"))


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


class ClinicalHistoryDbTest(unittest.TestCase):
    def setUp(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("TRUNCATE clinical_history_records RESTART IDENTITY")
            conn.commit()

    def test_replace_is_idempotent_and_preserves_source(self):
        records = [
            {"fecha": "2022-01-10", "tipo": "laboratorio", "valores": {"glucemia_mg_dl": 98}},
            {"fecha": "2024-03-02", "tipo": "estudio", "texto": "Control cardiológico"},
        ]
        self.assertEqual(db_clinical.clinical_history_replace("20.456.781", records), 2)
        self.assertEqual(db_clinical.clinical_history_replace("20.456.781", records), 2)
        rows = db_clinical.clinical_history_by_dni("20456781")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sistema"], "local_history")
        self.assertEqual(rows[1]["texto"], "Control cardiológico")


if __name__ == "__main__":
    unittest.main()
