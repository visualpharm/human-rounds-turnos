"""Retry future EMR error/pending requests through the durable journal.

This Core-owned module provides a thread/manual fallback that scans future
requests with hsi_estado=error/pendiente every TURNOS_HSI_RESYNC_INTERVAL_MIN
minutes (default 10) and delegates each outbound attempt to the journal.

Approvals and their linked cancellations persist locally even when the EMR
is unavailable. This fallback adopts those rows into the journal so they get
the same crash safety, idempotency, and retry/backoff behavior as ordinary
journal replay.

Rules:
- Only future error/pending requests are eligible, including linked cancellations;
  an appointment in the past cannot be scheduled and must not retry forever.
- If an exact journal replay is not confirmed, stop the sweep (stop-first behavior)
  and let the durable retry/backoff policy decide when to try again.
- ``TURNOS_SKIP_HSI=1`` makes the entire sweep a no-op.
- ``tick(now_utc=None)`` is testable; ``start()`` only schedules the thread.
  This is best effort and cannot crash the server. ``TURNOS_HSI_RESYNC_OFF=1``
  disables it.

This is Core-owned infrastructure, not vendor-specific. Previously lived at
connectors/hsi/hsi_resync.py as part of the vendor package. Moved to
modules/turnos/ (2026-08-31, S1 of HSI/Core boundary repair) because it's
Core-owned fallback logic, not vendor-specific code.
"""

import os
import sys
import threading
from datetime import datetime, timezone
from app.core import log_pii

_DEFAULT_INTERVAL_MIN = 10

_stop = threading.Event()


def _log(msg):
    print(f"[hsi_resync] {msg}", file=sys.stderr, flush=True)


def tick(now_utc=None):
    """Un barrido. Devuelve cuántas solicitudes quedaron sincronizadas ('ok')
    en esta pasada; nunca levanta. `now_utc` inyectable para tests."""
    if os.environ.get("TURNOS_SKIP_HSI") == "1":
        return 0
    from app.modules.turnos import hsi_journal
    if not hsi_journal._emr_selected():
        return 0
    now_utc = now_utc or datetime.now(timezone.utc)

    try:
        from app.core import db_booking
        from app.core import db_hsi
        filas = db_booking.booking_requests_hsi_pending(desde=now_utc)
    except Exception as e:  # noqa: BLE001 — el server nunca se cae por esto
        _log(f"no se pudo leer booking_requests_hsi_pending: {log_pii.exc(e)}")
        return 0

    sincronizadas = 0
    for row in filas:
        if row.get("estado") == "cancelada" and not row.get("hsi_appointment_id"):
            # No operation can be started from this snapshot. An earlier
            # create may still be uncertain in the outbox, whose fenced
            # replay owns its eventual adoption or definitive skip. Do not
            # let this non-attempt starve later actionable rows.
            continue
        try:
            # Narrower than hsi_journal.tick(): the fallback must not import
            # inbound facts or run the resolver while HSI_SYNC_OFF=1.
            result = hsi_journal.enqueue_and_replay_row(row)
        except db_hsi.StaleHsiReassignTarget:
            # The scan raced a local move/cancellation. This snapshot made no
            # durable attempt, so later actionable rows remain eligible.
            continue
        except Exception as e:  # noqa: BLE001
            _log(f"journal HSI failed id={row.get('id')}: {log_pii.exc(e)}")
            break
        if (result or {}).get("state") != "confirmed":
            # Preserve stop-first behavior. The exact journal item owns
            # retry/backoff and crash recovery; do not hammer later rows.
            break
        sincronizadas += 1
        _log(f"solicitud {row['id']} confirmada por journal HSI")
    return sincronizadas


def _loop(interval_s):
    while not _stop.wait(interval_s):
        from app.modules.turnos import hsi_journal
        if not hsi_journal._emr_selected():
            return
        tick()


def _off():
    """THE one resolution of TURNOS_HSI_RESYNC_OFF — start() reads it
    here, never a second parse. NOT a worker-registry field: hsi_resync
    deliberately has no queue-registry entry (see start()'s own
    docstring) — this flag only ever gates the thread."""
    return os.environ.get("TURNOS_HSI_RESYNC_OFF") == "1"


def _interval_min():
    """THE one cadence truth: TURNOS_HSI_RESYNC_INTERVAL_MIN (default
    _DEFAULT_INTERVAL_MIN), fed to the thread loop's own wait. Thread-only
    — hsi_resync has no queue-registry entry (see start()'s own
    docstring), so there is no cron derivation reading this."""
    try:
        return float(os.environ.get("TURNOS_HSI_RESYNC_INTERVAL_MIN")
                     or _DEFAULT_INTERVAL_MIN)
    except ValueError:
        return _DEFAULT_INTERVAL_MIN


def start():
    """Arranca el hilo de fondo (idempotente vía _stop; server.py lo llama una
    vez en main, sólo cuando TURNOS_HSI_SYNC_OFF=1). TURNOS_HSI_RESYNC_OFF=1
    => no arranca.

    Tier 4 daemon batch 4 (2026-08-09, narrowed on review): this legacy
    one-way resync stays ON-THREAD in every topology, TURNOS_QUEUE_WORKER
    included — deliberately NOT given a queue-registry entry alongside
    hsi_sync. It remains an operator-enabled manual fallback
    (TURNOS_HSI_SYNC_OFF=1), not a second queue migration. Each outward
    effect delegates to hsi_sync's existing crash-safe journal instead of the
    historical direct-push path; the thread still owns cadence, flags, and
    stop-first behavior.

    EMR boundary (2026-09-01): same gate as the journal thread — no
    selected EMR connector (hsi_journal._emr_selected()) means no fallback
    thread either. The rows it would adopt stay pending, honestly and
    reversibly, instead of being swept into journal items that can only
    degrade as 'skip'."""
    if _off():
        _log("apagado por TURNOS_HSI_RESYNC_OFF")
        return None
    from app.modules.turnos import hsi_journal
    if not hsi_journal._emr_selected():
        _log("ningún conector EMR seleccionado — fallback inactivo")
        return None
    interval_min = _interval_min()
    t = threading.Thread(target=_loop, args=(interval_min * 60,),
                         name="hsi-resync", daemon=True)
    t.start()
    return t
