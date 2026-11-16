"""reminders.py — email reminder the day before an approved appointment.

Runs as a background thread in server.py (same pattern as watcher.py): a
daemon thread that every TURNOS_REMINDER_INTERVAL_MIN minutes (default 30,
ex TURNOS_RECORDATORIO_INTERVAL_MIN) sweeps approved requests whose
appointment is "tomorrow" (Argentina time) and sends
notifications.notify_reminder. All best-effort: the server NEVER
goes down over this, nor over a Mailgun failure nor a down DB.

`tick(now_utc=None)` is the PURE-testable part (takes the clock, doesn't
read threading.Event or sleep) — start() is just the wrapper that schedules it.
"""

import sys
import threading
from datetime import datetime, timezone, timedelta

from app.core import config
from app.core import tz as _tz
from app.core import launch_signals
from app.core import log_pii

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)

_WINDOW_FROM_H = 9   # don't send reminders before dawn
_WINDOW_TO_H = 21

_DEFAULT_INTERVAL_MIN = 30

# When this process tries again a reminder that did not go out (the product owner's hub review, 2026-11-10): the first retry
# waits `_RETRY_FIRST`, every next one twice the wait before, and a row whose next try would come after the
# window closes (`_WINDOW_TO_H` of the day being swept) is given up for the day. A provider down for an hour and
# a half no longer burns tomorrow's reminders (they go out at the next try), and a permanent refusal (a hard
# bounce, a 4xx) is tried about five times a day, not every sweep. Every failed try gives its claim back, the last
# one too: a reminder that never went out keeps its column empty, so the report does not count it as sent; this
# process remembers that it gave the row up and the sweep does not claim it again. Kept per (column, request) for
# the day being swept; a restart starts again from the first try.
_RETRY_FIRST = timedelta(minutes=30)
_EARLY = timedelta(minutes=5)  # a sweep that fires a little before the wait ends still tries
_tries_lock = threading.Lock()
_tries = {}  # (column, request id) -> (failed tries, earliest next try; None = given up for the day)
_tries_day = []

_stop = threading.Event()


def _log(msg):
    print(f"[recordatorios] {msg}", file=sys.stderr, flush=True)


def _env(key, default=""):
    return config.get(key, default)


def tick(now_utc=None, stop=None):
    """Runs one sweep. `now_utc` (aware datetime or None = datetime.now(UTC))
    is the clock to use — injectable to test without waiting on the real
    clock. Outside the time window (09:00–21:00 ART) it sends nothing: a
    reminder email at 3am is useless. Returns how many reminders went out
    (for logging/tests; one with nothing to send is not counted), never raises.

    `stop` (`core.worker_stop.check()`, passed only by the queue worker): asked
    before every claim. Once set the sweep returns; the rows it did not claim
    are still unclaimed, so the next sweep sends them (claim-before-send is
    the re-run contract) and a SIGTERM never waits for a whole day's mail."""
    from app.core import worker_stop
    now_utc = now_utc or datetime.now(timezone.utc)
    ahora_art = now_utc.astimezone(_ART)
    if not (_WINDOW_FROM_H <= ahora_art.hour < _WINDOW_TO_H):
        return 0

    manana_art = (ahora_art + timedelta(days=1)).date()

    try:
        from app.core import db_booking
        filas = db_booking.booking_requests_to_remind(manana_art)
    except Exception as e:  # noqa: BLE001 — the server never goes down over this
        _log(f"no se pudo leer booking_requests_to_remind: {log_pii.exc(e)}")
        return 0

    procesadas = 0
    for row in filas:
        if worker_stop.halted(stop):
            _log("parada pedida: el resto queda para el proximo barrido")
            break
        # Claim BEFORE sending (db_booking.claim_reminder: CAS on reminder_at IS
        # NULL) — two overlapping ticks (a slow sweep + the next interval
        # firing, or two processes) must not both send the same email. A
        # crash between the claim and the send loses THAT one reminder,
        # which is preferred over a duplicate (see design doc). The claim
        # itself IS the mark, so there's no separate mark_reminder call
        # after — that's the whole point of claim-CAS over mark-after.
        # A send that FAILS (notify_reminder answers mail.FAILED: not built,
        # or every channel tried refused it) gives the claim back, so the next
        # sweep tries again (the product owner's hub, 2026-11-10) — later and later, and
        # not after the window closes (_release, _waiting).
        if _waiting("reminder_at", row.get("id"), manana_art, ahora_art):
            continue
        try:
            ganado = db_booking.claim_reminder(row["id"])
        except Exception as e:  # noqa: BLE001
            _log(f"no se pudo reclamar recordatorio id={row.get('id')}: {log_pii.exc(e)}")
            continue
        if not ganado:
            continue  # otro tick ya lo reclamó
        from app.notifications import mail
        try:
            from app.notifications import send as notifications
            salio = notifications.notify_reminder(row)
        except Exception as e:  # noqa: BLE001
            _log(f"fallo notify_reminder id={row.get('id')}: {log_pii.exc(e)}")
            salio = mail.FAILED
        if salio == mail.FAILED:
            _release(row, day=manana_art, now=ahora_art)
            continue
        if salio == mail.SKIPPED:
            _nothing_to_send(row)
            continue
        procesadas += 1
    if procesadas:
        _log(f"{procesadas} recordatorio(s) procesados para {manana_art}")
    return procesadas


def _waiting(column, sid, day, now):
    """True while this row waits for its next try after a failed one, or after this process gave it up for the
    day: the sweep then does not claim it."""
    with _tries_lock:
        entry = _tries.get((column, sid)) if _tries_day == [day] else None
    if entry is None:
        return False
    _failed, not_before = entry
    return not_before is None or now < not_before - _EARLY


def _failed_try(column, sid, day, now):
    """Counts one failed try of this row at `now` and answers (how many there were, the earliest next try or None
    when the next one would come after the window closes). The count is for the day being swept (`day`): a new
    day starts from zero, so the map never outgrows one day's rows."""
    closes = now.replace(hour=_WINDOW_TO_H, minute=0, second=0, microsecond=0)
    with _tries_lock:
        if _tries_day != [day]:
            _tries.clear()
            _tries_day[:] = [day]
        failed = _tries.get((column, sid), (0, None))[0] + 1
        not_before = now + _RETRY_FIRST * (2 ** (failed - 1))
        if not_before > closes:
            not_before = None
        _tries[(column, sid)] = (failed, not_before)
        return failed, not_before


def _nothing_to_send(row):
    """The reminder had nothing to send (no address and no phone, or the channels off by configuration — the
    outbound kill switch included): not a failure — no signal, no retry — and not a reminder sent either (the product owner's
    hub, 2026-11-10). It keeps its claim, so no sweep picks it up again, in this process or after a restart, and
    is marked `reminder_skipped`, so the report does not count it in the «ahorro». A mark that cannot be written
    leaves it counted, as a crash between the claim and the send would."""
    from app.core import db_booking
    try:
        db_booking.mark_reminder_skipped(row["id"])
        _log(f"recordatorio id={row.get('id')} sin nada que mandar: no cuenta como enviado")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo marcar recordatorio id={row.get('id')} sin nada que mandar: {log_pii.exc(e)}")


def _release(row, *, day, now, interview=False):
    """The reminder (`interview`: the pre-consult interview invitation) did not go out: its claim goes back, the
    failure is a closed code (core/launch_signals.py), and the next try waits (`_failed_try`). When the next try
    would come after the window closes the row is given up for the day: the claim goes back all the same, so the
    column stays empty and the report does not count a reminder that never went out, and `_waiting` keeps the
    sweep from claiming it again. A release that cannot be written loses that one reminder, as a crash between
    the claim and the send always did."""
    from app.core import db_booking
    que = "recordatorio de entrevista" if interview else "recordatorio"
    column = "interview_reminder_at" if interview else "reminder_at"
    intentos, siguiente = _failed_try(column, row.get("id"), day, now)
    try:
        db_booking.release_reminder(row["id"], column)
        if siguiente is None:
            _log(f"{que} id={row.get('id')} no salio en {intentos} intentos: no se reintenta hoy")
        else:
            _log(f"{que} id={row.get('id')} no salio: se reintenta desde {siguiente:%H:%M}")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo liberar {que} id={row.get('id')}: {log_pii.exc(e)}")
    if interview:
        launch_signals.handled("interview_reminder_failed")
    else:
        launch_signals.handled("reminder_failed")


def tick_interview(now_utc=None, stop=None):
    """SECOND day-before sweep (deliverable 3, 2026-08-01): the Pre-Consult
    AI interview reminder, independent of tick()'s appointment reminder —
    its own column (`interview_reminder_at`, not `reminder_at`) so a
    hospital can run one without the other. Same time window, same clock
    injection for testability, same "always mark, whatever happened"
    contract: the notification only goes out through the named hook
    "interview_reminder" (app_modules.hooks.call — Tier 2 module-contract
    framework, 2026-08-05), which intake's modules/intake/app.py registers
    at startup. reminders.py doesn't import modules.intake or check
    app_modules.is_enabled("intake") itself anymore — hooks.call() already
    only invokes a hook whose owning module is enabled RIGHT NOW, so
    intake absent/disabled is a silent no-op the same way it always was.
    A row whose invitation FAILED (the hook answers False: not built, or the
    provider refused it) is given back for the next sweep, like tick()'s
    (the product owner's hub, 2026-11-10), on the same growing waits in this process;
    one with nothing to send stays marked, never re-scanned. Returns how many were processed,
    never raises. `stop`: as in tick() — a row not yet claimed is left for
    the next sweep."""
    from app.core import worker_stop
    now_utc = now_utc or datetime.now(timezone.utc)
    ahora_art = now_utc.astimezone(_ART)
    if not (_WINDOW_FROM_H <= ahora_art.hour < _WINDOW_TO_H):
        return 0

    manana_art = (ahora_art + timedelta(days=1)).date()

    try:
        from app.core import db_booking
        filas = db_booking.booking_requests_to_remind_interview(manana_art)
    except Exception as e:  # noqa: BLE001 — the server never goes down over this
        _log(f"no se pudo leer booking_requests_to_remind_interview: {log_pii.exc(e)}")
        return 0

    procesadas = 0
    for row in filas:
        if worker_stop.halted(stop):
            _log("parada pedida: el resto queda para el proximo barrido")
            break
        # Same claim-before-send contract as tick() above, on the sibling
        # column (interview_reminder_at / db_booking.claim_interview_reminder).
        if _waiting("interview_reminder_at", row.get("id"), manana_art, ahora_art):
            continue
        try:
            ganado = db_booking.claim_interview_reminder(row["id"])
        except Exception as e:  # noqa: BLE001
            _log(f"no se pudo reclamar entrevista_recordatorio id={row.get('id')}: {log_pii.exc(e)}")
            continue
        if not ganado:
            continue  # otro tick ya lo reclamó
        # A hook that answers False (the invitation was not built, or the
        # provider refused it) gives the claim back, as tick() does.
        try:
            from app.core import app_modules
            respuestas = app_modules.hooks.call("interview_reminder", row)
        except Exception as e:  # noqa: BLE001
            _log(f"fallo interview_reminder id={row.get('id')}: {log_pii.exc(e)}")
            respuestas = [False]
        if any(r is False for r in respuestas):
            _release(row, day=manana_art, now=ahora_art, interview=True)
            continue
        procesadas += 1
    if procesadas:
        _log(f"{procesadas} recordatorio(s) de entrevista procesados para {manana_art}")
    return procesadas


def _loop(interval_min):
    while not _stop.is_set():
        try:
            tick()
        except Exception as e:  # noqa: BLE001 — the thread must never die
            _log(f"tick() falló: {log_pii.exc(e)}")
            launch_signals.handled("reminders_sweep_failed")
        try:
            tick_interview()
        except Exception as e:  # noqa: BLE001 — the thread must never die
            _log(f"tick_interview() falló: {log_pii.exc(e)}")
            launch_signals.handled("reminders_sweep_failed")
        _stop.wait(interval_min * 60)


def _interval_min():
    try:
        # `legacy`: the pre-2026-07-17 name, so vault entries on the old env keep working without a redeploy.
        return int(config.get("TURNOS_REMINDER_INTERVAL_MIN", default="",
                              legacy="TURNOS_RECORDATORIO_INTERVAL_MIN") or _DEFAULT_INTERVAL_MIN)
    except ValueError:
        return _DEFAULT_INTERVAL_MIN


def start():
    """Launches the sweep, unless TURNOS_REMINDERS_OFF=1. Never raises: a
    failure here must not prevent the server from starting (same contract
    as watcher.start).

    Tier 4 worker-topology slice: TURNOS_QUEUE_WORKER=1
    means a SEPARATE worker process (worker.py) owns this sweep
    via notifications/reminders_queue.py — this web process starts nothing
    for it. Same mutual-exclusion-by-construction shape as
    `waitlist.start()`: one if/elif chain, exactly one mechanism reached,
    proven by tests/test_queue_worker.py::TestDelegationMatrix."""
    if config.get("TURNOS_REMINDERS_OFF", default="", legacy="TURNOS_RECORDATORIOS_OFF") == "1":
        _log("desactivado (TURNOS_REMINDERS_OFF=1)")
        return
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        _log(f"delegado al worker externo (TURNOS_QUEUE_WORKER={modo_worker}) — este proceso no barre")
        return
    interval_min = _interval_min()
    try:
        th = threading.Thread(target=_loop, args=(interval_min,),
                              name="recordatorios", daemon=True)
        th.start()
        _log(f"iniciado (cada {interval_min} min)")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo iniciar: {log_pii.exc(e)}")
