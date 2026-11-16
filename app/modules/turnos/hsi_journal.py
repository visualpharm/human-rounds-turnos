"""Durable, bidirectional EMR journal for Turnos.

This Core-owned module manages the persistent journal that syncs appointments
between Turnos and the EMR (HSI or any other EmrSyncConnector). The journal
makes retry/restart behaviour observable: pending/sending/confirmed/error/conflict
states are persisted in Postgres, not hidden in a background thread.

The journal owns:
- Outbox replay: durable outbound appointment intents (create, cancel, reassign, attendance)
- Inbox reconciliation: importing EMR observations and mirroring them locally
- Idempotency: every intent carries a stable key, so crashes cannot duplicate
- Checkpoints: linked edits re-read EMR state before mutation, detecting drift
- Conflict visibility: operator-visible conflicts instead of silent overwrites

This is Core-owned infrastructure, not vendor-specific. It delegates to the
selected EmrSyncConnector via connectors.adapter_for() and works with any
connector that implements the EmrSync capability.

Previously lived at connectors/hsi/hsi_sync.py as part of the vendor package.
Moved to modules/turnos/ (2026-08-31, S1 of HSI/Core boundary repair) because
the journal tables (hsi_outbox, hsi_inbox, hsi_mirror_*, hsi_conflicts) are
owned by the turnos module. The vendor code (hsi_client.py, hsi_resolver.py)
remains in connectors/hsi/.
"""

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

from app import connectors
from app.modules.turnos import emr_observations
from app.core import clock, ids, log_pii, tz

_DEFAULT_INTERVAL_MIN = 5
_stop = __import__("threading").Event()


def _log(message):
    print(f"[hsi_sync] {message}", file=sys.stderr, flush=True)


def _jsonable(value):
    if isinstance(value, datetime):
        # Postgres may return the same timestamptz in the host timezone while
        # the browser supplied ART.  Normalize before hashing so reconnect
        # backfill cannot mint a second idempotency key for the same instant.
        art = timezone(timedelta(hours=-3))
        value = value.replace(tzinfo=art) if value.tzinfo is None else value.astimezone(art)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _key(prefix, payload):
    encoded = json.dumps(payload, default=_jsonable, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(encoded.encode()).hexdigest()[:24]}"


def appointment_payload(row, turno_dt=None):
    """The narrow, PHI-minimal payload required to recreate an approved slot."""
    when = turno_dt or row.get("turno_dt")
    nac = row.get("nacimiento")
    try:
        from app.modules.turnos import booking_requests
        specialty = booking_requests._service_name(row.get("servicio_id"))
    except Exception:
        specialty = row.get("servicio_id") or ""
    return {
        "solicitud_id": int(row["id"]), "dni": str(row.get("dni") or ""),
        "nombre": row.get("nombre") or "", "apellido": row.get("apellido") or "",
        "nacimiento": _jsonable(nac) if nac else None, "sexo": row.get("sexo") or "X",
        "servicio_id": row.get("servicio_id") or "", "especialidad": specialty,
        "when": _jsonable(when),
        "medico": row.get("medico_asignado"), "sobreturno": bool(row.get("sobreturno")),
    }


def enqueue_appointment(row, turno_dt=None):
    """Queue an unconfirmed approved appointment exactly once for replay.

    A request has at most ONE create in flight. An unsettled earlier create
    may already exist in the EMR (its response was lost) and its key hashes
    the slot it was queued for, so a create for a moved slot would open a
    second clinical appointment: the earlier one is returned instead, and
    `_replay_one` lands it on the request's current slot."""
    from app.core import db_hsi
    outstanding = db_hsi.hsi_outbox_outstanding("appointment", str(int(row["id"])), "create")
    if outstanding:
        return outstanding
    payload = appointment_payload(row, turno_dt)
    key = _key("appointment-create", payload)
    return db_hsi.hsi_outbox_enqueue("appointment", str(payload["solicitud_id"]),
                                 "create", payload, key)


def _linked_payload(row, operation, when=None):
    from app.core import db_hsi
    appointment_id = row.get("hsi_appointment_id")
    return {"solicitud_id": int(row["id"]), "appointment_id": str(appointment_id or ""),
            "checkpoint": db_hsi.hsi_checkpoint_get("appointment", appointment_id) if appointment_id else None,
            "when": _jsonable(when) if when else None, "operation": operation}


def enqueue_cancel(row):
    from app.core import db_hsi
    payload = _linked_payload(row, "cancel")
    return db_hsi.hsi_outbox_enqueue("appointment", str(row["id"]), "cancel", payload,
                                 _key("appointment-cancel", payload))


def enqueue_reassign(row, when):
    from app.core import db_hsi
    def build_intent(current, checkpoint, generation):
        payload = {
            "solicitud_id": int(current["id"]),
            "appointment_id": str(current["hsi_appointment_id"]),
            "checkpoint": checkpoint,
            "when": _jsonable(current["turno_dt"]),
            "operation": "reassign",
            "move_generation": generation,
        }
        return payload, _reassign_key(payload)

    return db_hsi.hsi_outbox_enqueue_reassign(row["id"], when, build_intent)


def _reassign_key(payload):
    # A refreshed checkpoint can be a legitimate corrective attempt for the
    # same local move. The event distinguishes repeated A -> B generations.
    return _key("appointment-reassign", payload)


def enqueue_attendance(row, asistencia):
    """Journal an operator attendance mark for HSI's verified state endpoint."""
    from app.core import db_hsi
    payload = _linked_payload(row, "attendance")
    payload["asistencia"] = str(asistencia or "")
    return db_hsi.hsi_outbox_enqueue("appointment", str(row["id"]), "attendance", payload,
                                 _key("appointment-attendance", payload))


def intent_for_row(row):
    """Choose the one durable operation represented by a local fallback row.

    This is intentionally narrower than ``tick()``: it never imports remote
    facts or invokes the resolver. Both journal backfill and the legacy
    thread/manual fallback share this mapping so a linked cancel or reassign
    cannot drift back into an accidental create.
    """
    if row.get("hsi_appointment_id"):
        if row.get("estado") == "cancelada":
            return enqueue_cancel(row)
        return enqueue_reassign(row, row.get("turno_dt"))
    # Una solicitud cancelada que nunca llegó al EMR no debe crearse allá.
    if row.get("estado") == "cancelada":
        return None
    return enqueue_appointment(row)


def replay_unlinked_move(row):
    """Sync a patient move of an approved request the EMR has not linked yet.

    The caller already moved the local time (``hsi_estado`` pending). The
    request's one create — the approval's, still unsettled, or a new one at
    the moved slot when none exists — is retried now; landing on a moved row
    queues the reassign onto the patient's slot, replayed here too. An outage
    leaves both durable for the worker."""
    from app.core import db_hsi
    intent = enqueue_appointment(row)
    if intent and intent.get("state") in ("pending", "error") and db_hsi.hsi_outbox_reset(intent["id"]):
        replay_item(intent["id"])
    follow = db_hsi.hsi_outbox_outstanding("appointment", str(int(row["id"])), "reassign")
    if follow and follow.get("state") == "pending":
        replay_item(follow["id"])


def enqueue_and_replay_row(row):
    """Durably enqueue and replay exactly one local fallback row.

    ``hsi_resync`` uses this instead of ``tick()`` to retain its thread-owned
    cadence and stop-first behavior while reusing the journal's idempotency,
    lease, remote confirmation, and local acknowledgement path.
    """
    intent = intent_for_row(row)
    return replay_item(intent["id"]) if intent else None


def _ack_followup(item, row, operation, checkpoint):
    """Build the next intent from the booking row locked by db's ack."""
    payload = {
        "solicitud_id": int(row["id"]),
        "appointment_id": str(row["hsi_appointment_id"]),
        "checkpoint": checkpoint,
        "when": _jsonable(row.get("turno_dt")) if operation == "reassign" else None,
        "operation": operation,
    }
    if operation == "reassign":
        payload["move_generation"] = row["_move_generation"]
        # Preserve the settled remote predecessor as corrective lineage. A
        # historical confirmed same-generation row may have been overtaken by
        # this late acknowledgement and must not swallow its correction.
        payload["predecessor_intent_id"] = item["id"]
        return operation, payload, _reassign_key(payload)
    # Corrective cancellation still follows the settled remote mutation.
    payload["predecessor_intent_id"] = item["id"]
    return operation, payload, _key("appointment-" + operation, payload)


def backfill_pending(now_utc=None):
    """Adopt approvals created before the journal (or during a DB blip).

    This makes the migration safe: an old ``hsi_estado=error|pendiente`` row
    is deterministically represented in the outbox on every startup/tick.
    The outbox unique idempotency key means repeated scans are harmless.
    """
    # Selection is read at every execution boundary, not just thread
    # startup.  An operator can deselect an EMR while this process is still
    # running; leave the existing pending rows intact for a later explicit
    # re-selection rather than converting them into outbox work or `skip`.
    if not _emr_selected():
        return 0
    from app.core import db_booking
    from app.core import db_hsi
    rows = db_booking.booking_requests_hsi_pending(desde=now_utc)
    adopted = 0
    for row in rows:
        if row.get("estado") == "cancelada" and not row.get("hsi_appointment_id"):
            # A create may have reached the EMR before its response was lost.
            # Only the fenced outbox replay can prove absence or adopt the
            # remote appointment and enqueue its corrective cancellation.
            # This scan is a stale snapshot, so it must never change status.
            continue
        try:
            intent_for_row(row)
        except db_hsi.StaleHsiReassignTarget:
            continue
        adopted += 1
    return adopted


def _backoff(attempts):
    return min(3600, 15 * (2 ** max(0, min(int(attempts), 8))))


def _permanent_hsi_failure(exc):
    """Classify deterministic routing/capacity failures as conflicts.

    Connectivity, authentication and server failures remain retryable.  A
    missing diary/professional or an exhausted immutable diary window cannot
    heal by replaying the same intent, so it needs operator review.
    """
    message = str(exc).lower()
    return any(marker in message for marker in (
        "no diaries exist in hsi",
        "no hsi diary matches professional",
        "no diary matches",
        "no exact free slot",
        "no free slot",
        "appointment create returned unexpected id",
    ))


def _test_hold_after_remote_accept():
    """Test seam ONLY, double-gated behind TURNOS_TEST_SEAMS=1 AND
    TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S (same discipline as
    core.queue_infra.test_hold — either var alone is a no-op, so a real
    deployment can never be paused by one stray env line). Holds
    _replay_one genuinely INSIDE the remote-accepted/local-uncommitted
    crash window this module's rerun contract documents: after HSI has
    already returned 'ok' for a create/reassign, before the local
    outbox/mirror write lands. A crash-recovery test SIGKILLs the worker
    while paused here to prove the outbox row's OWN lease (not just the
    queue job) survives and safely reclaims — added for batch 4's review
    (2026-08-09), narrowed scope: proves the interruption window this
    replatform's own rerun contract claims is safe, on real Postgres."""
    from app.core import queue_infra
    if not queue_infra.test_seams_enabled():
        return
    hold = os.environ.get("TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S", "0") or "0"
    try:
        hold = float(hold)
    except ValueError:
        return
    if hold > 0:
        import time
        time.sleep(hold)


def _reclaimed_result(adapter, item, idempotency_key):
    """Ask a probe-capable connector for a settled result BEFORE re-sending.

    A lease that is not this row's first (``attempts >= 2`` after the claim)
    means an earlier ``execute_intent`` with the same stable key ran and its
    local acknowledgement never landed — the crash window between the
    remote's acceptance and the local commit, or a transport failure after
    acceptance.  Connectors that cannot probe (the ``EmrSyncConnector`` ABC
    default returns None — every legacy in-process connector among them)
    keep the pre-probe behavior exactly: the same stable key is re-executed
    and the connector's own dedup de-duplicates.  A probe that answers
    ``found: False``, or itself fails, also falls through — the intent
    envelope's idempotency key makes a keyed re-send safe by contract.
    """
    if not idempotency_key:
        return None
    try:
        if int(item.get("attempts") or 0) < 2:
            return None
        probed = adapter.probe_intent(idempotency_key)
    except Exception as exc:  # a probe outage must never block the replay itself
        _log(
            f"intent probe failed for outbox item {item.get('id')}: "
            f"{log_pii.exc(exc)}; falling back to a keyed resend"
        )
        return None
    if not isinstance(probed, dict) or probed.get("found") is not True:
        return None
    result = probed.get("result")
    if isinstance(result, dict) and result.get("estado") == "ok":
        return result
    _log(
        f"intent probe returned no adoptable result for outbox item "
        f"{item.get('id')}; falling back to a keyed resend"
    )
    return None


def _cancelled_reassign_result(adapter, item, idempotency_key):
    """Resolve an attempted move without ever executing it after cancellation.

    A negative key probe is definitive. When key probing is unsupported, an
    optional read-only observation may establish the exact move; absence from
    that read is never a definitive negative. Failed or malformed answers
    leave the attempted intent retryable without sending the move again.
    """
    from app.core import db_values
    if int(item.get("attempts") or 0) < 2:
        return None
    probed = None
    if idempotency_key:
        try:
            probed = adapter.probe_intent(idempotency_key)
        except Exception as exc:  # noqa: BLE001 — remote outcome remains unknown
            _log(f"cancelled reassign probe failed for outbox item {item['id']}: {type(exc).__name__}")
            return False
    payload = item.get("payload") or {}
    if probed is None:
        try:
            result = adapter.observe_intent("reassign", payload)
        except Exception as exc:  # noqa: BLE001 — remote read remains uncertain
            _log(f"cancelled reassign observation failed for outbox item {item['id']}: {type(exc).__name__}")
            return False
    else:
        if isinstance(probed, dict) and probed.get("found") is False:
            return None
        result = probed.get("result") if isinstance(probed, dict) and probed.get("found") is True else None
    checkpoint = result.get("checkpoint") if isinstance(result, dict) else None
    appointment_id = str(payload.get("appointment_id") or "")
    prior = payload.get("checkpoint") or {}
    try:
        target = db_values._coerce_dt(payload.get("when"))
        observed = db_values._coerce_dt(checkpoint.get("scheduled_at")) if isinstance(checkpoint, dict) else None
        actual = db_values._coerce_dt(result.get("actual_turno_dt")) if isinstance(result, dict) else None
        if (isinstance(result, dict) and result.get("estado") == "ok"
                and appointment_id and str(result.get("appointment_id") or "") == appointment_id
                and isinstance(checkpoint, dict) and isinstance(prior, dict)
                and str(prior.get("appointment_id") or "") == appointment_id
                and str(checkpoint.get("appointment_id") or "") == appointment_id
                and checkpoint.get("state") == "scheduled"
                and checkpoint.get("schedule_id") and checkpoint.get("patient_id")
                and str(prior.get("schedule_id") or "") == str(checkpoint["schedule_id"])
                and str(prior.get("patient_id") or "") == str(checkpoint["patient_id"])
                and (not result.get("patient_id") or str(result["patient_id"]) == str(checkpoint["patient_id"]))
                and (not result.get("schedule_id") or str(result["schedule_id"]) == str(checkpoint["schedule_id"]))
                and target is not None and observed == target and actual == target):
            return result
    except (TypeError, ValueError, OverflowError):
        pass
    return False


def _local_row(payload):
    """The local request behind an intent, or None when it cannot be read.

    Reassign replay treats None as retryable uncertainty before remote I/O.
    """
    from app.core import db_booking
    try:
        sid = (payload or {}).get("solicitud_id")
        return db_booking.get_booking_request(sid) if sid is not None else None
    except Exception:  # noqa: BLE001
        return None


def _settle(item, state, **kwargs):
    """A replay outcome can only be written by the owner of this claim."""
    from app.core import db_hsi
    try:
        return db_hsi.hsi_outbox_settle(item, state, **kwargs)
    except db_hsi.StaleHsiLease:
        return db_hsi.hsi_outbox_get(item["id"])


def _replay_one(item):
    from app.core import db
    from app.core import db_hsi
    payload = item["payload"]
    try:
        connector_id, _source = connectors.resolve(connectors.EMR_SYNC)
        adapter = connectors.adapter_for(connectors.EMR_SYNC)
        if adapter is None:
            return _degrade_unconfigured(db, item, "no EMR connector is configured")
        if (getattr(adapter, "degrades_when_unconfigured", lambda: False)()
                and not connectors.is_configured(connector_id)):
            return _degrade_unconfigured(db, item, "EMR bridge is not configured")
        operation = item.get("operation")
        idempotency_key = item.get("idempotency_key") or ""
        # Crash-window reclaim first: a retried lease may already hold a
        # settled remote result, in which case the journal adopts it instead
        # of re-sending the mutation.  Connectors without probe support (the
        # ABC default) take the plain re-execute path, unchanged.
        # A request cancelled locally before its EMR create ever ran must not
        # be created remotely (TLC: NoCreateForCancelled).
        local = _local_row(payload)
        if operation == "reassign" and local is None:
            # An unreadable or missing booking could be cancelled. Neither
            # probing nor a send can safely settle it without the
            # current local status and linked identity.
            return _settle(item, "error",
                           error="reassign outcome unknown: local booking unavailable",
                           delay_seconds=_backoff(item.get("attempts") or 1))
        if (operation == "create" and local and local.get("estado") == "cancelada"
                and not local.get("hsi_appointment_id")):
            # ...unless an earlier lease of THIS create already landed in the
            # EMR and crashed before the local write (TLC 2026-09-24,
            # AppointmentHsiFixed: NoLiveRemoteForCancelled): skipping would
            # orphan a live EMR appointment. Adopt it and journal its cancel.
            # On a later lease, absence of an adoptable result is ambiguous:
            # the first create may have landed before its local write. Only a
            # definitive negative probe proves it is safe to skip. Never
            # re-send a create for a request the patient has cancelled.
            adopted = None
            if int(item.get("attempts") or 0) >= 2:
                try:
                    probed = adapter.probe_intent(idempotency_key) if idempotency_key else None
                except Exception as exc:  # noqa: BLE001 — keep an uncertain intent retryable
                    _log(f"cancelled create probe failed for outbox item {item['id']}: {type(exc).__name__}")
                    probed = None
                if isinstance(probed, dict) and probed.get("found") is True:
                    result = probed.get("result")
                    if isinstance(result, dict) and result.get("estado") == "ok":
                        adopted = result
                elif not (isinstance(probed, dict) and probed.get("found") is False):
                    return _settle(item, "error",
                                   error="cancelled create outcome unknown: probe unavailable",
                                   delay_seconds=_backoff(item.get("attempts") or 1))
                if isinstance(probed, dict) and probed.get("found") is True and not adopted:
                    return _settle(item, "error",
                                   error="cancelled create outcome unknown: probe result invalid",
                                   delay_seconds=_backoff(item.get("attempts") or 1))
            if adopted and adopted.get("appointment_id") is not None:
                for key in ("patient_id", "appointment_id"):
                    if adopted.get(key) is not None:
                        adopted[key] = str(adopted[key])
                return db_hsi.hsi_outbox_ack_appointment(
                    item, adopted,
                    lambda row, operation, checkpoint: _ack_followup(
                        item, row, operation, checkpoint))
            if adopted:
                return _settle(item, "error",
                               error="cancelled create outcome unknown: appointment id missing",
                               delay_seconds=_backoff(item.get("attempts") or 1))
            return _settle(item, "confirmed",
                           result={"estado": "skip", "reason": "cancelled locally before EMR create"},
                           booking_status="skip")
        if operation == "reassign" and local and local.get("estado") == "cancelada":
            # First lease could not have sent the move before this branch.
            # Later leases may have moved it remotely before crashing. Adopt
            # only the exact settled move, under the normal fenced ack, so
            # the current cancelled booking produces corrective cancel C2.
            if int(item.get("attempts") or 0) >= 2:
                adopted = _cancelled_reassign_result(adapter, item, idempotency_key)
                if adopted is False:
                    return _settle(item, "error",
                                   error="cancelled reassign outcome unknown: probe unavailable or invalid",
                                   delay_seconds=_backoff(item.get("attempts") or 1))
                if adopted is not None:
                    for key in ("patient_id", "appointment_id"):
                        if adopted.get(key) is not None:
                            adopted[key] = str(adopted[key])
                    return db_hsi.hsi_outbox_ack_appointment(
                        item, adopted,
                        lambda row, next_operation, checkpoint: _ack_followup(
                            item, row, next_operation, checkpoint))
            return _settle(item, "confirmed", result={
                "estado": "skip", "reason": "cancelled locally before EMR reassign"})
        # A queued create whose request is ALREADY linked (a waitlist offer
        # pushed its own create meanwhile, or an earlier replay linked it)
        # would open a second EMR appointment and drag the local time back
        # to the stale one (TLC 2026-09-24, AppointmentHsiFixed:
        # AtMostOneLiveRemote / TerminalNeverMoved).
        if local and operation == "create" and local.get("hsi_appointment_id"):
            return _settle(item, "confirmed", result={
                "estado": "skip", "reason": "already linked in the EMR"})
        result = _reclaimed_result(adapter, item, idempotency_key)
        if result is None:
            result = adapter.execute_intent(operation, payload, idempotency_key)
        if not isinstance(result, dict) or result.get("estado") != "ok":
            raise RuntimeError((result or {}).get("estado") or "HSI did not confirm appointment")
        _test_hold_after_remote_accept()
        # HSI returns numeric identifiers; Sana columns are text.  Normalize
        # before the DB write so Postgres never has to COALESCE smallint/text.
        for key in ("patient_id", "appointment_id"):
            if result.get(key) is not None:
                result[key] = str(result[key])
        # Persist HSI-confirmed identifiers before acknowledging the journal.
        # If we crash after remote success, the selected connector receives
        # the stable idempotency key again rather than a freshly minted one.
        if operation in ("create", "reassign"):
            return db_hsi.hsi_outbox_ack_appointment(
                item, result,
                lambda row, next_operation, checkpoint: _ack_followup(
                    item, row, next_operation, checkpoint))
        return _settle(item, "confirmed", result=result,
                       booking_status=result.get("estado"))
    except db_hsi.StaleHsiLease:
        # A newer owner may be sending this same idempotent intent. The old
        # worker must not turn its live lease back into an error.
        return db_hsi.hsi_outbox_get(item["id"])
    except Exception as exc:  # EMR/network failures are expected retry inputs
        if isinstance(exc, connectors.EmrConflict):
            return _conflict(db, item, exc,
                             getattr(exc, "remote", None) or payload.get("checkpoint"))
        if isinstance(exc, connectors.EmrRejected):
            return _conflict(db, item, exc, getattr(exc, "remote", None))
        if _permanent_hsi_failure(exc):
            return _conflict(db, item, exc, None)
        return _settle(item, "error", error=repr(exc),
                       delay_seconds=_backoff(item.get("attempts") or 1))


def _degrade_unconfigured(db, item, reason):
    """Degrade only an unattempted, provably local-only create.

    A reclaimed lease may have reached the remote system before its local
    acknowledgement, and a linked operation already refers to remote state.
    Neither can be confirmed as skipped while the connector cannot probe.
    Keep those intents claimable after reconfiguration without changing the
    booking or its remote checkpoint.
    """
    payload = item.get("payload") or {}
    local = _local_row(payload)
    first_local_create = (item.get("operation") == "create"
                          and int(item.get("attempts") or 0) == 1
                          and local is not None
                          and local.get("estado") is not None
                          and not local.get("hsi_appointment_id")
                          and not payload.get("appointment_id"))
    if not first_local_create:
        return _settle(item, "error",
                       error=f"{reason}; remote outcome requires retry after configuration",
                       delay_seconds=_backoff(item.get("attempts") or 1))
    return _settle(item, "confirmed", result={"estado": "skip", "reason": reason},
                   booking_status="skip", first_local_create_status=local["estado"])


def _conflict(db, item, exc, remote):
    """Persist a durable conflict AND make it visible on the request itself.

    The admin chip reads ``solicitudes.hsi_estado``; a permanent conflict used
    to leave the row saying "HSI pendiente" forever while the outbox quietly
    held the truth.  ``error`` is the honest visible state for an appointment
    that never reached HSI; the reason stays in ``last_error``/conflicts.
    Attendance/cancel conflicts keep the request's state: their appointment
    did reach HSI."""
    return _settle(item, "conflict", error=str(exc), remote_payload=remote,
                   booking_status="error" if item.get("operation") in ("create", "reassign") else None)


def motivo_operador(outcome):
    """The Spanish reason an operator sees when a manual retry fails.

    A generic "HSI no confirmó el turno" hid the real cause; the difference
    matters because a missing diary is fixed inside HSI, not by clicking
    Reintentar again."""
    outcome = outcome or {}
    error = str(outcome.get("last_error") or "")
    low = error.lower()
    if any(m in low for m in ("unreachable", "connection refused", "timed out",
                              "connectionerror", "timeouterror")):
        return ("HSI no responde en este momento. El sistema lo reintenta solo "
                "cada unos minutos; también podés reintentar más tarde.")
    if "no hsi diary matches professional" in low:
        return ("HSI no tiene agenda para ese profesional. Cargala en HSI o "
                "aprobá el turno con otro profesional.")
    if "no diary matches" in low or "no diaries exist" in low:
        return ("HSI no tiene una agenda cargada para esta especialidad. "
                "Hay que crearla en HSI antes de reintentar.")
    if "no exact free slot" in low or "no free slot" in low:
        return ("La agenda de HSI no tiene libre ese horario. Reprogramá el "
                "turno o abrí el horario en HSI.")
    if "/api/auth" in low or "401" in low or "403" in low:
        return "HSI rechazó las credenciales de Sana. Revisá HSI_EMAIL/HSI_PASS."
    if error:
        return "HSI no aceptó este turno: " + error[:200]
    return "HSI no confirmó el turno."


def replay_item(id_):
    """Lease and replay exactly one durable intent for the synchronous path."""
    if not _emr_selected():
        return None
    from app.core import db_hsi
    item = db_hsi.hsi_outbox_claim_id(id_)
    return _replay_one(item) if item else None


def replay(limit=20):
    """Replay due outbox records once; returns confirmed count."""
    if os.environ.get("TURNOS_SKIP_HSI") == "1" or not _emr_selected():
        return 0
    from app.core import db_hsi
    confirmed = 0
    for item in db_hsi.hsi_outbox_claim(limit):
        result = _replay_one(item)
        if result and result.get("state") == "confirmed":
            confirmed += 1
    return confirmed


def _same_local_time(local, remote):
    if not local or not remote:
        return False
    try:
        lhs = local.astimezone(timezone(timedelta(hours=-3))) if local.tzinfo else local.replace(tzinfo=timezone(timedelta(hours=-3)))
        rhs = datetime.fromisoformat(remote)
        return lhs.replace(second=0, microsecond=0) == rhs.replace(second=0, microsecond=0)
    except Exception:
        return False


def _remote_instant(raw):
    """An EMR `scheduled_at` in the installation timezone, or None when it does not parse or
    carries no offset (the connector's normalization always writes one; a naive time is not
    guessed at)."""
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(tz.tzinfo()) if parsed.tzinfo else None


def _linkable_instant(appt):
    """When a live EMR appointment happens (installation timezone), or None when it can never
    claim a request: cancelled or blocked (the desk reloads a cancelled turno as a new
    appointment, the dead one must not claim the request), or a time that does not parse. The one
    check `_relink_unchanged` and `_link_unlinked_request` share."""
    if appt.get("state") in ("cancelled", "blocked"):
        return None
    return _remote_instant(appt.get("scheduled_at"))


def _minute_key(when):
    return int(when.timestamp() // 60)


class _RelinkPool:
    """The minutes at which an approved request still links no HSI appointment, read ONCE per
    journal tick (lazily, on the first unchanged appointment). Most unchanged appointments the
    journal replays every tick are the hospital's own: a minute with no unlinked request cannot
    link anything, so those cost no query at all instead of three on separate connections under
    the journal's exclusive lock. A request approved during the tick is seen on the next one."""

    def __init__(self):
        self._minutes = None

    def has_candidate(self, when):
        if self._minutes is None:
            from app.core import db_hsi
            since = datetime.combine(clock.today(), datetime.min.time(), tzinfo=tz.tzinfo())
            self._minutes = {_minute_key(m) for m in db_hsi.hsi_unlinked_approved_minutes(since) if m}
        return _minute_key(when) in self._minutes


def _relink_unchanged(appt, patient, source_key, pool=None):
    """An EMR appointment observed again unchanged: a request may have become linkable since it
    was first seen (the patient poll learned the DNI, the desk approved the request or assigned its
    doctor, a conflict's extra request was rejected). Retried only for an unlinked appointment from
    today on whose minute has an unlinked approved request (`_RelinkPool`), so the cost stays
    bounded by the requests, not by the hospital's agendas. Returns the linked wire row, "conflict"
    when the retry opened a conflict that was not already open, or None (still unchanged)."""
    from app.core import db_hsi
    when = _linkable_instant(appt)
    if when is None or when.date() < clock.today():
        return None
    if not (pool or _RelinkPool()).has_candidate(when):
        return None
    if db_hsi.hsi_booking_requests_by_appointment(appt.get("id")):
        return None
    return _link_unlinked_request(appt, patient, source_key, retry=True)


def _link_unlinked_request(appt, patient, source_key, retry=False):
    """An EMR appointment no local request links: is it a request the desk loaded into HSI by hand
    (the Chrome extension knows no HSI id, so the request stays unlinked)? Exactly ONE approved,
    unlinked request of the same patient (the observation's DNI, or the patient mirror's when the
    observation carries none) and the same minute, narrowed to the same catalog doctor (the
    diary's professional, resolved by name like `hsi_busy` — only once a candidate exists, so a
    foreign appointment never pays the catalog scan), is linked to it once
    (`db_hsi.hsi_link_request`, an appointment event) and returned, so
    the caller reconciles it like any linked request (attendance, cancellation, move). Several
    candidates, or a candidate the journal is still sending, is a visible conflict (returns
    "conflict"); anything else is no link (None). A cancelled or blocked appointment never links:
    the desk reloads a turno cancelled in HSI as a new appointment at the same place, and the dead
    one must not claim the request. Nothing is written to HSI. A conflict already open for a
    candidate with the same reason is not opened again; on a `retry` (`_relink_unchanged`) with
    nothing new to open the answer is None and the inbox row is left as it is."""
    from app.core import db_hsi
    from app.modules.turnos import doctor_appointments
    when = _linkable_instant(appt)
    if when is None:
        return None
    dni = ids.dni(patient.get("document_number")) or ids.dni(
        db_hsi.hsi_mirror_patient_dni(appt.get("patient_id")))
    if not dni:
        return None
    candidates = db_hsi.hsi_unlinked_approved_at(dni, when)
    if not candidates:
        return None
    medico_id = doctor_appointments.resolve_doctor_id(db_hsi.hsi_diary_professional(appt.get("schedule_id")))
    if not medico_id:
        return None
    candidates = [c for c in candidates
                  if str(c.get("medico_asignado_id") or "") == str(medico_id)]
    if not candidates:
        return None
    minute = when.replace(second=0, microsecond=0)
    reason = None
    if len(candidates) > 1:
        reason = "ambiguous_local_requests_for_hsi_appointment"
    else:
        row, refused = db_hsi.hsi_link_request(candidates[0]["id"], appt.get("id"), appt.get("patient_id"),
                                               dni=dni, medico_id=medico_id, minute=minute)
        if row is not None:
            return row
        if refused != "local_intent_unsettled":
            return None
        reason = "hsi_link_while_local_intent_unsettled"
    fresh = [c for c in candidates if not db_hsi.hsi_conflict_open("appointment", c["id"], reason)]
    if retry and not fresh:
        return None
    for candidate in fresh:
        db_hsi.hsi_conflict("appointment", candidate["id"], "inbound", reason, candidate, appt)
    db_hsi.hsi_inbox_finish(source_key, "conflict", {"reason": reason})
    return "conflict"


def _reconcile_appointment(appt, source_key, source_new=True, relink_pool=None):
    """Mirror a normalized EMR appointment and update the local request safely. `relink_pool`
    is the tick's shared `_RelinkPool`."""
    from app.core import db_booking
    from app.core import db_hsi
    appt_id = appt.get("id")
    patient = appt.get("patient") or {"id": appt.get("patient_id")}
    patient_id = appt.get("patient_id")
    schedule_id = appt.get("schedule_id")
    mirror_changed = db_hsi.hsi_mirror_appointment(appt_id, schedule_id, patient_id, appt)
    if patient_id is not None:
        db_hsi.hsi_mirror_patient(patient_id, patient.get("document_number"), patient)
    if not source_new and not mirror_changed:
        linked = _relink_unchanged(appt, patient, source_key, relink_pool)
        if linked is None:
            return "unchanged"
        if linked == "conflict":
            return "conflict"
        locals_ = [linked]
    else:
        locals_ = db_hsi.hsi_booking_requests_by_appointment(appt_id)
    if not locals_:
        linked = _link_unlinked_request(appt, patient, source_key)
        if linked == "conflict":
            return "conflict"
        if linked is None:
            db_hsi.hsi_inbox_finish(source_key, "confirmed")
            return "imported"
        locals_ = [linked]
    if len(locals_) > 1:
        reason = "duplicate_local_hsi_appointment_id"
        for duplicate in locals_:
            db_hsi.hsi_conflict("appointment", duplicate["id"], "inbound", reason, duplicate, appt)
        db_hsi.hsi_inbox_finish(source_key, "conflict", {"reason": reason})
        return "conflict"
    local = locals_[0]
    remote = appt.get("scheduled_at")
    # Cancellation/blocked states must be handled by a human; neither side
    # silently overwrites a clinical decision.
    if appt.get("state") == "cancelled" and local.get("estado") == "cancelada":
        db_hsi.hsi_inbox_finish(source_key, "confirmed")
        return "confirmed"
    if appt.get("state") in ("cancelled", "blocked") or not remote:
        reason = "remote_appointment_cancelled_or_blocked"
        db_hsi.hsi_conflict("appointment", local["id"], "inbound", reason, local, appt)
        db_hsi.hsi_inbox_finish(source_key, "conflict", {"reason": reason})
        return "conflict"
    # The EMR owns its clinical attendance state.  Connectors map vendor
    # lifecycle codes to the stable Core vocabulary before reaching here.
    # A local attendance intent that has not settled is a true concurrent edit,
    # so preserve both facts as an observable conflict instead of overwriting
    # the operator's mark merely because the appointment time itself matches.
    attendance_state = {"no_show": "ausente", "attended": "presente"}.get(appt.get("state"))
    if attendance_state:
        try:
            in_flight = db_hsi.hsi_outbox_inflight("appointment", local["id"])
        except Exception:
            in_flight = True
        if in_flight:
            reason = "concurrent_local_and_hsi_attendance_change"
            db_hsi.hsi_conflict("appointment", local["id"], "inbound", reason, local, appt)
            db_hsi.hsi_inbox_finish(source_key, "conflict", {"reason": reason})
            return "conflict"
        if local.get("asistencia") != attendance_state:
            db_booking.mark_attendance(local["id"], attendance_state, "HSI", origen="hsi", exigir_no_futuro=False)
    if _same_local_time(local.get("turno_dt"), remote):
        db_hsi.hsi_inbox_finish(source_key, "confirmed")
        return "confirmed"
    # No in-flight local intent exists for this request, so the authoritative
    # HSI change can update Sana.  If an intent is in flight, preserve both
    # facts as a conflict for an operator.
    try:
        in_flight = db_hsi.hsi_outbox_inflight("appointment", local["id"])
    except Exception:
        in_flight = True
    if in_flight:
        reason = "concurrent_local_and_hsi_appointment_change"
        db_hsi.hsi_conflict("appointment", local["id"], "inbound", reason, local, appt)
        db_hsi.hsi_inbox_finish(source_key, "conflict", {"reason": reason})
        return "conflict"
    db_booking.reassign_appointment(local["id"], remote,
                        {"estado": "ok", "patient_id": str(patient_id) if patient_id is not None else None,
                         "appointment_id": str(appt_id)},
                        actor="hsi:inbound")
    db_hsi.hsi_inbox_finish(source_key, "confirmed")
    return "updated"


def reconcile(client=None, from_date=None, to_date=None):
    """Import normalized EMR observations into Core mirrors.

    ``client`` is a test seam for any generic ``EmrSyncConnector``.  It must
    return the normalized observation contract; Core deliberately never
    accepts a vendor-shaped fallback.
    """
    if os.environ.get("TURNOS_SKIP_HSI") == "1" or not _emr_selected():
        return {"schedules": 0, "appointments": 0, "patients": 0, "conflicts": 0}
    from app.core import db_hsi
    client = client or connectors.adapter_for(connectors.EMR_SYNC)
    if client is None:
        return {"schedules": 0, "appointments": 0, "patients": 0, "conflicts": 0}
    today = clock.today()  # the installation's day, not the process's
    try:
        lookback_days = max(1, min(365, int(os.environ.get("TURNOS_HSI_LOOKBACK_DAYS", "90"))))
    except (TypeError, ValueError):
        lookback_days = 90
    # Attendance and late cancellations are recorded after the visit. A
    # future-only poll silently missed them, so retain a bounded clinical
    # lookback while keeping the diary scan predictable at city scale.
    from_date = from_date or (today - timedelta(days=lookback_days)).isoformat()
    to_date = to_date or (today + timedelta(days=90)).isoformat()
    counts = {"schedules": 0, "appointments": 0, "patients": 0, "conflicts": 0}
    known_patient_ids = db_hsi.hsi_known_patient_ids()
    observations = emr_observations.bundle(
        client.pull_observations(from_date, to_date, known_patient_ids))
    for schedule in observations.get("schedules") or []:
        source = _key("schedule", schedule)
        source_new = db_hsi.hsi_inbox_record(source, "schedule", schedule["id"], schedule)
        mirror_changed = db_hsi.hsi_mirror_schedule(schedule["id"], schedule.get("professional_id"), schedule)
        if source_new or mirror_changed:
            db_hsi.hsi_inbox_finish(source, "confirmed")
            counts["schedules"] += 1
    relink_pool = _RelinkPool()
    for appt in observations.get("appointments") or []:
        source = _key("appointment", appt)
        source_new = db_hsi.hsi_inbox_record(source, "appointment", appt.get("id"), appt)
        outcome = _reconcile_appointment(appt, source, source_new=bool(source_new),
                                         relink_pool=relink_pool)
        if source_new or outcome != "unchanged":
            counts["appointments"] += 1
            if outcome == "conflict":
                counts["conflicts"] += 1
    # HSI denies global patient enumeration.  Poll only previously mirrored
    # IDs; patients with no appointment and never seen by Sana remain an
    # explicit permission boundary until the reference-deployment counterpart enables an HSI feed/audit role.
    for observed_patient in observations.get("patients") or []:
        try:
            patient_id = observed_patient.get("id")
            if db_hsi.hsi_mirror_patient(patient_id, observed_patient.get("document_number"), observed_patient):
                counts["patients"] += 1
        except Exception as exc:  # one inaccessible historic ID must not stop appointments
            _log(f"patient {patient_id} poll failed: {log_pii.exc(exc)}")
    return counts


def tick(stop=None):
    """One background pass: replay, pull HSI's authoritative view, then let
    the resolver auto-close every conflict a human does not actually need.
    Every phase is independently guarded (batch-4 fix, 2026-08-09): `replay()`
    used to be the one unguarded call — a DB outage inside it raised straight
    out of `tick()`, breaking this function's own documented "never raises"
    contract and, on the THREAD topology, killing the hsi-sync daemon thread
    permanently until process restart (`_loop()` does not catch either). On
    the queue topology the same gap would only fail that one job row
    (procrastinate isolates task exceptions), but the thread exposure was
    real and is closed the same way the other two phases already were.

    `stop` (`core.worker_stop.check()`, passed only by the queue worker) is
    asked between the phases: once set, the phases not yet started are left
    for the next tick, whose work is all durable (pending outbox rows, the
    remote view re-read from scratch), so a SIGTERM waits for at most the
    phase in flight, never the whole pass."""
    from app.core import worker_stop
    # Do this before the first DB read. Runtime deselection must stop an
    # already-running loop or queued task without backfilling, replaying,
    # reconciling, or touching its durable pending rows.
    if _off() or not _emr_selected():
        return {"backfilled": 0, "replayed": 0, "resolved": {},
                "schedules": 0, "appointments": 0, "patients": 0,
                "conflicts": 0}
    adopted, replayed, resolved = 0, 0, {}
    imported = {"schedules": 0, "appointments": 0, "conflicts": 0}

    def _halted():
        if worker_stop.halted(stop):
            _log("stop requested: the phases left wait for the next tick")
            return True
        return False

    if _halted():
        return {"backfilled": adopted, "replayed": replayed, "resolved": resolved, **imported}
    try:
        adopted = backfill_pending()
    except Exception as exc:
        _log(f"outbox backfill failed: {log_pii.exc(exc)}")
        adopted = 0
    if _halted():
        return {"backfilled": adopted, "replayed": replayed, "resolved": resolved, **imported}
    try:
        replayed = replay()
    except Exception as exc:
        _log(f"outbox replay failed: {log_pii.exc(exc)}")
        replayed = 0
    if _halted():
        return {"backfilled": adopted, "replayed": replayed, "resolved": resolved, **imported}
    try:
        imported = reconcile()
    except Exception as exc:  # keep the web server alive and leave durable work visible
        _log(f"reconcile failed: {log_pii.exc(exc)}")
        imported = {"schedules": 0, "appointments": 0, "conflicts": 0}
    if _halted():
        return {"backfilled": adopted, "replayed": replayed, "resolved": resolved, **imported}
    try:
        # The resolver is vendor behavior living behind the generic EMR
        # connector; its own pass keeps local-only conflict hygiene active
        # for every connector and gates vendor routing on
        # supports_auto_resolution().  No adapter → nothing to resolve.
        adapter = connectors.adapter_for(connectors.EMR_SYNC)
        resolved = adapter.run_conflict_resolution() if adapter else {}
    except Exception as exc:
        _log(f"resolver failed: {log_pii.exc(exc)}")
        resolved = {}
    _log(f"backfill={adopted} replay={replayed} imported={imported} resolved={resolved}")
    return {"backfilled": adopted, "replayed": replayed, "resolved": resolved, **imported}


def _loop(interval_s):
    while True:
        if _off() or not _emr_selected():
            return
        tick()
        if _stop.wait(interval_s):
            return


def _off():
    """THE one resolution of TURNOS_HSI_SYNC_OFF — start() and the worker
    registry (worker._hsi_sync_enabled) both read it here, never a second
    parse. Plain-environ on purpose, matching the flag's pre-existing
    resolution (server.py's legacy-fallback gate already read the same
    var directly)."""
    return os.environ.get("TURNOS_HSI_SYNC_OFF") == "1"


def _emr_selected():
    """THE one resolution of 'does this installation route an EMR
    connector' — the registry's own answer, never a local parse of
    CONNECTOR_EMR_SYNC. `emr_sync` is an explicit-only capability
    (connectors._EXPLICIT_ONLY_CAPABILITIES), so a connector_id here
    always means an operator selected one (`hsi`, `emr_bridge`, ...);
    None/off/invalid all mean 'nothing is routed' and Core starts no EMR
    lifecycle at all. start(), the worker registry
    (worker._hsi_sync_enabled) and the fallback thread
    (hsi_fallback.start) read it here so the three can never diverge."""
    return bool(connectors.resolve(connectors.EMR_SYNC)[0])


def _interval_min():
    """THE one cadence truth: TURNOS_HSI_SYNC_INTERVAL_MIN (default
    _DEFAULT_INTERVAL_MIN), fed to both the thread loop's own wait and
    the queue topology's self-rescheduling (hsi_sync_queue.py's
    register()/rekick() — NOT core.queue_infra.cron_for_interval; this
    task is a plain @app.task, not a periodic cron, so any positive
    value — fractional included — is valid, only <= 0 is rejected)."""
    try:
        return float(os.environ.get("TURNOS_HSI_SYNC_INTERVAL_MIN") or _DEFAULT_INTERVAL_MIN)
    except ValueError:
        return _DEFAULT_INTERVAL_MIN


def start():
    """Launches the daemon thread. Returns None when it does not apply.

    Tier 4 daemon batch 4 (durable HSI sync journal): with
    TURNOS_QUEUE_WORKER=1 the SEPARATE worker
    process owns the sweep via connectors/hsi/hsi_sync_queue.py and no
    thread starts here — exactly one mechanism per topology
    (TestDelegationMatrix). The OFF flag wins over delegation, checked
    first, same order as every other queue-migrated daemon
    (translate_warmup.start(), chart_summary.start()).

    EMR boundary (2026-09-01): no selected EMR connector means no journal
    thread either — an install that never picked a connector starts zero
    EMR lifecycle (_emr_selected(); the tick has nothing to replay against
    and no adapter to reconcile through)."""
    if _off():
        _log("disabled by TURNOS_HSI_SYNC_OFF")
        return None
    if not _emr_selected():
        _log("no EMR connector is selected — journal idle")
        return None
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        _log(f"sync delegated to the worker (TURNOS_QUEUE_WORKER={modo_worker}) — no thread here")
        return None
    interval = _interval_min()
    thread = __import__("threading").Thread(target=_loop, args=(interval * 60,),
                                               name="hsi-sync", daemon=True)
    thread.start()
    return thread
