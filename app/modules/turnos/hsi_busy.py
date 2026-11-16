"""modules/turnos/hsi_busy.py — hours taken by appointments created straight in HSI.

An appointment the front desk of the hospital creates in HSI has no local request,
so before 2026-11-11 the site could offer the same hour to a second patient. The
inbound sync already mirrors it (`hsi_appointments`, `hsi_doctor_schedules`); this
module turns that mirror into busy minutes, ONE reader for every place that counts
free hours (the public site and the wizard through `slot_holds`, the doctor's busy
minutes through `booking_capacity`, the day queue's free hours).

An HSI appointment takes an hour only when it is unambiguous:
  * not cancelled, in the future, and linked to no local request (a linked one is
    already counted as that request);
  * its diary's professional resolves to exactly ONE catalog doctor
    (`doctor_appointments.resolve_doctor_id`, the name match approval already uses);
  * its minute lies inside a window of that doctor's agenda for a service that books.
Anything else takes nothing and never raises (degrade, never fail). With the sync
off, or no EMR routed, the answer is empty — the behaviour before this module.
"""

import time
from datetime import datetime

from app.core import tz

_TTL = 3.0
_CACHE = {"ts": 0.0, "val": None}


def invalidate():
    _CACHE["ts"] = 0.0
    _CACHE["val"] = None


def _enabled():
    from app.modules.turnos import hsi_journal
    return not hsi_journal._off() and hsi_journal._emr_selected()


def _parse(raw):
    """ISO text with offset → (dia, hora) in the installation timezone, or None."""
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    dt = dt.astimezone(tz.tzinfo()) if dt.tzinfo else dt.replace(tzinfo=tz.tzinfo())
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), dt


def _in_window(medico, service, dia, hora):
    from app.modules.turnos import doctor_windows
    fuentes = [{"franjas": doctor_windows._bands(a), "excepciones": a.get("excepciones") or [],
                "centro": service.get("centro"), "servicio": service.get("id")}
               for a in doctor_windows.for_service(medico, service)]
    return any(w["desde"] <= hora < w["hasta"]
               for w in doctor_windows.windows_of_day(fuentes, dia))


def _compute():
    from app.core import db_hsi
    from app.modules.turnos import doctor_appointments, schedule_policy
    from app.patient_web import catalog, site_availability
    now = datetime.now(tz.tzinfo())
    rows = db_hsi.hsi_future_unlinked_appointments(now.strftime("%Y-%m-%d"))
    by_doctor, by_service = {}, set()
    if not rows:
        return by_doctor, by_service
    doctors = {m.get("id"): m for m in catalog.doctors()}
    services = [s for s in catalog.services()
                if not s.get("_borrado") and site_availability._offers_appointments(s)]
    resolved = {}
    for r in rows:
        ap = r["appointment"] or {}
        parsed = _parse(ap.get("scheduled_at"))
        if not parsed or parsed[2] <= now:
            continue
        name = ((r["schedule"] or {}).get("professional_name") or "").strip()
        if not name:
            continue
        if name not in resolved:
            resolved[name] = doctor_appointments.resolve_doctor_id(name)
        did = resolved[name]
        medico = doctors.get(did) if did else None
        if not medico:
            continue
        dia, hora = parsed[0], parsed[1]
        hit = [s for s in services
               if schedule_policy.belongs(medico, s) and _in_window(medico, s, dia, hora)]
        if not hit:
            continue
        by_doctor.setdefault(did, set()).add((dia, hora))
        by_service.update((s["id"], dia, hora) for s in hit)
    return by_doctor, by_service


def _load():
    now = time.time()
    if _CACHE["val"] is not None and now - _CACHE["ts"] < _TTL:
        return _CACHE["val"]
    try:
        val = _compute() if _enabled() else ({}, set())
    except Exception:  # noqa: BLE001 — a mirror problem must never break booking
        val = ({}, set())
    _CACHE["ts"], _CACHE["val"] = now, val
    return val


def busy_by_service():
    """{(servicio_id, dia, hora)} the HSI-made appointments take."""
    return set(_load()[1])


def busy_of_doctor(medico_id):
    """{(dia, hora)} the HSI-made appointments take of one catalog doctor."""
    return set(_load()[0].get(medico_id) or ())
