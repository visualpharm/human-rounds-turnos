"""hsi_journal_queue.py — the durable EMR journal sweep as a self-rescheduling
procrastinate task (Tier 4 daemon batch 4, docs/roadmap/tier4-replatform-design.md,
"Worker topology"): executes the SAME tick() body as the daemon thread
(backfill + outbox replay + reconciliation + conflict resolution), driven by
a DIFFERENT scheduling mechanism than every other queue-migrated job — see
"Why self-rescheduling, not periodic cron" below. Byte-identical tick() body —
this module owns scheduling only; the journal semantics (hsi_outbox replay,
hsi_inbox reconciliation, resolver auto-close) stay in hsi_journal.py.

This is Core-owned infrastructure, not vendor-specific. Previously lived at
connectors/hsi/hsi_sync_queue.py as part of the vendor package. Moved to
modules/turnos/ (2026-08-31, S1 of HSI/Core boundary repair) alongside the
journal it schedules.

OUTBOUND-capable by design: `tick()` calls `replay()` (pushes queued
appointment intents to HSI) and `reconcile()` (pulls diaries/appointments/
patients from HSI) — real network calls to `HSI_URL` whenever
TURNOS_SKIP_HSI is not set. Proven zero-egress/zero-spend under the
fail-closed outbound kill switch with a CONFIGURED dummy HSI_URL
(test_hsi_queue_proof.py), same discipline as the batch-3 AI-cache
warmups.

Deliberately a SEPARATE sibling module from `hsi_sync.py`, never imported
at that module's top level — same reasoning as every other `*_queue.py`
twin: `hsi_sync.py` is imported unconditionally by the web process
(server.py's boot path, solicitudes._push_hsi), so anything at ITS top
level becomes an unconditional cost of the default (no queue) deployment
shape. Only `worker.py` imports this module, and only when the worker
process actually runs.

Why self-rescheduling, not periodic cron (review fix, 2026-08-09 — this
was the SECOND design tried and rejected here; the first, a fixed cron
grid plus a "skip if recently succeeded" guard, is recorded below because
its failure mode is the actual reason this shape exists):

The thread loop (`hsi_sync._loop`) is `while True: tick(); wait(interval_s)`
— it waits interval_s AFTER each tick COMPLETES, so consecutive ticks are
ALWAYS EXACTLY interval_min apart, measured from completion to next start.
That is a rolling, completion-anchored cadence — NOT a fixed wall-clock
grid. `queue_infra.register_periodic_sweep`'s `@app.periodic(cron=...)`
IS a fixed grid (every batch-2/3 job uses it, safely, because their
sweeps are cheap DB-only no-ops when nothing changed). A first attempt
here tried to bridge the two: keep the cron grid, and have the task body
skip itself if a prior run succeeded "recently enough" (a percentage of
the interval). That CANNOT be made exact, for a structural reason: a
tick's completion time and the cron grid's boundaries are two independent
clocks with no fixed relationship. If a completion lands even slightly
BEHIND a grid boundary, the very next boundary gets skipped by the guard
(too soon since completion) and the tick doesn't fire until the boundary
AFTER that — silently stretching the gap toward ~2x the configured
interval. A percentage-based guard cannot both forbid early firing (which
requires the full 100% of the interval as the threshold) and forbid this
stretch (which requires the grid and the completion clock to already be
aligned, which they generally aren't). No threshold choice fixes both at
once, and reconcile() makes a REAL, unconditional HSI network read every
tick — unlike the batch-2/3 jobs, a cadence deviation here has an actual
external cost, which is exactly why the card asks for cadence
PRESERVATION, not approximation.

The fix: don't use a periodic task at all. hsi_sync's task is registered
as a plain `@app.task` (no `@app.periodic`). Each successful tick, AT
COMPLETION, defers its own next invocation `schedule_in=interval_min*60`
seconds — durable (a Postgres job row, not an in-memory timer), and this
reproduces the thread's exact completion-anchored spacing because it uses
the SAME mechanism: wait, then act, using the actual completion moment as
the reference point instead of a fixed grid. "Exact" here means the
computed `scheduled_at` value, not the wall-clock instant the tick
actually starts running — like every other job in this queue topology,
the fetch itself is subject to procrastinate's own small, bounded
polling delay (`build_app()`'s worker loop), so a tick becomes DUE
exactly on schedule and is PICKED UP shortly after, never instantly and
never unbounded.

Cold start vs. warm restart, disambiguated by ONE reused queueing lock:
`worker._defer_startup_jobs` still kicks off the FIRST tick at boot
(startup=True, startup_delay_s=0, unchanged — the thread's own first tick
is immediate too) using queueing_lock=f"startup:{task_name}". The
self-reschedule below deliberately reuses that EXACT same queueing_lock
string for every subsequent defer, not a fresh one — procrastinate's
queueing-lock uniqueness (its own unique index on status='todo') then
means: whenever a chain is already pending (a 'todo' row with that lock
exists, whether it came from the boot defer or a prior tick's
self-reschedule), a NEW boot's startup defer is rejected as
AlreadyEnqueued and quietly skipped — the existing chain just continues.
Only a GENUINE cold start (no such row exists) actually kicks off a fresh
chain. No new "does a chain already exist" query was needed — reusing the
lock string turns a mechanism batch 2/3 already had into a rescue net for
a case they never needed it for.

Known bounded residual, honestly recorded, not hidden: if a worker is
SIGKILLed strictly between a tick completing and its self-reschedule defer
landing, no chain-continuation row exists yet. The completed tick's job
row is 'succeeded' (not 'doing'), so ordinary stalled-job recovery does
NOT apply (that only requeues 'doing' rows) — the chain is genuinely
broken until something re-kicks it. Two independent rescues close this,
not one (staleness fix, 2026-08-09 — this paragraph used to name only the
first and read as if that were the whole story, which stopped being true
the moment the watchdog below shipped the SAME day): the next worker
BOOT's own startup defer (because the queueing lock check at that point
finds no pending 'todo' row, the boot legitimately defers a fresh
immediate tick, same as a cold start) AND, independently, the always-on
steady-state watchdog (`worker._register_hsi_sync_watchdog`, every 5
min — see "Two further review gates closed the same day" below) noticing
the same missing row and re-kicking it the same way, without waiting for
any restart. The gap this closes in is therefore bounded by "time until
the next watchdog pass" (currently <= 5 min) on a worker that stays up,
or by the next restart if that happens to land sooner — a real, named
limitation of this design, not a silent one; unlike the periodic-cron
alternative, it does not recur every cycle in steady state, only once per
this specific narrow crash window, and it self-heals without operator
action either way.

- `TURNOS_SKIP_HSI=1` makes `replay()`/`reconcile()` no-ops exactly as it
  does for the thread — the tick body is unchanged, so the flag's effect
  is unchanged.
- HSI-down / backoff is unchanged: `_replay_one` classifies exceptions
  into retryable (exponential backoff via `_backoff(attempts)`, capped at
  1 h) vs. permanent (durable conflict, human review) exactly as the
  thread's replay path always has — the queue topology adds nothing here,
  it only changes who calls `tick()` and how often.

Two further review gates closed the same day (2026-08-09), both real gaps
in the first cut of this design:

1. **Rolling-restart race**: if worker B boots while worker A's hsi_sync
   row is 'doing' (A alive, mid-tick — not crashed), the queueing lock
   alone does NOT block B's startup kick: the lock's uniqueness only
   covers 'todo' rows, so B successfully defers a fresh 'todo' row, which
   then just WAITS on the execution lock until A's in-flight tick
   finishes — producing a genuine back-to-back tick right after, the
   exact near-boundary defect this whole design exists to close, now via
   a different door. `chain_alive()` below closes the common case: every
   external kicker (the boot-time kick, the watchdog) checks it first and
   skips outright when a 'todo' OR 'doing' row already exists — a live
   chain never needs kicking. A first cut stopped there and called the
   remaining TOCTOU window (two kickers landing within the same instant a
   tick is completing) harmless because "the queueing lock still prevents
   a literal duplicate row" — true, but wrong to call harmless: a kick
   deferred for "now" is EXACTLY the back-to-back HSI read this design
   exists to prevent, just one row instead of two. `rekick()` closes this
   precisely, not just narrows it: an external kick is NEVER scheduled
   earlier than the last success plus the full interval (only a genuine
   never-succeeded cold start gets "now"). Whichever kicker's defer wins
   the race, the row it creates already carries the correct, interval-
   respecting schedule time — there is no longer a scenario where winning
   the race means firing early. Proven with a real two-worker
   rolling-restart race (worker A held mid-tick via
   TURNOS_TEST_SWEEP_HOLD_S, worker B boots while A is genuinely 'doing',
   not crashed, then A's completion and B's kick timed to land close
   together) in test_queue_worker.py's
   TestHsiSyncCrashRecoveryAndContention.
2. **Swallowed reschedule failure could stall the chain indefinitely**:
   the first cut caught a failed self-reschedule defer and let the
   completing tick still succeed, reasoning "rescued at next boot" — true
   only if the worker ever restarts; a long-lived worker that never
   restarts would silently stop ticking hsi_sync forever, with nothing
   watching. worker.py now registers a small always-on maintenance
   watchdog (`_register_hsi_sync_watchdog`, every 5 min, the same cadence
   the stalled-job recovery pass already uses) that calls the SAME
   `rekick()` the moment it finds neither a 'todo' nor a 'doing' row for
   this task — interval-respecting, not "now", for the same reason as
   point 1. Proven by forcing a defer failure and confirming the watchdog
   heals the chain, on schedule, while the worker stays up — not only
   across a restart.
"""
import os
import sys
import time
from app.core import launch_signals
from app.core import log_pii

_QUEUE = "hsi_sync"
# Preserve the old task name for backward compatibility with persisted queue rows
_TASK_NAME = "connectors.hsi.hsi_sync.tick"

_force_reschedule_fail_once = False  # test-only, see _maybe_force_reschedule_failure


def _log(msg):
    print(f"[hsi_sync_queue] {msg}", file=sys.stderr, flush=True)


def _maybe_force_reschedule_failure():
    """Test seam ONLY, double-gated behind TURNOS_TEST_SEAMS=1 AND
    TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE=1 (same discipline as
    core.queue_infra.test_hold — either var alone is a no-op). Raises
    ONCE per process (a module-level flag, cleared after firing so a
    long-running worker's LATER ticks reschedule normally), from INSIDE
    the self-reschedule's own try/except in _sweep below — a genuine
    forced failure of that exact call, not a simulated end state (e.g.
    deleting the continuation row after the fact would prove the
    watchdog notices a broken chain, but not that this module's own
    swallowed-exception handling is what got it into that state, or that
    the completing tick's OWN result survives the failure). Exists for
    test_watchdog_heals_broken_chain_while_worker_stays_up."""
    global _force_reschedule_fail_once
    from app.core import queue_infra
    if not queue_infra.test_seams_enabled():
        return
    if os.environ.get("TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE") != "1":
        return
    if _force_reschedule_fail_once:
        return
    _force_reschedule_fail_once = True
    raise RuntimeError("forced reschedule failure (TURNOS_TEST_HSI_FORCE_RESCHEDULE_FAIL_ONCE)")


def _rekick_skip_advisory_lock():
    """Test seam ONLY, double-gated behind TURNOS_TEST_SEAMS=1 AND
    TURNOS_TEST_HSI_SKIP_ADVISORY_LOCK=1 — makes rekick() skip its own
    pg_advisory_lock/unlock calls, running the check-compute-defer
    sequence WITHOUT the guard that closes the concurrent-cold-start
    race. Exists ONLY to let a test deterministically reproduce that race
    (RED: two kickers both pass chain_alive()==False and both defer) as
    the direct counterpart to the same scenario proven safe WITH the lock
    (GREEN) — never meant to run against real HSI or a real deployment,
    which is exactly what the double gate prevents."""
    from app.core import queue_infra
    if not queue_infra.test_seams_enabled():
        return False
    return os.environ.get("TURNOS_TEST_HSI_SKIP_ADVISORY_LOCK") == "1"


def _test_break_unlock():
    """Test seam ONLY, double-gated behind TURNOS_TEST_SEAMS=1 AND
    TURNOS_TEST_HSI_BREAK_UNLOCK=1 — makes rekick()'s own unlock
    statement fail DETERMINISTICALLY (a deliberately-invalid SQL
    fragment, a genuine Postgres error, not a monkeypatch or a simulated
    end state) so a test can prove the connection-discard fallback in
    rekick()'s `finally` actually runs. Exists for
    test_rekick_closes_connection_when_unlock_fails."""
    from app.core import queue_infra
    if not queue_infra.test_seams_enabled():
        return False
    return os.environ.get("TURNOS_TEST_HSI_BREAK_UNLOCK") == "1"


async def _maybe_rekick_hold():
    """Test seam ONLY, double-gated behind TURNOS_TEST_SEAMS=1 AND
    TURNOS_TEST_HSI_REKICK_HOLD_S. Holds INSIDE rekick()'s own
    check-compute-defer section, after chain_alive() and before the
    defer — the window a genuine concurrent kicker races through.
    asyncio.sleep, not time.sleep — must not stall the worker's event
    loop.

    A fixed-duration hold: see `_maybe_rekick_release_gate()` below for
    the EVENT-DRIVEN alternative the cold-boot-race tests use instead (a
    fixed N is a timing assumption, however generous). Kept for tests
    that genuinely want "hold for roughly this long" — still
    unit-tested directly by test_rekick_hold_flag_alone_is_inert."""
    from app.core import queue_infra
    if not queue_infra.test_seams_enabled():
        return
    hold = os.environ.get("TURNOS_TEST_HSI_REKICK_HOLD_S", "0") or "0"
    try:
        hold = float(hold)
    except ValueError:
        return
    if hold > 0:
        import asyncio
        await asyncio.sleep(hold)


async def _maybe_rekick_release_gate(cur, timeout_s=None, poll_s=0.2):
    """Test seam ONLY, double-gated behind TURNOS_TEST_SEAMS=1 AND
    TURNOS_TEST_HSI_REKICK_GATE_KEY=<name> — an EVENT-DRIVEN alternative
    to `_maybe_rekick_hold()`'s fixed-duration sleep (see that function's
    docstring for why a fixed hold is only ever a timing assumption). A
    distinct advisory-lock key (`"test_rekick_gate:" + key`) from
    rekick()'s own coordination lock — one session can hold both
    concurrently. The calling TEST acquires that key BEFORE spawning
    this worker and releases it only after directly observing the
    condition it cares about, so this call returns exactly when that
    became true. Polls `pg_try_advisory_lock()` cooperatively, same
    technique as `_acquire_rekick_lock()`. The 30 s default is a safety
    net against a crashed test process, not a real bound; the spawning
    test may set TURNOS_TEST_HSI_REKICK_GATE_TIMEOUT_S, because its
    choreography spans a second worker's whole boot, which on a loaded
    machine outlasts 30 s. Releases the
    gate lock again immediately once acquired — a one-shot rendezvous,
    not real mutual exclusion.

    `cur` is REQUIRED, unlike `chain_alive()`'s optional one: called
    from exactly one place, inside rekick()'s own already-open critical
    section, so a second `db._connect()` here would nest a checkout the
    same way `chain_alive()`'s bug did (missed for this seam that
    round). Contract: `cur` must be a `row_factory=dict_row` cursor
    (the same one `core.db._connect()` always hands out, and the only
    kind rekick() ever passes) — this function indexes its query result
    by column name, same as `_next_schedule_at()`/`_acquire_rekick_lock()`
    beside it. A caller outside rekick() (a direct test, say) must open
    its own connection the same way to satisfy that contract; a plain
    `psycopg.connect()` without it returns tuple rows and breaks here."""
    from app.core import queue_infra
    from app.core import db_locks
    if not queue_infra.test_seams_enabled():
        return
    key = (os.environ.get("TURNOS_TEST_HSI_REKICK_GATE_KEY") or "").strip()
    if not key:
        return
    import asyncio
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("TURNOS_TEST_HSI_REKICK_GATE_TIMEOUT_S") or 30.0)
        except ValueError:
            timeout_s = 30.0
    gate_lock_key = "test_rekick_gate:" + key
    deadline = time.monotonic() + timeout_s
    while True:
        if db_locks._try_advisory_lock(cur, gate_lock_key):
            db_locks._advisory_unlock(cur, gate_lock_key)
            return
        if time.monotonic() >= deadline:
            _log(f"rekick: gate de test {key!r} nunca se liberó dentro "
                 f"de {timeout_s:g}s - prosiguiendo de todos modos "
                 "(bug de test?)")
            return
        await asyncio.sleep(poll_s)


def _queueing_lock():
    """THE one queueing-lock string for hsi_sync's chain — shared by
    worker._defer_startup_jobs' boot-time kick, this module's own
    self-reschedule, and the steady-state watchdog, so procrastinate's
    unique 'todo' index sees all three as ONE lineage (cold-start-vs-
    warm-restart disambiguation — see module docstring)."""
    return f"startup:{_TASK_NAME}"


def chain_alive(cur=None):
    """True if a 'todo' OR 'doing' row already exists for hsi_sync's
    task — the chain is alive (queued or genuinely executing), so kicking
    a fresh one would race a live worker into a back-to-back tick right
    after the in-flight one completes. Used by BOTH the boot-time startup
    defer (worker._defer_startup_jobs) and the steady-state watchdog
    (_register_hsi_sync_watchdog) — one truth, not two copies that could
    drift.

    `cur`: an already-open psycopg cursor to reuse (see `queue_infra.chain_alive`), for a caller
    that already holds a checked-out connection (rekick()'s own critical section). Standalone
    callers omit it."""
    from app.core import queue_infra
    return queue_infra.chain_alive(_TASK_NAME, cur)


def _next_schedule_at(cur, interval_s):
    """Absolute next-tick timestamp, computed ENTIRELY inside Postgres in
    ONE query. Requires an already-open cursor (only ever called from
    rekick()'s own critical section — same reasoning as `chain_alive()`'s
    `cur`). Returns a tz-aware `datetime` for `configure(schedule_at=...)`,
    or `None` on a genuine cold start (caller then omits
    schedule_at/schedule_in entirely, matching the thread's own immediate
    first tick). `GREATEST(clock_timestamp(), ...)` clamps to "now" once
    a full interval has elapsed — never schedules in the past.

    Replaces the round-4 elapsed-seconds pair. Handing an elapsed
    DURATION to `configure(schedule_in=...)` still let procrastinate
    itself convert it to `schedule_at` via the APP's own clock
    (`utils.datetime_from_timedelta_params()` calls `utils.utcnow()` —
    confirmed against procrastinate's own source, not assumed) —
    reopening the exact clock-skew risk this was meant to close.
    Computing the ABSOLUTE timestamp here removes the app clock from the
    path entirely.

    `clock_timestamp()`, not `now()`: `now()`/`transaction_timestamp()`
    freeze at the Postgres TRANSACTION's own start, and rekick()'s
    connection is NOT autocommit (`_PooledConn.__exit__` commits/rolls
    back explicitly) — so its transaction opens before the advisory-lock
    poll and any test-gate hold that can burn real seconds before this
    query ever runs. `now()` here would read that stale pre-hold
    instant; `clock_timestamp()` always reads the actual current one."""
    import sqlalchemy as sa
    from app.core import sqlcore
    from app.core import db_values
    from app.core.schema_model import procrastinate_events, procrastinate_jobs
    e, j = procrastinate_events.alias("e"), procrastinate_jobs.alias("j")
    last = sa.func.max(e.c.at)
    row = sqlcore.run(cur, sa.select(sa.case(
        (last.is_(None), sa.null()),
        else_=sa.func.greatest(sa.func.clock_timestamp(), last + db_values._interval(interval_s, "second"))
    ).label("next_at")).select_from(e.join(j, j.c.id == e.c.job_id)).where(
        j.c.task_name == _TASK_NAME, e.c.type == sa.literal_column("'succeeded'"))).fetchone()
    return row["next_at"] if row else None


async def _acquire_rekick_lock(cur, timeout_s=20.0, poll_s=0.2):
    """Non-blocking, event-loop-cooperative acquisition of rekick()'s own
    coordination advisory lock. The original `pg_advisory_lock()` (no
    timeout) is a synchronous psycopg call made directly inside rekick()'s
    coroutine body — unlike a task's whole BODY (which procrastinate
    itself bridges onto a thread for a plain sync function, see
    `core.queue_infra.build_app()`'s docstring), a helper called FROM an
    already-async body gets no such bridging: a contended lock blocked
    the ENTIRE event loop, heartbeat sender included, for as long as the
    other holder's critical section took — reachable from both rekick()
    callers.

    `pg_try_advisory_lock()` polled here, yielding via `asyncio.sleep()`
    between attempts, keeps the loop free while waiting; the lock is
    still held EXCLUSIVELY once acquired. `timeout_s=20` is generous
    against every hold this codebase's tests use (<= 6 s) and against a
    real critical section (two cheap SELECTs plus one `defer_async()`
    round trip); a contention past that means something else is wrong,
    and giving up is safe — the next watchdog pass (<= 5 min) or the
    still-in-progress kicker owns the chain regardless.

    Logs "rekick: lock contendido, esperando" once, on the first failed
    attempt, unconditionally — the observable signal
    test_simultaneous_cold_boot_never_double_kicks needs to prove a
    second kicker is genuinely blocked, not probably blocked by now.

    Returns True once acquired, False on timeout (never raises)."""
    import asyncio
    from app.core import db_locks
    deadline = time.monotonic() + timeout_s
    logueado = False
    while True:
        if db_locks._try_advisory_lock(cur, _TASK_NAME + ":rekick"):
            return True
        if not logueado:
            _log("rekick: lock contendido, esperando")
            logueado = True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_s)


async def rekick(app):
    """Defers the chain's next tick at the SAFE time, or does nothing if
    the chain is already alive. Three algorithm mistakes, found in
    sequence by review (see module docstring for the outer design this
    sits inside):

    1. Checking chain_alive() in the CALLER, then always scheduling
    "now", is wrong even with that check in place: a job ACTIVELY
    'doing' (not yet completed) means the last-success time still
    reflects the PREVIOUS cycle, so a kicker landing in that window
    would compute a near-zero delay, not "later" — the check and the
    schedule-time computation must happen at the SAME instant, so both
    live HERE, not split across callers.
    2. Scheduling unconditionally at last-success + interval is only
    safe once the check above is current — folding it into this same
    function is what makes that hold.
    3. The queueing lock's uniqueness only rejects a SECOND insert while
    the FIRST is still 'todo'. On a genuine never-succeeded cold start,
    two external kickers can both read chain_alive()==False, and if
    kicker A's row gets FETCHED into 'doing' before kicker B's defer
    runs, B's insert no longer collides with anything 'todo' — a
    back-to-back tick via a THIRD door, this time in the insertion
    window itself. Procrastinate's own atomicity (a job's terminal
    status and its 'succeeded' event land atomically) says nothing
    about two SEPARATE processes both deciding to insert at once — a
    classic check-then-act race the queueing lock alone can't close.

    Closed with a Postgres SESSION-scoped advisory lock
    (`hashtext(task_name)`, the same FAMILY `db_booking.create_booking_request`'s
    xact-scoped variant uses — see queue_infra.ensure_schema()'s
    docstring — though that one auto-releases at commit; this one is
    explicit lock/unlock, acquired via `_acquire_rekick_lock()`) wrapping
    the ENTIRE check-compute-defer sequence: only one process can be
    inside this critical section for hsi_sync's task at a time,
    structurally, independent of procrastinate's own row-level
    atomicity. Held on ONE connection for the whole function body,
    released in `finally` (also automatically by Postgres if the
    session itself dies). Combined with the shared queueing lock (belt
    and suspenders, not the primary guard anymore), this closes the
    doing<->succeeded boundary race AND the concurrent-cold-start race,
    both precisely. Proven with a real two-worker simultaneous-cold-boot
    race in test_queue_worker.py's TestHsiSyncCrashRecoveryAndContention.

    Two later bugs, both about HOW this talks to Postgres, not the
    algorithm above — see `_acquire_rekick_lock()`'s docstring for the
    event-loop-blocking lock fix, and `_next_schedule_at()`'s for the
    connection-pool-exhaustion and clock-skew fixes it replaced two
    earlier functions to close."""
    from procrastinate import exceptions as _pexc
    from app.modules.turnos import hsi_journal
    if hsi_journal._off() or not hsi_journal._emr_selected():
        return False
    from app.core import db
    from app.core import db_locks
    interval_s = hsi_journal._interval_min() * 60
    skip_lock = _rekick_skip_advisory_lock()  # test seam ONLY — see its own docstring

    with db._connect() as conn, conn.cursor() as cur:
        held = False
        if not skip_lock:
            held = await _acquire_rekick_lock(cur)
            if not held:
                _log("rekick: no se pudo tomar el lock de coordinación "
                     "dentro del plazo (20s) — se reintenta en el "
                     "próximo ciclo (watchdog o el kicker en curso)")
                return False
        try:
            if chain_alive(cur):
                return False
            # Logged unconditionally (cheap, informative in real deploys
            # too) — also the synchronization point
            # test_simultaneous_cold_boot_never_double_kicks needs to
            # know a chain_alive()==False decision has genuinely been
            # made before spawning the second worker, removing launch-
            # order luck from that test.
            _log("rekick: chain_alive=False, procediendo")
            await _maybe_rekick_hold()  # test seam ONLY — inert unless armed
            await _maybe_rekick_release_gate(cur)  # test seam ONLY — inert unless armed
            _log("rekick: hold terminado, prosiguiendo")  # second sync point — see _log above
            next_at = _next_schedule_at(cur, interval_s)
            try:
                kwargs = {"queueing_lock": _queueing_lock()}
                if next_at is not None:  # None: cold start, immediate — matches the thread's own first tick
                    kwargs["schedule_at"] = next_at
                deferrer = app.tasks[_TASK_NAME].configure(**kwargs)
                await deferrer.defer_async(timestamp=int(time.time()))
                return True
            except _pexc.AlreadyEnqueued:
                return False
            except Exception as exc:  # noqa: BLE001 — best-effort; caller logs context
                _log(f"rekick falló: {log_pii.exc(exc)}")
                return False
        finally:
            if held:
                # putconn() on this pool does NOT reset session state
                # (core.db._PooledConn.__exit__ just commits/rolls back
                # via psycopg's Connection.__exit__ — no DISCARD ALL), so
                # a session-scoped advisory lock survives a normal
                # putconn on an otherwise-healthy connection. If the
                # unlock below raised WITHOUT marking the connection
                # broken (a transient query error, not a dead socket), a
                # lock-holding healthy connection could return to the
                # pool and be handed to a later, unrelated caller, which
                # would then silently "acquire" the same lock instantly
                # (advisory locks are reentrant within one session) —
                # defeating the mutual exclusion. Fixed structurally: on
                # any unlock exception, close the raw connection —
                # Connection.__exit__ no-ops on an already-closed one,
                # and psycopg_pool discards it on its next putconn
                # instead of reusing it (confirmed against both
                # libraries' source). A genuine process crash was
                # already safe without this (dead socket -> Postgres
                # releases the lock on disconnect); this closes the
                # narrower path where the connection stays healthy.
                try:
                    if _test_break_unlock():  # test seam ONLY — see its own docstring
                        db_locks._advisory_unlock(cur, _TASK_NAME + ":rekick",
                                            function="pg_advisory_unlock_turnos_test_break")
                    else:
                        db_locks._advisory_unlock(cur, _TASK_NAME + ":rekick")
                except Exception as exc:  # noqa: BLE001 — see comment above
                    _log(f"rekick: no se pudo liberar el lock ({log_pii.exc(exc)}) — "
                         "cerrando la conexión para que el pool la descarte")
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001 — best-effort, already failing
                        pass


def register(app, interval_min, cron=None):
    """Registers hsi_sync's task on a procrastinate App built by the
    CALLER (worker.py; tests build their own). Deliberately NOT
    `queue_infra.register_periodic_sweep` (cron-based) — see the module
    docstring's "Why self-rescheduling" section. `cron` is accepted only
    for call-site symmetry with every other `*_queue.py` module's
    `register(app, interval_min, cron=None)` signature; passing it is a
    caller error (self-rescheduling has no fixed grid to override) and
    raises loudly rather than silently ignoring it. `interval_min` may be
    ANY positive number, fractional included — self-rescheduling has no
    cron grid to approximate, so (unlike `queue_infra.cron_for_interval`,
    which this module never calls) there is nothing to reject except a
    genuinely degenerate value (<= 0, which would self-reschedule
    immediately forever)."""
    if cron is not None:
        raise ValueError("hsi_sync_queue.register does not take a cron "
                         "override — it self-reschedules from completion "
                         "time, there is no fixed grid to express as cron")
    if not (interval_min > 0):
        raise ValueError(f"intervalo de {interval_min} min debe ser > 0 "
                         "— re-programaría inmediatamente para siempre")
    from app.core import queue_infra

    interval_s = interval_min * 60
    lock = queue_infra.task_lock_name(_TASK_NAME)

    @app.task(queue=_QUEUE, name=_TASK_NAME, retry=False, lock=lock)
    async def _sweep(timestamp):  # noqa: ANN001 — tasks receive the unix ts
        from app.modules.turnos import hsi_journal
        # A queued job can outlive its selection. It is deliberately allowed
        # to finish as a no-op, but must neither invoke the journal nor mint
        # a continuation row; pending journal rows remain for re-selection.
        if hsi_journal._off() or not hsi_journal._emr_selected():
            return {"backfilled": 0, "replayed": 0, "resolved": {},
                    "schedules": 0, "appointments": 0, "patients": 0,
                    "conflicts": 0}
        # This coroutine used to call queue_infra.test_hold() and
        # hsi_sync.tick() (fully synchronous) DIRECTLY, un-offloaded —
        # blocking the worker's single event loop for the whole call,
        # including procrastinate's own ~10 s heartbeat sender
        # (worker._update_heartbeat, an asyncio.create_task on that SAME
        # loop). A tick slower than the stalled-job threshold could then
        # make THIS worker's heartbeat go stale while still alive and
        # working, and a DIFFERENT worker's stalled-job recovery (keyed
        # purely on heartbeat age) could requeue and re-run the same job.
        # Every OTHER queue module avoids this for free by staying a
        # plain sync function (core.queue_infra.build_app()'s docstring:
        # procrastinate bridges sync task bodies onto a thread itself —
        # confirmed against its own worker.py, utils.sync_to_async). This
        # module can't go sync too — the self-reschedule below needs
        # defer_async(), and build_app() only configures an ASYNC
        # connector. So only the blocking PART is offloaded, via
        # run_in_executor; the self-reschedule stays awaited normally.
        import asyncio

        from app.core import worker_stop
        stop = worker_stop.check()

        def _run_blocking():
            queue_infra.test_hold()
            from app.modules.turnos import hsi_journal
            # The executor thread is not cancellable either: a worker stop
            # ends the pass between its phases (core.worker_stop).
            return hsi_journal.tick(stop=stop)

        # tick() never raises (its own contract): each phase is wrapped
        # individually and logs+continues on failure, exactly like the
        # thread loop's guarantee that the server/worker stays up.
        resultado = await asyncio.get_running_loop().run_in_executor(None, _run_blocking)
        _log(f"backfill={resultado.get('backfilled')} replay={resultado.get('replayed')} "
             f"schedules={resultado.get('schedules')} appointments={resultado.get('appointments')} "
             f"conflicts={resultado.get('conflicts')} resolved={resultado.get('resolved')}")
        # Self-reschedule from THIS completion — the thread's own
        # "wait, then act" cadence, durable. Best-effort: a failure here
        # is logged, not raised (this tick's own result must not be
        # lost over a scheduling hiccup) — the steady-state watchdog
        # (worker._register_hsi_sync_watchdog, every 5 min) re-kicks the
        # chain if this defer is the one that silently failed, so a
        # long-lived worker that never restarts still self-heals within
        # minutes, not indefinitely.
        if hsi_journal._off() or not hsi_journal._emr_selected():
            return resultado
        try:
            _maybe_force_reschedule_failure()
            deferrer = app.tasks[_TASK_NAME].configure(
                queueing_lock=_queueing_lock(),
                schedule_in={"seconds": interval_s},
            )
            await deferrer.defer_async(timestamp=int(time.time()))
        except Exception as exc:  # noqa: BLE001 — see docstring: the watchdog heals this
            _log(f"no se pudo reprogramar el próximo barrido: {log_pii.exc(exc)} — "
                 "el watchdog de mantenimiento lo re-arranca en <= 5 min")
            launch_signals.handled("hsi_sync_reschedule_failed")
        return resultado

    return _sweep
