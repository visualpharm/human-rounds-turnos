"""doctor_patients.py — «Mis pacientes» (rol médico, 2026-08-01): el roster
LONGITUDINAL de un médico — todo paciente alguna vez referido, no sólo el de
hoy (eso es «Pacientes de hoy», doctor_appointments.py). Ordenado por
severidad de triage: quien tuvo una entrevista previa ROJO aparece primero,
sea cual sea la fecha de su turno.

  GET /api/admin/doctor-patients[?medico=<id>]
    - rol medico: SIEMPRE sus propios pacientes (medico_id de la cuenta,
      operator_doctor_id) — el query param se ignora, mismo criterio que
      day-queue (doctor_appointments._day_queue).
    - rol admin/operador: ?medico=<id> elige a quién mirar; sin parámetro,
      lista vacía + el picker de médicos (más simple que inventar "todos los
      pacientes de todos los médicos", que ya es la sección Pacientes).

Cada paciente combina dos fuentes independientes (db_clinical.patients_of_doctor +
db_clinical.interviews_of_doctor, ver sus docstrings): una solicitud referida a este
médico por id, o una entrevista previa cuyo doctor_id es este médico —
incluye las que nunca matchearon una solicitud (QR sin turno). Best-effort:
si el módulo intake está apagado o su lectura falla, el roster sigue
mostrando los pacientes con turno, sólo sin columna de entrevista.

The native FastAPI router in `doctor_patients_router.py` owns the HTTP
boundary. This module contains only its synchronous domain functions.
"""

from datetime import datetime, timezone

from app.core import access_log
from app.core import db_clinical
from app.core import patient_contact
from app.core.status_error import MessageStatusError
from app.modules.turnos import doctor_appointments
from app.core import tz as _tz

_ART = _tz.tzinfo()  # instalación (zoneinfo, DST-safe — ver tz.py)

_TRIAGE_ORDEN = {"ROJO": 0, "NARANJA": 1, "AMARILLO": 2, "VERDE": 3}


class DoctorPatientsError(MessageStatusError):
    """A 400 the caller turns into `{"error": str(exc)}` at `.status`."""


def build_payload(identity, medico_param):
    """Pure port of `_list`'s body (post-gate) — `medico_param` is the raw
    (possibly empty/None) `?medico=` query value."""
    # Sólo el admin elige de qué médico ver la lista; todo otro rol (el
    # médico, y desde K10 un rol propio con «atención médica») ve la de SU
    # médico vinculado, diga lo que diga ?medico=. Con «todos los pacientes»
    # (responsable del producto, 2026-10-23) elige como el admin; sin ?medico= arranca por la de
    # su médico vinculado, si tiene (la pantalla del médico no lo manda).
    acotado = access_log.scoped(identity)

    if acotado:
        medico_id = access_log.doctor_id(identity)
        if not medico_id:
            raise DoctorPatientsError(400, "cuenta_sin_medico")
    else:
        medico_id = (medico_param or "").strip() or None
        if not medico_id and identity.get("rol") != "admin":
            medico_id = access_log.doctor_id(identity) or None

    if not medico_id:
        return {"medico_id": None, "medico": None, "items": [],
                "medicos": _medicos_opciones()}

    medico = _doctor_of(medico_id)
    # Triage, resumen y última entrevista sólo existen para quien tiene la
    # entrevista previa encendida (responsable del producto, 2026-10-27: «triage, en curso» en la
    # lista de un médico que no la tiene). Apagada, ni las columnas ni los
    # datos viajan — mismo flag y misma regla que «Mis turnos».
    entrevista_activa = doctor_appointments._intake_on_for(medico)
    items = _build(medico_id, entrevista_activa)
    # La lista muestra triage y resumen de la última entrevista: cada
    # paciente que aparece queda en el registro de accesos.
    access_log.record_many(identity, "mis_pacientes", [(i.get("dni"), medico_id) for i in items])
    payload = {
        "medico_id": medico_id,
        "medico": (medico or {}).get("nombre") or None,
        "entrevista_activa": entrevista_activa,
        "items": items,
    }
    if not acotado:
        payload["medicos"] = _medicos_opciones()
    return payload


def _doctor_of(medico_id):
    try:
        return doctor_appointments.doctor_of(medico_id)
    except Exception:  # noqa: BLE001
        return None


def _medicos_opciones():
    """[{id,nombre}] para el picker admin/operador — sin las filas rol
    (Enfermería/Técnico/Administrativo) que no ven pacientes propios."""
    try:
        from app.patient_web import catalog
        return sorted(
            ({"id": m.get("id"), "nombre": m.get("nombre")}
             for m in catalog.doctors() if m.get("id") and m.get("nombre") and not m.get("rol")),
            key=lambda m: (m.get("nombre") or "").lower(),
        )
    except Exception:  # noqa: BLE001
        return []


def _entrevistas_de_doctor(medico_id):
    """This doctor's pre-consult interviews (modules/intake), by NAME: the
    "interviews_of_doctor" hook that modules/intake/app.py registers.
    Best-effort: if the intake module is off or its read fails, [] — the
    roster keeps showing patients with appointments, only without the
    interview column (hooks.call() fires nothing while the module is off
    and swallows an exception from a body that blows up)."""
    try:
        from app.core import app_modules
        res = app_modules.hooks.call("interviews_of_doctor", medico_id)
        return res[0] if res else []
    except Exception:  # noqa: BLE001
        return []


def _build(medico_id, con_entrevistas=True):
    try:
        solicitudes = db_clinical.patients_of_doctor(medico_id)
    except Exception:  # noqa: BLE001
        solicitudes = []

    entrevistas = _entrevistas_de_doctor(medico_id) if con_entrevistas else []

    ahora = datetime.now(timezone.utc)
    por_dni = {}

    def paciente(dni):
        return por_dni.setdefault(dni, {
            "dni": dni, "nombre": None, "contacto": None,
            "_ultima_entrevista": None, "_proximo_turno": None,
        })

    for r in solicitudes:
        dni = str(r.get("dni") or "").strip()
        if not dni:
            continue
        p = paciente(dni)
        nombre = " ".join(x for x in (r.get("nombre"), r.get("apellido")) if x).strip()
        if nombre and not p["nombre"]:
            p["nombre"] = nombre
        if not p.get("_solicitud"):
            # the NEWEST request decides who to call (solicitudes come
            # newest first) — same rule as «Mis turnos», core/patient_contact.py
            p["_solicitud"] = r
        turno_dt = r.get("turno_dt")
        if r.get("estado") == "aprobada" and turno_dt and turno_dt >= ahora:
            actual = p["_proximo_turno"]
            if not actual or turno_dt < actual["_dt"]:
                p["_proximo_turno"] = {"_dt": turno_dt, "fecha": turno_dt.astimezone(_ART).isoformat()}

    for iv in entrevistas:
        dni = str(iv.get("dni") or "").strip()
        if not dni:
            continue
        p = paciente(dni)
        nombre = " ".join(x for x in (iv.get("entered_name"), iv.get("entered_surname")) if x).strip()
        if nombre and not p["nombre"]:
            p["nombre"] = nombre
        creado = iv.get("created")
        actual = p["_ultima_entrevista"]
        if not actual or (creado and (not actual["_dt"] or creado > actual["_dt"])):
            p["_ultima_entrevista"] = {
                "_dt": creado,
                "id": iv.get("id"),
                "triage": iv.get("triage"),
                "resumen": iv.get("summary"),
                "estado": iv.get("status"),
                "fecha": _tz.iso(creado),
            }

    solicitudes_contacto = [p.get("_solicitud") or {"dni": dni} for dni, p in por_dni.items()]
    try:
        usuarios = db_clinical.users_by_dnis(sorted(set(por_dni) | set(patient_contact.personas_de(solicitudes_contacto))))
    except Exception:  # noqa: BLE001
        usuarios = {}
    for dni, p in por_dni.items():
        u = usuarios.get(dni)
        if u:
            nombre_cta = " ".join(x for x in (u.get("nombre"), u.get("apellido")) if x).strip()
            if nombre_cta:
                p["nombre"] = nombre_cta
        s = p.pop("_solicitud", None) or {}
        p.update(patient_contact.contacto(dni, s.get("solicitante_dni"), s.get("contacto"), usuarios))

    items = []
    for p in por_dni.values():
        iv = p.pop("_ultima_entrevista")
        turno = p.pop("_proximo_turno")
        if iv:
            iv.pop("_dt", None)
        if turno:
            turno.pop("_dt", None)
        p["ultima_entrevista"] = iv
        p["proximo_turno"] = turno
        items.append(p)

    # Orden compuesto con DOS sorts estables (severidad de triage, después
    # recencia dentro de cada severidad) en vez de reparsear fechas para
    # armar una clave negada: un ISO 8601 ordena lexicográficamente igual
    # que cronológicamente, así que alcanza con comparar el string tal cual.
    items.sort(key=lambda p: (p.get("ultima_entrevista") or {}).get("fecha") or "", reverse=True)
    items.sort(key=lambda p: _TRIAGE_ORDEN.get(
        str((p.get("ultima_entrevista") or {}).get("triage") or "").upper(), 4))
    return items
