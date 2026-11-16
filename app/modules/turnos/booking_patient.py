"""booking_patient.py — what a patient does with their own requests: list them, open one, cancel it,
reschedule it and opt in or out of the waitlist.

Split out of booking_requests.py (TASK-60, 2026-11-06). `_after_cancel` (the release and the waitlist
re-offer after a cancellation) is also the desk's cancel (booking_decisions). The public row shape, the
session helpers and the settings stay in booking_requests.py and are called as `bkr.<name>` at call time, so
a test that patches them there still reaches this module.
"""

import re
import secrets
from datetime import datetime, timezone

from app.modules.turnos import booking_requests_store as store
from app.core import format
from app.core import ids as _ids
from app.cuentas import claim_gate
from app.modules.turnos import booking_requests as bkr
from app.modules.turnos import booking_capacity


# ---- GET /api/requests?dni= -------------------------------------------------

def _waitlist_active(sid):
    """True if that request has an active waitlist opt-in. Never breaks
    loading the request if the table/database isn't available (best-effort,
    same contract as the rest of this module's side reads)."""
    try:
        return store.waitlist_active(sid)
    except Exception:  # noqa: BLE001
        return False


def _public_detail(row):
    """The same public projection as the list (`_public_row`, which already
    brings medico_asignado), plus whether "notify me if an earlier slot
    opens up" is active (it only makes sense to show the toggle on the
    detail, not on every row of the list). Everything else stays hidden
    just like in `_public_row` (no raw ai_json, contact, orden_path, or HSI
    ids)."""
    out = bkr._public_row(row)
    out["lista_espera_activo"] = _waitlist_active(row.get("id")) if row.get("id") is not None else False
    if out.get("id") is not None:
        # Pendiente sin médico asignado: decide el médico del bloque elegido
        # (slot_medico_id) — mismo criterio que la CTA de "Solicitud enviada".
        entrevista = bkr._entrevista_field(
            out["id"], row.get("medico_asignado_id") or row.get("slot_medico_id"),
            incluir_estado=True)
        if entrevista:
            out["entrevista"] = entrevista
    return out


def _patient_detail_core(usuario, sid):
    """GET /api/requests/<id> — the detail of ONE own request. Typed
    `usuario` (session identity, already resolved by the caller via
    `_get_user()`) and `sid` (the numeric id — the caller already declined a
    non-numeric one via the shared terminal-404 contract) in, `(status,
    payload)` out.

    Owner = the patient (dni) OR the adult who requested it
    (solicitante_dni), the same criterion as `list_by_dni` and the
    .ics: if the parent sees the child's row in the list, they must be able
    to open it. Someone else's request → 404 (not 403: we don't confirm it
    exists)."""
    if not usuario:
        return 401, {"error": "auth_requerida"}
    dni = _ids.dni(bkr._user_dict(usuario).get("dni"))
    row = store.get_booking_request(sid)
    if claim_gate.hidden(dni) or not row or dni not in (row.get("dni"), row.get("solicitante_dni")):
        return 404, {"error": "no_encontrado"}
    return 200, {"solicitud": _public_detail(row)}


def _parse_emr_appointment_dt(value):
    """Parse the EMR's confirmed timestamp into canonical ART, or None.

    The bridge timestamp is installation-local. Accept a legacy naive ISO value as
    ART, but normalize any offset-bearing value so equality and persistence do
    not depend on the Python process timezone.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=bkr._ART)
    return dt.astimezone(bkr._ART)


def patient_rows(dni):
    """Public appointment rows for an already-authenticated patient.

    The account HTML uses the same projection to embed its first render and
    avoid the old /account -> /api/account/me -> /api/requests waterfall.
    Identity still comes from the server-side session; callers must never pass
    a DNI supplied by the browser.
    """
    if claim_gate.hidden(dni):
        return []  # an account the desk has not confirmed sees no history (TASK-68)
    rows = store.list_by_dni(dni)
    imported = []
    for r in store.hsi_appointments_by_dni(dni):
        appt, schedule = r.get("appointment") or {}, r.get("schedule") or {}
        specialty = schedule.get("specialty") or "Turno registrado en EMR"
        when = _parse_emr_appointment_dt(appt.get("scheduled_at"))
        imported.append({"id": f"hsi-{r['hsi_appointment_id']}", "servicio_id": "hsi-importado",
                         "servicio_nombre": specialty, "estado": "aprobada", "turno_dt": bkr._iso_art(when),
                         "turno_pasado": bkr._appointment_has_elapsed({"turno_dt": when}),
                         "hsi_imported": True,
                         "medico_asignado": format.person_name(schedule.get("professional_name")),
                         "created": None})
    return [bkr._public_row(r) for r in rows] + imported


def _patient_list_core(usuario):
    """GET /api/requests — the session's own requests. Typed `usuario`
    (session identity, already resolved by the caller via `_get_user()`)
    in, `(status, payload)` out.

    Identity ALWAYS comes from the session — never from the client's
    ?dni=. The old "no session honors ?dni=" fallback was an IDOR: anyone
    could list any DNI's requests (PHI) without authenticating. No session
    -> 401, same contract as POST /api/requests."""
    dni = None
    if usuario:
        dni = _ids.dni(bkr._user_dict(usuario).get("dni")) or None
    if not dni:
        return 401, {"error": "auth_requerida"}
    return 200, {"solicitudes": patient_rows(dni)}


# ---- POST /api/requests/<id>/cancel (patient cancels their own) ---------------

def cancel_request(dni, sid):
    """Cancel a patient's own booking from typed identity and numeric id."""
    if dni is None:
        return 401, {"error": "auth_requerida"}
    row = store.get_booking_request(sid)
    if not row or dni not in (row.get("dni"), row.get("solicitante_dni")):
        return 404, {"error": "no_encontrado"}

    estado = row.get("estado")
    if estado not in ("pendiente", "aprobada"):
        return 400, {"error": "no_cancelable"}
    turno_dt = row.get("turno_dt")
    if estado == "aprobada":
        ahora = datetime.now(timezone.utc)
        if not turno_dt or turno_dt <= ahora:
            return 400, {"error": "turno_pasado"}

    emr_selected = bkr._emr_sync_enabled()
    updated = store.cancel(sid, actor=bkr._patient_actor(row.get("dni"), dni),
                           skip_hsi_without_connector=not emr_selected,
                           pending_hsi_with_connector=emr_selected)
    if updated is None:
        return 400, {"error": "no_cancelable"}

    updated = _after_cancel(sid, estado, turno_dt, updated, emr_selected)
    return 200, {"ok": True, "solicitud": _public_detail(updated)}


def _after_cancel(sid, estado, turno_dt, updated, emr_selected):
    """What a cancellation sets in motion once it is persisted, the same for
    the patient and for the desk: the slot hold's release, the EMR cancel and
    the waitlist re-offer. Returns the row as it stands afterwards.
    `emr_selected` is the connector choice the cancellation itself was
    written with."""
    # The request's own slot hold (a pending request's chosen time, or the one
    # a reschedule linked to an approved one) would otherwise keep that time
    # hidden from everyone until its hour. Best-effort like the rest: the
    # cancellation already persisted.
    try:
        store.holds_release_booking_request(sid)
    except Exception:  # noqa: BLE001
        pass
    # Decide the EMR cancel from the row AS WRITTEN, not from the pre-write
    # snapshot: an approval's create replay may have linked it in between.
    if updated.get("hsi_appointment_id") and emr_selected and bkr._emr_sync_enabled():
        try:
            # The cancellation transaction already made the linked row
            # pending; backfill can recover an interrupted enqueue.
            from app.modules.turnos import hsi_journal
            hsi_journal.enqueue_cancel(updated)
            hsi_journal.replay(limit=1)  # connected path uses the same journal contract
            updated = store.get_booking_request(sid) or updated
        except Exception:  # noqa: BLE001 — local cancellation remains durable/visible
            pass
    # Without a connector, cancel() chose skip or retained an unsettled
    # journal status in the cancellation transaction. No journal work is added.

    # If we freed up a future appointment, kick off the re-offer to that
    # service+center's waitlist. ALWAYS best-effort: the cancellation
    # ALREADY persisted above, this must never make it fail (same contract
    # as HSI/email).
    if estado == "aprobada" and turno_dt:
        try:
            from app.modules.turnos import waitlist
            waitlist.opt_out(sid)
            waitlist.release_slot(
                updated.get("servicio_id"), updated.get("centro_slug"), turno_dt)
        except Exception:  # noqa: BLE001
            pass
    return updated


# ---- POST /api/requests/<id>/reprogramar (patient picks a new time) -----------
#
# The "Reschedule" link on /my-requests used to lead to the service's FULL
# wizard — referral upload and all — as if it were a new request (the product owner,
# 2026-07-17: "should lead to the only new time, not the whole wizard
# uploading the order again"). This is only the time-picking step: the
# referral, the account, and the patient are already loaded from the
# existing request.

def _reschedule_core(request, dni, sid, read_body):
    """POST /api/requests/<id>/reprogramar — patient picks a new time. Typed
    `dni`/`sid` in, plus `read_body` (a zero-arg callable that parses the
    JSON body — kept as a callable, not a pre-parsed dict, so parsing keeps
    happening at the EXACT point the original code reached it: after the
    owner/state checks, never before). `(status, payload)` out. The request
    is forwarded ONLY to `notifications.notify_approved()`/
    `waitlist.release_slot()` (best-effort side notifications, owned by
    notifications/send.py and modules/turnos/waitlist.py) — never
    read/written here directly."""
    if dni is None:
        return 401, {"error": "auth_requerida"}
    row = store.get_booking_request(sid)
    if not row or dni not in (row.get("dni"), row.get("solicitante_dni")):
        return 404, {"error": "no_encontrado"}

    estado = row.get("estado")
    if estado not in ("pendiente", "aprobada"):
        return 400, {"error": "no_reprogramable"}
    turno_actual = row.get("turno_dt")
    if estado == "aprobada":
        ahora = datetime.now(timezone.utc)
        if not turno_actual or turno_actual <= ahora:
            return 400, {"error": "turno_pasado"}

    try:
        body = read_body()
    except Exception as e:  # noqa: BLE001
        return 400, {"error": f"JSON inválido: {e}"}

    dia = str(body.get("dia") or "").strip()
    hora = str(body.get("hora") or "").strip()
    if not (bkr._SLOT_DAY_RE.match(dia) and bkr._SLOT_TIME_RE.match(hora)):
        return 400, {"error": "dia/hora inválidos"}
    slot_dt = bkr._slot_dt_art(dia, hora)
    if slot_dt is None:
        return 400, {"error": "dia/hora inválidos"}
    preferencia = ((body.get("preferencia") or "").strip()[:120]) or None

    from app.modules.turnos import booking_horizon
    horizon_error = booking_horizon.check_slot(row.get("servicio_id"), dia, hora,
        doctor_id=row.get("medico_asignado_id") if estado == "aprobada" else None)
    if horizon_error:
        return 409, horizon_error
    medico_actual = row.get("medico_asignado") if estado == "aprobada" else None
    if medico_actual:
        _, motivo_horario = booking_capacity._doctor_appointment_in_schedule(medico_actual, slot_dt, row)
        if motivo_horario:
            return 409, {"error": motivo_horario}
        if booking_capacity._doctor_appointment_busy(medico_actual, slot_dt, sid,
                                    medico_id=row.get("medico_asignado_id")):
            return 409, {"error": "turno_medico_tomado"}

    # Same soft-lock as a new request (slot_holds.py): the chosen time is
    # taken BEFORE touching the request — if someone else just grabbed it,
    # 409 and nothing changes.
    servicio_id = row.get("servicio_id")
    token = secrets.token_urlsafe(16)
    try:
        from app.modules.turnos import slot_holds
        tomado = slot_holds.try_take(servicio_id, dia, hora, token)
    except Exception:  # noqa: BLE001 — fail CLOSED, same as `_create`'s take
        # (2026-08-05 data-integrity guards, plan item 7): treat a hiccup as
        # NOT acquired, never as silent success.
        tomado = False
    if not tomado:
        return 409, {"error": "turno_tomado"}

    # This request's previous hold (if any) is now stale — it's released
    # before linking the new one, so as not to leave an old slot blocked.
    store.holds_release_booking_request(sid)
    store.hold_link_booking_request(token, sid, slot_dt)

    if estado == "pendiente":
        # There's no hospital-confirmed turno_dt yet: only the PREFERENCE
        # that will be reviewed changes (same field a new request fills in
        # when choosing a time).
        updated = store.reschedule_preference(sid, preferencia)
    else:
        # Approved: the appointment was ALREADY confirmed — moving the date
        # is the same as accepting a waitlist offer (reassign_appointment),
        # with the same best-effort push to HSI and the same email notice as
        # an approval (the patient needs to know the new date/time).
        if row.get("hsi_appointment_id"):
            # Linked HSI turnos reassign through their own remote record;
            # creating a second appointment here was the old outage bug.
            try:
                updated = store.reassign_appointment(
                    sid, slot_dt, {"estado": "pendiente" if bkr._emr_sync_enabled() else "skip"},
                    actor=bkr._patient_actor(row.get("dni"), dni), slot_capacity=1)
            except store.SlotConflict:
                # Another approval won this slot after the hold was taken
                # (the capacity check / doctor index is the real guard):
                # nothing moved.
                store.hold_release(token)
                return 409, {"error": "turno_tomado"}
            if bkr._emr_sync_enabled():
                try:
                    from app.modules.turnos import hsi_journal
                    hsi_journal.enqueue_reassign(row, slot_dt)
                    hsi_journal.replay(limit=1)
                    updated = store.get_booking_request(sid) or updated
                except Exception:  # journal persists on DB; retry handles outage
                    pass
        else:
            # Not linked yet: the approval's create may already be in the EMR
            # with its response lost, so a direct create here opened a second
            # clinical appointment (hsi.md, formal sweep 2026-09-24). Only the
            # local time moves; the journal lands that one create on it.
            emr = bkr._emr_sync_enabled()
            try:
                updated = store.reassign_appointment(
                    sid, slot_dt, {"estado": "pendiente" if emr else "skip"},
                    actor=bkr._patient_actor(row.get("dni"), dni), slot_capacity=1)
            except store.SlotConflict:
                store.hold_release(token)
                return 409, {"error": "turno_tomado"}
            if emr:
                try:
                    from app.modules.turnos import hsi_journal
                    hsi_journal.replay_unlinked_move(updated)
                    updated = store.get_booking_request(sid) or updated
                except Exception:  # noqa: BLE001 — the durable intent retries
                    pass
        try:
            from app.notifications import send as notifications
            notifications.notify_approved(updated, handler=request)
        except Exception:  # noqa: BLE001
            pass
        # The time that's freed up might help someone on that
        # service+center's waitlist — best-effort, never breaks the above.
        try:
            from app.modules.turnos import waitlist
            waitlist.release_slot(servicio_id, row.get("centro_slug"),
                                      turno_actual, handler=request)
        except Exception:  # noqa: BLE001
            pass

    return 200, {"ok": True, "solicitud": _public_detail(updated)}


# ---- POST /api/requests/<id>/waitlist (opt-in/out) -----------------------------

def _waitlist_toggle_core(dni, sid, read_body):
    """POST /api/requests/<id>/waitlist — opt-in/out of "notify me if an
    earlier slot opens up". Typed `dni`/`sid` in, plus `read_body` (a
    zero-arg callable — same reasoning as `_reschedule_core`'s: parsing
    keeps happening after the owner check, never before). `(status,
    payload)` out. No handler forwarded — this route never needs one
    downstream."""
    if dni is None:
        return 401, {"error": "auth_requerida"}
    row = store.get_booking_request(sid)
    if not row or dni not in (row.get("dni"), row.get("solicitante_dni")):
        return 404, {"error": "no_encontrado"}
    try:
        body = read_body()
    except Exception as e:  # noqa: BLE001
        return 400, {"error": f"JSON inválido: {e}"}

    from app.modules.turnos import waitlist
    activo = bool(body.get("activo"))
    if not activo:
        waitlist.opt_out(sid)
        return 200, {"ok": True, "activo": False}

    turno_dt = row.get("turno_dt")
    if row.get("estado") != "aprobada" or not turno_dt or turno_dt <= datetime.now(timezone.utc):
        return 400, {"error": "no_aplica"}

    # The email is ALWAYS the requesting account's (solicitante_dni: a
    # dependent has no account of their own) — same criterion as
    # notifications._email_destino.
    cuenta_dni = re.sub(r"\D", "", str(row.get("solicitante_dni") or row.get("dni") or ""))
    cuenta = store.get_user(cuenta_dni) if cuenta_dni else None
    email = (cuenta or {}).get("email")
    if not email:
        return 400, {"error": "falta_email"}

    waitlist.opt_in(sid, row.get("dni"), email, row.get("servicio_id"),
                       row.get("centro_slug"), turno_dt)
    return 200, {"ok": True, "activo": True}
