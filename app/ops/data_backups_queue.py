"""Queue bridge for the durable off-box-data-backup producer.

Backups are completion-anchored: the legacy thread waits the configured
interval *after* a sweep finishes.  A fixed cron grid cannot preserve that
for arbitrary values (for example 45 minutes), so this task self-reschedules
from completion.  Both its first 60-second grace and every later wait live in
Postgres job rows, never an in-memory sleep.

The producer (`ops.data_backups.tick`) owns B2 and the byte/journal protocol.
This module owns only queue scheduling.  It deliberately has no restore,
listing, pruning, retention, or B2 credential behaviour.
"""

import asyncio
import sys
import time
from app.core import launch_signals
from app.core import log_pii

_QUEUE = "data_backups"
_TASK_NAME = "ops.data_backups.sweep"
_STARTUP_GRACE_S = 60


def _log(msg):
    print(f"[data_backups_queue] {msg}", file=sys.stderr, flush=True)


def _queueing_lock():
    """One lineage across boot kicks and completion re-schedules."""
    return f"startup:{_TASK_NAME}"


def chain_alive(cur=None):
    """Whether this self-rescheduling lineage is queued or executing."""
    from app.core import queue_infra
    return queue_infra.chain_alive(_TASK_NAME, cur)


def _next_schedule_at(cur, interval_s, startup_delay_s):
    """Compute the next absolute due time on the Postgres clock.

    On a true cold start this returns the historical 60-second grace.  Once
    the task has succeeded, it returns that success plus the exact interval,
    clamped to now.  `clock_timestamp()` avoids a stale transaction clock.
    """
    import sqlalchemy as sa
    from app.core import sqlcore
    from app.core import db_values
    from app.core.schema_model import procrastinate_events, procrastinate_jobs
    e, j = procrastinate_events.alias("e"), procrastinate_jobs.alias("j")
    last = sa.func.max(e.c.at)
    row = sqlcore.run(cur, sa.select(sa.case(
        (last.is_(None), sa.func.clock_timestamp() + db_values._interval(startup_delay_s, "second")),
        else_=sa.func.greatest(sa.func.clock_timestamp(), last + db_values._interval(interval_s, "second"))
    ).label("next_at")).select_from(e.join(j, j.c.id == e.c.job_id)).where(
        j.c.task_name == _TASK_NAME, e.c.type == sa.literal_column("'succeeded'"))).fetchone()
    return row["next_at"] if row else None


async def _acquire_rekick_lock(cur, timeout_s=20.0, poll_s=0.2):
    """Cooperatively acquire the bootstrap check/defer advisory lock."""
    from app.core import db_locks
    deadline = time.monotonic() + timeout_s
    while True:
        if db_locks._try_advisory_lock(cur, _TASK_NAME + ":rekick"):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_s)


async def rekick(app, *, interval_min, startup_delay_s=_STARTUP_GRACE_S):
    """Ensure one live backup chain, without creating a back-to-back sweep."""
    if interval_min <= 0:
        raise ValueError("backup interval must be positive")
    from procrastinate import exceptions as pexc
    from app.core import db
    from app.core import db_locks

    interval_s = interval_min * 60
    with db._connect() as conn, conn.cursor() as cur:
        held = await _acquire_rekick_lock(cur)
        if not held:
            _log("startup/watchdog coordination lock timed out; next pass retries")
            return False
        try:
            if chain_alive(cur):
                return False
            next_at = _next_schedule_at(cur, interval_s, startup_delay_s)
            try:
                deferrer = app.tasks[_TASK_NAME].configure(
                    queueing_lock=_queueing_lock(), schedule_at=next_at)
                await deferrer.defer_async(timestamp=int(time.time()))
                return True
            except pexc.AlreadyEnqueued:
                return False
        finally:
            try:
                db_locks._advisory_unlock(cur, _TASK_NAME + ":rekick")
            except Exception as exc:  # noqa: BLE001 - pooled session must not retain it
                _log(f"could not release scheduling lock ({log_pii.exc(exc)}); closing connection")
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


def register(app, interval_min, cron=None):
    """Register one exclusive self-rescheduling backup task.

    `cron` is rejected intentionally: arbitrary legacy intervals remain valid
    only by scheduling from completion, never by an approximate grid.
    """
    if cron is not None:
        raise ValueError("data_backups queue is self-rescheduling, not cron-based")
    if interval_min <= 0:
        raise ValueError("backup interval must be positive")
    from app.core import queue_infra
    lock = queue_infra.task_lock_name(_TASK_NAME)
    interval_s = interval_min * 60

    @app.task(queue=_QUEUE, name=_TASK_NAME, retry=False, lock=lock)
    async def _sweep(timestamp):  # noqa: ANN001
        def _run_blocking():
            queue_infra.test_hold()
            from app.ops import data_backups
            # Sentry Crons (TURNOS_SENTRY_SIGNALS=crons): a sweep that never starts or never ends is a missed
            # check-in. The sweep waits `interval_min` after each completion, so allow half an interval on top.
            with launch_signals.monitored("turnos-backup-data", ("interval", interval_min),
                                          interval_min // 2 + 30, 180):
                return data_backups.tick()

        # `tick` contains its own session advisory lock held across B2 and
        # journal commit.  The procrastinate task lock adds queue-level
        # serialisation; both worker replicas and an old web thread converge.
        uploaded = await asyncio.get_running_loop().run_in_executor(None, _run_blocking)
        try:
            # This task has not become a `succeeded` event yet (that happens
            # only after this coroutine returns), so deriving from the event
            # table here would see the *previous* completion and could queue
            # an immediate back-to-back no-op.  `schedule_in` is evaluated at
            # this completion point by procrastinate, matching the legacy
            # thread's `tick(); wait(interval)` contract exactly.  The boot
            # and watchdog `rekick` path is different: it runs outside a task
            # and therefore correctly derives an absolute time from the last
            # recorded success on PostgreSQL's clock.
            deferrer = app.tasks[_TASK_NAME].configure(
                queueing_lock=_queueing_lock(),
                schedule_in={"seconds": interval_s})
            await deferrer.defer_async(timestamp=int(time.time()))
        except Exception as exc:  # noqa: BLE001 - watchdog repairs the chain
            _log(f"next sweep was not scheduled ({log_pii.exc(exc)}); maintenance watchdog retries")
            launch_signals.handled("backup_data_reschedule_failed")
        return {"uploaded": uploaded}

    return _sweep
