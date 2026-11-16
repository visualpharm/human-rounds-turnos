"""patient_web/site_listing.py — Listings of the public site.

Column presets, listing rows, enriched services and doctors, the directory and walk-in cards, crawlable links, the booting scripts and the per-page search examples.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import json
import re
import urllib.parse

from app.core import format
from app.core import i18n
from app.core import static_fingerprint

from app.patient_web import site_availability
from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_centers
from app.patient_web import site_doctors
from app.patient_web import site_schedule


def _walkin_card(cl):
    """Directory entry of a walk-in cluster: no professional is promised there,
    so the card carries one description (`desc`) instead of faces."""
    svs = site_catalog._walkin_services()
    if not svs:
        return None
    # One paragraph says what the card is for (the product owner, 2026-11-13:
    # one description, not a subtitle plus a list of centers): the pack's own
    # sentence, shared with the cluster page.
    return {
        "id": cl["id"], "nombre": cl.get("nombre", cl["id"]), "top": cl.get("top"),
        "n_medicos": 0, "n_servicios": len(svs), "avatares": [],
        "walkin": True,
        "icon": cl.get("icon", ""), "pinned": cl.get("pinned"),
        "catch_all": bool(cl.get("catch_all")),
        "desc": site_catalog._walkin_description(
            cl, "directory.walkin_sub", "Sin turno, por llegada"),
    }


def _directory_data():
    """Directory model: clusters with counts + avatars (for the cards).
    `pinned` (listed first under «Más especialidades», ascending) and
    `catch_all` (listed last) travel as the pack set them."""
    meds, svs = site_catalog._professionals(), site_catalog._services()
    contexto = site_doctors._cluster_doctors_context()
    out = []
    for cl in site_catalog._clusters():
        cid = cl["id"]
        if cl.get("kind") == site_catalog._WALKIN_KIND:
            card = _walkin_card(cl)
            if card:
                out.append(card)
            continue
        # the doctors /d/<cid> lists — the card's «N médicos» is that list
        # (demo review 2026-11-15, D12/P18)
        cm = site_doctors._cluster_doctors(cid, contexto)[0]
        cs = [s for s in svs if s.get("cluster") == cid]
        if not cm and not cs:
            continue
        # Real portraits only here — doctors without a photo are summed up in
        # the client-side "+N", never as an initials circle (the product owner, 2026-07-11).
        # Resolved ONCE per doctor (it used to be called twice per face, and
        # only the first 6 of each cluster are ever drawn).
        avatares = []
        for m in cm:
            if len(avatares) == 6:
                break
            ph = site_doctors._photo_style(m["id"], chico=True) if m.get("id") else ""
            if ph:
                avatares.append({"ph": ph})
        out.append({
            "id": cid, "nombre": cl.get("nombre", cid), "top": cl.get("top"),
            "n_medicos": len(cm), "n_servicios": len(cs), "avatares": avatares,
            "icon": cl.get("icon", ""), "pinned": cl.get("pinned"),
            "catch_all": bool(cl.get("catch_all")),
        })
    return {"clusters": out, "total_medicos": len(meds)}


# ---------------------------------------------------------------- listing.js:
# canonical column registry (Python mirror of listing.js's registry — same
# order/labels/types). _cols(...claves) builds a table's column list from
# these keys, ALWAYS in the canonical entity-first order (the product owner, 2026-07-12 —
# revokes the separate "Turno" column): entity (Servicio/Estudio/Médico) ·
# context (Especialidad/Área) · Centro/lugar · Orden · Próxima disponibilidad
# — regardless of the order the keys are requested in. The "proximo" pill IS
# the button when there's a bookable slot (direct link + secondary "more
# times" link); walk-in is plain green text, never clickable. `overrides`
# lets a single table make a one-off tweak (e.g. hiding "centro" on mobile
# only in the cluster's doctors table).
_COL_PRESETS = {
    "servicio":     {"t": "link", "key": "nombre", "href": "href", "label": "Servicio"},
    "estudio":      {"t": "link", "key": "nombre", "href": "href", "label": "Estudio"},
    "medico":       {"t": "link", "key": "nombre", "href": "href", "label": "Médico", "avatar": True},
    "especialidad": {"t": "link", "key": "especialidad", "href": "esp_href", "label": "Especialidad", "hideSm": True, "sub": True},
    "area":         {"t": "text", "key": "area", "label": "Área", "hideSm": True, "sub": True},
    "centro":       {"t": "link", "key": "centro", "href": "centro_href", "label": "Centro", "sub": True},
    # Studies (2026-07-18): link to the center with the most hours + "+N" for
    # the rest (popover "Also available at") — same language as
    # Especialidad/Centro in Doctors, no longer plain text or "3 centers".
    "lugar":        {"t": "lugar", "key": "lugar", "href": "lugar_href", "extra": "lugar_mas", "label": "Centro", "col_key": "centro", "sub": True},
    "estado":       {"t": "estado", "key": "estado", "label": "Estado"},
    "proximo":      {"t": "proximo", "key": "proximo", "href": "turno_href", "more": "info_href", "label": "Próxima disponibilidad"},
    "orden":        {"t": "orden", "key": "orden", "label": "Orden"},
}
_COL_ORDER = ["servicio", "estudio", "medico", "especialidad", "area",
              "centro", "lugar", "estado", "orden", "proximo"]


def _cols(*claves, **overrides):
    """The requested columns, in canonical order, with their headers resolved
    against the string catalog for THIS request's language — the same
    `listing.col.*` keys listing.js uses when it builds a table client-side,
    so a header reads the same whichever side drew it."""
    out = []
    for name in _COL_ORDER:
        if name not in claves:
            continue
        col = dict(_COL_PRESETS[name])
        col.update(overrides.get(name) or {})
        if "label" in col:
            # `lugar` is the studies table's Centro column under another
            # preset name — it borrows centro's catalog key via `col_key`
            # rather than duplicating the string.
            col["label"] = i18n.t_or(
                f'listing.col.{col.pop("col_key", name)}', col["label"],
                i18n.active())
        out.append(col)
    return out


def _appointment_href(s):
    """Destination of a row's/CTA's ACTION. Request-based services go
    STRAIGHT to the wizard (/service/<id>/book — the product owner 2026-07-12: clicking
    "Electrocardiograma" = upload the referral right away, no toll page);
    walk-ins go to their info page (/service/<id>, there's no appointment to
    book) — except the walk-in that opens its same-day listing, which does
    have an appointment to book."""
    if s.get("modo") in ("solicitud_con_orden", "solicitud") or site_availability._opens_same_day(s):
        return f'/service/{s["id"]}/book'
    return f'/service/{s["id"]}'


def _slot_fields(s, px, medico_id=""):
    """Extra pill fields when the shown appointment is bookable: `slot_href`
    carries THAT appointment to the wizard (?slot=YYYY-MM-DDTHH:MM — clicking
    the time = the time is chosen, the wizard jumps straight to the next
    step) and `info_href` goes to the wizard's time grid ("more times" = see
    the times, not the info page). The product owner, 2026-07-14: "show the thing I
    already chose... only show the selection of times if I choose to see
    more times". With no real appointment (walk-in / to-confirm) it adds
    nothing."""
    if (s and px.get("kind") == "turno" and px.get("dia") and px.get("hora")
            and (s.get("modo") in ("solicitud_con_orden", "solicitud")
                 or site_availability._opens_same_day(s))):
        base = _appointment_href(s)
        medico_q = (f'&medico={urllib.parse.quote(str(medico_id))}'
                    if medico_id else "")
        info_q = (f'?slots=1&medico={urllib.parse.quote(str(medico_id))}'
                  if medico_id else "")
        return {"slot_href": f'{base}?slot={px["dia"]}T{px["hora"]}{medico_q}',
                "info_href": base + info_q}
    return {}


def _service_center_row(s):
    """'Where to be seen'/'where to get it done' row — the row CHOOSES the
    center. The center links to its own page (/center/<slug>, never straight
    to booking — the product owner, 2026-07-11: the center's name USED TO BE the action,
    now it informs first); the action lives in the Next-availability
    pill-button ("Book" / green "Opens..." text for walk-in) + the whole row
    via rowHref.
    The venue is appended to the center's display ("CAPS Ostende, Centro
    Cultural" — same render as /service/<id>'s "Where"): here the center is
    ALREADY a column and without the venue two rows of the same service look
    indistinguishable; a separate "lugar" column would duplicate the center
    in every other row."""
    c = site_centers._centers_by_slug().get(s.get("centro")) or {}
    centro_txt = c.get("name") or s.get("centro") or ""
    if s.get("sede"):
        centro_txt = f'{centro_txt}, {s["sede"]}' if centro_txt else s["sede"]
    px = site_availability._service_next(s)
    row = {
        "proximo": px,
        "nombre": s.get("nombre") or "",
        "centro": centro_txt,
        "centro_href": f'/center/{c["slug"]}' if c.get("slug") else "",
        # The entity's name opens its info. The appointment action has its
        # own destination and is never reused as the name's link.
        "href": f'/service/{s["id"]}',
        "turno_href": _appointment_href(s),
        "info_href": f'/service/{s["id"]}',
        "orden": site_catalog.requires_referral(s.get("modo")),
        "horas": site_availability._week_hours(s),  # popularity for listing.js's ranking
    }
    row.update(_slot_fields(s, px))
    return row


# Cluster ids that pin an audience on their own. Read BEFORE the specialty
# text because a cluster id is a wire value: it survives translation, while
# "Ginecología" becomes "Gynecology" and stops matching the Spanish stem
# below (2026-08-04, when pack content became translatable).
_CLUSTER_AUDIENCE = {
    "gineco": ("todos", "F"),
    "pediatria": ("ninos", None),
}


def _doctor_audience_sex(especialidad, cluster=None):
    """Derives `publico`/`sexo` from the specialty for /doctors' filters
    (2026-07-18: Doctors gains the same filters as Studies). CONSERVATIVE
    mapping — only restricts when the specialty makes it crystal clear; when
    in doubt, no restriction (we never hide a doctor from anyone by mistake):
    gynecology/obstetrics → sex F (they see women, not that the professional
    is one); pediatrics/"child" → children audience; the rest, all ages and
    both sexes."""
    pinned = _CLUSTER_AUDIENCE.get(str(cluster or "").strip())
    if pinned:
        return pinned
    e = (especialidad or "").lower()
    sexo = "F" if any(x in e for x in ("ginecol", "obstetr", "obstétr")) else None
    publico = "ninos" if ("pediatr" in e or "infantil" in e) else "todos"
    return publico, sexo


def _doctor_listing_row(m, servicio=None):
    """A professional's row: their own next appointment + name (with avatar)
    + their specialty + center; the link books an appointment where they see
    patients. The specialty links to its page (/d/<cluster>: same specialty
    across every center) and the center to its own (/center/<slug>) — no row
    link except the name/rowHref goes straight to booking. `servicio` builds
    the row for one of the doctor's several tagged services (its next time
    and its booking link) instead of the doctor's main one."""
    c = site_centers._centers_by_slug().get(m.get("centro")) or {}
    cl = site_catalog._cluster_by_id(m.get("cluster")) or {}
    # The row's link goes to the doctor's own page (/doctor/<id>), not to the
    # general service — from there the patient books where they see patients.
    href = f'/doctor/{m.get("id", "")}'
    # Two specialties restrict the audience only where they agree: a doctor
    # of Pediatría + Clínica is still for adults (never hide a doctor by mistake).
    pares = [(m.get("especialidad") or cl.get("nombre") or "", m.get("cluster"))]
    if m.get("especialidad_2"):
        pares.append((m["especialidad_2"], m.get("cluster_2")))
    audiencias = [_doctor_audience_sex(e, c) for e, c in pares]
    publico = audiencias[0][0] if all(a[0] == audiencias[0][0] for a in audiencias) else "todos"
    sexo = audiencias[0][1] if all(a[1] == audiencias[0][1] for a in audiencias) else None
    servicio = servicio or site_doctors._doctor_service(m)
    turno_href = href
    if servicio:
        turno_href = _appointment_href(servicio)
        if m.get("id") and turno_href != href:
            turno_href += f'?medico={urllib.parse.quote(str(m["id"]))}'
    row = {
        # the panel keys a doctor by id (Solicitudes' doctor filter)
        "id": m.get("id") or "",
        "proximo": site_availability._doctor_next(m, servicio),
        "nombre": m.get("nombre") or "",
        "href": href,
        "turno_href": turno_href,
        "info_href": href,
        # both specialties read together; the Especialidad filter matches either
        # one through `especialidad_todas` (listing.mjs), the link is the first's page
        # + what the row books when it is another area's («Cardiólogo · Ergometría»)
        "especialidad": site_doctors._doctor_row_specialty(m, servicio),
        # the generic title (not «Cardióloga»): one filter option per specialty
        "especialidad_todas": site_doctors._doctor_specialty_labels(m, gendered=False),
        "esp_href": f'/d/{m["cluster"]}' if m.get("cluster") else "",
        "centro": c.get("name") or "",
        "centro_href": f'/center/{c["slug"]}' if c.get("slug") else "",
        "demo": bool(m.get("demo")),
        # every service on the doctor's schedule is a search term too:
        # "electrocardiograma" finds the cardiologist who also does it
        "palabras": (m.get("palabras") or []) + [t["nombre"] for t in site_doctors._doctor_tagged_services(m)
                                                  if t.get("nombre")],
        "publico": publico,
        "sexo": sexo,
        # None when it can't be determined (the product owner, 2026-07-12): the cell stays
        # empty instead of lying "No referral" — see _doctor_referral.
        "orden": site_doctors._doctor_referral(m),
        "horas": site_schedule._week_hours_doctor(m),
    }
    # Walk-in: there's no appointment to book — the Next-availability pill
    # says so in plain green text ("Opens..." + "by order of arrival").
    # Bookable with a concrete appointment: same pair as the service row —
    # the pill IS the button (slot_href → wizard with ?slot=) and info_href
    # goes to the grid, so "More times" appears (before, href and info_href
    # were both /doctor/<id> → identical → moreLink suppressed it; product owner,
    # 2026-07-17).
    row.update(_slot_fields(servicio, row["proximo"], m.get("id") or ""))
    row.update(site_doctors._doctor_avatar_fields(m))
    return row


def _soonest_first(rows):
    """Rows in the order a patient reads them: soonest availability first
    (`proximo.sort`: a real slot by date, then walk-in, then «the center
    confirms it»), the incoming order (by name) within the same value."""
    return sorted(rows, key=lambda r: str((r.get("proximo") or {}).get("sort") or "9"))


# Shared columns of the center's doctors table, in canonical entity-first
# order: doctor with portrait | specialty (link to /d/…) | Referral
# (with/without/null) | next availability (pill-button or green walk-in
# text); the whole row goes to the doctor's page (rowHref).
#
# NOT a module-level constant on purpose (bug fixed 2026-08-04): `_cols()`
# resolves each label against `i18n.active()`, which at import time is
# always the canonical language (no request bound yet) — a frozen
# `_DOCTORS_COLS` would render Spanish headers on this table forever,
# regardless of the visitor's actual language, while every other table on
# the same page (built via a fresh `_cols()` call inside the request) got it
# right. Compute it per call so it sees the request's real language.
def _centro_medicos_cols():
    return _cols("medico", "especialidad", "orden", "proximo")


def _service_area_row(s):
    """A service's row on a center's page: service (link) + area (cluster) +
    appointment/next + referral; with audience/sex for the filters. `lugar` =
    the venue if the service has one (e.g. 'Centro Cultural' in Ostende,
    where two 'Medicina general' rows are told apart by where), otherwise the
    center."""
    c = site_centers._centers_by_slug().get(s.get("centro")) or {}
    cl = site_catalog._cluster_by_id(s.get("cluster")) or {}
    px = site_availability._service_next(s)
    row = {
        "proximo": px,
        "nombre": s.get("nombre") or "",
        "href": f'/service/{s["id"]}',
        "turno_href": _appointment_href(s),
        "info_href": f'/service/{s["id"]}',
        "area": cl.get("nombre") or "",
        "lugar": s.get("sede") or c.get("name") or "",
        "orden": site_catalog.requires_referral(s.get("modo")),
        "horas": site_availability._week_hours(s),  # popularity for listing.js's ranking
        "publico": site_catalog._study_audience([s]),
        "sexo": site_catalog._study_sex(s.get("nombre")),
        "palabras": list(s.get("palabras") or []) +
                    (["Medicina general"]
                     if site_base._norm_name(s.get("nombre")) == "clinica medica" else []),
    }
    row.update(_slot_fields(s, px))
    return row


def _listing_block(mount_id, cols, rows, opts=None, in_column=False):
    """Div + JSON for a listing.js (the <script src> is added once per page).

    `in_column`: the listing is one more block of a single item's page (a
    service, a study, a preparation) and keeps the page column's left edge;
    only a catalog's own listing breaks out to the viewport on desktop
    (listing.mjs, `.lst-in-column`). From the product owner's demo check 07.11: «Quién atiende» began
    at x≈30 under a column starting at x≈230."""
    payload = {"cols": cols, "rows": rows, "options": opts or {}}
    # Approximate height of what listing.js is about to draw, so the layout
    # doesn't jump when it appears (see .lst-slot:empty in _PAGE_CSS). The
    # per-row height came from measuring /doctors: ~109px stacked (mobile)
    # and ~63px in table; the fixed addend is the header + the filters. It's
    # a RESERVE, not an exact measurement: `:empty` clears it as soon as
    # there's a table.
    n = len(rows)
    reserva = f"--lst-reserva:{142 + n * 109}px;--lst-reserva-d:{58 + n * 63}px" if n else ""
    links = _listing_links(rows)
    cls = "lst-slot lst-slot--ssr" if links else "lst-slot"
    slot = (f'<div class="{cls}" style="{reserva}">{links}</div>' if reserva
            else f'<div class="{cls}">{links}</div>')
    sec = "sec lst-in-column" if in_column else "sec"
    return (f'<div class="{sec}" data-listado id="{site_base._esc(mount_id)}">{slot}'
            f'<script type="application/json" class="lst-data">{site_base._json_inline(payload)}</script></div>')


def _crawlable_href(href):
    """A link worth printing for a visitor/crawler without JS: an own page,
    never a booking step (/book is closed in robots.txt) or a JS anchor."""
    href = str(href or "")
    return href.startswith("/") and "/book" not in href


def _listing_links(rows):
    """The listing's rows as a plain list of real links, painted INSIDE the
    slot listing.mjs empties when it mounts (`mount.innerHTML = ""`), so with
    JS the table looks exactly as before. Without JS — an AI-search bot,
    Google's first pass, a visitor whose module failed — the page still
    links every doctor/service/study and its specialty and center (SEO audit
    2026-10-01, Pinamar item 1: /doctors, /centers, /d/* and /center/* had
    their links only inside the JSON block)."""
    items = []
    for r in rows:
        nombre, href = r.get("nombre"), r.get("href")
        if not nombre:
            continue
        partes = [f'<a href="{site_base._esc(href)}">{site_base._esc(nombre)}</a>' if _crawlable_href(href)
                  else site_base._esc(nombre)]
        for texto, enlace in ((r.get("especialidad"), r.get("esp_href")),
                              (r.get("centro") or r.get("lugar"),
                               r.get("centro_href") or r.get("lugar_href"))):
            if texto and _crawlable_href(enlace):
                partes.append(f'<a href="{site_base._esc(enlace)}">{site_base._esc(texto)}</a>')
            elif texto:
                partes.append(site_base._esc(texto))
        items.append("<li>" + " · ".join(partes) + "</li>")
    return f'<ul class="lst-ssr">{"".join(items)}</ul>' if items else ""


def _tile_links(tiles):
    """Same for a tiles.mjs mosaic (render() replaces the host's innerHTML):
    one real link per tile that has a page of its own."""
    items = []
    for t in tiles:
        nombre = t.get("nombre")
        href = t.get("href") if "href" in t else (f'/center/{t["slug"]}' if t.get("slug") else "")
        if nombre and _crawlable_href(href):
            items.append(f'<li><a href="{site_base._esc(href)}">{site_base._esc(nombre)}</a></li>')
    return f'<ul class="lst-ssr">{"".join(items)}</ul>' if items else ""


_LISTING_SCRIPT = (f'<script type="module" src="{static_fingerprint.url("platform/listing.mjs")}"></script>')


_TURNOS_BOOT_JS = """
  var hostId = cfg.hostId;
  var url = cfg.url;
  var captureUpload = cfg.captureUpload;
  var loadError = cfg.loadError;
  var pendingFile = null;
  var captureInstalled = false;
  function hostEl() { return document.getElementById(hostId); }
  function showLoadError() {
    var host = hostEl();
    if (!host) return;
    if (host.classList.contains("svc-form-host--schedule-boot")) {
      host.classList.remove("svc-form-host--schedule-boot");
      host.classList.add("svc-form-host--schedule-recovery");
    }
    host.innerHTML = '<div class="sec"><p class="svc-sub">' + loadError + "</p></div>";
  }
  function keep(file) { if (file) pendingFile = file; }
  function onPaste(e) {
    var items = (e.clipboardData && e.clipboardData.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind === "file" && /^image\\//.test(items[i].type || "")) {
        var f = items[i].getAsFile();
        if (f) { e.preventDefault(); keep(f); return; }
      }
    }
  }
  function onChange(e) {
    var t = e.target;
    if (t && t.id === "wz-file" && t.files && t.files[0]) keep(t.files[0]);
  }
  function onZoneClick(e) {
    var zone = e.target && e.target.closest && e.target.closest("#wz-upzone");
    if (!zone) return;
    if (zone._turnosKeyboardUntil && Date.now() < zone._turnosKeyboardUntil) {
      e.preventDefault();
      return;
    }
    var fileEl = document.getElementById("wz-file");
    if (fileEl) fileEl.click();
  }
  function onZoneKeydown(e) {
    var zone = e.target && e.target.closest && e.target.closest("#wz-upzone");
    if (!zone || (e.key !== "Enter" && e.key !== " ")) return;
    var fileEl = document.getElementById("wz-file");
    if (!fileEl) return;
    e.preventDefault();
    // A role=button div may synthesize click after Enter/Space. The click
    // listener is still required for pointer input, but must not open the
    // chooser a second time for this keyboard activation.
    zone._turnosKeyboardUntil = Date.now() + 120;
    fileEl.click();
  }
  function uninstallCapture() {
    if (!captureInstalled) return;
    document.removeEventListener("paste", onPaste);
    document.removeEventListener("change", onChange, true);
    document.removeEventListener("click", onZoneClick);
    document.removeEventListener("keydown", onZoneKeydown);
    captureInstalled = false;
  }
  // The native controller waits for its own dependencies before it mounts an
  // upload control. Keep this first-paint bridge active until that exact
  // handoff, so a paste or chooser result in the post-import gap is retained.
  function takePendingFile() {
    var file = pendingFile;
    pendingFile = null;
    uninstallCapture();
    return file;
  }
  function uploadUnavailable() {
    pendingFile = null;
    uninstallCapture();
  }
  if (captureUpload) {
    document.addEventListener("paste", onPaste);
    document.addEventListener("change", onChange, true);
    document.addEventListener("click", onZoneClick);
    document.addEventListener("keydown", onZoneKeydown);
    captureInstalled = true;
  }
  if (!url) { uploadUnavailable(); showLoadError(); return; }
  import(url).then(function (m) {
    return m.boot({ pendingFile: pendingFile, takePendingFile: takePendingFile,
      uploadUnavailable: uploadUnavailable });
  }).catch(function () { uploadUnavailable(); showLoadError(); });
"""


def _turnos_controller_scripts(controller, *, host_id, capture_upload=False):
    """Fingerprinted native boot for a modules/turnos controller.

    The page owns rejected-import recovery (and optional early paste/click
    capture). The controller never auto-boots; this script calls boot() once.

    The pack's phone shape is emitted HERE, not by each calling page: every
    turnos controller transitively imports platform/format.mjs, which throws
    at module load without `window.SANA_COUNTRY_PHONE` (format.mjs:85). Two
    of the three service renderers used to omit it, so format.mjs threw on
    every `/service/<id>` info page in production while the wizard — the one
    page that happened to emit it — worked. Emitting it from the single
    helper that mounts a controller is what makes that class of defect
    unrepeatable; the assignment precedes the import below, which is the
    ordering format.mjs requires.
    """
    controller_url = static_fingerprint.module_url("modules/turnos/" + controller)
    upload_url = static_fingerprint.module_url("modules/turnos/wz-upload.mjs")
    load_error = i18n.t_or(
        "upload.result.load_error",
        "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.",
        i18n.active(),
    )
    import_map = (
        '<script type="importmap">'
        + json.dumps({"imports": {
            "/static/modules/turnos/wz-upload.mjs": upload_url,
        }}, separators=(",", ":"))
        + "</script>\n"
    )
    cfg = json.dumps({
        "hostId": host_id,
        "url": controller_url,
        "captureUpload": bool(capture_upload),
        "loadError": load_error,
    }, separators=(",", ":"))
    return (
        "<script>window.SANA_COUNTRY_PHONE=" + format.country_phone_json() + ";</script>\n"
        + import_map
        + "<script>\n(function () {\n  var cfg = " + cfg + ";"
        + _TURNOS_BOOT_JS
        + "})();\n</script>"
    )


def _week_markup(horario, titulo):
    """Schedule host plus the small, server-localized native-load recovery.

    Week remains the only renderer. If its manifest import is rejected, the
    shell reveals a concise reload path instead of reviving a classic/global
    implementation and silently hiding the schedule.
    """
    lang = i18n.active()
    unavailable = i18n.t_or("date.schedule_unavailable", "No pudimos cargar los horarios.", lang)
    reload_label = i18n.t_or("date.reload", "Reintentar", lang)
    # The schedule as plain text, inside the host week.mjs repaints (its
    # render() replaces innerHTML only when the string parses): a crawler
    # that runs no script — and a visitor whose module never loads — reads
    # «Lunes a viernes de 8 a 13 h» instead of an empty box (SEO audit
    # 2026-10-01, Pinamar item 9). Same <h2 class="sem-t"> week.mjs draws,
    # so the title doesn't jump. Only a string with an hour in it: walk-in
    # prose ("No necesitás turno…") is already printed by the page itself.
    texto = ""
    if re.search(r"\d", str(horario or "")):
        texto = ((f'<h2 class="sem-t">{site_base._esc(titulo)}</h2>' if titulo else "")
                 + f'<p class="sem-txt">{site_base._esc(site_schedule._horario_display(horario))}</p>')
    return (f'<div data-semana data-titulo="{site_base._esc(titulo)}" data-horario="{site_base._esc(horario)}">'
            f'{texto}'
            f'<p data-semana-error hidden role="alert">{site_base._esc(unavailable)} '
            f'<a href="">{site_base._esc(reload_label)}</a></p></div>')


_WEEK_SCRIPT = """<script type="module">
const weekUrl = window.SANA_STATIC && window.SANA_STATIC["platform/week.mjs"];
const showWeekError = () => document.querySelectorAll("[data-semana-error]").forEach((message) => { message.hidden = false; });
if (!weekUrl) showWeekError();
else import(weekUrl).catch(showWeekError);
</script>"""


# Choose by WAIT, not by distance (the product owner, 2026-07-12: "they are all ten
# minutes from one another, so it's better to drive a bit than to wait a
# couple of weeks"). The district's centers are all ~10 min apart, so the
# criterion that serves the patient is WHEN they're seen, not which one is
# closest. The table already sorts by next availability (listing.js sorts by
# the `proximo` column by default) — this line explains WHY that's the order.
# patient-flow-design rule 7: lead with the fastest, without hiding the
# closest (they're all listed, and the Centro column links to each one).
_CHOOSE_BY_WAIT = (
    '<p class="elegir-espera">Los centros están a unos 10 minutos entre sí. '
    'Primero va el que te atiende antes: si podés viajar un poco, '
    'no esperás semanas por el más cercano.</p>'
)


def _example_locality(centro_nombres):
    """Locality present among the page's centers (e.g. 'Ostende')."""
    blob = " ".join(centro_nombres).lower()
    for loc in site_centers._EXAMPLE_LOCALITIES:
        if loc.lower() in blob:
            return loc
    return None


def _example_doctor(medicos):
    """The name of a doctor present on the page, as the row shows it.

    We prefer familiar Argentine last names when available: the example must
    read instantly as a person, not as a medical practice. The whole name, not
    the last name alone: a short last name on its own («Conti») read as a chip
    cut off at the screen's edge (ui review 2026-11-15).
    """
    ms = sorted((m for m in medicos if not m.get("rol") and m.get("nombre")),
                key=lambda m: site_base._norm_name(m["nombre"]))
    if not ms:
        return None

    def last_name_of(m):
        ape = str(m.get("apellido") or "").strip()
        if ape:
            return ape.split()[-1]
        toks = [t for t in str(m["nombre"]).split() if t]
        return toks[-1] if toks else ""

    comunes = ("sanchez", "gonzalez", "rodriguez", "lopez", "perez",
               "martinez", "fernandez", "garcia", "romero", "diaz")
    por_apellido = {site_base._norm_name(last_name_of(m)): m for m in ms}
    m = next((por_apellido[a] for a in comunes if a in por_apellido), ms[0])
    # The whole name as the row shows it, without the title: the search
    # matches a contiguous phrase («Juan Coronel» would not find Juan Carlos
    # Coronel), and any cut («Carlos Coronel», «la Fuente») stops reading as a
    # person. The title is not search text a patient types.
    words = [w for w in format.person_name(m["nombre"]).split()
             if not _EXAMPLE_TITLE.match(w)]
    return " ".join(words) or None


_EXAMPLE_TITLE = re.compile(r"(?i)^(dra?|lic|prof|esp)\.?$")


def _example_service(servicios, titulo=""):
    """A page's distinctive service (e.g. 'Urgencias' from 'Odontología -
    Urgencias'): prefers a name != the cluster's title, most hours first; if
    it carries a suffix after ' - ', uses the suffix (shorter and more
    recognizable)."""
    nombres = list(dict.fromkeys(s["nombre"] for s in servicios if s.get("nombre")))
    if not nombres:
        return None
    tnorm = site_base._norm_name(titulo)
    # Never the page's own title: «Probá: Cardiología» on the cardiology page
    # finds the whole page again (demo review 2026-11-15, P24) — no chip then.
    pool = [n for n in nombres if site_base._norm_name(n) != tnorm]
    if not pool:
        return None
    best = sorted(pool, key=lambda n: (-sum(site_availability._week_hours(s) for s in servicios
                                            if s.get("nombre") == n), len(n)))[0]
    # A short, recognizable chip: the suffix after ' - ' ('Odontología -
    # Urgencias' → 'Urgencias'), or without the generic prefix 'Consultorio
    # [de] ' ('Consultorio de niño enfermo' → 'niño enfermo'). Still valid as
    # a substring.
    if site_base._norm_name(best) == "clinica medica":
        return "Medicina general"
    if " - " in best:
        return best.split(" - ")[-1].strip()
    return re.sub(r"(?i)^consultorio\s+(?:de\s+)?", "", best).strip() or best


def _example_specialty(medicos, excluir=()):
    """Most frequent specialty among the page's doctors (for /doctors, as a
    third example of the 'service' type)."""
    ex = {site_base._norm_name(e) for e in excluir}
    conteo = {}
    for m in medicos:
        e = m.get("especialidad")
        if not e or m.get("rol") or site_base._norm_name(e) in ex:
            continue
        conteo[e] = conteo.get(e, 0) + 1
    return max(conteo, key=conteo.get) if conteo else None


def _example_dedup(*vals):
    """Non-empty, non-repeated examples (by normalized name), in order."""
    out, seen = [], set()
    for v in vals:
        if v and site_base._norm_name(v) not in seen:
            seen.add(site_base._norm_name(v))
            out.append(v)
    return out


def _services_enriched():
    """Services + 'proximo' cell (for the home page's search: same first
    fact as the tables) + 'horas' (popularity signal, see _week_hours)."""
    out = []
    for s in site_catalog._services():
        s2 = dict(s)
        s2["proximo"] = site_availability._service_next(s)
        s2["horas"] = site_availability._week_hours(s)
        out.append(s2)
    return out


def _doctors_enriched():
    """Doctor rows ready for listing.js (proximo/nombre/href/especialidad/
    centro/avatar) — consumed by the home page's doctor search."""
    return [_doctor_listing_row(m) for m in sorted(site_catalog._professionals(), key=lambda m: m["nombre"])]
