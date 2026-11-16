"""ai_register.observe_inventory against a real Postgres.

The observation takes a transaction-scoped advisory lock, asks whether any snapshot exists
(`had_baseline`) and reads each function's latest snapshot. `test_ai_register_db` walks the
behavior through the settings path; this file pins the three reads/locks by their effects.
"""
import threading
import unittest

import _dbtest
from app.core import ai_register, db
from app.core import db_ai

_database = _dbtest.PerModuleDb("sana_turnos_test_ai_register_observe")


def setUpModule():
    _database.set_up()


def tearDownModule():
    _database.tear_down()


def changes(kind=None):
    rows = db_ai.ai_register_list_changes()
    return [r for r in rows if kind is None or r["kind"] == kind]


class TestObserveInventory(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE ai_register_changes, ai_prompts, ai_prompts_history, catalog")

    def rows(self):
        return ai_register.inventory()

    def test_first_observation_records_one_snapshot_per_function_and_no_change_events(self):
        rows = self.rows()
        ai_register.observe_inventory(rows)
        self.assertEqual(sorted(c["function_id"] for c in changes("snapshot")), sorted(r["id"] for r in rows))
        self.assertEqual(len(changes()), len(rows))

    def test_an_unchanged_inventory_writes_nothing_the_second_time(self):
        rows = self.rows()
        ai_register.observe_inventory(rows)
        ai_register.observe_inventory(rows)
        self.assertEqual(len(changes()), len(rows))

    def test_a_changed_function_records_the_change_and_a_new_snapshot_from_the_latest_one(self):
        rows = self.rows()
        ai_register.observe_inventory(rows)
        rows[0] = dict(rows[0], data_categories=rows[0]["data_categories"] + ["synthetic scope"])
        ai_register.observe_inventory(rows)
        fid = rows[0]["id"]
        moved = [c for c in changes("data_scope") if c["function_id"] == fid]
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0]["substantial"])
        self.assertEqual(len([c for c in changes("snapshot") if c["function_id"] == fid]), 2)
        ai_register.observe_inventory(rows)  # compared with the NEWEST snapshot: nothing new
        self.assertEqual(len([c for c in changes("snapshot") if c["function_id"] == fid]), 2)
        self.assertEqual(len([c for c in changes("data_scope") if c["function_id"] == fid]), 1)

    def test_a_function_that_appears_after_the_baseline_is_a_functionality_event(self):
        rows = self.rows()
        ai_register.observe_inventory(rows)
        extra = dict(rows[0], id="synthetic_new")
        ai_register.observe_inventory(rows + [extra])
        events = [c for c in changes("functionality") if c["function_id"] == "synthetic_new"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], "system:observed")

    def test_a_function_in_the_very_first_observation_is_not_an_event(self):
        ai_register.observe_inventory(self.rows(), record_changes=True)
        self.assertEqual(changes("functionality"), [])

    def test_record_changes_off_still_moves_the_snapshot_without_events(self):
        rows = self.rows()
        ai_register.observe_inventory(rows)
        rows[0] = dict(rows[0], data_categories=["synthetic only"])
        ai_register.observe_inventory(rows, record_changes=False)
        self.assertEqual(changes("data_scope"), [])
        self.assertEqual(len([c for c in changes("snapshot") if c["function_id"] == rows[0]["id"]]), 2)

    def test_a_second_observer_waits_for_the_advisory_lock(self):
        done = threading.Event()
        errors = []

        def observe():
            try:
                ai_register.observe_inventory(self.rows())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            done.set()

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('ai_register:observation'))")
            thread = threading.Thread(target=observe)
            thread.start()
            blocked = not done.wait(0.8)
        finished = done.wait(15)
        thread.join(5)
        self.assertEqual(errors, [])
        self.assertTrue(blocked)
        self.assertTrue(finished)
        self.assertEqual(len(changes("snapshot")), len(self.rows()))

    def test_the_observation_can_run_inside_the_callers_transaction(self):
        rows = self.rows()
        with db._connect() as conn:
            ai_register.observe_inventory(rows, connection=conn)
            conn.rollback()
        self.assertEqual(changes(), [])
        with db._connect() as conn:
            ai_register.observe_inventory(rows, connection=conn)
        self.assertEqual(len(changes("snapshot")), len(rows))


if __name__ == "__main__":
    unittest.main()
