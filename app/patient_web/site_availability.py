"""patient_web/site_availability.py — The availability of appointments on the public site.

The next appointment of a service, group or doctor, connector slots, the agenda, hours per service and the appointments offered.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import re
import time
import datetime as _datetime

from app import connectors
from app.connectors.msd import msd_data
from app.modules.turnos import site_holidays
from app.core import i18n
from app.modules.turnos import booking_horizon, doctor_windows, schedule_policy

from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_centers
from app.patient_web import site_doctors
from app.patient_web import site_schedule


# ---------------------------------------------------------------- fragments
_STATUS_BY_MODE = {
    # same visual language as the home page (index.html:statusOf); no live
    # status here — the schedule is already in the row's sub-line.
    "walkin": ("green", "Sin turno", "vení directamente"),
    "walkin_con_orden": ("amber", "Con orden", "por llegada"),
    "solicitud": ("accent", "A solicitud", "pedís tu turno online"),
    "solicitud_con_orden": ("accent", "A solicitud", "subís tu orden"),
}


# ---------------------------------------------------------------- listing.js
# Single table component (listing.js). The server builds {cols, rows, options}
# and the client draws the columns. One datum per cell; first column = next
# appointment, honest and sortable.

def _next_from(modo, best):
    """Honest 'Next appointment' cell: real online slot > walk-in today > by
    order of arrival > the center confirms it. `best`=(dia,hora) or None.
    Returns {v, kind, sort}; sort orders real(0)→walk-in(1)→to-confirm(2).
    With a real slot, `v` comes in friendly format ('Today, 14:40' / 'Tuesday
    July 14, 15:00') and adds the raw dia/hora so the client (listing.js) can
    color-code proximity (calibrated to a public hospital, product owner 2026-07-11):
    ≤7 days green, 8-30 amber, >30 gray; walk-in is always muted."""
    if best:
        return {"v": site_schedule._friendly_date(best[0], best[1]) or site_schedule._fmt_appointment(best[0], best[1]),
                "kind": "turno", "dia": best[0], "hora": best[1],
                "sort": "0" + str(best[0]) + str(best[1])}
    if modo == "walkin":
        return {"v": i18n.t("listing.next.walkin_today", i18n.active()), "kind": "walkin", "sort": "1a",
                "nota": i18n.t("listing.next.walkin_note", i18n.active())}
    if modo == "walkin_con_orden":
        return {"v": i18n.t("listing.next.walkin_order", i18n.active()), "kind": "walkin", "sort": "1b",
                "nota": i18n.t("listing.next.walkin_order_note", i18n.active())}
    return {"v": i18n.t("listing.next.confirmed_by_center", i18n.active()), "kind": "solicitud", "sort": "2"}


def _with_schedule(px, s):
    """Adds the sign's schedule to the walk-in cell: the client (listing.js
    + hours.mjs) shows 'Open now' or 'Opens Thursday, 8:00' — never a
    scheduled slot (the product owner, 2026-07-10: 'say when does it open, or open now')."""
    if s and s.get("horario"):
        px["horario"] = s["horario"]
        # Monthly recurrence ("The first business day of each month"): 'Come
        # in today' lies — without JS the honest fallback is the sign itself;
        # with JS, hours.mjs turns it into the concrete date of the next occurrence.
        if re.search(r"\b(?:de cada|del) mes\b", s["horario"], re.I):
            px["v"] = s["horario"]
    return px


def _opens_same_day(s):
    """A by-order-of-arrival service that ALSO opens its same-day listing
    (2026-07-24 hospital meeting, reference-deployment counterpart: "the no-appointment,
    by-order-of-arrival one, that it opens the same day, so people can grab
    it remotely, but that same day"). It's the only crack in the "walk-in
    never offers appointments" invariant, and stays narrow: only TODAY, never
    tomorrow."""
    return bool(isinstance(s, dict) and s.get("apertura_mismo_dia")
                and s.get("modo") in site_catalog._NO_APPOINTMENT_MODES)


def _offers_appointments(s):
    """Whether this service can offer a concrete appointment. A walk-in
    can't, unless it opens the same day. BOTH ends consult it — the one that
    promises (`_next_appointment_best`, the band, the listing cell) and the
    one that offers (`_appointments_for_service`, the wizard's grid) — so
    they can never diverge."""
    if not isinstance(s, dict):
        return False
    if s.get("modo") in site_catalog._NO_APPOINTMENT_MODES:
        return _opens_same_day(s)
    return True


_OPENING_MEMO = {}  # (service id, doctor ref, today) -> (expires, apertura | None)
_OPENING_TTL = 30  # seconds: `_next_opening` scans up to 400 days per agenda


def _habilita(s, medico_ref=None):
    """When a bookable service with NO free slot starts taking turnos
    (`_next_opening`: ``{"estado": "fecha", "dia", "hora"}`, or
    ``{"estado": "sin_fechas"}``), None when there is no schedule to compute
    from. Memoized briefly — the listings ask once per row."""
    key = (s.get("id"), medico_ref or "", site_base._now_art().date().isoformat())
    hit = _OPENING_MEMO.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    ap = (_no_slots_info(s, medico_ref) or {}).get("apertura")
    if len(_OPENING_MEMO) > 2000:
        _OPENING_MEMO.clear()
    _OPENING_MEMO[key] = (now + _OPENING_TTL, ap)
    return ap


def _with_opening(px, s, medico_ref=None):
    """A request-mode cell with no appointment says WHEN turnos get enabled
    instead of offering a «Sacar turno» that ends in a dead end (the reference-deployment counterpart,
    19/10): adds ``habilita`` to the cell. Cells with a slot, walk-in cells
    and services with no schedule to compute from stay as they were."""
    if px.get("kind") == "solicitud" and _offers_appointments(s):
        ap = _habilita(s, medico_ref)
        if ap:
            px["habilita"] = ap
    return px


def _with_remedies(rows, by="medico"):
    """A row that says «No hay turnos disponibles» gets the next step next to
    it instead of a dead end (demo review 2026-11-15, P8; the remedies rule):
    ``proximo.remedio = {"texto", "href"}``.

    - another row of the same list with a real turno — for a doctor, one that
      shares a specialty (`especialidad_todas`); for a center row (`by`
      ``"centro"``), any — the soonest one: «Hay turno con <nombre> el mié 18
      nov, 08:00» / «Hay turno en <centro> …», linking to that turno;
    - none: «Contanos tu caso», the row's own booking wizard (it takes the
      case when there are no slots).

    Rows with a turno, walk-in rows and rows with nowhere to send the patient
    are left as they are. The cell is replaced, never mutated in place: a
    center row may share its doctor's `proximo`."""
    lang = i18n.active()

    def real(r):
        return str((r.get("proximo") or {}).get("sort") or "").startswith("0")

    for r in rows:
        px = r.get("proximo") or {}
        if not px.get("habilita") or px.get("remedio"):
            continue
        if by == "medico":
            mine = set(r.get("especialidad_todas") or [])
            cands = [o for o in rows if o is not r and real(o) and mine & set(o.get("especialidad_todas") or [])]
        else:
            cands = [o for o in rows if o is not r and real(o)]
        if cands:
            o = min(cands, key=lambda x: str(x["proximo"]["sort"]))
            href = o.get("slot_href") or o.get("turno_href") or o.get("href")
            # the column's one short date (review 2026-11-15, P21), inside a
            # whole-phrase catalog entry: «Hay turno con Ana el mié 18 nov, 08:00»
            when = (site_schedule._short_date_in_phrase(o["proximo"].get("dia"), o["proximo"].get("hora"))
                    or o["proximo"].get("v") or "")
            if by == "medico":
                texto = i18n.t_or("service.noslots.sooner_with", "Hay turno con {nombre} {when}", lang,
                                  nombre=o.get("nombre") or "", when=when)
            else:
                texto = i18n.t_or("service.noslots.sooner_at", "Hay turno en {centro} {when}", lang,
                                  centro=o.get("centro") or "", when=when)
        elif r.get("turno_href"):
            href = r["turno_href"]
            texto = i18n.t_or("service.noslots.case_label", "Contanos tu caso", lang)
        else:
            continue
        if href:
            r["proximo"] = dict(px, remedio={"texto": texto, "href": href})
    return rows


def _service_next(s):
    modo = s.get("modo")
    if modo in site_catalog._NO_APPOINTMENT_MODES:
        if _opens_same_day(s):
            best = _next_appointment_best(s)
            if best:
                # There's room today and it can be booked: the cell talks
                # like any appointment (verb + time button). With no room, it
                # goes back to being what it is — a by-order-of-arrival office.
                return _next_from(modo, best)
        return _with_schedule(_next_from(modo, None), s)
    return _with_opening(_next_from(modo, _next_appointment_best(s)), s)


def _group_best(group):
    best = None
    for s in group:
        if s.get("modo") in ("walkin", "walkin_con_orden"):
            continue  # walk-in shows its opening, not a schedule
        b = _next_appointment_best(s)
        if b and (best is None or b < best):
            best = b
    return best


def _group_next(group):
    best = _group_best(group)
    if best:
        return _next_from(None, best)
    modos = {s.get("modo") for s in group}
    modo = ("walkin" if "walkin" in modos
            else "walkin_con_orden" if "walkin_con_orden" in modos
            else "solicitud_con_orden")
    src = next((s for s in group if s.get("modo") == modo and s.get("horario")), None)
    return _group_opening(group, _with_schedule(_next_from(modo, None), src))


def _group_opening(group, px):
    """`_with_opening` for a group of services (one study, several centers):
    the earliest date any of them enables turnos — but only when EVERY
    bookable member has a schedule to compute from; one without it can still
    take a request, so the cell keeps its «Sacar turno»."""
    if px.get("kind") != "solicitud":
        return px
    aps = []
    for s in group:
        if not _offers_appointments(s):
            continue
        ap = _habilita(s)
        if not ap:
            return px
        aps.append(ap)
    fechas = [a for a in aps if a.get("estado") == "fecha"]
    if fechas:
        px["habilita"] = min(fechas, key=lambda a: (a["dia"], a.get("hora") or ""))
    elif aps:
        px["habilita"] = aps[0]
    return px


def _web_slots_enabled(ventana):
    """A reserved slot or an overbooking never gets published as a web slot.

    A legacy schedule has no ``cupos``: it keeps a single public slot.
    ``web: 0`` lets a window's online offer be closed without deleting its
    working hours. Larger values don't create duplicates: the public
    booking model still represents a single appointment per time.
    """
    cupos = ventana.get("cupos") if isinstance(ventana, dict) else None
    return not isinstance(cupos, dict) or cupos.get("web") != 0


def _day_exceptions(excepciones, dia):
    """Exceptions that apply to ``dia``; range closures are inclusive.

    THE single place site holidays enter slot generation (site_holidays.py,
    The reference-deployment counterpart 2026-10-15): on a holiday every agenda's day also carries a
    full-day ``cerrado``. The wizard grid, service and doctor pages, «Dar
    este turno», the chat tools, `_next_opening`, booking validation and the
    capacity counts all read a day through here, so none of them can offer
    a slot the site closed."""
    own = [e for e in (excepciones or []) if (
        e.get("fecha") == dia
        or (e.get("desde_fecha") and e.get("hasta_fecha")
            and e["desde_fecha"] <= dia <= e["hasta_fecha"])
    )]
    return own + site_holidays.closures_for(dia)


def _time_blocked(hora, excepciones):
    """Partial ranges use an exclusive end, same as a time window."""
    return any(e.get("tipo") == "bloqueado"
               and (e.get("desde") or "") <= hora < (e.get("hasta") or "")
               for e in (excepciones or []))


def _next_in_window(d, desde, hasta, cada_min, now, bloqueos=None, cupos=True):
    """First strictly-future slot of ONE window {desde,hasta,cada_min} on day
    `d`, or None if none is left (window already fully past)."""
    import datetime
    try:
        desde_h, desde_m = (int(x) for x in (desde or "09:00").split(":"))
        hasta_h, hasta_m = (int(x) for x in (hasta or "12:00").split(":"))
    except ValueError:
        return None
    cada = int(cada_min or 20)
    start_min, end_min = desde_h * 60 + desde_m, hasta_h * 60 + hasta_m
    if not cupos:
        return None
    t = start_min
    while t < end_min:
        slot = datetime.datetime.combine(d, datetime.time(t // 60, t % 60))
        hora = f"{t // 60:02d}:{t % 60:02d}"
        if slot > now and not _time_blocked(hora, bloqueos):
            return t
        t += cada
    return None


def _next_multi(franjas, excepciones=None, now=None):
    """(dia, hora) of the next slot across SEVERAL weekly windows (each one
    can cover different days with different hours — the product owner, 2026-07-11:
    "they could have different hours on different days") plus one-off
    exceptions: `cerrado` (vacation/leave — that day offers nothing even if
    it matches a window) or `extra` (a one-off shift with its own window,
    even if that day isn't in any window). days in JS `Date.getDay()`
    convention (0=Sunday..6=Saturday). Always future, looks up to 7 days
    ahead (covers any weekly pattern)."""
    import datetime
    excepciones = excepciones or []
    now = now or site_base._now_art()
    for offset in range(8):  # today..+7 days: covers any day at least once
        d = now.date() + datetime.timedelta(days=offset)
        diso = d.isoformat()
        excs = _day_exceptions(excepciones, diso)
        if any(e.get("tipo") == "cerrado" for e in excs):
            continue
        # isoweekday(): Monday=1..Sunday=7 -> %7 turns it into getDay():
        # Sunday=0, Monday=1..Saturday=6 (the `dias` convention, see the
        # module docstring).
        dow = d.isoweekday() % 7
        ventanas = [fr for fr in franjas if dow in (fr.get("dias") or [])]
        ventanas += [e for e in excs if e.get("tipo") == "extra"]
        best_t = None
        for v in ventanas:
            t = _next_in_window(
                d, v.get("desde"), v.get("hasta"), v.get("cada_min"), now,
                bloqueos=excs, cupos=_web_slots_enabled(v))
            if t is not None and (best_t is None or t < best_t):
                best_t = t
        if best_t is not None:
            return (diso, f"{best_t // 60:02d}:{best_t % 60:02d}")
    return None


def _demo_next(agenda, now=None):
    """(dia, hora) of the next slot of a doctor's `agenda` (demo OR real,
    loaded by the admin) — accepts the old flat form
    {dias,desde,hasta,cada_min} (a single window, no exceptions) OR the new
    form {franjas:[...], excepciones:[...]} (2026-07-11, multi-window +
    vacations/shifts) — see `_next_multi`. `now` is injectable (tests with a
    fixed "today"); defaults to datetime.now()."""
    if not isinstance(agenda, dict):
        return None
    if "franjas" in agenda:
        return _next_multi(agenda.get("franjas") or [], agenda.get("excepciones"), now=now)
    if not agenda.get("dias"):
        return None
    return _next_multi([agenda], None, now=now)


def _doctor_next(m, servicio=None):
    """`_doctor_next_raw` plus, for a doctor whose bookable service has no
    free slot, when turnos get enabled (`_with_opening`). `servicio` asks
    for the doctor's next time in THAT service (a doctor tagged with
    several); by default the doctor's own service."""
    px = _doctor_next_raw(m, servicio)
    sw = (servicio or site_doctors._doctor_service(m)) if px.get("kind") == "solicitud" else None
    return _with_opening(px, sw, str(m.get("id") or "")) if sw else px


def _doctor_next_raw(m, servicio=None):
    """Doctor's next appointment: their own real schedule (loaded by the
    admin in `agenda`) if they have one, otherwise their own slot (match by
    full name across the ENTIRE MSD dump — the online schedule can sit under
    a different specialty, e.g. pediatrician Zubiri sees patients under
    'Medicina general'), then match by last name across their cluster's
    services, then the service's own, otherwise the honest label. `demo`
    doctors (no real schedule — cluster with no availability) use their made
    up `agenda_demo` instead. Preference: real schedule > agenda_demo > MSD."""
    # If the doctor sees patients in a walk-in service (by order of arrival),
    # NEVER show a scheduled appointment — it contradicts the "You don't need
    # an appointment" sign (the product owner, 2026-07-10). The opening shows instead:
    # open now / when it opens.
    sw = servicio or site_doctors._doctor_service(m)
    if sw and sw.get("modo") in ("walkin", "walkin_con_orden"):
        return _with_schedule(_next_from(sw["modo"], None), sw)
    owns_service = bool(sw and schedule_policy.belongs(m, sw))
    if owns_service and _offers_appointments(sw):
        busy = _busy_default()
        slots = [slot for filtered in doctor_windows.for_service(m, sw)
                 for slot in _agenda_slots(filtered, service=sw,
                     doctor=None if schedule_policy.has_schedule(sw) else m)
                 if (sw.get("id"), slot["dia"], slot["hora"]) not in busy]
        if slots:
            first = min(slots, key=lambda t: (t["dia"], t["hora"]))
            return _next_from(None, (first["dia"], first["hora"]))
        # A schedule hidden by the public horizon must not reappear via a fallback.
        if schedule_policy.has_schedule(sw) or schedule_policy.doctor_has_schedule(m):
            return _next_from(None, None)
    if m.get("agenda"):
        slots = _agenda_slots(m["agenda"], service=sw if owns_service else None, doctor=m)
        if slots:
            first = min(slots, key=lambda t: (t["dia"], t["hora"]))
            return _next_from(None, (first["dia"], first["hora"]))
        return _next_from(None, None)
    if m.get("demo") and m.get("agenda_demo"):
        best = _demo_next(m["agenda_demo"])
        if best:
            return _next_from(None, best)
    own = msd_data.proximo_own_anywhere(
        m.get("nombre"), m.get("centro"), accept=lambda day, hour: _is_future_slot(day, hour)
        and booking_horizon.allows(day, sw, m, site_base._now_art()))
    if own:
        return _next_from(None, own)
    clusters, centro = site_catalog._doctor_clusters(m), m.get("centro")
    cands = sorted((s for s in site_catalog._services() if s.get("cluster") in clusters),
                   key=lambda s: (clusters.index(s.get("cluster")), s.get("centro") != centro))
    for s in cands:
        px = msd_data.proximo_for_medico(
            s, m.get("nombre"), strict=True, accept=lambda day, hour: _is_future_slot(day, hour)
            and booking_horizon.allows(day, s, m, site_base._now_art()))
        if px:
            return _next_from(s.get("modo"), px)
    s = sw
    if s:
        return _next_from(s.get("modo"), _next_appointment_best(s))
    return _next_from("solicitud_con_orden", None)


def _schedule_lines(m, now=None):
    """A professional's schedule lines, each with THEIR office and THEIR
    center (the reference-deployment counterpart, 2026-07-24 meeting: "Lazcano is in contraception
    Mondays 8 to 12, but Tuesdays 8 to 12 he's in the general-medicine
    office. He doesn't do the same thing on Mondays as on Tuesdays" — "what
    kind of office it is matters more than the specialty").

    The office comes from `franjas[].servicio` when it's tagged. When it
    isn't, it falls back to the SAME service this page's button already
    books into (`_doctor_service`): that way the label never invents a new
    fact, it only names what the page already states. The hospital's final
    spreadsheet is going to tag window by window; until then this shows what
    is known.

    The windows are the ones the booking reads (`doctor_windows`, hub brief
    2026-11-08): the agenda of the services that manage the doctor, else the
    doctor's center windows, else the flat agenda — walked over the next
    seven days from `now`, so a closed day this week reads «No atiende» and
    an `extra` window shows on its day. A managing service names its own
    office.

    Returns rows sorted by day and time, one per window (days within the
    same window contract in the text, they aren't exploded into rows)."""
    from app.modules.turnos import doctor_windows
    fuentes = doctor_windows.sources(m)[0]
    hoy = (now or site_base._now_art()).date()
    ventanas = {}
    for offset in range(7):
        d = hoy + _datetime.timedelta(days=offset)
        for v in doctor_windows.windows_of_day(fuentes, d):
            clave = (v["centro"] or "", v["servicio"] or "", v["desde"], v["hasta"])
            ventanas.setdefault(clave, []).append(d.isoweekday() % 7)
    by_slug = site_centers._centers_by_slug()
    fallbacks, filas = {}, []
    for (slug, sid, desde, hasta), dias in ventanas.items():
        # The fallback resolves PER CENTER: _doctor_service prefers the same
        # center's service, so a doctor who sees patients at two venues
        # doesn't end up with the other one's service in both rows.
        if slug not in fallbacks:
            fallbacks[slug] = site_doctors._doctor_service(dict(m, centro=slug) if slug else m)
        s = (site_catalog._service_by_id(sid) if sid else None) or fallbacks[slug]
        dias = sorted(dias, key=lambda d: (d + 6) % 7)
        filas.append({
            "dias": dias,
            "dias_txt": site_schedule._days_text(dias),
            "desde": desde,
            "hasta": hasta,
            "centro": slug,
            "centro_nombre": (by_slug.get(slug) or {}).get("name") or "",
            "servicio_id": (s or {}).get("id") or "",
            "servicio_nombre": (s or {}).get("nombre") or "",
            "etiquetado": bool(sid),
            "_sid": sid,
        })
    # Same day and time: the offices keep the order the schedule lists them in.
    orden = {}
    for src in fuentes:
        for fr in src["franjas"]:
            sid = src["servicio"] or str((fr or {}).get("servicio") or "").strip()
            orden.setdefault((src["centro"] or "", sid), len(orden))
    filas.sort(key=lambda f: (min((d + 6) % 7 for d in f["dias"]), f["desde"],
                              orden.get((f["centro"], f["_sid"]), len(orden))))
    for f in filas:
        del f["_sid"]
    return filas


def _schedule_table_html(m, now=None, href_for=None):
    """The professional page's "Cuándo atiende": one card per center, the
    week as a strip of merged blocks (the product owner approved design A, 2026-10-06,
    canvas "Cuándo atiende redesign"). The office is named only when this
    doctor has more than one office in the schedule; a center with a single
    office names it in the card header, one with several gets one labelled
    strip per office — offices with the very same week share one strip that
    names them all. '' if there is no real schedule to show."""
    filas = _schedule_lines(m, now)
    if not filas:
        return ""
    lang = i18n.active()
    hoy_pos = (now or site_base._now_art()).weekday()
    oficinas = {site_base._norm_name(f["servicio_nombre"]) for f in filas
                if f["servicio_nombre"]}
    mostrar_oficina = len(oficinas) > 1

    def oficina_html(primeras):
        valores = []
        for primera in primeras:
            if primera["servicio_id"]:
                # the doctor page passes `href_for`: booking with this doctor
                href = (href_for(primera["servicio_id"]) if href_for
                        else f'/service/{primera["servicio_id"]}')
                valores.append(f'<a href="{site_base._esc(href)}">'
                               f'{site_base._esc(primera["servicio_nombre"])}</a>')
            else:
                valores.append('<span class="mp-ag-na">—</span>')
        # the dot sticks to the name before it: a wrapped line never opens with «·»
        # A strip is labelled by the SERVICE it books (hr-patient review #23,
        # 15.11): «Consultorio Electrocardiograma» called a service a room.
        return f'<span class="mp-ag-office">{"&nbsp;· ".join(valores)}</span>'

    centros = {}
    for fila in filas:
        centros.setdefault(fila["centro"], []).append(fila)

    grupos = []
    for centro_slug, centro_filas in centros.items():
        centro_nombre = centro_filas[0]["centro_nombre"] or centro_slug or "—"
        centro_html = (f'<a href="/center/{site_base._esc(centro_slug)}">{site_base._esc(centro_nombre)}</a>'
                       if centro_slug and centro_filas[0]["centro_nombre"]
                       else site_base._esc(centro_nombre))
        por_oficina = {}
        for fila in centro_filas:
            clave = site_base._norm_name(fila["servicio_nombre"]) if mostrar_oficina else ""
            por_oficina.setdefault(clave, []).append(fila)
        # offices with the very same week share one strip that names them all
        # (a dentist booked for Odontología and the same-day Urgencias in the
        # same hours, 2026-11-08): two identical strips read as a repeat
        por_semana = {}
        for oficina_filas in por_oficina.values():
            semana = tuple(sorted((tuple(f["dias"]), f["desde"], f["hasta"]) for f in oficina_filas))
            por_semana.setdefault(semana, []).append(oficina_filas)
        cabecera_oficina = ""
        cuerpo = []
        if mostrar_oficina and len(por_semana) == 1:
            cabecera_oficina = oficina_html([o[0] for o in next(iter(por_semana.values()))])
        for iguales in por_semana.values():
            etiqueta = ""
            if mostrar_oficina and len(por_semana) > 1:
                etiqueta = f'<div class="mp-ag-sub">{oficina_html([o[0] for o in iguales])}</div>'
            cuerpo.append(etiqueta + site_schedule._week_strip_html(iguales[0], hoy_pos))
        grupos.append(
            f'<section class="mp-ag-group"><h3 class="mp-ag-center">{centro_html}'
            f'{cabecera_oficina}</h3>' + "".join(cuerpo) + "</section>")

    return (
        '<section class="mp-agenda">'
        f'<h2 class="mp-agenda-h">{i18n.t_or("doctor.agenda_heading", "Cuándo atiende", lang)}</h2>'
        '<div class="mp-ag-groups">' + "".join(grupos) + "</div></section>")


_BANNER_ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l18-5v13L3 14v-3z"></path><path d="M11.6 16.8a3 3 0 0 1-5.8-1"></path></svg>'


def _is_clinical(x):
    """General-medicine service — the one that issues the referral."""
    blob = (str(x.get("id", "")) + " " + str(x.get("nombre", ""))).lower()
    return "medicina general" in blob or "clinic" in blob or "clínic" in blob


_CLOCK_ICON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3.5 2"></path></svg>'


def _busy_default():
    """`slot_holds.taken()` (active holds + future approved requests), or
    set() if the module/database aren't available — must never take down a
    listing page. Lazy import: slot_holds.py imports db/catalog/abuse_guard,
    none of them import patient_web/site_*.py, but we keep the import inside the
    function just in case and because it's only needed here."""
    try:
        from app.modules.turnos import slot_holds
        return slot_holds.taken()
    except Exception:  # noqa: BLE001
        return set()


def _slot_source_info(s):
    """Live third-party availability for service `s` through the active
    slot_source connector (`connectors.adapter_for`; msd is the flagged
    installation default — registry rewire 2026-08-20). `{}` when nothing is
    routed (off / invalid / no default) or the connector raises
    ProviderUnavailable (webturno's adapter does; msd's never does — its
    missing-dump degrade is already `{}`): exactly the degrade the wizard and
    the listing cell always had, so both fall back to the real schedule."""
    adapter = connectors.adapter_for(connectors.SLOT_SOURCE)
    if adapter is None:
        return {}
    try:
        info = adapter.slots_for_service(s)
    except connectors.ProviderUnavailable:
        return {}
    info = _future_connector_slots(info)
    if schedule_policy.has_schedule(s):
        # Catalog service ownership is authoritative over imported doctor windows.
        return {}
    blocks = []
    for block in info.get("medicos") or []:
        doctor = site_doctors._doctor_catalog_by_name(block.get("medico"), s)
        slots = [t for t in block.get("turnos") or []
                 if booking_horizon.allows(t.get("dia"), s, doctor, site_base._now_art())]
        if slots:
            blocks.append(dict(block, turnos=slots, proximo=min(slots, key=lambda t: (t["dia"], t["hora"]))))
    return dict(info, medicos=blocks) if blocks else {}


def _is_future_slot(dia, hora, now=None):
    """Whether a connector's calendar value is still offerable in ART.

    Connector dumps are snapshots.  A successful read is not evidence that a
    timestamp in it is still usable, so the public boundary rejects malformed,
    past-date, and already-passed same-day entries before either the listing or
    the wizard can see them.  This is deliberately a timestamp check, not a
    dump-age policy: a stale dump can still contain a valid future slot.
    A site holiday (site_holidays.py) is never offerable either: the
    connector's calendar does not know the installation closed that day.
    """
    try:
        day = _datetime.date.fromisoformat(str(dia))
        hh, mm = (int(part) for part in str(hora).split(":", 1))
        slot = _datetime.datetime.combine(day, _datetime.time(hh, mm))
    except (TypeError, ValueError):
        return False
    return slot > (now or site_base._now_art()) and not site_holidays.name_for(day.isoformat())


def _future_connector_slots(info, now=None):
    """Copy a slot-source response with only still-bookable slots left.

    ``slot_source`` is a generic connector capability, so this lives at its
    consumer boundary rather than encoding an MSD-specific dump TTL.  An empty
    result takes the existing local-agenda fallback; future slots from an old
    snapshot remain valid and retain their connector-provided metadata.
    """
    if not isinstance(info, dict):
        return {}
    now = now or site_base._now_art()
    medicos = []
    for medico in info.get("medicos") or []:
        if not isinstance(medico, dict):
            continue
        turnos = [dict(turno) for turno in (medico.get("turnos") or [])
                  if isinstance(turno, dict)
                  and _is_future_slot(turno.get("dia"), turno.get("hora"), now)]
        # Older slot-source payloads can carry only ``proximo``.  Keep that
        # compatibility path, subject to the same future-only invariant.
        if not turnos:
            proximo = medico.get("proximo")
            if isinstance(proximo, dict) and _is_future_slot(
                    proximo.get("dia"), proximo.get("hora"), now):
                turnos = [dict(proximo)]
        if turnos:
            clean = dict(medico)
            clean["turnos"] = turnos
            # Legacy consumers of the single-slot field must see the same
            # future-only timeline as the grid, never a rejected source value.
            clean["proximo"] = dict(min(
                turnos, key=lambda turno: (turno.get("dia") or "", turno.get("hora") or "")))
            medicos.append(clean)
    if not medicos:
        return {}
    clean_info = dict(info)
    clean_info["medicos"] = medicos
    return clean_info


def _next_appointment_best(s, ocupados=None):
    """(dia, hora) of the closest online slot that's STILL free, or None.
    Preference: Mi Salud Digital > the service's own real schedule > its
    doctors' real schedules (same cluster and center — the hospital's import
    loads windows per doctor).

    `ocupados` (soft-lock, 2026-07-16 — decision with the maintainer): set of
    (servicio_id, dia, hora) taken by ANOTHER person (an active hold or an
    already-approved request) — dropped here so the listings' band/pills
    NEVER promise a slot the wizard can no longer offer (same invariant as
    `_appointments_for_service`, see its docstring). With no explicit
    `ocupados` (most callers), it resolves on its own via
    `slot_holds.taken()` — so no existing caller needs to change to stay
    protected."""
    if ocupados is None:
        ocupados = _busy_default()
    if not _offers_appointments(s):
        return None
    solo_hoy = _opens_same_day(s)
    sid = s.get("id")
    info = {} if schedule_policy.has_schedule(s) else _slot_source_info(s)
    best = None
    hoy = site_base._today_art().isoformat() if solo_hoy else None
    for m in info.get("medicos", []):
        candidatos = m.get("turnos") or ([m["proximo"]] if (m.get("proximo") or {}).get("dia") else [])
        for t in candidatos:
            dia, hora = t.get("dia"), t.get("hora")
            if not dia or (sid, dia, hora) in ocupados or not booking_horizon.allows(
                    dia, s, site_doctors._doctor_catalog_by_name(m.get("medico"), s), site_base._now_art()):
                continue
            if hoy and dia != hoy:
                continue
            cand = (dia, hora)
            if best is None or cand < best:
                best = cand
    if best:
        return best
    if s.get("agenda"):
        libres = sorted(
            (t["dia"], t["hora"]) for t in _agenda_slots(s["agenda"], solo_hoy=solo_hoy, service=s)
            if (sid, t["dia"], t["hora"]) not in ocupados
        )
        if libres:
            best = libres[0]
    for m in ([] if schedule_policy.has_schedule(s) else schedule_policy.pool(s, site_catalog._doctors(), keep=schedule_policy.offering)):
        for agenda in doctor_windows.for_service(m, s):
            libres = sorted((t["dia"], t["hora"]) for t in _agenda_slots(
                agenda, solo_hoy=solo_hoy, service=s, doctor=m)
                if (sid, t["dia"], t["hora"]) not in ocupados)
            if libres and (best is None or libres[0] < best):
                best = libres[0]
    return best


# The windows of a doctor that book a service: one owner, the slot generator
# and the doctor's page / day queue / printed sheet read the same function.
_service_schedule = doctor_windows.service_schedule

_SLOTS_DAYS = 6      # days with offered appointments
# No cap on hours per day: a day is offered whole, to its last hour (a 12-chip
# cut hid 17:00-19:40 of an afternoon agenda, salud 13.11). The grid wraps.
# Public advance notice resolves through booking_horizon, including opening time.


def _agenda_slots(agenda, now=None, solo_hoy=False, service=None, doctor=None):
    """ALL future appointments of an `agenda` (same shape `_demo_next`
    consumes), not just the first: [{dia, hora}] chronological, capped at
    `_SLOTS_DAYS` days with appointments; every hour of those days.

    Exists because the service page used to offer a concrete appointment
    ("Today, 07:30") that the wizard later didn't know how to offer — it only
    asked for a window. That's the root of what the usability study found
    (people read that appointment as the ONLY one): the others were never
    shown to them. `_next_appointment_best` picks the first of these same
    sources, so whatever the band promises is ALWAYS in this list."""
    import datetime
    if not isinstance(agenda, dict):
        return []
    franjas = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
    if not franjas:
        return []
    excepciones = agenda.get("excepciones") or []
    now = now or site_base._now_art()
    out, dias_con_turnos = [], 0
    # `solo_hoy`: same-day opening — the listing exists but opens TODAY and
    # closes today (see `_opens_same_day`). A single day of horizon.
    horizonte = booking_horizon.available_days(service, doctor, now)
    if solo_hoy:
        horizonte = min(1, horizonte)
    for offset in range(horizonte):  # cap: one month of advance notice
        if dias_con_turnos >= _SLOTS_DAYS:
            break
        d = now.date() + datetime.timedelta(days=offset)
        diso = d.isoformat()
        del_dia = _agenda_day_hours(franjas, excepciones, d, now)
        if del_dia:
            dias_con_turnos += 1
            out += [{"dia": diso, "hora": h} for h in sorted(del_dia)]
    return out


def _agenda_day_hours(franjas, excepciones, d, now):
    """The web-bookable times {"HH:MM"} of ONE calendar day `d` of a
    schedule, ignoring the booking horizon: closures, extra windows, blocked
    times and web quotas at 0 applied exactly as the grid applies them.
    EXCEPTION: inside `booking_horizon.staff()` (the desk's «Crear turno»
    grid) the web:0 windows are returned too; a patient-facing caller must
    never run under `staff()`.
    `_agenda_slots` (what can be booked now) and `_next_opening` (when the
    next hidden slot becomes bookable) both read a day through here, so they
    can never disagree on what a day holds."""
    import datetime
    excs = _day_exceptions(excepciones, d.isoformat())
    if any(e.get("tipo") == "cerrado" for e in excs):
        return set()
    ventanas = [fr for fr in franjas if (d.isoweekday() % 7) in (fr.get("dias") or [])]
    ventanas += [e for e in excs if e.get("tipo") == "extra"]
    del_dia = set()
    # The desk (inside `booking_horizon.staff()`) also sees the hours hidden
    # from the web (`cupos.web: 0`): they are the ones it keeps for phone
    # bookings, as «Pacientes de hoy» already shows them. The patient never does.
    for_desk = booking_horizon.is_staff()
    for v in ventanas:
        if not for_desk and not _web_slots_enabled(v):
            continue
        try:
            dh, dm = (int(x) for x in (v.get("desde") or "09:00").split(":"))
            hh, hm = (int(x) for x in (v.get("hasta") or "12:00").split(":"))
        except ValueError:
            continue
        cada = int(v.get("cada_min") or 20)
        if cada <= 0:
            continue
        t, fin = dh * 60 + dm, hh * 60 + hm
        while t < fin:
            hora = f"{t // 60:02d}:{t % 60:02d}"
            if (datetime.datetime.combine(d, datetime.time(t // 60, t % 60)) > now
                    and not _time_blocked(hora, excs)):
                del_dia.add(hora)
            t += cada
    return del_dia


# How far ahead `_next_opening` looks for a schedule's next hidden slot. A
# horizon is capped at booking_horizon.MAX_DAYS, so a year past it is enough to
# tell "opens on <date>" from "nothing is loaded".
_OPENING_SCAN_DAYS = 400


def _next_opening(s, medico_ref=None, now=None):
    """When the wizard's empty grid gets new slots, from real data only.

    La contraparte técnica (hospital, 2026-10-17): «Ahora no hay turnos» must say what day
    turnos appear again. A slot becomes bookable `days_for(service, doctor)`
    days before its own date (booking_horizon, resolved doctor → service →
    site, plus the opening time that holds back the last day). So the next
    opening is the earliest web slot BEYOND today's horizon, minus that
    horizon. Returns one of:

    - ``{"estado": "fecha", "dia": ISO, "hora": "HH:MM" | ""}`` — the moment
      that slot enters the window (``hora`` empty when the window opens at
      midnight);
    - ``{"estado": "sin_fechas"}`` — the service HAS schedules, but none of
      them holds a single future web slot (closures, quotas at 0): said
      honestly, no date invented;
    - ``None`` — nothing to compute from (no local schedule: the dates come
      from an external source or the desk), or walk-in: the line is omitted.
    """
    import datetime
    if not _offers_appointments(s) or _opens_same_day(s):
        return None
    now = now or site_base._now_art()
    if schedule_policy.has_schedule(s):
        agendas = [(s["agenda"], None)]
    else:
        doctor = _doctor_for_ref(s, medico_ref) if medico_ref else None
        pool = [doctor] if doctor else schedule_policy.pool(s, site_catalog._doctors(), keep=schedule_policy.offering)
        agendas = [(a, m) for m in pool for a in doctor_windows.for_service(m, s)]
    best = None
    for agenda, owner in agendas:
        if not isinstance(agenda, dict):
            continue
        franjas = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
        if not franjas:
            continue
        horizonte = booking_horizon.available_days(s, owner, now)
        dias = booking_horizon.days_for(s, owner)
        apertura = booking_horizon.opens_for(s, owner)
        for offset in range(horizonte, horizonte + _OPENING_SCAN_DAYS):
            d = now.date() + datetime.timedelta(days=offset)
            if best is not None and d - datetime.timedelta(days=dias - 1) >= best[0]:
                break
            if _agenda_day_hours(franjas, agenda.get("excepciones") or [], d, now):
                abre = max(d - datetime.timedelta(days=dias - 1), now.date())
                if best is None or abre < best[0]:
                    best = (abre, apertura)
                break
    if best is None:
        return {"estado": "sin_fechas"} if agendas else None
    dia, apertura = best
    return {"estado": "fecha", "dia": dia.isoformat(),
            "hora": "" if apertura == "00:00" else apertura}


def _no_slots_info(s, medico_ref=None, now=None):
    """What the wizard offers when a service has no free slot (the reference-deployment counterpart,
    2026-10-17): when turnos appear again (`_next_opening`), omitted rather
    than invented. The center's phone was the third option until the reference-deployment counterpart asked
    to drop it (2026-10-18): a patient who can't wait writes «Contanos tu
    caso» and the hospital calls back."""
    if not isinstance(s, dict) or not _offers_appointments(s):
        return None
    out = {}
    apertura = _next_opening(s, medico_ref, now)
    if apertura:
        out["apertura"] = apertura
    return out


def _place_of(s):
    """Where that service is seen, to put in each of the wizard's schedule
    blocks (the product owner, 2026-07-27: "you should show the medical centers here
    too"). Choosing a time without knowing which center to go to is choosing
    half the picture — and in Pinamar the centers are ten minutes apart, so
    the fact changes the decision.

    When the service has a `sede` (a room or a different building inside the
    same center, e.g. "Desarrollo Infantil" at CAPS Ostende) BOTH get said:
    the venue alone doesn't tell anyone which center to go to, and the
    center alone leaves them standing at the wrong door. In the listings one
    is enough because the Center column is separate; here there's a single line."""
    if not isinstance(s, dict):
        return {}
    centro = site_centers._centers_by_slug().get(s.get("centro")) or {}
    nombre = centro.get("name") or ""
    sede = (s.get("sede") or "").strip()
    return {
        "centro": s.get("centro") or "",
        "centro_nombre": nombre,
        "centro_href": f'/center/{centro["slug"]}' if centro.get("slug") else "",
        "sede": sede,
        "lugar": (f"{sede} · {nombre}" if sede and nombre else (sede or nombre)),
    }


def _doctor_meta(m):
    """What the person seeing patients is, for the wizard's schedule block
    (the product owner, 2026-07-28: "add not only the hospital but the specialization
    too"). The name alone isn't enough to choose: the specialty is what says
    whether that person is the right one. Comes with its link to
    `/d/<cluster>` (same specialty across every center), just like in the
    listings — replaces the 2026-07-27 decision to leave the place as loose
    text.

    With no catalog entry (a name that only comes from Mi Salud Digital) it
    returns empty: no specialty is made up."""
    if not isinstance(m, dict):
        return {"especialidad": "", "esp_href": ""}
    return {
        "especialidad": " · ".join(site_doctors._doctor_specialty_labels(m)),
        "esp_href": f'/d/{m["cluster"]}' if m.get("cluster") else "",
    }


def own_hold_tokens(hold_token, request):
    """The tokens whose holds count as the caller's own: the browser's token
    plus its account's — the same patient on the phone and the desktop is one
    owner (2026-11-15). Lazy import, like `slot_holds.taken()` above."""
    from app.modules.turnos import slot_holds
    return slot_holds.own_tokens(hold_token, slot_holds._session_user(request))


def _appointments_for_service(s, exclude_token=None):
    """What the wizard offers to choose from: Mi Salud Digital if it has
    appointments, otherwise the real schedule's (the service's own + its
    same cluster-and-center doctors', merged and deduplicated) — the SAME
    preference and the SAME sources as `_next_appointment_best`.

    Schedule appointments go WITHOUT a doctor's name: in clusters like
    `diagnostico` different studies coexist, so naming the cluster's
    professionals would list the ones from another study (the same reason
    the info page doesn't show "Who sees you" there). The hospital assigns
    the professional on approval. Nothing gets booked here: the choice
    travels as `preferencia` and the hospital confirms it.

    Soft-lock (2026-07-16): a slot taken by ANOTHER person (an active hold
    or an already-approved request) gets filtered out of the grid —
    `exclude_token` (the current request's own hold, if any) still lets its
    OWN chosen appointment show, so reopening the grid doesn't hide it outright."""
    if not _offers_appointments(s):
        # Walk-in with no same-day opening: there's nothing to choose. The
        # grid and the band share this same gatekeeper, so they can never diverge.
        return {}
    solo_hoy = _opens_same_day(s)
    sid = s.get("id")
    try:
        from app.modules.turnos import slot_holds
        ocupados = slot_holds.taken(exclude_token)
    except Exception:  # noqa: BLE001
        ocupados = set()

    hoy = site_base._today_art().isoformat() if solo_hoy else None
    msd = {} if schedule_policy.has_schedule(s) else _slot_source_info(s)
    if msd.get("medicos"):
        libres = []
        for m in msd["medicos"]:
            turnos = [t for t in (m.get("turnos") or [])
                      if (sid, t.get("dia"), t.get("hora")) not in ocupados
                      and (not hoy or t.get("dia") == hoy)
                      and booking_horizon.allows(t.get("dia"), s,
                          site_doctors._doctor_catalog_by_name(m.get("medico"), s), site_base._now_art())]
            if turnos:
                source_name = m.get("medico", "")
                catalogo = site_doctors._doctor_catalog_by_name(source_name, s)
                libres.append(dict({
                    "medico": (catalogo.get("nombre") if catalogo else source_name) or "",
                    "medico_id": catalogo.get("id") if catalogo else "",
                    "medico_href": (f'/doctor/{catalogo["id"]}'
                                    if catalogo and catalogo.get("id") else ""),
                    "turnos": turnos,
                }, **_place_of(s), **_doctor_meta(catalogo)))
        if libres:
            out = dict(msd)
            out["medicos"] = libres
            return out
        # This service's MSD appointments are all taken: falls back to the
        # real schedule same as if MSD had nothing (same preference as
        # _next_appointment_best).

    agendas = [(s["agenda"], None)] if schedule_policy.has_schedule(s) else []
    if not agendas:
        for m in schedule_policy.pool(s, site_catalog._doctors(), keep=schedule_policy.offering):
            agendas += [(a, m) for a in doctor_windows.for_service(m, s)]
    vistos, turnos = set(), []
    for a, owner in agendas:
        for t in _agenda_slots(a, solo_hoy=solo_hoy, service=s, doctor=owner):
            if (sid, t["dia"], t["hora"]) in ocupados:
                continue
            k = (t["dia"], t["hora"])
            if k not in vistos:
                vistos.add(k)
                turnos.append(t)
    if not turnos:
        return {}
    turnos.sort(key=lambda t: (t["dia"], t["hora"]))
    # The day cap applies AFTER merging the sources; a day is never cut by hour.
    dias = sorted({t["dia"] for t in turnos})[:_SLOTS_DAYS]
    salida = [t for t in turnos if t["dia"] in dias]
    return {"medicos": [dict({"medico": "", "medico_id": "", "medico_href": "",
                              "turnos": salida}, **_place_of(s))], "fuente": "agenda"}


def _doctor_for_ref(s, medico_ref):
    """The catalog professional a wizard link or an approved request points
    at: by id, then by normalized name, then by the name the service's own
    sources carry. None when nobody matches."""
    doctor = site_doctors._doctor_by_id(str(medico_ref))
    if not doctor:
        wanted = site_base._norm_name(medico_ref)
        doctor = next((m for m in site_catalog._doctors()
                       if site_base._norm_name(m.get("nombre")) == wanted), None)
    if not doctor:
        doctor = site_doctors._doctor_catalog_by_name(medico_ref, s)
    return doctor or None


def _doctor_service_agendas(s, doctor):
    """The doctor's own windows that belong to THIS service, closures kept."""
    return doctor_windows.for_service(doctor, s)


def _appointments_for_doctor(s, medico_ref, exclude_token=None):
    """Public slots for the doctor already assigned to an approved request.

    Reprogramming must preserve that clinical assignment.  Offering the
    service's generic agenda let patients choose minutes outside the doctor's
    own recurring hours, closures, and partial blocks.
    """
    if not medico_ref or not _offers_appointments(s):
        return {}
    doctor = _doctor_for_ref(s, medico_ref)
    if not doctor:
        return {}
    centro = s.get("centro")
    agendas = _doctor_service_agendas(s, doctor)
    if not agendas:
        return {}
    try:
        from app.modules.turnos import slot_holds
        ocupados = slot_holds.taken(exclude_token)
    except Exception:  # noqa: BLE001
        ocupados = set()
    vistos, turnos = set(), []
    for agenda in agendas:
        for turno in _agenda_slots(agenda, solo_hoy=_opens_same_day(s), service=s,
                                    doctor=None if schedule_policy.has_schedule(s) else doctor):
            if (s.get("id"), turno["dia"], turno["hora"]) in ocupados:
                continue
            key = (turno["dia"], turno["hora"])
            if key not in vistos:
                vistos.add(key)
                turnos.append(turno)
    turnos.sort(key=lambda t: (t["dia"], t["hora"]))
    dias = sorted({t["dia"] for t in turnos})[:_SLOTS_DAYS]
    salida = [t for t in turnos if t["dia"] in dias]
    return ({"medicos": [dict({
                 "medico": doctor.get("nombre") or str(medico_ref),
                 "medico_id": doctor.get("id") or "",
                 "medico_href": f'/doctor/{doctor["id"]}' if doctor.get("id") else "",
                 "turnos": salida,
             }, **_place_of(s), **_doctor_meta(doctor))],
             "fuente": "agenda-medico"} if salida else {})


def _appointments_for_service_preferred(s, medico_ref, exclude_token=None):
    """All service slots, with the doctor chosen upstream first.

    Doctor pages can expose a real local agenda even when Mi Salud Digital has
    slots for other professionals. Keep the selected doctor's schedule in the
    response, then leave the other professionals underneath. A doctor whose
    own windows here offer nothing right now gets NO fallback to the others
    (the wizard then asks for a franja, as the listing promised).
    """
    generic = _appointments_for_service(s, exclude_token=exclude_token)
    if not medico_ref:
        return generic
    preferred = _appointments_for_doctor(s, medico_ref, exclude_token=exclude_token)
    preferred_blocks = preferred.get("medicos") or []
    if not preferred_blocks:
        # The person HAS windows at this service but none is offered now (a
        # closure, the booking horizon, web quotas at 0, every minute taken).
        # The listing already says "la fecha la confirma el centro" for them,
        # so the pool of the service's OTHER professionals must not stand in
        # under no name as if it were theirs (hospital administrator's video,
        # 2026-10-14: closure set, Monday–Thursday mornings still offered).
        # Same rule as `_doctor_next`: a hidden schedule never reappears via
        # a fallback. With no windows of their own here, the pool is the only
        # thing this service can offer.
        doctor = _doctor_for_ref(s, medico_ref)
        if doctor and _doctor_service_agendas(s, doctor):
            return {}
        return generic

    first = dict(preferred_blocks[0])
    preferred_id = first.get("medico_id") or ""
    rest = []
    for block in generic.get("medicos") or []:
        if preferred_id and block.get("medico_id") == preferred_id:
            seen = {(t.get("dia"), t.get("hora")) for t in first.get("turnos") or []}
            first["turnos"] = (first.get("turnos") or []) + [
                t for t in block.get("turnos") or []
                if (t.get("dia"), t.get("hora")) not in seen
            ]
            first["turnos"].sort(key=lambda t: (t.get("dia") or "", t.get("hora") or ""))
        else:
            rest.append(block)

    # The UNnamed block is the service's schedule, and that schedule is
    # built from these same doctors' windows: repeating below, under the
    # "Horarios" label, the minutes that already show above with a full
    # name made the same appointment look shown twice — and worse, made
    # them look like two different appointments (the product owner, screenshot
    # 2026-07-27). The already-named ones are stripped out, and if nothing
    # is left the block doesn't go.
    con_nombre = {(t.get("dia"), t.get("hora"))
                  for b in [first] + rest if b.get("medico")
                  for t in b.get("turnos") or []}
    depurado = []
    for block in rest:
        if block.get("medico"):
            depurado.append(block)
            continue
        propios = [t for t in block.get("turnos") or []
                   if (t.get("dia"), t.get("hora")) not in con_nombre]
        if propios:
            depurado.append(dict(block, turnos=propios))

    out = dict(generic)
    out["medicos"] = [first] + depurado
    out["preferido"] = preferred_id
    return out


def _week_hours(s):
    """Weekly hours a SERVICE is seen: its own schedule if it has one;
    otherwise the sum of the windows of the doctors who see patients in the
    same cluster+center (webdata/medicos.json, `centros[].franjas`) —
    popularity signal for the search ranking (the product owner, 2026-07-12). 0.0 if
    there's no signal at all."""
    h = site_schedule._schedule_hours(s.get("agenda") or {})
    if h:
        return h
    total = 0.0
    for m in site_catalog._doctors():
        if s.get("cluster") not in site_catalog._doctor_clusters(m):
            continue
        for c in (m.get("centros") or []):
            if c.get("centro") == s.get("centro"):
                total += sum(site_schedule._window_hours(fr) for fr in (c.get("franjas") or []))
    return total
