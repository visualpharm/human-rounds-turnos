"""Periodic full-backup task. The producer owns locking, due checks and journal."""
import time

from app.core import backup_settings, launch_signals, queue_infra

_QUEUE = "backup_full"
_TASK_NAME = "ops.backup_full.run"
_MONITOR_SLUG = "turnos-backup-full"


def enabled():
    return backup_settings.configured()


def _scheduled_run(_timestamp):
    """The hourly due check. A pass that was not due stays silent. A run that really begins is one Sentry Crons
    check-in — `in_progress` when it starts (so a run that hangs is a runtime alert, even the first one), then
    `ok`, or `error` when it raised; the monitor is scheduled at the backup interval, so a day with no backup is
    a missed check-in. `backup_full_failed` is the matching Sentry code."""
    if not enabled():
        return {"skipped": "unconfigured"}
    from app.ops import backup_full
    try:
        interval_min = int(backup_settings.snapshot()["TURNOS_BACKUP_INTERVAL_MIN"])
    except ValueError:
        interval_min = 1440
    check = (_MONITOR_SLUG, ("interval", max(interval_min, 1)), 180, 240)
    started = time.monotonic()
    check_in = []

    def on_start():
        check_in.append(launch_signals.checkin(*check, "in_progress"))

    try:
        result = backup_full.run(scheduled=True, on_start=on_start)
    except Exception:
        launch_signals.handled("backup_full_failed")
        launch_signals.checkin(*check, "error", time.monotonic() - started, check_in[0] if check_in else None)
        raise
    if check_in:
        launch_signals.checkin(*check, "ok", time.monotonic() - started, check_in[0])
    return result


def register(app):
    from app.ops import backup_full  # noqa: F401 — registration needs the producer importable
    # A cheap hourly due check supports arbitrary installation intervals.
    # Actual full runs default to every 1440 min, including after worker restarts.
    return queue_infra.register_periodic_sweep(
        app, queue=_QUEUE, task_name=_TASK_NAME, interval_min=60,
        body=_scheduled_run, exclusive=True)
