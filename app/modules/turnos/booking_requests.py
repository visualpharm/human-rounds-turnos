"""booking_requests.py — Pinamar Turnos appointment requests: what the booking modules share.

The request backend is cut by action (TASK-60, 2026-11-06). Each route's domain function lives in the module
of its action, and booking_requests_router.py calls it there:
  booking_create      POST /api/requests (create_request and its steps)
  booking_patient     GET /api/requests?dni=NNN, GET /api/requests/<id>, POST /api/requests/<id>/cancel,
                      /reprogramar and /waitlist — the patient's own requests
  booking_referrals   POST /api/analyze-order, GET /api/orden-preview/<token>, GET /api/admin/order/<id>
                      (the file itself) and the referral retention sweep
  booking_queue       GET /api/admin/requests (the desk's queue), GET /api/admin/refeps
  booking_decisions   POST /api/admin/requests/<id>/approve, /assign, /reject, /cancel, /restore,
                      /campos, /patient-data, /attendance
  booking_emr         GET /api/admin/hsi-sync, POST /api/admin/hsi-sync/resolver and /conflicts,
                      POST /api/admin/requests/<id>/retry-hsi
  booking_capacity    «is this doctor free, is there room» for the approval, the panel and the reschedule
  panel_settings      GET/POST /api/admin/theme, /auth-mode, /turnstile

What stays here is used by more than one of them, or from outside: the service catalog and the doctor pool,
the session user, the public and admin row shapes, ART time, the staff access check, the interview CTA,
and the installation's paths and limits (REFERRALS_DIR, SERVICES_PATH, MAX_REFERRAL_BYTES — tests point
them elsewhere). The split modules call these as `bkr.<name>` at call time, so a test that patches a name
here reaches every caller. This module imports none of them and re-exports nothing: a caller imports the
module that owns the name, and a test patches the name where it lives.

Login/roles (user+password+2FA+Turnstile, role 'admin'/'operador') live in
admin_auth.py; operator CRUD lives in admin_users.py ('admin' role
only) — this module only consumes admin_auth.require_admin.

Certain specialties require a referral: the patient uploads a photo/PDF of
the order; an admin staff member reviews the queue and approves/rejects;
approving assigns an appointment and pushes it to HSI. An AI (Gemini Flash)
pre-analyzes the referral.

Everything lazily imports the heavy stuff (Postgres, HSI): importing this
module at server startup must NEVER fail.
"""

import os
import re
import sys
import json
from datetime import date, datetime

from app.modules.turnos import booking_requests_store as store
from app.auth import admin_auth
from app import connectors
from app.core import sitedata
from app.core import paths
from app.core import config
from app.core import format
from app.core import i18n
from app.core import tz as _tz
from app.core import ids as _ids

# Tier 2 batch D: this file moved from repo root into modules/turnos/, two
# directories deeper — three dirname() calls to reach the repo root now.
# REFERRALS_DIR must still resolve to the repo's real shared, gitignored
# data/ordenes directory — not a new nested copy under modules/turnos/.
ROOT = paths.PROJECT_ROOT
REFERRALS_DIR = os.path.join(ROOT, "data", "ordenes")
SERVICES_PATH = sitedata.path("servicios.json")
_DEFAULT_SERVICES_PATH = SERVICES_PATH  # original value, BEFORE a test monkeypatches it

# Tier 2 batch D dependency-direction fix (see notifications/send.py's own
# comment): notifications/ is a domain package and must never statically
# import a module, so this module registers ITSELF there instead — the
# allowed direction (a module importing a domain package). Runs once, at
# import time, which happens unconditionally at boot regardless of the
# turnos module's enabled/disabled state (app_modules._validate_routes()
# always imports every manifest's route modules).
from app.notifications import send as _notifications_send
_notifications_send.register_turnos(booking_requests=sys.modules[__name__])

# ~25 MB. Images already arrive downsized from the client (wz-upload.fileToOrden:
# ≤2000px, JPEG 0.85), so this cap only affects PDFs, which pass through as-is:
# a multi-page scan from a doctor's office can easily weigh over 10 MB. Having
# someone's referral bounce because of file size isn't a problem for them
# (responsable del producto, 2026-07-20: "si el usuario se confunde el tamaño, no es gran cosa,
# tratalo como normal" — if the user gets the size wrong, it's no big deal,
# treat it as normal) — the cap exists only so a giant POST doesn't eat up the
# process's memory.
MAX_REFERRAL_BYTES = 25 * 1024 * 1024

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)

# Slot soft-lock (2026-07-16, see slot_holds.py): a slot chosen in the
# wizard travels to booking_create as slot_dia/slot_hora/hold_token; taking it
# is attempted BEFORE creating the request (so no one else ends up with a slot
# that was just rejected via a 409) and the hold gets linked to the
# just-created request. The patient's reschedule reads the same two fields.
_SLOT_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLOT_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _slot_dt_art(dia, hora):
    """tz-aware (ART) datetime for an already-validated slot_dia/slot_hora, or
    None if the format didn't resolve to a real date/time (e.g. 2026-02-30)."""
    try:
        y, mo, d = (int(x) for x in dia.split("-"))
        h, mi = (int(x) for x in hora.split(":"))
        return datetime(y, mo, d, h, mi, tzinfo=_ART)
    except ValueError:
        return None


# ---- env / config ----------------------------------------------------------

def _env(key, default=""):
    return config.get(key, default)


def _emr_sync_enabled():
    """Whether this request transition may create EMR state at all.

    emr_sync is explicit-only: no stored connector, the off sentinel, and an
    invalid connector id all mean the local booking is authoritative. Keep
    the legacy TURNOS_SKIP_HSI emergency switch as a stronger no-egress and
    no-journal gate for an explicitly selected connector.
    """
    if _env("TURNOS_SKIP_HSI") == "1":
        return False
    return bool(connectors.resolve(connectors.EMR_SYNC)[0])


def _no_show_limit():
    """Current no-show count that blocks only the web sign-up.

    The operational agreement was the 2nd/3rd no-show; the config defaults
    to 3 and is clamped to a small range so an environment typo can never
    block the whole population or disable the safeguard.
    """
    try:
        return max(1, min(10, int(_env("TURNOS_NO_SHOW_LIMIT", "3"))))
    except (TypeError, ValueError):
        return 3


# ---- services (editable catalog via catalog.py — tolerate its absence) ----

def _dict_from_items(items):
    out = {}
    if isinstance(items, dict):
        for k, v in items.items():
            out[str(k)] = (v.get("nombre") or v.get("name") or str(k)) if isinstance(v, dict) else str(v)
    elif isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                sid = str(it.get("id") or it.get("slug") or it.get("servicio_id") or "")
                if sid:
                    out[sid] = it.get("nombre") or it.get("name") or sid
            elif isinstance(it, str):
                out[it] = it
    return out


def _services_from_path(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    items = data.get("servicios", data) if isinstance(data, dict) else data
    return _dict_from_items(items) or None


def _services():
    """Dict servicio_id -> name, or None if there's no catalog yet. Goes
    through catalog.py (the admin's editable overlay on top of the base
    JSON) when SERVICES_PATH still points at the real file; if a test
    monkeypatches it to its own catalog (see test_booking_requests.py) or
    catalog.py blows up for whatever reason, it falls back to reading the
    pointed-at file directly — same behavior as always."""
    if SERVICES_PATH == _DEFAULT_SERVICES_PATH:
        try:
            from app.patient_web import catalog
            out = _dict_from_items(catalog.services())
            if out:
                return out
        except Exception:  # noqa: BLE001
            pass
    return _services_from_path(SERVICES_PATH)


def _service_name(servicio_id):
    cat = _services()
    if cat and servicio_id in cat:
        return cat[servicio_id]
    # A service deleted after the appointment was booked (or a row from
    # another data package) must still READ as a service, never as its id.
    try:
        from app.patient_web import catalog
        return catalog.service_name(servicio_id) or str(servicio_id or "")
    except Exception:  # noqa: BLE001
        return str(servicio_id or "")


def _service_valid(servicio_id):
    """True only for a service the catalog lists. A catalog that cannot be
    read refuses too (schema review №10, 2026-10-24): accepting «any id» when
    the read failed stored references to services nobody could ever resolve."""
    cat = _services()
    if cat is None:
        return False
    return servicio_id in cat


def _service_full(servicio_id):
    """Full service record (includes `edad`), or {} if not found. Same
    dual pattern as _services(): catalog.py when SERVICES_PATH is still the
    real one; if a test monkeypatches it, reads the pointed-at file directly
    (without touching catalog/DB)."""
    if SERVICES_PATH == _DEFAULT_SERVICES_PATH:
        try:
            from app.patient_web import catalog
            for it in catalog.services() or []:
                if isinstance(it, dict) and str(it.get("id")) == str(servicio_id):
                    return it
        except Exception:  # noqa: BLE001
            pass
        return {}
    if not os.path.exists(SERVICES_PATH):
        return {}
    try:
        with open(SERVICES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    items = data.get("servicios", data) if isinstance(data, dict) else data
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and str(it.get("id") or it.get("slug")) == str(servicio_id):
                return it
    return {}


def _resolve_slot_doctor(servicio_id, slot_dia, slot_hora):
    """Catalog id of the doctor who actually owns a concrete (dia, hora)
    slot, or None when no doctor's agenda for this service generates it.

    The wizard's slot chips don't carry the doctor, and a crafted POST can
    send any `slot_medico_id` — so the doctor-level `turnos_activos_max`
    override must never be keyed off a client-sent id. Resolution mirrors
    `_next_appointment_best`: the service's own agenda first (a slot offered
    there has no doctor-level override by construction → None), then each
    doctor of the same cluster/center whose service-filtered agenda
    (`doctor_windows.for_service`) generates that slot via `_agenda_slots`.
    """
    try:
        from app.patient_web import catalog
        medicos = catalog.doctors() or []
    except Exception:  # noqa: BLE001 — no catalog → nothing to resolve
        medicos = []
    servicio = _service_full(servicio_id) or {}
    from app.modules.turnos import doctor_windows, schedule_policy
    if schedule_policy.has_schedule(servicio):
        return None
    # Resolve the actual calendar boundary, independently of public horizon/chip caps.
    try:
        date.fromisoformat(str(slot_dia))
    except (ValueError, TypeError):
        return None
    for m in schedule_policy.pool(servicio, medicos, keep=schedule_policy.offering):
        # The 12-chip display cap must not change server ownership.
        for agenda in doctor_windows.for_service(m, servicio):
            franjas = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
            from app.modules.turnos import doctor_appointments
            if slot_hora in doctor_appointments._minutes_of_day(franjas, agenda.get("excepciones") or [], str(slot_dia)):
                return m.get("id")
    return None


# ---- session user (accounts.py authors it in parallel) ---------------------

def _get_user(request):
    """Logged-in session user, or None. Deliberate indirection: accounts.py
    is written by another agent, so the import is lazy (ImportError -> None)
    and tests can monkeypatch `booking_requests._get_user`."""
    # MCP connector (mcp_api.py): the transport has already authenticated via
    # Bearer token and brings the resolved user — same identity-from-session,
    # a different door.
    mcp = getattr(request, "_usuario_mcp", None)
    if mcp:
        return mcp
    try:
        from app.cuentas import accounts
    except Exception:  # noqa: BLE001  (module missing or broken)
        return None
    try:
        return accounts.current_user(request)
    except Exception:  # noqa: BLE001
        return None


def _user_dict(usuario):
    """Normalizes the session user to a dict (supports dict or object)."""
    if usuario is None:
        return {}
    if isinstance(usuario, dict):
        return usuario
    out = {}
    for k in ("dni", "nombre", "apellido", "nacimiento", "sexo", "contacto"):
        v = getattr(usuario, k, None)
        if v is not None:
            out[k] = v
    return out


def session_dni(usuario):
    """Normalize a resolved session identity to its DNI, or return None
    when there is no session. Native endpoints resolve the identity first and
    pass it here, keeping this domain boundary free of request objects."""
    if not usuario:
        return None
    return _ids.dni(_user_dict(usuario).get("dni"))


# ---- JSON-safe serialization ------------------------------------------------

def _jsonable(row):
    """Converts a record (dict_row) to something serializable by json.dumps."""
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _iso_art(dt):
    """Aware datetime (any timezone — the process can run with the system
    TZ, America/New_York has been seen in dev) -> isoformat in Buenos Aires
    wall-clock time. Repo invariant (same criterion as
    calendario._dt_local): what the patient reads is ALWAYS the ART time,
    never the raw offset the Postgres column hands back. Without this, a
    turno_dt saved at -04:00 would get serialized with its literal time
    (09:30) instead of the real time the patient sees (10:30 ART)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat()
    return dt.astimezone(_ART).isoformat()


def _appointment_has_elapsed(row, now=None):
    """Display history from a concrete date, never from clinical status/age.

    Native and imported projections currently expose a start, not a reliable
    end. Use that start; a date without a time is past only after that day.
    A hold's expiry is not the appointment time and must not stand in for it.
    """
    current = now or datetime.now(_ART)
    current = current.astimezone(_ART) if current.tzinfo else current.replace(tzinfo=_ART)
    value = row.get("turno_dt")
    if not value:
        hold = row.get("hold") or {}
        day, hour = hold.get("dia"), hold.get("hora")
        value = f"{day}T{hour}" if day and hour else day
    if not value:
        return False
    try:
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return datetime.fromisoformat(value).date() < current.date()
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            return False
        when = value.astimezone(_ART) if value.tzinfo else value.replace(tzinfo=_ART)
        return when <= current
    except (ValueError, TypeError):
        return False


# `appointment_events.actor` of a patient-side cancellation (`_patient_actor`); any other named actor
# is the desk's username.
_PATIENT_ACTORS = ("paciente", "solicitante")

# The patient-facing text of a cancellation, by who cancelled (the product owner, 2026-10-26): one key per case, the
# same keys the account page (account-requests.mjs) reads — the assistant's status text and the desk
# email use them too. «Cancelada» alone when the data does not say who: never guessed.
CANCELLED_LABELS = {
    "paciente": ("account.mine.requests.status_cancelled_by_patient", "Cancelada por vos"),
    "centro": ("account.mine.requests.status_cancelled_by_center", "Cancelada por el centro"),
    None: ("account.mine.requests.status_cancelled", "Cancelada"),
}


def cancelled_by(actor):
    """Who cancelled, from the `actor` of the cancellation event: "paciente" (the patient, or the adult
    acting for them — "No voy" in a reminder is the same path), "centro" (a named desk user), or None
    when it is not recorded (cancelled before `appointment_events`, or no actor)."""
    actor = str(actor or "").strip()
    if not actor:
        return None
    return "paciente" if actor in _PATIENT_ACTORS else "centro"


def _public_row(row):
    r = _jsonable(row)
    out = {k: r.get(k) for k in
           ("id", "servicio_id", "centro_slug", "estado", "turno_dt",
            "preferencia_horario", "motivo_rechazo", "created")}
    out["turno_dt"] = _iso_art(row.get("turno_dt"))
    # The appointment the request asked for (Alembic 0037): what a rejected or
    # cancelled request still shows once its hold is gone (the product owner 26.10).
    out["turno_pedido"] = _iso_art(row.get("requested_at"))
    # medico_asignado also in the LIST (not just the detail): "My
    # appointments" shows doctor + time in the same cell when the request
    # has it (the product owner, 2026-07-18 — used to only show up in /request/<id>, the
    # table left it out).
    # …in normal capitalization: HSI and the front desk's Caps Lock both hand
    # us "ALEJO VARELA", and a shouted name in a card is noise (the product owner,
    # 2026-08-01). format.person_name only touches all-caps/all-lowercase.
    # The catalog's current name behind the id wins (2026-10-23): a renamed
    # doctor reads the same here as everywhere else.
    try:
        from app.modules.turnos import doctor_appointments
        out["medico_asignado"] = format.person_name(doctor_appointments.display_name(r)) or None
    except Exception:  # noqa: BLE001 — never break the card over a name
        out["medico_asignado"] = format.person_name(r.get("medico_asignado"))
    # catalog id to resolve the doctor's photo (2026-07-19, "Your next
    # appointment" card). Can be missing (old rows, or name matching found
    # nothing on approval) — the client falls back to an initials avatar in
    # that case, never breaks the card.
    out["medico_asignado_id"] = r.get("medico_asignado_id")
    out["paciente_dni"] = r.get("dni")
    out["paciente_nombre"] = " ".join(
        x for x in (r.get("nombre"), r.get("apellido")) if x).strip()
    # How to prepare (R4, patient-flow-design rule 8): only the list of
    # already-matched studies that /api/analyze-order left behind — never the
    # rest of ai_json (fields read from ANOTHER patient's referral are of no
    # use to anyone here, and there's no need to expose them).
    ai = r.get("ai_json") or {}
    out["preparacion"] = ai.get("preparacion") or []
    # Hold of the chosen appointment (2026-07-18): "My appointments" needs to
    # know whether a PENDING request already has a concrete date/time
    # (soft-lock, see slot_holds.py) to decide the button (Change time vs
    # Choose time) and whether the "Add to calendar" link applies — without
    # this, it was only knowable from the free-text preference, which can be
    # a band ("Morning") with no real date. Best-effort: never breaks the
    # list if the hold can't be read.
    out["hold"] = None
    if out.get("estado") == "pendiente" and out.get("id") is not None:
        try:
            out["hold"] = store.hold_of_booking_request(out["id"])
        except Exception:  # noqa: BLE001
            out["hold"] = None
    out["turno_pasado"] = _appointment_has_elapsed(out)
    if out.get("estado") == "cancelada":
        # The list rows carry the actor of the cancellation event; a row read on its own does not.
        try:
            actor = row["cancelled_actor"] if "cancelled_actor" in row else store.cancel_actor(out.get("id"))
        except Exception:  # noqa: BLE001 — never break the card over who cancelled
            actor = None
        out["cancelada_por"] = cancelled_by(actor)
    # Entrevista previa también a nivel de LISTA (my-requests.js ya sabe
    # pintar el botón cuando la fila trae `entrevista`): sólo en aprobadas —
    # las pendientes ofrecen la CTA en "Solicitud enviada" y en el detalle.
    if out.get("estado") == "aprobada" and out.get("id") is not None:
        entrevista = _entrevista_field(
            out["id"], r.get("medico_asignado_id") or r.get("slot_medico_id"),
            incluir_estado=True)
        if entrevista:
            out["entrevista"] = entrevista
    return out


def _admin_row(row):
    r = _jsonable(row)
    if r is None:
        return None
    r.pop("orden_path", None)  # don't expose the on-disk path
    # The key of the patient's confirm/calendar links (their authentication,
    # a secret in personal_data.REGISTRY): no staff screen reads it.
    r.pop("confirm_token", None)
    # The wizard's idempotency key of the booking POST (also a secret there):
    # the server reads it from the database, never from this row.
    r.pop("idem_key", None)
    # Who cancelled (same source as `_public_row`: the actor of the «cancelada» event). The list rows
    # carry it from the query; a row handed back after an action reads it on its own.
    r.pop("cancelled_actor", None)
    if r.get("estado") == "cancelada":
        try:
            actor = row["cancelled_actor"] if "cancelled_actor" in row else store.cancel_actor(r.get("id"))
        except Exception:  # noqa: BLE001 — the queue never breaks over who cancelled
            actor = None
        r["cancelada_por"] = cancelled_by(actor)
    # The slot the patient asked for is the patient's «Mis solicitudes» datum
    # (Alembic 0037); the desk keeps reading turno_dt and the hold, so the
    # panel's answer stays the one its route contracts pin.
    r.pop("requested_at", None)
    # Same invariant as _public_row: the panel reads the installation's wall
    # clock, never the offset of the Postgres session that fetched the row.
    # Callers that already serialized the row (the approve path hands a dict
    # whose turno_dt is a string) keep what _jsonable produced.
    if isinstance(row.get("turno_dt"), datetime):
        r["turno_dt"] = _iso_art(row.get("turno_dt"))
    if r.get("id") is not None:
        r["orden_url"] = f"/api/admin/order/{r['id']}"
        # The truth is the DISK, not the column: an old request can have
        # orden_path pointing at a file that's no longer there (volume
        # recreated, row seeded with no attachment). If we still said
        # tiene_orden=True, the panel would request the image and paint the
        # 404's JSON inside the card.
        _p = row.get("orden_path")
        r["tiene_orden"] = bool(_p) and os.path.exists(_p)
    # sin_medico (2026-07-19): lab work has no doctor, so the panel can hide
    # the selector without duplicating the detection (_is_lab) on the
    # client. catalog.services() is cached (3 s), cheap per row.
    try:
        r["sin_medico"] = _is_lab(_service_full(row.get("servicio_id")))
    except Exception:  # noqa: BLE001 — the queue never breaks over this
        r["sin_medico"] = False
    # medico_nombre (2026-10-23): the doctor this request belongs to, named
    # from the catalog by id — a slot-only request carries no free-text name,
    # and a disabled doctor is gone from every public list, so the panel's
    # filter showed «id:…» for both.
    try:
        from app.patient_web import catalog
        r["medico_nombre"] = format.person_name(catalog.doctor_name(
            r.get("medico_asignado_id") or r.get("slot_medico_id")))
    except Exception:  # noqa: BLE001 — the queue never breaks over this
        r["medico_nombre"] = ""
    # servicio_sugerido (2026-07-27): when the referral is for ANOTHER
    # specialty, the panel opens and prefills "Refer to" with the service the
    # referral asks for, instead of leaving the operator to search for it by
    # hand among 120 options. remedies.py computes it — the SAME function that
    # builds the link in the patient's email, so the operator refers to
    # wherever the email promised.
    try:
        from app.modules.turnos import remedies
        sug = remedies.suggested_service(r)
        if sug:
            r["servicio_sugerido"] = sug
    except Exception:  # noqa: BLE001
        pass
    return r


def staff_referral_row(identity, sid):
    """`_staff_row_or_error` for GET /api/admin/order/<id>: the referral file
    is clinical data, so the open — or the refused attempt on a request this
    account can't work — lands in the clinical access log (K10)."""
    row, err = _staff_row_or_error(identity, sid)
    vista = row if row is not None else store.get_booking_request(sid)
    if vista:
        from app.core import access_log
        access_log.record(identity, "orden", vista.get("dni"), target_id=sid,
                          allowed=err is None)
    return row, err


def _staff_row_or_error(identity, sid):
    """Pure domain function behind the "admin/admission may work any
    request; doctors only their assigned one" rule: typed `identity`
    (already resolved by the caller) and `sid` in, `(row, None)` on success
    or `(None, (status, payload))` for the native router to render."""
    row = store.get_booking_request(sid)
    if not row or not staff_can_see(identity, row):
        return None, (404, {"error": i18n.t_or("admin.srv.error.solicitud_no_encontrada", "solicitud no encontrada", i18n.active())})
    return row, None


def staff_can_see(identity, row):
    """The rule behind `_staff_row_or_error`, on a row already read: admin and
    admission see every request, a doctor only the ones assigned to them, and
    a center-scoped account (2026-08-31, same guard as `_mark_attendance_core`)
    only its centers. What an account cannot see does not exist for it (404)."""
    if identity.get("rol") == "medico":
        from app.core import db_clinical
        from app.modules.turnos import doctor_appointments
        medico_id = store.operator_doctor_id(identity["usuario"])
        medico = doctor_appointments.doctor_of(medico_id) if medico_id else None
        if row.get("estado") in db_clinical.DOCTOR_DEAD_STATUSES:
            return False
        if (not medico_id or
                not doctor_appointments._belongs_to(
                    row, medico_id, (medico or {}).get("nombre") or "")):
            return False
    centros_ok = admin_auth.centros_permitidos(identity)
    if centros_ok is not None and (row.get("centro_slug") or "") not in centros_ok:
        return False
    return True


# ---- POST /api/analyze-order (precedes registration, no auth, no DB) ------

def _json_invalido(e):
    """The 'malformed JSON body' error, patient-facing paths only — a broken
    client, not something a real visitor triggers by hand, but still worth
    saying in their language. `e`'s own text stays as the parser produced it
    (arbitrary, not ours to translate); only our own prefix is localized."""
    return f"{i18n.t_or('booking.error.invalid_json', 'JSON inválido', i18n.active())}: {e}"


def _json_invalido_admin(e):
    """SAME shape as the admin-side inline `i18n.t_or('admin.srv.error.
    json_invalido', ...)` calls scattered across every admin POST handler in
    this file — extracted once (Tier 4 slice 8, third batch native
    migration) so the FastAPI router can build the identical error payload
    without duplicating the literal key/string. Pure text, no handler
    touched."""
    return i18n.t_or("admin.srv.error.json_invalido", "JSON inválido: {motivo}",
                      i18n.active(), motivo=e)


def _entrevista_field(sid, doctor_id=None, incluir_estado=False):
    """{"ofrecer","url"} (+ "completada" on the detail) for the pre-consult
    interview CTA (phase 2) — None when the app module is disabled, the
    doctor is unknown (none given, or not in the catalog) or that doctor's
    effective toggle (own override or system default) is off, so callers
    just do `if resp.entrevista?.ofrecer`. The gate and the CTA are asked
    to intake by NAME ("interview_offer_for_request", registered by
    modules/intake/app.py); hooks.call() itself filters by the module being
    enabled right now, so its "no result" is this None. Best-effort: intake
    being absent/broken must never break a booking response."""
    try:
        from app.core import app_modules
        from app.patient_web import catalog
        res = app_modules.hooks.call(
            "interview_offer_for_request", sid,
            catalog.doctor_doc(doctor_id, tolerant=True), incluir_estado)
        return res[0] if res else None
    except Exception:  # noqa: BLE001
        return None


def _entrevista_patient_token(sid):
    """`patient_token` for an EMAIL/WhatsApp link (2026-08-03: "click →
    straight into the interview chat, no login" — the page used to demand a
    session, which meant a link opened on a phone bounced to a login
    screen). Lazily creates (or reuses) the interview so the outbound
    message can carry the same opaque-token-is-authentication the chat
    routes already trust (`interviews.patient_token`, same model as
    `appointment_offers.token`/`booking_requests.confirm_token` — see
    docs/architecture/notificaciones-y-lista-espera.md). Only ever called
    from notifications.py's outbound-message builders, always AFTER
    `_entrevista_field` said yes (that gate is what keeps this dark while
    intake is off — the "interview_patient_token_for_request" hook won't
    fire either): `_entrevista_field` above (the in-app JSON response, read
    by an already-logged-in patient) never eagerly creates the row, so
    "created on first visit" still holds for anyone just browsing their own
    requests — this is the one deliberate exception, same spirit as the
    confirm-token minted at approval time. Best-effort: None on any failure
    (missing row, DB down) — the caller degrades to a session-only link,
    same contract as the rest of this module."""
    try:
        from app.core import app_modules
        res = app_modules.hooks.call("interview_patient_token_for_request", sid)
        return res[0] if res else None
    except Exception:  # noqa: BLE001
        return None


from app.modules.turnos import schedule_policy as _schedule_policy

_norm_specialty = _schedule_policy.norm_specialty


def _is_lab(servicio):
    """True if `servicio` (full dict, as _service_full returns it) is
    lab/blood-draw work — there a doctor is NEVER assigned (the product owner,
    2026-07-19): the staff is the roster's `rol:true` "Técnico/a"
    (technician), never a scheduled professional. Decided by text (name +
    keywords, accents stripped) instead of a hand-maintained id list, so a
    future lab service is covered without touching code."""
    if not isinstance(servicio, dict):
        return False
    textos = [servicio.get("nombre")]
    textos.extend(servicio.get("palabras") or [])
    for t in textos:
        t = _norm_specialty(t)
        if "laborator" in t or "extracc" in t:
            return True
    return False


# ---- médico ↔ servicio: UNA sola regla (2026-10-23) ----------------------------
#
# Mapa del hospital (desk.json d10, problems.md №2): una solicitud de
# Ecocardiograma sin cardiólogo cargado salía con un traumatólogo preelegido —
# el panel tenía su propia regla (raíz de la especialidad) y, sin coincidencias,
# ofrecía el catálogo entero; el servidor aprobaba con cualquier nombre. La
# regla es la del paciente al reservar, `schedule_policy.belongs` (etiqueta de
# la franja, o cluster + centro): el panel recibe el pool por servicio en
# /api/admin/requests y la aprobación lo verifica acá.

def _doctor_pool(servicio, medicos):
    """Médicos del catálogo que la mesa puede asignar a `servicio`: los de su
    pool (`belongs`), sin borrados, filas-rol ni médicos demo — el mismo corte
    que `_suggested_doctor` (`schedule_policy.pool`), para que lo sugerido esté
    siempre en la lista."""
    from app.modules.turnos import schedule_policy
    if not servicio:
        return []
    return schedule_policy.pool(servicio, medicos)


def _doctor_serves_service(medico, servicio):
    """¿El médico NOMBRADO (texto libre del panel) está en el pool del
    servicio? Un nombre exacto (sin acentos ni mayúsculas) se juzga sólo por
    los médicos que lo llevan; si no hay exacto, por la coincidencia tolerante
    de `doctor_appointments._name_matches`. Un nombre que no es de ningún
    médico del catálogo no atiende nada. Servicio desconocido o catálogo
    ilegible → None (no se puede juzgar: no se bloquea, como antes)."""
    if not servicio:
        return None
    try:
        from app.patient_web import catalog
        from app.modules.turnos import doctor_appointments as da
        medicos = [m for m in (catalog.doctors() or [])
                   if isinstance(m, dict) and not m.get("_borrado") and m.get("nombre")]
    except Exception:  # noqa: BLE001
        return None
    exactos = [m for m in medicos if da._norm(m["nombre"]) == da._norm(medico)]
    candidatos = exactos or [m for m in medicos if da._name_matches(medico, m["nombre"])]
    return bool(_doctor_pool(servicio, candidatos))


def _patient_actor(patient_dni, session_dni):
    """`appointment_events.actor` for a patient-side action: "paciente" when
    the account acts on its own booking, "solicitante" when a guardian acts
    on a dependent's. Never the DNI itself — the row already says whose it
    is, and an actor column holding DNIs would be one more place a DNI
    change has to reach."""
    return "paciente" if not session_dni or session_dni == patient_dni else "solicitante"
