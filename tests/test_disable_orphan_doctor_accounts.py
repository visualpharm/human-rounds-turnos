"""scripts/fixes/disable_orphan_doctor_accounts.py — orphans report, question 1.

An ENABLED «médico» account whose doctor is in no catalog (pack, overlay) is
switched off; disabled ones stay as they are; an account whose doctor exists
(even hidden in the panel) is left alone; dry run writes nothing.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.patient_web import catalog  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_orphan_doctors", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _script():
    spec = importlib.util.spec_from_file_location(
        "disable_orphan_doctor_accounts",
        os.path.join(ROOT, "scripts", "fixes", "disable_orphan_doctor_accounts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPlan(unittest.TestCase):
    """The decision, without a database."""

    def test_only_an_enabled_account_whose_doctor_is_missing_is_disabled(self):
        ops = [
            {"usuario": "a", "medico_id": "gone", "activo": True},      # orphan, enabled -> disable
            {"usuario": "b", "medico_id": "gone-too", "activo": False},  # orphan, disabled -> untouched
            {"usuario": "c", "medico_id": "here", "activo": True},      # doctor exists -> untouched
            {"usuario": "d", "medico_id": None, "activo": True},        # no doctor link -> untouched
        ]
        out = _script().plan(["here"], ops)
        self.assertEqual([o["usuario"] for o in out["disable"]], ["a"])
        self.assertEqual((out["already_disabled"], out["linked_ok"], out["unlinked"]), (1, 1, 1))


class TestRun(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._tmp = tempfile.mkdtemp(prefix="orphan-doctors-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
            json.dump({"medicos": [{"id": "med-base", "nombre": "Ana Base",
                                    "especialidad": "Clínica médica"}]}, f)
        self.addCleanup(self._restore)   # registered first, so it runs after the environment is put back
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE operators, catalog RESTART IDENTITY CASCADE")
        db_operators.operator_create("con.medico", "Clave-123456", rol="medico", medico_id="med-base")
        db_operators.operator_create("sin.medico.on", "Clave-123456", rol="medico", medico_id="med-fantasma")
        db_operators.operator_create("sin.medico.off", "Clave-123456", rol="medico",
                           medico_id="med-fantasma-2", activo=False)
        db_operators.operator_create("secretaria", "Clave-123456", rol="admisionista")

    def _restore(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _active(self):
        return {o["usuario"]: o["activo"] for o in db_operators.operators_list()}

    def test_dry_run_writes_nothing(self):
        before = self._active()
        lines = []
        report = _script().run(apply=False, emit=lines.append)
        self.assertEqual(report["a_deshabilitar"], 1)
        self.assertEqual(report["deshabilitadas_ahora"], 0)
        self.assertEqual(self._active(), before)
        self.assertTrue(any("sin.medico.on" in l for l in lines))

    def test_apply_disables_only_the_enabled_orphan_and_is_idempotent(self):
        report = _script().run(apply=True, emit=lambda l: None)
        self.assertEqual(report["deshabilitadas_ahora"], 1)
        self.assertEqual(self._active(), {
            "con.medico": True, "sin.medico.on": False,
            "sin.medico.off": False, "secretaria": True})
        again = _script().run(apply=True, emit=lambda l: None)
        self.assertEqual((again["a_deshabilitar"], again["deshabilitadas_ahora"]), (0, 0))
        self.assertEqual(again["ya_deshabilitadas_sin_medico"], 2)

    def test_a_doctor_hidden_in_the_panel_still_counts_as_in_the_catalog(self):
        db_operators.operator_create("oculta", "Clave-123456", rol="medico", medico_id="med-base-2")
        db_catalog.catalog_upsert("medicos", "med-base-2", {"id": "med-base-2", "nombre": "Oculta Panel",
                                                    "especialidad": "Clínica médica", "centro": "hospital"})
        catalog.invalidate()
        from app.admin_panel import admin_content
        admin_content.delete_item("medicos", "med-base-2")
        self.assertIn("med-base-2", _script()._catalog_ids(), "hidden is not missing")
        before = self._active()["oculta"]   # hiding the doctor may already have disabled the account
        _script().run(apply=True, emit=lambda l: None)
        self.assertEqual(self._active()["oculta"], before, "the script leaves it exactly as it was")


if __name__ == "__main__":
    unittest.main()
