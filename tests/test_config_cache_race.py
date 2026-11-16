"""Regression for config reads publishing stale data after invalidation."""

import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import _env
from app import connectors
from app.core import config, db
from app.core import db_catalog
from app.core import db_ai


class _CacheRaceScenario(unittest.TestCase):
    def setUp(self):
        self._saved_catalog_get = db_catalog.catalog_get
        self._saved_catalog_upsert = db_catalog.catalog_upsert
        self._saved_env_path = config.ENV_PATH
        self._saved_db_cache = dict(config._db_cache)
        self._saved_dotenv_cache = dict(config._dotenv_cache)
        self._store = {"CONNECTOR_EMR_SYNC": "off"}
        self._old_reader_entered = threading.Event()
        self._release_old_reader = threading.Event()
        self._old_reader = None
        self._reader_error = None
        self._reader_result = None

        case = self
        # Keep the controlled race at the catalog seam while honoring the
        # atomic config/audit transaction introduced by slice 8.
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = lambda: {'doc':dict(self._store)}
        for transaction_patch in (patch.object(db,'_connect',return_value=conn),
                                  patch.object(db_ai,'ai_register_append')):
            transaction_patch.start()
            self.addCleanup(transaction_patch.stop)

        class DelayedRow(dict):
            def get(self, key, default=None):
                if (threading.current_thread() is case._old_reader
                        and key == "doc"):
                    case._old_reader_entered.set()
                    if not case._release_old_reader.wait(2):
                        raise RuntimeError("old config reader was not released")
                return super().get(key, default)

        def catalog_get(*_args):
            return DelayedRow(doc=dict(self._store))

        def catalog_upsert(*args, **kwargs):
            self._store.clear()
            self._store.update(args[-1])

        db_catalog.catalog_get = catalog_get
        db_catalog.catalog_upsert = catalog_upsert
        config.ENV_PATH = ""
        _env.set_env(self, CONNECTOR_EMR_SYNC=None, TURNOS_CONFIG_LOCK=None, TURNOS_ENV_FILE=None)
        config._reset_cache_para_tests()

    def tearDown(self):
        self._release_old_reader.set()
        if self._old_reader is not None:
            self._old_reader.join(2)
        db_catalog.catalog_get = self._saved_catalog_get
        db_catalog.catalog_upsert = self._saved_catalog_upsert
        config.ENV_PATH = self._saved_env_path
        # Invalidate outstanding reads, but never roll a generation backwards.
        config._reset_cache_para_tests()
        with config._db_cache_lock:
            config._db_cache.clear()
            config._db_cache.update(self._saved_db_cache)
        config._dotenv_cache.clear()
        config._dotenv_cache.update(self._saved_dotenv_cache)
        if self._old_reader is not None:
            self.assertFalse(self._old_reader.is_alive(), "old config reader leaked")

    def _start_old_reader(self):
        def read_old_value():
            try:
                # This is the shape used by a background config.get() call.
                self._reader_result = config.get("CONNECTOR_EMR_SYNC")
            except Exception as exc:  # surfaced after deterministic join
                self._reader_error = exc

        self._old_reader = threading.Thread(target=read_old_value,
                                            name="old-config-reader")
        self._old_reader.start()
        self.assertTrue(self._old_reader_entered.wait(2),
                        "reader did not reach the post-read cache gap")

    def _release_reader(self):
        self._release_old_reader.set()
        self._old_reader.join(2)
        self.assertFalse(self._old_reader.is_alive(), "old config reader hung")
        if self._reader_error is not None:
            raise self._reader_error

    def test_guardar_cannot_be_overwritten_after_selected_resolution(self):
        self._start_old_reader()
        try:
            config.guardar({"CONNECTOR_EMR_SYNC": "hsi"})
            self.assertEqual(connectors.resolve(connectors.EMR_SYNC),
                             ("hsi", "explicit"))

            self._release_reader()
            self.assertEqual(self._reader_result, "hsi")
            self.assertEqual(connectors.resolve(connectors.EMR_SYNC),
                             ("hsi", "explicit"))
        finally:
            self._release_reader()

    def test_reset_cannot_be_overwritten_by_an_inflight_old_read(self):
        self._start_old_reader()
        try:
            self._store["CONNECTOR_EMR_SYNC"] = "hsi"
            config._reset_cache_para_tests()
            self.assertEqual(connectors.resolve(connectors.EMR_SYNC),
                             ("hsi", "explicit"))

            self._release_reader()
            self.assertEqual(self._reader_result, "hsi")
            self.assertEqual(connectors.resolve(connectors.EMR_SYNC),
                             ("hsi", "explicit"))
        finally:
            self._release_reader()


class TestConfigCachePublicationRace(unittest.TestCase):
    """Keep unrelated full-suite background threads out of the controlled race.

    The scenario changes process-wide catalog readers and requires a cold
    cache. An unrelated thread can fill it before the designated reader starts,
    invalidating the setup without exercising publication after invalidation.
    A fresh process owns both the cache and every participant in the race.
    """

    def _run_scenario(self, method):
        result = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning", __file__,
             "_CacheRaceScenario." + method],
            cwd=ROOT, env={"PATH": os.defpath, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 1 test", result.stderr)
        self.assertNotIn("skipped", result.stderr)

    def test_guardar_cannot_be_overwritten_after_selected_resolution(self):
        self._run_scenario("test_guardar_cannot_be_overwritten_after_selected_resolution")

    def test_reset_cannot_be_overwritten_by_an_inflight_old_read(self):
        self._run_scenario("test_reset_cannot_be_overwritten_by_an_inflight_old_read")


def load_tests(loader, _tests, _pattern):
    return loader.loadTestsFromTestCase(TestConfigCachePublicationRace)


if __name__ == "__main__":
    unittest.main()
