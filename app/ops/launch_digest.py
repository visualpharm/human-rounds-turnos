"""ops/launch_digest.py — the daily digest of numbers an installation sends to its own error tracker
(TASK-4, 2026-10-30; switch and contract in `core/launch_signals.py`).

ONE info event a day, after 12:00 UTC (09:00 in Buenos Aires), carrying only integers under the closed keys
of `launch_signals.DIGEST_KEYS`:

- requests of the last 24 hours — created, approved, rejected, cancelled — from `appointment_events`, and the
  pending ones right now (the reasons are NOT counted: a rejection reason can be free text);
- the HSI outbox by state (pending / error / conflict) and the open sync conflicts;
- the worker queue — jobs waiting, running, the age in seconds of the oldest job that is due, and jobs that
  failed in the last 24 hours;
- the age in seconds of the last file the data backup stored and of the last verified full backup;
- the schema revision as a number (`0036_...` -> 36);
- the hourly counters of the last 24 hours (`n_<code>`: staff sign-ins ok / failed, mail and WhatsApp sent and
  failed, every swallowed-failure code), written by `launch_signals.count` / `handled`.

Each section is read on its own connection and skipped when its table or column is missing (an installation
without a worker has no queue tables): a section that fails costs only its own keys. Nothing here selects a
name, DNI, phone, address, message or free text — a count, an age or a revision is all that leaves the
database, and `error_tracking.scrub_event` lets nothing else through.

Who runs it. The worker, from its maintenance task `worker.maintenance.launch_signals` (every 5 minutes: the
check-in of the worker monitor plus this module's `tick()`); an installation without a worker runs `start()`'s
thread in the web process instead (every 10 minutes: the check-in `turnos-web-heartbeat` plus `tick()`). Once a day, whoever gets there first: `tick()` claims the day with a row in
`launch_signal_counters` (`digest_sent`), so two processes never send two digests.
"""

import sys
import threading
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from app.core import db
from app.core import launch_signals
from app.core import log_pii
from app.core import queue_infra
from app.core import schema_model as sm
from app.core import sqlcore

WEB_MONITOR = "turnos-web-heartbeat"   # the Sentry Crons slug of the web thread (no worker)
_NEVER = -1   # the value of a backup age when there is no such backup yet (a missing key means "not measured")
_SEND_AFTER_HOUR_UTC = 12
_CLAIM_CODE = "digest_sent"
_KEEP_COUNTERS = "14 days"
_THREAD_EVERY_S = 600

_stop = threading.Event()
_WINDOW = sa.literal_column("interval '24 hours'")


def _log(msg):
    print(f"[launch-digest] {msg}", file=sys.stderr, flush=True)


def _section(numbers, name, fn):
    """Runs one reading on its own connection; a failure leaves its keys out and counts in `sections_failed`."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            numbers.update(fn(cur))
    except Exception as exc:  # noqa: BLE001 — a missing table or a down database costs only this section
        _log(f"{name}: not read ({type(exc).__name__})")
        numbers["sections_failed"] = numbers.get("sections_failed", 0) + 1


def _requests(cur):
    e = sm.appointment_events
    rows = sqlcore.run(cur, sa.select(e.c.kind, sa.func.count().label("n")).where(
        e.c.created > sa.func.now() - _WINDOW,
        e.c.kind.in_(["creada", "aprobada", "rechazada", "cancelada"])).group_by(e.c.kind)).fetchall()
    by_kind = {r["kind"]: int(r["n"]) for r in rows}
    b = sm.booking_requests
    pending = sqlcore.run(cur, sa.select(sa.func.count().label("n")).where(
        b.c.status == "pendiente")).fetchone()["n"]
    return {"requests_created": by_kind.get("creada", 0), "requests_approved": by_kind.get("aprobada", 0),
            "requests_rejected": by_kind.get("rechazada", 0), "requests_cancelled": by_kind.get("cancelada", 0),
            "requests_pending": int(pending)}


def _hsi(cur):
    o = sm.hsi_outbox
    rows = sqlcore.run(cur, sa.select(o.c.state, sa.func.count().label("n")).where(
        o.c.state.in_(["pending", "error", "conflict"])).group_by(o.c.state)).fetchall()
    by_state = {r["state"]: int(r["n"]) for r in rows}
    c = sm.hsi_sync_conflicts
    open_conflicts = sqlcore.run(cur, sa.select(sa.func.count().label("n")).where(
        c.c.resolved_at.is_(None))).fetchone()["n"]
    return {"hsi_outbox_pending": by_state.get("pending", 0), "hsi_outbox_error": by_state.get("error", 0),
            "hsi_outbox_conflict": by_state.get("conflict", 0), "hsi_conflicts_open": int(open_conflicts)}


def _queue(cur):
    j, ev = sm.procrastinate_jobs, sm.procrastinate_events
    rows = sqlcore.run(cur, sa.select(j.c.status, sa.func.count().label("n")).where(
        j.c.status.in_(["todo", "doing"])).group_by(j.c.status)).fetchall()
    by_status = {r["status"]: int(r["n"]) for r in rows}
    oldest = sqlcore.run(cur, sa.select(
        sa.extract("epoch", sa.func.now() - sa.func.min(j.c.scheduled_at)).label("age")).where(
        j.c.status == "todo", j.c.scheduled_at <= sa.func.now())).fetchone()["age"]
    failed = sqlcore.run(cur, sa.select(sa.func.count().label("n")).where(
        ev.c.type == "failed", ev.c.at > sa.func.now() - _WINDOW)).fetchone()["n"]
    return {"queue_todo": by_status.get("todo", 0), "queue_doing": by_status.get("doing", 0),
            "queue_oldest_todo_age_s": max(int(oldest), 0) if oldest is not None else 0,
            "queue_failed_24h": int(failed)}


def _backups(cur):
    out = {}
    j, r = sm.backup_journal, sm.backup_runs
    last = sqlcore.run(cur, sa.select(
        sa.extract("epoch", sa.func.now() - sa.func.max(j.c.uploaded_at)).label("age"))).fetchone()["age"]
    out["backup_data_last_age_s"] = max(int(last), 0) if last is not None else _NEVER
    full = sqlcore.run(cur, sa.select(
        sa.extract("epoch", sa.func.now() - sa.func.max(r.c.finished_at)).label("age")).where(
        r.c.uploaded.is_(True), r.c.integrity_verified.is_(True))).fetchone()["age"]
    out["backup_full_last_ok_age_s"] = max(int(full), 0) if full is not None else _NEVER
    return out


def _schema(cur):
    v = sm.alembic_version
    version = sqlcore.run(cur, sa.select(v.c.version_num)).fetchone()["version_num"]
    digits = str(version).split("_", 1)[0]
    return {"schema_revision": int(digits)} if digits.isdigit() else {}


def _counters(cur):
    t = sm.launch_signal_counters
    # Whole hours: from the start of the hour that began 24 hours ago (between 24 and 25 hours of counts).
    rows = sqlcore.run(cur, sa.select(t.c.code, sa.func.sum(t.c.n).label("n")).where(
        t.c.bucket >= sa.func.date_trunc("hour", sa.func.now() - _WINDOW),
        t.c.code.in_(launch_signals.ALL_CODES)).group_by(t.c.code)).fetchall()
    found = {r["code"]: int(r["n"]) for r in rows}
    return {f"n_{code}": found.get(code, 0) for code in launch_signals.ALL_CODES}


def collect():
    """The digest's numbers, DIGEST_KEYS -> int (a key whose section could not be read is absent)."""
    numbers = {"sections_failed": 0}
    for name, fn in (("requests", _requests), ("hsi", _hsi), ("queue", _queue), ("backups", _backups),
                     ("schema", _schema), ("counters", _counters)):
        _section(numbers, name, fn)
    return numbers


def _claim_today():
    """True for exactly one caller per UTC day: inserts today's `digest_sent` row, False when it exists."""
    t = sm.launch_signal_counters
    insert = pg.insert(t).values(bucket=sa.func.date_trunc("day", sa.func.now()), code=_CLAIM_CODE, n=1)
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, insert.on_conflict_do_nothing(
            index_elements=[t.c.bucket, t.c.code])).rowcount == 1


def _release_today():
    """Gives today's claim back (a digest that crashed after claiming the day must not lose the day)."""
    t = sm.launch_signal_counters
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(
            t.c.bucket == sa.func.date_trunc("day", sa.func.now()), t.c.code == _CLAIM_CODE))


def _purge_old_counters():
    t = sm.launch_signal_counters
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(
            t.c.bucket < sa.func.now() - sa.literal_column(f"interval '{_KEEP_COUNTERS}'")))


def tick(now=None):
    """Sends today's digest when it is due and nobody has. Cheap when it is not (an hour comparison, then one
    insert attempt). Never raises."""
    if not launch_signals.active("digest"):
        return {"skipped": "off"}
    now = now or datetime.now(timezone.utc)
    if now.hour < _SEND_AFTER_HOUR_UTC:
        return {"skipped": "not_due"}
    try:
        if not _claim_today():
            return {"skipped": "already_sent"}
    except Exception as exc:  # noqa: BLE001 — the digest never breaks the sweep it rides on
        _log(f"digest not claimed: {log_pii.exc(exc)}")
        launch_signals.handled("digest_failed")
        return {"error": type(exc).__name__}
    # The day is ours. Until the event is handed to the SDK a failure gives it back (the next tick tries again);
    # after that the day stays claimed whatever happens to the housekeeping. A process killed in between loses
    # that day's digest — a window of milliseconds, and the next day is unaffected.
    try:
        numbers = collect()
        sent = launch_signals.send_digest(numbers)
        if not sent:
            raise RuntimeError("the error tracker did not accept the digest")
    except Exception as exc:  # noqa: BLE001
        _log(f"digest not sent: {log_pii.exc(exc)}")
        try:
            _release_today()
        except Exception:  # noqa: BLE001
            pass
        launch_signals.handled("digest_failed")
        return {"error": type(exc).__name__}
    try:
        _purge_old_counters()
    except Exception as exc:  # noqa: BLE001 — only housekeeping: the digest is out
        _log(f"old counters not purged: {type(exc).__name__}")
    return {"sent": True, "keys": len(numbers)}


def _loop():
    while not _stop.is_set():
        # The heartbeat of the web process itself (`crons`): with no worker this thread is the only periodic
        # thing the installation has, so a process that dies or hangs misses the check-in.
        with launch_signals.monitored(WEB_MONITOR, ("interval", _THREAD_EVERY_S // 60),
                                      _THREAD_EVERY_S // 60, 5):
            tick()
        _stop.wait(_THREAD_EVERY_S)


def start():
    """The web process's thread twin, for an installation WITHOUT a queue worker (`TURNOS_QUEUE_WORKER` unset).
    With a worker the maintenance task owns the digest and the heartbeat, and this starts nothing. Inert unless
    `digest` or `crons` is on. Never raises."""
    if not (launch_signals.active("digest") or launch_signals.active("crons")):
        return False
    if queue_infra.worker_mode():
        _log(f"delegated to the worker (TURNOS_QUEUE_WORKER={queue_infra.worker_mode()})")
        return False
    try:
        threading.Thread(target=_loop, name="launch-digest", daemon=True).start()
        _log("started (once a day, after 12:00 UTC)")
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"not started: {type(exc).__name__}")
        return False
