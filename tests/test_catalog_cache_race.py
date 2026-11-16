"""Keep catalog invalidation from republishing an old doctor overlay."""

import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.intake import intake_admin
from app.patient_web import catalog
from app.core import db_catalog


class _DoorTokenCacheScenario(unittest.TestCase):
    def setUp(self):
        self._saved_catalog_list = db_catalog.catalog_list
        self._saved_catalog_upsert = db_catalog.catalog_upsert
        self._saved_base_items = catalog._base_items_raw
        self._saved_raw_cache = dict(catalog._RAW_CACHE)
        self._saved_fail_until = dict(catalog._DB_FAIL_UNTIL)
        self._rows = [{
            "id": "qr-med-1", "doc": {"id": "qr-med-1", "nombre": "Doctor"},
            "borrado": False,
        }]
        self._reader_entered = threading.Event()
        self._release_reader = threading.Event()
        self._reader = None
        self._reader_error = None
        self._reader_result = None
        self._stale_failure = False
        self._reader_first_read = True

        def catalog_list(tipo):
            rows = [dict(row, doc=dict(row["doc"])) for row in self._rows]
            if (threading.current_thread() is self._reader and tipo == "medicos"
                    and self._reader_first_read):
                self._reader_first_read = False
                self._reader_entered.set()
                if not self._release_reader.wait(2):
                    raise RuntimeError("old catalog reader was not released")
                if self._stale_failure:
                    self._stale_failure = False
                    raise catalog.db.DbUnavailable("stale catalog read failed")
            return rows

        def catalog_upsert(tipo, id_, doc, borrado=False):
            self.assertEqual(tipo, "medicos")
            self._rows = [{"id": str(id_), "doc": dict(doc), "borrado": borrado}]

        db_catalog.catalog_list = catalog_list
        db_catalog.catalog_upsert = catalog_upsert
        catalog._base_items_raw = lambda tipo: []
        catalog.invalidate()

    def tearDown(self):
        self._release_reader.set()
        if self._reader is not None:
            self._reader.join(2)
        db_catalog.catalog_list = self._saved_catalog_list
        db_catalog.catalog_upsert = self._saved_catalog_upsert
        catalog._base_items_raw = self._saved_base_items
        catalog._RAW_CACHE.clear()
        catalog._RAW_CACHE.update(self._saved_raw_cache)
        catalog._DB_FAIL_UNTIL.clear()
        catalog._DB_FAIL_UNTIL.update(self._saved_fail_until)
        if self._reader is not None:
            self.assertFalse(self._reader.is_alive(), "catalog reader leaked")

    def _start_old_reader(self, *, stale_failure=False):
        self._stale_failure = stale_failure

        def read_old_doctors():
            try:
                self._reader_result = catalog.doctors()
            except Exception as exc:  # surfaced after deterministic join
                self._reader_error = exc

        self._reader = threading.Thread(target=read_old_doctors,
                                        name="old-catalog-reader")
        self._reader.start()
        self.assertTrue(self._reader_entered.wait(2),
                        "reader did not reach the post-read cache gap")

    def _release_old_reader(self):
        self._release_reader.set()
        self._reader.join(2)
        self.assertFalse(self._reader.is_alive(), "catalog reader hung")
        if self._reader_error is not None:
            raise self._reader_error

    def test_door_token_write_cannot_be_hidden_by_an_inflight_old_catalog_read(self):
        self._start_old_reader()
        try:
            intake_admin._write_door_token(
                "qr-med-1", {"id": "qr-med-1", "nombre": "Doctor"}, "door-token")
            self.assertEqual(
                intake_admin.doctor_by_door_token("door-token")["id"], "qr-med-1")

            self._release_old_reader()
            self.assertEqual(self._reader_result[0]["entrevista_qr_token"],
                             "door-token")
            self.assertEqual(
                intake_admin.doctor_by_door_token("door-token")["id"], "qr-med-1")
        finally:
            self._release_old_reader()

    def test_stale_failure_cannot_restore_a_cooldown_after_door_token_write(self):
        self._start_old_reader(stale_failure=True)
        try:
            intake_admin._write_door_token(
                "qr-med-1", {"id": "qr-med-1", "nombre": "Doctor"}, "door-token")

            self._release_old_reader()
            self.assertEqual(self._reader_result[0]["entrevista_qr_token"],
                             "door-token")
            self.assertEqual(
                intake_admin.doctor_by_door_token("door-token")["id"], "qr-med-1")
        finally:
            self._release_old_reader()


class TestCatalogCacheRace(unittest.TestCase):
    """Run the process-global cache race away from suite background threads."""

    def _run_scenario(self, method):
        result = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning", __file__,
             "_DoorTokenCacheScenario." + method],
            cwd=ROOT, env={"PATH": os.defpath, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 1 test", result.stderr)
        self.assertNotIn("skipped", result.stderr)

    def test_door_token_write_cannot_be_hidden_by_an_inflight_old_catalog_read(self):
        self._run_scenario(
            "test_door_token_write_cannot_be_hidden_by_an_inflight_old_catalog_read")

    def test_stale_failure_cannot_restore_a_cooldown_after_door_token_write(self):
        self._run_scenario(
            "test_stale_failure_cannot_restore_a_cooldown_after_door_token_write")


def load_tests(loader, _tests, _pattern):
    return loader.loadTestsFromTestCase(TestCatalogCacheRace)


if __name__ == "__main__":
    unittest.main()
