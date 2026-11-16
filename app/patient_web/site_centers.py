"""patient_web/site_centers.py — Health centers of the public site.

The center list and its derived views, addresses, rooms, phones, schema.org markup, the main center, the informational links and the example localities.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import hashlib
import json
import os
import re
import unicodedata
import urllib.parse

from app.core import format
from app.patient_web import doctor_photos
from app.core import sitedata
from app.core import data_packs
from app.core import i18n
from app.core import pack_l10n
from app.patient_web import seo
from app.core import site_identity

from app.patient_web import site_base
from app.patient_web import site_schedule
from app.patient_web import site_shell


def _centers(localize=True):
    d = site_base._load("centros-salud.json") or {}
    rows = site_base._with_overlay("centros", d.get("centros", []))
    try:
        from app.patient_web import catalog
        rows = catalog.expire_center_locations(rows, site_base._today_art())
    except Exception:  # noqa: BLE001
        pass
    rows = pack_l10n.localize("centros", rows) if localize else rows
    return rows if not localize else [_with_until_text(c) for c in rows]


def _with_until_text(c):
    """A place that is valid only until a date reads «<place>, hasta el 27 de
    noviembre» wherever the address is shown (never «desde»: the patient needs
    to know when it stops being right). The map link is built from the plain
    place first, so the date never ends up in the search."""
    until = str(c.get("location_until") or "")[:10]
    if not until or not c.get("address"):
        return c
    when = site_schedule._day_month(until)
    if not when:
        return c
    out = dict(c)
    if not out.get("maps_url"):
        out["maps_url"] = _maps_url(out)
    hasta = i18n.t_or("center.until", "hasta el {date}", i18n.active(), date=when)
    lugar = str(c["address"]).strip().rstrip(".")
    out["address"] = f'{lugar}, {hasta}'
    # The two parts apart, for a screen that links the place to the map: the
    # date is never inside the link (demo review 2026-11-15, P23).
    out["address_place"] = lugar
    out["address_until"] = hasta
    return out


def _centers_by_slug():
    return {c["slug"]: c for c in _centers() if c.get("slug")}


def _centers_info():
    """Researched info per center (webdata/centros-info.json): photo, credit,
    verified facts, more info and sources. {} if the center isn't there."""
    return site_base._load("centros-info.json") or {}


def _centers_reviews():
    """Curated reviews per center (webdata/centros-resenas.json, Google Maps via
    serper.dev): rating, count, maps, quotes, tips. {} if the center isn't in
    the file — degrades silently (no rating, no reviews)."""
    return site_base._load("centros-resenas.json") or {}


def _centers_gallery():
    """Photo gallery per center (webdata/centros-galeria.json). May not exist
    yet (another workstream generates it): _load already degrades None -> {}."""
    return site_base._load("centros-galeria.json") or {}


def _center_photo(slug, info=None):
    """The center's photo: the one uploaded from the panel (doctor_photos.py,
    `/center-photo/<slug>`) wins over the pack's centros-info.json one, the
    same priority a doctor's portrait has. A center the hospital creates in
    the panel only has the upload. '' when there's neither."""
    if info is None:
        info = _centers_info().get(slug) or {}
    return doctor_photos.photo_url_center(slug) or info.get("photo") or ""


def _center_pages():
    """{center slug: path} for the centers whose ONE page is an informational
    page instead of /center/<slug>: an enlaces.json entry that names the
    center (`"centro": "<slug>"`) owns it. Pinamar had two Zoonosis pages,
    /zoonosis and /center/zoonosis (press office, 2026-10-22); the richer one
    stays, takes the center's address, email and photo, and /center/zoonosis
    answers 301 to it. Only a site-relative path qualifies."""
    out = {}
    for e in _links(localize=False):
        if not isinstance(e, dict):
            continue
        slug = str(e.get("centro") or "").strip()
        href = str(e.get("href") or "").strip()
        if slug and href.startswith("/") and not href.startswith("//"):
            out.setdefault(slug, href)
    return out


def center_href(slug):
    """The canonical address of center `slug` — /center/<slug> unless an
    informational page owns it (`_center_pages`)."""
    return _center_pages().get(slug) or f"/center/{slug}"


def _directions_url(c):
    """Google Maps ROUTE to the center (the home's icon button): the address +
    the site's locality/province as destination. '' for a center with no fixed
    place — no address, or a `location_note` (the mobile unit moves)."""
    address = str(c.get("address") or "").strip().rstrip(".").strip()
    if not address or c.get("location_note"):
        return ""
    base = "https://www.google.com/maps/dir/?api=1&destination="
    lat, lng = c.get("lat"), c.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return base + f"{lat:.6f},{lng:.6f}"
    parts = [address, site_identity.get("city"), site_identity.get("province")]
    return base + urllib.parse.quote(", ".join(p for p in parts if p))


def _center_locality(c):
    """The area a center belongs to (Pinamar, Ostende, Valeria del Mar…): the
    one set in the panel (`locality`); otherwise the first known locality named
    in its name or address (the site's own city plus the pack's locality list).
    '' when none is named — the home then lists it after the areas, untitled,
    never guesses."""
    explicit = str(c.get("locality") or "").strip()
    if explicit:
        return explicit
    names = [str(n).strip() for n in (site_identity.get("city"), *_EXAMPLE_LOCALITIES) if n and str(n).strip()]
    hay_name = site_base._norm_name(c.get("name") or "")
    hay_addr = site_base._norm_name(c.get("address") or "")
    for hay in (hay_name, hay_addr):
        for loc in names:
            if re.search(r"(?<!\w)" + re.escape(site_base._norm_name(loc)) + r"(?!\w)", hay):
                return loc
    return ""


def _place_key(c):
    """Same-place key: the street part of the address (up to the first comma,
    accents/case/spacing ignored), so «Av. Shaw 255» and «Av. Shaw 255,
    Hospital Comunitario de Pinamar.» are one place. '' when there is no
    fixed address."""
    if c.get("location_note"):
        return ""
    street = str(c.get("address") or "").split(",")[0]
    street = unicodedata.normalize("NFKD", street)
    street = "".join(ch for ch in street if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", street).strip(" .").lower()


def _centers_web():
    """Centers with their `foto` attached (if any), for the home and /centers
    tiles; `href` only when the center's page is not /center/<slug>.
    `directions_url` (the route in Google Maps, '' when the center has no fixed
    place) and `place` (centers at the same street address share it; the main
    center of the site goes first in its place, `place_main`) feed the home's
    compact list."""
    info = _centers_info()
    paginas = _center_pages()
    out = []
    main = site_identity.get("main_center")
    for c in _centers():
        cc = dict(c)
        foto = _center_photo(c["slug"], info.get(c["slug"]) or {})
        if foto:
            cc["foto"] = foto
        if c["slug"] in paginas:
            cc["href"] = paginas[c["slug"]]
        cc["directions_url"] = _directions_url(c)
        # The address's own Maps link for every screen that writes the address
        # (the search row of a center): the data's maps_url, else built.
        cc["maps_url"] = _maps_url(c) if c.get("address") else ""
        cc["locality"] = _center_locality(c)
        place = _place_key(c)
        if place:
            cc["place"] = place
            if main and c["slug"] == main:
                cc["place_main"] = True
        out.append(cc)
    # Centers at the same address are in the same area: one that names no area
    # (Hemoterapia, «Av. Shaw 255») takes its neighbour's.
    area = {}
    for cc in out:
        if cc.get("place") and cc["locality"]:
            area.setdefault(cc["place"], cc["locality"])
    for cc in out:
        if cc.get("place") and not cc["locality"]:
            cc["locality"] = area.get(cc["place"], "")
    spec = _map_spec()
    if spec:
        for cc in out:
            pt = _map_point(cc, spec)
            if pt:
                cc["map_x"], cc["map_y"] = pt
    return out


# The home's map of the centers (the product owner, 2026-11-05: a handsome black-and-white map).
# The data pack carries a drawing made once from OpenStreetMap
# (`scripts/build_centers_map.py`): `centros-mapa.svg` plus `centros-mapa.json`
# with its bounding box and the point of each center. The page lays the dots
# and the names over the image itself; this side only says where each center
# falls on it, as fractions of the drawing.
_MAP_SPEC = "centros-mapa.json"


def _map_spec():
    """The pack's map description, or None when the pack has no map (or a
    broken one): the home then keeps the plain list."""
    spec = site_base._load(_MAP_SPEC)
    if not isinstance(spec, dict):
        return None
    bbox = spec.get("bbox")
    svg = str(spec.get("svg") or "")
    if (not isinstance(bbox, list) or len(bbox) != 4
            or not all(isinstance(v, (int, float)) for v in bbox)
            or not bbox[0] < bbox[2] or not bbox[1] < bbox[3]):
        return None
    if not svg or "/" in svg or "\\" in svg or not svg.endswith(".svg"):
        return None
    try:
        width, height = float(spec.get("width")), float(spec.get("height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    path = sitedata.path(svg)
    if not os.path.isfile(path):
        return None
    return {"bbox": bbox, "svg": svg, "path": path, "width": width, "height": height,
            "pins": spec.get("pins") if isinstance(spec.get("pins"), dict) else {},
            "areas": spec.get("areas") if isinstance(spec.get("areas"), list) else [],
            "attribution": str(spec.get("attribution") or "© OpenStreetMap"),
            "attribution_url": str(spec.get("attribution_url") or "https://www.openstreetmap.org/copyright")}


def _map_fraction(spec, lat, lng):
    w, s, e, n = spec["bbox"]
    if not (s <= lat <= n and w <= lng <= e):
        return None
    return round((lng - w) / (e - w), 5), round((n - lat) / (n - s), 5)


def _map_point(c, spec):
    """Where center `c` sits on the map, as (x, y) fractions of the drawing,
    or None. Only a center with a route (`directions_url`: a fixed place) is
    drawn. The coordinates typed in the panel win; otherwise the pack's pin,
    but only while the center is still at the address the pin was taken for
    (`place`): a center moved in the panel leaves the map for the list under
    it instead of pointing at its old door."""
    if not c.get("directions_url"):
        return None
    lat, lng = c.get("lat"), c.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return _map_fraction(spec, lat, lng)
    pin = spec["pins"].get(c.get("slug"))
    if not isinstance(pin, dict) or not pin.get("place") or pin.get("place") != _place_key(c):
        return None
    lat, lng = pin.get("lat"), pin.get("lng")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    return _map_fraction(spec, lat, lng)


def _centers_map_meta(centers):
    """What the home needs to draw the map, or None when no center lands on
    it. The image URL carries the hash of its bytes (`?v=`), so the server
    hands it out as immutable and a redrawn map is fetched anew."""
    spec = _map_spec()
    if not spec or not any("map_x" in c for c in centers):
        return None
    areas = []
    for a in spec["areas"]:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        lat, lng = a.get("lat"), a.get("lng")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        pt = _map_fraction(spec, lat, lng)
        if pt:
            areas.append({"name": str(a["name"]), "x": pt[0], "y": pt[1]})
    return {"svg": f"/webdata/{spec['svg']}?v={_photo_sha(spec['path'])}",
            "width": spec["width"], "height": spec["height"], "areas": areas,
            "attribution": spec["attribution"], "attribution_url": spec["attribution_url"]}


def _centers_api():
    """/api/centers: the centers and, when the pack has one, the map."""
    data = _centers_web()
    out = {"centros": data}
    meta = _centers_map_meta(data)
    if meta:
        out["mapa"] = meta
    return out


def _tile_center(c):
    """Minimal tile payload (the only thing tiles.mjs draws). Reads the
    (now-English) center fields but WRITES the tile's own Spanish wire
    vocabulary (nombre/direccion/jtbd) unchanged — tiles.mjs is shared with
    _page_workshops()'s tile payload (built from servicios.json, not yet
    renamed), so the tile keys stay a stable contract independent of
    either source's own field names.

    No `maps_url` (demo review 2026-11-15, P35): the card is ONE link to the
    center's page; a second link to Maps inside it read as a nested link.
    The address stays as dark text; the Maps link lives on the center's
    page, next to the phone."""
    tile = {"slug": c["slug"], "nombre": c["name"],
            "direccion": c.get("address", ""),
            "jtbd": c.get("jtbd") or c.get("description") or "",
            "foto": c.get("foto") or ""}
    if c.get("address_until") and c.get("address_place"):
        # the place is the address line, the date its own plain line under it
        tile["direccion"] = c["address_place"]
        hasta = str(c["address_until"])
        tile["direccion_nota"] = hasta[:1].upper() + hasta[1:]
    if c.get("href"):
        tile["href"] = c["href"]
    return tile


# A 24 h guardia has ONE source (demo review 2026-11-15, P12): the hours of a
# walk-in service at that center («Las 24 horas, todos los días»). The home's
# walk-in card and the /centers cards are pack prose; a sentence there that
# promises a 24 h guardia must name a center that holds such a service, or a
# patient walks to a CAPS at 3 am. `round_the_clock_contradictions()` is the
# check the packs answer to (tests/test_home_shell_review.py runs it on the
# demo pack; a delivery of another pack runs it the same way).
_ROUND_THE_CLOCK = re.compile(r"\b24\s*(?:h|hs|horas)\b", re.I)
_GUARDIA_24H = re.compile(r"guardia[^.;\n]{0,40}?\b24\s*(?:h|hs|horas)\b"
                          r"|\b24\s*(?:h|hs|horas)\b[^.;\n]{0,20}?guardia", re.I)
_WALKIN_MODES = ("walkin", "walkin_con_orden")


def round_the_clock_centers(servicios):
    """Slugs of the centers holding a walk-in service open 24 hours."""
    return {s.get("centro") for s in servicios or []
            if s.get("centro") and s.get("modo") in _WALKIN_MODES
            and _ROUND_THE_CLOCK.search(str(s.get("horario") or ""))}


def round_the_clock_contradictions(centros, servicios, clusters):
    """(id, field) of every prose claim of a 24 h guardia the data does not
    back: a center's `jtbd`/`description`/`jobs` when that center holds no
    24 h walk-in service, and a walk-in cluster's `descripcion` when no
    center does. Empty list = the packs say it from one source."""
    backed = round_the_clock_centers(servicios)
    out = []
    for c in centros or []:
        slug = c.get("slug")
        if slug in backed:
            continue
        for field in ("jtbd", "description"):
            if _GUARDIA_24H.search(str(c.get(field) or "")):
                out.append((slug, field))
        if any(_GUARDIA_24H.search(str(j or "")) for j in c.get("jobs") or []):
            out.append((slug, "jobs"))
    if not backed:
        for cl in clusters or []:
            if cl.get("kind") == "walkin" and _GUARDIA_24H.search(str(cl.get("descripcion") or "")):
                out.append((cl.get("id"), "descripcion"))
    return out


# A center photo is a building, not a face: drawn whole in a 44 px circle it
# reads as a smudge of sky and pavement (the product owner, 2026-10-27: «choose larger
# fragments»). The data pack names the fragment per photo in centros-info.json
# — `"avatar": {"x": .62, "y": .62, "zoom": 2, "for": "<sha256[:12]>"}`, the
# focus as fractions of the photo and how many times narrower than its short
# side the square is — and `for` ties it to the exact bytes it was chosen on:
# once the hospital uploads another photo from the panel, a focus picked on
# the old one would cut a random patch of the new one, so it falls back to
# this default (a bit below the middle, where entrances are) instead.
_CENTER_FOCUS_DEFAULT = (0.5, 0.58, 1.6)
_photo_sha_memo = {}  # (path, mtime_ns, size) -> sha256[:12]


def _photo_sha(path):
    try:
        st = os.stat(path)
    except OSError:
        return ""
    k = (path, st.st_mtime_ns, st.st_size)
    h = _photo_sha_memo.get(k)
    if h is None:
        try:
            with open(path, "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()[:12]
        except OSError:
            return ""
        _photo_sha_memo[k] = h
    return h


def _center_photo_file(slug, info):
    """Local file behind `_center_photo()` — the panel's upload first, else
    the pack's `/webdata/…` photo — or '' when it has none on disk."""
    subido = doctor_photos._existing(slug, "centers")
    if subido:
        return subido[0]
    rel = (info.get("photo") or "").split("?", 1)[0]
    if not rel.startswith("/webdata/"):
        return ""
    rel = rel[len("/webdata/"):]
    if "\\" in rel or any(p in ("", ".", "..") for p in rel.split("/")):
        return ""
    p = sitedata.path(rel)
    return p if os.path.isfile(p) else ""


def _center_focus(info, path):
    a = info.get("avatar")
    if isinstance(a, dict) and a.get("for") and a.get("for") == _photo_sha(path):
        try:
            return (float(a.get("x", 0.5)), float(a.get("y", 0.5)),
                    float(a.get("zoom", 1.0)))
        except (TypeError, ValueError):
            pass
    return _CENTER_FOCUS_DEFAULT


def _center_faces():
    """[(`center:<slug>`, file, focus)] for EVERY center that has a photo,
    handed to the shared atlas next to the doctors' portraits: each place that
    draws a center as a small circle (the walk-in card on the home, the panel's
    photo editor) reads its tile from here, never the photo itself — a panel
    upload can be several MB (the product owner, 2026-10-28: the hospital circles pulled
    huge pictures). A center's tile is ~1 KB in the atlas the home already loads."""
    try:
        info = _centers_info()
        out = []
        for c in _centers():
            slug = c.get("slug")
            if not slug:
                continue
            ci = info.get(slug) or {}
            path = _center_photo_file(slug, ci)
            if path:
                out.append(("center:" + slug, path, _center_focus(ci, path)))
        return out
    except Exception:
        return []


def _room_text(valor):
    """A service's room, read once (TASK-57, 26.10). The packs already write
    «Consultorio 1» or «Sala de curaciones»; only a bare number («4», «12B»)
    needs the label in front of it."""
    v = str(valor or "").strip()
    if v[:1].isdigit():
        return f'{i18n.t_or("page.hours.room", "Consultorio", i18n.active())} {v}'
    return v


def _room_line_html(valor):
    """The room line of a service page: the label as its key only when the
    value is a bare number, the named place by itself otherwise."""
    v = str(valor or "").strip()
    if v[:1].isdigit():
        return (f'<div class="ci"><span class="ci-k">'
                f'{i18n.t_or("page.hours.room", "Consultorio", i18n.active())}</span> {site_base._esc(v)}</div>')
    return f'<div class="ci">{site_base._esc(v)}</div>'


def _maps_url(centro):
    """Google Maps link: uses the data's maps_url if present, otherwise builds
    it from the address + the active site's locality/province (site_identity.py)."""
    mu = centro.get("maps_url")
    if mu:
        return mu
    # a sentence's closing period («…de Pinamar.») is not part of the place
    address = str(centro.get("address_place") or centro.get("address") or "").strip().rstrip(".").strip()
    parts = [address, site_identity.get("city"), site_identity.get("province")]
    q = ", ".join(p for p in parts if p)
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(q)


def _center_schema_type(slug):
    """schema.org type of a center: the installation's main center (sitio.json
    `main_center` — Pinamar's hospital) is a Hospital, every other one (CAPS,
    annexes) a MedicalClinic."""
    return "Hospital" if slug and slug == site_identity.get("main_center") else "MedicalClinic"


def _center_address_ld(c):
    addr = {"@type": "PostalAddress"}
    if c.get("address"):
        addr["streetAddress"] = c["address"]
    addr.update({"addressLocality": site_identity.get("city"),
                 "addressRegion": site_identity.get("province"),
                 "addressCountry": site_base._COUNTRY["iso2"]})
    return addr


def _center_ld(c, photo=""):
    """JSON-LD Hospital/MedicalClinic for /center/<slug> — only facts the page
    itself shows (name, address, phone, email, map, photo); no opening hours,
    since the pack's `horario` is free text, not a parseable schedule."""
    ld = {"@context": "https://schema.org", "@type": _center_schema_type(c.get("slug")),
          "name": c["name"], "url": seo.canonical_url(f'/center/{c["slug"]}'),
          "address": _center_address_ld(c)}
    if c.get("phone"):
        ld["telephone"] = _phone_text(c["phone"])
    if c.get("email"):
        ld["email"] = c["email"]
    if c.get("address") or c.get("maps_url"):
        ld["hasMap"] = _maps_url(c)
    if photo:
        ld["image"] = seo.canonical_url(photo) if photo.startswith("/") else photo
    return f'<script type="application/ld+json">{site_base._json_inline(ld)}</script>'


def _phone_href(tel):
    """First number in the phone field, for a tel: href — format.py's rule
    (the canonical E.164 form when the number is complete), so the link dials
    exactly the number the page shows."""
    return format.tel_href(tel)


def _phone_text(tel):
    """A phone number, written the same way as EVERYWHERE on the site. The
    rule doesn't live here: format.py has it, the server-side mirror of
    format.mjs — a single criterion for the pages the server renders and the
    ones the browser builds (contact hook, product owner 2026-07-28)."""
    return format.tel_text(tel)


def main_center():
    """The installation's main center — Settings «Centro principal»
    (SITE_ID_MAIN_CENTER) over sitio.json `main_center` — or None when it is
    unset or names no live center. The ONE place a patient is pointed to when
    a problem needs someone in person, instead of «tu centro de salud» (the product owner
    23.10: «suggest the main center»)."""
    slug = str(site_identity.get("main_center") or "").strip()
    return _centers_by_slug().get(slug) if slug else None


def main_center_place():
    """`main_center()` as the hints show it: name, address, its page and one
    contact (WhatsApp, else phone), plain values for the browser to render.
    No opening hours: the pack's centers carry none for the admission desk."""
    c = main_center()
    if not c:
        return None
    wa = str(c.get("whatsapp") or "").strip()
    tel = str(c.get("phone") or "").strip()
    return {
        "nombre": c.get("name") or "",
        "direccion": c.get("address") or "",
        "maps_url": _maps_url(c) if c.get("address") else "",
        "url": f'/center/{c["slug"]}',
        "telefono": format.tel_text(wa or tel) if (wa or tel) else "",
        "telefono_href": (f"https://wa.me/{format.tel_wa(wa)}" if wa
                          else (f"tel:{format.tel_e164(tel)}" if tel else "")),
    }


site_identity.register_main_center_place(main_center_place)


def main_center_text():
    """«Hospital de Pinamar, Av. Shaw 255» for a plain-text message; '' when
    there is no main center (the caller keeps its generic wording)."""
    p = main_center_place()
    if not p:
        return ""
    return ", ".join(x for x in (p["nombre"], p["direccion"]) if x)


def help_contact_html(centro=None, nombre_fallback=""):
    """The ONE «¿Te trabás en algún paso?» line of the order upload page and
    the booking wizard. An installation with a help line on WhatsApp
    (sitio.json `help_whatsapp`) sends everyone there — Pinamar's MUPI bot,
    2254 60-2222, instead of the hospital's landline (press office,
    2026-10-22); otherwise the center's WhatsApp, else its phone. '' when
    there is no number: never a made-up one."""
    lang = i18n.active()
    sitio_wa = str(site_identity.get("help_whatsapp") or "").strip()
    sitio_link = format.tel_wa(sitio_wa) if sitio_wa else ""
    if sitio_link:
        return (i18n.t_or("wizard.contact.whatsapp_site",
                          "¿Te trabás en algún paso? Escribinos por WhatsApp:", lang) + ' '
                f'<a href="https://wa.me/{sitio_link}">{site_base._esc(format.tel_text(sitio_wa))}</a>')
    c = centro or {}
    nombre_c = site_base._esc(c.get("name") or nombre_fallback)
    wa = str(c.get("whatsapp") or "").strip()
    tel = str(c.get("phone") or "").strip()
    wa_link = format.tel_wa(wa) if wa else ""
    tel_link = format.tel_e164(tel) if tel else ""
    if wa_link:
        return (i18n.t_or("wizard.contact.whatsapp",
                          "¿Te trabás en algún paso? Escribile a {name} por WhatsApp:",
                          lang, name=nombre_c) + ' '
                f'<a href="https://wa.me/{wa_link}">{site_base._esc(format.tel_text(wa))}</a>')
    if tel_link:
        return (i18n.t_or("wizard.contact.call", "¿Te trabás en algún paso? Llamá a {name}:",
                          lang, name=nombre_c) + ' '
                f'<a href="tel:{tel_link}">{site_base._esc(format.tel_text(tel))}</a>')
    return ""


# MODE_LABEL / CATEGORY_LABEL / CATEGORY_ORDER / MODE_SECTION vivían acá:
# diccionarios/listas a nivel de módulo que ARMABAN sus labels con
# `i18n.t_or(..., i18n.active())` al importar el archivo, cuando todavía no
# hay ningún request bindeado (mismo defecto que `_STUDIES_COLS` de arriba,
# congelado en castellano para siempre). Se eliminan en vez de arreglarse
# porque `git grep` no encontró NINGUNA referencia a los cuatro nombres en
# todo el repo — están muertos, nadie los arma ni los lee. Si alguien los
# necesita, que arme el dict/list DENTRO de la función que los use (mismo
# patrón que `_cols()`/`_centro_medicos_cols()`), nunca como constante de
# módulo: así no se resucita el mismo bug.


def _age_text(servicio):
    e = servicio.get("edad") or {}
    lo, hi = e.get("min"), e.get("max")
    if lo and hi:
        return i18n.t_or("page.age.between", "De {lo} a {hi} años", i18n.active(), lo=lo, hi=hi)
    if hi:
        return i18n.t_or("page.age.upto", "Hasta los {hi} años", i18n.active(), hi=hi)
    if lo:
        return i18n.t_or("page.age.from", "Desde los {lo} años", i18n.active(), lo=lo)
    return ""


# ---- Per-page search examples (the product owner, 2026-07-23) ----------------------------
# Every "Try:" MUST return results ON that page, and teach the three useful
# search modes: by locality, by the last name of a doctor present on the
# page, and by service/study. They're computed from the page's own data, so
# they never go stale or suggest something the page doesn't have (the old
# fixed set "Clínica médica · Hospital · Ostende" used to show up even in
# Odontología).

# Phase 2c (2026-07-30, see docs/architecture/data-packs.md, "patient_web/site_*.py
# live-page audit"): was a literal Pinamar-area list. Now the active pack's
# own example-localities.json (whole-file override). A town pack that hasn't
# shipped one gets an EMPTY list, deliberately never falling back to
# Pinamar's — a search-example feature quietly not firing for a town that
# hasn't curated its own list is fine (see _example_locality below: it only
# ever surfaces a name already present on the page); silently showing
# another town's locality name never is. Read once at import (deployment
# content, not admin-editable), same pattern as format.py's PHONE.
def _load_example_localities():
    try:
        with open(data_packs.resolve_path("example-localities.json"), encoding="utf-8") as f:
            localities = (json.load(f) or {}).get("localities")
        if isinstance(localities, list) and all(isinstance(x, str) for x in localities):
            return localities
    except (OSError, ValueError):
        pass
    return []


_EXAMPLE_LOCALITIES = _load_example_localities()


# ---- study preparation (fasting / bring a CD / full bladder …) -------------

def _links(localize=True):
    """Informational search results (telemedicina, zoonosis, donar sangre…) —
    same pack-override + admin-overlay pattern as _centers()/_preparations().
    A pack that hasn't defined enlaces.json yet -> no results, same
    never-breaks contract as everything else in this file."""
    base = site_base._load("enlaces.json") or {}
    base = base.get("enlaces", []) if isinstance(base, dict) else []
    rows = site_base._with_overlay("enlaces", base)
    if not localize:
        return rows
    # `{{sitio.emergency_number}}` and friends resolve AFTER the translation
    # lookup, which is keyed on the pack's own text (placeholder included).
    rows = site_identity.expand(pack_l10n.localize("enlaces", rows))
    builtin = _upload_order_link(rows)
    return rows + [builtin] if builtin else rows


# What people type when they look for the referral reader: the platform's own
# search vocabulary (never translated — the matcher needs both languages, same
# as a pack's `palabras`). Usability study 2026-10-16: «subir orden» found
# nothing on a pack that had no enlace to /upload-order.
_UPLOAD_ORDER_WORDS = (
    "subir orden", "cargar orden", "subir la orden", "cargar la orden",
    "orden médica", "orden medica", "pedido médico", "pedido medico",
    "orden de derivación", "orden de derivacion", "derivación", "derivacion",
    "pedido de estudio", "foto de la orden",
    "upload order", "upload referral", "referral", "medical order",
)


def _upload_order_link(rows):
    """The platform's own search result for /upload-order, on every
    installation: «Subí tu orden» must be findable by search even when the
    pack declares no enlace for it. A pack that does declare one (any enlace
    whose href is /upload-order) wins — its wording and keywords are kept."""
    if any((r.get("href") or "").split("?")[0].rstrip("/") == "/upload-order"
           for r in rows if isinstance(r, dict)):
        return None
    lang = i18n.active()
    return {
        "id": "subir-orden",
        "nombre": i18n.t_or("nav.upload_order", "Subí tu orden", lang),
        "descripcion": i18n.t_or(
            "upload.page.subtitle",
            "La leemos y te decimos qué turno sacar y cómo prepararte.",
            lang),
        "href": "/upload-order",
        "accion_texto": i18n.t_or("upload.zone.heading", "Tomá una foto de la orden", lang),
        "accion_href": "/upload-order",
        "palabras": list(_UPLOAD_ORDER_WORDS),
    }


def _link_by_id(lid):
    if not lid:
        return None
    for e in _links():
        if e.get("id") == lid:
            return e
    return None


def _center_call_links(c, cls):
    """The center's phone and WhatsApp as links, phone first, each with its
    own icon: a WhatsApp number (Pinamar's MUPI bot, 2254 60-2222) goes to
    wa.me with the Icons8 WhatsApp glyph and the word «WhatsApp», never as a
    tel: link a bot cannot answer. One helper for the service and doctor
    pages' «where» blocks."""
    out = []
    if c.get("phone"):
        out.append(f'<a class="{cls}" href="tel:{site_base._esc(_phone_href(c["phone"]))}">'
                   f'{site_shell._PHONE_ICON}{site_base._esc(_phone_text(c["phone"]))}</a>')
    wa = format.tel_wa(c.get("whatsapp") or "") if c.get("whatsapp") else ""
    if wa:
        out.append(f'<a class="{cls}" href="https://wa.me/{wa}">'
                   '<img src="/static/icons/whatsapp-sf-regular.svg" alt="" width="14" height="14">'
                   f'WhatsApp · {site_base._esc(format.tel_text(c["whatsapp"]))}</a>')
    return out
