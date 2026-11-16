"""modules/turnos/doctor_windows.py — WHEN a doctor attends, one source.

The windows that decide a doctor's hours, in the order the booking engine
resolves them: the agendas of the services that manage the doctor
(`schedule_policy.managed_services`, the pool relation the slot generator
books with), else the windows of each of the doctor's centers, else the legacy
flat `agenda`. A day is read with the slot generator's own resolution
(`site_availability._day_exceptions`: closures, ranges, site holidays, `extra`
windows).

A service's agenda replaces the doctor's own windows for THAT service only: a
doctor it manages who keeps own windows for a service without an agenda of its
own, that books appointments, attends those too (`sources` adds them, through
`for_service`).

`for_service` is what the slot generator books a service with (the wizard,
the band, the desk's suggestion, approval) and `sources` is built from the
same pieces, so the windows booked and the windows shown cannot drift.

Read by the public doctor page («Cuándo atiende», the next seven days), the
desk's day queue, free hours and capacity (`doctor_appointments`) and the
printed day sheet (`agenda_print`). Before 2026-11-08 the doctor page read the
doctor's own windows only, so a schedule set on the service in the panel left
the page empty or telling other hours than the ones booked.
"""

from datetime import date


def hhmm(v):
    """"8:00" → "08:00"; None when it is not a time."""
    try:
        h, m = (int(x) for x in str(v or "").split(":"))
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 24 and 0 <= m < 60):
        return None
    return f"{h:02d}:{m:02d}"


def _bands(agenda):
    agenda = agenda or {}
    return agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])


def service_schedule(agenda, sid):
    """A doctor's schedule, trimmed to the windows that belong TO this service.

    The same professional sees patients in different offices on different
    days (2026-07-24 hospital meeting, the reference-deployment counterpart about Lazcano: "Mondays he's in
    contraception, Tuesdays in general medicine; he doesn't do the same
    thing on Mondays as on Tuesdays"). The window says which office it
    belongs to (`franjas[].servicio`), but that tag was only used to SHOW the
    schedule: the grid kept offering the whole schedule, so the
    contraception appointment showed up as if it were general medicine's.

    An UNtagged window still counts for the service: most of the catalog
    isn't tagged yet and filtering it out would leave whole services with no
    appointments. Returns None when no window of its own is left.
    """
    if not isinstance(agenda, dict):
        return None
    franjas = agenda.get("franjas")
    if not franjas:
        return agenda  # single window: doesn't say which office it is
    propias = [fr for fr in franjas
               if not str((fr or {}).get("servicio") or "").strip()
               or str(fr.get("servicio")).strip() == sid]
    if not propias:
        return None
    if len(propias) == len(franjas):
        return agenda
    return dict(agenda, franjas=propias)


def for_service(medico, service):
    """The agendas that book `service` with this doctor — what the slot
    generator offers (the wizard, the band, the desk's suggestion, approval):
    the service's agenda when it has one and the doctor is in its pool, else
    the doctor's own windows at the service's center that belong to it
    (`service_schedule`), the service's own closures on top
    (`schedule_policy.doctor_agendas`)."""
    from app.modules.turnos import schedule_policy
    sid = (service or {}).get("id")
    return [a for ag in schedule_policy.doctor_agendas(medico or {}, service or {})
            if (a := service_schedule(ag, sid))]


def sources(medico, services=None):
    """`[{"franjas", "excepciones", "centro", "servicio"}]`, one per agenda that
    decides when the doctor attends; `managed` is True when services own them.
    `servicio` is the managing service's id (None for the doctor's own windows,
    whose bands may carry their own `servicio` tag). `services` = the catalog
    services when the caller already holds them (a loop over doctors).

    A service's agenda replaces the doctor's own windows only for that
    service (hub brief 2026-11-08): a doctor it manages who also keeps own
    windows for a service WITHOUT an agenda of its own gets those too —
    exactly the windows `for_service` books that service with. Only a
    service that books (`site_availability._offers_appointments`): a walk-in
    one offers no slot, so its windows are not booked hours (the demo's
    general practitioners also staff the walk-in desk of their clinic)."""
    from app.modules.turnos import schedule_policy
    from app.patient_web import catalog
    medico = medico or {}
    pool = catalog.services() if services is None else services
    managed = schedule_policy.managed_services(medico, pool)
    if managed:
        out = [{"franjas": _bands(s.get("agenda")),
                "excepciones": (s.get("agenda") or {}).get("excepciones") or [],
                "centro": s.get("centro"), "servicio": s.get("id")} for s in managed]
        from app.patient_web import site_availability
        for s in pool:
            if (s.get("_borrado") or schedule_policy.has_schedule(s) or not schedule_policy.belongs(medico, s)
                    or not site_availability._offers_appointments(s)):
                continue
            out += [{"franjas": _bands(a), "excepciones": a.get("excepciones") or [],
                     "centro": s.get("centro"), "servicio": s.get("id")}
                    for a in for_service(medico, s)]
        return out, True
    own = [{"franjas": c.get("franjas") or [], "excepciones": c.get("excepciones") or [],
            "centro": c.get("centro"), "servicio": None}
           for c in (medico.get("centros") or []) if isinstance(c, dict)]
    if any(o["franjas"] for o in own):
        return own, False
    flat = medico.get("agenda") or {}
    if _bands(flat):
        return [{"franjas": _bands(flat), "excepciones": flat.get("excepciones") or [],
                 "centro": medico.get("centro"), "servicio": None}], False
    return [], False


def windows_of_day(fuentes, dia):
    """`[{"desde", "hasta", "centro", "servicio"}]` the `fuentes` open on `dia`
    (a date or "YYYY-MM-DD"), sorted, without repeats: a closed day drops its
    source, an `extra` exception adds a window — the slot generator's day."""
    from app.patient_web import site_availability
    try:
        d = dia if isinstance(dia, date) else date.fromisoformat(str(dia)[:10])
    except ValueError:
        return []
    weekday = d.isoweekday() % 7
    seen = set()
    for src in fuentes:
        excs = site_availability._day_exceptions(src.get("excepciones") or [], d.isoformat())
        if any(e.get("tipo") == "cerrado" for e in excs):
            continue
        ventanas = [fr for fr in src.get("franjas") or [] if isinstance(fr, dict) and weekday in (fr.get("dias") or [])]
        ventanas += [e for e in excs if e.get("tipo") == "extra"]
        for v in ventanas:
            desde, hasta = hhmm(v.get("desde")), hhmm(v.get("hasta"))
            if desde and hasta and desde < hasta:
                servicio = src.get("servicio") or str(v.get("servicio") or "").strip()
                seen.add((desde, hasta, src.get("centro") or "", servicio or ""))
    return [{"desde": a, "hasta": b, "centro": c or None, "servicio": s or None}
            for a, b, c, s in sorted(seen)]
