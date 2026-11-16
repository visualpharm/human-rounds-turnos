"""consult_admin.py — la consulta del médico sobre una entrevista previa.

Rutas:
  GET  /api/admin/intake/consult/<interview_id>   (fase 3, implementada) —
       native staff gate (`permitir_medico=True`); un rol
       'medico' sólo ve entrevistas de SU PROPIO médico (db_operators.operator_doctor_id),
       404 (no 403 — no confirmamos que el id de otro médico existe, mismo
       criterio que doctor_appointments._my_referral) si la entrevista es de
       otro médico o no existe. admin/operator ven cualquiera. Devuelve la
       fila completa de `interviews` (sin `patient_token` — es la
       autorización del CHAT del paciente, no algo que el panel deba ver),
       el diferencial/exámenes sugeridos de la IA (sólo sugerencia — el
       panel ya NO preselecciona nada: cada elección es explícita del
       clínico), los mensajes del transcript, el nombre/matrícula del médico
       y los catálogos (estudios/diagnósticos/
       medicamentos/frecuencias) filtrados por la especialidad del médico
       asignado.

  POST /api/admin/intake/consult/<interview_id>/confirm   (fase 5) —
       {diagnosticos, estudios, medicacion, notas, patient_reviewed,
       choices_reviewed} -> coerciona/valida cada
       ítem contra el catálogo de la especialidad (los `custom` pasan como
       texto libre), exige identidad y selección revisadas idénticas a lo
       que se va a guardar, compone borradores receta_json/orden_json sin
       reescritura de IA, persiste una fila nueva de `consultations` con tokens
       frescos y devuelve {ok, receta_url, orden_url}. UNA consulta vigente
       por entrevista (2026-10-23, problems.md №1): si ya hay una, el confirm
       responde 409 con `confirmada` salvo que traiga `reemplaza_id` = el id
       de esa vigente («Reemplazar receta y orden»); entonces nace un juego
       de copias nuevo (nunca una edición del que ya se le dio al paciente)
       y el viejo queda MARCADO como reemplazado: su link NO se borra — sigue
       mostrando lo emitido con «ANULADA — REEMPLAZADA» y el link al nuevo
       (the product owner, hub, 2026-10-23).
"""

import re
import secrets

from app.core import access_log
from app.core import db_booking
from app.core import db_accounts
from app.core import db_clinical
from app.core import db_chat
from app.core import format
from app.core import i18n
from app.core.status_error import StatusError
from app.core import tz as _tz
from app.modules.intake import intake_catalog, consult_compose, health_profile

_MAX_ITEMS = 30
_MAX_LABEL = 240
_MAX_FIELD = 120
_MAX_NOTAS = 4000


class ConsultAdminError(StatusError):
    """A typed 4xx raised by the domain layer for the native router."""


def guard_own_interview_or_404(identity, row, action="entrevista"):
    """Only the administrator opens any interview; every other role (the
    medico preset, and since K10 any custom role holding `atencion_medica`)
    only those of the doctor its account is linked to (`access_log.scoped`)
    — 404, not 403: we don't confirm that another doctor's interview id
    exists, same criterion as `doctor_appointments._my_referral`. Allowed or
    not, the attempt lands in the clinical access log; a log that can't be
    written refuses the read (503)."""
    allowed = True
    if access_log.scoped(identity):
        medico_id = access_log.doctor_id(identity)
        allowed = bool(medico_id) and str(row.get("doctor_id") or "") == medico_id
    # AccessLogUnavailable is a DbUnavailable: the router answers it with 503.
    access_log.record(identity, action, row.get("dni"), target_id=row.get("id"),
                      allowed=allowed)
    if not allowed:
        raise ConsultAdminError(404, {"error": "entrevista no encontrada"})


def _doctor_especialidad(doctor_id):
    """Especialidad del médico del catálogo, o None (sin médico asignado
    todavía, o catálogo caído — best-effort, nunca rompe la consulta)."""
    from app.patient_web import catalog
    return catalog.doctor_specialty(doctor_id)


def _mensajes(session_key):
    """El transcript que lee el médico. Los marcadores del agente ([fin],
    [opciones: …]) son protocolo de la entrevista: se sacan con el mismo
    interview_agent.sin_marcadores que la vista del paciente — el médico
    leía «… cuando te atienda. [fin]» (plan de lanzamiento P4). Un mensaje
    del asistente que era sólo marcador no deja una burbuja vacía."""
    from app.modules.intake import interview_agent
    historial = db_chat.chat_history("interview", session_key, 200) or []
    out = []
    for m in historial:
        if m.get("role") not in ("user", "assistant"):
            continue
        texto = m.get("content")
        if m.get("role") == "assistant" and isinstance(texto, str):
            texto, _ = interview_agent.sin_marcadores(texto)
            if not texto.strip():
                continue
        out.append({"role": m.get("role"), "content": texto})
    return out


# ---- confirm: coerción del body ------------------------------------------

def _trim(v, maxlen):
    return str(v or "").strip()[:maxlen]


_CIE10_RE = re.compile(r"^([A-Z])(\d{2})\.?([0-9A-Z]{1,4})?$")


def _codigo_cie10(raw):
    """Código CIE-10 de un diagnóstico escrito a mano, normalizado como lo
    imprime la orden («j209» → «J20.9»), o None si no tiene forma de código.
    Sólo la FORMA (letra + dos dígitos + subcategoría opcional): que el código
    exista y corresponda lo decide el médico — no hay catálogo CIE-10 completo
    en el pack (2026-10-23). Espejo de admin-consult.mjs:cie10."""
    m = _CIE10_RE.match(str(raw or "").strip().upper())
    if not m:
        return None
    return m.group(1) + m.group(2) + ("." + m.group(3) if m.group(3) else "")


def _coerce_diagnosticos(raw, catalogo):
    """`id` no-custom se valida contra el catálogo (label/código/sistema
    SIEMPRE del catálogo, nunca de lo que mandó el cliente — evita que un
    id real llegue con un código inventado); un `id` que no está en el
    catálogo se descarta entero (no se inventa nada a partir de un id
    ajeno). `custom: true` pasa como texto libre, recortado."""
    by_id = {d.get("id"): d for d in (catalogo or []) if d.get("id")}
    out = []
    for item in (raw or [])[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        custom = bool(item.get("custom"))
        did = _trim(item.get("id"), 80)
        if did and not custom:
            cat = by_id.get(did)
            if not cat:
                continue
            out.append({"id": did, "label": cat.get("label") or "",
                        "codigo": cat.get("codigo"), "sistema": cat.get("sistema"),
                        "custom": False})
            continue
        label = _trim(item.get("label"), _MAX_LABEL)
        if not label:
            continue
        # A hand-written diagnosis may carry its CIE-10 code (the pack only
        # has a cardiology catalog, doctor.json m14): only a code-shaped value
        # reaches the printed order, never free text in the code slot.
        codigo = None
        if _trim(item.get("codigo"), 40):
            codigo = _codigo_cie10(item.get("codigo"))
            if not codigo:
                raise ConsultAdminError(400, {"error": i18n.t_or(
                    "admin.reports.consulta.codigo_invalido",
                    "«{codigo}» no es un código CIE-10 (letra y dos números, por ejemplo J20.9). Corregilo o dejalo vacío.",
                    i18n.active(), codigo=_trim(item.get("codigo"), 40))})
        out.append({"id": None, "label": label, "codigo": codigo,
                    "sistema": "CIE-10" if codigo else None,
                    "custom": True})
    return out


def _coerce_estudios(raw, catalogo):
    by_id = {e.get("id"): e for e in (catalogo or []) if e.get("id")}
    out = []
    for item in (raw or [])[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        custom = bool(item.get("custom"))
        eid = _trim(item.get("id"), 80)
        if eid and not custom:
            cat = by_id.get(eid)
            if not cat:
                continue
            out.append({"id": eid, "label": cat.get("label") or "", "custom": False})
            continue
        label = _trim(item.get("label"), _MAX_LABEL)
        if not label:
            continue
        out.append({"id": None, "label": label, "custom": True})
    return out


def _coerce_medicacion(raw):
    out = []
    for item in (raw or [])[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        nombre = _trim(item.get("nombre"), _MAX_LABEL)
        if not nombre:
            continue
        out.append({
            "nombre": nombre,
            "dosis": _trim(item.get("dosis"), _MAX_FIELD),
            "frecuencia": _trim(item.get("frecuencia"), _MAX_FIELD),
            "duracion": _trim(item.get("duracion"), _MAX_FIELD),
            "custom": bool(item.get("custom")),
        })
    return out


def _doctor_info(doctor_id):
    """nombre/matrícula/especialidad para el encabezado de los imprimibles
    — best-effort, nunca rompe confirm() por un catálogo caído."""
    from app.patient_web import catalog
    m = catalog.doctor_doc(doctor_id, tolerant=True)
    if m is None:
        return {}
    matricula = m.get("matricula")
    numero = matricula.get("numero") if isinstance(matricula, dict) else matricula
    return {"nombre": m.get("nombre") or "",
            "especialidad": m.get("especialidad") or "",
            "matricula": numero or ""}


def _paciente_nombre(row):
    """Nombre real del paciente para el consultorio, la receta y la orden.
    Misma prioridad que doctor_patients._build (Mis pacientes): la CUENTA
    registrada gana si existe, si no la SOLICITUD de turno matcheada (el
    camino normal post-reserva: `matched=True`, `entered_name`/`_surname`
    nunca se completan ahí), si no lo tipeado en la puerta (QR walk-in) —
    "DNI <n>" es el ÚLTIMO recurso, sólo para una entrevista sin cuenta, sin
    solicitud y sin nombre tipeado. Pasa por format.person_name para que un
    nombre en MAYÚSCULAS (HSI, mostrador) se muestre normal."""
    dni = row.get("dni")
    nombre = None

    if dni:
        try:
            usuario = db_accounts.get_user(dni)
        except Exception:  # noqa: BLE001 — nunca rompe el consultorio por esto
            usuario = None
        if usuario:
            nombre = " ".join(
                p for p in (usuario.get("nombre"), usuario.get("apellido")) if p
            ).strip()

    if not nombre and row.get("matched") and row.get("request_id"):
        try:
            solicitud = db_booking.get_booking_request(row.get("request_id"))
        except Exception:  # noqa: BLE001
            solicitud = None
        if solicitud:
            nombre = " ".join(
                p for p in (solicitud.get("nombre"), solicitud.get("apellido")) if p
            ).strip()

    if not nombre:
        nombre = " ".join(
            p for p in (row.get("entered_name"), row.get("entered_surname")) if p
        ).strip()

    if nombre:
        return format.person_name(nombre)
    if dni:
        return f"DNI {dni}"
    return "Paciente"


def confirm_payload(identity, iid, body):
    """Pure domain body of POST .../consult/<iid>/confirm — takes the
    ALREADY-PARSED JSON body; may raise `ConsultAdminError` (400/404/409) or
    `db.DbUnavailable`."""
    row = db_clinical.interview_get(iid)
    if not row:
        raise ConsultAdminError(404, {"error": "entrevista no encontrada"})
    guard_own_interview_or_404(identity, row, "consulta")

    doctor_id = row.get("doctor_id")
    if not doctor_id:
        raise ConsultAdminError(400, {"error": "la entrevista no tiene médico asignado"})

    body = body if isinstance(body, dict) else {}
    especialidad = _doctor_especialidad(doctor_id)
    diagnosticos = _coerce_diagnosticos(body.get("diagnosticos"), intake_catalog.diagnoses(especialidad))
    estudios = _coerce_estudios(body.get("estudios"), intake_catalog.exams(especialidad))
    medicacion = _coerce_medicacion(body.get("medicacion"))
    notas = _trim(body.get("notas"), _MAX_NOTAS)

    reviewed_profile = None
    if body.get("profile_reviewed") is True:
        if not isinstance(body.get("profile"), dict):
            raise ConsultAdminError(400, {"error": "perfil revisado inválido"})
        reviewed_profile = health_profile.normalizar(body["profile"])

    patient = {"nombre": _paciente_nombre(row), "dni": row.get("dni") or ""}
    # Both snapshots are required on every caller of the native endpoint.
    # Old clients must reload/review; omission is never a compatibility bypass.
    if not isinstance(body.get("patient_reviewed"), dict):
        raise ConsultAdminError(400, {"error": i18n.t_or(
            "admin.reports.consulta.revisar_paciente_requerido",
            "Recargá la consulta y revisá los datos del paciente antes de generar el borrador.", i18n.active())})
    if body["patient_reviewed"] != patient:
        raise ConsultAdminError(409, {"error": i18n.t_or(
            "admin.reports.consulta.revisar_paciente_cambio",
            "Los datos del paciente cambiaron. Recargá y revisalos antes de generar el borrador.", i18n.active())})
    if not isinstance(body.get("choices_reviewed"), dict):
        raise ConsultAdminError(400, {"error": i18n.t_or(
            "admin.reports.consulta.revisar_seleccion_requerida",
            "Recargá la consulta y revisá la selección antes de generar el borrador.", i18n.active())})
    expected_choices = consult_compose.review_choices(diagnosticos, estudios, medicacion, notas)
    if body["choices_reviewed"] != expected_choices:
        raise ConsultAdminError(409, {"error": i18n.t_or(
            "admin.reports.consulta.revisar_seleccion_cambio",
            "La selección cambió desde la revisión. Recargá y revisá los diagnósticos, estudios, medicación e indicaciones antes de generar el borrador.", i18n.active())})

    receta_json, orden_json = consult_compose.compose(
        doctor=_doctor_info(doctor_id),
        paciente=patient,
        diagnosticos=diagnosticos, estudios=estudios, medicacion=medicacion, notas=notas,
        allow_ai=False,
    )
    receta_json["borrador"] = True
    orden_json["borrador"] = True

    receta_token = secrets.token_urlsafe(24)
    orden_token = secrets.token_urlsafe(24)

    try:
        db_clinical.consultation_confirm(
            interview_id=iid, doctor_id=str(doctor_id), diagnoses=diagnosticos,
            exams=estudios, meds=medicacion, notes=notas,
            receta_json=receta_json, orden_json=orden_json,
            receta_token=receta_token, orden_token=orden_token,
            created_by=identity.get("usuario"),
            reemplaza_id=body.get("reemplaza_id"),
        )
    except db_clinical.ConsultationExists as exc:
        vigente = exc.vigente or db_clinical.consultation_current_for_interview(iid)
        raise ConsultAdminError(409, {
            "error": i18n.t_or(
                "admin.reports.consulta.ya_confirmada",
                "Esta consulta ya tiene receta y orden emitidas. Para cambiarlas, usá «Reemplazar receta y orden».",
                i18n.active()),
            "confirmada": _confirmada_publica(vigente),
        })

    if reviewed_profile is not None:
        # Only human-supplied reviewed values; never model output at close.
        db_clinical.health_profile_save(row.get("dni"), reviewed_profile, iid)

    return {"ok": True, "receta_url": f"/r/{receta_token}", "orden_url": f"/o/{orden_token}"}


def _confirmada_publica(fila):
    """Lo ya emitido en la consulta vigente, en las MISMAS líneas que se
    revisaron e imprimieron (consult_compose.review_choices) — la pantalla
    reabierta lo muestra de sólo lectura con sus links, nunca un formulario
    vacío (problems.md №1). None si la entrevista no tiene consulta vigente."""
    if not fila:
        return None
    lineas = consult_compose.review_choices(
        fila.get("diagnoses") or [], fila.get("exams") or [],
        fila.get("meds") or [], fila.get("notes") or "")
    return {
        "id": fila.get("id"),
        "confirmada_at": _tz.iso(fila.get("created")),
        "confirmada_por": fila.get("created_by"),
        "diagnosticos": lineas["diagnosticos"],
        "estudios": lineas["estudios"],
        "medicacion": lineas["medicacion"],
        "notas": lineas["notas"],
        "receta_url": f"/r/{fila.get('receta_token')}",
        "orden_url": f"/o/{fila.get('orden_token')}",
        "reemplazos": int(fila.get("reemplazos") or 0),
        # Lo elegido tal cual se guardó: «Reemplazar» arranca el formulario
        # desde acá, así una corrección es un cambio y no volver a cargar todo.
        "seleccion": {"diagnosticos": fila.get("diagnoses") or [],
                      "estudios": fila.get("exams") or [],
                      "medicacion": fila.get("meds") or [],
                      "notas": fila.get("notes") or ""},
    }


def consult_payload(identity, iid):
    """Pure domain body of GET .../consult/<iid> — may raise
    `ConsultAdminError` (404) or `db.DbUnavailable`."""
    row = db_clinical.interview_get(iid)
    if not row:
        raise ConsultAdminError(404, {"error": "entrevista no encontrada"})
    guard_own_interview_or_404(identity, row)

    especialidad = _doctor_especialidad(row.get("doctor_id"))
    catalogos = {
        "estudios": intake_catalog.exams(especialidad),
        "diagnosticos": intake_catalog.diagnoses(especialidad),
        "medicamentos": intake_catalog.medications(especialidad),
        "frecuencias": intake_catalog.frequencies(),
    }

    return {
        "id": row.get("id"),
        "doctor_id": row.get("doctor_id"),
        "request_id": row.get("request_id"),
        "entry_mode": row.get("entry_mode"),
        "entered_name": row.get("entered_name"),
        "entered_surname": row.get("entered_surname"),
        "matched": bool(row.get("matched")),
        # Resuelto server-side (cuenta > solicitud matcheada > tipeado en la
        # puerta > "DNI <n>") — el panel lo pinta tal cual, sin repetir la
        # lógica de fallback en el cliente (admin-consulta.js).
        "nombre": _paciente_nombre(row),
        "dni": row.get("dni"),
        # Profesional responsable: la tarjeta de revisión previa a generar
        # muestra nombre/matrícula del MISMO médico que usa confirm()
        # (_doctor_info), best-effort — nunca rompe la consulta.
        "medico": _doctor_info(row.get("doctor_id")),
        "session_key": row.get("session_key"),
        "status": row.get("status"),
        "triage": row.get("triage"),
        "summary": row.get("summary"),
        "brief": row.get("brief") or [],
        "differential": row.get("differential") or [],
        "suggested_exams": row.get("suggested_exams") or [],
        "red_flags": row.get("red_flags") or [],
        "ai_model": row.get("ai_model"),
        "analyzed_at": _tz.iso(row.get("analyzed_at")),
        # "Actualizar entrevista" (2026-08-01): no-nulo -> el paciente agregó
        # información nueva después del primer cierre; el panel usa esto
        # para la marca "Actualizada" sobre el resumen (admin-consulta.js).
        "reopened_at": _tz.iso(row.get("reopened_at")),
        "created": _tz.iso(row.get("created")),
        "updated": _tz.iso(row.get("updated")),
        # patient_token deliberadamente afuera: es la autorización del chat
        # del paciente (mismo modelo que appointment_offers.token), nunca se
        # expone al panel.
        "mensajes": _mensajes(row.get("session_key")),
        "catalogos": catalogos,
        # Lo que esta persona viene contando en entrevistas anteriores
        # (medicación, condiciones, alergias). Es lo que DIJO el paciente, no
        # un registro clínico — el panel lo muestra con esa etiqueta.
        "perfil": (health_profile.leer(row.get("dni")) or {}).get("datos") or {},
        "perfil_propuesta": health_profile.proposal(row.get("id")),
        # La consulta ya confirmada (receta/orden vigentes), o None: el panel
        # la muestra de sólo lectura y sólo un «Reemplazar» explícito abre el
        # formulario de nuevo.
        "confirmada": _confirmada_publica(db_clinical.consultation_current_for_interview(row.get("id"))),
    }
