"""booking_create.py — POST /api/requests: a patient's booking, step by step.

Split out of booking_requests.py (TASK-60, 2026-11-06). `create_request()` runs `_CREATE_STEPS` in order;
each step reads and sets fields of the shared state and returns None to go on, or the (status, body) that
ends the call. Helpers shared with the rest of the booking code stay in booking_requests.py and are called
as `bkr.<name>` at call time, so a test that patches them there still reaches this module.
"""

import os
import secrets
import sys
from datetime import date
from types import SimpleNamespace

from app.modules.turnos import booking_requests_store as store
from app.ai import ai_referral
from app.core import format
from app.core import i18n
from app.core import ids as _ids
from app.core import launch_signals
from app.core import log_pii
from app.modules.turnos import booking_requests as bkr
from app.modules.turnos import booking_decisions
from app.modules.turnos import booking_referrals


def _lost_after_save(what, b, reason):
    """A side step that failed after the request was ALREADY saved: the request stands (never break here), one
    line with its id and `reason` — `log_pii.exc(exc)`, the exception's type, never its text nor the patient.
    The caller adds its closed launch-signal code (core/launch_signals.py)."""
    print(f"[solicitudes] {what} id={b.sid}: {reason}", file=sys.stderr, flush=True)


# «Contanos tu caso»: enough for a few sentences, never an arbitrary payload.
MAX_PATIENT_NOTE = 1000


def _catalog_error(key, fallback, codigo, status=400):
    return status, {"error": i18n.t_or(key, fallback, i18n.active()), "codigo": codigo}


def _check_catalog_refs(servicio_id, centro_slug, slot_medico_id):
    """None when the references of a patient's booking exist in the catalog,
    else the `(status, payload)` refusal (schema review №10, 2026-10-24: the
    catalog is JSON, the database cannot check these ids — the write points
    do). The service must be listed; the center must be a live catalog center
    AND the one the service belongs to; the doctor of the chosen slot (sent by
    the browser, forgeable) must be a live catalog doctor in the service's
    pool. An unreadable catalog is a refusal (503), never «accept anything»."""
    if bkr._services() is None:
        return _catalog_error(
            "booking.error.catalog_unavailable",
            "No pudimos consultar los servicios en este momento. Probá de nuevo en unos minutos.",
            "catalogo_no_disponible", 503)
    if not bkr._service_valid(servicio_id):
        return 400, {"error": i18n.t_or(
            "booking.error.service_not_found", "servicio inexistente", i18n.active())}
    from app.patient_web import catalog
    try:
        centros = {str(c.get("slug")) for c in (catalog.centers() or [])
                   if isinstance(c, dict) and c.get("slug")}
    except Exception:  # noqa: BLE001 — unreadable catalog: refuse, do not guess
        centros = set()
    try:
        medicos = catalog.doctors() or []
    except Exception:  # noqa: BLE001
        medicos = None
    # Only a reference the booking carries needs its part of the catalog: an
    # unreadable doctor list must read «try again», never «that doctor is gone».
    if (centro_slug and not centros) or (slot_medico_id and medicos is None):
        return _catalog_error(
            "booking.error.catalog_unavailable",
            "No pudimos consultar los servicios en este momento. Probá de nuevo en unos minutos.",
            "catalogo_no_disponible", 503)
    servicio = bkr._service_full(servicio_id) or {}
    if centro_slug:
        if centro_slug not in centros:
            return _catalog_error(
                "booking.error.center_not_found",
                "El centro de este turno ya no está disponible. Volvé a elegir el servicio desde la lista.",
                "centro_inexistente")
        if servicio.get("centro") and servicio["centro"] != centro_slug:
            return _catalog_error(
                "booking.error.center_mismatch",
                "Ese servicio no se atiende en el centro elegido. Volvé a elegir el servicio desde la lista.",
                "centro_no_corresponde")
    if slot_medico_id and not bkr._is_lab(servicio):
        medico = next((m for m in medicos if isinstance(m, dict)
                       and str(m.get("id")) == slot_medico_id and not m.get("rol")), None)
        # A test doctor is never offered to a patient (catalog.is_test_doctor):
        # a forged slot naming it reads exactly like a doctor that is gone.
        if medico is None or catalog.is_test_doctor(medico):
            return _catalog_error(
                "booking.error.doctor_not_found",
                "El profesional que elegiste ya no está disponible. Elegí otro horario.",
                "medico_inexistente")
        # a demo doctor is synthetic (never assigned, see `_auto_confirm_doctor`):
        # it only has to exist; a real one has to be in the service's pool
        if not medico.get("demo") and not bkr._doctor_pool(servicio, [medico]):
            return _catalog_error(
                "booking.error.doctor_not_in_service",
                "Ese profesional no atiende este servicio. Elegí otro horario.",
                "medico_no_atiende_servicio")
    return None


def _patient_age_years(nacimiento):
    """Reuses accounts._age_years (same criterion as edad.js). Lazy
    import, same defensive pattern as _get_user."""
    try:
        from app.cuentas import accounts
        return accounts._age_years(nacimiento)
    except Exception:  # noqa: BLE001
        return None


def _out_of_range(edad_num, edad_cfg):
    if edad_num is None or not isinstance(edad_cfg, dict):
        return False
    mn, mx = edad_cfg.get("min"), edad_cfg.get("max")
    if mn is not None and edad_num < mn:
        return True
    if mx is not None and edad_num > mx:
        return True
    return False


# ---- POST /api/requests (requires a session; identity from the session) --

def _limit_reached_response(n, abiertas, servicio_id, nombre_svc):
    """409 payload for the booking_limits guardrail — shared by the
    best-effort pre-check in `_create` (fires in the common case, before a
    slot hold is even taken) and the atomic `LimitReached` it can still
    lose the race against (2026-08-05 data-integrity guards,
    docs/roadmap/data-integrity-guards-plan.md #3): both report the SAME
    error shape, so the client can't tell which one fired."""
    nombre_svc = nombre_svc or i18n.t_or(
        "booking.error.active_limit_generic_service", "este servicio", i18n.active())
    # Singular/plural is decided by the LIMIT (n), same as the original
    # Spanish did — most services allow 1, so this branch is almost always
    # the singular one; kinesiología (limit 10) always plural.
    # The way out is the one «Mis turnos» really offers: cancel, then book
    # again. Rescheduling left that screen on 30.08 (TASK-49, 26.10).
    if n == 1:
        detalle = i18n.t_or(
            "booking.error.active_limit_detail_one",
            "Ya tenés {abiertas} turno de {servicio} pedido sin atender, que es el "
            "máximo. Para pedir otro horario, cancelá el que tenés en Mis turnos y "
            "pedí uno nuevo.",
            i18n.active(), abiertas=abiertas, servicio=nombre_svc)
    else:
        detalle = i18n.t_or(
            "booking.error.active_limit_detail_many",
            "Ya tenés {abiertas} turnos de {servicio} pedidos sin atender, que es el "
            "máximo. Para pedir otro, cancelá uno de los que tenés en Mis turnos y "
            "pedí uno nuevo.",
            i18n.active(), abiertas=abiertas, servicio=nombre_svc)
    return {
        "error": "limite_turnos_activos",
        "limite": n,
        "abiertas": abiertas,
        "servicio_id": servicio_id,
        "detalle": detalle,
        "accion": {"texto": i18n.t_or(
            "booking.error.active_limit_action", "Mis turnos", i18n.active()),
            "url": "/account#turnos"},
    }


def _respuesta_idem_existente(fila, slot_medico_id):
    """El mismo shape de éxito que un `/api/requests` fresco devuelve,
    construido a partir de una fila que YA EXISTÍA (dedup por `idem_key`)
    en vez de una que esta llamada acaba de crear. Compartida por el fast
    path (antes de `booking_limits.check`) y el chequeo post-insert más
    abajo — un solo lugar, para que un campo nuevo del shape de éxito no
    pueda desincronizarse entre los dos (2026-08-05 tier 14 fix)."""
    sid = fila["id"]
    out = {"ok": True, "id": sid, "estado": fila.get("estado"),
           "ai": fila.get("ai_json")}
    if fila.get("estado") == "aprobada" and fila.get("turno_dt"):
        out["turno_dt"] = bkr._iso_art(fila["turno_dt"])
    entrevista = bkr._entrevista_field(sid, slot_medico_id)
    if entrevista:
        out["entrevista"] = entrevista
    return out


def _with_interview(out, sid, slot_medico_id):
    """`out` plus the pre-consult interview offer, when there is one — the
    last field every success shape of `create_request` carries."""
    entrevista = bkr._entrevista_field(sid, slot_medico_id)
    if entrevista:
        out["entrevista"] = entrevista
    return out


def _idem_answer(idem_key, slot_medico_id):
    """`(200, success)` of the request this `idem_key` already created, or
    None when there is no key or no such request. `create_request` asks three
    times (fast path, limit refusal, lost race) and answers the same."""
    if idem_key:
        existente = store.get_booking_request_by_idem_key(idem_key)
        if existente is not None:
            return 200, _respuesta_idem_existente(existente, slot_medico_id)
    return None


def _birth_date(raw):
    """The ISO date of an account's or dependent's `nacimiento`, None when
    it is empty or malformed."""
    nac_raw = str(raw or "").strip()
    try:
        return date.fromisoformat(nac_raw[:10]) if nac_raw else None
    except ValueError:
        return None


def _step_identity(b):
    """Identity validated from the session: account holder or dependent.
    Sets u, dni, nombre, apellido, sexo, nacimiento, solicitante_dni."""
    u = bkr._user_dict(b.usuario)
    dni = _ids.dni(u.get("dni"))
    if not format.dni_valid_length(dni):
        return 400, {"error": i18n.t_or(
            "booking.error.invalid_account_dni", "la cuenta no tiene un DNI válido",
            i18n.active())}
    nombre = (u.get("nombre") or "").strip()
    apellido = (u.get("apellido") or "").strip()

    sexo = (str(u.get("sexo") or "").strip().upper()) or None
    if sexo is not None and sexo not in ("M", "F", "X"):
        sexo = None

    nacimiento = _birth_date(u.get("nacimiento"))

    solicitante_dni = dni
    paciente_dni = _ids.dni(b.body.get("paciente_dni"))
    if paciente_dni and paciente_dni != solicitante_dni:
        dependiente = store.get_dependent(solicitante_dni, paciente_dni)
        if not dependiente:
            return 403, {"error": "paciente_no_vinculado"}
        dni = _ids.dni(dependiente.get("dni"))
        nombre = (dependiente.get("nombre") or "").strip()
        apellido = (dependiente.get("apellido") or "").strip()
        sexo = (str(dependiente.get("sexo") or "").strip().upper()) or None
        nacimiento = _birth_date(dependiente.get("nacimiento"))
    b.u, b.dni, b.nombre, b.apellido = u, dni, nombre, apellido
    b.sexo, b.nacimiento, b.solicitante_dni = sexo, nacimiento, solicitante_dni
    return None


def _step_no_show(b):
    """Repeated no-shows: the limit protects only the web channel for the
    final clinical DNI (also when it's a dependent). Never blocks the
    front desk or staff, who don't go through this patient endpoint."""
    resumen_asistencia = store.attendance_summary_dni(b.dni, bkr._no_show_limit())
    if not resumen_asistencia.get("bloqueado_web"):
        return None
    # The desk that lifts it is a named place — the installation's main
    # center (responsable del producto 23.10) — never «el hospital o el centro de salud».
    from app.patient_web import site_centers
    place = site_centers.main_center_text()
    detalle = (i18n.t_or(
        "booking.error.no_show_blocked_detail_center",
        "Por tus ausencias recientes, este turno no se puede pedir por la web. "
        "Acercate a la admisión y te ayudamos: {place}.",
        i18n.active(), place=place) if place else i18n.t_or(
        "booking.error.no_show_blocked_detail",
        "Por tus ausencias recientes, este turno no se puede pedir por la web. "
        "Acercate al hospital o al centro de salud para que te ayudemos.",
        i18n.active()))
    return 403, {
        "error": "bloqueado_por_ausencias",
        "faltas": resumen_asistencia.get("faltas", 0),
        "limite": bkr._no_show_limit(),
        "detalle": detalle,
        "centro": site_centers.main_center_place(),
    }


def _step_fields(b):
    """What the patient typed, clamped, and the catalog references it names.
    Sets contacto, preferencia, nota_paciente, servicio_id, centro_slug,
    slot_medico_id, idem_key."""
    body = b.body
    # contact CAN come from the body (patient confirms/edits it in step 3)
    b.contacto = ((body.get("contacto") or b.u.get("contacto") or "").strip() or None)
    # time preference the patient picks after uploading the referral (day +
    # band); it's a preference, not a booked appointment — the hospital
    # honors it on confirmation. Clamped to a short text so we don't store
    # arbitrary payloads.
    b.preferencia = ((body.get("preferencia") or "").strip()[:120]) or None
    # «Contanos tu caso» (the reference-deployment counterpart, 2026-10-17): with no free slot, the patient
    # says why they can't wait and the desk contacts them to evaluate it.
    # Free text, clamped; only travels with a request that holds no slot.
    b.nota_paciente = (str(body.get("caso") or "").strip()[:MAX_PATIENT_NOTE]) or None
    # Since 2026-11-07 the case is a short chat (modules/intake/case_chat):
    # its token travels here and the chat moves into the request once it
    # exists (_bind_case_chat); `caso` still carries the patient's words.
    b.caso_chat = str(body.get("caso_chat") or "").strip()[:80] or None

    b.servicio_id = (body.get("servicio_id") or "").strip()
    b.centro_slug = (body.get("centro_slug") or "").strip()
    if not b.servicio_id:
        return 400, {"error": i18n.t_or(
            "booking.error.missing_service", "falta servicio_id", i18n.active())}

    # The doctor whose block the patient picked the slot from (catalog id,
    # optional). It ONLY feeds the pre-consult interview offer — a doctor's
    # own override can be ON while the system default is OFF, and without
    # this the success page could never know (the request has no assigned
    # doctor yet). It never assigns the appointment. Parsed here (not further
    # down) because the limit pre-check below also needs it (doctor → service
    # → site resolution) and idem_key's fast path right after does too.
    b.slot_medico_id = str(body.get("slot_medico_id") or "").strip() or None

    # Service, center and slot doctor must exist in the catalog (and belong
    # together): the database holds them as plain text, so this is the check.
    refusal = _check_catalog_refs(b.servicio_id, b.centro_slug, b.slot_medico_id)
    if refusal:
        return refusal
    b.idem_key = (body.get("idem_key") or "").strip() or None
    return None


def _step_idem_fast_path(b):
    """Confirmá idempotency, FAST PATH (2026-08-05 tier 14 fix, plan item 4;
    docs/roadmap/data-integrity-guards-plan.md #4): looked up BEFORE
    `booking_limits.check` on purpose. That pre-check doesn't know about
    `idem_key` — it counts every 'pendiente'/'aprobada' row for this DNI,
    INCLUDING the one this very idem_key already created, so a doubled tap
    or a client retry on a limite=1 service (most of them) got a 409
    "límite_turnos_activos" instead of the same success the first call
    already sent — reproducible even SEQUENTIALLY, no concurrency needed.
    A hit here returns that original response verbatim and skips every
    side effect below (referral file move, AI pass, notification) instead
    of repeating them on the same row — that's what the OLD post-insert
    `ya_resuelta` check (further down) couldn't guarantee for a losing
    thread of a real race, see `create_booking_request_with_limit`'s
    `_ya_existia` marker. Absent/empty idem_key (older clients, or a
    band-only submit) → no dedup, identical to before."""
    return _idem_answer(b.idem_key, b.slot_medico_id)


def _step_booking_limit(b):
    """How many appointments of this service the patient may hold at once
    (booking_limits.py): 1 for most specialties, more where a series is the
    normal case (kinesiología). Checked here, on the patient's own POST, and
    never on the panel's — the front desk is not subject to a web guardrail.
    It runs BEFORE taking the slot hold below: refusing after the soft-lock
    would park a slot nobody ends up using. This is a best-effort PRE-check
    (count in its own transaction, same as always) — it saves the common
    case from wasting a slot hold and a referral/AI pass, but it is NOT what
    closes the race between two concurrent POSTs from the same patient:
    that's `create_booking_request_with_limit` below, which counts and
    inserts under one advisory lock (2026-08-05 data-integrity guards,
    docs/roadmap/data-integrity-guards-plan.md #3).
    Sets slot_dia, slot_hora, limite_medico_id, limite_info."""
    from app.modules.turnos import booking_limits
    # Doctor-level `turnos_activos_max` overrides: when the patient picked a
    # concrete slot, the doctor for the limit check is the one who OWNS that
    # slot in the catalog — resolved server-side, never the client-sent
    # slot_medico_id (forgeable, and absent for wizard-picked slots). A slot
    # that resolves to no doctor (service-owned agenda, or unresolvable)
    # falls back to the service-level limit.
    b.slot_dia = (b.body.get("slot_dia") or "").strip() or None
    b.slot_hora = (b.body.get("slot_hora") or "").strip() or None
    b.limite_medico_id = (
        bkr._resolve_slot_doctor(b.servicio_id, b.slot_dia, b.slot_hora)
        if (b.slot_dia and b.slot_hora) else b.slot_medico_id)
    permitido, b.limite_info = booking_limits.check(b.dni, b.servicio_id, b.limite_medico_id)
    if permitido:
        return None
    # Re-check idem_key right before rejecting (2026-08-05 tier 14 fix):
    # under real concurrency, a sibling call with the SAME idem_key can
    # commit its insert in the window between our fast path above and
    # this best-effort count — since idem_key is unique, the ONLY row
    # that can push `abiertas` over the limit right now IS that sibling's
    # (this dni+servicio has no other traffic that idem_key didn't
    # already dedup), so it's always safe to treat a hit here as "that
    # was me" and answer success instead of a false 409.
    answer = _idem_answer(b.idem_key, b.slot_medico_id)
    if answer is not None:
        return answer
    info = b.limite_info
    return 409, _limit_reached_response(
        info.get("limite", 1), info.get("abiertas", 0),
        b.servicio_id, info.get("servicio_nombre"))


def _step_age_gate(b):
    """Server-side age gate: the client already validates it (edad.js),
    but an adult can't book pediatrics for themself by sending the POST
    directly. `nacimiento` here is already the FINAL one (account holder
    or dependent — the identity step resolved it)."""
    edad_cfg = (bkr._service_full(b.servicio_id) or {}).get("edad") or {}
    if _out_of_range(_patient_age_years(b.nacimiento), edad_cfg):
        return 400, {"error": "edad_fuera_de_rango"}
    return None


def _step_slot_hold(b):
    """Soft-lock of the chosen appointment (2026-07-16, see slot_holds.py):
    the wizard sends slot_dia/slot_hora/hold_token when the patient chose a
    concrete time (band-only still sends nothing here). Taking it is
    attempted BEFORE creating the request — if someone else just grabbed
    it, 409 and nothing gets created; the client picks again without
    losing the rest of the wizard. Sets hold_token and slot_dt (None for a
    band-only request). idem_key, slot_medico_id and slot_dia/slot_hora are
    already parsed by the steps above."""
    b.hold_token = (b.body.get("hold_token") or "").strip() or None
    b.slot_dt = None
    if not (b.slot_dia or b.slot_hora):
        return None
    if not (b.slot_dia and b.slot_hora and bkr._SLOT_DAY_RE.match(b.slot_dia)
            and bkr._SLOT_TIME_RE.match(b.slot_hora)):
        return 400, {"error": i18n.t_or(
            "booking.error.invalid_slot", "slot_dia/slot_hora inválidos",
            i18n.active())}
    b.slot_dt = bkr._slot_dt_art(b.slot_dia, b.slot_hora)
    if b.slot_dt is None:
        return 400, {"error": i18n.t_or(
            "booking.error.invalid_slot", "slot_dia/slot_hora inválidos",
            i18n.active())}
    from app.modules.turnos import booking_horizon
    horizon_error = booking_horizon.check_slot(
        b.servicio_id, b.slot_dia, b.slot_hora, doctor_id=b.limite_medico_id)
    if horizon_error:
        return 409, horizon_error
    try:
        from app.modules.turnos import slot_holds
        # A logged-in patient holds under the account's token, whichever
        # device sends (2026-11-15): the slot the desktop held is the phone's too.
        cuenta = slot_holds.owner_token(bkr._user_dict(b.usuario)) if b.usuario else None
        if cuenta and b.hold_token and b.hold_token != cuenta \
                and not b.hold_token.startswith("acct-"):
            try:
                store.hold_release(b.hold_token)   # its browser hold moves to the account
            except Exception:  # noqa: BLE001 — a leftover loose hold just expires
                pass
        b.hold_token = cuenta or b.hold_token or secrets.token_urlsafe(16)
        tomado = slot_holds.try_take(b.servicio_id, b.slot_dia, b.slot_hora, b.hold_token)
    except Exception:  # noqa: BLE001 — fail CLOSED (2026-08-05 data-integrity
        # guards, plan item 7): a slot_holds/DB hiccup is treated as NOT
        # acquired, never as silent success. Reverts the old "don't block
        # the booking" degrade — that let two patients past the soft-lock
        # at the exact moment a DB blip made the collision more likely,
        # not less; the patient just gets the normal retry solución below.
        tomado = False
    if not tomado:
        return 409, {"error": "turno_tomado"}
    return None


def _step_referral(b):
    """Resolve the referral: by token (analyzed earlier) or inline (legacy).
    Sets orden_token, orden_bytes, orden_mime, orden_filename, stored_ai,
    pending_path."""
    b.orden_token = (b.body.get("orden_token") or "").strip() or None
    b.orden_bytes, b.orden_mime, b.orden_filename = None, None, None
    b.stored_ai, b.pending_path = None, None

    if b.orden_token:
        meta = booking_referrals._load_pending(b.orden_token)
        if meta and meta.get("orden_path") and os.path.exists(meta["orden_path"]):
            b.pending_path = meta["orden_path"]
            b.orden_mime = meta.get("orden_mime")
            b.orden_filename = meta.get("orden_filename")
            b.stored_ai = meta.get("ai")
        # expired/nonexistent token -> continue without a referral (doesn't block)
    elif b.body.get("orden"):
        try:
            b.orden_bytes, b.orden_mime, b.orden_filename = booking_referrals._decode_referral(b.body.get("orden"))
        except ValueError as e:
            return 400, {"error": str(e)}
        if len(b.orden_bytes) > bkr.MAX_REFERRAL_BYTES:
            return 413, {"error": i18n.t_or(
                "booking.error.file_too_large", "el archivo es muy pesado",
                i18n.active())}
    return None


def _step_persist(b):
    """Persist the request: count-and-insert ATOMICALLY under an advisory
    lock scoped to (dni, servicio_id) — the real guard against the same
    patient's limit race, and (via idem_key) against a doubled Confirmá tap
    (2026-08-05 data-integrity guards, plan items 3-4). Sets fila and sid."""
    try:
        b.fila = store.create_booking_request_with_limit(
            b.dni, b.nombre, b.apellido, b.nacimiento, b.contacto, b.servicio_id, b.centro_slug,
            b.limite_info.get("limite", 1), orden_mime=b.orden_mime, sexo=b.sexo,
            preferencia_horario=b.preferencia, solicitante_dni=b.solicitante_dni,
            slot_medico_id=b.slot_medico_id, idem_key=b.idem_key,
            actor=bkr._patient_actor(b.dni, b.solicitante_dni),
            nota_paciente=None if b.slot_dt is not None else b.nota_paciente,
            # The appointment asked for outlives its hold (rejection and
            # cancellation delete the hold): «Mis solicitudes» still says
            # which one it was (the product owner 26.10).
            turno_pedido=b.slot_dt)
    except store.LimitReached as e:
        # Lost the race the best-effort check above missed: a sibling
        # request from the SAME patient took the last slot in the window
        # between that check and this atomic insert. Nothing was created —
        # release the slot hold this call took, since nothing will use it.
        if b.slot_dt is not None and b.hold_token:
            try:
                store.hold_release(b.hold_token)
            except Exception:  # noqa: BLE001
                pass
        # Belt-and-suspenders idem_key re-check (2026-08-05 tier 14 fix): the
        # lock-scoped re-check inside `create_booking_request_with_limit`
        # already makes THIS exception impossible for our own idem_key's
        # row, but a stray retry-of-the-retry landing here anyway should
        # still resolve to success rather than a false 409, same rule as the
        # other two idem_key checks above.
        answer = _idem_answer(b.idem_key, b.slot_medico_id)
        if answer is not None:
            return answer
        return 409, _limit_reached_response(
            e.limite, e.abiertas, b.servicio_id, b.limite_info.get("servicio_nombre"))
    b.sid = b.fila["id"]
    # `_ya_existia` (2026-08-05 tier 14 fix) is the RELIABLE signal, set by
    # `create_booking_request_with_limit` itself, for "this call fetched a
    # row that already existed" vs "this call's own INSERT won" — status
    # alone can't tell them apart: a losing thread of a real concurrent race
    # gets back the winner's row, and on a service without auto_confirmar
    # that row is STILL 'pendiente', identical to what a fresh insert looks
    # like (the old `fila.get("estado") != "pendiente"` heuristic missed
    # exactly this: a losing thread would fall through and re-run the rest
    # of `_create` — referral file move, AI pass, notification — on the
    # SAME row a second time).
    if b.idem_key is not None and bool(b.fila.get("_ya_existia")):
        # Confirmá idempotency (plan item 4): a doubled tap or a
        # post-timeout retry landed on a request that already finished
        # processing (referral, AI, auto-confirmation, notification) —
        # report its CURRENT state as success, with none of that repeated.
        return 200, _respuesta_idem_existente(b.fila, b.slot_medico_id)
    _bind_case_chat(b)
    return None


def _bind_case_chat(b):
    """«Contanos tu caso» chat → this new request (intake hook; module off
    or a failure → the request stands with its `caso` text alone). Only a
    request that holds no slot carries a case, same as its note."""
    if not b.caso_chat or b.slot_dt is not None:
        return
    try:
        from app.core import app_modules
        app_modules.hooks.call("case_chat_bind", b.caso_chat, b.sid, b.u)
    except Exception:  # noqa: BLE001 — the request was ALREADY created
        pass


def _step_link_hold(b):
    """The hold now blocks until the appointment's own time (a slot that's
    already past blocks nothing — see hold_link_booking_request). On
    approval, the request's turno_dt takes over the block; on
    rejection, the slot becomes free again (see _approve/_reject)."""
    if b.slot_dt is not None and b.hold_token:
        try:
            store.hold_link_booking_request(b.hold_token, b.sid, b.slot_dt)
        except Exception as exc:  # noqa: BLE001 — the request was ALREADY created, don't break here
            _lost_after_save("la reserva del horario no quedo atada a la solicitud", b, log_pii.exc(exc))
            launch_signals.handled("booking_hold_link_failed")
    return None


def _step_referral_file(b):
    """Put the referral file where the request owns it: move the pending
    one to its final name, or write the inline bytes. Sets orden_path."""
    sid = b.sid
    b.orden_path = None
    if b.pending_path:  # move the pending file to its final location
        os.makedirs(bkr.REFERRALS_DIR, exist_ok=True)
        b.orden_path = os.path.join(
            bkr.REFERRALS_DIR, f"{sid}.{booking_referrals._ext_for(b.orden_bytes, b.orden_mime, b.orden_filename)}")
        try:
            os.replace(b.pending_path, b.orden_path)
        except OSError:
            try:
                import shutil
                shutil.copyfile(b.pending_path, b.orden_path)
            except OSError as exc:
                b.orden_path = None
                _lost_after_save("la orden no se pudo guardar", b, log_pii.exc(exc))
                launch_signals.handled("booking_referral_failed")
        booking_referrals._discard_pending_meta(b.orden_token)
        if b.orden_path:
            try:
                store.set_referral(sid, b.orden_path, b.orden_mime)
            except Exception as exc:  # noqa: BLE001 — the request was ALREADY created, don't break here
                _lost_after_save("la orden no quedo atada a la solicitud", b, log_pii.exc(exc))
                launch_signals.handled("booking_referral_failed")
    elif b.orden_bytes is not None:  # inline (legacy/tests)
        os.makedirs(bkr.REFERRALS_DIR, exist_ok=True)
        b.orden_path = os.path.join(
            bkr.REFERRALS_DIR, f"{sid}.{booking_referrals._ext_for(b.orden_bytes, b.orden_mime, b.orden_filename)}")
        try:
            with open(b.orden_path, "wb") as f:
                f.write(b.orden_bytes)
            store.set_referral(sid, b.orden_path, b.orden_mime)
        except OSError as exc:
            b.orden_path = None
            _lost_after_save("la orden no se pudo guardar", b, log_pii.exc(exc))
            launch_signals.handled("booking_referral_failed")
    return None


def _step_analysis(b):
    """ai_json: reuse the saved analysis, or run 3 passes inline. Sets ai_json."""
    solicitud = {"dni": b.dni, "nombre": b.nombre, "apellido": b.apellido,
                 "servicio_id": b.servicio_id,
                 "especialidad": bkr._service_name(b.servicio_id),
                 "filename": b.orden_filename}
    if b.stored_ai is not None:
        # The token was created before login and didn't yet know the account
        # holder. Reconciling NOW avoids "Looks correct" against a
        # DNI/name that's visibly someone else's in the admin queue.
        b.ai_json = ai_referral.reconcile_with_request(b.stored_ai, solicitud)
    else:
        b.ai_json = ai_referral.analyze_referral_3pass(b.orden_path, b.orden_mime, solicitud)
    try:
        store.save_ai(b.sid, b.ai_json)
    except store.DbUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — the request was ALREADY created, don't break here
        _lost_after_save("la lectura de la orden no quedo guardada", b, log_pii.exc(exc))
        launch_signals.handled("booking_ai_save_failed")
    return None


def _step_auto_confirmation(b):
    """Automatic confirmation (hospital meeting 2026-07-24): a `solicitud`
    mode service with auto_confirmar (e.g. health check) skips the queue —
    the chosen appointment gets approved instantly. Requires a CONCRETE
    time (the soft-lock above already guaranteed it was free); with
    band-only it stays pending as always (someone still has to set the
    time). The email sent is the appointment-confirmed one, not "we
    received your request". If approval fails, the request stays
    pending — it's never lost.
    Same-day openings confirm themselves by definition: the listing opens
    today and the person goes today — there's no window for anyone to
    review it (the reference-deployment counterpart, 2026-07-24: "yo sé que voy a ir y ya lo saco,
    pero ese día" — I know I'm going and I'll just take it, same day).
    That's why it doesn't also need to be marked "confirmed by the
    hospital"."""
    from app.patient_web import site_availability
    sid, ai_json = b.sid, b.ai_json
    servicio_doc = bkr._service_full(b.servicio_id) or {}
    auto = bool(servicio_doc.get("auto_confirmar")) or site_availability._opens_same_day(servicio_doc)
    if not (b.allow_auto_confirmation and auto and b.slot_dt is not None):
        return None
    try:
        # The doctor of the slot the patient picked goes onto the turno
        # (the reference-deployment counterpart, 2026-10-24: a self-booked Cirugía turno appeared in
        # «Sin médico asignado» though she had chosen the doctor's slot).
        auto_medico, auto_medico_id = _auto_confirm_doctor(b.servicio_id, b.limite_medico_id)
        booking_decisions._persist_approval(sid, b.slot_dt, medico=auto_medico, medico_id=auto_medico_id,
                          resuelta_por="auto:web", slot_capacity=1)
        out = {"ok": True, "id": sid, "estado": "aprobada",
               "turno_dt": b.slot_dt.isoformat(), "ai": ai_json}
        # Approved: the doctor just put onto the turno decides the interview
        # offer, the same one its confirmation email asks about.
        return 200, _with_interview(out, sid, auto_medico_id)
    except (store.AlreadyProcessed, store.SlotConflict):
        # A concurrent duplicate of THIS exact request (same idem_key,
        # near-simultaneous retry) got approved — or lost the slot race —
        # first. Report its real current state instead of silently
        # mis-reporting "pendiente" and sending a second notification below.
        try:
            actual = store.get_booking_request(sid) or b.fila
        except Exception:  # noqa: BLE001 — worst case, report the pre-conflict row
            actual = b.fila
        out = {"ok": True, "id": sid, "estado": actual.get("estado") or "pendiente",
               "ai": ai_json}
        if actual.get("estado") == "aprobada" and actual.get("turno_dt"):
            out["turno_dt"] = bkr._iso_art(actual["turno_dt"])
        return 200, _with_interview(out, sid, b.slot_medico_id)
    except Exception:  # noqa: BLE001 — falls back to the normal pending path
        return None


def _answer_pending(b):
    """The request stays pending: tell the desk (never breaks the booking —
    it was ALREADY created) and answer 200."""
    try:
        from app.notifications import send as notifications
        notifications.notify_received(
            {"id": b.sid, "nombre": b.nombre, "apellido": b.apellido, "dni": b.dni,
             "solicitante_dni": b.solicitante_dni, "servicio_id": b.servicio_id,
             "centro_slug": b.centro_slug, "preferencia_horario": b.preferencia,
             "medico_asignado_id": b.slot_medico_id, "ai_json": b.ai_json},
            None)
    except Exception:  # noqa: BLE001 — the request was ALREADY created, never break here
        pass

    out = {"ok": True, "id": b.sid, "estado": "pendiente", "ai": b.ai_json}
    return 200, _with_interview(out, b.sid, b.slot_medico_id)


# What create_request runs, in order: each step reads and sets fields of the
# shared state and returns None to go on, or the (status, body) that ends
# the call. The answers and writes are pinned by test_create_request_steps.
_CREATE_STEPS = (
    _step_identity, _step_no_show, _step_fields, _step_idem_fast_path,
    _step_booking_limit, _step_age_gate, _step_slot_hold, _step_referral,
    _step_persist, _step_link_hold, _step_referral_file, _step_analysis,
    _step_auto_confirmation,
)


def create_request(usuario, body, *, allow_auto_confirmation=True):
    """Create a patient's booking from an already-resolved identity and body.

    This is the canonical transport-free operation used by the patient API,
    chat, WhatsApp and MCP. Notification links use the installation's pinned
    base URL; no HTTP request or handler-shaped object crosses this boundary.
    """
    state = SimpleNamespace(usuario=usuario, body=body,
                            allow_auto_confirmation=allow_auto_confirmation)
    for step in _CREATE_STEPS:
        answer = step(state)
        if answer is not None:
            return answer
    return _answer_pending(state)


def _auto_confirm_doctor(servicio_id, medico_id):
    """(name, id) of the catalog doctor an automatically confirmed web turno
    belongs to, or (None, None). `medico_id` is the slot's owner resolved on
    the server (else the id the wizard sent, which is forgeable): either way
    it only counts when that doctor is in the service's pool — the same rule
    the desk's approval verifies (`_doctor_pool`) — so a crafted id never puts
    a doctor of another specialty on a turno. Lab work has no doctor."""
    if not medico_id:
        return None, None
    try:
        from app.patient_web import catalog
        servicio = bkr._service_full(servicio_id)
        if not servicio or bkr._is_lab(servicio):
            return None, None
        medico = catalog.doctor_by_id(medico_id)
        if not medico or not bkr._doctor_pool(servicio, [medico]):
            return None, None
        return str(medico.get("nombre") or "") or None, str(medico["id"])
    except Exception:  # noqa: BLE001 — an unresolved doctor never blocks the confirmation
        return None, None
