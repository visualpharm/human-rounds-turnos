"""patient_web/site_page_centers.py — The center pages of the public site.

The centers list and a center page, the workshops and the walk-in page.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import urllib.parse

from app.core import format
from app.core import i18n
from app.core import site_identity
from app.core import static_fingerprint
from app.core import theme

from app.patient_web import site_availability
from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_centers
from app.patient_web import site_listing
from app.patient_web import site_schedule
from app.patient_web import site_shell


# ---------------------------------------------------------------- pages
def _page_centers():
    # Photo tile mosaic (same component as the home page: tiles.mjs). Each
    # tile is ONE link to /center/<slug>; no nested links, no accent-colored text.
    centros = site_centers._centers_web()
    # Flat list, no "Other offices" subcategory — Zoonosis and the rest of
    # the offices with no online booking mix in with the rest (the product owner,
    # 2026-07-11: don't sort them as a separate subcategory).
    tiles = [site_centers._tile_center(c) for c in centros]
    payload = {"centros": tiles}
    # The first tile's photo is this page's LCP, but tiles.mjs only draws it
    # once it runs: the browser can't even request it until then (measured:
    # 1.2s of "load delay"). The preload kicks it off together with the HTML.
    # Only the first one — preloading ten would compete for bandwidth with
    # the one that's actually seen.
    primera = next((t.get("foto") for t in tiles if t.get("foto")), "")
    pre = (f'<link rel="preload" as="image" href="{site_base._esc(primera)}" '
           f'fetchpriority="high">') if primera else ""
    # Same as .lst-slot: tiles.mjs draws AFTER the first paint, so the mosaic
    # is born empty and pushes everything below it down once it fills. It
    # starts near the top of the page here, so the whole jump is visible.
    # The server only counts: tiles, and rows in 2 and in 3 columns. The row
    # height follows the photo, whose width follows the viewport, so the shell
    # CSS turns the counts into a height ([data-tiles] in site_shell.py). The
    # old fixed 168px desktop row left the footer in view at 1280 (CLS 0.18).
    # `:not(:has(.tile))` clears it as soon as there's content.
    n_t = len(tiles)
    reserva_t = (f'style="--tiles-n:{n_t};--tiles-filas-2:{-(-n_t // 2)};'
                 f'--tiles-filas-3:{-(-n_t // 3)}"') if n_t else ""
    tiles_html = (f'<div data-tiles {reserva_t}>{site_listing._tile_links(tiles)}<script type="application/json" class="tiles-data">'
                  f'{site_base._json_inline(payload)}</script></div>')

    body = f"""
  <h1 class="h1">{site_base._esc(i18n.t_or("page.centers.h1", "Hospital y centros de salud de {city}", i18n.active(), city=site_identity.get("city")))}</h1>
  {tiles_html}
  {_workshops_section()}
  <script type="module" src="{static_fingerprint.url('platform/tiles.mjs')}"></script>
"""
    return site_shell._shell(f'{i18n.t_or("page.centers.title", "Centros de salud de {city}", i18n.active(), city=site_identity.get("city"))} | {theme.brand_name()}', "centros", body,
                  f'{site_identity.get("network")}: direcciones, teléfonos, horarios y servicios.',
                  canonical="/centers", head_extra=pre)


def _center_no_booking_contact(c):
    """The contact a center that takes no turnos repeats at the decision point."""
    # A non-bookable center repeats the actual contact at the decision point;
    # “use the details above” makes people hunt and is especially poor on a
    # long mobile page. WhatsApp keeps its icon so it is not mistaken for a
    # voice-only phone number.
    no_booking_contact = ""
    center_wa = str(c.get("whatsapp") or "").strip()
    center_email = str(c.get("email") or "").strip()
    center_phone = str(c.get("phone") or "").strip()
    if center_wa:
        wa_digits = format.tel_wa(center_wa)
        if wa_digits:
            no_booking_contact += (
                f'<a class="wa-action wa-action--inline" href="https://wa.me/{wa_digits}">'
                f'<img src="/static/icons/whatsapp-sf-regular.svg" alt="">'
                f'WhatsApp · {site_base._esc(format.tel_text(center_wa))}</a>')
    elif center_phone:
        no_booking_contact += (
            f'<a class="stz-link" href="tel:{site_base._esc(site_centers._phone_href(center_phone))}">'
            f'{site_base._esc(format.tel_text(center_phone))}</a>')
    if center_email:
        if no_booking_contact:
            no_booking_contact += " " + site_base._esc(i18n.t_or("center.contact_or", "o", i18n.active())) + " "
        no_booking_contact += f'<a class="stz-link" href="mailto:{site_base._esc(center_email)}">{site_base._esc(center_email)}</a>'
    return no_booking_contact


def _center_doctors_html(svs, meds, hay_ambas):
    """The listing of the center's professionals; '' when it has none."""
    ejemplos_c = site_listing._example_dedup(site_listing._example_service(svs), site_listing._example_doctor(meds), site_listing._example_specialty(meds))
    if not meds:
        return ""
    # soonest availability first, then name, as on /doctors (demo review
    # 2026-11-15, P20); a doctor with no turno names the next step (P8)
    mrows = site_availability._with_remedies(site_listing._soonest_first(
        [site_listing._doctor_listing_row(m) for m in sorted(meds, key=lambda m: m.get("nombre") or "")]))
    return site_listing._listing_block(
        "centro-medicos", site_listing._centro_medicos_cols(), mrows,
        {"title": i18n.t_or("page.doctors.title", "Médicos y profesionales", i18n.active()),
         "rowHref": "href",
         "search": True, "searchGroup": "centro" if hay_ambas else None,
         # no match here → the same query in the all-centers search
         "searchAllHref": "/doctors",
         "searchExamples": ejemplos_c,
         "searchPlaceholder": i18n.t_or("page.doctors.search", "Buscar médico, especialidad o servicio", i18n.active())})


def _center_services_html(c, svs, meds_html, hay_ambas):
    """The center's services in ONE table, after the doctors' listing when it has one."""
    rows = [site_listing._service_area_row(s) for s in svs]
    # "lugar" (venue) only shows up as a column if SOME row of this
    # center has its own venue (e.g. CAPS Ostende: two "Medicina
    # general" rows told apart by where — venue vs. main building, the product owner
    # 2026-07-11).
    claves = ["servicio", "area"]
    if any(r["lugar"] != (c.get("name") or "") for r in rows):
        # only the differing venue shows: repeating the center's own
        # name in every row is noise (ui-rules: each fact appears once
        # — the reader is already ON that center's page).
        for r in rows:
            if r["lugar"] == (c.get("name") or ""):
                r["lugar"] = ""
        claves.append("lugar")
    claves += ["proximo", "orden"]
    cols = site_listing._cols(*claves)
    filters = [{"t": "facet", "key": "area",
                "label": i18n.t_or("listing.col.area", "Área", i18n.active())}]
    if any(r["publico"] != "todos" for r in rows):
        filters.append({"t": "publico"})
    if any(r["sexo"] for r in rows):
        filters.append({"t": "sexo"})
    titulo_svs = ({"title": i18n.t_or("center.services_title", "Servicios", i18n.active())}
                  if meds_html else {})
    # search ALWAYS (the product owner, 2026-07-16): the center's search box looks and
    # behaves the same as /doctors' and /estudios' — listing.js only used
    # to draw it with >15 rows, so a small CAPS was left without a search box.
    # With doctors, THIS table listens to the box above (searchGroup) and
    # doesn't draw its own; without doctors it's the driver and brings its
    # own examples (service · area).
    if hay_ambas:
        svs_opts = {"filters": filters, "search": False, "searchGroup": "centro",
                    "rank": "popularidad"}
    else:
        area_ej = site_listing._example_specialty([{"especialidad": r.get("area")} for r in rows])
        svs_opts = {"filters": filters, "searchPlaceholder": i18n.t_or("page.services.search", "Buscar servicio o área", i18n.active()),
                    "search": True, "rank": "popularidad", "searchAllHref": "/doctors",
                    "searchExamples": site_listing._example_dedup(site_listing._example_service(svs), area_ej)}
    return (meds_html
            + site_listing._listing_block("centro-servicios", cols, rows,
                             dict(svs_opts, **titulo_svs))
            + site_listing._LISTING_SCRIPT)


def _center_sections_html(c, svs, meds):
    """What follows the header: the doctors' and the services' listings, or how to reach a center that takes no turnos."""
    # ONE single search box for the center (the product owner, 2026-07-23): filters
    # doctors AND services (searchGroup). Examples: a doctor's last name · a
    # service · a specialty. No locality — you're already AT a center.
    # Driver = the doctors table (goes first); the services one listens and
    # hides itself if it doesn't match.
    hay_ambas = bool(meds) and bool(svs)
    meds_html = _center_doctors_html(svs, meds, hay_ambas)
    if svs:
        return _center_services_html(c, svs, meds_html, hay_ambas)
    if meds_html:
        return meds_html + site_listing._LISTING_SCRIPT
    no_booking_contact = _center_no_booking_contact(c)
    return ('<div class="note">'
            + site_base._esc(i18n.t_or("center.no_online_booking", "Este centro no toma turnos por la app. Coordiná directamente:", i18n.active()))
            + (" " + no_booking_contact if no_booking_contact else "")
            + '</div>')


def _center_hero_html(c, foto, gal_group):
    """The center's photo as a contained block that opens the viewer; '' without a photo."""
    if not foto:
        return ""
    # Photo: a contained block (no more full-width strip) that on desktop
    # goes to the header's right column; on mobile it goes below the lede.
    # No photo → no hero. Clicking the hero opens the native photo viewer.
    # with ALL of the center's photos (the product owner, 2026-07-16): the hero and the
    # gallery share the same data-photozoom-group, so the <details> fold
    # doesn't hide them.
    # No photo credit on the page (the product owner, 2026-07-10): the source stays
    # documented in centros-info.json, not on screen.
    return (
        f'<figure class="hero-photo">'
        f'<a href="{site_base._esc(foto)}" target="_blank" rel="noopener" '
        f'data-photozoom data-photozoom-group="{site_base._esc(gal_group)}" '
        f'data-photozoom-src="{site_base._esc(foto)}" '
        f'data-photozoom-alt="{site_base._esc(c.get("name", ""))}">'
        f'<img src="{site_base._esc(foto)}" alt="{site_base._esc(c.get("name", ""))}" loading="lazy"></a></figure>')


def _center_contact_html(c):
    """Main contact of the center, right below the lede."""
    # Main contact (address with a Maps link + phone + email) right below
    # the lede — where Google's rating used to be, which was removed (the product owner,
    # 2026-07-10). The email stopped living under the fold (the product owner, 2026-07-16).
    contact_bits = []
    if c.get("address"):
        # a temporary place: the date follows the link, never inside it
        lugar = c.get("address_place") if c.get("address_until") else None
        contact_bits.append(
            f'<a class="stz-link" href="{site_base._esc(site_centers._maps_url(c))}" '
            f'target="_blank" rel="noopener">{site_base._esc(lugar or c["address"])}</a>'
            + (f', {site_base._esc(c["address_until"])}' if lugar else ""))
    if c.get("phone"):
        contact_bits.append(
            f'<a class="stz-link" href="tel:{site_base._esc(site_centers._phone_href(c["phone"]))}">{site_base._esc(site_centers._phone_text(c["phone"]))}</a>')
    if c.get("whatsapp"):
        wa_digits = format.tel_wa(c["whatsapp"])
        if wa_digits:
            contact_bits.append(
                f'<a class="wa-action wa-action--inline" href="https://wa.me/{wa_digits}">'
                f'<img src="/static/icons/whatsapp-sf-regular.svg" alt="">'
                f'WhatsApp · {site_base._esc(format.tel_text(c["whatsapp"]))}</a>')
    if c.get("email"):
        contact_bits.append(
            f'<a class="stz-link" href="mailto:{site_base._esc(c["email"])}">{site_base._esc(c["email"])}</a>')
    return f'<p class="c-contact">{" · ".join(contact_bits)}</p>' if contact_bits else ""


def _center_facts_html(c, svs, info, lede):
    """The center's jobs and facts as ONE bullet list; '' when there are none."""
    # Jobs (verb-first) + verified facts + secondary info: ONE single bullet
    # list, one column (the product owner, 2026-07-16 — two lists with different styles
    # read as two separate things). The old "more info" also joins in here:
    # a fold for a single line took more room than the line itself (the product owner,
    # 2026-07-16). Facts stay deduplicated against what the page already
    # shows (lede + jobs + service names): never repeat a fact.
    jobs = c.get("jobs")
    jobs_list = jobs if isinstance(jobs, list) else []
    corpus = " ".join([lede] + jobs_list + [s.get("nombre", "") for s in svs])
    extras = ([c["horario"]] if c.get("horario") else []) \
        + ([c["notas"]] if c.get("notas") else []) \
        + [m for m in info.get("more") or [] if m]
    datos = site_catalog._dedupe_facts((info.get("facts") or []) + extras, corpus)
    # A job that IS the name of one of this center's services («Vacunatorio»)
    # links to that service's page, where its hours and rules live (Pinamar
    # press office, 2026-10-22: «Vacunatorios > enlaces a la ficha de cada
    # vacunatorio»). Only an unambiguous match: two services with that name
    # at the center → plain text.
    por_nombre = {}
    for s in svs:
        por_nombre.setdefault(site_base._norm_name(s.get("nombre")).strip(), []).append(s)

    def _punto(p, es_job):
        mismo = por_nombre.get(site_base._norm_name(p).strip()) if es_job else None
        if mismo and len(mismo) == 1 and mismo[0].get("id"):
            return f'<li><a href="/service/{site_base._esc(mismo[0]["id"])}">{site_base._esc(p)}</a></li>'
        return f"<li>{site_base._esc(p)}</li>"
    puntos = [(j, True) for j in jobs_list if j] + [(d, False) for d in datos]
    if not puntos:
        return ""
    return '<ul class="facts">' + "".join(_punto(p, es_job) for p, es_job in puntos) + "</ul>"


def _center_head_html(c, lede, contact_html, hero_html, bottom_html):
    """The header: title, lede and contact, and the jobs and facts below, next to the photo on desktop."""
    # Two-column header on desktop (title/lede/contact on top and jobs/facts
    # below, both in the left column; the photo takes up the right column
    # end to end). No photo → no grid, the usual plain layout.
    top_html = f'<h1 class="h1">{site_base._esc(c["name"])}</h1><p class="lede">{site_base._esc(lede)}</p>{contact_html}'
    if not hero_html:
        return top_html + bottom_html
    return (
        '<div class="centro-head">'
        f'<div class="ch-top">{top_html}</div>'
        f'<div class="ch-photo">{hero_html}</div>'
        f'<div class="ch-bottom">{bottom_html}</div>'
        '</div>'
    )


def _center_gallery_thumbs(c, galeria, gal_group):
    """The gallery thumbnails of the center, each one a link into the photo viewer."""
    # Gallery thumbnails: click enlarges WITH navigation (photo-zoom.mjs)
    # across all of the center's photos — the fallback href still opens the
    # image alone if the JS didn't load (degrades without breaking
    # anything). No credit line on screen (same reasons as the hero).
    # alt: what the photo shows when the pack says so (`alt` in
    # centros-galeria.json, e.g. «Entrada de la guardia»); otherwise «<center>,
    # foto 2 de 12» — never the same alt on every image (SEO audit 2026-10-01,
    # Pinamar item 12), and never a description nobody wrote.
    fotos = [g for g in galeria if g.get("src")]

    def _gal_alt(n, g):
        if g.get("alt"):
            return g["alt"]
        if len(fotos) < 2:
            return c.get("name", "")
        return i18n.t_or(
            "center.photo_alt", "{name}, foto {n} de {total}", i18n.active(),
            name=c.get("name", ""), n=n, total=len(fotos))
    return "".join(
        f'<a class="gal-thumb" href="{site_base._esc(g.get("src", ""))}" target="_blank" rel="noopener" '
        f'data-photozoom data-photozoom-group="{site_base._esc(gal_group)}" data-photozoom-src="{site_base._esc(g.get("src", ""))}" '
        f'data-photozoom-alt="{site_base._esc(_gal_alt(n, g))}">'
        f'<img src="{site_base._esc(g.get("src", ""))}" alt="{site_base._esc(_gal_alt(n, g))}" loading="lazy"></a>'
        for n, g in enumerate(fotos, 1)
    )


def _center_folds_html(thumbs, resenas):
    """The folds under the header: photos, neighbor quotes and neighbor tips (only the ones with content)."""
    # Neighbor quotes and tips: no stars or Google branding — the quotes
    # stand as neighbors' voice, the rating was removed (2026-07-10).
    quote_items = "".join(
        f'<li>{site_base._esc(q["text"])}</li>'
        for q in resenas.get("quotes") or [] if q.get("text")
    )
    consejo_items = "".join(f"<li>{site_base._esc(t)}</li>" for t in resenas.get("tips") or [] if t)

    # Three separate folds, each with its own name in the summary (the product owner,
    # 2026-07-16 — a single "Photos, tips… and more info" mixed everything
    # together): Fotos / Lo que dicen los vecinos / Consejos de vecinos. Only
    # the ones with content get drawn. No sources on screen (we're part of
    # the same portal we were quoting from; they stay documented in
    # centros-info.json). Clicking the hero photo opens the same photos in
    # the viewer without opening the fold.
    pliegues = []
    if thumbs:
        pliegues.append(f'<details class="more"><summary>{i18n.t_or("center.photos", "Fotos", i18n.active())}</summary>'
                        f'<div class="more-body"><div class="gal-grid">{thumbs}</div></div></details>')
    if quote_items:
        pliegues.append(f'<details class="more"><summary>{i18n.t_or("center.neighbor_quotes", "Lo que dicen los vecinos", i18n.active())}</summary>'
                        f'<div class="more-body"><ul class="quotes">{quote_items}</ul></div></details>')
    if consejo_items:
        pliegues.append(f'<details class="more"><summary>{i18n.t_or("center.neighbor_tips", "Consejos de vecinos", i18n.active())}</summary>'
                        f'<div class="more-body"><ul class="consejos">{consejo_items}</ul></div></details>')
    return "".join(pliegues)


def _page_center(slug):
    c = site_centers._centers_by_slug().get(slug)
    if not c:
        return None
    svs = [s for s in site_catalog._services() if s.get("centro") == slug]
    # The center's professionals first (CAPS Valeria design, 2026-07-10):
    # portrait + friendly next appointment; the whole row books, the
    # specialty links to its page.
    meds = sorted((m for m in site_catalog._professionals() if m.get("centro") == slug),
                  key=lambda m: m["nombre"])
    secs = _center_sections_html(c, svs, meds)

    # Lede = "what you use it for". jtbd if the data brings it; otherwise the description.
    lede = c.get("jtbd") or c.get("description") or ""

    info = site_centers._centers_info().get(slug) or {}
    resenas = site_centers._centers_reviews().get(slug) or {}

    galeria = site_centers._centers_gallery().get(slug) or []
    gal_group = f"centro-{slug}"
    foto = site_centers._center_photo(slug, info)
    hero_html = _center_hero_html(c, foto, gal_group)
    head_html = _center_head_html(c, lede, _center_contact_html(c), hero_html,
                                  _center_facts_html(c, svs, info, lede))
    thumbs = _center_gallery_thumbs(c, galeria, gal_group)
    details_html = _center_folds_html(thumbs, resenas)

    pz_script = f'<script type="module" src="{static_fingerprint.url("platform/photo-zoom.mjs")}"></script>' if (hero_html or thumbs) else ""

    body = f"""
  {site_centers._center_ld(c, foto)}
  {head_html}
  {details_html}
{secs}
  {pz_script}
"""
    desc = (i18n.t_or("center.meta_with_lede", "{lede} Consultá servicios, profesionales, horarios y datos de contacto.",
                      i18n.active(), lede=lede)
            if lede else
            i18n.t_or("center.meta_default", "Servicios, profesionales, horarios y contacto de {name} en {city}.",
                      i18n.active(), name=c["name"], city=site_identity.get("city")))
    return site_shell._shell(f'{c["name"]} | {theme.brand_name()}', "centros", body, desc,
                  canonical=f"/center/{slug}")


def _age_workshop(s):
    """A workshop's age, for the tile's TITLE (nombre_sub) — only when it
    genuinely restricts: 'De 13 a 17 años', 'Para mayores de 60'. A min of
    18 (adults) stays quiet: it's the default and on 'Curso acompañamiento
    embarazo' it'd be noise."""
    e = s.get("edad") or {}
    mn, mx = e.get("min"), e.get("max")
    if mx is not None:
        return (i18n.t_or("page.workshop.age_between", "De {mn} a {mx} años",
                          i18n.active(), mn=mn, mx=mx)
                if mn is not None
                else i18n.t_or("page.workshop.age_upto", "Hasta {mx} años",
                               i18n.active(), mx=mx))
    if mn is not None and mn > 18:
        return i18n.t_or("page.workshop.age_over", "Para mayores de {mn}",
                         i18n.active(), mn=mn)
    return ""


def _workshop_note(s):
    """The ONLY thing a workshop's sign adds on top of the shared template
    ('Acercate al CAPS durante el horario del grupo' is already said by the
    page's lede): e.g. Adolescentes' brief entry interview. Lives in its OWN
    `nota` field on the service (packs/.../servicios.json) — it used to be
    parsed out of `cartel` at render time by matching a hardcoded Spanish
    marker ('durante el horario del grupo.'), which silently dropped the
    note on every non-Spanish page: `cartel` is pack content pack_l10n
    machine-translates at the loader boundary, so the Spanish marker never
    matches the already-translated string (found 2026-08-04, the demo in
    English). `nota` is itself one of pack_l10n.FIELDS's translatable
    fields, so it already arrives in the visitor's language — no parsing,
    no marker, nothing that can go stale when the prose changes language."""
    return s.get("nota") or ""


def _venue_href(venue, center):
    """Installation-owned destination for a venue without its own page.

    ``sitio.json`` may map an exact venue name to an HTTPS destination under
    ``venues``. Missing or malformed entries fall back to the owning center;
    pack content can never inject a script/data URL into the patient page.
    """
    venues = site_identity.get("venues", {})
    href = venues.get(venue) if isinstance(venues, dict) else None
    if isinstance(href, str):
        parsed = urllib.parse.urlparse(href.strip())
        if parsed.scheme == "https" and parsed.netloc:
            return href.strip()
    return f'/center/{center["slug"]}' if center else ""


def _workshops_section():
    """The workshops, after the centers: a heading that links to /d/grupos
    and the workshops page's own one-line lede. It replaces the «Centros |
    Talleres» tab bar that sat above both titles (the product owner moved
    Talleres out of the top menu on 2026-10-03; the demo review of
    2026-11-15 dropped the tab bar, P35). The header keeps «Centros» active
    on /d/grupos. A pack with no workshops gets nothing."""
    if not any(s.get("categoria") == "grupos" or s.get("cluster") == "grupos"
               for s in site_catalog._services()):
        return ""
    lang = i18n.active()
    cl = site_catalog._cluster_by_id("grupos") or {}
    titulo = cl.get("nombre") or i18n.t_or("nav.groups", "Talleres", lang)
    lede = i18n.t_or("page.workshops.lede", "Espacios grupales gratuitos del sistema público de {city}. No necesitás turno: acercate en el horario del grupo.", lang, city=site_base._esc(site_identity.get("city")))
    return (f'<section class="sec"><h2 class="lst-h centers-workshops"><a href="/d/grupos">'
            f'{site_base._esc(titulo)}</a></h2><p class="lede">{lede}</p></section>')


def _page_workshops(cl):
    """Groups and workshops (/d/grupos): a mosaic of INFORMATIONAL tiles —
    all of the workshop's info lives on this page (the product owner, 2026-07-16: no
    per-workshop detail pages). tiles.mjs with href:"" draws a <div> with no
    hover. A workshop is flagged categoria='grupos' even when its thematic
    cluster is something else (salud-mental, clinica): ALL of them come
    together here — this page used to filter by cluster and show 5 of 14.
    The center's name inside the tile LINKS to its page (the product owner: no separate
    'Where they are' block) — that's where address, phone and map live; the
    image lives in the service's `foto` (webdata/talleres/, sources in
    FUENTES.md)."""
    centros = site_centers._centers_by_slug()
    svs = sorted((s for s in site_catalog._services()
                  if s.get("categoria") == "grupos" or s.get("cluster") == "grupos"),
                 key=lambda s: s["nombre"].lower())
    items = []
    for s in svs:
        c = centros.get(s.get("centro")) or {}
        sede = s.get("sede")
        lugar = sede or c.get("name") or ""
        consultorio = s.get("consultorio")
        sufijo = (", " + i18n.t_or("page.workshop.room", "consultorio {n}",
                                   i18n.active(), n=consultorio)
                  if consultorio and consultorio != "SUM"
                  else (", SUM" if consultorio == "SUM" else ""))
        notas = [n for n in (_workshop_note(s),) if n]
        items.append({
            "href": "",
            "nombre": s["nombre"],
            # the age travels in the title ("· Para mayores de 60"), not as
            # a gray note below the place.
            "nombre_sub": _age_workshop(s),
            "direccion": site_schedule._horario_display(s.get("horario") or ""),
            "jtbd": lugar,
            # The center links to its page. A separately named venue may
            # override that destination through the active installation pack.
            "jtbd_href": (_venue_href(sede, c) if sede
                          else (f'/center/{c["slug"]}' if c else "")),
            "jtbd_suffix": sufijo,
            "notas": notas,
            "foto": s.get("foto") or "",
        })
    payload = {"centros": items}
    tiles_html = (f'<div data-tiles>{site_listing._tile_links(items)}<script type="application/json" class="tiles-data">'
                  f'{site_base._json_inline(payload)}</script></div>')
    body = f"""
  <h1 class="h1">{site_base._esc(cl["nombre"])}</h1>
  <p class="lede">{i18n.t_or("page.workshops.lede", "Espacios grupales gratuitos del sistema público de {city}. No necesitás turno: acercate en el horario del grupo.", i18n.active(), city=site_base._esc(site_identity.get("city")))}</p>
  {tiles_html}
  <script type="module" src="{static_fingerprint.url('platform/tiles.mjs')}"></script>
"""
    return site_shell._shell(f'{cl["nombre"]} | {theme.brand_name()}', "centros", body,
                  i18n.t_or("doctor.workshops_meta_desc",
                            "Grupos y talleres gratuitos del {network}: cuándo se reúnen, dónde y cómo participar.",
                            i18n.active(), network=site_identity.get("network")),
                  canonical=f'/d/{cl["id"]}')


def _page_walkin(cl):
    """«Demanda espontánea» (/d/<id> of a `"kind": "walkin"` cluster): the
    links the cluster names first (Telemedicina), then every walk-in
    consultorio, one table per center in the pack's center order. Walk-in
    never offers an appointment: each row says when it opens, «por orden de
    llegada», and leads to the service's own page."""
    lang = i18n.active()
    links_html = ""
    for lid in cl.get("links") or []:
        e = site_centers._link_by_id(lid)
        if not e or not e.get("href"):
            continue
        accion = str(e.get("accion_href") or "")
        cta = (f'<a class="stz-cta" href="{site_base._esc(accion)}" target="_blank" rel="noopener">'
               f'{site_base._esc(e.get("accion_texto") or e.get("nombre") or "")}</a>'
               if accion.startswith("https://") else "")
        links_html += (f'<section class="sec walkin-link"><h2 class="lst-h">'
                       f'<a href="{site_base._esc(e["href"])}">{site_base._esc(e.get("nombre") or "")}</a></h2>'
                       + (f'<p>{site_base._esc(e["descripcion"])}</p>' if e.get("descripcion") else "")
                       + cta + "</section>")
    centros = site_centers._centers_by_slug()
    grupos = []
    for s in site_catalog._walkin_services():
        if not grupos or grupos[-1][0] != s.get("centro"):
            grupos.append((s.get("centro"), []))
        grupos[-1][1].append(s)
    # One plain list per center, on the page's own left edge: the service (a
    # link to its page) and WHEN it takes people, in the pack's own words
    # («Las 24 horas, todos los días» for the Guardia). No table, no header
    # row, no empty column (the product owner, 2026-11-13).
    fallback = site_base._esc(i18n.t_or("page.walkin.always", "Sin turno, por llegada", lang))
    tablas = ""
    for slug, svs in grupos:
        c = centros.get(slug) or {}
        nombre = site_base._esc(c.get("name") or slug)
        titulo = (f'<a href="/center/{site_base._esc(c["slug"])}">{nombre}</a>'
                  if c.get("slug") else nombre)
        filas = ""
        for s in svs:
            cuando = site_schedule._horario_display(s.get("horario") or "") or ""
            filas += (f'<li><a class="wk-name" href="/service/{site_base._esc(s["id"])}">'
                      f'{site_base._esc(s.get("nombre") or "")}</a>'
                      f'<span class="wk-when">{site_base._esc(cuando) if cuando else fallback}</span></li>')
        tablas += (f'<section class="sec walkin-center"><h2 class="lst-h">{titulo}</h2>'
                   f'<ul class="wk-list">{filas}</ul></section>')
    if not tablas and not links_html:
        return None
    body = f"""
  <h1 class="h1">{site_base._esc(cl["nombre"])}</h1>
  <p class="lede walkin-lede">{site_base._esc(site_catalog._walkin_description(cl, "page.walkin.lede", "No necesitás turno ni orden: te atienden por llegada, en el horario de cada consultorio."))}</p>
  <style>
    .walkin-link p{{margin:0 0 12px;}}
    .wk-list{{list-style:none;margin:0;padding:0;}}
    .walkin-lede{{color:var(--text);}}
    .wk-list li{{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:14px 0;border-top:1px solid var(--line,#e3e3df);}}
    .wk-list li:last-child{{border-bottom:1px solid var(--line,#e3e3df);}}
    .wk-name{{font-size:17px;font-weight:600;}}
    .wk-when{{font-size:15px;color:var(--text,#222);}}
  </style>
{links_html}
{tablas}
"""
    return site_shell._shell(f'{cl["nombre"]} | {theme.brand_name()}', "", body,
                  i18n.t_or("page.walkin.meta",
                            "Dónde atenderte sin turno en el {network}: consultorios por llegada, centro por centro.",
                            lang, network=site_identity.get("network")),
                  canonical=f'/d/{cl["id"]}')
