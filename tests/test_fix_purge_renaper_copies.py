"""scripts/fixes/purge_renaper_account_copies.py — the copies of real accounts
that the identity simulator left in its registry (2026-10-23, problems.md
№5) go; test people, accounts and history stay."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "fixes"))

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_catalog  # noqa: E402
import purge_renaper_account_copies as purge  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_fix_purge_renaper",
                          seed=lambda: db_catalog.catalog_list(purge.CATALOG_TYPE))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _row(dni, origen):
    return {"id": dni, "doc": {"dni": dni, "nombre": "X", "apellido": "Y",
                               "contacto": "2254000000", "origen": origen}}


class TestPlan(unittest.TestCase):
    def test_only_copies_of_accounts_are_deleted(self):
        rows = [_row("30000001", "cuenta_existente"), _row("30000002", "cuenta_existente"),
                _row("99000001", "predefinida"), _row("30000003", "aleatoria")]
        decision = purge.plan(rows, account_dnis={"30000001", "30000002", "30000003"},
                              history_dnis={"30000002"})
        self.assertEqual(decision["delete"], ["30000001"])
        self.assertEqual(decision["kept_history"], ["30000002"])
        self.assertEqual(decision["test_people"], 2)
        self.assertEqual(decision["test_accounts"], 1)

    def test_history_copies_go_only_when_asked(self):
        rows = [_row("30000002", "cuenta_existente")]
        decision = purge.plan(rows, set(), {"30000002"}, include_history=True)
        self.assertEqual(decision["delete"], ["30000002"])


class TestRun(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres no accesible")
        for dni, origen in (("30000011", "cuenta_existente"), ("30000012", "cuenta_existente"),
                            ("99000001", "predefinida")):
            db_catalog.catalog_upsert(purge.CATALOG_TYPE, dni, _row(dni, origen)["doc"])
        for dni in ("30000011", "30000012"):
            db_accounts.create_user(dni, "Real", "Paciente", "1970-01-01", "F", "2254111111", "x")
        db_clinical.clinical_history_replace("30000012", [{"fecha": "2020-01-01", "texto": "consulta"}])

    def tearDown(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM catalog WHERE type=%s", (purge.CATALOG_TYPE,))
            cur.execute("DELETE FROM clinical_history_records")
            cur.execute("DELETE FROM users")

    def _run(self, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            report = purge.run(**kw)
        return report, out.getvalue()

    def test_dry_run_reports_and_changes_nothing(self):
        report, printed = self._run()
        self.assertEqual(report["copias_de_cuentas_reales"], 2)
        self.assertEqual(report["copias_a_borrar"], 1)
        self.assertEqual(report["copias_con_historia_conservadas"], 1)
        self.assertEqual(report["copias_borradas"], 0)
        self.assertEqual(len(db_catalog.catalog_list(purge.CATALOG_TYPE)), 3)
        # Counts only: no DNI, name or phone in the output.
        for secret in ("30000011", "30000012", "2254", "Real"):
            self.assertNotIn(secret, printed)

    def test_apply_deletes_the_copy_and_keeps_accounts_and_history(self):
        report, _ = self._run(apply=True)
        self.assertEqual(report["copias_borradas"], 1)
        left = {r["id"] for r in db_catalog.catalog_list(purge.CATALOG_TYPE)}
        self.assertEqual(left, {"30000012", "99000001"})
        self.assertIsNotNone(db_accounts.get_user("30000011"))
        self.assertEqual(len(db_clinical.clinical_history_by_dni("30000012")), 1)
        again, _ = self._run(apply=True)
        self.assertEqual(again["copias_borradas"], 0)


if __name__ == "__main__":
    unittest.main()
