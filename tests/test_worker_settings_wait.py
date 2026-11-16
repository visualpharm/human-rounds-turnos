"""The worker waits for the panel settings to be readable before it registers its jobs.

config.get reads a failed catalog read as «no overrides», so a worker booting while the web process still
applies migrations saw TURNOS_BACKUP_ENABLED off and ran no encrypted backup until the next deploy
(pinamar, 2026-11-13)."""
import unittest
from unittest import mock

from app import worker
from app.core import config, db_catalog


class _State:
    stop_requested = False


class TestAwaitSettingsDb(unittest.TestCase):
    def test_waits_until_the_catalog_answers_then_drops_the_cache(self):
        calls = {"n": 0}

        def flaky(kind, ident):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("relation catalog does not exist yet")
            return None

        with mock.patch.object(db_catalog, "catalog_get", flaky), \
                mock.patch.object(config, "_invalidate_db_cache") as invalidate:
            worker._await_settings_db(_State(), wait_s=5, poll_s=0)
        self.assertEqual(calls["n"], 3)
        invalidate.assert_called_once()

    def test_gives_up_after_the_bound_and_still_goes_on(self):
        def broken(kind, ident):
            raise RuntimeError("down")

        with mock.patch.object(db_catalog, "catalog_get", broken), \
                mock.patch.object(config, "_invalidate_db_cache") as invalidate, \
                mock.patch.object(worker, "_log") as log:
            worker._await_settings_db(_State(), wait_s=0, poll_s=0)
        invalidate.assert_called_once()
        self.assertTrue(any("unreadable" in str(c) for c in log.call_args_list))

    def test_a_stop_request_ends_the_wait(self):
        state = _State()
        state.stop_requested = True

        def broken(kind, ident):
            raise RuntimeError("down")

        with mock.patch.object(db_catalog, "catalog_get", broken), \
                mock.patch.object(config, "_invalidate_db_cache"), \
                mock.patch.object(worker, "_log"):
            worker._await_settings_db(state, wait_s=60, poll_s=60)


if __name__ == "__main__":
    unittest.main()
