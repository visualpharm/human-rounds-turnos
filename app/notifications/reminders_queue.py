"""reminders_queue.py — the reminders sweep as a procrastinate periodic
task (Tier 4 worker-topology slice, docs/roadmap/tier4-replatform-design.md):
the SAME `reminders.tick()` + `reminders.tick_interview()` pair the daemon
thread runs every TURNOS_REMINDER_INTERVAL_MIN minutes, registered on a
queue app instead. Second job migrated after the waitlist pilot — the one
that proves `worker.py`'s composition mechanism generalizes beyond a
single module.

Deliberately a SEPARATE sibling module from `reminders.py`, never imported
at that module's top level — same reasoning as `waitlist_queue.py` vs
`waitlist.py` (see that module's docstring): `reminders.py` is imported
unconditionally by `server.py:main()`, so anything at ITS top level
becomes an unconditional cost of the default (no queue) deployment shape.
Only `worker.py` imports this module, and only when the worker process
actually runs.

ONE task per tick, wrapping BOTH sweeps in order, exactly like the thread
loop's body (`reminders._loop` runs tick() then tick_interview() per
iteration, each in its own try) — two separate periodic tasks would let
the two sweeps drift apart in cadence, which is a behavior change this
slice's acceptance bar ("preserve cadence and externally visible
behavior") forbids. Duplicate-send safety does NOT live here: both ticks
claim-CAS each row (`db_booking.claim_reminder` / `db_booking.claim_interview_reminder`)
before sending, so two workers racing the same sweep hit the same DB-level
guard two overlapping thread ticks already hit today.
"""
import sys
from app.core import launch_signals
from app.core import log_pii

_QUEUE = "reminders"
_TASK_NAME = "notifications.reminders.sweep"


def _log(msg):
    print(f"[reminders_queue] {msg}", file=sys.stderr, flush=True)


def register(app, interval_min, cron=None):
    """Registers the periodic task on a procrastinate App built by the
    CALLER (worker.py; tests build their own). Registration mechanics
    (cron derivation, the `cron` test seam, the double-gated mid-task
    hold for crash suites) live ONCE in
    `queue_infra.register_periodic_sweep` — this module only owns the
    sweep body."""
    from app.core import queue_infra

    def _cuerpo(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        from app.notifications import reminders
        from app.core import worker_stop
        # A worker stop ends both sweeps before the next claim
        # (core.worker_stop): unclaimed rows wait for the next tick.
        stop = worker_stop.check()
        procesadas = 0
        # Each tick guards itself exactly like the thread loop does: one
        # failing must not stop the other (reminders._loop's own shape).
        try:
            procesadas += reminders.tick(stop=stop)
        except Exception as e:  # noqa: BLE001 — same visibility the thread loop gave
            _log(f"tick() falló: {log_pii.exc(e)}")
            launch_signals.handled("reminders_sweep_failed")
        try:
            procesadas += reminders.tick_interview(stop=stop)
        except Exception as e:  # noqa: BLE001
            _log(f"tick_interview() falló: {log_pii.exc(e)}")
            launch_signals.handled("reminders_sweep_failed")
        if procesadas:
            _log(f"barrido: {procesadas} recordatorio(s)")
        return procesadas

    return queue_infra.register_periodic_sweep(
        app, queue=_QUEUE, task_name=_TASK_NAME,
        interval_min=interval_min, cron=cron, body=_cuerpo,
        monitor=("turnos-reminders", max(interval_min, 5), 30))
