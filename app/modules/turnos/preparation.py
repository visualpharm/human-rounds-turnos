"""preparation.py — "Subí tu orden".

The full assistant (R4, expanded 2026-07-16): the patient uploads the
referral their doctor gave them and we tell them (a) what it asks for, (b)
WHICH APPOINTMENT it leads to — matched against the real catalog via
SanaSearch.buscar on the client, with a direct link to the appointment
wizard carrying the already-loaded referral — and (c) how to prepare. No
account needed until they decide to book the appointment (only then does
the wizard ask for login/registration).

The analysis itself (Gemini 3-pass, the same one the wizard's "Orden de
derivación" step uses) runs in booking_requests._analyze_referral — POST
/api/analyze-order, shared with service.js — so this module no longer has
its own analysis endpoint. `/api/preparacion` (1-pass, informational, saved
nothing) DIED: no clients since the assistant switched to reading via the
same endpoint that creates the `token` the wizard needs to avoid
re-uploading the file.

Native routes owned by this module through ``preparation_router.py``:
  GET /upload-order[?service=<id>]   the assistant's page (uploads the referral)
  GET /api/preparation-studies       the catalog of preparations with the
                                     admin overlay (no auth: it's the same
                                     thing the service page already shows)

`match_studies` (matching IN CODE against
estudios-preparacion.json — packs/global/, phase 2b of data packs — never
over the network) still lives
here — it's consumed both by upload-order.js (via the `ai.preparacion` that
/api/analyze-order's response already carries) and by
booking_requests._analyze_referral (lazy import, same data).

Import-safe: application startup must never fail because of this module. `ai_referral`
(for _norm) and `patient_web/site_*.py` (for _shell, _LISTING_SCRIPT and the catalog)
are imported at module level — neither of them imports this module back, so
there's no cycle.
"""

import json
from urllib.parse import quote

from app.ai import ai_referral
from app.core import ai_register, i18n
from app.core import static_fingerprint
from app.patient_web import site_base, site_catalog, site_centers, site_listing, site_shell, site_studies
from app.core import sitedata
from app.modules.turnos import prep_model
from app.core import site_identity
from app.core import theme

_SERVICIOS_PATH = sitedata.path("servicios.json")

_SERVICIOS_CACHE = None


# ---- preparation catalog ----------------------------------------------

def _studies():
    """The preparations with the admin's editable overlay on top. ONE single
    source for the whole site: patient_web/site_*.py uses it to render the service
    page and this module to match what the referral says, so a change made
    in the panel reaches both at once."""
    return site_catalog._preparations()


def _services_map():
    """id -> name from webdata/servicios.json (for match_studies's fallback
    by servicio_id). Cached; doesn't go through catalog.py (this is only
    for picking the fallback keyword, not the canonical catalog)."""
    global _SERVICIOS_CACHE
    if _SERVICIOS_CACHE is None:
        try:
            with open(_SERVICIOS_PATH, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        items = d.get("servicios", []) if isinstance(d, dict) else (d or [])
        _SERVICIOS_CACHE = {s.get("id"): s.get("nombre") for s in items if s.get("id")}
    return _SERVICIOS_CACHE


def _best_match(texto_norm):
    """The study whose most SPECIFIC (longest) keyword appears in
    `texto_norm` — so 'ecografia abdominal' wins over the generic
    'ecografia' (webdata/estudios-preparacion.json:_meta.diseno_match). For
    very short texts it also accepts the reverse sense (the whole text
    inside the keyword). None if nothing matches."""
    if not texto_norm:
        return None
    best, best_len = None, 0
    for e in _studies():
        for kw in e.get("match", []):
            k = ai_referral._norm(kw)
            if not k:
                continue
            hit = (k in texto_norm) or (len(texto_norm) <= 5 and texto_norm in k)
            if hit and len(k) > best_len:
                best, best_len = e, len(k)
    return best


def match_studies(textos, servicio_id=None):
    """[texto, ...] (what the referral says) -> (matched, unrecognized).

    `matched`: entries from estudios-preparacion.json, in the order of the
    first text that matched them, no duplicates. `unrecognized`: the texts
    (as they came in) that didn't match any study. If nothing matched and
    `servicio_id` resolves in webdata/servicios.json, retries against the
    service's name (fallback) — that name isn't added to `unrecognized` (it's
    not text from the referral, it's the page's own service acting as a
    backup)."""
    matched, no_reconocidos, seen = [], [], set()
    for texto in (textos or []):
        texto = str(texto or "").strip()
        if not texto:
            continue
        e = _best_match(ai_referral._norm(texto))
        if e:
            if e["id"] not in seen:
                matched.append(e)
                seen.add(e["id"])
        else:
            no_reconocidos.append(texto)

    if not matched and servicio_id:
        nombre = _services_map().get(servicio_id)
        if nombre:
            e = _best_match(ai_referral._norm(nombre))
            if e:
                matched.append(e)

    return _merge_identical(matched), no_reconocidos


def _signature(e):
    """What the patient REALLY reads off an entry. Two studies with the same
    signature produce two identical cards. The keys are the ones
    prep_model.py renders: if a new section shows up, it goes here too, or
    two different preparations will merge into a single card."""
    firma = {"ayuno": e.get("ayuno") or {}, "resumen": e.get("resumen") or "",
             "antes": prep_model.antes(e)}
    for clave, _titulo, _tono in prep_model.SECCIONES:
        if clave != "antes":
            firma[clave] = e.get(clave) or []
    return json.dumps(firma, sort_keys=True, ensure_ascii=False)


def _and_join(nombres):
    """Joins study names for a merged card's title. `nombres` come from
    `_studies()`, already localized — currently no two entries in the real
    pack share a signature (so this never fires in production yet), but the
    joiner word itself must still track the active language rather than
    hardcoding Spanish, or a future match would read half-English,
    half-Spanish ("Electrocardiogram y Ecocardiograma")."""
    if len(nombres) == 1:
        return nombres[0]
    joiner = i18n.t_or("booking.prep.and_joiner", " y ", i18n.active())
    return ", ".join(nombres[:-1]) + joiner + nombres[-1]


def _merge_identical(matched):
    """Two studies that prepare EXACTLY the same = a single card, with both
    names in the title ("Electrocardiograma y Ecocardiograma"). Seeing the
    same card twice reads as a bug, not as information."""
    grupos = {}
    orden = []
    for e in matched:
        f = _signature(e)
        if f not in grupos:
            grupos[f] = []
            orden.append(f)
        grupos[f].append(e)
    out = []
    for f in orden:
        grupo = grupos[f]
        e = dict(grupo[0])
        if len(grupo) > 1:
            e["nombre"] = _and_join([g.get("nombre") or "" for g in grupo])
            e["ids"] = [g.get("id") for g in grupo]
        out.append(e)
    return out


def _care_href(matched, servicio_id=None):
    """Public route to go from preparation to where to be seen.

    If the tool was opened from a service, it goes back to that service.
    From the general entry point, lab work groups under the word that
    returns the walk-in centers; everything else uses the first recognized
    study's name.
    """
    if servicio_id:
        return f"/service/{quote(servicio_id)}"
    if not matched:
        return None
    grupos = {site_studies._study_group(e.get("nombre")) for e in matched}
    q = "laboratorio" if "Laboratorio" in grupos else (matched[0].get("nombre") or "")
    return "/?q=" + quote(q) if q else None


# ---- GET /upload-order (the assistant's page) --------------------------------

def _contact_html(centro):
    """Same pattern as site_page_service._page_service: ONE WhatsApp or phone number
    for the center, never made up. With no ?servicio (general entry point)
    we use the site's main center's phone (site_identity.get("main_center"))
    — we don't invent a new contact."""
    c = centro or site_centers._centers_by_slug().get(site_identity.get("main_center")) or {}
    # One helper for every «¿Te trabás?» line (same wording as the booking
    # wizard's own escape hatch); the installation's help line wins.
    return site_centers.help_contact_html(
        c, i18n.t_or("upload.contact.hospital_fallback", "el hospital", i18n.active()))


def _upload_order_page(qs):
    lang = i18n.active()
    servicio_id = (qs.get("service", [""])[0] or qs.get("servicio", [""])[0] or "").strip()
    servicio = site_catalog._service_by_id(servicio_id) if servicio_id else None
    centro = None
    if servicio:
        centro = site_centers._centers_by_slug().get(servicio.get("centro")) or {}

    if servicio:
        subtitle = ""
    else:
        # General page (no ?servicio): the assistant's instructions ARE the
        # subtitle — not the "Hospital de Pinamar y los CAPS" line (responsable del producto,
        # 2026-07-17). With ?servicio, "servicio · centro" context is kept.
        subtitle = i18n.t_or(
            "upload.page.subtitle",
            "La leemos y te decimos qué turno sacar y cómo prepararte.",
            lang)

    breadcrumb = ""
    if servicio:
        bits = []
        if centro and centro.get("name"):
            centro_nombre = site_base._esc(centro["name"])
            bits.append(
                f'<a href="/center/{site_base._esc(centro["slug"])}">{centro_nombre}</a>'
                if centro.get("slug") else centro_nombre
            )
        if servicio.get("nombre"):
            bits.append(
                f'<a href="/service/{site_base._esc(servicio["id"])}">'
                f'{site_base._esc(servicio["nombre"])}</a>'
            )
        sep = ' <span class="svc-crumb-sep">›</span> '
        breadcrumb = (
            '<nav class="svc-crumb" aria-label="'
            + site_base._esc(i18n.t_or("page.breadcrumb", "Ubicación", lang)) + '">'
            + sep.join(bits) + "</nav>"
        )

    sub_hidden = " hidden" if not subtitle else ""
    contacto = _contact_html(centro)

    # AI down (daily cap exhausted / OpenRouter down): this assistant has NO
    # function without AI (it reads the referral and tells you which
    # appointment to book). Instead of an uploader that's going to fail, we
    # show that it's unavailable and send them to the flow that DOES work:
    # book the appointment by uploading the referral, which the hospital
    # reviews.
    try:
        from app.ai import ai_health
        ia_ok = ai_health.available()
    except Exception:  # noqa: BLE001
        ia_ok = True
    h1 = i18n.t_or("page.wizard.upload_order", "Subí tu orden", lang)
    if not ia_ok:
        banner = i18n.t_or("upload.ai_down.banner",
                            "El lector automático de órdenes no está disponible en este momento.", lang)
        cuerpo = i18n.t_or(
            "upload.ai_down.body",
            "Podés sacar el turno igual: elegí el servicio, subí la orden en el paso "
            "correspondiente y el hospital la revisa. No hace falta esperar.", lang)
        cta = i18n.t_or("upload.ai_down.cta", "Ver servicios y sacar turno", lang)
        body = f"""
  {breadcrumb}
  <h1 class="h1" id="prep-title">{site_base._esc(h1)}</h1>
  <p class="svc-sub" id="prep-sub"{sub_hidden}>{subtitle}</p>
  <div class="sec">
    <div class="wz-soft" style="color:var(--accent)">{site_base._esc(banner)}</div>
    <p style="margin:14px 0 0;font-size:15px;color:var(--text-2);line-height:1.5;">
      {site_base._esc(cuerpo)}</p>
    <p style="margin:16px 0 0;"><a class="lnk" href="/">{site_base._esc(cta)}</a></p>
  </div>
"""
        return site_shell._shell(
            f"{h1} | {theme.brand_name()}", "orden", body,
            i18n.t_or("upload.ai_down.meta_desc",
                      "El lector de órdenes no está disponible ahora. Podés sacar el turno subiendo la orden.",
                      lang),
            chrome="full", contacto=contacto, index=False)

    data = site_base._json_inline({
        "servicio_id": servicio_id or None,
        "servicio_nombre": (servicio or {}).get("nombre"),
    })

    # Analysis (POST /api/analyze-order, booking_requests.py) + matching the
    # appointment (SanaSearch.buscar over /api/services) run client-side —
    # the same scripts as the homepage (buscar.js + listado.js) to render
    # the "which appointment this leads to" table with the SAME look as the
    # rest of the site.
    body = f"""
  {breadcrumb}
  <h1 class="h1" id="prep-title">{site_base._esc(h1)}</h1>
  <p class="svc-sub" id="prep-sub"{sub_hidden}>{subtitle}</p>
  <!-- Reserved height: upload-order.js draws the step after the first
       paint, and without this the whole body jumps when it appears.
       `:empty` clears it as soon as there's content (see .lst-slot in
       patient_web/site_*.py). -->
  <style>#prep-host:empty{{min-height:420px}}</style>
  <p data-ai-notice="order" style="font-size:16px;color:var(--text);">{site_base._esc(ai_register.notice("ai.notice.order"))}</p>
  <div id="prep-host"></div>
  <script id="prep-data" type="application/json">{data}</script>
  <script type="module" src="{static_fingerprint.url('platform/photo-zoom.mjs')}"></script>
  {site_listing._LISTING_SCRIPT}
  <script src="/vendor/lottie/lottie_light.min.js?v=1" defer></script>
  {site_listing._turnos_controller_scripts("upload-order.mjs", host_id="prep-host", capture_upload=True)}
"""
    return site_shell._shell(
        f"{h1} | {theme.brand_name()}", "orden", body,
        i18n.t_or("upload.page.meta_desc",
                  "Subí la orden que te dio tu médico: te decimos qué turno sacar y cómo prepararte.",
                  lang),
        chrome="full", contacto=contacto, index=False)
