"""format.py — server-side mirror of static/platform/format.mjs.

The site writes phone numbers from BOTH sides: the browser (static/platform/format.mjs, in
the panel and the wizard) and the server (the center, service and doctor
pages, which are rendered whole here). Two runtimes, so two files — but ONE
rule: the PAIS (country) block below comes from the SAME file both sides
read, `packs/ar/country.json` ("phone"), and `tests/formato.test.mjs` fails
if a code change makes them disagree.

For another country, change `packs/ar/country.json` and nothing else —
neither this file nor format.mjs hardcodes a country anymore (Phase 2b,
2026-07-30; see docs/architecture/data-packs.md). Keys are English
(docs/rename-map.md §6); this module projects them into the same local
`PAIS` shape both files' functions already used, so the functions below
didn't need to change.

Canonical form (the product owner, 2026-07-28), the only one shown and the only one
saved:

    +54 9 11 3547-7302     AMBA cell phone
    +54 11 4321-5678       AMBA landline
    +54 9 2254 12-3456     Pinamar cell phone
    +54 2254 40-1234       Pinamar landline
"""

import json
import re

from app.core import data_packs

# ====================== THE COUNTRY LIVES IN packs/ar/country.json ========
# Read once at import (this module's PAIS is a constant, not admin-editable
# content, so no mtime-refresh dance is needed). The literal dict below is
# ONLY a last-resort safety net for a broken/mid-deploy checkout missing the
# pack — data_packs.resolve_path() already degrades gracefully on its own,
# this is one layer further out. Keep it equal to country.json's "phone"
# block (English keys, docs/rename-map.md §6); a code review of this file
# is not the place to change a country. `PHONE` is that raw shape — also
# what gets sent to the browser as window.SANA_COUNTRY_PHONE
# (`country_phone_json()` below) so format.mjs normalizes it exactly the
# same way this module does, one JSON, two independent readers.
_FALLBACK_PHONE = {
    "code": "54", "exit_prefix": "00", "national_length": 10, "mobile_prefix": "9",
    "mobile_local_suffix": "15", "trunk_prefix": "0", "areas_2": ["11"],
    "areas_3": ("220 221 223 230 236 237 249 260 261 263 264 266 280 291 297 299 "
                "341 342 343 345 348 351 353 358 362 364 370 376 379 380 381 383 "
                "385 387 388").split(),
    "area_default": 4, "final_group": 4, "non_geographic": ["800", "810", "600"],
}


def _load_phone():
    phone = data_packs.value("country.json", "phone")
    return phone if isinstance(phone, dict) and phone else dict(_FALLBACK_PHONE)


def _normalize(phone):
    return {
        "codigo": phone["code"],
        "salida": phone["exit_prefix"],
        "largo_nacional": phone["national_length"],
        "movil_prefijo": phone["mobile_prefix"],
        "movil_local": phone["mobile_local_suffix"],
        "larga_distancia": phone["trunk_prefix"],
        "areas_2": phone["areas_2"],
        "areas_3": phone["areas_3"],
        "area_default": phone["area_default"],
        "grupo_final": phone["final_group"],
        "no_geograficos": phone["non_geographic"],
    }


PHONE = _load_phone()
try:
    PAIS = _normalize(PHONE)
except (KeyError, TypeError):
    PHONE = dict(_FALLBACK_PHONE)
    PAIS = _normalize(PHONE)
# ================== end of the country block ============================

_AREA2 = set(PAIS["areas_2"])
_AREA3 = set(PAIS["areas_3"])

# ============ DNI length, also from packs/ar/country.json ================
# Phase 2c (2026-07-30, see docs/architecture/data-packs.md): was ~7
# hardcoded `7 <= len(dni) <= 9` sites (accounts.py, admin_patients.py,
# puco.py, admin_appointments.py, account_photo.py) — now this one function,
# same read-once + literal-fallback pattern as PHONE above.
_FALLBACK_DNI = {"length_min": 7, "length_max": 9}


def _load_dni():
    dni = data_packs.value("country.json", "dni")
    if not isinstance(dni, dict) or "length_min" not in dni or "length_max" not in dni:
        return dict(_FALLBACK_DNI)
    return dni


DNI = _load_dni()


def dni_valid_length(dni):
    """True if `dni` (already digits-only) has a plausible length for the
    installation's national id document — DNI_LENGTH_MIN..DNI_LENGTH_MAX
    inclusive, from packs/ar/country.json."""
    n = len(str(dni or ""))
    return DNI["length_min"] <= n <= DNI["length_max"]
# ================ end of the DNI block ====================================

# ========= currency/locale, also from packs/ar/country.json ===============
# Phase 2c (2026-07-30, see docs/architecture/data-packs.md): admin-reports.js
# hardcoded "es-AR"/"AR$" for every peso figure in the Reports section — now
# injected the same way SANA_COUNTRY_PHONE is (window.SANA_COUNTRY_CURRENCY,
# see country_currency_json() and booking_requests._serve_admin's
# {{country_currency}} substitution). "US$" (AI costs, always billed in
# dollars) is NOT part of this block — it isn't local currency.
_FALLBACK_CURRENCY = {"code": "ARS", "symbol": "$", "locale": "es-AR", "iso2": "AR"}


def _load_currency():
    doc = data_packs.value("country.json") or {}
    currency = doc.get("currency")
    if not isinstance(currency, dict) or not currency.get("locale"):
        return dict(_FALLBACK_CURRENCY)
    currency = dict(currency)
    currency.setdefault("iso2", doc.get("iso2") or _FALLBACK_CURRENCY["iso2"])
    return currency


CURRENCY = _load_currency()


def country_currency_json():
    """`CURRENCY` as JSON safe to embed inside a `<script>…</script>` —
    injected as `window.SANA_COUNTRY_CURRENCY` before admin-reports.js,
    same pattern as `country_phone_json()`. Drops any `_`-prefixed
    documentation key (e.g. country.json's `currency._note`) — admin-only
    page source, but no reason to ship a comment as page bytes."""
    public = {k: v for k, v in CURRENCY.items() if not k.startswith("_")}
    return json.dumps(public, ensure_ascii=False).replace("<", "\\u003c")
# ============== end of the currency block ==================================


def number(n, decimals=1, lang=None):
    """A number in the language's digits — the server twin of format.mjs
    numero(): thousands always grouped («4.188»), at most `decimals`
    decimals, never a trailing zero. One implementation, `i18n.format_number`."""
    from app.core import i18n
    return i18n.format_number(n, decimals, lang)


def percent(n, lang=None):
    """«52%» — twin of format.mjs porcentaje(); `i18n.format_percent`."""
    from app.core import i18n
    return i18n.format_percent(n, lang)


def _digits(v):
    return re.sub(r"\D", "", str(v if v is not None else ""))


def _area_length(d):
    if d[:2] in _AREA2:
        return 2
    return 3 if d[:3] in _AREA3 else PAIS["area_default"]


def _non_geographic(d):
    return d[:3] in PAIS["no_geograficos"]


def parts(v):
    """(national_digits, is_mobile, is_valid) for any way of writing it.
    Mobile is NEVER guessed: it's only known if the number carries the mark
    (the 9 after the country code, or the 15 after the area code), because
    +54 11 4321-5678 and +54 9 11 4321-5678 are two different lines."""
    d = _digits(v)
    mobile = False
    if not d:
        return "", False, False
    if d.startswith(PAIS["salida"]):
        d = d[len(PAIS["salida"]):]
    largo = PAIS["largo_nacional"]
    if len(d) > largo and d.startswith(PAIS["codigo"]):
        d = d[len(PAIS["codigo"]):]
    if len(d) > largo and d.startswith(PAIS["movil_prefijo"]):
        d, mobile = d[1:], True
    if len(d) > largo and d.startswith(PAIS["larga_distancia"]):
        d = d[1:]
    if len(d) > largo:  # historical 15, tacked on after the area code
        a = _area_length(d)
        ml = PAIS["movil_local"]
        if d[a:a + len(ml)] == ml:
            d, mobile = d[:a] + d[a + len(ml):], True
    ok = len(d) == largo and not _non_geographic(d)
    return d, mobile, ok


def _group(rest):
    g = PAIS["grupo_final"]
    return rest if len(rest) <= g else rest[:-g] + "-" + rest[-g:]


def tel_text(v):
    """The canonical form. A field can carry several numbers
    ("(02254) 49-1670 / 49-1671"): each part gets formatted, and one that
    isn't a complete number (the second one shares the area code) is left
    as-is — making one up would be lying."""
    trozos = [p.strip() for p in str(v if v is not None else "").split("/")]
    return " / ".join(_tel_text_one(p) for p in trozos if p)


def _tel_text_one(v):
    crudo = str(v if v is not None else "").strip()
    d, mobile, ok = parts(crudo)
    if not ok:
        return crudo
    a = _area_length(d)
    prefijo = "+" + PAIS["codigo"] + " " + (PAIS["movil_prefijo"] + " " if mobile else "")
    return prefijo + d[:a] + " " + _group(d[a:])


def _first(v):
    """An E.164 or a WhatsApp link is for ONE number: with several, the
    first one."""
    return str(v if v is not None else "").split("/")[0].strip()


def tel_valido(v):
    """Can it be called? (TASK-51, 26.10) A complete domestic number, or a
    foreign one written with its + (or the 00 exit prefix) and 8 to 15
    digits. Anything else — «2254» alone, the area code typed twice, an 0800
    — is not stored as a phone. Same rule as format.mjs telValido."""
    s = _first(v)
    if parts(s)[2]:
        return True
    d = _digits(s)
    if s.startswith("+"):
        salida = d
    elif d.startswith(PAIS["salida"]):
        salida = d[len(PAIS["salida"]):]
    else:
        return False
    if not salida or salida.startswith(PAIS["codigo"]):
        return False
    return 8 <= len(salida) <= 15


def tel_e164(v):
    """The same canonical form without separators, for storage."""
    d, mobile, ok = parts(_first(v))
    if not ok:
        return ""
    return "+" + PAIS["codigo"] + (PAIS["movil_prefijo"] if mobile else "") + d


def tel_href(v):
    """What goes after `tel:` for the number `tel_text` shows: the E.164 form
    of the first number when it is complete, else its digits (a short code
    like 107, an 0800, a foreign number keeping its +). The link dials
    exactly what the page shows. Same rule as format.mjs telHref."""
    uno = _first(v)
    e164 = tel_e164(uno)
    if e164:
        return e164
    crudo = uno.strip()
    return ("+" if crudo.startswith("+") else "") + _digits(crudo)


def tel_national(v):
    """Just the national digits — the key for comparing two numbers written
    in different forms."""
    d, _, _ = parts(v)
    return d


def tel_wa(v):
    """Digits with country code for a wa.me link. The 9 always goes in:
    it's what WhatsApp requires for an Argentine cell phone."""
    uno = _first(v)
    d, _, ok = parts(uno)
    if ok:
        return PAIS["codigo"] + PAIS["movil_prefijo"] + d
    if _non_geographic(d):
        return ""
    crudo = _digits(uno)
    return crudo if len(crudo) >= 8 else ""


# ---- people's names -------------------------------------------------------
# Mirror of format.mjs:nombre(). A name is ALWAYS shown in normal capitalization
# (the product owner, 2026-08-01: "don't capitalize the names, write them everywhere
# normally") — "ALEJO VARELA" shouted in a card is noise that comes from HSI
# and from whatever the front desk types with Caps Lock on, not from how the
# person is called.
#
# Only an all-caps or all-lowercase string is rewritten. Anything already mixed
# ("Hugo Montes", "van der Berg") was typed by someone who knows how it is
# spelled, and guessing on top of that breaks real surnames.
_NAME_PARTICLES = {
    "de", "del", "la", "las", "los", "y", "e", "da", "das", "do", "dos",
    "di", "du", "van", "von", "der", "den", "ter", "le",
}
_NAME_TITLES = {"dr.": "Dr.", "dra.": "Dra.", "lic.": "Lic.", "prof.": "Prof.", "esp.": "Esp."}
_NAME_LETTER = re.compile(r"[^A-Za-zÁÉÍÓÚÜÑÇáéíóúüñç]")
_NAME_HAS_LOWERABLE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑÇáéíóúüñç]")


def _name_piece(p):
    if not p or not _NAME_HAS_LOWERABLE.search(p):
        return p
    return p[0].upper() + p[1:].lower()


def _name_word(w):
    # Capitalize each hyphen/apostrophe part: O'Brien, Ana-María.
    out, buf = [], ""
    for ch in w:
        if ch in "-'’":
            out.append(_name_piece(buf))
            out.append(ch)
            buf = ""
        else:
            buf += ch
    out.append(_name_piece(buf))
    return "".join(out)


def person_name(v):
    """A person's name in normal capitalization. See the note above."""
    s = " ".join(str(v or "").split())
    if not s:
        return ""
    words = s.split(" ")
    # The title doesn't vote: "Dr. DIEGO CORONEL" is a shouted name with a
    # correctly-written title in front, not a mixed-case string to respect.
    letters = _NAME_LETTER.sub(
        "", " ".join(w for w in words if w.lower() not in _NAME_TITLES))
    if not letters:
        return s
    if not (letters == letters.upper() or letters == letters.lower()):
        return s
    out = []
    for i, w in enumerate(words):
        k = w.lower()
        if k in _NAME_TITLES:
            out.append(_NAME_TITLES[k])
        elif 0 < i < len(words) - 1 and k in _NAME_PARTICLES:
            out.append(k)
        else:
            out.append(_name_word(w))
    return " ".join(out)


def resolved_by_text(value, resolver_name=None, short=False):
    """Who resolved a request (`solicitudes.resuelta_por`): the stored
    «auto:web» / «mesa:<user>» / «medico:<user>» as a phrase a person reads.
    `resolver_name` is the doctor's (or desk operator's) name when known, else
    the login; `short` is the narrow-column wording. An unknown prefix shows
    without it, empty shows nothing. Mirror of format.mjs:resueltaPor."""
    from app.core import i18n
    v = str(value if value is not None else "").strip()
    if not v:
        return ""
    pref, sep, rest = v.partition(":")
    if not sep:
        pref, rest = "", v
    pref = pref.lower()
    who = person_name(resolver_name) if resolver_name else rest
    lang = i18n.active()
    if pref == "auto":
        if rest == "web":
            if short:
                return i18n.t_or("admin.queue.resuelta_por.auto_web_corto", "Automática (web)", lang)
            return i18n.t_or("admin.queue.resuelta_por.auto_web",
                             "Automática: el paciente sacó el turno en la página", lang)
        return i18n.t_or("admin.queue.resuelta_por.auto", "Automática", lang)
    if pref == "mesa":
        return i18n.t_or("admin.queue.resuelta_por.mesa", "Mesa de entrada · {nombre}", lang, nombre=who)
    if pref == "medico":
        return i18n.t_or("admin.queue.resuelta_por.medico", "Médico · {nombre}", lang, nombre=who)
    return rest


def _birth_and_today(birth, today=None):
    import datetime
    try:
        b = datetime.date.fromisoformat(str(birth or "")[:10])
    except ValueError:
        return None, None
    if today:
        t = datetime.date.fromisoformat(str(today)[:10])
    else:
        from app.core import tz
        t = datetime.datetime.now(tz.tzinfo()).date()
    return b, t


def age_years(birth, today=None):
    """Whole years since `birth` ("YYYY-MM-DD"); None when missing, invalid or
    out of range. Mirror of format.mjs:edad. `today` pins the day in tests."""
    b, t = _birth_and_today(birth, today)
    if b is None:
        return None
    a = t.year - b.year
    if (t.month, t.day) < (b.month, b.day):
        a -= 1
    return a if 0 <= a < 130 else None


def age_text(birth, today=None):
    """The age as people say it: the bare number, months under a year.
    Mirror of format.mjs:edadTexto."""
    a = age_years(birth, today)
    if a is None:
        return ""
    if a > 0:
        return str(a)
    b, t = _birth_and_today(birth, today)
    months = (t.year - b.year) * 12 + (t.month - b.month)
    if t.day < b.day:
        months -= 1
    if months <= 0:
        return "recién nacido"
    return f"{months} mes" if months == 1 else f"{months} meses"


def person_age(first, last, birth, today=None):
    """A person as people write it: "Ilya Belov, 48" (first name, last name,
    comma, age; no comma without a birth date). Mirror of format.mjs:personaEdad
    — one function for every list of the panel."""
    n = person_name(" ".join(p for p in (first, last) if p))
    e = age_text(birth, today)
    return f"{n}, {e}" if n and e else n


def _doctors_order(items):
    ok = [m for m in (items or []) if m and m.get("nombre")]
    return sorted(ok, key=lambda m: (-(m.get("n") or 0), m["nombre"]))


def doctors_text(items):
    """Which doctor(s) a person has appointments with: one name, or the one
    with most appointments plus "+N" for the rest. `items` = [{nombre, n}].
    Mirror of format.mjs:medicosTexto."""
    o = _doctors_order(items)
    if not o:
        return ""
    return o[0]["nombre"] if len(o) == 1 else f"{o[0]['nombre']} +{len(o) - 1}"


def doctors_title(items):
    """All the doctors, for the tooltip. Mirror of format.mjs:medicosTitulo."""
    return ", ".join(m["nombre"] for m in _doctors_order(items))


def country_phone_json():
    """`PHONE` (the raw English-keyed country.json block) as JSON safe to
    embed inside a `<script>…</script>` — what the pages that load
    format.mjs provide as `window.SANA_COUNTRY_PHONE` before importing the
    module, so both runtimes normalize the exact same bytes. See
    patient_web/site_*.py's booking-wizard template and booking_requests.py's
    `_serve_admin`."""
    return json.dumps(PHONE, ensure_ascii=False).replace("<", "\\u003c")


def schedule_band(band, *, common=None):
    """Compact schedule line; no IDs or fabricated schedule defaults.
    ``common`` is the appointment length the page already said once: a band
    of that length drops its «turnos de N min» tail (the product owner, 02.11)."""
    from app.core import i18n
    keys = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']
    labels = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb']
    days = [i18n.t_or('date.day_short.' + keys[d], labels[d], i18n.active()) for d in [1, 2, 3, 4, 5, 6, 0] if d in (band.get('dias') or [])]
    if i18n.active() == 'es':
        days = [label.lower() for label in days]
    if not days:
        return ''
    joined = i18n.t_or('admin.doctor.days_join', '{first} y {last}', i18n.active(), first=', '.join(days[:-1]), last=days[-1]) if len(days) > 1 else days[0]
    if common and (band.get('cada_min') or 20) == common:
        return i18n.t_or('admin.doctor.schedule_line_short', '{days}, {from} a {to}', i18n.active(),
            days=joined[:1].upper() + joined[1:], **{'from': band.get('desde') or '', 'to': band.get('hasta') or ''})
    return i18n.t_or('admin.doctor.schedule_line', '{days}, {from} a {to} · turnos de {minutes} min', i18n.active(),
        days=joined[:1].upper() + joined[1:], **{'from': band.get('desde') or '', 'to': band.get('hasta') or '', 'minutes': band.get('cada_min') or 20})
