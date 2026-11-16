"""analytics_schedule.py — the daily snapshot of PUBLIC availability for the analytics stream.

Slots are not stored anywhere: they are computed from the doctors' and services' agendas
(`site_availability._agenda_day_hours`). This module computes them for the next DAYS days, per
doctor / center / service / day, and adds how many appointments are already held:

  hours        open hours of that source on the day (the agenda's windows)
  slots_total  every slot the agenda generates, the desk-only (web quota 0) ones included
  slots_web    the slots a patient can book on the web
  taken        pending + approved appointments of that doctor, center and service on the day
  free         max(0, slots_web - taken): an approximation, a held phone slot may sit in a desk-only hour

Only counts leave this module: no appointment time, no request, no patient. The doctor's id and name are
public catalog data (the site lists them).
"""

import datetime

from app.core import clock, db_stream, tz

DAYS = 60


def _naive_local(now):
    return now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now


def build(now=None):
    """The snapshot rows (dicts of the `analytics_schedule` columns except `snapshot`)."""
    from app.modules.turnos import booking_horizon, doctor_windows
    from app.patient_web import catalog, site_availability

    today = _naive_local(now or clock.now()).date()
    # The whole day counts (past slots of today too), so `taken` and the slot totals describe the same slots.
    now = datetime.datetime.combine(today, datetime.time.min)
    last = today + datetime.timedelta(days=DAYS - 1)
    services = catalog.services()
    rows = {}
    order = {}
    for doctor in catalog.doctors():
        did = str(doctor.get("id") or "")
        if not did:
            continue
        sources, _managed = doctor_windows.sources(doctor, services)
        for src in sources:
            center, service = str(src.get("centro") or ""), str(src.get("servicio") or "")
            for i in range(DAYS):
                day = today + datetime.timedelta(days=i)
                web = site_availability._agenda_day_hours(src["franjas"], src["excepciones"], day, now)
                with booking_horizon.staff():
                    total = site_availability._agenda_day_hours(src["franjas"], src["excepciones"], day, now)
                if not total:
                    continue
                windows = doctor_windows.windows_of_day([src], day)
                hours = sum(_minutes(w["hasta"]) - _minutes(w["desde"]) for w in windows) / 60.0
                key = (day, did, center, service)
                row = rows.setdefault(key, {"day": day, "doctor": did, "center": center, "service": service,
                                            "hours": 0, "slots_total": 0, "slots_web": 0, "taken": 0, "free": 0})
                row["hours"] = round(row["hours"] + hours, 2)
                row["slots_total"] += len(total)
                row["slots_web"] += len(web)
                order.setdefault((day, did), key)
    for (day, did, center, service), n in db_stream.taken_by_day(today, last, tz.tz_name()).items():
        key = (day, did, center, service)
        if key not in rows:
            key = order.get((day, did))
        if key is not None:
            rows[key]["taken"] += n
    for row in rows.values():
        row["free"] = max(0, row["slots_web"] - row["taken"])
    return sorted(rows.values(), key=lambda r: (r["day"], r["doctor"], r["center"], r["service"]))


def _minutes(hhmm):
    h, m = str(hhmm).split(":")[:2]
    return int(h) * 60 + int(m)
