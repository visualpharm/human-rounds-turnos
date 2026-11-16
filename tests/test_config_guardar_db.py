"""config.guardar against a real Postgres: the read-merge-write of the settings overlay.

`guardar` takes a transaction-scoped advisory lock, reads the saved overlay with a row lock
(`SELECT doc ... FOR UPDATE`), merges the requested keys (None / "" deletes a key) and
writes the result back together with its audit events. The statements that carry the lock
and the read are pinned here by their effects: the merge is built on what the database
holds, and a second writer waits for the first one's transaction.
"""
import threading
import unittest

import _dbtest
from app.core import ai_register, config, db
from app.core import db_catalog
from app.core import db_ai

_database = _dbtest.PerModuleDb("sana_turnos_test_config_guardar")


def setUpModule():
    _database.set_up()


def tearDownModule():
    _database.tear_down()


class TestGuardar(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE ai_register_changes, ai_prompts, ai_prompts_history, catalog")
        config._reset_cache_para_tests()

    def stored(self):
        row = db_catalog.catalog_get("config", "settings")
        return row["doc"] if row else None

    def test_overrides_merge_into_what_is_stored_and_none_or_empty_removes_a_key(self):
        self.assertEqual(config.guardar({"SYNTH_A": "1"}), {"SYNTH_A": "1"})
        self.assertEqual(config.guardar({"SYNTH_B": "2"}), {"SYNTH_A": "1", "SYNTH_B": "2"})
        self.assertEqual(self.stored(), {"SYNTH_A": "1", "SYNTH_B": "2"})
        self.assertEqual(config.guardar({"SYNTH_A": None, "SYNTH_C": "3"}), {"SYNTH_B": "2", "SYNTH_C": "3"})
        self.assertEqual(config.guardar({"SYNTH_B": ""}), {"SYNTH_C": "3"})
        self.assertEqual(self.stored(), {"SYNTH_C": "3"})
        self.assertEqual(config.guardar({"SYNTH_ABSENT": None}), {"SYNTH_C": "3"})

    def test_the_audit_compares_with_the_stored_overlay_not_with_the_cache(self):
        config.guardar({"TURNOS_AI_MODEL": "synthetic/one"})
        events = [c for c in db_ai.ai_register_list_changes() if c["kind"] == "model"]
        self.assertTrue(events)
        before = len(events)
        config.guardar({"TURNOS_AI_MODEL": "synthetic/one"})  # same value: nothing to audit
        self.assertEqual(len([c for c in db_ai.ai_register_list_changes() if c["kind"] == "model"]), before)
        config.guardar({"TURNOS_AI_MODEL": "synthetic/two"})
        after = [c for c in db_ai.ai_register_list_changes() if c["kind"] == "model"]
        self.assertGreater(len(after), before)
        newest = max(after, key=lambda c: c["id"])["details"]
        self.assertEqual(newest["before"], ai_register.fingerprint("synthetic/one"))
        self.assertEqual(newest["after"], ai_register.fingerprint("synthetic/two"))

    def test_installation_keys_never_reach_the_database(self):
        with self.assertRaises(ValueError):
            config.guardar({"TURNOS_BACKUP_RECOVERY_KEY": "x"})
        self.assertIsNone(self.stored())

    def _blocked_until_released(self, hold):
        """Runs guardar in a thread while `hold(cur)` keeps a lock from another transaction;
        returns (was_blocked_while_held, finished_after_release)."""
        done = threading.Event()
        errors = []

        def writer():
            try:
                config.guardar({"SYNTH_LOCKED": "1"})
            except Exception as exc:  # noqa: BLE001 — surfaced by the assertion below
                errors.append(exc)
            done.set()

        with db._connect() as conn, conn.cursor() as cur:
            hold(cur)
            thread = threading.Thread(target=writer)
            thread.start()
            blocked = not done.wait(0.8)
        finished = done.wait(10)
        thread.join(5)
        self.assertEqual(errors, [])
        return blocked, finished

    def test_a_second_writer_waits_for_the_advisory_lock(self):
        blocked, finished = self._blocked_until_released(
            lambda cur: cur.execute("SELECT pg_advisory_xact_lock(hashtext('config:settings'))"))
        self.assertTrue(blocked)
        self.assertTrue(finished)
        self.assertEqual(self.stored(), {"SYNTH_LOCKED": "1"})

    def test_a_second_writer_waits_for_the_row_lock_of_the_overlay(self):
        config.guardar({"SYNTH_FIRST": "1"})
        blocked, finished = self._blocked_until_released(
            lambda cur: cur.execute("SELECT doc FROM catalog WHERE type='config' AND id='settings' FOR UPDATE"))
        self.assertTrue(blocked)
        self.assertTrue(finished)
        self.assertEqual(self.stored(), {"SYNTH_FIRST": "1", "SYNTH_LOCKED": "1"})


if __name__ == "__main__":
    unittest.main()
