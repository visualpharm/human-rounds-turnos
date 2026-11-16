"""patient_web/site_preparation.py — Preparation and announcements of a service.

The announcements, the study preparation text, the teaser and the preparation page.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import re

from app.modules.turnos import prep_model
from app.core import i18n
from app.core import pack_l10n
from app.core import site_identity
from app.core import theme

from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_listing
from app.patient_web import site_shell


def _announcements():
    """Pack-authored notices attached to one or more services or clusters."""
    doc = site_base._load("announcements.json") or {}
    rows = doc.get("announcements", []) if isinstance(doc, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("active", True)]


def _service_announcements_html(service):
    sid = service.get("id")
    cluster = service.get("cluster")
    relevant = [row for row in _announcements()
                if sid in (row.get("service_ids") or [])
                or cluster in (row.get("cluster_ids") or [])]
    inline = service.get("announcement")
    if isinstance(inline, dict) and inline.get("active", True) and inline.get("body"):
        relevant.append(inline)
    return _announcements_html(relevant)


def _link_announcements_html(link_id):
    """Render active notices on informational destinations such as Zoonosis."""
    relevant = [row for row in _announcements()
                if link_id in (row.get("link_ids") or [])]
    return _announcements_html(relevant)


def _announcements_html(rows):
    if not rows:
        return ""
    return "".join(
        '<section class="service-announcement" role="status">'
        f'<h2>{site_base._esc(row.get("title") or i18n.t_or("service.announcement", "Aviso importante", i18n.active()))}</h2>'
        f'<p>{site_base._esc(row.get("body") or "")}</p></section>'
        for row in rows if row.get("body"))


def _prep_match(text):
    """The preparation for the study named in `text` (most specific keyword)."""
    t = prep_model.normalize(text)
    if not t:
        return None
    best, best_len = None, 0
    for e in site_catalog._preparations():
        for kw in e.get("match", []):
            k = prep_model.normalize(kw)
            if k and k in t and len(k) > best_len:
                best, best_len = e, len(k)
    return best


def _service_canonical_name(sid):
    """A service's PACK-LANGUAGE name — for matching against
    `estudios-preparacion.json`'s `match` keywords only, never for display.
    `match` is search vocabulary (pack_l10n.py never translates it), so
    matching it against the service's LOCALIZED `nombre` (English on an
    English page, since `_services()` does translate that field) would stop
    finding the study the moment a second language is enabled — the study
    would silently vanish instead of showing translated."""
    for row in site_catalog.canonical_rows("servicios"):
        if row.get("id") == sid:
            return row.get("nombre") or ""
    return ""


def _prep_body_html(e):
    """The full preparation: summary, fasting and the sections (before, what
    to bring, give notice, not needed, after). Which sections exist and what
    they're called is decided by prep_model.py, the same model the wizard,
    "Subí tu orden" and the emails use — here it's just about how it's drawn.

    `e["ayuno"]["nota"]` arrives ALREADY localized: `_preparations()` runs
    every row through `pack_l10n.localize()`, and `preparaciones.ayuno.nota`
    is one of its fields (2026-08-04 — one level of dict nesting, see
    `pack_l10n._get_field`). The one exception is a center's L5 override
    (`_prep_section_html`), which replaces `ayuno` wholesale AFTER
    localization ran — see that function for why.

    The markup itself is `prep_model.cuerpo_html` — the wizard's card draws
    the same string through prep.mjs (the product owner 26.10)."""
    return prep_model.cuerpo_html(e)


def _center_prep_overrides(center):
    """L5 center pack overrides for `estudios-preparacion.json` entries
    (data_packs.py, "Center packs (L5)"): `{id: override_dict}`, `{}` when
    `center` has no pack (the common case — no crash, no import, the site
    renders exactly like before L5 existed). `preparacion-centro.json`'s
    `overrides` list carries whole-field replacements (e.g. `ayuno`) keyed
    by the study's `id`; `_prep_section_html` shallow-merges them onto the
    matched entry, so only a center with its own pack ever sees them."""
    if not center:
        return {}
    doc = site_base._load("preparacion-centro.json", center=center) or {}
    return {o["id"]: o for o in (doc.get("overrides") or []) if o.get("id")}


def _prep_section_html(s):
    """The "Cómo prepararte" body of a service's page: the preparation that
    matches it, or the honest sentence if the catalog doesn't have one yet
    ("" — the caller decides what to put in its place). The preparation's
    NAME is only written when it says something the page's title doesn't
    already say — on Vacunatorio, a gray "Vacunación" right at the top is
    noise; on Cardiología, a "Consulta médica" warns that the instruction is
    a general consult's, not the study's.

    If the service's own center (`s["centro"]`) has an L5 pack overriding
    this study (see `_center_prep_overrides`), that center's fields win —
    every other center showing the same study keeps the generic entry."""
    prep = _prep_match(_service_canonical_name(s.get("id")) or s.get("nombre", ""))
    if not prep:
        return ""
    override = _center_prep_overrides(s.get("centro")).get(prep.get("id"))
    if override:
        prep = dict(prep, **{k: v for k, v in override.items() if k != "id"})
        # The override REPLACES `ayuno` wholesale with raw pack text (it
        # doesn't go through `_preparations()`'s localize() pass), so its
        # `nota` needs its own translate call — same cache-key convention as
        # the base entry's, best-effort: nothing sweeps a center pack's
        # overrides yet, so this only starts returning English once
        # something does (never wrong in the meantime, just Spanish).
        ay_over = prep.get("ayuno")
        if isinstance(ay_over, dict) and ay_over.get("nota"):
            from app.core import translate
            lang = i18n.active()
            pack_lang = translate.pack_language()
            if translate._primary(lang) != translate._primary(pack_lang):
                swapped = translate.cached_text(
                    pack_l10n.key("preparaciones", prep.get("id", ""), "ayuno.nota"),
                    ay_over["nota"], lang, pack_lang=pack_lang)
                if swapped != ay_over["nota"]:
                    prep["ayuno"] = dict(ay_over, nota=swapped)
    # The same guide has its own page (/preparation/<id>, every guide a
    # service matches): one link to it, so the page isn't an orphan.
    guia_html = ""
    if prep.get("id") and not prep.get("no_preparation_needed"):
        guia_html = (f'<p class="prep-guide"><a href="/preparation/{site_base._esc(prep["id"])}">'
                     f'{site_base._esc(i18n.t_or("service.prep_page.link", "Guía completa: cómo prepararte para {nombre}", i18n.active(), nombre=_prep_title(prep.get("nombre", ""))))}'
                     '</a></p>')
    nombre_html = ""
    n_prep, n_svc = prep_model.normalize(prep.get("nombre", "")), prep_model.normalize(s.get("nombre", ""))
    # "Vacunación" under the title "Vacunatorio" informs nothing: they share
    # the root. Six letters are enough to tell that apart from a case that
    # DOES need the heads-up ("Consulta médica" under "Cardiología").
    misma_raiz = bool(n_prep) and n_prep[:6] == n_svc[:6]
    if n_prep and not misma_raiz and n_prep not in n_svc and n_svc not in n_prep:
        nombre_html = f'<p class="prep-name">{site_base._esc(prep["nombre"])}</p>'
    return nombre_html + _prep_body_html(prep) + guia_html


def _prep_page_ids():
    """{prep_id: [services]} — the preparation guides that get their own
    public page (/preparation/<id>): those the active catalog actually
    offers (at least one service matches them, by the same _prep_match the
    service page uses) and that do ask for some preparation. Computed per
    call: services and the guide are both overlay-editable."""
    out = {}
    for s in site_catalog._services():
        prep = _prep_match(_service_canonical_name(s.get("id")) or s.get("nombre", ""))
        if prep and prep.get("id") and not prep.get("no_preparation_needed"):
            out.setdefault(prep["id"], []).append(s)
    return out


_PREP_PLACEHOLDER = re.compile(r"\s*\((?:[^()]*\bno especificad[oa]\b[^()]*|[^()]*\bsin especificar\b[^()]*)\)\s*$", re.I)


def _prep_title(nombre):
    """The study's name for a heading or a link: the catalog's generic
    entries carry a note for the matcher in parentheses («Ecografía (tipo no
    especificado)», «Tomografía / TAC (sin especificar contraste)») that a
    patient should not read in an H1. Real qualifiers («Perfil lipídico
    (colesterol, triglicéridos)») stay."""
    return _PREP_PLACEHOLDER.sub("", nombre or "").strip()


def _page_preparation(pid):
    """/preparation/<id>: one study's preparation guide as a page of its own,
    plus where the study is done in this installation (SEO audit 2026-10-01,
    Pinamar item 17 — «cuántas horas de ayuno para análisis de sangre»,
    «preparación para ecografía abdominal / transvaginal» had nowhere to land
    even though every matching service page already showed the guide).

    The text is ONLY estudios-preparacion.json's, drawn by the same
    _prep_body_html the service page uses, with the guide's own sources at
    the end; the page itself adds no medical statement. The "where" list is
    the same listing engine as a study's page (center, next availability,
    referral) — patient-flow-design rule 7."""
    servicios = _prep_page_ids().get(pid)
    if not servicios:
        return None
    e = next((x for x in site_catalog._preparations() if x.get("id") == pid), None)
    if not e:
        return None
    lang = i18n.active()
    city = site_identity.get("city")
    nombre = _prep_title(e.get("nombre"))
    h1 = i18n.t_or("service.prep_page.h1", "Cómo prepararte: {nombre}", lang, nombre=nombre)
    rows = [site_listing._service_center_row(s) for s in servicios]
    cols = site_listing._cols("servicio", "centro", "proximo", "orden")
    opts = {"title": i18n.t_or("service.prep_page.where", "Dónde te lo hacés en {city}", lang, city=city),
            "rowHref": "href"}
    listado = site_listing._listing_block("preparacion-servicios", cols, rows, opts, in_column=True)
    fuentes = [f for f in (e.get("sources") or []) if f.get("url")]
    fuentes_html = ""
    if fuentes:
        fuentes_html = (f'<section class="sec"><h2>{site_base._esc(i18n.t_or("service.prep_page.sources", "Fuentes", lang))}</h2>'
                        '<ul class="facts">'
                        + "".join(f'<li><a href="{site_base._esc(f["url"])}" target="_blank" rel="noopener">'
                                  f'{site_base._esc(f.get("title") or f["url"])}</a></li>' for f in fuentes)
                        + "</ul></section>")
    aviso = i18n.t_or("service.prep_page.disclaimer",
                      "Si tu orden o el servicio te indican otra cosa, seguí esa indicación.", lang)
    ay = prep_model.ayuno(e)
    desc = " ".join(x for x in (prep_model.resumen(e), ay["texto"] if ay["hay"] else "",
                                i18n.t_or("service.prep_page.desc_where", "Dónde hacerlo en {city}.", lang, city=city)) if x)
    body = f"""
  <h1 class="h1">{site_base._esc(h1)}</h1>
  <section class="sec svc-major">{_prep_body_html(e)}</section>
  <p class="svc-hours-note">{site_base._esc(aviso)}</p>
{listado}
{fuentes_html}
{site_listing._LISTING_SCRIPT}
"""
    return site_shell._shell(f"{h1} | {theme.brand_name()}", "estudios", body, desc,
                  canonical=f"/preparation/{pid}")


# Referral icon: the SAME strokes as the "Subí tu orden" button in the bar
# (nav.js:ICO_ORDEN). It's a single action across the whole site, so it
# looks the same wherever it is.
_REFERRAL_ICON = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
               '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"></path>'
               '<path d="M14 3v5h5"></path>'
               '<line x1="9" y1="13" x2="15" y2="13"></line>'
               '<line x1="9" y1="16.5" x2="13" y2="16.5"></line></svg>')


def _prep_teaser_html(s=None):
    """Entry point to /upload-order (R4, expanded 2026-07-16). It's NOT a
    guide: it's the assistant that READS the referral you upload and tells
    you (a) what it asks for, (b) which appointment it leads to — a direct
    link to the wizard with the referral already loaded — and (c) how to
    prepare. Uploading your own referral matches more precisely than the
    service's name (e.g. 'abdominal ultrasound' vs. the generic 'ultrasound'
    this page sees) and covers studies that aren't on the sign.

    **Button + one line, not a text card** (the product owner, 2026-07-25): it used to be
    a link-card with a bold title and a three-line paragraph you had to READ
    to discover there was something to click. The action shows itself
    outright: the button (same white/neutral-border/referral icon as the
    bar's — one action, one look) and next to it a single sentence about
    what it gives back. `s=None` = general entry point (cluster page: still
    choosing the study, so no ?service)."""
    if s is not None and s.get("categoria") != "estudios":
        return ""
    href = f'/upload-order?service={site_base._esc(s.get("id", ""))}' if s is not None else "/upload-order"
    # Same button, same words, everywhere it appears (nav.js's "Subí tu
    # orden" reuses this exact key — one action, one look, one translation).
    # The button alone, no sentence beside it (2026-11-15, public review:
    # the subtitle repeated what the page already says, in small grey print).
    boton = i18n.t_or("nav.upload_order", "Subí tu orden", i18n.active())
    return (f'<div class="ai-line">'
            f'<a class="ai-btn" href="{href}">{_REFERRAL_ICON}{site_base._esc(boton)}</a>'
            f'</div>')
