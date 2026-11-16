"""booking_emr.py — the panel's «HSI» cores: sync status, conflict resolver run and apply, and the retry of
one request.

Split out of booking_requests.py (TASK-60, 2026-11-06). Core holds no vendor import: everything goes through
`connectors.adapter_for("emr_sync")`, and an approval reaches the EMR only through the durable journal
(hsi_journal). `_emr_sync_enabled` stays in booking_requests.py and is called as `bkr.<name>` at call time.
The direct push (`_push_hsi`) and its helpers had no caller left and were deleted on 2026-11-06.
"""

from datetime import datetime, timezone

from app.modules.turnos import booking_requests_store as store
from app import connectors
from app.core import i18n
from app.modules.turnos import booking_requests as bkr


def _hsi_sync_status_core():
    """GET /api/admin/hsi-sync — sync status + panel (auth already checked
    by the caller). No args, `(status, payload)` out."""
    hsi_status = store.hsi_sync_status()
    payload = {"counts": hsi_status["counts"],
               "outbox": [bkr._jsonable(r) for r in hsi_status["outbox"]],
               "conflicts": [bkr._jsonable(r) for r in hsi_status["conflicts"]],
               "hsi_activo": bkr._emr_sync_enabled()}
    # The admin's «HSI» section: % synced, open clusters with a proposal,
    # recent automatic resolutions.  The resolver is vendor behavior: reach
    # it through the generic EMR connector (None adapter → degraded panel).
    try:
        adapter = connectors.adapter_for(connectors.EMR_SYNC)
        payload["panel"] = adapter.conflict_panel() if adapter else None
    except Exception:  # noqa: BLE001 — the raw status is still useful
        payload["panel"] = None
    return 200, payload


def _hsi_resolver_run_core():
    """«Recheck»: one resolver pass on operator demand — pure domain
    function (see `_approve_core`'s docstring for the campaign context).
    No args (the legacy caller's `identity` was already unused by this
    business logic, same as before extraction). Also runs with
    TURNOS_SKIP_HSI=1 — the strategies that don't talk to HSI (duplicates,
    stale, repeated) still make sense there. `(status, payload)` out."""
    adapter = connectors.adapter_for(connectors.EMR_SYNC)
    resumen = adapter.run_conflict_resolution() if adapter else {}
    try:
        panel = adapter.conflict_panel() if adapter else None
    except Exception:  # noqa: BLE001
        panel = None
    return 200, {"ok": True, "resumen": resumen, "panel": panel}


def _hsi_conflicts_apply_core(identity, body):
    """The resolution buttons in the «HSI» section: move to the first free
    slot / overbook / reject, applied to the flagged conflicts. Pure domain
    function: typed `identity`/already-parsed `body` in, `(status, payload)`
    out — body parsing happens BEFORE this in the router (nothing else
    ran between the auth gate and the parse in the original, so a
    pre-parsed dict is order-safe here, unlike `_assign_core`/
    `_mark_attendance_core`)."""
    accion = str(body.get("accion") or "")
    ids = [int(i) for i in (body.get("conflictos") or []) if str(i).isdigit()]
    if not ids:
        return 400, {"error": "sin_conflictos"}
    adapter = connectors.adapter_for(connectors.EMR_SYNC)
    if adapter is None:
        return 409, {"error": "resolucion_no_disponible", "resultados": []}
    out = adapter.apply_conflict_resolution(
        accion, ids, resolved_by=identity.get("usuario") or "operador")
    if not out.get("ok"):
        return 409, {"error": out.get("error") or "no_se_pudo",
                     "resultados": out.get("resultados") or []}
    try:
        out["panel"] = adapter.conflict_panel()
    except Exception:  # noqa: BLE001
        out["panel"] = None
    return 200, out


def _retry_hsi_core(sid):
    """Pure domain function behind `_retry_hsi` (see that function's own
    docstring for the full behavior — unchanged verbatim here; the legacy
    caller's `identity` was already unused by this business logic, same as
    before extraction). `sid` in, `(status, payload)` out."""
    if not bkr._emr_sync_enabled():
        return 409, {"error": "hsi_desactivado"}
    row = store.get_booking_request(sid)
    if not row:
        return 404, {"error": i18n.t_or("admin.srv.error.solicitud_no_encontrada", "solicitud no encontrada", i18n.active())}
    turno_dt = row.get("turno_dt")
    if row.get("estado") != "aprobada" or not turno_dt:
        return 400, {"error": i18n.t_or("admin.srv.retryhsi.no_aprobada", "solicitud no aprobada", i18n.active())}
    if row.get("hsi_estado") == "ok":
        return 200, {"ok": True, "solicitud": bkr._admin_row(row)}
    aware = turno_dt if turno_dt.tzinfo else turno_dt.replace(tzinfo=bkr._ART)
    if aware < datetime.now(timezone.utc):
        # HSI schedules appointments: one already in the past can't be
        # pushed, retrying can only fail forever.
        return 400, {"error": "turno_pasado"}
    outcome = None
    try:
        from app.modules.turnos import hsi_journal
        # An appointment already linked to HSI is NEVER re-created (it
        # would duplicate the clinical booking): it's rescheduled on top of
        # the existing appointment.
        if row.get("hsi_appointment_id"):
            intent = hsi_journal.enqueue_reassign(row, turno_dt)
        else:
            intent = hsi_journal.enqueue_appointment(row)
        if intent and store.hsi_outbox_reset(intent["id"]):
            outcome = hsi_journal.replay_item(intent["id"])
        updated = store.get_booking_request(sid) or row
    except Exception:  # noqa: BLE001 — preserve the durable pending/error state
        updated = store.get_booking_request(sid) or row
    if updated.get("hsi_estado") != "ok":
        try:
            detalle = hsi_journal.motivo_operador(outcome)
        except Exception:  # noqa: BLE001
            detalle = i18n.t_or("admin.srv.retryhsi.no_confirmado", "HSI no confirmó el turno.", i18n.active())
        return 502, {"error": "hsi_no_confirmo", "detalle": detalle,
                     "solicitud": bkr._admin_row(updated)}
    return 200, {"ok": True, "solicitud": bkr._admin_row(updated)}
