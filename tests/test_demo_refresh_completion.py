"""Durable completion proof for the demo refresh admission gate.

Production and demo both run the ASGI topology, but a live process is not
proof that the fictional queue has been regenerated. These tests use a
disposable PostgreSQL database for the one-row ledger and mock only the
non-sensitive seed phases. No real pack asset, demo row, provider, or
credential is touched.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.admin_panel import system_status  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import db, tz  # noqa: E402
from app.core import db_backup  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.ops import demo_refresh, demo_refresh_store  # noqa: E402

import _dbtest  # noqa: E402


_db = _dbtest.PerModuleDb("sana_turnos_test_demo_refresh")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class DemoEnvironment(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres unavailable")
        _env.set_env(self, TURNOS_DEMO_MODE="1", TURNOS_DEMO_REFRESH_OFF=None)
        demo_refresh._started = False
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("DELETE FROM demo_refresh_completion")

    def tearDown(self):
        demo_refresh._started = False

    @staticmethod
    def _today():
        return datetime.now(tz.tzinfo()).date()

    def _run(self, *, assets=None, referrals=None, seeded=None, day=None):
        with mock.patch.object(demo_refresh, "reseed_assets", side_effect=assets), \
             mock.patch.object(demo_refresh, "_reparar_referrals", side_effect=referrals), \
             mock.patch.object(demo_refresh, "_dia_ya_sembrado", side_effect=seeded):
            return demo_refresh.refresh_once(day or self._today())


class TestDemoRefreshLedger(DemoEnvironment):
    def test_success_is_persisted_only_after_all_phases_and_survives_restart(self):
        result = self._run(assets=lambda: True, referrals=lambda: False,
                           seeded=lambda _day: True)
        self.assertEqual(result, "success")
        first = db_backup.demo_refresh_completion_status()
        self.assertEqual(first["outcome"], "success")
        self.assertEqual(first["success_for"], self._today())
        self.assertIsNotNone(first["last_success_at"])

        # A fresh pool/boot has no in-memory success cache to consult.
        db.close_pools()
        db_schema._ensured.clear()
        restarted = db_backup.demo_refresh_completion_status()
        self.assertEqual(restarted["outcome"], "success")
        self.assertEqual(restarted["success_for"], self._today())

    def test_partial_attempt_overwrites_green_outcome(self):
        self._run(assets=lambda: True, referrals=lambda: False,
                  seeded=lambda _day: True)
        result = self._run(assets=RuntimeError("asset copy"), referrals=lambda: False,
                           seeded=lambda _day: True)
        self.assertEqual(result, "partial")
        row = db_backup.demo_refresh_completion_status()
        self.assertEqual(row["outcome"], "partial")
        self.assertEqual(row["success_for"], self._today(),
                         "the audit fact may remain, but it must not turn status green")

    def test_all_failed_phases_are_persisted_as_failed(self):
        result = self._run(assets=RuntimeError("assets"),
                           referrals=RuntimeError("referrals"),
                           seeded=RuntimeError("database read"))
        self.assertEqual(result, "failed")
        row = db_backup.demo_refresh_completion_status()
        self.assertEqual(row["outcome"], "failed")
        self.assertIsNone(row["last_success_at"])

    def test_same_day_loss_is_detectable_for_loop_recovery(self):
        with mock.patch.object(demo_refresh, "_dia_ya_sembrado", return_value=False):
            self.assertTrue(demo_refresh._needs_refresh(self._today(), self._today(), True))

    def test_same_day_marker_loss_is_reseeded_and_read_back_before_success(self):
        with mock.patch.object(demo_refresh, "reseed_assets", return_value=None), \
             mock.patch.object(demo_refresh, "_reparar_referrals", return_value=False), \
             mock.patch.object(demo_refresh, "_dia_ya_sembrado", side_effect=(False, True)) as covered, \
             mock.patch.object(demo_refresh, "reseed") as reseed:
            result = demo_refresh.refresh_once(self._today())
        self.assertEqual(result, "success")
        reseed.assert_called_once_with()
        self.assertEqual(covered.call_count, 2)

    def test_a_reseed_that_leaves_the_days_queue_empty_is_no_success(self):
        # 09.11: the Monday refresh wrote «success» over an empty queue. An
        # empty day after the reseed is a named failure in the log.
        with mock.patch.object(demo_refresh, "reseed_assets", return_value=None), \
             mock.patch.object(demo_refresh, "_reparar_referrals", return_value=False), \
             mock.patch.object(demo_refresh, "_dia_ya_sembrado", side_effect=(False, False)), \
             mock.patch.object(demo_refresh, "reseed"), \
             mock.patch.object(demo_refresh, "_log") as log:
            result = demo_refresh.refresh_once(self._today())
        self.assertEqual(result, "partial")
        self.assertEqual(db_backup.demo_refresh_completion_status()["outcome"], "partial")
        lines = " ".join(c.args[0] for c in log.call_args_list)
        self.assertIn("queue for " + str(self._today()) + " is empty", lines)

    def test_postgres_lock_makes_a_rolling_deploy_wait_without_writing(self):
        """A second web process must neither seed nor overwrite the ledger.

        Keep the lock in a live, distinct PostgreSQL session.  This tests the
        actual contention path rather than merely asserting that the SQL name
        exists in the source.
        """
        day = self._today()
        with db._connect() as holder, holder.cursor() as cur:  # noqa: SLF001
            cur.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)",
                        (demo_refresh._LOCK_KEY,))  # noqa: SLF001
            try:
                with mock.patch.object(demo_refresh, "reseed_assets") as assets, \
                     mock.patch.object(demo_refresh, "_reparar_referrals") as referrals, \
                     mock.patch.object(demo_refresh, "_dia_ya_sembrado") as covered:
                    self.assertEqual(demo_refresh.refresh_once(day), "busy")
                assets.assert_not_called()
                referrals.assert_not_called()
                covered.assert_not_called()
                self.assertIsNone(db_backup.demo_refresh_completion_status())
            finally:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)",
                            (demo_refresh._LOCK_KEY,))  # noqa: SLF001

        self.assertEqual(self._run(assets=lambda: True, referrals=lambda: False,
                                   seeded=lambda _day: True, day=day), "success")
        self.assertEqual(db_backup.demo_refresh_completion_status()["outcome"], "success")


class TestExclusiveRefreshFailurePath(DemoEnvironment):
    """A ledger write that dies mid-attempt must not strand the lock.

    Independent review (2026-08-16) of the store extraction: the unlock ran
    while the body's transaction was still aborted, so Postgres rejected the
    unlock itself and the healthy pooled connection went back to the pool
    still holding the session-scoped advisory lock.  Every later refresh
    would then answer ``busy`` from any other session — or, on the poisoned
    session itself, silently "re-acquire" instantly, since advisory locks
    are reentrant within a session.  The failure below is a GENUINE
    Postgres error (a CHECK constraint no ledger write can satisfy), never
    a monkeypatched store.
    """

    def _wedge_ledger_writes(self):
        # A GENUINE server-side failure for exactly the ledger write: a
        # CHECK constraint no row can satisfy.  Any SQL error aborts the
        # transaction — the exact state the review found the old unlock
        # running in.  (Alembic owns schema bring-up here, so dropping and
        # "re-ensuring" the table would NOT recreate it; a constraint is
        # also the exact-inverse restore.)
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("ALTER TABLE demo_refresh_completion"
                        " ADD CONSTRAINT demo_refresh_wedge_write"
                        " CHECK (1 = 0)")

    def _unwedge_ledger_writes(self):
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.execute("ALTER TABLE demo_refresh_completion"
                        " DROP CONSTRAINT IF EXISTS demo_refresh_wedge_write")

    def test_failed_ledger_write_does_not_wedge_the_next_session(self):
        import psycopg
        from psycopg.rows import dict_row

        day = self._today()
        self._wedge_ledger_writes()
        self.addCleanup(self._unwedge_ledger_writes)

        # The attempt reports failure through the orchestrator's own
        # caught-error path, exactly as with any other database outage.
        with mock.patch.object(demo_refresh, "_log"):
            self.assertEqual(
                self._run(assets=lambda: True, referrals=lambda: False,
                          seeded=lambda _day: True, day=day),
                "failed")

        # PRIMARY PROOF — an independent session (its own connection, not
        # the pool's, so a poisoned pooled session cannot "re-acquire"
        # through reentrancy) must be able to take the lock right now.
        # With the leak this try-lock returned False and every later
        # refresh answered "busy".
        probe = psycopg.connect(_db.test_url, autocommit=True,
                                connect_timeout=5, row_factory=dict_row)
        try:
            with probe.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s)::bigint) AS acquired",
                    (demo_refresh_store.LOCK_KEY,))
                self.assertTrue(
                    cur.fetchone()["acquired"],
                    "the lock was left held after the failed ledger write")
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)",
                            (demo_refresh_store.LOCK_KEY,))
        finally:
            probe.close()

        # And the very next attempt completes once the ledger accepts
        # writes again.
        self._unwedge_ledger_writes()
        self.assertEqual(
            self._run(assets=lambda: True, referrals=lambda: False,
                      seeded=lambda _day: True, day=day),
            "success")
        self.assertEqual(db_backup.demo_refresh_completion_status()["outcome"],
                         "success")


class _FakeRawSession:
    """Deterministic stand-in for the raw psycopg session behind
    `db._connect()` — records every protocol step the store's lock frame
    takes (rollback, each advisory-lock statement, close) so the frame's
    ordering and exception precedence are pinned WITHOUT a database."""

    def __init__(self, unlock_error=None):
        self.events = []
        self.unlock_error = unlock_error
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.closed = True
        self.events.append("close")


class _FakeCursor:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if "pg_try_advisory_lock" in sql:
            self._session.events.append("try_lock")
        elif "pg_advisory_unlock" in sql:
            self._session.events.append("unlock")
            if self._session.unlock_error is not None:
                raise self._session.unlock_error
        else:
            self._session.events.append("sql:" + sql.strip().split(None, 1)[0].lower())

    def fetchone(self):
        return {"acquired": True}


class _FakePooledConn:
    """The `db._connect()` return value: yields the raw session and
    remembers what its own ``__exit__`` saw propagate out of the frame."""

    def __init__(self, raw):
        self.raw = raw
        self.exit_exc = "never exited"

    def __enter__(self):
        return self.raw

    def __exit__(self, exc_type, exc, tb):
        self.exit_exc = exc
        return False


class TestExclusiveRefreshFrameContract(unittest.TestCase):
    """The store frame's cleanup contract, pinned deterministically.

    The real-Postgres class above proves the LOCK side (a failed ledger
    write does not wedge the next session); this one pins the EXCEPTION
    side the review's round 2 flagged: when the body and the unlock BOTH
    raise, the body's ORIGINAL exception is what leaves the frame — the
    unlock failure must not mask it — while the raw session is still
    closed and the rollback still precedes the unlock attempt.  No DB,
    no seams: only fakes swapped in for `db._connect`.
    """

    def _frame(self, unlock_error=None):
        raw = _FakeRawSession(unlock_error=unlock_error)
        pooled = _FakePooledConn(raw)
        patcher = mock.patch.object(demo_refresh_store.db, "_connect",
                                    return_value=pooled)
        return raw, pooled, patcher

    def test_body_exception_survives_unlock_failure_rollback_first(self):
        unlock_error = ValueError("unlock rejected on an aborted session")
        body_error = RuntimeError("ledger write died")
        raw, pooled, patcher = self._frame(unlock_error=unlock_error)
        with patcher:
            with self.assertRaises(RuntimeError) as ctx:
                with demo_refresh_store.exclusive_refresh() as conn:
                    self.assertIs(conn, raw)
                    raise body_error
        self.assertIs(ctx.exception, body_error,
                      "the body's own exception object must propagate, "
                      "never the unlock failure that followed it")
        self.assertEqual(raw.events, ["try_lock", "rollback", "unlock", "close"],
                         "rollback must run BEFORE the unlock attempt, and "
                         "the unlock failure must still close the raw session")
        self.assertIs(pooled.exit_exc, body_error,
                      "the pooled wrapper's __exit__ sees the original "
                      "body exception, not the unlock failure")
        self.assertIsInstance(ctx.exception.__context__, ValueError,
                              "the unlock failure rides along chained onto "
                              "the body's exception, not lost")

    def test_unlock_failure_on_a_successful_body_is_the_frame_error(self):
        unlock_error = ValueError("unlock rejected")
        raw, pooled, patcher = self._frame(unlock_error=unlock_error)
        with patcher:
            with self.assertRaises(ValueError) as ctx:
                with demo_refresh_store.exclusive_refresh() as conn:
                    self.assertIs(conn, raw)
        self.assertIs(ctx.exception, unlock_error,
                      "with no body failure, the unlock failure is the "
                      "frame's own raised error")
        self.assertEqual(raw.events, ["try_lock", "unlock", "close"])
        self.assertNotIn("rollback", raw.events,
                         "a successful body must not be rolled back by "
                         "the frame's cleanup")
        self.assertIs(pooled.exit_exc, unlock_error)

    def test_clean_success_unlocks_without_rollback_or_close(self):
        raw, pooled, patcher = self._frame(unlock_error=None)
        with patcher:
            with demo_refresh_store.exclusive_refresh() as conn:
                self.assertIs(conn, raw)
        self.assertEqual(raw.events, ["try_lock", "unlock"])
        self.assertFalse(raw.closed)
        self.assertIsNone(pooled.exit_exc)


class TestDemoRefreshSafety(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_DEMO_MODE="0", TURNOS_DEMO_REFRESH_OFF=None)
        demo_refresh._started = False

    def tearDown(self):
        demo_refresh._started = False

    def test_non_demo_guard_returns_before_database_work(self):
        with mock.patch("app.core.db._connect", side_effect=AssertionError("database touched")):
            self.assertEqual(demo_refresh.refresh_once(), "inactive")

    def test_non_demo_system_status_has_no_demo_item(self):
        self.assertIsNone(system_status._demo_refresh_item())

    def test_demo_database_failure_returns_failed_without_a_green_fallback(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        with mock.patch("app.core.db._connect", side_effect=RuntimeError("db down")), \
             mock.patch.object(demo_refresh, "_log"):
            self.assertEqual(demo_refresh.refresh_once(), "failed")

    def test_start_is_singleton_in_one_web_process(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        thread = mock.Mock()
        with mock.patch.object(demo_refresh.threading, "Thread", return_value=thread) as made:
            self.assertTrue(demo_refresh.start())
            self.assertFalse(demo_refresh.start())
        made.assert_called_once()
        thread.start.assert_called_once()

    def test_non_demo_keep_alive_returns_before_database_work(self):
        with mock.patch("app.core.db._connect", side_effect=AssertionError("database touched")):
            self.assertFalse(demo_refresh._mantener_escenarios())

    def test_the_tick_keep_alive_runs_both_steps_and_degrades_on_failure(self):
        """The showcase keep-alive (plan slice 1, 2026-11-10): every tick
        re-asserts the demo catalog overlay and repairs the featured
        patient's walkthrough; a failed step logs and lets the next tick
        retry instead of killing the loop."""
        import scripts.seed_demo_all as seed_demo_all
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        with mock.patch.object(seed_demo_all, "_asegurar_catalogo_demo") as overlay, \
             mock.patch.object(seed_demo_all, "_asegurar_turno_destacado") as turno:
            self.assertTrue(demo_refresh._mantener_escenarios())
        overlay.assert_called_once_with()
        turno.assert_called_once_with()
        with mock.patch.object(seed_demo_all, "_asegurar_catalogo_demo",
                               side_effect=RuntimeError("catalog down")), \
             mock.patch.object(seed_demo_all, "_asegurar_turno_destacado") as turno, \
             mock.patch.object(demo_refresh, "_log"):
            self.assertTrue(demo_refresh._mantener_escenarios())  # degrades, doesn't raise
        turno.assert_called_once_with()

    def test_a_seed_that_cannot_be_loaded_does_not_kill_the_loop(self):
        # the import sits inside the guard too: the daemon and its daily
        # reseed must outlive a keep-alive that cannot even load
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        real_import = __import__

        def sin_seed(name, *args, **kwargs):
            if name == "scripts.seed_demo_all":
                raise ImportError("seed unavailable")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=sin_seed), \
             mock.patch.object(demo_refresh, "_log") as log:
            self.assertFalse(demo_refresh._mantener_escenarios())
        log.assert_called_once()


class TestAdminStatus(DemoEnvironment):
    def _item(self, now=None):
        return system_status._demo_refresh_item(now=now)

    def test_current_completed_generation_is_green_without_sensitive_fields(self):
        db_backup.demo_refresh_completion("success", self._today())
        row = db_backup.demo_refresh_completion_status()
        now = datetime.now(timezone.utc)
        item = self._item(now)
        self.assertEqual(item["estado"], "ok", {
            "item": item, "row": row, "now": now,
            "age": now - row["last_success_at"].astimezone(timezone.utc),
        })
        self.assertEqual(item["valor"], "Datos de hoy completos")
        self.assertNotIn("TURNOS_", str(item))
        self.assertNotIn("@", str(item))

    def test_missing_completion_is_explicitly_non_green(self):
        item = self._item(datetime.now(timezone.utc))
        self.assertEqual(item["estado"], "error")
        self.assertEqual(item["valor"], "Sin actualización completa")

    def test_partial_failure_and_stale_generation_are_non_green(self):
        db_backup.demo_refresh_completion("partial", self._today())
        self.assertEqual(self._item(datetime.now(timezone.utc))["estado"], "error")

        db_backup.demo_refresh_completion("success", self._today() - timedelta(days=1))
        fixed_now = datetime.now(timezone.utc)
        self.assertEqual(self._item(fixed_now)["valor"], "Desactualizada")
        self.assertEqual(self._item(fixed_now)["estado"], "error")

    def test_refresh_failure_does_not_masquerade_as_database_outage(self):
        db_backup.demo_refresh_completion("partial", self._today())
        with mock.patch.object(system_status, "probe_db", return_value=(True, True, True, None)):
            cluster = system_status._cluster_app()
        self.assertEqual(cluster["resumen"],
                         "La actualización de la demo no terminó: revisá el ítem marcado.")

    def test_materially_future_completion_is_not_green(self):
        now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
        row = {
            "outcome": "success",
            "success_for": now.astimezone(tz.tzinfo()).date(),
            "last_success_at": now + system_status._DEMO_REFRESH_MAX_FUTURE_SKEW
                               + timedelta(seconds=1),
        }
        with mock.patch("app.core.db_backup.demo_refresh_completion_status", return_value=row):
            item = self._item(now)
        self.assertEqual(item["estado"], "error")
        self.assertEqual(item["valor"], "Desactualizada")

    def test_database_failure_is_not_green(self):
        with mock.patch("app.core.db_backup.demo_refresh_completion_status", side_effect=RuntimeError("down")):
            item = self._item()
        self.assertEqual(item["estado"], "error")
        self.assertEqual(item["valor"], "No disponible")

    def test_app_cluster_includes_current_refresh_evidence(self):
        """The existing System payload includes the demo-only safe item."""
        db_backup.demo_refresh_completion("success", self._today())
        row = db_backup.demo_refresh_completion_status()
        with mock.patch.object(system_status, "probe_db", return_value=(True, True, True, None)):
            cluster = system_status._cluster_app()
        item = next(i for i in cluster["items"] if i["label"] == "Actualización de la demo")
        self.assertEqual(item["estado"], "ok", {"item": item, "row": row})


class TestDemoRefreshTransportParity(unittest.TestCase):
    """The real System router must carry the safe completion item."""

    def setUp(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1", TURNOS_DEMO_REFRESH_OFF=None)

    def test_authorized_asgi_system_route_carries_completion_evidence(self):
        """No production auth or database is used: only the shared adapter.

        This pins the native route's own response directly.
        """
        from fastapi.testclient import TestClient
        from app import main as app_module

        now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
        row = {
            "outcome": "success",
            "success_for": now.astimezone(tz.tzinfo()).date(),
            "last_success_at": now,
        }

        def safe_payload(_request):
            return {"clusters": [{"items": [system_status._demo_refresh_item(now=now)]}]}

        with mock.patch.object(admin_auth, "evaluate_admin_gate",
                                return_value=admin_auth.GateResult(identity={"role": "admin"})), \
             mock.patch("app.core.db_backup.demo_refresh_completion_status", return_value=row), \
             mock.patch.object(system_status, "status", side_effect=safe_payload), \
             mock.patch("app.ops.web_runtime.start_background_services"), \
             mock.patch("app.ops.web_runtime.stop_app_modules"), \
             TestClient(app_module.app) as client:
            asgi = client.get("/api/admin/system")

        self.assertEqual(asgi.status_code, 200)
        demo_item = asgi.json()["clusters"][0]["items"][0]
        self.assertEqual(demo_item["estado"], "ok")
        self.assertEqual(demo_item["valor"], "Datos de hoy completos")


if __name__ == "__main__":
    unittest.main(verbosity=2)
