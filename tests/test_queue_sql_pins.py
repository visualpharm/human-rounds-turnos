"""test_queue_sql_pins.py — the SQL of the queue plumbing, pinned before it moved to SQLAlchemy Core.

The self-rescheduling chains of `hsi_sync` and of the backup sweep keep their state in the queue
library's tables and coordinate through Postgres advisory locks. The worker suites
(`test_queue_worker`, `test_hsi_queue_proof`, `test_data_backups_worker_proof`) prove the behavior
with real subprocesses; this file calls the small functions directly and checks what each one reads,
which lock KEY it takes (a second process holds the same one), and that it waits for a holder.

Run with discover (see CONTRIBUTING.md); own database `sana_turnos_test_queue_sql`.
"""
import asyncio
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db, queue_infra  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.modules.turnos import hsi_journal_queue as hsq  # noqa: E402
from app.ops import data_backups, data_backups_queue as dbq  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_queue_sql")
HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL = False, None, None, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url
    if HAVE_DB:
        queue_infra.ensure_schema()


def tearDownModule():
    _db.tear_down()


def _conn(url=None):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(url or _TEST_URL, row_factory=dict_row, autocommit=True, connect_timeout=5)


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with _conn() as c:
            c.execute("TRUNCATE procrastinate_events, procrastinate_jobs RESTART IDENTITY CASCADE")

    def _job(self, task, status):
        with _conn() as c:
            return c.execute("INSERT INTO procrastinate_jobs (queue_name, task_name, status) "
                             "VALUES ('default', %s, %s) RETURNING id", (task, status)).fetchone()["id"]

    def _event(self, job_id, kind, at):
        with _conn() as c:
            c.execute("INSERT INTO procrastinate_events (job_id, type, at) VALUES (%s, %s, %s)",
                      (job_id, kind, at))


class TestSchemaPresent(DbCase):
    def test_true_once_the_queue_schema_is_applied_and_false_on_a_database_without_it(self):
        with _conn() as c, c.cursor() as cur:
            self.assertTrue(queue_infra._schema_present(cur))
        name = _dbtest.run_scoped("sana_turnos_test_queue_sql_bare")
        with _conn(_MAINT_URL) as c:
            c.execute('CREATE DATABASE "%s"' % name)
        try:
            with _conn(_MAINT_URL.rsplit("/", 1)[0] + "/" + name) as c, c.cursor() as cur:
                self.assertFalse(queue_infra._schema_present(cur))
        finally:
            with _conn(_MAINT_URL) as c:
                c.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name)

    def test_applying_the_schema_waits_for_whoever_holds_the_schema_lock(self):
        """Two processes booting at once: the second takes the same key and must wait."""
        name = _dbtest.run_scoped("sana_turnos_test_queue_sql_lock")
        with _conn(_MAINT_URL) as c:
            c.execute('CREATE DATABASE "%s"' % name)
        url = _MAINT_URL.rsplit("/", 1)[0] + "/" + name
        try:
            with _env.patched_env({"DATABASE_URL": url}):
                db_schema._ensured.clear()
                db._connect().close()    # Alembic head first, so only the queue schema is missing
                holder = _conn(url)
                holder.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)", (queue_infra.SCHEMA_LOCK_KEY,))
                done = {}
                worker = threading.Thread(target=lambda: done.update(applied=queue_infra.ensure_schema()))
                worker.start()
                worker.join(1.0)
                self.assertTrue(worker.is_alive(), "ensure_schema did not wait for the lock holder")
                holder.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (queue_infra.SCHEMA_LOCK_KEY,))
                worker.join(30)
                self.assertEqual(done, {"applied": True})
                holder.close()
                with _conn(url) as c, c.cursor() as cur:
                    self.assertTrue(queue_infra._schema_present(cur))
                self.assertFalse(queue_infra.ensure_schema())
        finally:
            db_schema._ensured.discard(url)
            db.close_pools()
            db_schema._ensured.clear()
            with _conn(_MAINT_URL) as c:
                c.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name)


class TestReleaseGate(DbCase):
    ENV = {"TURNOS_TEST_SEAMS": "1", "TURNOS_TEST_SWEEP_RELEASE_GATE_KEY": "pin"}

    def test_a_held_gate_fails_closed_and_a_released_one_lets_the_sweep_run(self):
        holder = _conn()
        holder.execute("SELECT pg_advisory_lock(hashtext(%s))", ("test_sweep_release_gate:pin",))
        try:
            with _env.patched_env(self.ENV):
                with self.assertRaises(RuntimeError):
                    queue_infra._wait_for_test_release_gate(timeout_s=0.3, poll_s=0.05)
                holder.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("test_sweep_release_gate:pin",))
                queue_infra._wait_for_test_release_gate(timeout_s=2, poll_s=0.05)
        finally:
            holder.close()

    def test_the_spawning_test_sets_the_gate_timeout_through_the_environment(self):
        holder = _conn()
        holder.execute("SELECT pg_advisory_lock(hashtext(%s))", ("test_sweep_release_gate:pin",))
        try:
            with _env.patched_env({**self.ENV, "TURNOS_TEST_SWEEP_RELEASE_GATE_TIMEOUT_S": "0.3"}):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "within 0.3s"):
                    queue_infra._wait_for_test_release_gate(poll_s=0.05)
                self.assertLess(time.monotonic() - started, 20)
        finally:
            holder.close()

    def test_another_key_does_not_wait_and_the_seam_is_inert_without_its_flag(self):
        holder = _conn()
        holder.execute("SELECT pg_advisory_lock(hashtext(%s))", ("test_sweep_release_gate:pin",))
        try:
            with _env.patched_env({**self.ENV, "TURNOS_TEST_SWEEP_RELEASE_GATE_KEY": "other"}):
                queue_infra._wait_for_test_release_gate(timeout_s=0.3, poll_s=0.05)
            with _env.patched_env({"TURNOS_TEST_SWEEP_RELEASE_GATE_KEY": "pin", "TURNOS_TEST_SEAMS": None}):
                queue_infra._wait_for_test_release_gate(timeout_s=0.3, poll_s=0.05)
        finally:
            holder.close()


class ChainMixin:
    """The same three functions exist for each self-rescheduling chain."""
    module = None

    def test_chain_alive_sees_a_todo_or_doing_job_of_its_own_task_only(self):
        m = self.module
        self.assertFalse(m.chain_alive())
        self._job("otra.tarea", "todo")
        self._job(m._TASK_NAME, "succeeded")
        self._job(m._TASK_NAME, "failed")
        self.assertFalse(m.chain_alive())
        with _conn() as c, c.cursor() as cur:
            self.assertFalse(m.chain_alive(cur))
        self._job(m._TASK_NAME, "todo")
        self.assertTrue(m.chain_alive())
        with _conn() as c, c.cursor() as cur:
            self.assertTrue(m.chain_alive(cur))
        with _conn() as c:
            c.execute("TRUNCATE procrastinate_events, procrastinate_jobs RESTART IDENTITY CASCADE")
        self._job(m._TASK_NAME, "doing")
        self.assertTrue(m.chain_alive())

    def test_the_rekick_lock_is_taken_on_the_task_key_and_waits_for_a_holder(self):
        m = self.module
        holder = _conn()
        holder.execute("SELECT pg_advisory_lock(hashtext(%s))", (m._TASK_NAME + ":rekick",))
        other = _conn()
        try:
            with other.cursor() as cur:
                self.assertFalse(asyncio.run(m._acquire_rekick_lock(cur, timeout_s=0.3, poll_s=0.05)))
            holder.execute("SELECT pg_advisory_unlock(hashtext(%s))", (m._TASK_NAME + ":rekick",))
            with other.cursor() as cur:
                self.assertTrue(asyncio.run(m._acquire_rekick_lock(cur, timeout_s=2, poll_s=0.05)))
                # session lock: held now, so the former holder cannot take it
                self.assertFalse(holder.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok",
                                                (m._TASK_NAME + ":rekick",)).fetchone()["ok"])
        finally:
            other.close()
            holder.close()

    def _succeeded(self, task, at):
        self._event(self._job(task, "succeeded"), "succeeded", at)


class TestHsiChain(ChainMixin, DbCase):
    module = hsq

    def _next(self, interval_s):
        with _conn() as c, c.cursor() as cur:
            return hsq._next_schedule_at(cur, interval_s)

    def test_no_success_yet_is_a_cold_start(self):
        self.assertIsNone(self._next(600))
        # events of other kinds or of another task do not count
        self._event(self._job(hsq._TASK_NAME, "doing"), "started", datetime.now(timezone.utc))
        self._succeeded("otra.tarea", datetime.now(timezone.utc))
        self.assertIsNone(self._next(600))

    def test_next_tick_is_the_last_success_plus_the_interval(self):
        at = datetime.now(timezone.utc)
        self._succeeded(hsq._TASK_NAME, at - timedelta(seconds=10))
        self._succeeded(hsq._TASK_NAME, at)
        got = self._next(600)
        self.assertEqual(got.tzinfo is not None, True)
        self.assertAlmostEqual((got - at).total_seconds(), 600, delta=1)

    def test_an_interval_already_elapsed_clamps_to_now(self):
        before = datetime.now(timezone.utc)
        self._succeeded(hsq._TASK_NAME, before - timedelta(hours=3))
        got = self._next(60)
        self.assertGreaterEqual(got, before)
        self.assertLess(got - datetime.now(timezone.utc), timedelta(seconds=2))

    def test_the_gate_waits_for_its_holder_then_frees_its_own_lock(self):
        env = {"TURNOS_TEST_SEAMS": "1", "TURNOS_TEST_HSI_REKICK_GATE_KEY": "pin"}
        holder = _conn()
        holder.execute("SELECT pg_advisory_lock(hashtext(%s))", ("test_rekick_gate:pin",))
        other = _conn()
        try:
            with _env.patched_env(env), other.cursor() as cur:
                t0 = time.monotonic()
                asyncio.run(hsq._maybe_rekick_release_gate(cur, timeout_s=0.4, poll_s=0.05))
                self.assertGreaterEqual(time.monotonic() - t0, 0.35)    # gave up: nobody released it
                holder.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("test_rekick_gate:pin",))
                t0 = time.monotonic()
                asyncio.run(hsq._maybe_rekick_release_gate(cur, timeout_s=5, poll_s=0.05))
                self.assertLess(time.monotonic() - t0, 1)
                # one-shot rendezvous: it took the key and gave it back at once
                self.assertTrue(holder.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok",
                                               ("test_rekick_gate:pin",)).fetchone()["ok"])
        finally:
            other.close()
            holder.close()

    def test_the_spawning_test_sets_the_gate_timeout_through_the_environment(self):
        env = {"TURNOS_TEST_SEAMS": "1", "TURNOS_TEST_HSI_REKICK_GATE_KEY": "pin",
               "TURNOS_TEST_HSI_REKICK_GATE_TIMEOUT_S": "0.4"}
        holder = _conn()
        holder.execute("SELECT pg_advisory_lock(hashtext(%s))", ("test_rekick_gate:pin",))
        other = _conn()
        try:
            with _env.patched_env(env), other.cursor() as cur:
                t0 = time.monotonic()
                asyncio.run(hsq._maybe_rekick_release_gate(cur, poll_s=0.05))
                elapsed = time.monotonic() - t0
                self.assertGreaterEqual(elapsed, 0.35)    # waited for the env's timeout...
                self.assertLess(elapsed, 20)              # ...not the 30 s default
        finally:
            other.close()
            holder.close()

    def test_the_gate_does_nothing_without_the_seam(self):
        other = _conn()
        try:
            with other.cursor() as cur:
                asyncio.run(hsq._maybe_rekick_release_gate(cur, timeout_s=0.2, poll_s=0.05))
        finally:
            other.close()


class TestBackupChain(ChainMixin, DbCase):
    module = dbq

    def _next(self, interval_s, grace_s=60):
        with _conn() as c, c.cursor() as cur:
            return dbq._next_schedule_at(cur, interval_s, grace_s)

    def test_a_cold_start_waits_the_startup_grace(self):
        before = datetime.now(timezone.utc)
        got = self._next(600, 45)
        self.assertAlmostEqual((got - before).total_seconds(), 45, delta=2)
        self._event(self._job(dbq._TASK_NAME, "doing"), "started", datetime.now(timezone.utc))
        self._succeeded("otra.tarea", datetime.now(timezone.utc))
        self.assertAlmostEqual((self._next(600, 45) - before).total_seconds(), 45, delta=2)

    def test_after_a_success_the_next_tick_is_that_success_plus_the_interval(self):
        at = datetime.now(timezone.utc)
        self._succeeded(dbq._TASK_NAME, at - timedelta(seconds=10))
        self._succeeded(dbq._TASK_NAME, at)
        self.assertAlmostEqual((self._next(600, 45) - at).total_seconds(), 600, delta=1)

    def test_an_interval_already_elapsed_clamps_to_now(self):
        before = datetime.now(timezone.utc)
        self._succeeded(dbq._TASK_NAME, before - timedelta(hours=3))
        got = self._next(60, 45)
        self.assertGreaterEqual(got, before)
        self.assertLess(got - datetime.now(timezone.utc), timedelta(seconds=2))


class TestSweepLock(DbCase):
    def test_the_sweep_holds_one_session_lock_and_frees_it_on_exit(self):
        key = data_backups._SWEEP_LOCK_KEY
        other = _conn()
        try:
            with data_backups._sweep_lock() as held:
                self.assertTrue(held)
                self.assertFalse(other.execute("SELECT pg_try_advisory_lock(hashtext(%s)::bigint) AS ok",
                                               (key,)).fetchone()["ok"])
            self.assertTrue(other.execute("SELECT pg_try_advisory_lock(hashtext(%s)::bigint) AS ok",
                                          (key,)).fetchone()["ok"])
        finally:
            other.close()

    def test_a_second_sweep_waits_for_the_first(self):
        key = data_backups._SWEEP_LOCK_KEY
        holder = _conn()
        holder.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)", (key,))
        got = {}

        def run():
            with data_backups._sweep_lock() as held:
                got["held"] = bool(held)
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(0.8)
        self.assertTrue(worker.is_alive(), "the second sweep did not wait")
        holder.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (key,))
        worker.join(10)
        holder.close()
        self.assertEqual(got, {"held": True})


if __name__ == "__main__":
    unittest.main()
