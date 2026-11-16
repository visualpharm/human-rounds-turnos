"""Regression for a swallowed periodic-side-task cancellation.

Procrastinate's native ``Worker._shutdown`` cancels side tasks once and then
gathers them. If downstream DB-pool cleanup consumes that cancellation, the
stock PeriodicDeferrer proceeds to its next cron sleep and blocks native
unregistration. The red control demonstrates this; the guarded real Worker
instance exits through the same native shutdown path.
"""
import asyncio
import datetime
import logging
import os
import sys
import unittest
from collections import deque
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from procrastinate import App, periodic, testing  # noqa: E402
from procrastinate.worker import Worker  # noqa: E402
from psycopg.pq import TransactionStatus  # noqa: E402
from psycopg_pool import AsyncConnectionPool  # noqa: E402
from app import worker  # noqa: E402


class TestPeriodicCancellationGuard(unittest.TestCase):
    def test_swallowed_defer_cancellation_does_not_block_native_unregistration(self):
        asyncio.run(self._exercise_native_shutdown())

    async def _exercise_native_shutdown(self):
        entered_sleep = asyncio.Event()
        original_wait = periodic.PeriodicDeferrer.wait
        seams = deque()

        class _PgConn:
            transaction_status = TransactionStatus.IDLE

        class _Connection:
            def __init__(self):
                self.pgconn = _PgConn()
                self._pool = object()
                self.close_calls = 0

            async def close(self):
                self.close_calls += 1

        def reset_seam():
            entered_reset = asyncio.Event()
            cancellation_retained = asyncio.Event()
            connection = _Connection()

            async def reset(_connection):
                entered_reset.set()
                await asyncio.Event().wait()

            # This is the installed psycopg_pool cleanup path: its
            # _reset_connection() catches CancelledError from reset() and
            # closes the connection without re-raising it.
            pool = AsyncConnectionPool("", open=False, reset=reset)
            return (pool, connection, entered_reset, cancellation_retained)

        async def defer_through_real_pool_reset(self, jobs_to_defer):
            pool, connection, _entered, cancellation_retained = seams.popleft()
            await pool._reset_connection(connection)
            if asyncio.current_task().cancelling():
                cancellation_retained.set()

        async def mark_real_sleep(self, next_tick):
            entered_sleep.set()
            await original_wait(self, next_tick)

        with patch.object(periodic.PeriodicDeferrer, "defer_jobs",
                          defer_through_real_pool_reset), \
             patch.object(periodic.PeriodicDeferrer, "wait", mark_real_sleep), \
             patch.object(logging.getLogger("psycopg.pool"), "warning"):
            # RED control: a stock installed Worker reaches the actual
            # PeriodicDeferrer sleep after its sole cancellation is consumed;
            # native Worker._shutdown is still gathering it.
            stock, stock_app = await self._worker(guarded=False)
            stock_seam = reset_seam()
            seams.append(stock_seam)
            stock_side = asyncio.create_task(stock._periodic_deferrer())
            stock_shutdown = None
            try:
                await asyncio.wait_for(stock_seam[2].wait(), timeout=1)
                stock_shutdown = asyncio.create_task(stock._shutdown([stock_side]))
                await asyncio.wait_for(entered_sleep.wait(), timeout=1)
                self.assertFalse(stock_shutdown.done())
                self.assertEqual(stock_seam[1].close_calls, 1,
                                 "installed pool reset must close after catching cancellation")
                self.assertTrue(stock_seam[3].is_set(),
                                "the task cancellation must survive the pool catch")
            finally:
                if not stock_side.done():
                    stock_side.cancel()  # required cleanup of the red control
                if stock_shutdown is not None:
                    await asyncio.wait_for(stock_shutdown, timeout=1)
                else:
                    await asyncio.gather(stock_side, return_exceptions=True)
            self.assertEqual(stock_app.connector.workers, {})

            # GREEN: the same real Worker class, real PeriodicDeferrer.worker,
            # and native _shutdown never enter sleep after the first
            # cancellation was consumed; unregister_worker runs normally.
            entered_sleep.clear()
            guarded, guarded_app = await self._worker(guarded=True)
            guarded_seam = reset_seam()
            seams.append(guarded_seam)
            guarded_side = asyncio.create_task(guarded._periodic_deferrer())
            guarded_shutdown = None
            try:
                await asyncio.wait_for(guarded_seam[2].wait(), timeout=1)
                guarded_shutdown = asyncio.create_task(guarded._shutdown([guarded_side]))
                await asyncio.wait_for(guarded_shutdown, timeout=1)
                self.assertFalse(entered_sleep.is_set())
                self.assertTrue(guarded_side.cancelled())
                self.assertEqual(guarded_seam[1].close_calls, 1)
                self.assertTrue(guarded_seam[3].is_set())
            finally:
                if not guarded_side.done():
                    guarded_side.cancel()
                if guarded_shutdown is not None and not guarded_shutdown.done():
                    await asyncio.wait_for(guarded_shutdown, timeout=1)
                else:
                    await asyncio.gather(guarded_side, return_exceptions=True)
            self.assertEqual(guarded_app.connector.workers, {})

    async def _worker(self, guarded):
        app = App(connector=testing.InMemoryConnector())

        @app.periodic(cron="* * * * *")
        @app.task(queue="periodic-test")
        async def periodic_probe(timestamp):
            return timestamp

        instance = Worker(app=app, queues=["periodic-test"],
                          name="periodic-cancellation-test",
                          install_signal_handlers=False)
        instance.worker_id = await app.job_manager.register_worker()
        instance._running_jobs = {}
        if guarded:
            worker._guard_periodic_deferrer_cancellation(instance)
        return instance, app

    def test_guard_keeps_an_uncancelled_periodic_wait_normal(self):
        async def run():
            registry = periodic.PeriodicRegistry()
            deferrer = worker._cancellation_aware_periodic_deferrer()(registry)
            await deferrer.wait(0)

        asyncio.run(run())

    def test_swallowed_heartbeat_cancellation_does_not_block_native_unregistration(self):
        asyncio.run(self._exercise_heartbeat_shutdown())

    def test_heartbeat_guard_keeps_uncancelled_update_normal(self):
        async def run():
            instance, app = await self._worker(guarded=False)
            calls = 0

            async def update_heartbeat(_worker_id):
                nonlocal calls
                calls += 1

            instance.app.job_manager.update_heartbeat = update_heartbeat
            worker._guard_heartbeat_update_cancellation(instance)
            await instance.app.job_manager.update_heartbeat(instance.worker_id)
            self.assertEqual(calls, 1)
            await app.job_manager.unregister_worker(instance.worker_id)
            self.assertEqual(app.connector.workers, {})

        asyncio.run(run())

    def test_heartbeat_guard_calls_real_manager_and_preserves_other_apps(self):
        async def run():
            instance, app = await self._worker(guarded=False)
            other, other_app = await self._worker(guarded=False)
            other_update = other_app.job_manager.update_heartbeat
            old_heartbeat = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
            app.connector.workers[instance.worker_id] = old_heartbeat
            try:
                worker._guard_heartbeat_update_cancellation(instance)
                # Real JobsManager and connector dispatch, not a fake heartbeat.
                result = await app.job_manager.update_heartbeat(instance.worker_id)
                self.assertIsNone(result)
                self.assertGreater(app.connector.workers[instance.worker_id], old_heartbeat)
                self.assertEqual(other_app.job_manager.update_heartbeat, other_update)
            finally:
                await app.job_manager.unregister_worker(instance.worker_id)
                await other_app.job_manager.unregister_worker(other.worker_id)
            self.assertEqual(app.connector.workers, {})
            self.assertEqual(other_app.connector.workers, {})

        asyncio.run(run())

    async def _exercise_heartbeat_shutdown(self):
        class _PgConn:
            transaction_status = TransactionStatus.IDLE

        class _Connection:
            def __init__(self):
                self.pgconn = _PgConn()
                self._pool = object()
                self.close_calls = 0

            async def close(self):
                self.close_calls += 1

        def reset_seam():
            entered_reset = asyncio.Event()
            cancellation_retained = asyncio.Event()
            connection = _Connection()

            async def reset(_connection):
                entered_reset.set()
                await asyncio.Event().wait()

            return (AsyncConnectionPool("", open=False, reset=reset), connection,
                    entered_reset, cancellation_retained)

        def install_pool_reset(instance, seam, entered_again):
            pool, connection, _entered, cancellation_retained = seam
            calls = 0

            async def update_heartbeat(_worker_id):
                nonlocal calls
                calls += 1
                if calls == 1:
                    await pool._reset_connection(connection)
                    if asyncio.current_task().cancelling():
                        cancellation_retained.set()
                    return
                entered_again.set()
                await asyncio.Event().wait()

            instance.app.job_manager.update_heartbeat = update_heartbeat

        # RED control: native heartbeat loops again once pool cleanup has
        # consumed the sole shutdown cancellation, so native _shutdown waits.
        stock, stock_app = await self._worker(guarded=False)
        stock.update_heartbeat_interval = 0
        stock_seam = reset_seam()
        stock_again = asyncio.Event()
        install_pool_reset(stock, stock_seam, stock_again)
        stock_side = asyncio.create_task(stock._update_heartbeat())
        stock_shutdown = None
        try:
            await asyncio.wait_for(stock_seam[2].wait(), timeout=1)
            stock_shutdown = asyncio.create_task(stock._shutdown([stock_side]))
            await asyncio.wait_for(stock_again.wait(), timeout=1)
            self.assertFalse(stock_shutdown.done())
            self.assertEqual(stock_seam[1].close_calls, 1)
            self.assertTrue(stock_seam[3].is_set())
        finally:
            if not stock_side.done():
                stock_side.cancel()
            if stock_shutdown is not None:
                await asyncio.wait_for(stock_shutdown, timeout=1)
            else:
                await asyncio.gather(stock_side, return_exceptions=True)
        self.assertEqual(stock_app.connector.workers, {})

        # GREEN: the app-local call-boundary adapter re-raises before the
        # native heartbeat loop starts another interval; native unregister runs.
        guarded, guarded_app = await self._worker(guarded=False)
        guarded.update_heartbeat_interval = 0
        guarded_seam = reset_seam()
        guarded_again = asyncio.Event()
        install_pool_reset(guarded, guarded_seam, guarded_again)
        worker._guard_heartbeat_update_cancellation(guarded)
        guarded_side = asyncio.create_task(guarded._update_heartbeat())
        guarded_shutdown = None
        try:
            await asyncio.wait_for(guarded_seam[2].wait(), timeout=1)
            guarded_shutdown = asyncio.create_task(guarded._shutdown([guarded_side]))
            await asyncio.wait_for(guarded_shutdown, timeout=1)
            self.assertFalse(guarded_again.is_set())
            self.assertTrue(guarded_side.cancelled())
            self.assertEqual(guarded_seam[1].close_calls, 1)
            self.assertTrue(guarded_seam[3].is_set())
        finally:
            if not guarded_side.done():
                guarded_side.cancel()
            if guarded_shutdown is not None and not guarded_shutdown.done():
                await asyncio.wait_for(guarded_shutdown, timeout=1)
            else:
                await asyncio.gather(guarded_side, return_exceptions=True)
        self.assertEqual(guarded_app.connector.workers, {})
