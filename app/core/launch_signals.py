"""core/launch_signals.py — what an installation whose logs and database the developers cannot read tells
its own error tracker: closed failure codes, hourly counters, Sentry Crons check-ins (TASK-4, 2026-10-30).

Why it exists. salud.pinamar.gob.ar runs behind the municipal gateway: there is no log to read and no
database to query, and many failures are swallowed on purpose (the booking persists, the mail is a best-effort
send, the HSI push is retried). Without a signal, a mail provider that stopped delivering on day one is found
by a patient. The installation already has a Sentry project (`core/error_tracking.py`); this module adds three
things to it, each behind one switch, and nothing else.

`TURNOS_SENTRY_SIGNALS` — a comma list of `failures`, `digest`, `crons` (or `all`). Empty or unset (the default)
is fully inert: no event, no check-in, no database write. Every feature also needs the DSN
(`error_tracking.enabled()`); with no DSN the module does nothing even when the list is set.

- `failures` — `handled(code)`: a failure the code swallows becomes ONE Sentry event whose message is the code,
  never a sentence, never an argument. The codes are the closed list `FAILURE_CODES`; any other string is
  dropped. At most one event per code per `_FAILURE_EVENT_EVERY_S` seconds per process (the counter below still
  counts every occurrence), so a provider outage costs a handful of events an hour, not one per patient.
- `digest` — `count(code)` adds one to the hourly counter table `launch_signal_counters` (Alembic 0036); the
  worker reads the last 24 hours once a day and sends ONE info event with numbers only
  (`ops/launch_digest.py`). The table holds an hour, a code from the closed lists and a number.
- `crons` — `monitored(slug, ...)` wraps a recurring job in a Sentry Crons check-in (started, then ok or
  error), so a worker or a backup that goes quiet becomes a "missed check-in" issue in the project. A
  check-in carries the monitor slug, a status, a duration and the schedule — no text.

What leaves, by construction: `error_tracking.scrub_event` keeps `tags.failure_code` only when its value is in
`FAILURE_CODES`, and `contexts.launch_digest` only as `DIGEST_KEYS -> int`; a check-in is rebuilt from its
allowlist. A caller cannot widen that by passing more: `handled` and `monitored` take no free text.

Every function here NEVER raises and never blocks the caller beyond one short database write (a counter write
waits at most `_COUNT_LOCK_TIMEOUT_MS` for the row lock and then gives the count up).
"""

import sys
import threading
import time

from app.core import settings

FEATURES = ("failures", "digest", "crons")

# Seconds between two Sentry events of the same failure code in one process.
_FAILURE_EVENT_EVERY_S = 900
# How long a counter write may wait for the row lock before it gives up (the count is then lost, never the request).
_COUNT_LOCK_TIMEOUT_MS = 500

# Events that are only counted: the normal traffic the digest reports next to the failures.
COUNT_CODES = (
    "staff_login_ok",
    "staff_login_failed",
    "mail_sent",
    "whatsapp_sent",
)

# Failures the code swallows on purpose. The message of a Sentry event IS one of these strings.
FAILURE_CODES = (
    "mail_failed",                    # the mail provider did not accept a message, or raised
    "whatsapp_failed",                # the WhatsApp transport did not accept a message, or raised
    "reminders_sweep_failed",         # a reminders tick raised (notifications/reminders*.py)
    "waitlist_sweep_failed",          # a waitlist tick raised (modules/turnos/waitlist*.py)
    "hsi_sync_reschedule_failed",     # the HSI journal chain could not schedule its next tick
    "backup_data_upload_failed",      # a file was not accepted by the remote store this sweep
    "backup_data_journal_failed",     # the backup journal could not be read or committed
    "backup_data_reschedule_failed",  # the data-backup chain could not schedule its next sweep
    "backup_full_failed",             # a full encrypted backup run failed
    "digest_failed",                  # the daily digest could not be built or sent (core/launch_signals' own)
    # A step after an action that had ALREADY persisted raised and was swallowed (the product owner's hub, 2026-11-10):
    "notify_received_failed",         # the «we got your request» notice could not be built or sent
    "notify_approved_failed",         # the «your appointment is confirmed» email or WhatsApp
    "notify_rejected_failed",         # the rejection notice (with its remedy)
    "notify_cancelled_failed",        # the «cancelled by the centre» notice
    "reminder_failed",                # a day-before reminder did not go out; its claim is given back for a retry
    "interview_reminder_failed",      # the day-before pre-consult interview invitation, the same
    "booking_referral_failed",        # a new request's referral file was not stored or not linked to it
    "booking_ai_save_failed",         # a new request's referral reading was not saved on it
    "booking_hold_link_failed",       # a new request's slot hold was not tied to it
    "db_unavailable",                 # a request got the 503 «database unavailable» (at most one per 15 min)
)

ALL_CODES = COUNT_CODES + FAILURE_CODES

# The numbers the daily digest may carry. `ops/launch_digest.py` fills them; `error_tracking.scrub_event`
# lets nothing else through. `handled_<code>` / `<count code>` counters come from ALL_CODES.
DIGEST_KEYS = (
    "requests_created", "requests_approved", "requests_rejected", "requests_cancelled", "requests_pending",
    "hsi_outbox_pending", "hsi_outbox_error", "hsi_outbox_conflict", "hsi_conflicts_open",
    "queue_todo", "queue_doing", "queue_failed_24h", "queue_oldest_todo_age_s",
    "backup_data_last_age_s", "backup_full_last_ok_age_s",
    "schema_revision", "sections_failed",
) + tuple(f"n_{code}" for code in ALL_CODES)

_state_lock = threading.Lock()
_last_event_at = {}


def _log(msg):
    print(f"[launch-signals] {msg}", file=sys.stderr, flush=True)


def features():
    """The features switched on by TURNOS_SENTRY_SIGNALS, as a frozenset of FEATURES. Read from the process
    environment like the DSN (a deployment decision, no database read on the hot paths that count)."""
    raw = settings.ProcessSettings.current().sentry_signals.lower()
    names = {part.strip() for part in raw.replace(";", ",").split(",") if part.strip()}
    if "all" in names:
        return frozenset(FEATURES)
    return frozenset(names & set(FEATURES))


def active(feature):
    """True when `feature` is listed AND a Sentry DSN is configured."""
    from app.core import error_tracking
    return feature in features() and error_tracking.enabled()


def any_active():
    return any(active(name) for name in FEATURES)


def init_sentry():
    """Starts the Sentry SDK in a process that has no web boot (the worker) when any feature is on, in
    `signals_only` mode: it sends what the functions here hand it and reports no error of its own.
    False, and nothing imported, when the list is empty or no DSN is set. Never raises."""
    if not any_active():
        return False
    try:
        from app.core import error_tracking
        return bool(error_tracking.init(signals_only=True))
    except Exception as exc:  # noqa: BLE001 — a broken DSN must not stop the worker
        _log(f"Sentry not started: {type(exc).__name__}")
        return False


# ---- hourly counters --------------------------------------------------------

def count(code):
    """Adds one to the hourly counter of `code` (any code of ALL_CODES) when `digest` is on. Never raises."""
    if code not in ALL_CODES or not active("digest"):
        return
    try:
        import sqlalchemy as sa
        from sqlalchemy.dialects import postgresql as pg
        from app.core import db
        from app.core import schema_model as sm
        from app.core import sqlcore
        t = sm.launch_signal_counters
        insert = pg.insert(t).values(bucket=sa.func.date_trunc("hour", sa.func.now()), code=code, n=1)
        with db._connect() as conn, conn.cursor() as cur:
            # The hourly row is shared by every request that counts: never wait behind another writer for it.
            sqlcore.run(cur, sa.select(sa.func.set_config(
                "lock_timeout", f"{_COUNT_LOCK_TIMEOUT_MS}ms", True)))   # True = this transaction only
            sqlcore.run(cur, insert.on_conflict_do_update(
                index_elements=[t.c.bucket, t.c.code], set_={"n": t.c.n + 1}))
    except Exception as exc:  # noqa: BLE001 — a counter never breaks the request that triggered it
        _log(f"counter {code} not written: {type(exc).__name__}")


# ---- closed failure codes ---------------------------------------------------

def handled(code):
    """A failure the code swallows: counts it and, when `failures` is on, sends one Sentry event whose message
    is `code` (rate-limited per code). A code outside FAILURE_CODES is ignored. Never raises."""
    if code not in FAILURE_CODES:
        return
    count(code)
    if not active("failures"):
        return
    now = time.monotonic()
    with _state_lock:
        last = _last_event_at.get(code)
        if last is not None and now - last < _FAILURE_EVENT_EVERY_S:
            return
        _last_event_at[code] = now
    try:
        import sentry_sdk
        sentry_sdk.capture_event({
            "level": "warning",
            "logger": "launch_signals",
            "message": code,
            "fingerprint": ["launch-signals", code],
            "tags": {"failure_code": code},
        })
    except Exception as exc:  # noqa: BLE001
        _log(f"event {code} not sent: {type(exc).__name__}")


# ---- the daily digest event -------------------------------------------------

def send_digest(numbers):
    """ONE info event carrying `numbers` (DIGEST_KEYS -> int). Anything else is dropped here and again by
    `error_tracking.scrub_event`. True when the SDK accepted the event. Never raises."""
    clean = {}
    for key in DIGEST_KEYS:
        value = numbers.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        clean[key] = value
    if not active("digest"):
        return False
    try:
        import sentry_sdk
        return bool(sentry_sdk.capture_event({
            "level": "info",
            "logger": "launch_signals",
            "message": "daily digest",
            "fingerprint": ["launch-signals", "daily-digest"],
            "contexts": {"launch_digest": clean},
        }))
    except Exception as exc:  # noqa: BLE001
        _log(f"digest not sent: {type(exc).__name__}")
        return False


# ---- Sentry Crons -----------------------------------------------------------

def _monitor_config(schedule, margin_min, max_runtime_min):
    """`schedule` is ("crontab", "*/5 * * * *") or ("interval", minutes)."""
    kind, value = schedule
    if kind == "crontab":
        sched = {"type": "crontab", "value": str(value)}
    else:
        sched = {"type": "interval", "value": int(value), "unit": "minute"}
    return {"schedule": sched, "timezone": "UTC", "checkin_margin": int(margin_min),
            "max_runtime": int(max_runtime_min), "failure_issue_threshold": 1, "recovery_threshold": 1}


def checkin(slug, schedule, margin_min, max_runtime_min, status, duration=None, check_in_id=None):
    """One Sentry Crons check-in (`status` in_progress / ok / error). Returns its id, or None when `crons` is
    off. For a job that only knows after the fact whether it ran (the hourly full-backup due check); a job that
    always runs uses `monitored`. Never raises."""
    if not active("crons"):
        return None
    try:
        from sentry_sdk.crons import capture_checkin
        return capture_checkin(monitor_slug=slug, check_in_id=check_in_id, status=status, duration=duration,
                               monitor_config=_monitor_config(schedule, margin_min, max_runtime_min))
    except Exception as exc:  # noqa: BLE001
        _log(f"check-in {slug} not sent: {type(exc).__name__}")
        return check_in_id


class monitored:
    """Context manager: a Sentry Crons check-in around a recurring job. `in_progress` on entry, `ok` on a clean
    exit, `error` when the block raises (the exception still propagates). `slug` and `schedule` are constants
    of the caller. Inert unless `crons` is on; never raises by itself.

        with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 5):
            ...
    """

    def __init__(self, slug, schedule, margin_min, max_runtime_min):
        self._args = (slug, schedule, margin_min, max_runtime_min)
        self._id = None
        self._started = 0.0

    def __enter__(self):
        self._started = time.monotonic()
        self._id = checkin(*self._args, "in_progress")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._id is not None:
            checkin(*self._args, "ok" if exc_type is None else "error",
                    duration=time.monotonic() - self._started, check_in_id=self._id)
        return False
