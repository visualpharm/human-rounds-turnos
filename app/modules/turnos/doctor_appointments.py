"""doctor_appointments.py — "My appointments": the doctor's own view (Pinamar Turnos).

Owner of this module:
  GET /api/admin/my-appointments?days=14    the session doctor's future
                                             approved appointments, groupable
                                             by day on the client
                                             (admin-my-appointments.js).
  GET /api/admin/my-appointments/feed       {url, hint} of the logged-in
                                             doctor's ICS feed: the full url
                                             only when this call issued the
                                             token (the DB keeps its hash),
                                             else just its last 4 chars; the
                                             public feed lives in
                                             calendar_feed.py:
                                             GET /feed/doctor/<token>.ics.
  GET /api/admin/doctors/<id>/appointments?days=N same shape as
                                             /my-appointments but for
                                             ANY doctor in the catalog,
                                             explicit id in the path — the
                                             doctor editor's "Calendar" tab in
                                             admin-content.js consumes it.
  GET /api/admin/doctors/stats              MANAGEMENT metrics per doctor
                                             (hours/week, next day,
                                             occupancy/appointments in 14
                                             days, has photo/login, license
                                             number, service count) for the
                                             admin's #medicos listing —
                                             admin-content.js consumes it in
                                             parallel with the base listing
                                             (/api/admin/content/doctors,
                                             owned by admin_content.py) and
                                             merges by id. admin and operator.
  GET /api/admin/day-queue?dia=YYYY-MM-DD   "Today's patients" (2026-07-24):
       [&medico=<id>]                       DAY's approved appointments (past
                                             and future of the day, not just
                                             what's left) — a doctor sees only
                                             their own in a single group; an
                                             admin/operator sees ALL of them
                                             grouped by doctor + a no-doctor
                                             group (lab work).
                                             `dia` defaults to today in ART.
  GET /api/admin/my-appointments/<id>/order the doctor requests THEIR OWN
                                             attached referral from the day
                                             queue (streaming shared with
                                             booking_requests._admin_referral via
                                             booking_requests.stream_referral).

Who can come in:
  - `/api/admin/my-appointments*` — ONLY a 'medico'-role session
    (admin_auth.require_admin with permitir_medico=True, then this module
    enforces the exact role — an admin or operator hitting this also gets
    403, because this view makes no sense for them: they have no medico_id).
  - `/api/admin/doctors/<id>/appointments` and `/api/admin/doctors/stats` —
    admin and operator (2026-07-17 meeting: admin staff manage the
    day-to-day agendas, so they see the management listing and each
    doctor's calendar; a 'medico' still gets 403 from the central gate).
  - `/api/admin/day-queue` — admin, operator AND medico (permitir_medico=True,
    the ONLY endpoint in this module open to all three roles: 2026-07-17
    gave the operator the catalog's day-to-day, and 2026-07-24 gave the
    doctor the day queue too, who used to only see future appointments).
The rest of the admin surface (requests queue, content, users, reports,
doctor photos) is already closed to 'medico' by the central gate in
admin_auth.require_admin.

Matching doctor ↔ request (see CLAUDE.md "Datos" — `medico_asignado` is
free text the operator picks from a <select> on approval, NOT an id):
  1. Fast path: `solicitudes.medico_asignado_id` (an additive column that
     booking_requests._approve tries to resolve with resolve_doctor_id() from
     here, saved forward, on approval).
  2. Fallback: name-token matching (same criterion as
     ai_referral._name_matches / scripts/import_detalle_turnos.py) against
     `medico_asignado` — covers rows approved BEFORE the _id column existed,
     or where the name didn't match unambiguously.

Import-safe: only stdlib + db/catalog/admin_auth (all already import-safe by
their own design). Must never break server startup.

Native-migration split (2026-08-26, docs/roadmap/native-migration-campaign-
20260826.md family F7): every GET below now has its business logic in a pure
`*_data(...)` function — parsed arguments in (already-resolved `identity`,
path/query values), a JSON-able dict out, or a raised `DomainError(status,
body)`. `doctor_appointments_router.py` calls those domain functions from
real `async def` FastAPI endpoints and builds the native response from their
returned value or raised error. The domain layer is the one behavior source.
"""

import os
import re
from datetime import datetime, timezone, timedelta

from app.core import clock
from app.core import db_booking
from app.core import db_operators
from app.core import db_clinical
from app.core import db_hsi
from app.core import db_slots
from app.core import patient_contact
from app.auth import admin_auth
from app.core.status_error import BodyStatusError
from app.core import text_fold
from app.core import tz as _tz


class DomainError(BodyStatusError):
    """Carries the exact `(status, body)` a native route needs.

    The router converts this value to response bytes; no domain function
    writes an HTTP response itself.
    """

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)

_DAYS_DEFAULT = 14
_DAYS_MIN = 1
_DAYS_MAX = 90


# ---- name matching (same criterion as ai_referral._name_matches) ----------

def _norm(s):
    return text_fold.alnum_words(text_fold.strip_accents(str(s or ""), "NFD").lower())


def _name_matches(asignado, catalogo_nombre):
    """Normalized tokens (no accents): tolerates reversed order and one
    side having a middle/last name the other one is missing."""
    return text_fold.same_name_tokens(set(_norm(asignado).split()), set(_norm(catalogo_nombre).split()))


def resolve_doctor_id(nombre):
    """Free-text name (as the operator saves it on approval) -> doctor
    catalog id, or None if there's no unique match. Ambiguous among several
    candidates: an EXACT match wins if there's exactly one; otherwise None
    (better to not save an id than to save the wrong one — the read-time
    name fallback keeps working the same way)."""
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    try:
        from app.patient_web import catalog
        meds = catalog.doctors()
    except Exception:  # noqa: BLE001
        return None
    candidatos = [m for m in meds if m.get("nombre") and _name_matches(nombre, m["nombre"])]
    if len(candidatos) == 1:
        return candidatos[0]["id"]
    if len(candidatos) > 1:
        exactos = [m for m in candidatos if _norm(m["nombre"]) == _norm(nombre)]
        if len(exactos) == 1:
            return exactos[0]["id"]
    return None


def resolve_pair(nombre, medico_id=None):
    """The ONE write rule for a request's doctor (problems.md №3,
    2026-10-23): `(assigned_doctor, assigned_doctor_id)` always travel
    together. A known catalog id wins and brings the catalog's own name; a
    name alone keeps its id only when it resolves to exactly ONE catalog
    doctor (and then the catalog spelling replaces the typed one); anything
    else is the name as given with id None — never an older doctor's id under
    a new name. Unreadable catalog: an explicit id is kept as given (degrade,
    never invent), a bare name gets no id."""
    nombre = (nombre or "").strip() or None
    medico_id = str(medico_id or "").strip() or None
    if medico_id:
        # Disabled doctors included (2026-10-24): the row stays theirs.
        medico = doctor_by_id(medico_id)
        if medico:
            return str(medico["nombre"]), medico_id
        try:
            from app.patient_web import catalog
            catalog.doctors()
        except Exception:  # noqa: BLE001 — sin catálogo no se puede juzgar el id
            return nombre, medico_id
    if not nombre:
        return None, None
    resuelto = resolve_doctor_id(nombre)
    medico = doctor_of(resuelto, incluir_incompletos=True) if resuelto else None
    if medico and medico.get("nombre"):
        return str(medico["nombre"]), resuelto
    return nombre, None


def display_name(row):
    """The doctor's name to SHOW for a request row: the catalog's current name
    behind `medico_asignado_id` (a renamed doctor reads the same everywhere),
    else the stored name (legacy rows, doctors outside the catalog)."""
    row = row or {}
    mid = row.get("medico_asignado_id")
    if mid:
        medico = doctor_by_id(mid)
        if medico:
            return str(medico["nombre"])
    return (row.get("medico_asignado") or "").strip()


def same_doctor(row, medico_id, nombre_norm, norm):
    """Does an approved row belong to the wanted doctor? By id when both sides
    have one; the stored name (normalized with `norm`) only for the legacy
    side that has no id."""
    asignado_id = row.get("medico_asignado_id")
    if asignado_id and medico_id:
        return str(asignado_id) == str(medico_id)
    return bool(nombre_norm) and norm(row.get("medico_asignado")) == nombre_norm


# ---- catalog (tolerant, same pattern as calendar_feed.py) -----------------

def doctor_by_id(medico_id):
    """The doctor a request's id names, disabled and incomplete ones included
    (catalog.doctor_by_id), or None — tolerant like doctor_of."""
    try:
        from app.patient_web import catalog
        return catalog.doctor_by_id(str(medico_id or "").strip() or None)
    except Exception:  # noqa: BLE001
        return None


def doctor_of(medico_id, incluir_incompletos=False):
    """{id,nombre,...} from the catalog, or None. Public: calendar_feed.py
    uses it to build the doctor's ICS feed. The panel passes
    `incluir_incompletos=True`: a doctor saved without a center yet (the
    editor no longer preselects one) still has its own, empty calendar."""
    from app.patient_web import catalog
    medico = catalog.doctor_doc(medico_id, incluir_incompletos=incluir_incompletos, tolerant=True)
    # `doctor_doc` compares ids as text; this lookup always compared them as they come (a caller's int 7 is not "7")
    return medico if medico is not None and medico.get("id") == medico_id else None


def resolver_names(resuelta_por_values):
    """{stored resuelta_por: doctor's name} for the `medico:<user>` values among
    `resuelta_por_values`, so the panel and the CSV can write who resolved a
    request (`format.resolved_by_text`). Best-effort: a user with no doctor
    linked (or a storage hiccup) is simply absent and shows as its login."""
    out = {}
    for v in set(resuelta_por_values or ()):
        pref, _, usuario = str(v or "").partition(":")
        if pref.lower() != "medico" or not usuario:
            continue
        try:
            medico_id = db_operators.operator_doctor_id(usuario)
            medico = doctor_of(medico_id, incluir_incompletos=True) if medico_id else None
        except Exception as exc:  # noqa: BLE001 — a display nicety, never a reason to fail the list
            import logging
            from app.core import log_pii
            logging.getLogger(__name__).warning("resolver_names: no name for %s: %s", v, log_pii.exc(exc))
            continue
        if medico and medico.get("nombre"):
            out[v] = medico["nombre"]
    return out


def own_doctor(identity):
    """The catalog doctor a 'medico' session belongs to — None for any other
    role, and None too when the account has no doctor linked.

    One place, because more than one module now has to answer "is this a
    doctor, and which one?" before it lets a request touch an agenda: the day
    queue, the patient search and creating an appointment (2026-08-05, when a
    doctor got to hand out the free slots of their OWN day). Callers that must
    tell "not a doctor" from "a doctor we can't resolve" check the role
    themselves first — here both come back None on purpose, so the permissive
    branch is never the accidental one.
    """
    if (identity or {}).get("rol") != "medico":
        return None
    try:
        mid = db_operators.operator_doctor_id(identity.get("usuario"))
    except Exception:  # noqa: BLE001
        return None
    return doctor_of(mid) if mid else None


def _service_of(sid):
    """Deleted services included: the appointment still happened, and the
    front desk needs its name, not its id (catalog.service_by_id)."""
    try:
        from app.patient_web import catalog
        return catalog.service_by_id(sid)
    except Exception:  # noqa: BLE001
        return None


def _service_name(sid):
    try:
        from app.patient_web import catalog
        return catalog.service_name(sid) or sid
    except Exception:  # noqa: BLE001
        return sid


def _center_name(slug):
    try:
        from app.patient_web import catalog
        return catalog.center_name(slug) or slug
    except Exception:  # noqa: BLE001
        return slug


def _center_hsi_institution(slug):
    try:
        from app.patient_web import catalog
        return catalog.center_hsi_institution(slug) or None
    except Exception:  # noqa: BLE001
        return None


def _center_of_group(medico, turnos):
    """Where that doctor sees patients THAT day, for the group header.

    Goes with what their appointments say (that's the day's real data); if
    that day has none at all — an entirely free agenda — falls back to the
    catalog's centers. With more than one, it doesn't guess which one: it
    returns None rather than claiming the wrong one (a doctor with two
    locations is the normal case here).
    """
    nombres = []
    for t in turnos or []:
        c = t.get("centro")
        if c and c not in nombres:
            nombres.append(c)
    if len(nombres) == 1:
        return nombres[0]
    if nombres:
        return None
    slugs = [c.get("centro") for c in ((medico or {}).get("centros") or []) if c.get("centro")]
    if not slugs and (medico or {}).get("centro"):
        slugs = [medico["centro"]]
    return _center_name(slugs[0]) if len(set(slugs)) == 1 else None


def _centers_of_group(medico, turnos):
    """ALL the centers a group belongs to that day, by name. `_center_of_group`
    answers the header (one center or none); this answers the center filter,
    where a doctor with two locations must still be found under each of them
    (the reference-deployment counterpart, 2026-10-24: «Todos los centros» offered only two). Same source
    order: the day's appointments first, else the catalog's centers."""
    nombres = []
    for t in turnos or []:
        c = t.get("centro")
        if c and c not in nombres:
            nombres.append(c)
    if nombres:
        return nombres
    slugs = [c.get("centro") for c in ((medico or {}).get("centros") or []) if c.get("centro")]
    if not slugs and (medico or {}).get("centro"):
        slugs = [medico["centro"]]
    for slug in slugs:
        n = _center_name(slug)
        if n and n not in nombres:
            nombres.append(n)
    return nombres


def _center_filter_names(centros_ok):
    """Every center the account may see, by name — the options of the day
    queue's center filter. They do NOT depend on the day shown: a center with
    no agenda that Monday is still a center the desk can pick."""
    try:
        from app.patient_web import catalog
        cats = catalog.centers()
    except Exception:  # noqa: BLE001
        return []
    nombres = []
    for c in cats:
        slug = c.get("slug")
        if not slug or c.get("_borrado") or (centros_ok is not None and slug not in centros_ok):
            continue
        n = _center_name(slug)
        if n and n not in nombres:
            nombres.append(n)
    return sorted(nombres, key=lambda s: s.lower())


# ---- filter by doctor -------------------------------------------------------

def _belongs_to(row, medico_id, nombre_medico):
    asignado_id = row.get("medico_asignado_id")
    if asignado_id:
        return asignado_id == medico_id
    asignado = row.get("medico_asignado")
    return bool(asignado) and _name_matches(asignado, nombre_medico)


def appointments_of(medico_id, nombre_medico, desde=None, hasta=None):
    """RAW rows (dict_row from booking requests) approved and in the future
    that belong to `medico_id`. One single place that knows how to match
    doctor↔request (see the module docstring) — reused both by
    /api/admin/my-appointments (below) and by the doctor's ICS feed
    (calendar_feed.py), so the matching criterion isn't duplicated across two
    files. `hasta` is an optional cutoff (my-appointments uses it, `dias=N`);
    the feed doesn't pass it — it wants ALL future appointments, same as the
    patient's own feed.

    `db_booking.list_approved_future_for_doctor` (2026-08-06 audit) pushes the
    `medico_id` filter into SQL instead of pulling the hospital-wide table —
    it still hands back the small backlog of rows with no
    `assigned_doctor_id` resolved yet, so `_belongs_to` below still does the
    real, tolerant name-matching for those; it just never re-scans every
    OTHER doctor's rows to do it."""
    rows = db_booking.list_approved_future_for_doctor(medico_id, desde=desde, hasta=hasta)
    out = [r for r in rows if _belongs_to(r, medico_id, nombre_medico or "")]
    if hasta is not None:
        out = [r for r in out if r["turno_dt"] <= hasta]
    return out


# ---- output format ----------------------------------------------------------

def _hsi_web_base():
    """The legacy HSI web base, or None unless HSI itself is selected.

    The bridge is a generic API connector, not an HSI web UI. It must never
    inherit HSI_URL or the legacy localhost default into appointment payloads.
    Without an explicit HSI selection the panel renders neither the patient
    link nor the "create in the EMR" emergency exit (admin-content.js already
    degrades both on a falsy value)."""
    from app import connectors
    if connectors.resolve(connectors.EMR_SYNC)[0] != "hsi":
        return None
    return (os.environ.get("TURNOS_HSI_WEB_URL") or
            os.environ.get("HSI_URL") or "http://localhost:8280").rstrip("/")


def _attendance_eligible(row):
    """Same criterion as db_booking.mark_attendance(exigir_no_futuro=True): an
    approved appointment can be marked if its ART day is not LATER than
    today (today itself is enough — the doctor ticks it off as they see
    patients, not only at night). Server-computed so the day queue doesn't
    have to rebuild the rule in JS with partial data."""
    dt = row.get("turno_dt")
    if row.get("estado") != "aprobada" or not dt:
        return False
    return dt.astimezone(_ART).date() <= datetime.now(_ART).date()


def _cuentas_de_contacto(rows):
    """Las cuentas de quien sacó cada turno, en una consulta — para
    `patient_contact.contacto`. Best-effort: sin base, el contacto de la
    solicitud (marcado "solicitud")."""
    try:
        return db_clinical.users_by_dnis(patient_contact.personas_de(rows))
    except Exception:  # noqa: BLE001
        return {}


def _appointment_public(row, rol=None, cuentas=None):
    dt_local = row["turno_dt"].astimezone(_ART)
    ai = row.get("ai_json") or {}
    svc = _service_of(row.get("servicio_id"))
    # contact/birth date/overbooking/office (2026-07-20 audit): data that
    # already lives on the row or in the catalog and that the doctor/admin
    # staff needs for their day — used to be dropped on serialization.
    hsi_base = _hsi_web_base()
    institution_id = os.environ.get("HSI_INSTITUTION_ID", "1")
    patient_id = row.get("hsi_patient_id")
    sid = row.get("id")
    orden_path = row.get("orden_path")
    tiene_orden = bool(orden_path) and os.path.exists(orden_path)
    orden_url = None
    if tiene_orden and sid is not None:
        # The doctor can only request THEIR OWN referral through a
        # doctor-gated route (ownership check inside); admin/operator keep
        # using the usual requests-queue route (decision #6).
        orden_url = (f"/api/admin/my-appointments/{sid}/order" if rol == "medico"
                     else f"/api/admin/order/{sid}")
    asistencia_en = row.get("asistencia_actualizada_en")
    cobertura = row.get("cobertura_usuario") or {}
    obras = cobertura.get("obras_sociales") or []
    if not obras and cobertura.get("obra_social"):
        obras = [cobertura.get("obra_social")]
    nombres_cobertura = []
    for obra in obras:
        nombre = obra.get("nombre") if isinstance(obra, dict) else str(obra or "")
        if nombre and nombre not in nombres_cobertura:
            nombres_cobertura.append(nombre)
    prepaga = cobertura.get("prepaga")
    if isinstance(prepaga, dict):
        prepaga = prepaga.get("nombre")
    if prepaga and prepaga not in nombres_cobertura:
        nombres_cobertura.append(str(prepaga))
    created = row.get("created")
    return {
        "id": sid,
        "turno_dt": dt_local.isoformat(),
        "dia_iso": dt_local.strftime("%Y-%m-%d"),
        "hora": dt_local.strftime("%H:%M"),
        "paciente": " ".join(x for x in (row.get("nombre"), row.get("apellido")) if x).strip(),
        "dni": row.get("dni"),
        "nacimiento": str(row.get("nacimiento") or "") or None,
        # HSI finds a patient by document AND sex, never by document alone
        # (docs/solutions/hsi/patient-needs-gender.md): the desk's HSI
        # extension needs it next to the DNI.
        "sexo": row.get("sexo") or None,
        # ONE rule with «Mis pacientes» (problems.md №4): the request's phone
        # leads; a different one in the account of whoever booked comes next to
        # it, each saying whose it is (`contacto_de`, `contacto_cuenta_de`,
        # `contacto_responsable`) — core/patient_contact.py.
        **patient_contact.contacto(row.get("dni"), row.get("solicitante_dni"),
                                   row.get("contacto"), cuentas),
        "solicitado_en": _tz.iso(created),
        "cobertura": " · ".join(nombres_cobertura) or None,
        "sobreturno": bool(row.get("sobreturno")),
        "servicio": (svc.get("nombre") if svc else None) or _service_name(row.get("servicio_id")),
        "consultorio": (svc or {}).get("consultorio") or None,
        "centro": _center_name(row.get("centro_slug")),
        # The desk's HSI extension picks the HSI institution by the row's
        # center: the name can be edited in the panel, the slug cannot.
        "centro_id": row.get("centro_slug") or None,
        # Each center is its own institution in HSI: the desk's extension opens
        # the row's own one (catalog field «Institución en HSI»), None = ask.
        "hsi_institucion": _center_hsi_institution(row.get("centro_slug")),
        "preparacion": ai.get("preparacion") or [],
        "hsi_patient_url": (f"{hsi_base}/institucion/{institution_id}/pacientes/profile/{patient_id}"
                            if patient_id and hsi_base else None),
        "hsi_url": hsi_base,
        # Day-queue fields (2026-07-24): the row ALREADY carries all of this
        # (SELECT * from listar_aprobadas_dia/appointments_of), it just
        # wasn't re-exposed yet — datetimes ALWAYS isoformat, never the raw
        # object (documented gotcha in
        # docs/solutions/hsi/attendance-no-show-sync.md).
        "asistencia": row.get("asistencia"),
        "asistencia_en": _tz.iso(asistencia_en),
        "asistencia_por": row.get("asistencia_actualizada_por"),
        # What the patient answered to the day-before reminder (2026-07-24)
        # — 'si' | 'no' | None (hasn't answered yet). Not the same as
        # `asistencia`, which is what actually happened.
        "confirmacion": row.get("confirmacion"),
        "tiene_orden": tiene_orden,
        "orden_url": orden_url,
        # the file's real mime type (2026-07-24 fix): the overlay needs it
        # to pick <img>/<embed>/<object> instead of guessing from
        # Content-Type (an <object> with no type renders blank in Chromium
        # for an image).
        "orden_mime": row.get("orden_mime") or None,
        "ai_campos": ai.get("campos") or None,
        "elegible_asistencia": _attendance_eligible(row),
    }


def _parse_days(qs):
    raw = (qs.get("days", [""])[0] or "").strip()
    if not raw:
        return _DAYS_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _DAYS_DEFAULT
    return max(_DAYS_MIN, min(_DAYS_MAX, n))


# ---- GET /api/admin/my-appointments ----------------------------------------------

def _log_shown(identity, action, turnos):
    """K10: these lists carry the interview's triage/summary and the AI
    reading of each referral — one access row per patient shown (deduped
    across refreshes by access_log.record_many)."""
    from app.core import access_log
    access_log.record_many(identity, action, [(t.get("dni"), t.get("id")) for t in turnos])


def my_appointments_data(identity, dias):
    """Pure body of GET /api/admin/my-appointments — `identity` already
    resolved by the caller's own auth gate, `dias` already parsed. Returns
    the JSON-able dict on success; raises DomainError with the exact
    status/body this route has always sent for every rejection branch."""
    if identity.get("rol") != "medico":
        raise DomainError(403, {"error": "solo_medico"})

    medico_id = db_operators.operator_doctor_id(identity["usuario"])
    if not medico_id:
        raise DomainError(400, {"error": "cuenta_sin_medico"})
    medico = doctor_of(medico_id)
    if not medico:
        # the catalog changed under the account's feet (id deleted/renamed),
        # or a new doctor still without a center: nothing can be booked yet.
        return {"medico": None, "dias": dias, "turnos": [], "total": 0,
                "tiene_horarios": False}

    ahora = datetime.now(timezone.utc)
    hasta = ahora + timedelta(days=dias)
    rows = appointments_of(medico_id, medico.get("nombre"), desde=ahora, hasta=hasta)

    cuentas = _cuentas_de_contacto(rows)
    turnos = [_appointment_public(row, rol="medico", cuentas=cuentas) for row in rows]
    # Same two auxiliary reads the day queue does, batched over the whole
    # window: how often this patient shows up, and what the pre-consult
    # interview said. Both are what the doctor's own list leads with now
    # (the product owner, 2026-08-05) — service/office/center moved behind the gear.
    _add_ranking([{"turnos": turnos}])
    _add_triage_by_request(turnos, medico)
    _log_shown(identity, "mis_turnos", turnos)
    return {
        "medico": {"id": medico_id, "nombre": medico.get("nombre")},
        "dias": dias,
        "turnos": turnos,
        "entrevista_activa": _intake_on_for(medico),
        "tiene_horarios": has_schedule(medico),
        "total": len(turnos),
    }


def has_schedule(medico):
    """True when the catalog doctor has at least one weekly time band in any
    center (or in the legacy flat `agenda`). «Mis turnos» uses it to tell an
    empty fortnight apart from a doctor nobody can book yet: the second one
    is sent to load their hours instead of being told to wait."""
    if not medico:
        return False
    agendas = [c for c in (medico.get("centros") or []) if isinstance(c, dict)]
    if isinstance(medico.get("agenda"), dict):
        agendas.append(medico["agenda"])
    return any(a.get("franjas") for a in agendas)


def _intake_on_for(medico):
    """True when the pre-consult interview is on for THIS doctor — the module
    enabled and their own toggle. Decides whether the columns that only make
    sense with it (triage, summary) are offered at all. Asked to intake by
    NAME (the "interview_enabled_for" hook modules/intake/app.py registers);
    hooks.call() itself filters by the module being enabled right now, so
    its "no result" IS the off answer."""
    try:
        from app.core import app_modules
        res = app_modules.hooks.call("interview_enabled_for", medico)
        return bool(res and res[0])
    except Exception:  # noqa: BLE001
        return False


def _add_triage_by_request(turnos, medico):
    """Attaches `entrevista` (triage + summary) to each appointment, keyed by
    request id instead of by day — "Mis turnos" spans 14 days, so the
    day-bounded query `_add_triage` uses would need 14 round trips.

    Best-effort by contract, same as `_add_ranking`: the list must never go
    down over an auxiliary piece of data. The query + shaping is intake's
    ("interview_state_by_requests" hook); a failure there is swallowed by
    the registry, same as the try/except this used to hand-roll."""
    if not turnos or not _intake_on_for(medico):
        return
    try:
        from app.core import app_modules
        app_modules.hooks.call("interview_state_by_requests", turnos, medico)
    except Exception:  # noqa: BLE001
        return


# ---- GET /api/admin/my-appointments/feed -----------------------------------------

def feed_url_data(identity):
    """Pure body of GET /api/admin/my-appointments/feed — the logged-in
    doctor's ICS feed address, same contract as the patient's
    (calendar_feed.resolve_feed_token): the first call creates it and returns
    {url, hint}; later calls {url: None, hint} — the database keeps only the
    token's hash, so only a new address (POST …/feed/rotate, «Crear una
    dirección nueva») shows a full one again."""
    if identity.get("rol") != "medico":
        raise DomainError(403, {"error": "solo_medico"})
    issued = db_operators.operator_feed_token_issue(identity["usuario"])
    if not issued:
        raise DomainError(400, {"error": "cuenta_sin_medico"})
    from app.modules.turnos import calendar_feed
    token = issued.get("token")
    return {"url": calendar_feed.doctor_feed_url(token) if token else None, "hint": issued.get("hint")}


# ---- GET /api/admin/doctors/<id>/appointments (admin, any doctor) ----------

def doctor_appointments_admin_data(identity, mid, dias):
    """Pure body of GET /api/admin/doctors/<id>/appointments."""
    medico = doctor_of(mid, incluir_incompletos=True)
    if not medico:
        raise DomainError(404, {"error": "medico_desconocido"})

    ahora = datetime.now(timezone.utc)
    hasta = ahora + timedelta(days=dias)
    rows = appointments_of(mid, medico.get("nombre"), desde=ahora, hasta=hasta)

    cuentas = _cuentas_de_contacto(rows)
    turnos = [_appointment_public(row, rol=identity.get("rol"), cuentas=cuentas) for row in rows]

    # Calendar colored by occupancy (the product owner, 2026-07-20): for each day in the
    # range, agenda capacity vs already-approved appointments — the client
    # paints red (full) / yellow (≤3 left) / green (plenty of room). A day
    # with appointments but no loaded agenda still shows up, with capacity 0.
    ocupados_por_dia = {}
    for t in turnos:
        ocupados_por_dia[t["dia_iso"]] = ocupados_por_dia.get(t["dia_iso"], 0) + 1
    # naive/local, same calendar-day criterion as _doctors_stats
    cap = _effective_capacity(medico, dias, datetime.now(_ART).replace(tzinfo=None))
    dias_detalle = [
        {"dia": f, "capacidad": c, "ocupados": ocupados_por_dia.get(f, 0)}
        for f, c in cap.items()
    ]
    dias_detalle += [
        {"dia": f, "capacidad": 0, "ocupados": n}
        for f, n in ocupados_por_dia.items() if f not in cap
    ]
    dias_detalle.sort(key=lambda d: d["dia"])
    _log_shown(identity, "agenda", turnos)

    return {
        "medico": {"id": mid, "nombre": medico.get("nombre")},
        "dias": dias,
        "turnos": turnos,
        "dias_detalle": dias_detalle,
        "total": len(turnos),
        # The "Create appointment" action opens HSI, the record system. No
        # deep frontend route is hardcoded here because it changes between
        # releases. None when no EMR connector is selected — no link, and
        # no invented localhost default (_hsi_web_base).
        "hsi_url": _hsi_web_base(),
    }


# ---- GET /api/admin/day-queue (2026-07-24) ---------------------------------
#
# "Today's patients": the doctor sees who they're seeing TODAY (past and
# future of that same day, not just what's left — appointments_of/
# listar_aprobadas_futuras miss what's already past in the day) and can tick
# off attendance as they go. admin/operator see the SAME view for ALL
# doctors, grouped, with a `medico: null` group for the ones with no doctor
# assigned (lab work) — never visible to the 'medico' role.
#
# Fase 4 (2026-07-31): cada turno lleva `entrevista` cuando modules/intake/
# tiene una entrevista previa matcheada (`_add_triage`), y el rol 'medico'
# recibe además `entrevistas_sin_turno` (entrevistas por QR de ESE médico sin
# turno matcheado ese día). Fase 7: además recibe `entrevista_config`, para
# el encabezado compacto de #cola (activar/desactivar + cartel).

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _today_art_iso():
    return datetime.now(_ART).date().isoformat()


def _parse_day(qs):
    raw = (qs.get("dia", [""])[0] or "").strip()
    return raw if _DAY_RE.match(raw) else _today_art_iso()


def _add_ranking(grupos):
    """Attaches the attendance ranking (patient_ranking.py) to every
    appointment of the day, in ONE query for the whole day.

    This is the front-desk view: whoever receives people is the one who most
    needs to know who's worth calling before their time slips by.
    Best-effort by contract — the day view must never go down over an
    auxiliary piece of data.
    """
    turnos = [t for g in grupos for t in g.get("turnos") or []]
    if not turnos:
        return
    try:
        from app.modules.turnos import patient_ranking
        rank = patient_ranking.by_dnis([t.get("dni") for t in turnos])
    except Exception:  # noqa: BLE001
        return
    for t in turnos:
        r = rank.get(str(t.get("dni") or ""))
        if r:
            t["ranking"] = r


def _add_triage(grupos, dia):
    """Attaches the pre-consult interview state (phase 4) to every
    appointment of the day whose doctor has the effective toggle on, in ONE
    query for the whole day — same batched pattern as `_add_ranking`.

    The query, the per-doctor gate and the shaping are intake's own
    ("interview_state_for_day", registered by modules/intake/app.py);
    hooks.call() only fires it while the module is enabled, which is the
    fast skip this used to hand-roll. Best-effort by contract — the day
    view must never go down over an auxiliary piece of data (a failure in
    the body is swallowed by the registry)."""
    try:
        from app.core import app_modules
        app_modules.hooks.call("interview_state_for_day", grupos, dia)
    except Exception:  # noqa: BLE001
        return


def _entrevistas_sin_turno(medico_id, medico_doc, dia, identity=None):
    """[{"id","nombre","hora","triage","resumen","estado"}, ...] — QR
    interviews for THIS doctor on that day that matched no appointment.
    Only called for the 'medico' role, with the module enabled and the
    effective toggle on (same gates as `_add_triage`); empty today because
    QR entry is phase 6, but wired now. The whole computation is intake's
    ("interviews_unmatched_for_day" hook) — module off → no result → the
    empty list from here. Best-effort — never takes down the queue."""
    try:
        from app.core import app_modules
        res = app_modules.hooks.call(
            "interviews_unmatched_for_day", medico_id, medico_doc, dia)
        rows = res[0] if res else []
        # These carry the triage and the summary of an interview: one access row each, and a list whose
        # rows cannot be logged is not shown (security review 2026-11). The DNI stays on the server.
        dnis = [r.pop("_dni", None) for r in rows]
        if rows and identity is not None:
            from app.core import access_log
            access_log.record_many(identity, "cola", [
                (dni, "entrevista:%s" % r.get("id")) for dni, r in zip(dnis, rows)])
        return rows
    except Exception:  # noqa: BLE001
        return []


def _entrevista_config(medico_id, medico_doc):
    """{"efectivo","override","cartel_url"} for the compact #cola header
    (phase 7, 'medico' role only) — unlike `_entrevistas_sin_turno`, this is
    computed ALWAYS while the module is on (even with the effective toggle
    off: that is exactly what lets it be turned on from here). None if the
    module is disabled entirely (the whole row is hidden on the client) —
    that None is this wrapper's "no result": the "interview_config" hook
    doesn't fire while intake is off. Best-effort — never takes down the
    queue."""
    try:
        from app.core import app_modules
        res = app_modules.hooks.call("interview_config", medico_id, medico_doc)
        return res[0] if res else None
    except Exception:  # noqa: BLE001
        return None


def queue_calendar_data(identity, desde, hasta, anchor=None, filtro=None, centro=None):
    """Permission-scoped date set and nearest occupied dates, with no patient data."""
    from datetime import date
    try:
        start, end = date.fromisoformat(desde), date.fromisoformat(hasta)
        selected = date.fromisoformat(anchor or _today_art_iso())
        if not 0 <= (end - start).days <= 366:
            raise ValueError()
    except (TypeError, ValueError):
        raise DomainError(400, {"error": "rango_fechas_invalido"})
    rows = db_slots.approved_calendar_groups()
    centers = admin_auth.centros_permitidos(identity)
    if centers is not None:
        rows = [r for r in rows if r.get("centro_slug") in centers]
    if centro:
        rows = [r for r in rows if r.get("centro_slug") == centro or _center_name(r.get("centro_slug")) == centro]
    doctor = None
    if identity.get("rol") == "medico":
        doctor = own_doctor(identity)
        if not doctor:
            raise DomainError(400, {"error": "cuenta_sin_medico"})
        rows = [r for r in rows if _belongs_to(r, doctor["id"], doctor.get("nombre") or "")]
    elif filtro:
        doctor = doctor_of(filtro)
        rows = [r for r in rows if doctor and _belongs_to(r, filtro, doctor.get("nombre") or "")]
    days = sorted({r["dia"] for r in rows})
    counts = {}
    for row in rows:
        if desde <= row["dia"] <= hasta and row.get("cantidad") is not None:
            counts[row["dia"]] = counts.get(row["dia"], 0) + int(row["cantidad"])
    key = selected.isoformat()
    return {"desde": desde, "hasta": hasta, "dia": key,
            "dias": [day for day in days if desde <= day <= hasta], "conteos": counts,
            "atencion": _attendance_days(doctor, desde, hasta),
            "anterior": next((day for day in reversed(days) if day < key), None),
            "siguiente": next((day for day in days if day > key), None)}


def _attendance_days(doctor, desde, hasta):
    """The days from today on, inside [desde, hasta], on which `doctor` has an
    agenda (the same effective hours the queue's free slots use) — the calendar
    lights them even with no turno yet, so a doctor sees WHEN they attend, not
    only when somebody booked. Empty with no single doctor (admin without a
    filter) and best-effort: a broken agenda never takes the calendar down."""
    if not doctor:
        return []
    from datetime import date, timedelta
    try:
        first = max(date.fromisoformat(desde), date.fromisoformat(_today_art_iso()))
        last = date.fromisoformat(hasta)
    except ValueError:
        return []
    try:
        agendas = _doctor_agendas(doctor)[0]
    except Exception:  # noqa: BLE001
        return []
    out = []
    day = first
    while day <= last:
        iso = day.isoformat()
        try:
            if _effective_day_hours(doctor, iso, agendas):
                out.append(iso)
        except Exception:  # noqa: BLE001
            return out
        day += timedelta(days=1)
    return out


def day_queue_data(identity, dia, filtro):
    """Pure body of GET /api/admin/day-queue — `identity` already resolved
    by the caller's own auth gate (permitir_medico + permitir_admisionista,
    2026-07-24: the admission desk sees the whole day queue same as admin/
    operator — only the 'medico' role gets filtered down further below).
    `filtro` is the already-parsed `?medico=` query value (admin/operator
    branch only)."""
    rows = db_slots.list_approved_day(dia)
    rol = identity.get("rol")

    # Center scope (2026-07-24): a scoped account sees ONLY the day for its
    # own centers. Filtered here, before grouping, so it can't slip through
    # either branch below.
    centros_ok = admin_auth.centros_permitidos(identity)
    if centros_ok is not None:
        rows = [r for r in rows if (r.get("centro_slug") or "") in centros_ok]

    if rol == "medico":
        medico_id = db_operators.operator_doctor_id(identity["usuario"])
        if not medico_id:
            raise DomainError(400, {"error": "cuenta_sin_medico"})
        medico = doctor_of(medico_id)
        nombre = (medico or {}).get("nombre") or ""
        propios = [r for r in rows if _belongs_to(r, medico_id, nombre)]
        cuentas = _cuentas_de_contacto(propios)
        turnos = [_appointment_public(r, rol="medico", cuentas=cuentas) for r in propios]
        grupos = [{"medico_id": medico_id, "medico": nombre or None,
                   "especialidad": (medico or {}).get("especialidad") or None,
                   "centro": _center_of_group(medico, turnos),
                   "turnos": turnos,
                   "libres": _free_of_day(medico, dia,
                                             {t.get("hora") for t in turnos})}]
        _add_ranking(grupos)
        _add_triage(grupos, dia)
        _log_shown(identity, "cola", [t for g in grupos for t in g["turnos"]])
        return {
            "dia": dia, "hoy": dia == _today_art_iso(),
            "grupos": grupos, "total": len(propios),
            # Fase 4: entrevistas por QR de este médico sin turno matcheado
            # ese día — hoy siempre vacía (el ingreso por QR es fase 6), se
            # cablea igual para no tener que retocar el contrato después.
            "entrevistas_sin_turno": _entrevistas_sin_turno(medico_id, medico, dia, identity),
            # Fase 7: config de la entrevista previa para el encabezado
            # compacto de #cola (activar/desactivar + cartel) — None si el
            # módulo está apagado.
            "entrevista_config": _entrevista_config(medico_id, medico),
        }

    # admin/operator: group ALL rows by doctor (same id-first/name-fallback
    # criterion as _belongs_to, see resolve_doctor_id) plus a no-doctor
    # group (lab work) — ?medico=<id> filters down to just one.
    try:
        from app.patient_web import catalog
        meds = catalog.doctors()
    except Exception:  # noqa: BLE001
        meds = []
    try:
        services = catalog.services()  # once for every doctor's free slots below
    except Exception:  # noqa: BLE001
        services = None
    med_by_id = {m.get("id"): m for m in meds if m.get("id")}
    cuentas = _cuentas_de_contacto(rows)

    grupos_map = {}
    for r in rows:
        mid = r.get("medico_asignado_id")
        nombre_libre = (r.get("medico_asignado") or "").strip()
        if not mid and nombre_libre:
            mid = resolve_doctor_id(nombre_libre)
        if filtro and (mid or "") != filtro:
            continue
        if mid:
            key, nombre_grupo = mid, (med_by_id.get(mid) or {}).get("nombre") or nombre_libre or None
        elif nombre_libre:
            # free-text name that didn't match any catalog id (doctor no
            # longer active, or ambiguous): still groups by name instead of
            # losing it into the no-doctor bucket, which is specifically
            # the lab-work one.
            key, nombre_grupo = "libre:" + _norm(nombre_libre), nombre_libre
        else:
            key, nombre_grupo = None, None
        grupo = grupos_map.setdefault(key, {"medico_id": mid, "medico": nombre_grupo, "turnos": []})
        grupo["turnos"].append(_appointment_public(r, rol=rol, cuentas=cuentas))

    # Free slots per doctor: what the front desk needs to be able to give an
    # appointment to someone who walked in without one (the reference-deployment counterpart,
    # 2026-07-24: "there I only see the taken ones, I don't see the ones
    # that are still open"). Only for the groups with a catalog doctor — the
    # "no doctor assigned" group (lab work) has no schedule of its own.
    for g in grupos_map.values():
        m = med_by_id.get(g.get("medico_id")) if g.get("medico_id") else None
        g["libres"] = _free_of_day(m, dia, {t.get("hora") for t in g["turnos"]}, services) if m else []
        # Group specialty and center (2026-07-27 handoff): each doctor's
        # header says what they see patients for and where — with six
        # groups on screen the name alone doesn't place anyone.
        g["especialidad"] = (m or {}).get("especialidad") or None
        g["centro"] = _center_of_group(m, g["turnos"])
        g["centros"] = _centers_of_group(m, g["turnos"])

    # And the doctors who see patients that day but have NO appointment given
    # at all: without this, the entire agenda of someone who hasn't received
    # anyone yet was invisible, which is the most useful case for the front
    # desk.
    for mid, m in med_by_id.items():
        if mid in grupos_map or (filtro and mid != filtro) or m.get("rol"):
            continue
        if centros_ok is not None and not _serves_at(m, centros_ok):
            continue
        libres = _free_of_day(m, dia, set(), services)
        if libres:
            grupos_map[mid] = {"medico_id": mid, "medico": m.get("nombre") or None,
                               "especialidad": m.get("especialidad") or None,
                               "centro": _center_of_group(m, []),
                               "centros": _centers_of_group(m, []),
                               "turnos": [], "libres": libres}

    grupos = sorted(grupos_map.values(), key=lambda g: (g["medico_id"] is None and g["medico"] is None,
                                                        (g["medico"] or "").lower()))
    _add_ranking(grupos)
    _add_triage(grupos, dia)
    _log_shown(identity, "cola", [t for g in grupos for t in g["turnos"]])
    total = sum(len(g["turnos"]) for g in grupos)
    return {"dia": dia, "hoy": dia == _today_art_iso(), "grupos": grupos, "total": total,
            "centros": _center_filter_names(centros_ok)}


# ---- GET /api/admin/day-queue/changes (the desk's HSI extension) ----------------------
#
# «Pacientes de hoy» lists only what is approved on one day, so a turno the desk or the
# patient cancelled, or moved to another day, simply disappears from it. The extension
# remembers the requests it loaded into HSI and asks here what became of them.

_CHANGES_MAX = 100


def parse_change_ids(raw):
    """The request ids of `?ids=1,2,3`: whole numbers, in order, each once, at most `_CHANGES_MAX`."""
    seen, ids = set(), []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) not in seen:
            seen.add(int(part))
            ids.append(int(part))
    return ids[:_CHANGES_MAX]


def day_changes_data(identity, ids):
    """Pure body of GET /api/admin/day-queue/changes: for each asked request, its state now.

    `estado` is the request's own state (`aprobada`, `cancelada`, ...; `None` when it is not one
    this account may see). An approved or cancelled request carries `turno`: the same row «Pacientes
    de hoy» gives (so the extension loads a new slot exactly as it loads any turno) plus `medico` and
    `especialidad` as the day queue groups them. A cancelled one keeps the slot it had, which is what
    the extension needs to find the turno in HSI. Same scope as the day queue: a scoped account sees
    its own centers, a doctor only their own patients; every row shown leaves its access-log row.

    `oculto` says somebody of the desk hid the request, as it is now, from the extension's panel
    (`hide_change_data`); `ahora` is the installation's own day and minute, which the extension
    compares the turnos' places with: HSI changes nothing that has already passed, and the desk's
    computer may keep another hour. A request the server's own journal links answers without
    `turno` while an EMR connector is routed: the extension ignores it, so the journal stays the
    one driver of that turno."""
    rows = _scoped_change_rows(identity, ids)
    shown = [r for _, r in rows if _change_shown(r)]
    marks = db_hsi.hsi_hidden_marks([r["id"] for r in shown])
    cuentas = _cuentas_de_contacto(shown)
    try:
        from app.patient_web import catalog
        med_by_id = {m.get("id"): m for m in catalog.doctors() if m.get("id")}
    except Exception:  # noqa: BLE001
        med_by_id = {}
    turnos = {}
    server_drives = _server_drives(shown)
    for r in shown:
        if r["id"] in server_drives:
            continue
        turno = _appointment_public(r, rol=identity.get("rol"), cuentas=cuentas)
        m = med_by_id.get(r.get("medico_asignado_id")) or {}
        turno["medico"] = m.get("nombre") or (r.get("medico_asignado") or "").strip() or None
        turno["especialidad"] = m.get("especialidad") or None
        turnos[r["id"]] = turno
    # Every request the answer says something about leaves its access row: the turno rows, but also
    # the `estado`/`oculto` of a request the server drives (no `turno`) or one that is not shown.
    _log_shown(identity, "cola", [r for _, r in rows if r])
    return {"ahora": clock.now().strftime("%Y-%m-%d %H:%M"), "cambios": [
        {"id": sid, "estado": (row or {}).get("estado") or None, "turno": turnos.get((row or {}).get("id")),
         "oculto": bool(row) and db_hsi.hsi_change_hidden(row, marks.get(row.get("id")))}
        for sid, row in rows]}


def _scoped_change_rows(identity, ids):
    """`[(id, row or None)]`: each asked request as this account may see it, in the day queue's scope."""
    centros_ok = admin_auth.centros_permitidos(identity)
    medico_id, nombre = None, ""
    if identity.get("rol") == "medico":
        medico_id = db_operators.operator_doctor_id(identity["usuario"])
        if not medico_id:
            raise DomainError(400, {"error": "cuenta_sin_medico"})
        nombre = (doctor_of(medico_id) or {}).get("nombre") or ""
    rows = []
    for sid in ids:
        row = db_booking.get_booking_request(sid)
        if row and centros_ok is not None and (row.get("centro_slug") or "") not in centros_ok:
            row = None
        if row and medico_id and not _belongs_to(row, medico_id, nombre):
            row = None
        rows.append((sid, row))
    return rows


def _change_shown(row):
    return bool(row) and row.get("estado") in ("aprobada", "cancelada") and bool(row.get("turno_dt"))


def _server_drives(rows):
    """The ids of the requests whose turno in HSI the server's own journal drives: they link an HSI
    appointment and this installation routes an EMR connector. The desk extension must not drive
    them too — its panel would offer to replay in HSI (cancel, move, present) what the journal
    already did itself; a request loaded by hand before the connector was routed stays with the
    extension until the inbound sync links it."""
    linked = {r["id"] for r in rows if r.get("hsi_appointment_id")}
    if not linked:
        return set()
    from app.modules.turnos import booking_requests, hsi_journal
    # With the journal switched off (TURNOS_HSI_SYNC_OFF) nothing drives the turno: the extension keeps it.
    if hsi_journal._off():
        return set()
    return linked if booking_requests._emr_sync_enabled() else set()


def hide_change_data(identity, sid):
    """Pure body of POST /api/admin/day-queue/changes/<id>/hide: «Ocultar» on the extension's panel.

    For a change HSI refuses (a closed agenda, a turno that has passed): the request, as it is now,
    stops asking to be passed to HSI on every computer of the desk, and its history keeps who hid it
    (`db_hsi.hide_hsi_change`). Its next change shows again. Same scope as the GET: a request this
    account may not see answers 404, like one that does not exist."""
    [(_, row)] = _scoped_change_rows(identity, [sid])
    if not _change_shown(row):
        raise DomainError(404, {"error": "turno_no_encontrado"})
    if not db_hsi.hide_hsi_change(sid, identity.get("usuario") or ""):
        raise DomainError(404, {"error": "turno_no_encontrado"})
    return {"ok": True}


# ---- GET /api/admin/my-appointments/<id>/order (doctor only, owner) -------
#
# Same file the requests queue already serves at /api/admin/order/<id>
# (admin/operator) — here the doctor requests THEIR OWN referral from the
# day queue, with a doctor gate + ownership check instead of the
# admin/operator gate (decision #6, not up for relitigating).

def _referral_file(row):
    """(mime, bytes) of the referral attached to `row`, or raise DomainError
    with the exact status/body `booking_requests.stream_referral` already
    sends for these two failure cases (missing file on disk / read error).

    Doesn't call `stream_referral` itself: that function is handler-shaped
    (writes straight to `handler.send_response`/`wfile`, by design — it's
    ALSO reused by the admin/operator route in booking_requests.py, which
    stays untouched and keeps calling it exactly as before). This ports
    just the doctor's-own-referral slice of that same behavior into a pure
    function (same two i18n keys/defaults), so the native router has one
    source for this doctor's file bytes."""
    from app.core import i18n
    if not row or not row.get("orden_path") or not os.path.exists(row["orden_path"]):
        raise DomainError(404, {"error": i18n.t_or(
            "admin.srv.referral.no_encontrada", "orden no encontrada", i18n.active())})
    try:
        with open(row["orden_path"], "rb") as f:
            data = f.read()
    except OSError:
        raise DomainError(500, {"error": i18n.t_or(
            "admin.srv.referral.error_lectura", "no se pudo leer la orden", i18n.active())})
    from app.modules.turnos import booking_referrals
    return booking_referrals.referral_served_type(data), data


def my_referral_data(identity, sid):
    """Pure body of GET /api/admin/my-appointments/<id>/order — (mime,
    bytes) on success, DomainError otherwise. 404, not 403, for an id that
    isn't this doctor's own (we don't confirm to a doctor that someone
    else's id exists)."""
    if identity.get("rol") != "medico":
        raise DomainError(403, {"error": "solo_medico"})
    medico_id = db_operators.operator_doctor_id(identity["usuario"])
    if not medico_id:
        raise DomainError(400, {"error": "cuenta_sin_medico"})
    medico = doctor_of(medico_id)
    row = db_booking.get_booking_request(sid)
    propia = (bool(row) and row.get("estado") not in db_clinical.DOCTOR_DEAD_STATUSES
              and _belongs_to(row, medico_id, (medico or {}).get("nombre") or ""))
    if row:
        # K10: la orden es un dato clínico — queda en el registro de accesos,
        # también el intento sobre la de otro médico.
        from app.core import access_log
        access_log.record(identity, "orden", row.get("dni"), target_id=sid, allowed=propia)
    if not propia:
        raise DomainError(404, {"error": "orden no encontrada"})
    return _referral_file(row)


# ---- GET /api/admin/doctors/stats (admin, #medicos listing management) ----

_STATS_DAYS = 14


def _short_day(dia_iso, hoy):
    """'Today' / 'Tomorrow' / 'Monday' (short day name, WITHOUT a date) —
    unlike site_schedule._friendly_date, which always carries the full date;
    here the value lives in a narrow table column (table-design: short data,
    doesn't wrap)."""
    from app.patient_web import site_schedule
    from datetime import date
    try:
        d = date.fromisoformat(str(dia_iso)[:10])
    except (TypeError, ValueError):
        return None
    from app.core import i18n
    lang = i18n.active()
    if d == hoy:
        return i18n.t_or("date.today", "Hoy", lang)
    if d == hoy + timedelta(days=1):
        return i18n.t_or("date.tomorrow", "Mañana", lang)
    return i18n.t_or("date.day." + site_schedule._DAY_KEYS[d.weekday()],
                     site_schedule._DAYS_FULL[d.weekday()], lang)


def _windows_and_exceptions(m, services=None):
    """All windows/exceptions of a doctor, merging ALL of their centers
    (`centros[]`) — to know WHEN they see patients without distinguishing
    where (next day, hours/week, slots offered in 14 days). Compat: older
    doctors still without `centros[]`, only with the flat `agenda` of a
    single center (same fallback as
    site_schedule._week_hours_doctor/_doctor_next)."""
    return _merged_windows(_doctor_agendas(m, services)[0])


def _doctor_agendas(m, services=None):
    """`([(franjas, excepciones), …], managed)`: the agendas that decide WHEN
    a doctor attends — one pair per service whose agenda manages the doctor
    (each service's closures close only that service), else the doctor's own
    windows (`managed` False). Resolved ONCE per doctor and handed down to
    every day of a range: `catalog.services()` re-merges the whole catalog and
    `managed_services` walks it, and asking both once per day of 14 days for
    every doctor made the Médicos list wait 20 s and more for its stats
    (2026-11-05). `services` = the catalog services when the caller already
    holds them. The sources are `doctor_windows.sources` — the same ones the
    public doctor page and the printed day sheet read; the doctor's own
    windows come one pair per center, so a center's closure closes only it."""
    from app.modules.turnos import doctor_windows
    fuentes, managed = doctor_windows.sources(m, services)
    return [(f["franjas"], f["excepciones"]) for f in fuentes], managed


def _merged_windows(agendas):
    franjas, excepciones = [], []
    for fr, ex in agendas:
        franjas += fr
        excepciones += ex
    return franjas, excepciones


def _slots_by_day(franjas, excepciones, dias, ahora):
    """{iso_date: capacity} for the next `dias` running days from `ahora`
    (today included) — only the days the agenda actually offers slots on.
    Same day-by-day criterion as site_availability._next_multi/_agenda_slots: a
    'cerrado' exception removes the whole day, 'extra' adds one more window
    that day. Consumed by the occupancy denominator (stats) and the
    editor's colored calendar (the product owner, 2026-07-20)."""
    from app.patient_web import site_availability
    out = {}
    for offset in range(dias):
        d = ahora.date() + timedelta(days=offset)
        # Same day resolution as the slot generator: range closures and site
        # holidays (site_holidays.py) come through patient_web/site_*.py's helper.
        excs = site_availability._day_exceptions(excepciones or [], d.isoformat())
        if any(e.get("tipo") == "cerrado" for e in excs):
            continue
        ventanas = [fr for fr in franjas if (d.isoweekday() % 7) in (fr.get("dias") or [])]
        ventanas += [e for e in excs if e.get("tipo") == "extra"]
        total = 0
        for v in ventanas:
            try:
                dh, dm = (int(x) for x in (v.get("desde") or "").split(":"))
                hh, hm = (int(x) for x in (v.get("hasta") or "").split(":"))
            except (ValueError, AttributeError):
                continue
            cada = int(v.get("cada_min") or 20)
            dur = hh * 60 + hm - (dh * 60 + dm)
            if cada > 0 and dur > 0:
                total += dur // cada
        if total:
            out[d.isoformat()] = total
    return out


def _slots_in_days(franjas, excepciones, dias, ahora):
    """Total slots over the next `dias` days (sum of _slots_by_day)."""
    return sum(_slots_by_day(franjas, excepciones, dias, ahora).values())


def _minutes_of_day(franjas, excepciones, dia):
    """Every time ("HH:MM") the agenda opens THAT day, in order.

    Same window walk as `_slots_by_day`, but returning the minutes instead of
    counting them: the day queue needs to know WHICH slot is free, not how
    many there are (the reference-deployment counterpart, 2026-07-24 meeting: "here Roger from 8 to
    9:30, but if there's a free slot at 8:30 I don't see it so I can give it
    to someone who walked in")."""
    from app.patient_web import site_availability
    try:
        d = datetime.strptime(str(dia)[:10], "%Y-%m-%d").date()
    except ValueError:
        return []
    # Exceptions are resolved with patient_web/site_*.py's helper, which also
    # understands a RANGE closure (vacations), not just a single loose date.
    excs = site_availability._day_exceptions(excepciones or [], d.isoformat())
    if any(e.get("tipo") == "cerrado" for e in excs):
        return []
    ventanas = [fr for fr in (franjas or []) if (d.isoweekday() % 7) in (fr.get("dias") or [])]
    ventanas += [e for e in excs if e.get("tipo") == "extra"]
    horas = set()
    for v in ventanas:
        # Deliberately NOT filtered by `cupos.web`: a reserved slot is
        # exactly the one the front desk has been holding back to give out
        # at the counter.
        try:
            dh, dm = (int(x) for x in (v.get("desde") or "").split(":"))
            hh, hm = (int(x) for x in (v.get("hasta") or "").split(":"))
        except (ValueError, AttributeError):
            continue
        cada = int(v.get("cada_min") or 20)
        if cada <= 0:
            continue
        t, fin = dh * 60 + dm, hh * 60 + hm
        while t < fin:
            hora = f"{t // 60:02d}:{t % 60:02d}"
            if not site_availability._time_blocked(hora, excs):
                horas.add(hora)
            t += cada
    return sorted(horas)


def _serves_at(medico, centros):
    """True if the doctor sees patients at any of those centers (looks at
    `centros[]` and the flat `centro` of the ones not yet re-saved with the
    editor)."""
    suyos = {c.get("centro") for c in (medico.get("centros") or []) if c.get("centro")}
    if medico.get("centro"):
        suyos.add(medico["centro"])
    return bool(suyos & set(centros))


def _effective_day_hours(medico, dia, agendas=None):
    """Every time the doctor attends THAT day. `agendas` = the doctor's
    `_doctor_agendas(...)[0]` when the caller walks a range of days."""
    if agendas is None:
        agendas = _doctor_agendas(medico)[0]
    # Each service's closures affect only that service, not its siblings.
    hours = set()
    for bands, excepciones in agendas:
        hours.update(_minutes_of_day(bands, excepciones, dia))
    return sorted(hours)


def _effective_capacity(medico, dias, ahora, resolved=None):
    """{iso_date: slots} for the next `dias` days. `resolved` = the doctor's
    `_doctor_agendas(...)` when the caller already has it."""
    agendas, managed = resolved or _doctor_agendas(medico)
    if not managed:
        out = {}
        for franjas, excepciones in agendas:  # one pair per center
            for day, n in _slots_by_day(franjas, excepciones, dias, ahora).items():
                out[day] = out.get(day, 0) + n
        return dict(sorted(out.items()))
    out = {}
    for offset in range(dias):
        day = (ahora.date() + timedelta(days=offset)).isoformat()
        hours = [h for h in _effective_day_hours(medico, day, agendas)
                 if day > ahora.date().isoformat() or h > ahora.strftime("%H:%M")]
        if hours:
            out[day] = len(hours)
    return out


def _free_of_day(medico, dia, ocupadas, services=None):
    """That doctor's times THAT day that don't have a patient yet.

    `ocupadas` = the "HH:MM" times already taken (includes overbooks: if
    someone's at 8:30, 8:30 stops being free — the front desk cares about
    the actual empty slot, not the window's theoretical capacity).
    `services` = the catalog services when the caller loops over doctors."""
    if not medico:
        return []
    agendas = _doctor_agendas(medico, services)[0]
    from app.modules.turnos import hsi_busy
    # An appointment created straight in HSI takes the hour too (hsi_busy).
    hsi = {h for d, h in hsi_busy.busy_of_doctor(medico.get("id")) if d == str(dia)[:10]}
    return [h for h in _effective_day_hours(medico, dia, agendas) if h not in ocupadas and h not in hsi]


def _doctor_services(m, servicios_cat):
    """Count of catalog services matching the doctor's cluster+center —
    same matching criterion as site_availability._week_hours."""
    from app.patient_web import catalog
    centros_m = {c.get("centro") for c in (m.get("centros") or []) if c.get("centro")}
    if not centros_m and m.get("centro"):
        centros_m = {m["centro"]}
    clusters = catalog.doctor_clusters(m)
    if not clusters or not centros_m:
        return 0
    return sum(1 for s in servicios_cat if s.get("cluster") in clusters and s.get("centro") in centros_m)


def _doctor_stats(m, servicios_cat, turnos_propios, ahora):
    """One doctor's metrics. `turnos_propios` = rows ALREADY filtered (see
    _doctors_stats — one single query for everyone, grouped here in Python)."""
    from app.patient_web import catalog, site_doctors, site_schedule
    resolved = _doctor_agendas(m, servicios_cat)
    franjas, _ = _merged_windows(resolved[0])
    horas = round(sum(site_schedule._window_hours(fr) for fr in franjas), 1) if franjas else 0.0

    capacities = _effective_capacity(m, _STATS_DAYS, ahora, resolved) if franjas else {}
    proximo_iso = min(capacities) if capacities else None

    turnos_14d = len(turnos_propios)
    ocupacion_14d = None
    slots_14d = 0
    if franjas:
        slots_14d = sum(capacities.values())
        if slots_14d:
            ocupacion_14d = round(100 * turnos_14d / slots_14d)

    ph = site_doctors._photo_style(m.get("id") or "")

    return {
        "horas_semana": horas,
        "proximo_dia": _short_day(proximo_iso, ahora.date()) if proximo_iso else None,
        "proximo_dia_iso": proximo_iso,
        "turnos_14d": turnos_14d,
        "slots_14d": slots_14d,
        "ocupacion_14d": ocupacion_14d,
        "tiene_foto": bool(ph),
        "tiene_login": False,  # filled in by _doctors_stats (one single query for everyone)
        "matricula": catalog.license_number(m),
        "servicios": _doctor_services(m, servicios_cat),
    }


def doctors_stats_data():
    """Pure body of GET /api/admin/doctors/stats — takes no identity: the
    only thing the original did with it was the permitir_reportes gate,
    already done by the caller before this runs."""
    from app.patient_web import catalog
    meds = [m for m in catalog.admin_list("medicos") if not m.get("rol")]
    servicios_cat = catalog.services()

    ahora_utc = datetime.now(timezone.utc)
    hasta = ahora_utc + timedelta(days=_STATS_DAYS)
    # Hospital-wide on purpose — every doctor's stats in one query, grouped
    # in Python below — but bounded to the same window used everywhere else
    # in this function so SQL does the trimming instead of a Python filter
    # after pulling rows past `hasta` (2026-08-06 audit).
    rows = db_booking.list_approved_future(desde=ahora_utc, hasta=hasta)
    con_login = db_operators.operator_doctor_ids()

    # naive/local — same criterion as site_availability._demo_next/_agenda_slots
    # (server calendar day, not UTC): _next_multi compares against a naive
    # datetime, mixing it with the aware one above breaks the subtraction.
    ahora_local = datetime.now()
    out = {}
    for m in meds:
        mid = m.get("id")
        if not mid:
            continue
        propios = [r for r in rows if _belongs_to(r, mid, m.get("nombre") or "")]
        st = _doctor_stats(m, servicios_cat, propios, ahora_local)
        st["tiene_login"] = mid in con_login
        out[mid] = st
    return {"medicos": out}


def _window_services(m):
    """Service ids tagged on a doctor's windows (all their locations — same
    shape as _windows_and_exceptions), in order of appearance and without
    duplicates."""
    if not m:
        return []
    franjas, _ = _windows_and_exceptions(m)
    out, vistos = [], set()
    for fr in franjas:
        s = str((fr or {}).get("servicio") or "").strip()
        if s and s not in vistos:
            vistos.add(s)
            out.append(s)
    return out


def compatible_services_data(mid, especialidad_qs):
    """Pure body of GET /api/admin/doctors/<id>/compatible-services — the
    service list for the "this window is for <service>" combo in the agenda
    editor (the product owner, 2026-07-24: a doctor does different services at different
    times and centers). Ordered by COMPATIBILITY: (1) the services THIS
    doctor already has tagged on their windows ("if they already have two,
    those two go on top"), (2) how many doctors of the SAME specialty have
    the service on their agendas, (3) the ones in the same cluster as the
    specialty, (4) the rest, alphabetical. For a new doctor (no id yet) the
    caller sends `especialidad_qs` and (2)-(4) apply."""
    from app.patient_web import catalog
    medico = doctor_of(mid)
    especialidad = (especialidad_qs or (medico or {}).get("especialidad") or "")
    clusters = catalog.doctor_clusters(medico)
    if not clusters and especialidad:
        try:
            clusters = [catalog._specialty_cluster(especialidad) or ""]
        except Exception:  # noqa: BLE001
            clusters = []

    propios = _window_services(medico)
    uso = {}  # servicio_id -> how many doctors of the same specialty have it
    esp_n = _norm(especialidad)
    for m in catalog.doctors():
        if esp_n and _norm(m.get("especialidad")) != esp_n:
            continue
        for s in _window_services(m):
            uso[s] = uso.get(s, 0) + 1

    centros = {c.get("slug"): c.get("name") or "" for c in catalog.centers()}
    orden_propio = {s: i for i, s in enumerate(propios)}

    def clave(s):
        sid = str(s.get("id"))
        return (
            orden_propio.get(sid, len(orden_propio)),      # own ones first, in their own order
            0 if sid in orden_propio else 1,
            -uso.get(sid, 0),                              # more used by the specialty
            0 if (s.get("cluster") and s.get("cluster") in clusters) else 1,
            str(s.get("nombre") or "").lower(),
        )

    items = [{
        "id": s.get("id"),
        "nombre": s.get("nombre"),
        "centro": s.get("centro"),
        "centro_nombre": centros.get(s.get("centro"), ""),
        "propio": str(s.get("id")) in orden_propio,
        "usos_especialidad": uso.get(str(s.get("id")), 0),
    } for s in sorted(catalog.services(), key=clave)]
    return {"items": items}
