"""Patient-only booking window, resolved doctor-at-center → agenda/doctor → service → site.

Days include today, preserving the original range(31) contract. Opening time
holds back the last day until the site's local clock reaches HH:MM.
"""
import contextlib
import contextvars
import re
from datetime import date
from app.core import clock, config, i18n, tz

DEFAULT_DAYS = 31
MAX_DAYS = 365
# The desk's floor: a doctor back from holidays in December must be bookable
# by staff in October (the reference-deployment counterpart, 2026-10-23 — cardiology closed until 26/11–15/12).
STAFF_DAYS = 92
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _clamp(value):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return min(n, MAX_DAYS) if n >= 1 else None


def site_default():
    return _clamp(config.get("BOOKING_HORIZON_DAYS", DEFAULT_DAYS)) or DEFAULT_DAYS


def center_days(service=None, doctor=None):
    """The doctor's own window AT the service's center (`centros[].dias_anticipacion`):
    the same doctor can open further ahead at the hospital than at a CAPS."""
    where = (service or {}).get("centro") if isinstance(service, dict) else None
    if not (where and isinstance(doctor, dict)):
        return None
    for entry in doctor.get("centros") or []:
        if isinstance(entry, dict) and entry.get("centro") == where:
            return _clamp(entry.get("dias_anticipacion"))
    return None


def own_days(doc):
    """The window a doctor or service sets for itself, or None when it inherits."""
    if not isinstance(doc, dict):
        return None
    for source in (doc.get("agenda") or {}, doc):
        value = _clamp(source.get("dias_anticipacion"))
        if value is not None:
            return value
    return None


def days_for(service=None, doctor=None):
    value = center_days(service, doctor)
    if value is not None:
        return value
    for doc in (doctor, service):
        value = own_days(doc)
        if value is not None:
            return value
    return site_default()


def opens_for(service=None, doctor=None):
    for doc in (doctor, service):
        if isinstance(doc, dict):
            for source in (doc.get("agenda") or {}, doc):
                value = source.get("apertura_turnos")
                if isinstance(value, str) and _TIME.fullmatch(value):
                    return value
    value = config.get("BOOKING_OPENS_AT", "")
    return value if isinstance(value, str) and _TIME.fullmatch(value) else "00:00"


def _local_now(now):
    now = now or clock.now()
    return now.astimezone(tz.tzinfo()).replace(tzinfo=None) if now.tzinfo else now


# The window is the PATIENT's (module docstring). The desk books on the
# doctor's real agenda however far the public window reaches: a doctor with a
# one-week window and a closure until Friday showed the desk «no tiene horarios
# con lugar en el próximo mes» (contraparte técnica, 2026-10-19). Desk code runs inside
# `staff()`, which widens the window to at least the site default and
# STAFF_DAYS.
_STAFF = contextvars.ContextVar("booking_horizon_staff", default=False)


@contextlib.contextmanager
def staff():
    token = _STAFF.set(True)
    try:
        yield
    finally:
        _STAFF.reset(token)


def is_staff():
    """True inside `staff()`: the desk is reading the agenda, not the patient."""
    return _STAFF.get()


def available_days(service=None, doctor=None, now=None):
    now = _local_now(now)
    n = days_for(service, doctor)
    if _STAFF.get():
        return max(n, site_default(), STAFF_DAYS)
    return max(0, n - (now.strftime("%H:%M") < opens_for(service, doctor)))


def allows(day, service=None, doctor=None, now=None):
    now = _local_now(now)
    try:
        offset = (date.fromisoformat(str(day)) - now.date()).days
    except (ValueError, TypeError):
        return False
    return 0 <= offset < available_days(service, doctor, now)


def rejection(service):
    from app.patient_web import site_centers
    sid = (service or {}).get("id") or ""
    place = site_centers.main_center_text()
    detalle = (i18n.t_or(
        "booking.error.horizon_detail_center",
        "Ese día todavía no está habilitado. Elegí un horario disponible en la agenda del servicio o acercate a la admisión: {place}.",
        i18n.active(), place=place) if place else i18n.t_or(
        "booking.error.horizon_detail",
        "Ese día todavía no está habilitado. Elegí un horario disponible en la agenda del servicio o acercate al centro de salud.",
        i18n.active()))
    return {"error": "turno_fuera_de_anticipacion", "detalle": detalle, "href": "/service/" + str(sid),
        "accion": {"texto": i18n.t_or("booking.error.horizon_action", "Elegir otro horario", i18n.active()),
                   "url": "/service/" + str(sid) + "/book?slots=1"}}


def check_slot(service_id, day, hour, now=None, doctor_id=None):
    from app.modules.turnos import booking_limits, booking_requests, schedule_policy
    from app.patient_web import catalog
    service = dict(booking_limits._service_doc(service_id) or {}, id=service_id)
    if not doctor_id:
        doctor_id = booking_requests._resolve_slot_doctor(service_id, day, hour)
    doctor = None if schedule_policy.has_schedule(service) else catalog.doctor_doc(doctor_id)
    return None if allows(day, service, doctor, now) else rejection(service)
