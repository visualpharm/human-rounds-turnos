"""patient_web/site_page_doctors.py — The doctor pages of the public site.

The cluster page, the doctors list and the page of one doctor.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import urllib.parse

from app.patient_web import doctor_photos
from app.core import i18n
from app.patient_web import seo
from app.core import site_identity
from app.core import static_fingerprint
from app.core import theme

from app.patient_web import site_availability
from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_centers
from app.patient_web import site_doctors
from app.patient_web import site_listing
from app.patient_web import site_page_centers
from app.patient_web import site_preparation
from app.patient_web import site_shell
from app.patient_web import site_studies


def _center_rows_from_doctors(svs, doc_rows, doc_servicios):
    """The «where to be seen» rows of a specialty page, read from the same
    doctors the table below lists (demo review 2026-11-15, P2/P3: a CAPS row
    offered a cardiology turno where no cardiologist below works, and the
    hospital row said «Sin orden — 26 de noviembre» over cardiologists seen
    on the 19th, two of them «Con orden»).

    A bookable service row keeps only when a listed doctor books into it; its
    first turno, booking link and «orden» are those doctors' (the soonest
    one's turno; «orden» only when they all agree, else the cell stays
    empty). A walk-in row stays as it is — nobody books it. With no doctor
    listed at all the rows are the services' own, as before."""
    rows = []
    for s in svs:
        row = site_listing._service_center_row(s)
        if not doc_rows or s.get("modo") in site_catalog._NO_APPOINTMENT_MODES:
            rows.append(row)
            continue
        suyos = [r for r, sv in zip(doc_rows, doc_servicios) if (sv or {}).get("id") == s.get("id")]
        if not suyos:
            continue
        primero = site_listing._soonest_first(suyos)[0]
        row["proximo"] = primero["proximo"]
        for k in ("slot_href", "info_href", "turno_href"):
            row.pop(k, None)
            if primero.get(k):
                row[k] = primero[k]
        row.setdefault("info_href", f'/service/{s["id"]}')
        ordenes = {r.get("orden") for r in suyos}
        row["orden"] = ordenes.pop() if len(ordenes) == 1 else None
        rows.append(row)
    return rows


def _page_cluster(cid):
    """Specialty/cluster page: the patient CHOOSES THE PROVIDER here — the
    same specialty across different centers (with its schedule on the left)
    or a doctor of that specialty. The referral, if needed, gets uploaded on
    the next step (the service's page)."""
    cl = site_catalog._cluster_by_id(cid)
    if not cl:
        return None
    if cid == "grupos":
        return site_page_centers._page_workshops(cl)
    if cl.get("kind") == site_catalog._WALKIN_KIND:
        return site_page_centers._page_walkin(cl)
    svs = sorted((s for s in site_catalog._services() if s.get("cluster") == cid),
                 key=lambda s: (s.get("modo") != "walkin", s["nombre"].lower()))
    # Doctors of another specialty whose schedule names a service of this
    # one (a cardiologist who also does Cirugía) are listed here too, with
    # that service's next time and booking link.
    meds, extra = site_doctors._cluster_doctors(cid)
    if not svs and not meds:
        return None
    # each doctor's row once: the doctors' table below and the per-center
    # summary above read the same rows
    doc_rows = [site_listing._doctor_listing_row(m, extra.get(id(m))) for m in meds]
    doc_servicios = [extra.get(id(m)) or site_doctors._doctor_service(m) for m in meds]

    # In "Estudios" (diagnostics/imaging) the catalog is studies, not
    # providers: they're grouped by name (one study = one row) and choosing
    # it shows where and when. In specialty clusters the row is still by
    # center/provider.
    es_estudios = cid == "diagnostico"

    # This cluster's own search examples (locality · the name of a
    # doctor on the page · a distinctive service). There's ONE search box
    # and it filters BOTH tables (searchGroup): so the doctor example
    # falls on the doctors table and the service example on the one above —
    # see listing.js.
    _centro_nombres = [(site_centers._centers_by_slug().get(s.get("centro")) or {}).get("name") or ""
                       for s in svs]
    _ejemplos = site_listing._example_dedup(site_listing._example_locality(_centro_nombres), site_listing._example_doctor(meds),
                          site_listing._example_service(svs, cl["nombre"]))

    opciones = ""
    if svs and es_estudios:
        opciones = site_studies._studies_listing(cid, {"searchGroup": "cluster", "searchExamples": _ejemplos})
    elif svs:
        # "Where to be seen": same specialty across different centers — the
        # center IS a column (you choose where to go; wait + travel
        # together). Sorted by appointment. If the cluster's services have
        # different names (e.g. three variants in Ostende), the service's
        # name is the link — otherwise two rows of the same center look
        # indistinguishable and the click is a lottery.
        rows = site_availability._with_remedies(_center_rows_from_doctors(svs, doc_rows, doc_servicios), by="centro")
        # The center links to its own page (never straight to booking); the
        # action is the Next-availability pill-button + the whole row.
        if len({r["nombre"] for r in rows}) > 1:
            cols = site_listing._cols("servicio", "centro", "proximo", "orden")
        else:
            cols = site_listing._cols("centro", "proximo", "orden")
        opciones = "" if not rows else site_listing._listing_block(
            "cluster-centros", cols, rows,
            {"rowHref": "href", "search": True, "searchGroup": "cluster",
             "searchPlaceholder": i18n.t_or("page.cluster.search_placeholder", "Médico, servicio o centro", i18n.active()),
             "searchLabel": i18n.t_or("page.cluster.search_label", "Buscar servicio, centro, médico o localidad", i18n.active()),
             "searchExamples": _ejemplos})

    meds_html = ""
    if meds:
        rows = site_availability._with_remedies(site_listing._soonest_first(doc_rows))
        # Although the cluster shares a general area, each professional can
        # have a different specialty. Match /doctors; at narrow widths,
        # listing.js moves this same cell below the name.
        # "orden" here too (the product owner, 2026-07-12: missing on EVERY doctors
        # table, no exception) — a separate call site, not _DOCTORS_COLS.
        # Centro and Orden stay on a phone too, as one compact line under the
        # name (review 2026-11-15, P6/D17); listing.js collapses a center or
        # specialty shared by every row.
        cols = site_listing._cols("medico", "especialidad", "centro", "orden", "proximo")
        # Listener of the same group: the search box above also filters it;
        # if the query matches no doctor, this whole section hides.
        meds_html = site_listing._listing_block("cluster-medicos", cols, rows,
                                   {"title": i18n.t_or("page.doctors.title", "Médicos y profesionales", i18n.active()),
                                    "rowHref": "href",
                                    # soonest first, then name — no popularity
                                    # rank (demo review 2026-11-15, P20)
                                    "searchGroup": "cluster" if svs else None})

    # R4: general entry point to "Cómo prepararte" from the studies cluster —
    # here the patient is still choosing the study, so no ?servicio.
    prep_line = site_preparation._prep_teaser_html() if es_estudios else ""

    body = f"""
  <h1 class="h1">{site_base._esc(cl["nombre"])}</h1>
  {prep_line}
{opciones}
{meds_html}
{site_listing._LISTING_SCRIPT}
"""
    # the nav flags its item: /d/diagnostico is "Estudios" (Talleres is no
    # longer a menu item — it lives as a card in the home page's "Más buscadas")
    section = "estudios" if es_estudios else ""
    return site_shell._shell(f'{cl["nombre"]} | {theme.brand_name()}', section, body,
                  f'{cl["nombre"]} en el {site_identity.get("network")}: dónde atenderte, con quién y cómo sacar turno.',
                  canonical=f"/d/{cid}")


def _page_doctors():
    # ONE single table of every professional (table-design skill: >15 rows →
    # sort + search + profiled filter). Médico (with avatar) | Especialidad |
    # Centro | Turno | Próxima disponibilidad; the row goes to the doctor's page.
    meds = sorted(site_catalog._professionals(), key=lambda m: m["nombre"])
    # Visible order: soonest availability first, then name (demo review
    # 2026-11-15, P20: the hours-per-week popularity rank read as no order at
    # all). The client keeps it: with no `rank` it sorts by the Próximo
    # column, stable over this name order.
    rows = site_availability._with_remedies(
        site_listing._soonest_first([site_listing._doctor_listing_row(m) for m in meds]))
    # Centro column (the product owner, 2026-07-23): on /doctors the center is NOT
    # redundant (unlike a center's own page) — it says where each one sees
    # patients and enables searching by locality. On a phone it stays, with
    # Orden, as one compact line under the name (review 2026-11-15, P6/D17).
    cols = site_listing._cols("medico", "especialidad", "centro", "orden", "proximo")
    # 13 specialties → a <select> (more than 6 groups, skill).
    # Same filters as Estudios (the product owner, 2026-07-18: "unify filters between
    # Médicos y Estudios"): Especialidad is Médicos' own facet (this
    # table's "área"), + audience/sex derived from the specialty
    # (_doctor_audience_sex) so we don't show gynecology to a man nor hide
    # pediatrics from someone searching for a child.
    # Examples: locality (the Centro column makes it valid) · a doctor's
    # name · a frequent specialty (the third, "service"-type example).
    ejemplos = site_listing._example_dedup(
        site_listing._example_locality([r.get("centro") or "" for r in rows]),
        site_listing._example_doctor(meds),
        site_listing._example_specialty(meds))
    # "rowHref" es el NOMBRE de la clave de cada fila que lleva el link, no
    # texto de interfaz: quedó envuelto en i18n.t_or por error (la clave
    # "page.center.search_doctor" en inglés vale "Search doctor or
    # specialty") y en el sitio en inglés el click en la fila se rompía
    # entero. Un literal fijo, nunca traducido.
    opts = {"searchPlaceholder": i18n.t_or("doctor.search_placeholder", "Buscar médico o especialidad", i18n.active()),
            "rowHref": "href",
            # No silent preload of «Adultos» / «Para mujeres» from the account
            # (demo review 15.11): it hid every pediatrician, so «Pediatra» in
            # the specialty list and the «Sánchez» example both came up empty.
            # The visitor sets the audience filters here; every row shows.
            "profile": False,
            "urlState": True, "searchExamples": ejemplos,
            "filters": [
                {"t": "facet", "key": "especialidad",
                 "label": i18n.t_or("listing.col.especialidad", "Especialidad", i18n.active()),
                 "widget": "select"},
                {"t": "publico"},
                {"t": "sexo"},
            ]}
    listado = site_listing._listing_block("medicos-tabla", cols, rows, opts)
    body = f"""
  <h1 class="h1">{site_base._esc(i18n.t_or("page.doctors.title", "Médicos y profesionales", i18n.active()))}</h1>
{listado}
{site_listing._LISTING_SCRIPT}
"""
    return site_shell._shell(f'{i18n.t_or("page.doctors.tab", "Médicos del {network}", i18n.active(), network=site_identity.get("network"))} | {theme.brand_name()}', "medicos", body,
                  i18n.t_or("doctor.doctors_meta_desc",
                            "Médicos y profesionales del sistema público de salud de {city}, por especialidad, centro y próxima disponibilidad.",
                            i18n.active(), city=site_identity.get("city")),
                  canonical="/doctors")

# Updates the walk-in band with its live status (hours.mjs:frase): "Abierto
# ahora · hasta las 12:00" (green), "Abre el jueves, 08:00" or — monthly
# recurrences — "Lunes 3 de agosto"; the sign's full schedule drops to a
# sub-line. If the schedule can't be parsed, the band keeps the sign's text
# as-is (never guesses).
# NOTE: the JS block below is embedded product output served byte-for-byte
# to the browser — its internal Spanish comments are page content, not
# module documentation, and are intentionally left as-is.
_BAND_SCHEDULE_JS = """
document.addEventListener("DOMContentLoaded",function(){
  var band=document.querySelector(".mp-band[data-horario]");
  var hoursUrl=window.SANA_STATIC&&window.SANA_STATIC["platform/hours.mjs"];
  if(!band||!hoursUrl)return;
  import(hoursUrl).then(function(H){
  var raw=band.getAttribute("data-horario");
  var e=H.estado(raw);
  var txt=H.frase(e);
  if(!txt)return;
  if(e.abierto)band.classList.add("mp-band--open");
  var v=band.querySelector(".mp-band-v");
  v.textContent=txt;
  // el horario crudo baja a una sub-línea SOLO si la grilla semanal no puede
  // dibujarlo (p.ej. recurrencia mensual: parse() null pero frase() sí).
  if(!H.parse(raw)){
    var sub=document.createElement("div");
    sub.className="mp-band-sub";
    sub.textContent=raw;
    v.parentNode.appendChild(sub);
  }
  });
});
"""

def _doctor_avatar_html(m, nombre):
    """The doctor's portrait on the page, and the portrait's inline style ('' without a photo)."""
    # Portrait: the admin's upload, otherwise the sprite (positioned in %,
    # works at any size), otherwise initials in a circle. With a photo it's
    # clickable → enlarges the photo in HIGH resolution (photo-zoom.mjs) —
    # the admin's upload IS ALREADY that high resolution (foto_full = the
    # same URL); the sprite instead is a 128px tile, looks pixelated
    # enlarged, so its high resolution lives separately in
    # webdata/medico-photos/. A doctor has a single photo: the viewer opens
    # with NO navigation arrows (no data-photozoom-group).
    mid_ = m.get("id") or ""
    ph = site_doctors._photo_style(mid_)
    if ph:
        # The hero paints its own 224 px thumbnail (avatar_thumb.py), not a
        # crop of the whole-roster sprite: that sprite was 525 KB on every
        # doctor page and put mobile LCP at 4.1-4.3 s (2026-09-17). Without a
        # thumbnail the old background style stays as the fallback.
        from app.patient_web import avatar_thumb
        thumb = avatar_thumb.url(mid_)
        img_attr = (f'><img class="mp-av-img" src="{site_base._esc(thumb)}" alt="" width="112" height="112" decoding="async"'
                    if thumb else f' style="{ph}"')
        foto_full = doctor_photos.photo_url(mid_) or f'/webdata/medico-photos/{urllib.parse.quote(mid_)}.webp'
        avatar = (f'<a href="{site_base._esc(foto_full)}" target="_blank" rel="noopener" class="mp-av mp-av--ph" aria-label="Ampliar foto de {site_base._esc(nombre)}" '
                  f'data-photozoom data-photozoom-src="{site_base._esc(foto_full)}" data-photozoom-alt="{site_base._esc(nombre)}" '
                  f'{img_attr}></a>')
    else:
        ini, color = site_doctors._avatar(nombre)
        avatar = (f'<div class="mp-av mp-av--{color}" role="img" aria-label="{site_base._esc(nombre)}">'
                  f'{site_base._esc(ini)}</div>')
    return avatar, ph


def _doctor_walkin_band(px, agenda_html):
    """The band of a doctor who sees patients walk-in, and the script that keeps its opening live."""
    # Walk-in: there's no appointment to book — no button (the product owner,
    # 2026-07-10: "there should not be a button to get a turno if you
    # don't need it"). The band states the opening: hours.mjs turns it
    # into "Abierto ahora · hasta las 12:00" or "Abre el jueves, 08:00"
    # live; with no JS the sign's schedule stays.
    horario = px.get("horario") or ""
    band = (
        f'<div class="mp-band mp-band--off mp-band--horario" data-horario="{site_base._esc(horario)}">'
        f'<span class="mp-band-ico" aria-hidden="true">{site_shell._CLOCK_ICON_LG}</span>'
        f'<div class="mp-band-txt"><div class="mp-band-k">{site_base._esc(px.get("nota") or i18n.t_or("doctor.no_appointment_fallback", "Sin turno", i18n.active()))}</div>'
        f'<div class="mp-band-v">{site_base._esc(horario or px.get("v") or "")}</div></div>'
        f'</div>'
        # weekly grid (platform/week.mjs, "store hours" style): the whole week in
        # blocks with matching days merged — replaces the gray sub-line
        # of a raw schedule (the product owner, 2026-07-10: "avoid such low contrast text").
        + ('' if agenda_html else
           site_listing._week_markup(horario, i18n.t_or("doctor.agenda_heading", "Cuándo atiende", i18n.active())))
    )
    horario_script = site_listing._WEEK_SCRIPT + f'<script>{_BAND_SCHEDULE_JS}</script>'
    return band, horario_script


def _doctor_next_heading(m, s):
    """The band's label. A doctor who books into several services names the
    one the button books (hr-patient review #4b, 15.11: «Sacar mañana…» did
    not say for which of four services)."""
    propios = [t for t in site_doctors._doctor_tagged_services(m) if t.get("id")]
    if len(propios) > 1 and s and s.get("nombre"):
        return site_base._esc(i18n.t_or("doctor.next_slot_heading_service", "Primer turno libre en {service}",
                                         i18n.active(), service=s["nombre"]))
    return i18n.t_or("doctor.next_slot_heading", "Primer turno libre", i18n.active())


def _doctor_booking_band(m, s, px, c, centro_slug):
    """The band of a doctor who sees patients by turno: ONE primary action, to book where they see patients."""
    band_off = "" if px.get("kind") == "turno" else " mp-band--off"
    band_k = (i18n.t_or("page.band.next_slot", "Próximo turno disponible", i18n.active())
              if px.get("kind") == "turno"
              else i18n.t_or("page.band.appointments", "Turnos", i18n.active()))
    # ONE primary action: book where they see patients. The band speaks
    # the SAME language as the service's and the listing cell's (the product owner,
    # 2026-07-14, "make the interface consistent in general"): with a
    # concrete appointment → green verb button ("Sacar el viernes 17 de
    # julio, 13:00", ?slot= skips the grid; an invalid slot degrades to
    # the grid) + "Más horarios"; bookable with no date → ink-colored
    # "Sacar turno" button + note "La fecha la confirma el centro". With
    # no bookable service, the destination is the center's page (worded accordingly).
    con_orden_m = bool(s and s.get("modo") == "solicitud_con_orden")
    # misma clave que la columna "Orden" de las tablas (listing.js) —
    # una sola traducción para la misma palabra en todo el sitio.
    orden_badge = (f'<span class="mp-band-badge">{i18n.t_or("listing.order.with", "Con orden", i18n.active())}</span>'
                   if con_orden_m else "")
    doctor_q = urllib.parse.quote(str(m.get("id") or ""))
    if (s and px.get("kind") == "turno" and px.get("dia") and px.get("hora")
            and s.get("modo") in ("solicitud_con_orden", "solicitud")):
        cta_href = site_listing._appointment_href(s)
        return (
            f'<div class="mp-band mp-band--turno">'
            f'<span class="mp-band-ico" aria-hidden="true">{site_shell._CAL_ICON}</span>'
            f'<div class="mp-band-txt">'
            f'<div class="mp-band-k">{_doctor_next_heading(m, s)}'
            f'{(": " + site_base._esc(px.get("v"))) if con_orden_m and px.get("v") else ""}{orden_badge}</div>'
            f'</div>'
            f'<div class="mp-band-actions">'
            f'<a class="mp-cta mp-cta--{site_studies._cta_bucket(px.get("dia"))}" '
            f'href="{site_base._esc(cta_href)}?slot={site_base._esc(px["dia"])}T{site_base._esc(px["hora"])}'
            f'&amp;medico={doctor_q}">'
            f'{site_studies._order_cta_label() if con_orden_m else site_base._esc(site_studies._get_label(px.get("v")))}</a>'
            f'<a class="mp-more" href="{site_base._esc(cta_href)}?slots=1'
            f'&amp;medico={doctor_q}">{i18n.t_or("listing.more_times", "Más horarios", i18n.active())}</a>'
            f'</div></div>')
    elif s and site_availability._offers_appointments(s) and site_availability._habilita(s, str(m.get("id") or "")):
        return site_studies._habilita_band(
            site_availability._habilita(s, str(m.get("id") or "")),
            f'{site_base._esc(site_listing._appointment_href(s))}?medico={doctor_q}', orden_badge)
    elif s:
        return (
            f'<div class="mp-band mp-band--off">'
            f'<span class="mp-band-ico" aria-hidden="true">{site_shell._CAL_ICON}</span>'
            f'<div class="mp-band-txt">'
            f'<div class="mp-band-note">{i18n.t_or("listing.date_confirmed", "La fecha la confirma el centro", i18n.active())}{orden_badge}</div></div>'
            f'<div class="mp-band-actions">'
            f'<a class="mp-cta mp-cta--ink" href="{site_base._esc(site_listing._appointment_href(s))}'
            f'?medico={doctor_q}">'
            f'{site_studies._order_cta_label() if con_orden_m else i18n.t_or("listing.book", "Sacar turno", i18n.active())}</a></div>'
            f'</div>')
    else:
        cta_href, cta_label = ((f'/center/{centro_slug}',
                        i18n.t_or("page.cta.how_to_be_seen", "Ver cómo atenderte", i18n.active()))
                               if centro_slug else
                               ("/centers", i18n.t_or("doctor.cta_see_centers", "Ver los centros", i18n.active())))
        return (
            f'<div class="mp-band{band_off}">'
            f'<span class="mp-band-ico" aria-hidden="true">{site_shell._CAL_ICON}</span>'
            f'<div class="mp-band-txt"><div class="mp-band-k">{band_k}</div>'
            f'<div class="mp-band-v">{site_base._esc(px.get("v") or "")}</div></div>'
            f'<div class="mp-band-actions">'
            f'<a class="mp-cta" href="{site_base._esc(cta_href)}">{cta_label} {site_shell._ARROW_ICON}</a></div>'
            f'</div>'
        )


def _doctor_place_html(m, c, centro_nombre):
    """Where they see patients: the center with its address, phone and schedule TOGETHER and visible (not in a
    fold) — the "where" is part of deciding on the appointment."""
    ref = []
    if c.get("address"):
        ref.append(
            f'<div class="ci"><a class="stz-link ci-ico" href="{site_base._esc(site_centers._maps_url(c))}" '
            f'target="_blank" rel="noopener">{site_shell._PIN_ICON}{site_base._esc(c["address"])}</a></div>')
    ref.extend(f'<div class="ci">{a}</div>' for a in site_centers._center_call_links(c, "stz-link ci-ico"))
    if c.get("horario"):
        ref.append(f'<div class="ci"><span class="ci-k">{i18n.t_or("page.hours.time", "Horario", i18n.active())}</span> {site_base._esc(c["horario"])}</div>')
    # With the table above already saying each schedule's center, this
    # block only makes sense when there's ONE center: for a professional
    # with two venues it used to state "Atiende en <one>" and it was
    # flat-out false. With two or more, each row's center already links to
    # its page, which has address and phone.
    multi_centro = len({f["centro"] for f in site_availability._schedule_lines(m)}) > 1
    if not centro_nombre or multi_centro:
        return ""
    return (
        f'<div class="mp-lugar">'
        f'{"".join(ref)}</div>')


def _doctor_service_href(m, t):
    """Where a service named on a doctor's page leads: booking that service
    with this doctor when it takes appointments, else its page."""
    if t and site_availability._offers_appointments(t):
        return f'{site_listing._appointment_href(t)}?medico={urllib.parse.quote(str(m.get("id") or ""))}'
    return f'/service/{urllib.parse.quote(str((t or {}).get("id") or ""))}'


def _doctor_schedule_href(m):
    """`_doctor_service_href` keyed by service id, for «Cuándo atiende»."""
    def href(sid):
        t = site_catalog._service_by_id(sid)
        return _doctor_service_href(m, t) if t else f'/service/{urllib.parse.quote(str(sid))}'
    return href


def _doctor_services_html(m, s):
    """The link(s) from the doctor's page to the page of the service(s) they book into."""
    # Link to the page of the service the doctor books into (the same
    # `_doctor_service` the band's button uses): a plain text link, so the
    # doctor page points to the service page and the band stays the one action.
    # A doctor who does several things lists every one of them, each linking
    # to booking that service with this doctor (on-site tester, 2026-10-21, after
    # webturnos: "aparece el listado de todo lo que hace").
    propios = [t for t in site_doctors._doctor_tagged_services(m) if t.get("id") and t.get("nombre")]
    if len(propios) > 1:
        # «Cuándo atiende» already names and links every one of them, one
        # strip each: the line would repeat the blocks (hr-patient review #23).
        filas = site_availability._schedule_lines(m)
        en_agenda = {str(f.get("servicio_id") or "") for f in filas}
        # the strips name their service only when they show more than one
        nombrados = len({site_base._norm_name(f.get("servicio_nombre")) for f in filas
                         if f.get("servicio_nombre")}) > 1
        if nombrados and all(str(t["id"]) in en_agenda for t in propios):
            return ""
        links = ", ".join(
            f'<a class="stz-link" href="{site_base._esc(_doctor_service_href(m, t))}">{site_base._esc(t["nombre"])}</a>'
            for t in propios)
        return (f'<p class="mp-service">'
                f'{i18n.t_or("doctor.services_line", "Atiende: {services}", i18n.active(), services=links)}</p>')
    if s and s.get("id") and s.get("nombre"):
        return (
            f'<p class="mp-service"><a class="stz-link" href="/service/{site_base._esc(urllib.parse.quote(str(s["id"])))}">'
            f'{site_base._esc(i18n.t_or("doctor.service_link", "Ver el servicio: {service}", i18n.active(), service=s["nombre"]))}'
            f'</a></p>')
    return ""


def _doctor_physician_ld(m, name_full, esp, c, centro_nombre, centro_slug):
    """JSON-LD Physician of a real doctor (a demo isn't a real person, it isn't declared as a schema.org entity)."""
    ld = {
        "@context": "https://schema.org", "@type": "Physician",
        "name": name_full, "url": seo.canonical_url(f"/doctor/{m.get('id', '')}"),
        "areaServed": f'{site_identity.get("city")}, {site_identity.get("province")}, {site_base._COUNTRY["name"]}',
    }
    if esp:
        ld["medicalSpecialty"] = esp
    if centro_nombre:
        # Same type and url as that center's own page (_center_ld): the
        # hospital is a Hospital, not a MedicalClinic.
        ld["memberOf"] = {"@type": site_centers._center_schema_type(centro_slug), "name": centro_nombre,
                          "address": site_centers._center_address_ld(c)}
        if centro_slug:
            ld["memberOf"]["url"] = seo.canonical_url(f"/center/{centro_slug}")
    return f'<script type="application/ld+json">{site_base._json_inline(ld)}</script>'


def _doctor_title_and_description(name_full, esp, px):
    """The <title> and the meta description of a doctor's page."""
    # Título y descripción SEO: la frase entera es UNA entrada de catálogo
    # con {name}/{specialty}/{city} nombradas — antes se armaba por
    # concatenación en Python ("{especialidad} en {ciudad}"), lo que
    # congelaba el orden de las palabras en castellano y dejaba "en" sin
    # traducir en la página en inglés (responsable del producto/coordinador, 2026-08-04).
    title_full = (i18n.t_or("doctor.title_specialty_city", "{name} — {specialty} en {city}",
                             i18n.active(), name=name_full, specialty=esp, city=site_identity.get("city"))
                  if esp else f'{name_full} — {site_identity.get("hospital")}')
    desc_cola = (i18n.t_or("page.doctor.desc_walkin",
                            "Mirá los días y horarios en que atiende: sin turno, por llegada.",
                            i18n.active())
                 if px.get("kind") == "walkin"
                 else i18n.t_or("page.doctor.desc_booking",
                                "Mirá el próximo turno y sacá turno donde atiende.",
                                i18n.active()))
    desc_intro = i18n.t_or("doctor.meta_intro", "{name}, {specialty} en el sistema público de salud de {city}.",
                            i18n.active(), name=name_full, specialty=esp, city=site_identity.get("city"))
    return title_full, f'{desc_intro} {desc_cola}'


def _page_doctor(mid):
    """A professional's own page: /doctor/<id>. Large portrait, name,
    specialty · center, next-appointment band and ONE action (book where
    they see patients), with the center's reference in a fold and JSON-LD
    Physician."""
    m = site_doctors._doctor_by_id(mid)
    if not m:
        return None
    is_demo = bool(m.get("demo"))
    nombre = m.get("nombre") or ""
    sex = site_doctors._doctor_genero(m) or site_doctors._guess_sex(nombre)
    # El inglés no gendera el título ("Dr." para los dos) — ambas claves
    # existen para que el castellano siga distinguiendo "Dra."/"Dr.".
    titulo = (i18n.t_or("doctor.title_f", "Dra.", i18n.active()) if sex == "f"
              else (i18n.t_or("doctor.title_m", "Dr.", i18n.active()) if sex == "m" else ""))
    name_full = (titulo + " " + nombre).strip()
    cl = site_catalog._cluster_by_id(m.get("cluster")) or {}
    esp = m.get("especialidad") or cl.get("nombre") or ""
    c = site_centers._centers_by_slug().get(m.get("centro")) or {}
    centro_nombre = c.get("name") or ""
    centro_slug = c.get("slug") or m.get("centro") or ""
    avatar, ph = _doctor_avatar_html(m, nombre)

    # Subtitle: specialty alone — the center lives in its own block, next to
    # the address and phone (one fact, shown once). It's a link to every
    # professional of that specialty at any center (the product owner, 2026-07-11) —
    # /doctors preselects the facet via a query param.
    # A second specialty (hospital TASK-35) is one more link beside it.
    # The link carries the generic title, the value of /doctors' Especialidad
    # filter; the text is the doctor's own («Cardióloga»).
    subtitle = " · ".join(
        f'<a href="/doctors?specialty={urllib.parse.quote(clave)}">{site_base._esc(label)}</a>'
        for label, clave in zip(site_doctors._doctor_specialty_labels(m),
                                site_doctors._doctor_specialty_labels(m, gendered=False)))

    # Next-appointment band (same honest semantics as the listing row).
    px = site_availability._doctor_next(m)
    s = site_doctors._doctor_service(m)
    # "Cuándo atiende": one row per window, with THEIR office and THEIR
    # center. When it exists, it replaces the sign's weekly grid — it says
    # the same (days and hours) plus which office it is.
    agenda_html = site_availability._schedule_table_html(m, href_for=_doctor_schedule_href(m))
    horario_script = ""
    if px.get("kind") == "walkin":
        band, horario_script = _doctor_walkin_band(px, agenda_html)
    else:
        band = _doctor_booking_band(m, s, px, c, centro_slug)

    ref_html = _doctor_place_html(m, c, centro_nombre)
    service_html = _doctor_services_html(m, s)
    ld_html = "" if is_demo else _doctor_physician_ld(m, name_full, esp, c, centro_nombre, centro_slug)

    # "demo" chip next to the name — the doctor/schedule is made up for the
    # demo, not a real hospital professional (the product owner, 2026-07-10).
    demo_chip = ' <span class="mp-demo-tag">demo</span>' if is_demo else ""
    demo_note = (f'<p class="mp-demo-note">{i18n.t_or("doctor.demo_note", "Agenda de demostración — datos no reales.", i18n.active())}</p>'
                 if is_demo else "")
    photo_zoom_script = f'<script type="module" src="{static_fingerprint.url("platform/photo-zoom.mjs")}"></script>' if ph else ""

    body = f"""
  {ld_html}
  <div class="mp-hero">
    {avatar}
    <div class="mp-hgroup">
      <h1 class="mp-h1">{site_base._esc(name_full)}{demo_chip}</h1>
      {f'<p class="mp-sub">{subtitle}</p>' if subtitle else ''}
    </div>
  </div>
  {photo_zoom_script}
  {band}
  {demo_note}
  {agenda_html}
  {ref_html}
  {service_html}
  {horario_script}
"""
    title_full, description = _doctor_title_and_description(name_full, esp, px)
    return site_shell._shell(f'{title_full} | {theme.brand_name()}', "medicos", body,
                  description, canonical=f"/doctor/{mid}")