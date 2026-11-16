"""pack_l10n.py — the patient site's view of the data package, in the
visitor's language (2026-08-04).

`i18n.py` translates INTERFACE strings (curated, hand-written, one catalog per
language). `translate.py` translates PACK CONTENT (machine, cached in
Postgres). This module is the third piece those two always needed: the map of
WHICH fields of WHICH pack files are content a visitor reads, and the one
place that walks a loaded pack row and swaps them.

Without it, switching the demo to English produced a half-English page — the
chrome translated, every service name, cluster title, specialty and center
description still Spanish (the product owner, 2026-08-04: "we don't translate any content…
the specializations and so on… most of the content isn't translated").

Three rules the field map encodes:

  - **Names of PEOPLE are never translated.** Dr. Juan González is called
    that in every language. A CENTER's display `name`/`nombre` IS translated,
    though (2026-09-11): a centre's name is mostly generic words a patient
    reads — "Hospital Regional Sampleton" in a Russian answer, not the
    demo pack's English "Sampleton Regional Hospital" leaking untranslated
    into ru/pt prose. The batch translator's prompt already pins proper
    nouns and place names, so the distinctive part survives translation;
    `medicos.nombre` (a person) stays excluded on purpose.
  - **A service's search vocabulary (`palabras`) IS translated (2026-08-04).**
    This is the one deliberate exception to "search vocabulary stays put":
    The product owner's call was "if the page is in English, the chips are in English,
    searches in English, and results in English" — an English visitor
    typing "blood test" must find `laboratorio-sangre`, and that only
    happens if the matcher has an English synonym to hit. `palabras` is a
    LIST of short terms, not a sentence, so it's translated term by term
    (see `entries()`/`localize()`'s list branch below) — the English list is
    free to carry BOTH spellings where they differ ("ultrasound"/"sonogram",
    "pediatrics"/"paediatrics"), since redundancy in an index is free recall.
    The home page's example search CHIPS stay in pack language regardless
    (`site_identity.search_examples_html`, owned by `scripts/search_examples.py`)
    — those are literal queries proven against the Spanish catalog by
    `scripts/search_probe.js`, a different concern from the vocabulary a
    translated query is matched against.
  - **Identifiers are never translated.** id/slug/cluster/categoria/modo are
    wire values, not text.
  - **Strings the code PARSES are never translated.** `horario` ("Lunes a
    viernes de 08:00 a 12:00") looks like prose but is the input to
    hours.mjs/platform/week.mjs's day parser, which reads Spanish day names; translating
    it would empty the weekly grid on every service page. `consultorio` is a
    room code that renders after a separately-translated "Consultorio" label,
    and `sede` is a venue's name. All three stay canonical — the way to show
    an English weekly grid is to translate the day names at RENDER time, not
    the data.

Reads go through `translate.cached_text`, never the on-demand path: a page
render must never wait on a model call. `scripts/translate_pack.py` fills the
cache ahead of time, and anything it hasn't reached yet simply shows in the
pack's own language.
"""

import time

from app.core import i18n
from app.core import translate

# {kind: (fields that are patient-visible prose)}. `kind` is also the first
# segment of the cache key, so renaming one orphans its cached rows —
# deliberately stable, never derived from a filename.
#
# `palabras` is a LIST field (search synonyms), not a sentence — `entries()`
# and `localize()` below already walk list values generically (one cache
# entry per item, index-suffixed key), so adding it here is enough; no
# special-casing needed in either function. Only `servicios` carries
# `palabras` in any pack today (checked every pack's medicos.json — none
# do), so it's the only kind listed with it.
FIELDS = {
    # "pami" es la nota para pacientes con esa cobertura (2026-08-04: se
    # traducía el cartel pero no esta nota, y la demo en inglés la dejaba en
    # castellano en /service/<id>).
    "servicios": ("nombre", "top_label", "descripcion", "cartel", "nota", "palabras", "pami"),
    # `name` first, `nombre` for packs that spell it that way — either field
    # a centre's display name lives in is content, see the rule above.
    "centros": ("name", "nombre", "description", "jtbd", "jobs"),
    "clusters": ("nombre", "descripcion"),
    # A doctor's `nombre` is a person's name and stays put; the specialty is
    # the only thing on their card that is a word rather than a label.
    "medicos": ("especialidad", "especialidad_2"),
    # estudios-preparacion.json — "Cómo prepararte" (2026-08-04). `nombre`
    # here is the STUDY's name ("Análisis de sangre / laboratorio"), not a
    # person or a place, so unlike medicos/centros it's fair game. `antes`
    # replaced `preparacion` as the field name on 2026-07-29 (prep_model.py's
    # own docstring); both are listed because an older pack (or an entry
    # nobody's re-saved yet) may still carry the old one — a row missing a
    # field is silently skipped by `entries()`/`localize()`, never an error.
    # `match` (search keywords) and `id`/`sources` stay out on purpose: the
    # first is search vocabulary, the second are wire values/citation URLs.
    # `ayuno.nota` (dotted — see `_get_field`) is a fasting CAVEAT nested one
    # level inside the `ayuno` object (14/47 studies in the reference pack
    # carry one, e.g. "sólo el transesofágico pide ayuno"); the sibling keys
    # of that same object (`required`/`horas`/`agua_permitida`) are booleans
    # and numbers, not translatable text, so only `nota` is listed.
    "preparaciones": ("nombre", "resumen", "antes", "preparacion", "traer",
                       "avisar", "no_hace_falta", "despues", "ayuno.nota"),
    # enlaces.json (2026-08-27, hospital meeting — informational search
    # results: telemedicina/zoonosis/donar sangre). `palabras` is search
    # vocabulary, same exception as servicios' own `palabras` (an English
    # visitor's query has to hit the matcher too). The `pagina` bullet lists
    # Lists are translated item by item by the generic walker below. Official
    # link labels stay canonical because their target and label are authored
    # together by the installation.
    "enlaces": ("nombre", "descripcion", "palabras", "accion_texto", "disponibilidad",
                "pagina.lede", "pagina.que_es", "pagina.para", "pagina.no_para",
                "pagina.pasos", "pagina.no_es", "pagina.cta_texto", "pagina.cta_nota",
                "pagina.servicios", "pagina.castracion", "pagina.roedores",
                "pagina.mordedura", "pagina.nota", "pagina.cuerpo", "pagina.antes",
                "pagina.quienes"),
}

# Which field identifies a row inside its kind, in order of preference.
_ID_FIELDS = ("id", "slug", "nombre", "name")


def _ident(row):
    for field in _ID_FIELDS:
        value = row.get(field)
        if value:
            return str(value)
    return ""


def _get_field(row, field):
    """Reads `field` off `row` — a dotted name ("ayuno.nota") reads ONE level
    into a nested dict field instead of the row's own top level. Every field
    the rest of this module walks is either flat or one level deep; nothing
    here needs more than that."""
    if "." in field:
        outer, inner = field.split(".", 1)
        outer_val = row.get(outer)
        return outer_val.get(inner) if isinstance(outer_val, dict) else None
    return row.get(field)


def _set_field(row, field, value):
    """The write side of `_get_field` — `row` is already a shallow copy the
    caller owns, but a nested field's own dict is NOT (it's still the
    original's), so it gets copied here before the one key changes; every
    sibling key of that nested dict (e.g. `ayuno.required`/`horas`) survives
    untouched."""
    if "." in field:
        outer, inner = field.split(".", 1)
        nested = dict(row.get(outer) or {})
        nested[inner] = value
        row[outer] = nested
    else:
        row[field] = value


def key(kind, ident, field, index=None):
    """The cache key for one translatable value. Flat and stable, the same
    shape `translate.py`'s docstring documents for preparation summaries."""
    base = f"{kind}.{ident}.{field}"
    return base if index is None else f"{base}.{index}"


def entries(kind, rows):
    """[(cache_key, source_text)] for everything translatable in `rows` —
    what the sweep iterates, and the definition `localize` reads back."""
    out = []
    for row in rows or ():
        ident = _ident(row)
        if not ident:
            continue
        for field in FIELDS.get(kind, ()):
            value = _get_field(row, field)
            if isinstance(value, str) and value.strip():
                out.append((key(kind, ident, field), value))
            elif isinstance(value, list):
                for n, item in enumerate(value):
                    if isinstance(item, str) and item.strip():
                        out.append((key(kind, ident, field, n), item))
    return out


# A page calls `_services()`/`_centers()` thousands of times — once per doctor
# per slot computation — and got away with it because those loaders return an
# already-built list. Localizing turns each of those calls into a fresh list of
# ~80 dicts and ~400 catalog lookups, which took a doctors page from 0.3 s to
# never finishing (measured 2026-08-04). So the localized copy is memoized.
#
# Keyed on (kind, lang) and validated against the row identities, with a short
# TTL on top: an overlay edit to a row's TEXT (its id unchanged) shows up on
# the next expiry rather than instantly. That staleness only ever affects a
# non-canonical language — the canonical path returns before any of this —
# and 30 s is the same bound `translate.py`'s own read cache accepts.
_MEMO_TTL_S = 30
_memo = {}  # (kind, lang) -> (expires_at, ident_tuple, localized_rows)


def invalidate_memo():
    """Drop localized copies and their source rows after explicit invalidation."""
    _memo.clear()
    translate.invalidate_rows()


def localize_state(kind, lang=None):
    """(label, payload) — an O(1), side-effect-free snapshot of `localize()`'s
    memo for (kind, lang), the invalidation half for callers that memoize a
    LOCALIZED result across many reads (site_catalog._services): compare two
    snapshots' labels with `==` and their payloads with `is` — nothing else.

      ("no-fields", None) — the kind has nothing translatable (identity path)
      ("canonical", None) — the visitor reads the pack's own language;
                            localize() returns `rows` unchanged, no memo
      ("absent", None)    — nothing memoized yet for this (kind, lang)
      ("expired", entry)  — the memo aged out; the next localize() rebuilds
      ("live", entry)     — a memo entry younger than _MEMO_TTL_S

    `entry` is the very tuple stored in `_memo`, so identity is definitive: a
    rebuild (TTL expiry, a fresh cache-fill, invalidate_memo()) always stores
    a different object."""
    if not FIELDS.get(kind):
        return ("no-fields", None)
    lang = lang or i18n.active()
    if translate._primary(lang) == translate._primary(translate.pack_language()):
        return ("canonical", None)
    entry = _memo.get((kind, lang))
    if entry is None:
        return ("absent", None)
    if entry[0] <= time.monotonic():
        return ("expired", entry)
    return ("live", entry)


def localize(kind, rows, lang=None):
    """`rows` with their patient-visible fields swapped for the cached
    translation into `lang` (default: this request's language).

    Returns the rows UNCHANGED — the very same list object — when there is
    nothing to do: the visitor is reading the pack's own language, or the
    kind has no translatable fields. That identity is what keeps a
    single-language installation byte-for-byte what it was."""
    fields = FIELDS.get(kind)
    if not rows or not fields:
        return rows
    lang = lang or i18n.active()
    pack_lang = translate.pack_language()
    if translate._primary(lang) == translate._primary(pack_lang):
        return rows

    import time
    idents = tuple(_ident(r) for r in rows)
    cached = _memo.get((kind, lang))
    now = time.monotonic()
    if cached and cached[0] > now and cached[1] == idents:
        return cached[2]

    out = []
    for row in rows:
        ident = _ident(row)
        if not ident:
            out.append(row)
            continue
        copy = None
        for field in fields:
            value = _get_field(row, field)
            if isinstance(value, str) and value.strip():
                swapped = translate.cached_text(
                    key(kind, ident, field), value, lang, pack_lang=pack_lang)
                if swapped != value:
                    copy = dict(row) if copy is None else copy
                    _set_field(copy, field, swapped)
            elif isinstance(value, list) and value:
                items = list(value)
                changed = False
                for n, item in enumerate(items):
                    if not (isinstance(item, str) and item.strip()):
                        continue
                    swapped = translate.cached_text(
                        key(kind, ident, field, n), item, lang,
                        pack_lang=pack_lang)
                    if swapped != item:
                        items[n] = swapped
                        changed = True
                if changed:
                    copy = dict(row) if copy is None else copy
                    _set_field(copy, field, items)
        out.append(copy if copy is not None else row)
    _memo[(kind, lang)] = (now + _MEMO_TTL_S, idents, out)
    return out
