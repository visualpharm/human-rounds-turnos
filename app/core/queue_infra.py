"""queue_infra.py — the generic procrastinate machinery, shared by every
queue-backed job (Tier 4, docs/roadmap/tier4-replatform-design.md,
"Background daemon threads -> Postgres-backed queue" + the worker-topology
slice that followed it).

Extracted VERBATIM from `modules/turnos/waitlist_queue.py` (the slice 5
pilot) the day the SECOND queue consumer appeared: the standalone worker
entrypoint (`worker.py`) needs the exact same schema-apply guard and
cron-derivation logic the pilot already proved, and copying either one
would fork the advisory-lock idiom the pilot deliberately reused from
`db_booking.create_booking_request`. `waitlist_queue.py` now imports these and
keeps its own names as thin aliases, so its tests (and any deployment
running the in-process pilot today) see zero behavior change.

Nothing here decides WHICH jobs run or WHERE the worker lives — that's the
caller's business (`waitlist_queue.start()` for the in-process pilot,
`worker.py` for the supervised-process topology). This module only knows how
to (a) turn "every N minutes" into cron, (b) apply procrastinate's
non-idempotent schema exactly once, and (c) build the async-connector App
a worker loop requires.
"""
import os
import sys

from app.core import launch_signals
from app.core import settings

SCHEMA_LOCK_KEY = "turnos:procrastinate_schema"

# The fixed extra margin the OUTER supervisor (asgi_main.py's `_terminar`,
# `TURNOS_QUEUE_WORKER=1` topology) grants after every bounded shutdown
# phase before SIGKILL. One constant, not independently-chosen margins:
# worker.py's own
# post-run_worker_async cleanup (draining hsi_sync's boot-time kick, see
# worker._drain_hsi_sync_startup_tasks) has to fit INSIDE this same
# margin, not allocate a second one of its own — the process only gets
# SIGKILLed once, at `drain + SUPERVISOR_SIGKILL_MARGIN_S` total, however
# many internal steps happen between SIGTERM and exit.
SUPERVISOR_SIGKILL_MARGIN_S = 5.0

# Headroom worker.py reserves INSIDE SUPERVISOR_SIGKILL_MARGIN_S for its
# own post-run_worker_async cleanup to actually finish and be observed —
# review catch, 2026-08-09: if _drain_hsi_sync_startup_tasks() waited the
# FULL margin above, its own deadline would land at EXACTLY the same
# instant as the supervisor's SIGKILL, not safely before it. Signal
# delivery, event-loop teardown and log flushing all take genuine
# (if small) wall-clock time on either side of that boundary — racing it
# means the SIGKILL can win, and the very "cancelled, logged" outcome
# this machinery exists to guarantee would itself go unobserved, exactly
# the silent-drop this whole feature was built to prevent. Kept as a
# distinct constant (not folded into SUPERVISOR_SIGKILL_MARGIN_S) because
# it is worker.py's OWN safety choice, not part of the supervisor's
# actual contract with asgi_main.
HSI_SYNC_DRAIN_HEADROOM_S = 2.0

# psycopg_pool's own hardcoded bound on `AsyncConnectionPool.open(wait=True)`
# — procrastinate's `PsycopgConnector.open_async()` calls it with NO timeout
# argument, so this is psycopg_pool's method-signature default (`timeout:
# float = 30.0`), not a value either procrastinate or this codebase can
# configure. worker.py's own signal handling deliberately never interrupts
# this await (a raw `task.cancel()` landing inside pool creation can abandon
# a partially-created resource — see worker._boot_and_run's docstring), so a
# SIGTERM arriving while Postgres is unreachable at startup is only checked
# once this call returns (success OR PoolTimeout) — up to this many
# seconds. Round-9 review finding: with the default TURNOS_WORKER_GRACEFUL_
# TIMEOUT (20s), the supervisor's own patience (drain + SUPERVISOR_SIGKILL_
# MARGIN_S = 25s) was SHORTER than this bound, so a worker stuck opening its
# pool during a Postgres blip got SIGKILLed 5s before it would ever have
# honored the graceful request on its own — asgi_main.py's `_terminar` now
# waits for the longest interruptible phase, then every sequential close,
# then SUPERVISOR_SIGKILL_MARGIN_S. This covers startup and post-startup
# shutdown rather than assuming their closes overlap the preceding phase.
POOL_OPEN_TIMEOUT_S = 30.0

# Worst-case bound on the pool CLOSE that runs during graceful shutdown
# (worker._boot_and_run's `finally`: `ctx.__aexit__` ==
# PsycopgConnector.close_async) — a conservative bound the supervisor's
# SIGTERM→SIGKILL window must also outlast, because a worker that only just
# finished opening its pool (or was stuck opening it, see
# POOL_OPEN_TIMEOUT_S) when SIGTERM lands still has to close it before it
# can honor the request and exit. Open and close are sequential pool-
# lifecycle phases, so the budget SUMS the two bounds (see
# supervisor_sigkill_budget) — covering the union, not just one.
POOL_CLOSE_TIMEOUT_S = 5.0

# `_register_jobs()` resolves DB-backed configuration before the health line,
# which can create core.db's synchronous ConnectionPool. Its atexit hook closes
# that pool after worker.main() returns, using psycopg_pool's separate 5-second
# default. This phase is sequential with procrastinate's async-pool close, so
# the outer supervisor must include both bounds.
CORE_DB_POOL_CLOSE_TIMEOUT_S = 5.0


def supervisor_sigkill_budget(graceful_timeout=None):
    """The total seconds the OUTER supervisor (asgi_main.py's `_terminar`,
    `TURNOS_QUEUE_WORKER=1` topology) waits between SIGTERM and SIGKILL of
    a worker subprocess — ``max(drain, POOL_OPEN_TIMEOUT_S) +
    POOL_CLOSE_TIMEOUT_S + CORE_DB_POOL_CLOSE_TIMEOUT_S +
    SUPERVISOR_SIGKILL_MARGIN_S``.

    ONE function, the single source of this contract: the production
    supervisor and the real-subprocess test harness
    (``_workerproc.WorkerProc.stop``) both read it here, so the harness can
    never re-derive an undersized override that SIGKILLs a worker before it
    could honor the graceful request on its own (round-9 review finding:
    with the default drain, a hard-coded ``drain + margin`` (25s) was
    SHORTER than POOL_OPEN_TIMEOUT_S (30s), so a worker stuck opening its
    pool during a Postgres blip was killed 5s before it would ever have
    reacted — see POOL_OPEN_TIMEOUT_S).

    ``graceful_timeout`` defaults to TURNOS_WORKER_GRACEFUL_TIMEOUT
    (default 20s; a value that is not a number is the default), the same
    field worker.py drains by (``core.settings.ProcessSettings``).
    """
    if graceful_timeout is None:
        graceful_timeout = settings.ProcessSettings.current().worker_graceful_timeout
    return (max(graceful_timeout, POOL_OPEN_TIMEOUT_S)
            + POOL_CLOSE_TIMEOUT_S
            + CORE_DB_POOL_CLOSE_TIMEOUT_S
            + SUPERVISOR_SIGKILL_MARGIN_S)


def _log(msg):
    print(f"[queue_infra] {msg}", file=sys.stderr, flush=True)


def worker_mode():
    """THE one parse of TURNOS_QUEUE_WORKER — returns "1" (the worker runs as
    the supervised child of the web process) or "" (off: the sweeps are
    daemon threads of the web process). Every consumer (waitlist.start,
    reminders.start, asgi_main._start_queue_worker_supervisor) calls this
    instead of reading the env itself: the Fable review (2026-08-08)
    reproduced a real mutual-exclusion hole when asgi_main stripped
    whitespace and the web modules compared the raw value — a Coolify paste
    of "1 " made the supervisor spawn the worker while both web modules
    started their threads too. One parser, one truth. Any other value
    (including the retired "external") is off."""
    # Topology is process placement, not an installation setting: it is
    # decided before the worker may open Postgres.  DB-backed `config.get()`
    # would make that gate circular, so this one parser is intentionally
    # process-environment-only; every web module, supervisor, and worker
    # imports this exact function.
    valor = (settings.ProcessSettings.current().queue_worker or "").strip()
    return valor if valor == "1" else ""


def cron_for_interval(interval_min):
    """Derives the cron for a thread sweep's "every N minutes" cadence —
    EXACT or REJECTED, never approximated (Fable review 2026-08-08 finding
    6, tightened twice the same day: first by the product owner for >= 60, then by the
    Codex round for sub-60 — `*/45` restarts at the hour boundary and
    fires a 15-min gap at :00, exactly the faster-than-configured defect
    class at minute scale). Accepted, each proven exact with croniter by
    the tests:

    - N that divides 60 (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30):
      `*/N * * * *`. Covers every value this repo has ever configured
      (waitlist default 2, reminders default 30).
    - N = 60·H with H a divisor of 24 (1, 2, 3, 4, 6, 8, 12):
      `0 */H * * *` — `0 */H` restarts at the DAY boundary, so any other
      H (5, 7, …, 23) would sweep faster than configured.
    - N = 1440: `0 0 * * *` (daily).

    Anything else (7, 45, 90, 300, 1380, 2880, …) RAISES ValueError. The
    caller's failure mode is loud by construction: worker.py exits with
    its permanent-config code (2, no supervisor retry), the in-process
    pilot logs "cola no se pudo iniciar". The thread path keeps supporting
    any interval — only the queue topology rejects.

    A non-integral or sub-1-minute `interval_min` is ALSO rejected, not
    silently coerced (batch-4 review fix, 2026-08-09): `max(1, int(...))`
    used to truncate 5.9 -> 5 and clamp 0.5 -> 1, both the exact
    faster-than-configured defect class this function's docstring already
    bans for non-divisor integers — a fractional env value (a typo, or a
    thread-only test seam like TURNOS_HSI_SYNC_INTERVAL_MIN=0.05 leaking
    into the queue topology) would have swept the HSI journal, or any
    future job, more often than configured instead of failing loudly. The
    thread path is UNCHANGED — it keeps its own float parsing and can run
    at any fractional-minute cadence; only this function, the one the
    queue topology's cron derivation goes through, now demands a whole
    minute >= 1."""
    if isinstance(interval_min, float) and not interval_min.is_integer():
        raise ValueError(
            f"intervalo de {interval_min} min no es un número entero de minutos "
            "— la topología de cola sólo acepta minutos enteros (el hilo sigue "
            "soportando fracciones; la cola las rechaza en vez de truncarlas y "
            "barrer más seguido de lo configurado)")
    interval_min = int(interval_min)
    if interval_min < 1:
        raise ValueError(
            f"intervalo de {interval_min} min menor a 1 — la topología de cola "
            "lo rechaza en vez de subirlo silenciosamente a 1 min")
    if interval_min < 60 and 60 % interval_min == 0:
        return f"*/{interval_min} * * * *"
    horas = interval_min // 60
    if interval_min % 60 == 0 and horas < 24 and 24 % horas == 0:
        return f"0 */{horas} * * *" if horas > 1 else "0 * * * *"
    if interval_min == 1440:
        return "0 0 * * *"
    raise ValueError(
        f"intervalo de {interval_min} min no se puede expresar exacto en cron "
        "(soportados: divisores de 60, 60·H con H divisor de 24, y 1440) — la "
        "topología de cola lo rechaza en vez de barrer más seguido de lo configurado")


def test_seams_enabled():
    """Master gate for every TURNOS_TEST_* seam the queue topology honors
    (Codex gate, round 4; moved here from worker.py in the registry-hardening
    round so the queue modules stop keeping private copies): a seam variable
    accidentally inherited by a real deployment (one env line pasted into
    Coolify, a copied env file) must be INERT on its own. Only when
    TURNOS_TEST_SEAMS=1 is ALSO set do the individual seams
    (TURNOS_TEST_UNSAFE_STALLED, TURNOS_TEST_SWEEP_HOLD_S, housekeeping's
    TURNOS_TEST_MAIL_ACCEPT_FILE/_HOLD_S) take effect — a single-var
    accident cannot suppress real alerts, sleep jobs, or weaken the
    recovery floor."""
    import os
    return os.environ.get("TURNOS_TEST_SEAMS") == "1"


def _wait_for_test_release_gate(timeout_s=None, poll_s=0.05):
    """Wait for the double-gated contention-test rendezvous, if armed.

    The caller holds the matching advisory lock until it has observed the
    intended queue state.  A timeout is a broken test choreography, not a
    permission to run the business sweep unobserved, so it fails closed.
    ``timeout_s``/``poll_s`` are injectable only for direct seam tests.
    The spawning test may set TURNOS_TEST_SWEEP_RELEASE_GATE_TIMEOUT_S
    (default 30 s): its choreography spans a second worker's whole boot,
    which on a loaded machine outlasts 30 s.
    """
    import time
    if not test_seams_enabled():
        return
    gate_key = (os.environ.get("TURNOS_TEST_SWEEP_RELEASE_GATE_KEY") or "").strip()
    if not gate_key:
        return
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("TURNOS_TEST_SWEEP_RELEASE_GATE_TIMEOUT_S") or 30.0)
        except ValueError:
            timeout_s = 30.0
    from app.core import db
    from app.core import db_locks
    lock_key = "test_sweep_release_gate:" + gate_key
    deadline = time.monotonic() + timeout_s
    with db._connect() as conn, conn.cursor() as cur:
        while True:
            # Transaction scope is intentional: this connection comes from
            # the shared pool, so a session lock would survive an exception
            # unless every future error path remembered to unlock it.
            if db_locks._try_advisory_xact_lock(cur, lock_key):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"test release gate {gate_key!r} was not released within "
                    f"{timeout_s:g}s; refusing to run the sweep")
            time.sleep(poll_s)


def test_hold():
    """Test seam ONLY, double-gated behind `test_seams_enabled()`: with
    TURNOS_TEST_SEAMS=1 AND TURNOS_TEST_SWEEP_HOLD_S set, holds a sweep's
    task body in 'doing' long enough for the crash-recovery suites to
    SIGKILL the worker genuinely MID-task. Either var alone (e.g.
    accidentally inherited env) is a no-op — a real deployment can never
    be put to sleep by one stray line. Lived as identical private copies
    in housekeeping_queue/referrals_queue until the registry-hardening
    round; now `register_periodic_sweep` calls it for EVERY sweep, so all
    four queues are crash-testable the same way."""
    import os
    import time
    if not test_seams_enabled():
        return
    # Event-driven test rendezvous for contention proofs.  A fixed sleep
    # only guesses that another worker has reached the intended state; this
    # key is held by the test and released only after it observes that state.
    # It is deliberately inert without the shared master test-seam switch.
    _wait_for_test_release_gate()
    try:
        hold = float(os.environ.get("TURNOS_TEST_SWEEP_HOLD_S", "0") or 0)
    except ValueError:
        return
    if hold > 0:
        time.sleep(hold)


def task_lock_name(task_name):
    """The per-task EXECUTION lock string for `exclusive` sweeps — ONE
    derivation, used by registration and pinned by the tests."""
    return f"task:{task_name}"


def register_periodic_sweep(app, *, queue, task_name, interval_min, cron=None,
                            body, exclusive=False, monitor=None):
    """THE one registration shape every queue-migrated sweep uses
    (registry-hardening round, 2026-08-08): derives the cron from the
    sweep's thread-twin cadence (`cron_for_interval` — exact or
    ValueError), registers `body` as a periodic task with `retry=False`
    (a task that raises stays failed: the diagnosis trail), and opens the
    body with the double-gated `test_hold()` so the crash suites can
    SIGKILL any sweep genuinely mid-task. Before this helper, each queue
    module carried its own copy of the decorator stack, the `cron` test
    seam and (two of them) `_test_hold` — the duplication the
    worker-registry card ordered removed.

    `cron`, if given, OVERRIDES the interval-derived expression — a test
    seam only (sub-minute cadence for fast end-to-end cases); production
    call sites never pass it. `body(timestamp)` is the module's own sweep
    wrapper — business logic and logging stay with the module that owns
    the sweep, never here.

    `exclusive=True` (the batch-3 AI-cache warmups) serializes EVERY
    invocation path of the task — cron tick, startup defer, crash-recovery
    re-run — through procrastinate's NATIVE per-job `lock` (registered as
    the task-level default, `task_lock_name(task_name)`, so every defer
    inherits it: `Task.configure` falls back to the task's own lock when a
    caller supplies only queueing_lock/schedule_in/schedule_at, and a recovery requeue
    reuses the original row, lock column included). The library's contract
    is exactly what the outbound-capable sweeps need — "no two jobs with
    the same lock string can run simultaneously", enforced at FETCH time
    in procrastinate_fetch_job_v2 with FIFO ordering among same-lock rows —
    so a second job of the task WAITS ('todo') while one runs, then runs
    after it: no overlap, no lost tick. The double-spend path is closed
    by the CHAIN, stated honestly: the lock removes overlap (two
    executions reading the same stale state and both paying), and the
    serialized follow-up run sees the first run's persisted effects, so
    its fingerprint/hash guard no-ops on unchanged sources — the exact
    property TestRerunContractsRunTwice proves. Residual cost stays as
    each rerun contract documents it (a death between provider call and
    upsert re-buys that one call).
    Known bound, documented not hidden: a SIGKILLed worker leaves its
    'doing' row holding the lock until the stalled-job recovery requeues
    it (stalled threshold + recovery cadence) — same-lock jobs wait it
    out; they never overlap it."""
    cron_expr = cron or cron_for_interval(interval_min)

    @app.periodic(cron=cron_expr)
    @app.task(queue=queue, name=task_name, retry=False,
              lock=task_lock_name(task_name) if exclusive else None)
    def _sweep(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        if monitor is None:
            test_hold()
            return body(timestamp)
        # `monitor=(slug, margin_min, max_runtime_min)`: a Sentry Crons check-in around the sweep
        # (TURNOS_SENTRY_SIGNALS=crons; inert otherwise, core/launch_signals.py).
        slug, margin_min, max_runtime_min = monitor
        with launch_signals.monitored(slug, ("crontab", cron_expr), margin_min, max_runtime_min):
            test_hold()
            return body(timestamp)

    return _sweep


def _schema_present(cur):
    import sqlalchemy as sa
    from app.core import sqlcore
    return bool(sqlcore.run(cur, sa.select(
        sa.func.to_regclass("public.procrastinate_jobs").is_not(None).label("present"))).fetchone()["present"])


def task_chain_alive(cur, task_name):
    """True if a 'todo' OR 'doing' job exists for `task_name` (a self-rescheduling chain is queued or
    running). `cur` yields dict rows or tuples; only the presence of a row is read."""
    import sqlalchemy as sa
    from app.core import sqlcore
    from app.core.schema_model import procrastinate_jobs as jobs
    return sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(jobs).where(
        jobs.c.task_name == task_name,
        jobs.c.status.in_([sa.literal_column("'todo'"), sa.literal_column("'doing'")])
    ).limit(sa.literal_column("1"))).fetchone() is not None


def chain_alive(task_name, cur=None):
    """`task_chain_alive` for a caller that may or may not already hold a cursor: `cur` is an open psycopg
    cursor to reuse (a caller inside a critical section on a checked-out connection - a second pooled
    checkout there could exhaust a small pool, e.g. TURNOS_DB_POOL=1); without it a fresh, self-contained
    `db._connect()` is opened and closed here."""
    if cur is not None:
        return task_chain_alive(cur, task_name)
    from app.core import db
    with db._connect() as conn, conn.cursor() as own_cur:
        return task_chain_alive(own_cur, task_name)


def ensure_schema():
    """Applies procrastinate's bundled schema.sql exactly once. Returns True
    if it just applied it (first time ever against this database), False if
    it was already present.

    The schema is NOT idempotent DDL — confirmed by reading the bundled
    `schema.sql`: bare `CREATE TABLE`/`CREATE TYPE`, no `IF NOT EXISTS`
    guard on either — a second apply against an already-migrated database
    raises. Guarded two ways: a cheap `to_regclass` existence check first,
    then (only if that says "absent") the SAME keyed
    `pg_advisory_xact_lock(hashtext(...))` idiom `db_booking.create_booking_request`
    already uses for a check-then-act race (db.py:3749), re-checking
    existence AFTER acquiring the lock so two processes booting at once (a
    Coolify rolling redeploy, or the web process racing the worker process)
    can't both see "absent" and both try to `CREATE TYPE`.

    Deliberately never called from `db._connect()`'s boot path — nothing
    here runs unless a queue mode was explicitly chosen (the in-process
    pilot's `waitlist._start_queue()`, or `worker.py` starting up). Every
    existing Alembic zero-DDL proof (test_alembic_revisions.py) exercises
    `db._connect()` alone and is untouched; see tests/test_waitlist_queue.py
    ::TestZeroDDLWhenFlagOff for the matching proof for this schema."""
    from app.core import db
    from app.core import db_values
    from app.core import db_locks
    with db._connect() as conn, conn.cursor() as cur:
        if _schema_present(cur):
            return False
        db_locks._advisory_xact_lock(cur, db_values._text_value(SCHEMA_LOCK_KEY))
        if _schema_present(cur):
            return False
        # Still holding the advisory lock on THIS connection's transaction
        # (released at commit/rollback below) while a SEPARATE, throwaway
        # procrastinate connector actually applies the schema on its own
        # connection — same reasoning Tier 4 slice 4 already used for
        # Alembic's own connection vs. the outer pg_advisory_lock (db.py's
        # `_run_alembic_upgrade` docstring): every OTHER process racing to
        # reach this point blocks on `pg_advisory_xact_lock` itself, before
        # it ever opens its own connection, so cross-process serialization
        # holds without the two connections needing to be the same one.
        _apply_schema_standalone()
        conn.commit()
        _log("esquema de procrastinate aplicado (primera vez)")
        return True


def _apply_schema_standalone():
    import procrastinate
    from app.core import db
    connector = procrastinate.SyncPsycopgConnector(conninfo=db._db_url(),
                                                     min_size=1, max_size=1)
    app = procrastinate.App(connector=connector)
    app.open()
    try:
        app.schema_manager.apply_schema()
    finally:
        app.close()


def build_app(max_pool_size=3):
    """One procrastinate App over the async `PsycopgConnector` — the ONLY
    connector class that can run a worker loop (`SyncPsycopgConnector`
    raises `SyncConnectorConfigurationError` the moment `run_worker()`
    touches it; found by running it, not by reading docs — see the slice 5
    note in docs/roadmap/tier4-replatform-design.md). Task bodies stay
    plain sync functions: procrastinate bridges them onto the worker's
    event loop via a thread executor, so nothing in this repo's business
    logic goes async. Callers register their tasks on the returned app
    (each job module's `register(app, ...)`)."""
    import procrastinate
    from app.core import db
    connector = procrastinate.PsycopgConnector(conninfo=db._db_url(),
                                                 min_size=1,
                                                 max_size=max_pool_size)
    return procrastinate.App(connector=connector)
