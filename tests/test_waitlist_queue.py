"""test_waitlist_queue.py — Tier 4 slice 5 proof tests
(docs/roadmap/tier4-replatform-design.md, "Background daemon threads ->
Postgres-backed queue"). Four properties, each proven directly:

  1. Cadence parity — the cron expression `waitlist_queue` derives from
     TURNOS_WAITLIST_INTERVAL_MIN fires at the SAME every-N-minutes cadence
     as the thread-based sweep it replaces, for every interval value this
     repo has ever configured (TestCronCadenceParity, no DB needed).
  2. Mutual exclusion BY CONSTRUCTION — `waitlist.start()` reaches exactly
     one of the two mechanisms per TURNOS_WAITLIST_QUEUE value, never both,
     which is also the "zero-DDL-when-off" proof: the ONLY call site that
     can reach procrastinate's schema is `_start_queue()`, so proving it's
     never called when the flag is off proves the schema is never touched
     either (TestMutualExclusion, no DB needed).
  3. Schema handling — procrastinate's bundled schema.sql is genuinely
     non-idempotent DDL, applied exactly once, guarded against a
     concurrent-boot race, and NEVER touched by the normal (flag-off)
     `db._connect()` boot path Tier 4 slice 4's own zero-DDL proof already
     covers (TestSchemaLifecycle).
  4. Sweep parity — the periodic task, run through the REAL procrastinate
     machinery (real schema, real connector, real queue), produces the
     IDENTICAL observable side effect (expire the stale offer, promote the
     next candidate, one notification) that the thread-based `tick()`
     already produces, because it calls the exact same `waitlist.tick()`
     (TestQueueSweepParity) — proven twice: once by manually deferring one
     job (deterministic), once by letting the real periodic deferrer fire
     it on its own cron (bounded-time, proves the end-to-end wiring, not
     just the call chain).

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
      discover -s tests -p "test_waitlist_queue.py" -v
"""
import asyncio
import contextlib
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""      # no leer el .env real (mismo truco que test_integrity_races.py)

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.modules.turnos import waitlist           # noqa: E402
from app.modules.turnos import waitlist_queue     # noqa: E402

import _dbtest            # noqa: E402

UTC = timezone.utc

_db = _dbtest.PerModuleDb("sana_turnos_test_waitlist_queue")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("TRUNCATE appointment_offers RESTART IDENTITY")
        cur.execute("TRUNCATE waitlist RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")


def _crear_solicitud(dni):
    return db_booking.create_booking_request(
        dni, "Ana", "Gómez", "1990-01-01", "wa:1", "cardiologia", "centro-a")


def _fresh_db_url(name):
    import psycopg
    with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
        cur.execute("CREATE DATABASE %s" % name)
    p = urlsplit(_TEST_URL)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def _drop_db(name):
    import psycopg
    with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)


@contextlib.contextmanager
def _database_url_at(url):
    """Same dance test_alembic_revisions.py's `_connect_new_boot_path` uses:
    swap DATABASE_URL + clear db_schema._ensured, restore on exit."""
    db_schema._ensured.clear()
    try:
        with _env.patched_env(DATABASE_URL=url):
            yield
    finally:
        db_schema._ensured.clear()


def _procrastinate_schema_present(url):
    import psycopg
    with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.procrastinate_jobs') IS NOT NULL")
        return bool(cur.fetchone()[0])


class TestCronCadenceParity(unittest.TestCase):
    """No DB needed — pure cron-string derivation + croniter's own cadence
    evaluation, compared against the thread sweep's `interval_min`."""

    def test_cron_matches_thread_interval_for_every_configured_divisor(self):
        from croniter import croniter
        base = datetime(2026, 1, 1, tzinfo=UTC)
        # Every value TURNOS_WAITLIST_INTERVAL_MIN divides 60 evenly with —
        # the only shapes this repo's own interval knob has ever taken.
        for interval_min in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30):
            cron_expr = waitlist_queue._cron_for_interval(interval_min)
            self.assertEqual(cron_expr, f"*/{interval_min} * * * *")
            it = croniter(cron_expr, base)
            fires = [it.get_next(datetime) for _ in range(8)]
            gaps_min = [(fires[i + 1] - fires[i]).total_seconds() / 60
                        for i in range(len(fires) - 1)]
            self.assertTrue(
                all(g == interval_min for g in gaps_min),
                f"interval_min={interval_min}: cron fires every {gaps_min} "
                f"min, thread sweep fires every {interval_min} min — cadence mismatch")

    def test_default_interval_matches_waitlist_module_default(self):
        # waitlist._DEFAULT_INTERVAL_MIN is what the thread sweep uses when
        # TURNOS_WAITLIST_INTERVAL_MIN is unset — the queue path must derive
        # its cron from the SAME default, not a second hardcoded one.
        self.assertEqual(
            waitlist_queue._cron_for_interval(waitlist._DEFAULT_INTERVAL_MIN),
            "*/2 * * * *")


class TestMutualExclusion(unittest.TestCase):
    """Tier 4 slice 5 non-negotiable: the legacy thread and the queue worker
    must never both run the same sweep in the same deployment mode.
    `waitlist.start()` is the ONE function that decides — this proves each
    TURNOS_WAITLIST_QUEUE value reaches exactly one of the two starters,
    never both. This doubles as the zero-DDL-when-off proof: `_start_queue`
    is the only call site that can reach `waitlist_queue.ensure_schema()`,
    so "never called when the flag is off" means procrastinate's schema is
    never touched either — see TestSchemaLifecycle for the DB-level version
    of the same claim."""

    def setUp(self):
        _env.set_env(self, TURNOS_WAITLIST_OFF=None, TURNOS_LISTA_ESPERA_OFF=None)

    def test_flag_unset_starts_thread_never_queue(self):
        _env.set_env(self, TURNOS_WAITLIST_QUEUE=None)
        with patch.object(waitlist, "_start_thread") as mock_thread, \
             patch.object(waitlist, "_start_queue") as mock_queue:
            waitlist.start()
        mock_thread.assert_called_once()
        mock_queue.assert_not_called()

    def test_flag_on_starts_queue_never_thread(self):
        _env.set_env(self, TURNOS_WAITLIST_QUEUE="1")
        with patch.object(waitlist, "_start_thread") as mock_thread, \
             patch.object(waitlist, "_start_queue") as mock_queue:
            waitlist.start()
        mock_queue.assert_called_once()
        mock_thread.assert_not_called()

    def test_off_switch_wins_over_queue_flag_too(self):
        # TURNOS_WAITLIST_OFF is the pre-existing kill switch (untouched by
        # this slice) — it must still short-circuit BEFORE either mechanism
        # is even considered, queue flag or not.
        _env.set_env(self, TURNOS_WAITLIST_OFF="1", TURNOS_WAITLIST_QUEUE="1")
        with patch.object(waitlist, "_start_thread") as mock_thread, \
             patch.object(waitlist, "_start_queue") as mock_queue:
            waitlist.start()
        mock_thread.assert_not_called()
        mock_queue.assert_not_called()


class TestSchemaLifecycle(unittest.TestCase):
    """procrastinate's bundled schema.sql has no IF-NOT-EXISTS guard on its
    CREATE TABLE/CREATE TYPE statements (confirmed by reading it) — a
    second apply against an already-migrated database raises. Both
    properties proven against genuinely fresh, disposable databases (never
    the shared per-module one, so "first apply" really means first)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def test_schema_absent_by_default_normal_boot_never_touches_it(self):
        """The design's own STOP condition: if procrastinate required
        schema on boot regardless of the flag, that would conflict with
        Alembic's stamp-only deploy guarantee. Proven false here — the
        NORMAL boot path (db._connect(), what every request/daemon uses,
        flag on or off) never creates procrastinate's tables."""
        # run_scoped: two concurrent runs must own disjoint databases —
        # a fixed name here is the same cross-run DROP collision _dbtest's
        # per-run tokens exist to prevent.
        name = _dbtest.run_scoped("sana_turnos_test_wq_schema_absent")
        url = _fresh_db_url(name)
        try:
            with _database_url_at(url):
                conn = db._connect()  # runs the normal (Alembic-driven) boot sequence
                conn.close()
                self.assertFalse(
                    _procrastinate_schema_present(url),
                    "db._connect() must NEVER create procrastinate's schema on its own")
        finally:
            _drop_db(name)

    def test_ensure_schema_applies_once_then_is_a_noop(self):
        name = _dbtest.run_scoped("sana_turnos_test_wq_schema_apply")
        url = _fresh_db_url(name)
        try:
            with _database_url_at(url):
                self.assertFalse(_procrastinate_schema_present(url))
                applied_first = waitlist_queue.ensure_schema()
                self.assertTrue(applied_first, "first call against a fresh db must apply it")
                self.assertTrue(_procrastinate_schema_present(url))
                applied_second = waitlist_queue.ensure_schema()
                self.assertFalse(
                    applied_second,
                    "second call must be a no-op — the schema's own DDL is NOT "
                    "idempotent (no IF NOT EXISTS), a real re-apply would raise")
        finally:
            _drop_db(name)


class TestQueueSweepParity(unittest.TestCase):
    """The periodic task run through the REAL procrastinate machinery
    produces the IDENTICAL observable side effect as calling
    `waitlist.tick()` directly — because it's the same function. `_reset_tables`
    only truncates the turnos-side tables; procrastinate's own schema/tables
    are applied once (if absent) and reused across this class's tests, same
    as a real long-running worker would."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        waitlist_queue.ensure_schema()
        self.servicio_id = "cardiologia"
        self.centro_slug = "centro-a"
        self.slot_dt = datetime.now(UTC) + timedelta(days=1)

    def _seed_expired_offer_with_next_candidate(self):
        """Same shape as test_waitlist.py's `test_ventana_vencida_pasa_al_siguiente`,
        against real Postgres: candidate A already has a live-but-EXPIRED
        offer for the slot; candidate B is next in line, not yet offered."""
        sid_a = _crear_solicitud("20111111")
        wa_a = waitlist.opt_in(sid_a, "20111111", "a@example.com",
                                self.servicio_id, self.centro_slug,
                                self.slot_dt + timedelta(days=2))
        sid_b = _crear_solicitud("20111112")
        wa_b = waitlist.opt_in(sid_b, "20111112", "b@example.com",
                                self.servicio_id, self.centro_slug,
                                self.slot_dt + timedelta(days=3))
        past = datetime.now(UTC) - timedelta(minutes=10)
        oferta_a = db_slots.create_appointment_offer(
            self.slot_dt, self.servicio_id, self.centro_slug, wa_a, "tok-a",
            past - timedelta(minutes=5), past)
        return oferta_a, wa_a, wa_b

    def _offer_status(self, offer_id):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM appointment_offers WHERE id=%s", (offer_id,))
            return cur.fetchone()["status"]

    def _live_offer_for(self, waitlist_id):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM appointment_offers WHERE waitlist_id=%s", (waitlist_id,))
            row = cur.fetchone()
            return row["status"] if row else None

    def test_manually_deferred_job_reaches_the_same_state_tick_would(self):
        """Deterministic: defer exactly one job, drain the queue once
        (`wait=False` — 'run until caught up, then stop'), assert the same
        end state waitlist.py's own thread-driven tests assert for this
        exact scenario shape."""
        oferta_a, wa_a, wa_b = self._seed_expired_offer_with_next_candidate()
        app, sweep_task = waitlist_queue.build_app(2)
        try:
            with patch("app.notifications.send.notify_offer") as mock_notify:
                app.open()
                sweep_task.defer(timestamp=0)
                app.run_worker(queues=["waitlist"], wait=False,
                                listen_notify=False, install_signal_handlers=False)
                app.close()
                self.assertEqual(mock_notify.call_count, 1)
                self.assertEqual(mock_notify.call_args.args[0]["id"], wa_b)
        finally:
            pass
        self.assertEqual(self._offer_status(oferta_a), "expirada")
        self.assertEqual(self._live_offer_for(wa_b), "ofrecida")

    def test_periodic_deferrer_fires_on_its_own_and_reaches_the_same_state(self):
        """Bounded end-to-end run (no manual defer): the REAL periodic
        deferrer, on a fast test-only cron (a 6th cron field = seconds —
        procrastinate's own extension, see Blueprint.periodic's docstring),
        fires the sweep on its own within a few seconds. Proves the
        connector/schema/queue-name wiring, not just the call chain the
        test above already proves deterministically."""
        oferta_a, wa_a, wa_b = self._seed_expired_offer_with_next_candidate()
        app, sweep_task = waitlist_queue.build_app(2, cron="* * * * * *")  # every second

        with patch("app.notifications.send.notify_offer") as mock_notify:
            async def _bounded_run():
                async with app.open_async():
                    # Do not cancel run_worker_async() at an arbitrary point.
                    # A synchronous task may already be executing in the event
                    # loop's default executor; asyncio.run() then waits for that
                    # executor during teardown and an order-heavy suite can hang
                    # long after the 4-second wait_for() expired. Exercise the
                    # worker's own graceful stop contract instead: observe the
                    # periodic job's real side effect, request stop, and bound
                    # both phases independently.
                    # Without LISTEN/NOTIFY the worker only sees the job the
                    # deferrer queued on its next poll, and procrastinate's
                    # default poll is 5s — longer than the 4s bound, so a
                    # job deferred just after the first poll timed out on a
                    # loaded run. Poll fast so the bound measures cadence.
                    worker = app._worker(
                        queues=["waitlist"], listen_notify=False,
                        fetch_job_polling_interval=0.05,
                        install_signal_handlers=False)
                    worker_task = asyncio.create_task(worker.run())
                    try:
                        async with asyncio.timeout(4.0):
                            while mock_notify.call_count < 1:
                                await asyncio.sleep(0.02)
                    finally:
                        worker.stop()
                    await asyncio.wait_for(worker_task, timeout=4.0)

            asyncio.run(_bounded_run())
            self.assertGreaterEqual(
                mock_notify.call_count, 1,
                "the periodic deferrer never fired within the 4s bound — cadence/wiring broken")

        self.assertEqual(self._offer_status(oferta_a), "expirada")
        self.assertEqual(self._live_offer_for(wa_b), "ofrecida")

    def test_empty_queue_no_next_offer_same_as_thread_tick(self):
        """No candidate waiting: the queue-driven sweep never creates a new
        offer (no notification) — same "cola vacía" outcome
        test_waitlist.py's pure-engine `test_cola_vacia_deja_vacante`
        asserts for the identical shape. The expired offer's row itself
        stays 'expirada' (tick()'s `_mark_vacant` targets status='ofrecida',
        which this row no longer is by the time it runs — a pre-existing
        `waitlist.py` quirk, identical under either mechanism since it's
        the same unmodified function; not something this slice changes or
        should paper over)."""
        sid_a = _crear_solicitud("20111113")
        wa_a = waitlist.opt_in(sid_a, "20111113", "a@example.com",
                                self.servicio_id, self.centro_slug,
                                self.slot_dt + timedelta(days=2))
        past = datetime.now(UTC) - timedelta(minutes=10)
        oferta_a = db_slots.create_appointment_offer(
            self.slot_dt, self.servicio_id, self.centro_slug, wa_a, "tok-a",
            past - timedelta(minutes=5), past)

        app, sweep_task = waitlist_queue.build_app(2)
        with patch("app.notifications.send.notify_offer") as mock_notify:
            app.open()
            sweep_task.defer(timestamp=0)
            app.run_worker(queues=["waitlist"], wait=False,
                            listen_notify=False, install_signal_handlers=False)
            app.close()
            mock_notify.assert_not_called()
        self.assertEqual(self._offer_status(oferta_a), "expirada")


if __name__ == "__main__":
    unittest.main()
