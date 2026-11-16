"""patient_web/site_studies.py — Studies of the public site.

Grouping of a study, call-to-action labels, the studies table and listing, and the study page.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

from app.core import i18n
from app.core import site_identity
from app.core import theme

from app.patient_web import site_availability
from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_centers
from app.patient_web import site_listing
from app.patient_web import site_schedule
from app.patient_web import site_shell


def _page_study(cid, slug):
    """A study's detail: where to get it done and when. Lists one row per
    center (status + schedule), each one goes to that center's service page.
    Sorted by next availability: the center that sees you SOONER goes on
    top, not the closest one (see `_CHOOSE_BY_WAIT`)."""
    cl = site_catalog._cluster_by_id(cid)
    if not cl:
        return None
    group = [s for s in site_catalog._services()
             if s.get("cluster") == cid and site_base._slug(s.get("nombre")) == slug]
    if not group:
        return None
    nombre = group[0]["nombre"]
    # One row per center — the row CHOOSES the center (same listing.js
    # component). The center links to its own page; the action lives in the
    # combined Appointment column + the whole row (rowHref).
    rows = [site_listing._service_center_row(s) for s in group]
    cols = site_listing._cols("centro", "proximo", "orden")
    opts = {"title": "Dónde te lo hacés", "rowHref": i18n.t_or("page.study.where", "href", i18n.active())}
    listado = site_listing._listing_block("estudio-centros", cols, rows, opts, in_column=True)
    # The wait-vs-travel tip only makes sense if there's MORE THAN ONE center
    # to choose from; with just one there's nothing to compare and it'd be noise.
    elegir = site_listing._CHOOSE_BY_WAIT if len(group) > 1 else ""
    body = f"""
  <h1 class="h1">{site_base._esc(nombre)}</h1>
  <p class="lede">Dónde te hacés {site_base._esc(nombre.lower())} en el sistema público de {site_identity.get("city")} y cuándo.</p>
{elegir}
{listado}
{site_listing._LISTING_SCRIPT}
"""
    # "estudios" section in the nav, NOT "centros": this is a study's page.
    # It used to flag "Centros de salud" — exactly the item the product owner wanted to
    # demote in favor of Estudios (2026-07-12).
    return site_shell._shell(f'{nombre} en {site_identity.get("city")} | {theme.brand_name()}', "estudios", body,
                  f'{nombre} en el {site_identity.get("network")}: dónde te lo hacés y cuándo.',
                  canonical=f"/study/{cid}/{slug}")


_GROUP_KEYS = [
    ("Cardiología", ("electrocardiograma", "ecocardiograma", "ergometria", "holter",
                     "ecodoppler", "cardio", "presurometr", "mapa")),
    ("Laboratorio", ("laboratorio", "extraccion", "extracciones", "analisis", "sangre",
                     "testeo", "hisopado", "glucemia", "hemograma", "perfil lipid",
                     "creatinin", "uricemia", "hemoglobina glicosilada")),
    ("Audiología", ("audiometria", "otoemision", "otoemisiones", "impedanciometr")),
    ("Imagen", ("ecografia", "radiografia", "mamografia", "tomografia", "resonancia",
                "doppler", "densitometr", "ecodoppler")),
]


def _study_group(nombre):
    t = site_base._norm_name(nombre)
    for grupo, keys in _GROUP_KEYS:
        if any(k in t for k in keys):
            return grupo
    return "Otros estudios"


def _get_label(v):
    """Appointment button with a VERB — same grammar as timePill
    (listing.js): 'Hoy, 14:40' -> 'Sacar hoy, 14:40'; 'Viernes 17 de julio,
    13:00' -> 'Sacar el viernes 17 de julio, 13:00' in Spanish, 'Book Friday,
    17 July, 13:00' in English. A button says what it does.

    The article and the lowercase-first-letter used to be bolted onto the
    ALREADY-TRANSLATED string with a Spanish-only rule — that's exactly how
    the demo rendered 'Sacar el tomorrow, 07:00' (the product owner, 2026-08-04): `v` was
    correctly translated, but the grammar wrapped around it wasn't. English
    (and any future language with the same rule) never gets an inserted
    article and never has its capitalisation touched — the catalog already
    gives weekday/month names the right case for that language."""
    lang = i18n.active()
    v = (v or "").strip()
    if not v:
        return i18n.t_or("date.book_turno", "Sacar turno", lang)
    verb = i18n.t_or("date.book_verb", "Sacar", lang)
    if lang != i18n.CANONICAL_LANG:
        return f"{verb} {v}"
    low = v[0].lower() + v[1:]
    primera = low.split(",", 1)[0].strip()
    art = "" if primera in ("hoy", "mañana") else "el "
    return f"{verb} {art}{low}"


def _habilita_text(ap):
    """The sentence under «No hay turnos disponibles» for an
    ``apertura`` from `_next_opening` — the date turnos get enabled; empty
    when there is none (the headline already says it, never twice)."""
    lang = i18n.active()
    if (ap or {}).get("estado") == "fecha" and ap.get("dia"):
        # the availability column's one short date (review 2026-11-15, P21)
        when = site_schedule._short_date_in_phrase(ap["dia"], ap.get("hora") or None) or ap["dia"]
        return i18n.t_or("service.noslots.opens_from_when", "Se habilitan desde {when}", lang, when=when)
    return ""


def _habilita_band(ap, case_href, extra=""):
    """Band of a service with no open turnos: the message INSTEAD of a
    «Sacar turno» that dead-ends (the reference-deployment counterpart, 19/10). The way to write the case
    stays as a quiet link, never named «Sacar turno»."""
    lang = i18n.active()
    when = _habilita_text(ap)
    when_html = f'<div class="mp-band-sub">{site_base._esc(when)}</div>' if when else ""
    return (
        f'<div class="mp-band mp-band--off">'
        f'<span class="mp-band-ico" aria-hidden="true">{site_shell._CAL_ICON}</span>'
        f'<div class="mp-band-txt">'
        f'<div class="mp-band-k">{i18n.t_or("service.noslots.when_label", "No hay turnos disponibles", lang)}{extra}</div>'
        f'{when_html}'
        f'<div class="mp-band-sub">{i18n.t_or("service.noslots.urgency", "Si es una urgencia, llamá al 107 o acercate a la guardia.", lang)}</div></div>'
        f'<div class="mp-band-actions">'
        f'<a class="mp-more" href="{case_href}">{i18n.t_or("service.noslots.case_label", "Contanos tu caso", lang)}</a>'
        f'</div></div>')


def _order_cta_label():
    """«Subí tu orden» — the action of a slot/service that needs a referral
    (the reference-deployment counterpart, 19/10); same key as the listing cell (listing.mjs orderPill)."""
    return i18n.t_or("listing.upload_order", "Subí tu orden", i18n.active())


def _cta_bucket(dia):
    """Same rule as ORDEN.bucket (listing.mjs): every slot is green, the wait
    only shifts it toward blue in 11 steps of 3 days — 'w0' (today/tomorrow)
    … 'w10' (30+ days). The band's button is painted like the listing cell's.
    No date → 'w0' (the old default 'ya')."""
    import datetime
    try:
        dd = (datetime.date.fromisoformat(str(dia)[:10]) - site_base._today_art()).days
    except Exception:
        return "w0"
    return "w%d" % max(0, min(10, int(dd / 3 + 0.5)))


def _studies_place(group):
    """Studies table's "Centro" cell: WHERE you get it done — a link to the
    center's page, just like Especialidad/Centro in Médicos (responsable del producto,
    2026-07-18: "3 centros" doesn't help, and in Médicos that column is
    already a link; here it was plain text). One center → its name, linked.
    Several → the center with MOST weekly hours linked (the one most likely
    to see you sooner), + the rest in `lugar_mas` (also sorted by hours
    desc) for the "+N" that opens "También se hace en" — the whole row still
    leads to the full wait comparison (`/study/<cid>/<slug>`), this is just
    the cell."""
    centros = site_centers._centers_by_slug()
    ordenado = sorted(group, key=lambda s: site_availability._week_hours(s), reverse=True)
    principal = centros.get(ordenado[0].get("centro")) or {}
    nombre = principal.get("name") or ""
    href = f'/center/{principal["slug"]}' if principal.get("slug") else ""
    mas = []
    vistos = {principal.get("slug")}
    for s in ordenado[1:]:
        c = centros.get(s.get("centro")) or {}
        if not c.get("slug") or c["slug"] in vistos:
            continue
        vistos.add(c["slug"])
        mas.append({"nombre": c.get("name") or "", "href": f'/center/{c["slug"]}'})
    return nombre, href, mas


def _studies_table_data(cid):
    """Studies table rows (one study = one row), enriched with the next
    online appointment, where it's done, group, audience and sex
    restriction. A single piece of data per column."""
    svs = [s for s in site_catalog._services() if s.get("cluster") == cid]
    grupos = {}
    for s in svs:
        grupos.setdefault(s["nombre"], []).append(s)
    rows = []
    for nombre in sorted(grupos, key=str.lower):
        group = grupos[nombre]
        modos = {s.get("modo") for s in group}
        needs_orden = any(site_catalog.requires_referral(m) for m in modos)
        href = (f'/service/{group[0]["id"]}' if len(group) == 1
                else f'/study/{cid}/{site_base._slug(nombre)}')
        turno_href = (site_listing._appointment_href(group[0]) if len(group) == 1 else href)
        px = site_availability._group_next(group)
        lugar_nombre, lugar_href, lugar_mas = _studies_place(group)
        row = {
            "nombre": nombre,
            "area": _study_group(nombre),  # dato del filtro facet, ya no es columna visible
            "sexo": site_catalog._study_sex(nombre),
            "publico": site_catalog._study_audience(group),
            "lugar": lugar_nombre,
            "lugar_href": lugar_href,
            "lugar_mas": lugar_mas,
            "orden": needs_orden,
            # honest 'Próximo turno' cell (real appointment / walk-in today /
            # the center confirms it) — NEVER 'Con turno'. Uses the group's best slot.
            "proximo": px,
            "href": href,
            "turno_href": turno_href,
            # "more times" (the next pill's secondary link, product owner 2026-07-12):
            # group of 1 → the service's info page; grouped → same
            # destination as href, suppressed on the client.
            "info_href": (f'/service/{group[0]["id"]}' if len(group) == 1 else href),
            "n_centros": len(group),
            # study popularity = sum of hours across all its venues (a study
            # offered in more centers / more hours ranks higher).
            "horas": sum(site_availability._week_hours(s) for s in group),
        }
        # Group of 1: the pill carries THAT appointment to the wizard
        # (?slot=…) and "more times" to the grid. Grouped, no — the shown
        # appointment might be from a different center than the one the
        # patient chooses.
        if len(group) == 1:
            row.update(site_listing._slot_fields(group[0], px))
        rows.append(row)
    return rows


# Loses "area" (the product owner, 2026-07-12: explicit request — the row still carries
# "area" as DATA for the facet filter, it just stops being a visible column)
# and "turno" (dead combined column, see _COL_PRESETS).
# Gains "lugar" (2026-07-12): with no WHERE, the table doesn't answer the
# question the patient came to ask ("which center do I get it done at and
# which sees me sooner?") — `lugar` says the center, or "3 centros" when
# there's a choice of where.
#
# NOT a module-level constant on purpose (same bug as `_DOCTORS_COLS` before
# it, fixed 2026-08-04 → `_centro_medicos_cols()`; this one shipped after
# that fix and froze the same way): `_cols()` resolves each label against
# `i18n.active()`, which at import time is always the canonical language —
# a frozen `_STUDIES_COLS` rendered Spanish headers on every `/d/<cluster>`
# studies table regardless of the visitor's language. Compute it per call.
def _studies_cols():
    return site_listing._cols("estudio", "lugar", "orden", "proximo")


def _studies_listing(cid, extra=None):
    rows = _studies_table_data(cid)
    opts = {
        # nav.studies ("Estudios"/"Studies") es la MISMA palabra que el ítem
        # de menú — reusar la clave en vez de un literal evita que este
        # título quede en castellano en inglés (bug real, demo, 2026-08-05:
        # "/d/diagnostico" mostraba "Estudios" arriba del buscador en inglés).
        "title": i18n.t_or("nav.studies", "Estudios", i18n.active()),
        "searchPlaceholder": i18n.t_or("page.cluster.search_study", "Buscar estudio, médico o localidad", i18n.active()),
        # search ALWAYS: listing.js only shows it on its own past >15 rows
        # and there are exactly 15 studies, so the search box this table
        # already had configured NEVER got drawn. The product owner explicitly asked to
        # be able to search the study ("people can search for the study
        # they want", 2026-07-12): here the search box doesn't depend on
        # how many rows there are.
        "search": True,
        "urlState": True,
        "filters": [
            # misma clave que la línea ~2569 (centro-medicos): un solo lugar
            # para "Área" del facet, no dos literales que puedan divergir.
            {"t": "facet", "key": "area",
             "label": i18n.t_or("listing.col.area", "Área", i18n.active())},
            {"t": "publico"},
            {"t": "sexo"},
        ],
        "rank": "popularidad",
    }
    if extra:
        opts.update(extra)
    return site_listing._listing_block("estudios-tabla", _studies_cols(), rows, opts)
