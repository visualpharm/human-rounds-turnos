"""The two numbers that decide when a burst of requests becomes 503s.

Thread limiter (`TURNOS_THREADPOOL_SIZE`, core/concurrency.py) and database
pool (`TURNOS_DB_POOL`, `TURNOS_DB_POOL_TIMEOUT`, core/db.py): each reads the
environment, falls back to the value the app has always run with when the
variable is unset or unusable, and never turns into «wait forever».
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import concurrency  # noqa: E402
from app.core import db  # noqa: E402


class TestDefaultsAreTheOldValues(unittest.TestCase):
    def test_nothing_set_means_15_connections_3_seconds_40_threads(self):
        with _env.patched_env(TURNOS_DB_POOL=None, TURNOS_DB_POOL_TIMEOUT=None, TURNOS_THREADPOOL_SIZE=None):
            self.assertEqual(db._pool_size(), 15)
            self.assertEqual(db._pool_timeout(), 3.0)
            self.assertEqual(concurrency.threadpool_size(), 40)


class TestPoolTimeout(unittest.TestCase):
    def test_reads_seconds_from_the_environment(self):
        for raw, expected in (("5", 5.0), ("0.5", 0.5), (" 12 ", 12.0)):
            with self.subTest(raw=raw), _env.patched_env(TURNOS_DB_POOL_TIMEOUT=raw):
                self.assertEqual(db._pool_timeout(), expected)

    def test_unusable_values_fall_back_to_the_default(self):
        for raw in ("", "abc", "0", "-1", "nan", "inf", "1e999"):
            with self.subTest(raw=raw), _env.patched_env(TURNOS_DB_POOL_TIMEOUT=raw):
                self.assertEqual(db._pool_timeout(), 3.0)

    def test_a_huge_wait_is_capped_at_a_minute(self):
        for raw in ("61", "600", "1e300"):
            with self.subTest(raw=raw), _env.patched_env(TURNOS_DB_POOL_TIMEOUT=raw):
                self.assertEqual(db._pool_timeout(), 60.0)
        with _env.patched_env(TURNOS_DB_POOL_TIMEOUT="60"):
            self.assertEqual(db._pool_timeout(), 60.0)

    def test_the_pool_and_the_checkout_use_the_same_wait(self):
        built = {}

        class _Pool:
            def __init__(self, url, **kwargs):
                built.update(kwargs)

        _Pool.check_connection = staticmethod(lambda conn: None)
        with _env.patched_env(TURNOS_DB_POOL_TIMEOUT="7", TURNOS_DB_POOL="9"), \
                mock.patch("psycopg_pool.ConnectionPool", _Pool), \
                mock.patch.object(db, "_pools", {}):
            db._get_pool("postgresql://fake/timeout")
        self.assertEqual(built["timeout"], 7.0)
        self.assertEqual(built["max_size"], 9)

    def test_getconn_waits_for_the_configured_seconds(self):
        seen = {}

        class _Pool:
            def getconn(self, timeout=None):
                seen["timeout"] = timeout
                raise RuntimeError("stop here")

        with _env.patched_env(TURNOS_DB_POOL_TIMEOUT="6"), \
                mock.patch.object(db, "_get_pool", lambda url: _Pool()), \
                mock.patch.object(db, "_db_url", lambda: "postgresql://fake/fake"):
            with self.assertRaises(db.DbUnavailable):
                db._connect()
        self.assertEqual(seen["timeout"], 6.0)


class TestThreadpoolSize(unittest.TestCase):
    def test_reads_the_environment_and_never_goes_below_one(self):
        for raw, expected in (("25", 25), (" 60 ", 60), ("0", 1), ("-3", 1)):
            with self.subTest(raw=raw), _env.patched_env(TURNOS_THREADPOOL_SIZE=raw):
                self.assertEqual(concurrency.threadpool_size(), expected)

    def test_garbage_falls_back_to_the_default(self):
        for raw in ("", "many", "4.5"):
            with self.subTest(raw=raw), _env.patched_env(TURNOS_THREADPOOL_SIZE=raw):
                self.assertEqual(concurrency.threadpool_size(), 40)

    def test_applying_it_sets_the_anyio_default_limiter(self):
        import anyio.to_thread

        async def apply_and_read():
            limiter = anyio.to_thread.current_default_thread_limiter()
            before = limiter.total_tokens
            try:
                with _env.patched_env(TURNOS_THREADPOOL_SIZE="12"):
                    applied = concurrency.apply_threadpool_size()
                return applied, limiter.total_tokens
            finally:
                limiter.total_tokens = before

        self.assertEqual(asyncio.run(apply_and_read()), (12, 12))

    def test_the_lifespan_start_applies_it_first_thing(self):
        # start_background_services runs inside the app lifespan (the event loop the limiter
        # belongs to); it would start real daemons, so its source is what is pinned here.
        import inspect
        from app.ops import web_runtime

        source = inspect.getsource(web_runtime.start_background_services)
        applied = source.index("apply_threadpool_size()")
        self.assertLess(applied, source.index("install_access_log_filter()"))


if __name__ == "__main__":
    unittest.main()
