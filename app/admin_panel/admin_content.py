"""admin_content.py — CRUD del admin sobre el catálogo editable (Pinamar Turnos).

Dueño de este módulo:
  GET    /api/admin/content/<tipo>                lista mergeada (json+db) con metadatos
  POST   /api/admin/content/<tipo>                 crea (o agrega) un item
  PUT    /api/admin/content/<tipo>/<id>             reemplaza el doc completo (override)
  DELETE /api/admin/content/<tipo>/<id>             soft-delete (id de la base) o hard-delete (id sólo-DB)
  POST   /api/admin/content/<tipo>/<id>/restaurar   vuelve al original / des-borra

`tipo` en la URL ∈ centers | services | doctors (inglés — TYPE_MAP acá abajo
lo traduce a las claves de catalog.py, centros | servicios | medicos, que
son las que vive la tabla `catalogo.tipo` en Postgres y NO cambian).

Permisos por rol (reunión con la municipalidad 2026-07-17: las agendas del
día a día — vacaciones, cambios de horario — las manejan las administrativas;
el alta/baja de profesionales queda en la Secretaría):
  - GET (listar):        admin y operador (el operador necesita ver el
                         catálogo para editar agendas; un 'medico' sigue 403).
  - PUT servicios/medicos: admin y operador — editar lo existente (agenda,
                         excepciones, palabras clave) ES el trabajo diario.
  - PUT centros:         sólo admin (contenido del sitio, no operación).
  - POST/DELETE/restaurar: sólo admin — crear o sacar un servicio/médico/
                         centro es decisión de la Secretaría.
The native router owns HTTP parsing, authorization, and responses; this module
owns the catalog domain operations it calls.
"""

import json
import re
from urllib.parse import urlencode

from app.core import db_booking
from app.core import db_operators
from app.core import db_slots
from app.core import db_catalog
from app.core import theme, i18n
from app.modules.turnos import schedule_policy
from app.patient_web import catalog
from app.patient_web import doctor_photos
from app.ai import ai_client
from app.ai import ai_referral

# segmento de URL (inglés) -> tipo de catalog.py (español, valor de DB sin tocar)
TYPE_MAP = {"centers": "centros", "services": "servicios", "doctors": "medicos",
            "preparations": "preparaciones", "knowledge": "conocimiento"}

_KNOWLEDGE_JURISDICTIONS = {"pinamar", "provincia", "nacion", "interno"}
_TAG_STOPWORDS = {
    "para", "como", "esta", "este", "desde", "hasta", "sobre", "entre",
    "tambien", "segun", "cada", "donde", "cuando", "puede", "debe", "tiene",
    "salud", "informacion", "personas", "persona", "oficial",
}


def _knowledge_fake_properties(content, categories, current):
    """Deterministic proposal for tests and offline demo installations."""
    clean = " ".join(str(content or "").split())
    sentence = re.split(r"(?<=[.!?])\s+", clean, maxsplit=1)[0].strip()
    title = sentence.rstrip(".!?")[:90] or "Nuevo conocimiento"
    summary = sentence[:180]
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,}", clean.lower())
    tags = []
    for word in words:
        normalized = word.translate(str.maketrans("áéíóúüñ", "aeiouun"))
        if normalized in _TAG_STOPWORDS or normalized in tags:
            continue
        tags.append(normalized)
        if len(tags) == 5:
            break
    allowed = {str(c.get("id")) for c in categories if isinstance(c, dict) and c.get("id")}
    parent = str(current.get("parent_id") or "")
    jurisdiction = str(current.get("jurisdiction") or "pinamar")
    return {
        "title": title,
        "summary": summary,
        "tags": tags,
        "parent_id": parent if parent in allowed else None,
        "jurisdiction": jurisdiction if jurisdiction in _KNOWLEDGE_JURISDICTIONS else "pinamar",
    }


def _sanitize_knowledge_properties(raw, categories, current):
    raw = raw if isinstance(raw, dict) else {}
    allowed = {str(c.get("id")) for c in categories if isinstance(c, dict) and c.get("id")}
    parent = str(raw.get("parent_id") or current.get("parent_id") or "")
    jurisdiction = str(raw.get("jurisdiction") or current.get("jurisdiction") or "pinamar")
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return {
        "title": str(raw.get("title") or "").strip()[:120],
        "summary": str(raw.get("summary") or "").strip()[:280],
        "tags": [str(tag).strip()[:40] for tag in tags if str(tag).strip()][:12],
        "parent_id": parent if parent in allowed else None,
        "jurisdiction": jurisdiction if jurisdiction in _KNOWLEDGE_JURISDICTIONS else "pinamar",
    }


# ============================================================ domain

class ContentError(Exception):
    """A domain failure rendered as `{"error": str(exc)}` at `.status`."""

    def __init__(self, status, message):
        self.payload = message if isinstance(message, dict) else {"error": message}
        super().__init__(self.payload["error"])
        self.status = status


def list_role_flags(tipo):
    """Role gate for an internal catalog key such as ``conocimiento``."""
    publico = tipo != "conocimiento"
    return dict(solo_admin=not publico, permitir_reportes=publico,
                permitir_medico=publico, permitir_admisionista=publico)


def _booking_policy_projection(tipo, item, services=None, doctors=None, can_edit_services=False):
    from app.modules.turnos import booking_limits, booking_horizon
    result = dict(item)
    if tipo == 'servicios':
        doctors = doctors if doctors is not None else catalog.doctors()
        # «Los N médicos de este servicio atienden en este horario»: the pool for whom this
        # schedule applies at runtime (same relation as the read-only forms).
        result['_schedule_doctor_count'] = sum(1 for doctor in doctors if not doctor.get('_borrado') and schedule_policy.belongs(doctor, item))
        # «Consultorio sin médico» (the hospital's technical contact, 2026-11-02): the people the desk can actually
        # assign to this service (`schedule_policy.pool`, same relation the booking wizard
        # uses), so Servicios shows who attends it or says «Sin médico».
        result['_medicos'] = [{'id': d.get('id'), 'nombre': d.get('nombre') or d.get('id')}
                              for d in schedule_policy.pool(item, doctors)]
    elif tipo == 'medicos':
        services = services if services is not None else catalog.services()
        applicable = [s for s in services if not s.get('_borrado') and schedule_policy.belongs(item, s)]
        def inherited_display(values):
            values = sorted(set(values))
            return values[0] if len(values) == 1 else str(values[0]) + '–' + str(values[-1])
        def inherited_from(own):
            # Where an empty field's number comes from (pinamar-turnos
            # 2026-11-03): the site, the one service that sets its own, or
            # several services that differ.
            names = {(s.get('nombre') or s.get('id')) if own(s) is not None else None for s in applicable}
            if not names or names == {None}:
                return {'from': 'site'}
            if len(names) == 1:
                return {'from': 'service', 'name': names.pop()}
            return {'from': 'services'}
        result['_booking_defaults'] = {
            'patient': inherited_display([booking_limits.limit_for(s) for s in applicable] or [booking_limits.site_default()]),
            'patient_from': inherited_from(booking_limits.own_limit),
            'days': inherited_display([booking_horizon.days_for(s) for s in applicable] or [booking_horizon.site_default()]),
            'days_from': inherited_from(booking_horizon.own_days)}
        # An admin can act on «el horario lo define el servicio» (open that
        # service and switch its single schedule off); a doctor cannot.
        # The services this doctor belongs to (the pool relation `belongs`): what
        # «Crear turno» offers once a doctor is pinned and the inverse of the
        # per-service doctor pool — the desk never re-derives the relation.
        result['_servicios_pool'] = [s['id'] for s in applicable if s.get('id')]
        result['_agendas_servicio'] = [dict(service, _puede_editar=bool(can_edit_services))
                                       for service in schedule_policy.managed_services(item, services)]
    return result


def _can_edit_services(identity):
    """«Abrir el servicio» en la ficha del médico: sólo quien tiene el permiso
    `servicios` en ESTE pedido (el que el gate verificó), no «todo rol que no
    sea médico» — el gestor de agendas no lo tiene (hallazgo K8, 2026-10-23)."""
    if not identity:
        return False
    from app.auth import admin_auth
    return "servicios" in admin_auth.active_permissions()


def list_payload(tipo, identity=None):
    """Merged catalog plus inherited policy; a doctor can edit only their own row."""
    from app.modules.turnos import booking_limits, booking_horizon
    items = catalog.admin_list(tipo)
    if tipo == 'servicios':
        doctors = catalog.doctors()
        items = [_booking_policy_projection(tipo, service, doctors=doctors) for service in items]
    if tipo == "medicos":
        services = catalog.services()
        can_edit_services = _can_edit_services(identity)
        items = [_booking_policy_projection(tipo, item, services=services, can_edit_services=can_edit_services)
                 for item in items]
        if (identity or {}).get("rol") == "medico":
            own_id = db_operators.operator_doctor_id(identity.get("usuario"))
            items = [m for m in items if m.get("id") == own_id]
            from app.modules.turnos import doctor_appointments
            for item in items:
                item["_servicios_compatibles"] = doctor_appointments.compatible_services_data(item["id"], "").get("items") or []
    from app.modules.turnos import site_holidays
    return {"items": items, "turnos_max_sitio": booking_limits.site_default(),
            "dias_anticipacion_sitio": booking_horizon.site_default(),
            "apertura_turnos_sitio": booking_horizon.opens_for(),
            # Read-only next to each agenda's own closures (Settings owns them).
            "feriados_sitio": site_holidays.upcoming_labeled()}


def edit_role_flags(tipo):
    """Role gate for editing an internal catalog key."""
    return dict(solo_admin=tipo in ("centros", "preparaciones", "conocimiento"))


def _schedule_message():
    return i18n.t_or("admin.content.schedule.service_applies",
        "El horario lo define el servicio. Lo cambia el administrador.", i18n.active())


def _check_schedule(tipo, doc, body, connection, unchanged_schedule=False):
    if tipo == "medicos" and schedule_policy.doctor_has_schedule(doc):
        # Existing legacy conflicts must not prevent an admin correcting a
        # name, specialty or note. Creating/restoring/changing schedules still
        # takes the ownership gate, including every doctor self-service save.
        if unchanged_schedule:
            return
        managed = schedule_policy.managed_services(doc, catalog.admin_list("servicios"))
        if managed:
            raise ContentError(409, {"error": "agenda_servicio_aplica", "detalle": _schedule_message(),
                                     "servicios": [s["id"] for s in managed]})
    if tipo != "servicios" or not schedule_policy.has_schedule(doc):
        return
    if unchanged_schedule:
        # Saving a service that already owns this exact schedule (a booking
        # window, keywords, a note…) must never ask about — or touch — the
        # doctors: only taking over / changing the schedule supersedes theirs.
        return
    # Only the service's OWN doctors (strict relation) and only when the reset
    # would really remove something at the service's center: a colleague of a
    # neighbouring specialty or a doctor whose schedule is elsewhere is neither
    # listed nor touched.
    affected = [m for m in catalog.admin_list("medicos")
                if not m.get("_borrado") and schedule_policy.serves(m, doc)
                and schedule_policy.reset_changes(m, doc)]
    if affected and body.get("confirmar_reinicio_agendas") is not True:
        raise ContentError(409, {"error": "agenda_doctores_se_reinician",
            "doctores": [m.get("nombre") or m["id"] for m in affected], "count": len(affected)})
    for doctor in affected:
        db_catalog.catalog_upsert("medicos", doctor["id"], schedule_policy.reset_doctor(doctor, doc), connection=connection)
    doc.pop("confirmar_reinicio_agendas", None)


def create_item(tipo, body):
    if tipo not in ("servicios", "medicos"):
        return _create_item(tipo, body)
    body = dict(body)
    # «Invitar médico»: the same create, plus an invitation even without an
    # email (then the admin gets the link to copy). A plain create invites
    # only when an email was typed (the product owner, 2026-10-19).
    invitar = body.pop("_invitar", False) is True
    if tipo == "medicos" and not body.get("rol"):
        from app.admin_panel import admin_users
        taken = admin_users.portal_email_conflict(body.get("email"))
        if taken:
            raise ContentError(409, {"error": taken, "field": "email"})
        missing = admin_users.portal_email_missing(body.get("email")) if invitar else None
        if missing:
            raise ContentError(400, {"error": missing, "field": "email"})
    cuenta = None
    with db_catalog.catalog_schedule_lock() as connection:
        catalog.invalidate()
        result = _create_item(tipo, body, connection)
        if tipo == "medicos" and not result["item"].get("rol"):
            # A generic role entry («Enfermería», `rol`) is not a person and
            # gets no account. Médico = usuario: the doctor's account is created in the SAME
            # transaction as the doctor; if it fails, the doctor is not saved.
            from app.admin_panel import admin_users
            cuenta = admin_users.provision_doctor_account(result["item"], connection=connection)
    catalog.invalidate()
    result["item"] = _item_out(tipo, result["item"].get(catalog.id_key(tipo)))
    if result["item"]:
        result["item"] = _booking_policy_projection(tipo, result["item"])
    if cuenta is not None:
        result["cuenta"] = {"usuario": cuenta["usuario"], "cuenta_estado": cuenta["cuenta_estado"],
                            "activo": cuenta["activo"]}
        if invitar or str((result["item"] or {}).get("email") or "").strip():
            from app.admin_panel import admin_users
            # After the commit: an email that cannot leave never undoes the
            # doctor and the account — the link is handed back instead.
            result["cuenta"] = admin_users.invite_account(cuenta, result["item"] or {})
            result["cuenta"]["activo"] = True
    return result


def _band_minutes(value):
    """HH:MM -> minutes since midnight, or None when the shape is not a time
    (malformed values are the catalog validator's to reject, not ours)."""
    if not isinstance(value, str) or ":" not in value:
        return None
    hours, _, minutes = value.partition(":")
    if not (hours.isdigit() and minutes.isdigit()):
        return None
    if len(hours) != 2 or len(minutes) != 2 or int(hours) > 23 or int(minutes) > 59:
        return None
    return int(hours) * 60 + int(minutes)


def _attendance_overlap(centros):
    """First pair of the doctor's own bands that overlaps on the same weekday,
    within one center or across two centers — one doctor cannot hold both.
    Returns (center_name_a, band_a, center_name_b, band_b) or None."""
    names = {c.get("slug"): c.get("name") or c.get("nombre") or c.get("slug") or ""
             for c in catalog.centers()}
    windows = []
    for entry in centros or []:
        bands = entry.get("franjas") or []
        # The normal catalog validator reports malformed intervals. This
        # cross-center guard must not turn those into an internal error.
        if not isinstance(bands, list):
            continue
        for band in bands:
            if not isinstance(band, dict) or not isinstance(band.get("dias"), list):
                continue
            desde, hasta = _band_minutes(band.get("desde")), _band_minutes(band.get("hasta"))
            if desde is None or hasta is None or hasta <= desde:
                continue
            for day in band.get("dias") or []:
                windows.append((day, desde, hasta, names.get(entry.get("centro"), entry.get("centro") or ""), band))
    for i, left in enumerate(windows):
        for right in windows[i + 1:]:
            if left[0] == right[0] and left[1] < right[2] and right[1] < left[2]:
                return left[3], left[4], right[3], right[4]
    return None


def edit_item(tipo, id_, body, identity=None):
    if tipo not in ("servicios", "medicos"):
        return _edit_item(tipo, id_, body)
    # One email per doctor (2026-11-03): the record's «Email» is its
    # account's too — refused before saving when another active account
    # already logs in with it.
    new_email = None
    if tipo == "medicos" and "email" in body:
        from app.admin_panel import admin_users
        new_email = str(body.get("email") or "").strip()
        taken = admin_users.email_conflict_for_doctor(id_, new_email)
        if taken:
            # A stable code: the doctor form says it at the «Email» field.
            raise ContentError(409, {"error": "email_en_uso", "mensaje": taken})
        if not new_email:
            # An emptied field travels as "" so the account empties too; the
            # record itself keeps no empty key.
            body = {k: v for k, v in body.items() if k != "email"}
    with db_catalog.catalog_schedule_lock() as connection:
        catalog.invalidate()
        if (identity or {}).get("rol") == "medico":
            if tipo != "medicos" or db_operators.operator_doctor_id(identity.get("usuario")) != id_:
                raise ContentError(404, "no_encontrado")
            current = _item_out(tipo, id_)
            if not current:
                raise ContentError(404, "no_encontrado")
            # The form of a service-managed doctor has no schedule editor and
            # says so; its rebuilt `centros` is not a schedule change.
            no_schedule_edit = body.get('_agenda_sin_cambios') is True
            schedule_changed = not no_schedule_edit and any(k in body for k in ('centros', 'agenda'))
            if schedule_changed and schedule_policy.managed_services(current, catalog.services()):
                raise ContentError(409, {"error": "agenda_servicio_aplica", "detalle": _schedule_message()})
            allowed_centers = {c.get("centro") for c in current.get("centros") or []} | {current.get("centro")}
            if not (current.get("centros") or current.get("centro")):
                # Invited without a center: the onboarding asks where they
                # see patients — any center of the catalog, once.
                allowed_centers = {c.get("slug") for c in catalog.centers()}
            if any(c.get("centro") not in allowed_centers for c in body.get("centros") or []):
                raise ContentError(403, "centro_no_permitido")
            # The client gates this too, but the server owns the truth: a
            # personal attendance schedule with two bands on the same weekday
            # that overlap — in one center or across the network — is not a
            # schedule one doctor can keep. Service-managed schedules and the
            # admin editors keep their own rules; this gate is self-service only.
            if schedule_changed and "centros" in body:
                clash = _attendance_overlap(body.get("centros"))
                if clash:
                    first_center, first_band, second_center, second_band = clash
                    # Same keys and remedy strings the client-side gate shows.
                    detalle = i18n.t_or(
                        "admin.doctor.overlap", "Dos horarios de {center} se superponen ({first} y {second}). Separalos para guardar.",
                        i18n.active(), center=first_center,
                        first=f"{first_band.get('desde')}–{first_band.get('hasta')}",
                        second=f"{second_band.get('desde')}–{second_band.get('hasta')}") if first_center == second_center else i18n.t_or(
                        "admin.doctor.overlap_centers", "El mismo día te superpone {first} en {first_center} y {second} en {second_center}. Elegí un horario para cada uno.",
                        i18n.active(), first_center=first_center, second_center=second_center,
                        first=f"{first_band.get('desde')}–{first_band.get('hasta')}",
                        second=f"{second_band.get('desde')}–{second_band.get('hasta')}")
                    raise ContentError(409, {"error": "agenda_superpuesta", "detalle": detalle})
            # Only explicit self-service fields; IDs, cluster, licenses, booking
            # policies, ownership and center membership remain admin-owned.
            if isinstance(body.get("centros"), list):
                body = dict(body, centros=[dict(c) if isinstance(c, dict) else c for c in body["centros"]])
                _keep_center_windows(body, current)
            own_fields = ("nombre_pila", "apellido", "especialidad", "especialidad_2", "email", "telefono")
            if not no_schedule_edit:
                own_fields = ("centros", "agenda") + own_fields
            body = dict(current, **{k: body[k] for k in own_fields if k in body})
            if new_email == "":
                body.pop("email", None)
        result = _edit_item(tipo, id_, body, connection,
                            allow_unchanged_schedule=True)
    catalog.invalidate()
    result["item"] = _item_out(tipo, id_)
    if new_email is not None:
        # The account takes the email as the record stored it (the validator
        # normalizes it), not as it was typed.
        from app.admin_panel import admin_users
        stored = (result["item"] or {}).get("email") if new_email else ""
        admin_users.doctor_email_changed(id_, stored or new_email)
    if result["item"]:
        result["item"] = _booking_policy_projection(
            tipo, result["item"],
            can_edit_services=_can_edit_services(identity))
    if tipo == "medicos" and result["item"]:
        if (identity or {}).get("rol") == "medico":
            from app.modules.turnos import doctor_appointments
            result["item"]["_servicios_compatibles"] = doctor_appointments.compatible_services_data(id_, "").get("items") or []
    return result


def _create_item(tipo, body, connection=None):
    """Create an item; may raise ``ContentError`` or ``db.DbUnavailable``."""
    doc, err = catalog.validate(tipo, {k: v for k, v in body.items() if k != "confirmar_reinicio_agendas"}, is_new=True)
    if err:
        raise ContentError(400, err)
    key = catalog.id_key(tipo)
    id_ = doc.get(key)
    existentes = {it.get(key) for it in catalog.admin_list(tipo)}
    derived = tipo == "medicos" and not str(body.get(key) or "").strip()
    if id_ in existentes and not derived:
        raise ContentError(409, f"ya existe un {tipo[:-1]} con id '{id_}'")
    if derived:
        # A derived id still held by an account (a deleted doctor keeps its
        # inactive one) or by a disabled doctor (kept, never deleted, while
        # anything names it — 2026-10-23) is taken: suffix it, never relink
        # that old account.
        base, n = id_, 2
        while id_ in existentes or db_operators.operator_holds_doctor_id(id_, connection=connection):
            id_ = f"{base}-{n}"
            n += 1
        doc[key] = id_
    _check_schedule(tipo, doc, body, connection)
    db_catalog.catalog_upsert(tipo, id_, doc, borrado=False, connection=connection)
    catalog.invalidate()
    return {"ok": True, "item": _item_out(tipo, id_) if connection is None else doc}


def _centers_schedule(doc):
    """A doctor's `centros` without the booking window each carries."""
    return [{k: v for k, v in c.items() if k != "dias_anticipacion"}
            for c in (doc or {}).get("centros") or [] if isinstance(c, dict)]


def _keep_center_windows(body, current):
    """A doctor edits their own hours, never the booking window an admin set per
    center: carry the stored one over (and drop whatever the body sent)."""
    stored = {c.get("centro"): c.get("dias_anticipacion")
              for c in (current or {}).get("centros") or [] if isinstance(c, dict)}
    for c in body.get("centros") or []:
        if not isinstance(c, dict):
            continue
        c.pop("dias_anticipacion", None)
        if stored.get(c.get("centro")) is not None:
            c["dias_anticipacion"] = stored[c.get("centro")]


def _edit_item(tipo, id_, body, connection=None, allow_unchanged_schedule=False):
    """Update an item after the router has parsed and authorized the request."""
    key = catalog.id_key(tipo)
    body = dict(body)
    body[key] = id_  # la key del path GANA sobre el body — id/slug inmutable en edit
    if tipo == "medicos":
        # «Incompleto» is derived from the stored doctor, never from the
        # client: only a doctor still waiting for onboarding may stay
        # without name, specialty or center (catalog._validate_doctor).
        stored_doc = _item_out(tipo, id_) or {}
        body["incompleto"] = stored_doc.get("incompleto") is True
    # The editor says it did not edit the schedule (it is read-only while the
    # service owns it): keep the stored one byte for byte instead of comparing
    # a schedule the form rebuilt from its own state (empty franjas and
    # unknown keys dropped), which made every save look like a schedule change.
    if body.pop("_agenda_sin_cambios", False) is True and tipo == "medicos":
        stored = _item_out(tipo, id_)
        if stored:
            for field in ("centro", "centros", "agenda"):
                if field in stored:
                    body[field] = stored[field]
                else:
                    body.pop(field, None)
    doc, err = catalog.validate(tipo, {k: v for k, v in body.items() if k != "confirmar_reinicio_agendas"}, is_new=False)
    if err:
        raise ContentError(400, err)
    existing = db_catalog.catalog_get(tipo, id_)
    borrado_actual = bool(existing["borrado"]) if existing else False
    unchanged_schedule = False
    if tipo in ("medicos", "servicios") and allow_unchanged_schedule:
        current = _item_out(tipo, id_)
        # Compare validated canonical schedules, so the editor's legacy
        # agenda → centros conversion is not mistaken for a schedule change.
        # For a service, what decides WHICH doctors its schedule owns (center,
        # group and name) travels with the schedule itself.
        # Both sides go through the SAME validator before they are compared.
        prior, prior_error = catalog.validate(tipo, current, is_new=False) if current else (None, None)
        if tipo == "medicos":
            # A center's booking window is a policy, not part of the schedule.
            unchanged_schedule = bool(prior and not prior_error and all(
                doc.get(k) == prior.get(k) for k in ("centro", "agenda", "cluster"))
                and _centers_schedule(doc) == _centers_schedule(prior))
        else:
            # The editor re-sends the stored schedule with its own defaults
            # (quotas) and ordering: compare what it MEANS, not its bytes.
            unchanged_schedule = bool(prior and not prior_error
                                      and schedule_policy.agenda_unchanged(doc.get("agenda"), prior.get("agenda"))
                                      and all(doc.get(k) == prior.get(k) for k in ("centro", "cluster", "nombre")))
    _check_schedule(tipo, doc, body, connection, unchanged_schedule=unchanged_schedule)
    db_catalog.catalog_upsert(tipo, id_, doc, borrado=borrado_actual, connection=connection)
    catalog.invalidate()
    return {"ok": True, "item": _item_out(tipo, id_) if connection is None else doc}


def delete_item(tipo, id_):
    """Delete an item after the router has authorized the request."""
    if tipo == "conocimiento":
        existing = db_catalog.catalog_get(tipo, id_)
        if existing is not None:
            db_catalog.catalog_mark_deleted(tipo, id_, True)
        elif _in_base(tipo, id_):
            key = catalog.id_key(tipo)
            doc = next((it for it in catalog.base_items(tipo)
                        if it.get(key) == id_), {})
            db_catalog.catalog_upsert(tipo, id_, doc, borrado=True)
        catalog.invalidate()
        return {"ok": True, "turnos": 0, "conservado": True}
    turnos = _with_history(tipo, id_)
    otras = 0
    if tipo == "medicos":
        _refuse_with_upcoming(id_)
        otras = _other_references(id_)
    if _in_base(tipo, id_):
        existing = db_catalog.catalog_get(tipo, id_)
        if existing is not None:
            doc = existing["doc"]
        else:
            key = catalog.id_key(tipo)
            doc = next((it for it in catalog.base_items(tipo) if it.get(key) == id_), {})
        db_catalog.catalog_upsert(tipo, id_, doc, borrado=True)
    elif turnos != 0 or otras != 0:
        db_catalog.catalog_mark_deleted(tipo, id_, True)
    else:
        _erase_photos(tipo, id_)
        db_catalog.catalog_delete_row(tipo, id_)
    if tipo == "medicos":
        # Médico = usuario: a disabled doctor keeps no usable login. The
        # account is deactivated, never deleted (audit trail).
        cuenta = db_operators.operator_by_doctor_id(id_)
        # An administrator who is also this doctor keeps the account: the
        # administration is not the doctor's login.
        if cuenta and cuenta.get("activo") and cuenta.get("rol") == "medico":
            db_operators.operator_deactivate(cuenta["usuario"])
        # Nor a pending request parked on it: the desk sees it unassigned.
        db_booking.unassign_pending_of_doctor(id_)
    catalog.invalidate()
    return {"ok": True, "turnos": max(turnos, 0), "conservado": turnos != 0 or otras != 0}


def restore_item(tipo, id_):
    """Restoring the base cannot bypass exclusive schedule ownership."""
    if tipo not in ("servicios", "medicos"):
        return _restore_item(tipo, id_)
    with db_catalog.catalog_schedule_lock() as connection:
        catalog.invalidate()
        _restore_item(tipo, id_, connection)
    catalog.invalidate()
    return {"ok": True, "item": _item_out(tipo, id_)}


def _restore_item(tipo, id_, connection=None):
    in_base = _in_base(tipo, id_)
    row = db_catalog.catalog_get(tipo, id_)
    if not in_base and row is None:
        # Nothing to restore anywhere: the family's 404, never a 200 with an
        # empty item.
        raise ContentError(404, "no_encontrado")
    original = next((it for it in catalog.base_items(tipo) if it.get(catalog.id_key(tipo)) == id_), None) if in_base else (row or {}).get("doc")
    if original and tipo in ("servicios", "medicos"):
        _check_schedule(tipo, original, {}, connection)
    if in_base:
        db_catalog.catalog_delete_row(tipo, id_, connection=connection)
    elif row is not None and row.get("borrado"):
        db_catalog.catalog_mark_deleted(tipo, id_, False, connection=connection)
    catalog.invalidate()
    return {"ok": True, "item": _item_out(tipo, id_) if connection is None else original}


def knowledge_properties(body):
    """Generate knowledge properties; may raise ``ContentError`` (400/503/502)."""
    content = str(body.get("content") or "").strip()
    if not content:
        raise ContentError(400, "Escribí el contenido antes de actualizar las propiedades.")
    categories = body.get("categories") if isinstance(body.get("categories"), list) else []
    current = body.get("current") if isinstance(body.get("current"), dict) else {}
    if ai_referral._env("TURNOS_FAKE_AI") == "1":
        return {"properties": _knowledge_fake_properties(content, categories, current)}
    if not ai_client.available():
        raise ContentError(503, "La IA no está configurada.")
    prompt = (
        "Propose concise Spanish editorial properties for this public-health knowledge-base article. "
        "Return JSON with title, one-sentence summary, tags, jurisdiction, and parent_id. "
        "Use only the supplied content, preserve its meaning, do not invent facts or sources, and choose "
        "parent_id only from the supplied categories.\n\n"
        f"Categories: {json.dumps(categories, ensure_ascii=False)}\n"
        f"Current properties: {json.dumps(current, ensure_ascii=False)}\n"
        f"Content:\n{content[:12000]}"
    )
    from app.core import pii
    anon = pii.Scrubber()
    prompt = anon.scrub(prompt)
    request = {
        "model": ai_referral._model(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    try:
        data = ai_client.call(
            request,
            timeout=30,
            headers_extra={"X-Title": theme.brand_name() + " - propiedades de conocimiento"},
        )
        text = anon.restore(data["choices"][0]["message"]["content"])
        raw = ai_referral.extract_json(text) or json.loads(text)
        properties = _sanitize_knowledge_properties(raw, categories, current)
        if not properties["title"]:
            raise ValueError("respuesta sin título")
        ai_referral._report_ai(None)
    except Exception as exc:  # noqa: BLE001
        ai_referral._report_ai(exc)
        raise ContentError(502, "No se pudieron actualizar las propiedades.") from exc
    return {"properties": properties}


# ---- helpers -----------------------------------------------------------

def _item_out(tipo, id_):
    """El doc mergeado + metadatos de un id puntual (para el `item` de las
    respuestas de escritura) — None si el id ya no existe en ningún lado."""
    key = catalog.id_key(tipo)
    for it in catalog.admin_list(tipo):
        if it.get(key) == id_:
            return it
    return None


def _in_base(tipo, id_):
    key = catalog.id_key(tipo)
    return any(it.get(key) == id_ for it in catalog.base_items(tipo))

def _name_of(tipo, id_):
    """Nombre del item (para contar turnos viejos de un médico, que se
    guardaron por nombre). Nunca rompe el borrado si no lo encuentra."""
    try:
        key = catalog.id_key(tipo)
        for it in catalog.admin_list(tipo):
            if it.get(key) == id_:
                # "name" for centros (renamed 2026-07-30), "nombre" for the
                # other types (not renamed yet) — see booking_requests.py's
                # own it.get("nombre") or it.get("name") idiom.
                return it.get("nombre") or it.get("name") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _with_history(tipo, id_):
    """Cuántos turnos referencian a este registro. Si la base no contesta,
    devuelve -1: ante la duda NO se borra en duro (mejor un registro escondido
    de más que un turno huérfano)."""
    try:
        return db_slots.count_appointments_of(tipo, id_, _name_of(tipo, id_))
    except Exception:  # noqa: BLE001
        return -1


def _other_references(id_):
    """Filas fuera de las solicitudes que nombran a este médico (entrevistas,
    consultas, resúmenes, cola del día, su cuenta — `db_slots.DOCTOR_ID_COLUMNS`).
    Mismo criterio que `_with_history`: si la base no contesta, -1 y no se
    borra en duro (problems.md №6, 2026-10-23)."""
    try:
        return db_slots.doctor_other_references(id_)
    except Exception:  # noqa: BLE001
        return -1


def _erase_photos(tipo, id_):
    """A record deleted for good takes its photos along (2026-11-06): its id
    comes from its name, so the next doctor or center with the same name would
    show the previous one's photo, and a doctor's private original would stay on
    disk. A disabled record keeps them (restoring it brings them back). Before
    the row goes: a photo that cannot be removed keeps the record, and the
    desk can try again."""
    ent = {"medicos": "doctors", "centros": "centers"}.get(tipo)
    if ent is None:
        return
    try:
        doctor_photos.erase(id_, ent)
    except OSError as exc:
        raise ContentError(507, "No se pudo quitar la foto.") from exc


def _refuse_with_upcoming(id_):
    """Un médico con turnos por venir no se deshabilita: los pacientes
    llegarían a un turno con un médico que ya no está en ninguna lista
    (2026-10-23). La mesa los reasigna o cancela antes. Una base que no
    contesta no se toma como «sin turnos»: el 503 de siempre."""
    n = db_slots.doctor_upcoming_appointments(id_, _name_of("medicos", id_))
    if not n:
        return
    lang = i18n.active()
    message = i18n.tn_or(
        "admin.srv.doctor.upcoming", n,
        "Tiene {n} turno por venir: reasignalo o cancelalo antes de deshabilitar al médico.",
        "Tiene {n} turnos por venir: reasignalos o cancelalos antes de deshabilitar al médico.",
        lang)
    # The remedy, not just the refusal: the desk lands on Solicitudes filtered
    # to this doctor's UPCOMING approved appointments, where each one is moved
    # or cancelled (remedies, 2026-10-23).
    enlace = "/admin/solicitudes?" + urlencode({"medico": id_, "estado": "aprobada", "proximos": "1"})
    raise ContentError(409, {"error": message, "turnos_futuros": n, "enlace": enlace})
