"""booking_decisions.py — what the desk decides on a request: approve (with its HSI push), assign, reject,
correct a field the AI read, complete the patient's data, cancel from the desk, restore a cancelled
appointment and mark attendance.

Split out of booking_requests.py (TASK-60, 2026-11-06). `_persist_approval` is also the patient's
auto-confirmation (booking_create) and the panel's appointment moves (admin_appointments). The row shape, the
catalog and the settings stay in booking_requests.py and are called as `bkr.<name>` at call time, so a test
that patches them there still reaches this module.
"""

import re
import copy
import secrets
from datetime import date, datetime

from app.modules.turnos import booking_requests_store as store
from app.ai import ai_referral
from app.auth import admin_auth
from app.core import clock, i18n
from app.modules.turnos import booking_requests as bkr
from app.modules.turnos import booking_capacity
from app.modules.turnos import booking_patient


class OverbookingPolicyDenied(Exception):
    """A shared staff path attempted a disabled/malformed percentage policy."""


# ---- POST /api/admin/requests/<id>/approve ------------------------------------

def _approve_core(identity, row, sid, body, handler=None):
    """POST /api/admin/requests/<id>/approve — pure domain function behind
    the native endpoint (native-migration campaign, W2d follow-up on family
    F7's deliberately-deferred CAS action rows). Typed `identity`/`row`
    (already resolved by the caller via `_staff_row_or_error`) and
    `sid`/`body` (already parsed) in, `(status, payload)` out — EXACTLY the
    same validation order and the same `store.AlreadyProcessed`/
    `store.SlotConflict` CAS handling `_approve` always had, only moved
    verbatim behind this typed boundary; zero change to the compare-and-swap
    semantics `test_integrity_races.py` exercises. `handler` is forwarded
    ONLY to `_persist_approval()` -> `notifications.notify_approved()`
    (best-effort email; already swallows every exception) — never
    read/written here directly, same contract `cancel_request`/
    `_reschedule_core` already established for this pattern."""
    turno_raw = (body.get("turno_dt") or "").strip()
    if not turno_raw:
        return 400, {"error": i18n.t_or("admin.srv.approve.falta_turno", "falta turno_dt", i18n.active())}
    try:
        turno_dt = datetime.fromisoformat(turno_raw.replace("Z", "+00:00"))
    except ValueError:
        return 400, {"error": i18n.t_or("admin.srv.approve.turno_invalido", "turno_dt inválido (usar ISO 8601)", i18n.active())}

    # Operator reassignment (optional): a different service and/or doctor.
    # The override is applied BEFORE the push to HSI so the appointment
    # lands in the correct specialty.
    servicio_id = (body.get("servicio_id") or "").strip() or None
    # The desk always sends the service it shows: an unreadable catalog keeps
    # approving the request's own service as before, and only a «Derivar a»
    # another one waits for the catalog (a retry phrase, not «inválido»).
    if servicio_id and bkr._services() is None:
        if servicio_id != row.get("servicio_id"):
            return 503, {"error": i18n.t_or(
                "booking.error.catalog_unavailable",
                "No pudimos consultar los servicios en este momento. Probá de nuevo en unos minutos.",
                i18n.active())}
    elif servicio_id and not bkr._service_valid(servicio_id):
        return 400, {"error": i18n.t_or("admin.srv.approve.servicio_invalido", "servicio_id inválido", i18n.active())}
    medico = (body.get("medico") or "").strip() or None

    centro_slug = None
    if servicio_id and servicio_id != row.get("servicio_id"):
        row = dict(row)
        row["servicio_id"] = servicio_id
        # The appointment is where the service is: a «Derivar a» a service of
        # another center moves the request there too, or the email, the
        # account and the day queue would send the patient to the old place.
        centro_slug = str((bkr._service_full(servicio_id) or {}).get("centro") or "").strip() or None
        if centro_slug:
            row["centro_slug"] = centro_slug
    else:
        servicio_id = None   # no change → don't touch the column

    # Lab work has no doctor (the product owner, 2026-07-19): if the body carries one
    # anyway (old UI, manual client) it's silently ignored instead of
    # rejecting the approval — the servicio_id already considered is the
    # (possibly reassigned) one from `row`.
    if medico and bkr._is_lab(bkr._service_full(row.get("servicio_id"))):
        medico = None

    # Nunca un médico de otra especialidad (2026-10-23, ver _doctor_pool): el
    # nombrado tiene que estar en el pool del servicio que SE APRUEBA (el
    # derivado, si hubo derivación). El sugerido al paciente ya sale de ese
    # pool. Sin médico se sigue aprobando «a asignar», como siempre.
    if medico and bkr._doctor_serves_service(medico, bkr._service_full(row.get("servicio_id"))) is False:
        return 409, {"error": "medico_no_atiende_servicio"}
    # Approving «a asignar» must not keep a doctor of ANOTHER service that was
    # saved earlier (`/assign` runs against the service before a «Derivar a»,
    # so it cannot check): the approval's UPDATE keeps what the row had.
    if not medico:
        guardado = (row.get("medico_asignado") or "").strip()
        if guardado and bkr._doctor_serves_service(guardado, bkr._service_full(row.get("servicio_id"))) is False:
            store.assign_pending_booking_request(sid, None, None)

    # The catalog id is the doctor's identity (2026-10-23): the busy checks
    # below compare by it, and _persist_approval writes name+id as ONE pair
    # (doctor_appointments.resolve_pair). A name that resolves to no single
    # catalog doctor keeps medico_id None — never blocks the approval.
    medico_id = None
    if medico:
        try:
            from app.modules.turnos import doctor_appointments
            medico_id = doctor_appointments.resolve_doctor_id(medico)
        except Exception:  # noqa: BLE001
            medico_id = None

    sobreturno_raw = body.get("sobreturno", False)
    if not isinstance(sobreturno_raw, bool):
        return 400, {"error": "sobreturno_invalido"}
    sobreturno = sobreturno_raw
    ocupado = bool(medico and booking_capacity._doctor_appointment_busy(medico, turno_dt, sid, medico_id=medico_id))
    ocupado_en_centro = bool(medico and booking_capacity._doctor_appointment_busy(
        medico, turno_dt, sid, center_slug=row.get("centro_slug"), medico_id=medico_id))
    if sobreturno and not ocupado_en_centro:
        return 409, {"error": "sobreturno_requiere_turno_previo"}
    if medico:
        _, motivo_horario = booking_capacity._doctor_appointment_in_schedule(medico, turno_dt, row)
        if motivo_horario:
            return 409, {"error": motivo_horario}
    # A same-doctor/same-minute collision is always a 409 unless staff makes
    # the exceptional action explicit AND the selected doctor's own agenda
    # grants a remaining sobreturno at that time. The public web never sees
    # this capacity; it only exists in the operator approval route.
    if ocupado and not sobreturno:
        return 409, {"error": "turno_medico_tomado"}
    overbooking_policy = None
    if sobreturno:
        permitido, motivo = booking_capacity._overbooking_allowed(medico, turno_dt, row, sid)
        if not permitido:
            return 409, {"error": motivo or "sobreturno_no_habilitado"}

    try:
        updated = _persist_approval(sid, turno_dt, servicio_id=servicio_id,
                                        centro_slug=centro_slug,
                                        medico=medico, medico_id=medico_id,
                                        resuelta_por=identity.get("usuario"),
                                        sobreturno=sobreturno, handler=handler)
    except store.AlreadyProcessed:
        # Status-CAS lost (2026-08-05 data-integrity guards, plan item 1):
        # another approve/reject already resolved this exact row — two
        # clicks on "Aprobar" in two tabs, or the queue and the panel both
        # landing on it at once. Zero side effects: `approve`'s UPDATE
        # never matched a row, so nothing to undo.
        return 409, {"error": i18n.t_or(
            "admin.srv.request.ya_resuelta", "La solicitud ya fue resuelta.",
            i18n.active())}
    except store.SlotConflict:
        # The pre-check above (`_doctor_appointment_busy`) is UX only — this
        # is the real guard: another approval won this exact (médico,
        # horario) in the same instant. Same surface the pre-check already
        # uses, so the operator sees one consistent message either way.
        return 409, {"error": "turno_medico_tomado"}
    except store.OverbookingCapacityReached:
        return 409, {"error": "sobreturno_cupo_agotado"}
    except OverbookingPolicyDenied:
        return 409, {"error": "sobreturno_no_configurado"}
    return 200, {"ok": True, "solicitud": bkr._admin_row(updated)}


def _assign_core(identity, row, sid, read_body):
    """POST /api/admin/requests/<id>/assign — pure domain function (see
    `_approve_core`'s docstring for the campaign/CAS-preservation context).
    Typed `identity`/`row`/`sid` in, plus `read_body` (a zero-arg callable
    that parses the JSON body — kept as a callable, NOT a pre-parsed dict,
    same reason `_reschedule_core` keeps it a callable: the already-resolved
    409 below must fire BEFORE any body parsing is attempted, exactly as
    the retired `handle()`'s dispatch order did). `(status, payload)` out."""
    if row.get("estado") != "pendiente":
        return 409, {"error": i18n.t_or("admin.srv.request.ya_resuelta", "La solicitud ya fue resuelta.", i18n.active())}
    try:
        body = read_body()
    except Exception as exc:  # noqa: BLE001
        return 400, {"error": i18n.t_or("admin.srv.error.json_invalido", "JSON inválido: {motivo}", i18n.active(), motivo=exc)}
    medico_id = str((body or {}).get("medico_id") or "").strip()
    medico_nombre = str((body or {}).get("medico") or "").strip()
    try:
        from app.patient_web import catalog
        medico = next((m for m in catalog.doctors()
                       if not m.get("_borrado")
                       and ((medico_id and str(m.get("id") or "") == medico_id)
                            or (medico_nombre and str(m.get("nombre") or "") == medico_nombre))),
                      None)
    except Exception:  # noqa: BLE001
        medico = None
    if not medico:
        return 400, {"error": i18n.t_or("admin.srv.assign.medico_invalido", "Médico inválido.", i18n.active())}
    resolved_medico_id = str(medico.get("id") or "").strip()
    # The assignment makes the patient the doctor's (access_log.may_see): leave the row first, and assign
    # nothing when it cannot be written (security review 2026-11, M-2).
    from app.core import access_log
    try:
        access_log.record(identity, "edicion", row.get("dni"), target_id="assign:%s" % sid)
    except access_log.AccessLogUnavailable as exc:
        status, payload = access_log.error_body(exc)
        return status, payload
    except access_log.AccessLogFailed:
        return 500, {"error": "registro_de_accesos"}
    updated = store.assign_pending_booking_request(
        sid, str(medico.get("nombre") or ""), resolved_medico_id)
    if not updated:
        return 409, {"error": i18n.t_or("admin.srv.request.ya_resuelta", "La solicitud ya fue resuelta.", i18n.active())}
    return 200, {"ok": True, "solicitud": bkr._admin_row(updated)}


def _persist_approval(sid, turno_dt, servicio_id=None, medico=None,
                          medico_id=None, resuelta_por=None, sobreturno=False,
                          overbooking_policy=None, handler=None, slot_capacity=None,
                          centro_slug=None, restaurar=False):
    """Shared approval core (validations ALREADY done by the caller):
    persist + enqueue HSI + release the hold + email. Used by `_approve`
    (operator) and `_create`'s automatic confirmation (auto_confirmar,
    2026-07-24 hospital meeting). `slot_capacity` is passed only by the web
    auto-confirmation: the atomic twin of `slot_holds.try_take`'s "already
    approved" check (see the store's `_enforce_slot_capacity`), and by the
    desk's «Restaurar» of a doctor-less appointment. `restaurar` approves a
    CANCELLED row back (`_desk_restore_core`). Returns the final row."""
    fila_previa = store.get_booking_request(sid) or {}
    if medico or medico_id:
        # Nombre e id juntos (2026-10-23): id + nombre del catálogo, o el
        # nombre tal cual con id None — nunca el id de otro médico.
        try:
            from app.modules.turnos import doctor_appointments
            medico, medico_id = doctor_appointments.resolve_pair(medico, medico_id)
        except Exception:  # noqa: BLE001 — legacy free-text clinician remains valid
            medico_id = None
    # All staff paths converge here (queue approval and desk-created
    # appointments).  Deriving here prevents a caller from skipping the
    # band-level atomic policy by merely omitting an optional argument.
    if sobreturno and medico:
        franja, _ = booking_capacity._doctor_appointment_in_schedule(medico, turno_dt, fila_previa, medico_id, staff_capacity=True)
        overbooking_policy = (booking_capacity._percentage_overbooking_policy(
            medico, turno_dt, franja, fila_previa, medico_id) if franja else None)
        if overbooking_policy is False or not franja:
            raise OverbookingPolicyDenied("sobreturno_no_configurado")
        if overbooking_policy:
            # The store receives only the database contract; this identity is
            # for the read-side preflight and should never become a new DB API.
            overbooking_policy = dict(overbooking_policy)
            overbooking_policy.pop("_overbooking_doctor_id", None)
    # Only the restore passes the flag: test fakes of `store.approve` keep their signature.
    restaurar_kw = {"restaurar": True} if restaurar else {}
    if not bkr._emr_sync_enabled() or not (fila_previa.get("dni") or "").strip():
        # A local-only installation deliberately has no clinical EMR handoff.
        # Keep its explicit state rather than manufacturing retry work.
        # No DNI also means no handoff is possible: an appointment loaded
        # with only a note (front desk, 2026-07-27) has no patient to send
        # to HSI, and enqueueing it would leave an eternal retry. It's
        # marked 'skip', which is the truth — it wasn't attempted — instead
        # of 'error', which would invite a retry.
        updated = store.approve(sid, turno_dt, {"estado": "skip"},
                             servicio_id=servicio_id, centro_slug=centro_slug,
                             medico_asignado=medico, medico_asignado_id=medico_id,
                             resuelta_por=resuelta_por, sobreturno=sobreturno,
                             slot_capacity=slot_capacity, **restaurar_kw,
                             **(overbooking_policy or {}))
    else:
        # Persist the local decision as pending before enqueueing or attempting
        # any HSI network I/O.  If this process dies before enqueue, startup
        # backfill deterministically adopts the pending approved row.
        updated = store.approve(sid, turno_dt, {"estado": "pendiente"},
                             servicio_id=servicio_id, centro_slug=centro_slug,
                             medico_asignado=medico, medico_asignado_id=medico_id,
                             resuelta_por=resuelta_por, sobreturno=sobreturno,
                             slot_capacity=slot_capacity, **restaurar_kw,
                             **(overbooking_policy or {}))
        try:
            from app.modules.turnos import hsi_journal
            intent = hsi_journal.enqueue_appointment(updated)
            if intent:
                # Claim this exact item before replaying: it records sending,
                # attempts, and a lease before the first external call.
                hsi_journal.replay_item(intent["id"])
            updated = store.get_booking_request(sid) or updated
        except Exception:  # noqa: BLE001 — pending local decision stays visible
            updated = store.get_booking_request(sid) or updated
    try:
        # The approved request's turno_dt takes over the block (see
        # slot_holds.taken) — the soft-lock hold that held it is no longer needed.
        store.holds_release_booking_request(sid)
    except Exception:  # noqa: BLE001 — the approve ALREADY persisted, never break here
        pass
    try:
        from app.notifications import send as notifications
        notifications.notify_approved(updated, handler)
    except Exception:  # noqa: BLE001 — the approve ALREADY persisted, never break here
        pass
    return updated


def _mark_attendance_core(identity, sid, read_body):
    """Pure domain function behind `_mark_attendance` (see that function's
    own docstring for the full behavior — unchanged verbatim here). Typed
    `identity` (already resolved by the caller — admin/operador/medico/
    admisionista, per `admin_auth.require_admin(permitir_medico=True,
    permitir_admisionista=True)`) and `sid` in, plus `read_body` (a zero-arg
    callable — same reason `_assign_core` keeps it a callable: the
    medico-ownership and centro-scope checks below must run BEFORE any body
    parsing is attempted. `(status, payload)` out."""
    if identity.get("rol") == "medico":
        from app.modules.turnos import doctor_appointments
        medico_id = store.operator_doctor_id(identity["usuario"])
        row_actual = store.get_booking_request(sid) if medico_id else None
        medico = doctor_appointments.doctor_of(medico_id) if medico_id else None
        if (not medico_id or not row_actual
                or not doctor_appointments._belongs_to(row_actual, medico_id, (medico or {}).get("nombre") or "")):
            return 404, {"error": i18n.t_or("admin.srv.error.solicitud_no_encontrada", "solicitud no encontrada", i18n.active())}
    # Center-scoped access (2026-07-24): a restricted account doesn't mark
    # attendance on an appointment at another center. 404 not 403, same as
    # above: if it can't see it, it doesn't exist for that account.
    centros_ok = admin_auth.centros_permitidos(identity)
    if centros_ok is not None:
        fila = store.get_booking_request(sid)
        if not fila or (fila.get("centro_slug") or "") not in centros_ok:
            return 404, {"error": i18n.t_or("admin.srv.error.solicitud_no_encontrada", "solicitud no encontrada", i18n.active())}
    try:
        body = read_body()
    except Exception as exc:  # noqa: BLE001
        return 400, {"error": i18n.t_or("admin.srv.error.json_invalido", "JSON inválido: {motivo}", i18n.active(), motivo=exc)}
    asistencia = str(body.get("asistencia") or "").strip().lower()
    if asistencia not in ("presente", "ausente"):
        return 400, {"error": "asistencia_invalida"}
    try:
        row = store.mark_attendance(sid, asistencia, identity.get("usuario") or "operador")
    except ValueError as exc:
        code = str(exc)
        return (409 if code == "asistencia_no_elegible" else 400), {"error": code}
    if not row:
        return 404, {"error": i18n.t_or("admin.srv.error.solicitud_no_encontrada", "solicitud no encontrada", i18n.active())}

    hsi_sync = "sin_turno_hsi_vinculado"
    if row.get("hsi_appointment_id") and bkr._emr_sync_enabled():
        try:
            from app.modules.turnos import hsi_journal as durable_hsi
            durable_hsi.enqueue_attendance(row, asistencia)
            durable_hsi.replay(limit=1)
            after = store.get_booking_request(sid) or row
            hsi_sync = after.get("hsi_estado") or "pendiente"
            row = after
        except Exception:  # noqa: BLE001 — the local mark/audit log was already confirmed
            hsi_sync = "pendiente"
    resumen = store.attendance_summary_dni(row.get("dni"), bkr._no_show_limit())
    admin_row = bkr._admin_row(row)
    admin_row["asistencia_resumen"] = resumen
    admin_row["no_show_limit"] = bkr._no_show_limit()
    # Marking a no-show changes the ranking on the spot: the row that goes
    # back to the client must carry it recalculated, not the pre-mark one.
    from app.modules.turnos import patient_ranking
    admin_row["ranking"] = patient_ranking.by_dni(row.get("dni"))
    # `store.mark_attendance()` writes the queue invalidation in the same
    # transaction as this attendance change. SSE readers replay that durable,
    # non-PHI record rather than receiving a post-commit in-process hint.
    return 200, {"ok": True, "solicitud": admin_row, "hsi_sync": hsi_sync}


# ---- POST /api/admin/requests/<id>/reject -------------------------------------

def _reject_core(identity, sid, body, handler=None):
    """POST /api/admin/requests/<id>/reject — pure domain function (see
    `_approve_core`'s docstring for the campaign/CAS-preservation context).
    Typed `identity`/`sid`/already-parsed `body` in, `(status, payload)`
    out — the SAME `store.AlreadyProcessed` CAS handling `_reject` always
    had, moved verbatim. `handler` is forwarded ONLY to
    `notifications.notify_rejected()` (best-effort email; already swallows
    every exception) — never read/written here directly."""
    motivo = (body.get("motivo") or "").strip()
    if not motivo:
        return 400, {"error": i18n.t_or("admin.srv.reject.falta_motivo", "falta el motivo del rechazo", i18n.active())}
    try:
        updated = store.reject(sid, motivo, resuelta_por=identity.get("usuario"))
    except store.AlreadyProcessed:
        # Status-CAS lost (2026-08-05 data-integrity guards, plan item 1):
        # another approve/reject already resolved this row — no side effects.
        return 409, {"error": i18n.t_or(
            "admin.srv.request.ya_resuelta", "La solicitud ya fue resuelta.",
            i18n.active())}
    try:
        # Rejected: the slot becomes free for anyone again.
        store.holds_release_booking_request(sid)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.notifications import send as notifications
        notifications.notify_rejected(updated, handler)
    except Exception:  # noqa: BLE001
        pass
    return 200, {"ok": True, "solicitud": bkr._admin_row(updated)}


# ---- POST /api/admin/requests/<id>/campos --------------------------------------
#
# The operator corrects a value the AI misread (2026-07-17 handoff: "make
# all this data editable, and show the edited states"). Closed whitelist —
# `practicas` is never editable (read-only chips), nor are `prepaga`/
# `afiliado` (informational, no coincide). Persists in
# ai_json.campos[campo] = {valor, editado:true, ...} — recomputing
# `coincide` is cheap (same ai_referral helpers the rest of the module
# already uses) and is only attempted for dni/paciente; the rest stays "not
# comparable" after a manual edit.

EDITABLE_FIELDS = {"dni", "paciente", "especialidad", "diagnostico", "medico", "matricula", "fecha"}


_MAX_VALUE_LEN = 300


def _edit_field_core(row, sid, body):
    """POST /api/admin/requests/<id>/campos — pure domain function (see
    `_approve_core`'s docstring for the campaign context). Typed `row`/
    `sid`/already-parsed `body` in (`identity` is unused by this business
    logic; authorization and row resolution happen in the native router).
    `(status, payload)` out. `store.DbUnavailable` propagates
    uncaught to the caller, same as the original's redundant
    `except store.DbUnavailable: raise`."""
    campo = str(body.get("campo") or "").strip()
    if campo not in EDITABLE_FIELDS:
        return 400, {"error": i18n.t_or("admin.srv.editfield.campo_invalido", "campo inválido", i18n.active())}
    valor = body.get("valor")
    valor = None if valor is None else str(valor).strip()
    if valor and len(valor) > _MAX_VALUE_LEN:
        return 400, {"error": i18n.t_or("admin.srv.editfield.valor_largo", "valor demasiado largo", i18n.active())}

    ai_json = copy.deepcopy(row.get("ai_json") or {})
    campos = ai_json.setdefault("campos", {})
    c = dict(campos.get(campo) or {}) if isinstance(campos.get(campo), dict) else {}
    c["valor"] = valor or None
    c["editado"] = True

    if campo == "dni":
        dni_sol = ai_referral._digits(row.get("dni"))
        dni_leido = ai_referral._digits(valor)
        c["coincide"] = (dni_leido == dni_sol) if (dni_leido and dni_sol) else None
    elif campo == "paciente":
        nombre_sol = " ".join(x for x in (row.get("nombre"), row.get("apellido")) if x).strip()
        c["coincide"] = ai_referral._name_matches(valor, nombre_sol) if (valor and nombre_sol) else None
    else:
        # the rest of the editable fields don't have a cheap comparison to
        # recompute server-side (especialidad/medico/matricula/fecha/
        # diagnostico) — after a manual edit they stay "not comparable",
        # never a ✓/✗ mark that's no longer known to still be true.
        c.pop("coincide", None)

    campos[campo] = c
    store.save_ai(sid, ai_json)
    return 200, {"ok": True, "campos": campos}


# ---- POST /api/admin/requests/<id>/patient-data ------------------------------

_PATIENT_DATA_KEYS = ("nombre", "apellido", "nacimiento", "sexo")


_MAX_NAME_LEN = 80


def _patient_data_core(row, sid, body):
    """The desk completes what HSI needs to register this patient (names,
    birth date, sex) at the moment the appointment is loaded into HSI, and it
    is kept here so nobody is asked twice. Any subset of the four keys; an
    empty value is refused rather than erasing what is stored.

    The booking row is what the day queue reads, so it is always written. The
    patient's own account only gains what it was missing: a value the patient
    entered themselves is never replaced from the desk through this path."""
    if not isinstance(body, dict):
        return 400, {"error": "datos inválidos"}
    campos = {}
    for k in _PATIENT_DATA_KEYS:
        if k not in body:
            continue
        v = str(body.get(k) or "").strip()
        if not v:
            return 400, {"error": f"{k} vacío"}
        if k in ("nombre", "apellido"):
            if len(v) > _MAX_NAME_LEN:
                return 400, {"error": f"{k} demasiado largo"}
        elif k == "sexo":
            v = v.upper()
            if v not in ("F", "M", "X"):
                return 400, {"error": "sexo inválido (M, F o X)"}
        else:
            try:
                nac = date.fromisoformat(v) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None
            except ValueError:
                nac = None
            if nac is None or nac > clock.utcnow().date() or nac.year < 1900:
                return 400, {"error": "nacimiento inválido (usar AAAA-MM-DD)"}
        campos[k] = v
    if not campos:
        return 400, {"error": "faltan datos (nombre, apellido, nacimiento o sexo)"}

    actual = store.update_booking_patient_data(sid, campos) or row
    dni = str(row.get("dni") or "").strip()
    cuenta = store.get_user(dni) if dni else None
    if cuenta:
        faltantes = {k: v for k, v in campos.items() if not cuenta.get(k)}
        if faltantes:
            store.user_admin_update(dni, faltantes)
    return 200, {"ok": True, "paciente": {
        "nombre": actual.get("nombre") or None,
        "apellido": actual.get("apellido") or None,
        "nacimiento": str(actual.get("nacimiento") or "") or None,
        "sexo": actual.get("sexo") or None,
    }}


# ---- POST /api/admin/requests/<id>/cancel (the desk cancels) -----------------
#
# La contraparte técnica (hospital, 2026-10-19): «Hay que tener la opción de cancelar/eliminar
# turno» — the desk had no way to take an appointment out of «Pacientes de
# hoy». Cancel, never delete: the row and its history stay (same rule as
# «Deshabilitar, nunca eliminar»), the slot frees up exactly as when the
# patient cancels, and the patient is told by email.

def _desk_cancel_core(identity, row, sid, handler=None):
    """Typed `identity`/`row` (already scoped by `_staff_row_or_error`) in,
    `(status, payload)` out."""
    estado = row.get("estado")
    if estado not in ("pendiente", "aprobada"):
        return 409, {"error": i18n.t_or(
            "admin.srv.cancel.no_cancelable", "Ese turno ya no está activo.", i18n.active())}
    turno_dt = row.get("turno_dt")
    if estado == "aprobada" and turno_dt and turno_dt <= clock.utcnow():
        return 409, {"error": i18n.t_or(
            "admin.srv.cancel.turno_pasado",
            "Ese turno ya pasó: marcá la asistencia en lugar de cancelarlo.", i18n.active())}
    emr_selected = bkr._emr_sync_enabled()
    updated = store.cancel(sid, actor=identity.get("usuario"),
                           skip_hsi_without_connector=not emr_selected,
                           pending_hsi_with_connector=emr_selected)
    if updated is None:
        return 409, {"error": i18n.t_or(
            "admin.srv.cancel.no_cancelable", "Ese turno ya no está activo.", i18n.active())}
    updated = booking_patient._after_cancel(sid, estado, turno_dt, updated, emr_selected)
    try:
        from app.notifications import send as notifications
        notifications.notify_cancelled(updated, handler)
    except Exception:  # noqa: BLE001
        pass
    return 200, {"ok": True, "solicitud": bkr._admin_row(updated)}


# ---- POST /api/admin/requests/<id>/restore (the desk undoes a cancel) --------
#
# The product owner, 2026-11-05, on the patient file: if it was cancelled, maybe reschedule
# it or bring it back. «Restaurar» puts a cancelled
# APPOINTMENT back exactly as it was — same time, doctor, service — only while
# that time is still ahead and nobody else holds it; otherwise the file offers
# «Reprogramar» alone (a new appointment through the desk's own form). It is an
# ordinary desk approval: same doctor checks, the CAS, the audit event
# («restaurada»), the approval email. No booking limit: limits bind only the
# patient's own POST, never the desk (CLAUDE.md invariant).

def _patient_archived(dni):
    """True when the request's patient has an account and it is archived. No
    account (a desk-only patient) is not archived."""
    from app.core import db_accounts
    u = db_accounts.get_user(str(dni or "").strip()) if dni else None
    return bool(u) and u.get("activo") is False


def restore_blocker(row, held=None, archived=None):
    """None when the desk may restore this cancelled row, else the reason code.
    `row` is a wire-format request carrying `cancelled_from` (the patient
    file's read) or not (then it is read here). `held` is
    `slot_holds.held_or_offered()`, passed in when checking many rows;
    `archived` likewise (the file is one patient), else read here."""
    if row.get("estado") != "cancelada":
        return "no_restaurable"
    previo = row["cancelled_from"] if "cancelled_from" in row else store.cancelled_from(row.get("id"))
    if previo != "aprobada":
        return "no_restaurable"   # a request never had a place in the agenda
    if row.get("sobreturno"):
        # An overbooking sits on top of another turno and spends a quota;
        # «Reprogramar» books it again through those checks.
        return "no_restaurable"
    turno_dt = row.get("turno_dt")
    if not turno_dt or turno_dt <= clock.utcnow():
        return "turno_pasado"
    if archived is None:
        archived = _patient_archived(row.get("dni"))
    if archived:
        # Archiving cancelled it; the patient comes back first (their own
        # «Restaurar» on the file), then the turno — never an approved turno
        # for someone who cannot sign in (2026-11-06).
        return "paciente_archivado"
    if _restore_service_closed(row.get("servicio_id"), turno_dt.astimezone(bkr._ART)):
        return "no_restaurable"
    if bkr._emr_sync_enabled() and (row.get("hsi_appointment_id")
                                    or (row.get("hsi_estado") or "skip") != "skip"):
        # Its EMR appointment is cancelled (or being cancelled) there; a second
        # create for the same slot would hash to the first one's key and land
        # nowhere. A new appointment («Reprogramar») goes through cleanly.
        return "restaurar_con_hsi"
    if held is None:
        from app.modules.turnos import slot_holds
        held = slot_holds.held_or_offered()
    local = turno_dt.astimezone(bkr._ART)
    if (str(row.get("servicio_id") or ""), local.strftime("%Y-%m-%d"), local.strftime("%H:%M")) in held:
        return "turno_tomado"   # a web patient is choosing it or the waitlist was offered it
    medico = (row.get("medico_asignado") or "").strip()
    medico_id = row.get("medico_asignado_id")
    if medico:
        if booking_capacity._doctor_appointment_busy(medico, turno_dt, row.get("id"), medico_id=medico_id):
            return "turno_medico_tomado"
        _, motivo = booking_capacity._doctor_appointment_in_schedule(medico, turno_dt, row, medico_id)
        if motivo:
            return motivo
    elif store.slot_taken(row.get("servicio_id"), turno_dt, row.get("id")):
        return "turno_tomado"
    return None


def _restore_service_closed(servicio_id, local):
    """True when the catalog lists the service and it no longer gives this turno:
    walk-in now, or a same-day listing on another day (walk-in never offers
    appointments, on any surface). A catalog that cannot be read refuses."""
    try:
        from app.patient_web import catalog, site_availability
        servicio = next((s for s in catalog.services() if str(s.get("id")) == str(servicio_id)), None)
        if servicio is None:
            return False   # a service gone from the catalog: the turno was real, undoing a mistake is fine
        if not site_availability._offers_appointments(servicio):
            return True
        return bool(site_availability._opens_same_day(servicio)) and local.date() != clock.today()
    except Exception:  # noqa: BLE001 — fail closed: «Reprogramar» stays
        return True


def _desk_restore_core(identity, row, sid, handler=None):
    """Typed `identity`/`row` (already scoped by `_staff_row`, the desk's cancel
    scope) in, `(status, payload)` out. Holds the minute (the desk's own hold,
    as «Crear turno» does) from the last check until the write, so a patient
    cannot pick it in between."""
    from app.modules.turnos import slot_holds
    held = slot_holds.held_or_offered()   # read before our own hold joins it
    motivo = restore_blocker(row, held)
    if motivo:
        return 409, {"error": motivo}
    local = row["turno_dt"].astimezone(bkr._ART)
    token = "mesa:" + secrets.token_urlsafe(12)
    try:
        tomado = slot_holds.take_for_restore(
            str(row.get("servicio_id") or ""), local.strftime("%Y-%m-%d"), local.strftime("%H:%M"), token)
    except Exception:  # noqa: BLE001 — fail closed, as `admin_appointments._take_desk_hold`
        tomado = False
    if not tomado:
        return 409, {"error": "turno_tomado"}
    medico = (row.get("medico_asignado") or "").strip() or None
    try:
        # Again under the hold: a desk booking written since the first check is seen now.
        motivo = restore_blocker(row, held)
        if motivo:
            return 409, {"error": motivo}
        updated = _persist_approval(
            sid, row["turno_dt"], medico=medico, medico_id=row.get("medico_asignado_id"),
            resuelta_por=identity.get("usuario"), handler=handler,
            slot_capacity=None if medico else 1, restaurar=True)
    except store.AlreadyProcessed:
        return 409, {"error": "no_restaurable"}
    except store.SlotConflict:
        return 409, {"error": "turno_medico_tomado" if medico else "turno_tomado"}
    finally:
        store.hold_release(token)
    return 200, {"ok": True, "solicitud": bkr._admin_row(updated)}
