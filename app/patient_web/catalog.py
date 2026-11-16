"""catalog.py — Pinamar Turnos' editable content layer (Postgres overlay over
the base JSON).

The catalog's authored base is still static JSON (webdata/*.json, authored
from the municipal page). This module merges that base with the overlay the
admin edits (table `catalogo` in Postgres, see db.py):

  - a row with `borrado=false` for an id that ALREADY exists in the base JSON
    overrides the whole doc (override);
  - a row for an id that does NOT exist in the base JSON gets appended at
    the end;
  - a row with `borrado=true` hides the item (soft-delete) — the original
    stays intact in the file, "restoring" is simply deleting the row.

If the base goes down, `centers()`/`services()`/`doctors()` serve JSON
only — the public site must NEVER break because of this (lazy/tolerant
import of db, same criterion as the rest of the code). `admin_list()` is the
exception: admin_content.py uses it for the edit screen, and there we DO
want a connection error to SHOW (503) instead of displaying a possibly
stale list — that's why it queries the base in strict mode (lets
DbUnavailable through) and without cache.

Do NOT import patient_web/site_*.py here (would be circular — patient_web/site_*.py calls
this module, not the other way around). This module has its own mtime-based
`_load`, copied from site_base.py:35's pattern.
"""

import os
import re
import json
import datetime
import time
import math
import secrets
import threading
from urllib.parse import urlparse

from app.core import sitedata
from app.core import paths
from app.core import text_fold
from app.core import data_packs
from app.modules.turnos import prep_model

try:
    from app.core import db
    from app.core import db_catalog
except Exception:  # noqa: BLE001  (should never fail — db.py is import-safe)
    db = None

_DIR = paths.PROJECT_ROOT
_BASE_CREATED_ISO = "2026-07-08T00:00:00-03:00"

# ---------------------------------------------------------------- JSON base

_JSON_CACHE = {}


def _load_json(fname):
    # Cache key is the RESOLVED path, not the bare filename: with layered
    # data packs the same fname can resolve to a different file depending
    # on the active pack chain, so a filename-keyed cache would serve a
    # stale cross-pack collision instead of invalidating (2026-07-30).
    path = sitedata.path(fname)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _JSON_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _JSON_CACHE[path] = (mtime, data)
    return data


# type (value of catalogo.tipo in Postgres; admin_content.TYPE_MAP
# translates it from the English segment of /api/admin/content/<tipo>) -> file/list/key
_TYPES = {
    "centros":   {"file": "centros-salud.json", "list_key": "centros", "id_key": "slug"},
    "servicios": {"file": "servicios.json", "list_key": "servicios", "id_key": "id"},
    "medicos":   {"file": "medicos.json", "list_key": "medicos", "id_key": "id"},
    # The "How to prepare" instructions. They belong to the hospital, not the
    # code: if tomorrow the lab changes fasting from 8 to 6 hours, that gets
    # changed here, not in a deploy (the product owner, 2026-07-29).
    "preparaciones": {"file": "estudios-preparacion.json", "list_key": "estudios", "id_key": "id"},
    "conocimiento": {"file": "conocimiento.json", "list_key": "articles", "id_key": "id"},
    # Informational search results that lead somewhere OTHER than a
    # consultorio/service (telemedicina, zoonosis, donar sangre — 2026-08-27
    # hospital meeting). Whole-file override, same as centros/servicios: the
    # active pack supplies its own current list.
    "enlaces": {"file": "enlaces.json", "list_key": "enlaces", "id_key": "id"},
}

SERVICE_MODES = ("walkin", "walkin_con_orden", "solicitud", "solicitud_con_orden")
_NO_APPOINTMENT_MODES = ("walkin", "walkin_con_orden")

# ------------------------------------------------------- overlay key alias
#
# The DB overlay (Postgres `catalog` table, `doc` jsonb) stores an admin
# edit AS-IS, field names included. The base JSON files' field names are
# being renamed Spanish -> English one file at a time (see
# docs/architecture/data-packs.md, "THE central blocker for everything
# else"). The instant a base file's key is renamed, any overlay row saved
# BEFORE that rename still carries the OLD key in its jsonb — without
# translation, merge_with_overlay() would silently drop that field for
# every admin-edited item (reads as missing) while every un-edited item
# shows the new key, a real data-integrity bug, not just a naming one.
#
# Populated per `tipo` in the SAME commit that renames that type's base
# JSON keys — a tipo with no entry here (every tipo, until its own rename
# lands) means _apply_legacy_aliases() is a no-op for it, exactly like
# today's un-migrated merge behaviour. New writes from the admin panel
# need no code here: once a type's JS form is renamed alongside its base
# file (same commit), the payload it POSTs already uses the new keys, so
# the alias step is naturally an identity map for it going forward — this
# table only ever needs OLD -> NEW entries, never the reverse.
_LEGACY_KEY_ALIASES = {
    # centros-salud.json renamed 2026-07-30 (phase 2b slice 17): an overlay
    # row saved through the admin BEFORE this rename still has these old
    # Spanish keys in its jsonb `doc`.
    "centros": {
        "nombre": "name",
        "direccion": "address",
        "telefono": "phone",
        "descripcion": "description",
        "nota_ubicacion": "location_note",
    },
    # conocimiento.json renamed 2026-07-30 (phase 2b slice 18). Note "fuente"
    # is a nested dict, not a flat field — an overlay row saved before this
    # rename has BOTH the outer key AND its own inner keys in old form, but
    # _apply_legacy_aliases() only translates the TOP-LEVEL key ("fuente" ->
    # "source"); the nested dict's own keys (titulo/organismo/verificado_en/
    # revisar_cada_dias) stay whatever they were saved as. Acceptable: every
    # live reader of `source` (catalog._validate_knowledge, knowledge.py,
    # admin-knowledge.js) already reads the NEW inner keys post-rename, so an
    # old-key overlay row's `source` sub-dict silently loses its nested
    # fields the same way any other stale un-migrated jsonb would — a
    # pre-existing acceptable-risk pattern, not a new one introduced here.
    "conocimiento": {
        "tipo": "type",
        "titulo": "title",
        "resumen": "summary",
        "contenido": "content",
        "estado": "status",
        "jurisdiccion": "jurisdiction",
        "fuente": "source",
    },
    # estudios-preparacion.json renamed 2026-07-30 (phase 2b slice 19) — ONLY
    # these two top-level keys. `nombre`/`resumen`/`ayuno`/`antes`/`traer`/
    # `avisar`/`no_hace_falta`/`despues` are DELIBERATELY still Spanish (see
    # prep_model.py's own docstring: they're mirrored across prep_model.py's
    # public function names, prep.mjs's public function names, AND this
    # base JSON's field names on purpose, for copy-paste parity between the
    # Python/JS twin implementations — renaming the JSON without renaming
    # the two twin modules' public APIs would break that, and the twins'
    # public names are explicitly pinned Spanish). See
    # docs/architecture/data-packs.md, "estudios-preparacion.json field
    # rename", for the full reasoning.
    "preparaciones": {
        "fuentes": "sources",
        "sin_preparacion": "no_preparation_needed",
    },
}


def _apply_legacy_aliases(tipo, doc):
    """Translates any OLD Spanish keys found in `doc` (an overlay row's
    jsonb, already loaded) to their NEW English equivalents per
    `_LEGACY_KEY_ALIASES[tipo]`. A key already in new form (or any tipo/key
    with no mapping registered) passes through untouched. Never mutates
    the input dict."""
    aliases = _LEGACY_KEY_ALIASES.get(tipo)
    if not aliases:
        return dict(doc)
    return {aliases.get(k, k): v for k, v in doc.items()}


def id_key(tipo):
    """Name of a type's identity key: 'slug' for centers, 'id' for
    services/doctors."""
    return _TYPES[tipo]["id_key"]


def _base_items_raw(tipo):
    cfg = _TYPES[tipo]
    # A composable file (data_packs._COMPOSABLE, e.g. conocimiento.json since
    # phase 3b 2026-08-06) is COMPOSED across the active pack chain -- low
    # level wins by default, a higher pack's item with the same id_key
    # replaces it in place, and a `{"_removed": true}` entry tombstones one
    # out. Every other type keeps whole-file OVERRIDE via `_load_json()`
    # (backed by `sitedata.path()`/`data_packs.resolve_path()`).
    if data_packs.is_composable(cfg["file"]):
        return list(data_packs.compose(cfg["file"]))
    d = _load_json(cfg["file"]) or {}
    return d.get(cfg["list_key"]) or []


def base_items(tipo):
    """Items from the base JSON, as-is, with no overlay (to decide soft vs
    hard delete and to check whether an id 'exists in the base')."""
    return list(_base_items_raw(tipo))


# ---------------------------------------------------------------- DB overlay

_TTL = 3.0                 # seconds between DB queries (public read)
_DB_FAIL_COOLDOWN = 3.0    # if the DB failed, don't retry before this
_RAW_CACHE = {}            # tipo -> (ts, rows)
_DB_FAIL_UNTIL = {}        # tipo -> ts until which we don't retry
_RAW_CACHE_LOCK = threading.Lock()
_RAW_CACHE_GENERATION = 0


def invalidate():
    """Discards the merge cache. Call after every admin write."""
    global _RAW_CACHE_GENERATION
    with _RAW_CACHE_LOCK:
        _RAW_CACHE_GENERATION += 1
        _RAW_CACHE.clear()
        _DB_FAIL_UNTIL.clear()


def overlay_state(tipo):
    """(label, payload) — an O(1), side-effect-free snapshot of the CURRENT
    effective overlay inputs for `tipo`, read off the same caches `_db_rows`
    consults (never queries the DB, never iterates the rows). This is the
    invalidation half for callers that memoize a MERGED result across many
    reads (site_catalog._services): compare two snapshots' labels with `==`
    and their payloads with `is` — nothing else.

      ("live", rows)    — a cached DB read younger than the public TTL
      ("expired", rows) — that read aged out; the next merge re-queries
      ("db-fail", ts)   — inside a failure cooldown (merge falls back to base)
      ("none", None)    — nothing cached and not failing (first read, or
                          right after invalidate())

    `rows` is the very list object `_RAW_CACHE` holds and `ts` the very float
    `_DB_FAIL_UNTIL` holds, so identity is definitive: any fresh DB read,
    admin write (invalidate()) or new cooldown produces a different object."""
    now = time.time()
    with _RAW_CACHE_LOCK:
        fail_until = _DB_FAIL_UNTIL.get(tipo, 0)
        if fail_until > now:
            return ("db-fail", fail_until)
        cached = _RAW_CACHE.get(tipo)
        if cached is None:
            return ("none", None)
        if now - cached[0] >= _TTL:
            return ("expired", cached[1])
        return ("live", cached[1])


def _db_rows(tipo, strict=False):
    """Overlay rows for a type (all of them, including deleted ones).

    Lenient mode (default, used by the public site): caches for 3s, and if
    the base fails returns None without retrying for a few seconds (doesn't
    hammer the connection) — the caller falls back to the base JSON alone.
    Strict mode (admin_list): no cache, lets DbUnavailable through — the
    caller (admin_content._db_guard) responds 503.
    """
    if strict:
        if db is None:
            raise RuntimeError("db module not available")
        return db_catalog.catalog_list(tipo)

    # Database I/O stays outside the lock.  An admin write can invalidate a
    # read while it is in flight; that old response must neither repopulate
    # the overlay nor restore a failure cooldown after the write completes.
    while True:
        now = time.time()
        with _RAW_CACHE_LOCK:
            cached = _RAW_CACHE.get(tipo)
            if cached and now - cached[0] < _TTL:
                return cached[1]
            if _DB_FAIL_UNTIL.get(tipo, 0) > now:
                return None
            generation = _RAW_CACHE_GENERATION
        if db is None:
            with _RAW_CACHE_LOCK:
                if generation != _RAW_CACHE_GENERATION:
                    continue
                _DB_FAIL_UNTIL[tipo] = now + _DB_FAIL_COOLDOWN
                return None
        try:
            rows = db_catalog.catalog_list(tipo)
        except db.DbUnavailable:
            with _RAW_CACHE_LOCK:
                if generation != _RAW_CACHE_GENERATION:
                    continue
                _DB_FAIL_UNTIL[tipo] = now + _DB_FAIL_COOLDOWN
                return None
        with _RAW_CACHE_LOCK:
            if generation != _RAW_CACHE_GENERATION:
                continue
            _RAW_CACHE[tipo] = (now, rows)
            return rows


def merge_with_overlay(tipo, base, incluir_borrados=False, strict=False, incluir_incompletos=None):
    """(doc_list, meta) — merges a list of base items ALREADY LOADED (by
    whoever owns it) with `tipo`'s DB overlay. Kept separate from `_merged`
    so site_base.py can keep owning its own base-JSON `_load`/cache (its
    tests monkeypatch `site_base._load`) and only ask this module to do the
    overlay-merge step — without this split, changing
    site_centers._centers()/_services()/_doctors() to go through catalog.py
    would have made that monkeypatch inert (broke test_centros_tiles.py).

    meta[id] = {'_origen', '_borrado', '_created', '_updated'}. Preserves
    `base`'s order for its ids; DB-only ids go at the end, sorted by id
    (stable)."""
    key = id_key(tipo)
    base_ids = {it.get(key) for it in base}
    rows = _db_rows(tipo, strict=strict) or []
    rows_by_id = {r["id"]: r for r in rows}

    out = []
    meta = {}
    for it in base:
        iid = it.get(key)
        row = rows_by_id.get(iid)
        if row is not None:
            borrado = bool(row.get("borrado"))
            if borrado and not incluir_borrados:
                continue
            doc = _apply_legacy_aliases(tipo, row["doc"] or {})
            doc[key] = iid
            out.append(doc)
            creado = row.get("created")
            actualizado = row.get("updated")
            meta[iid] = {
                "_origen": "json+db",
                "_borrado": borrado,
                "_created": creado.isoformat() if hasattr(creado, "isoformat") else (creado or _BASE_CREATED_ISO),
                "_updated": actualizado.isoformat() if hasattr(actualizado, "isoformat") else (actualizado or _BASE_CREATED_ISO),
            }
        else:
            out.append(dict(it))
            meta[iid] = {
                "_origen": "json",
                "_borrado": False,
                "_created": _BASE_CREATED_ISO,
                "_updated": _BASE_CREATED_ISO,
            }

    nuevos = sorted((r for iid, r in rows_by_id.items() if iid not in base_ids),
                    key=lambda r: r["id"])
    for row in nuevos:
        iid = row["id"]
        borrado = bool(row.get("borrado"))
        if borrado and not incluir_borrados:
            continue
        doc = _apply_legacy_aliases(tipo, row["doc"] or {})
        doc[key] = iid
        out.append(doc)
        creado = row.get("created")
        actualizado = row.get("updated")
        meta[iid] = {
            "_origen": "db",
            "_borrado": borrado,
            "_created": creado.isoformat() if hasattr(creado, "isoformat") else (creado or _BASE_CREATED_ISO),
            "_updated": actualizado.isoformat() if hasattr(actualizado, "isoformat") else (actualizado or _BASE_CREATED_ISO),
        }

    # A doctor still missing name, specialty or center (created or invited
    # empty, onboarding pending) exists for the panel and its account, never
    # for patients: the public reads leave it out. The admin list (which
    # already includes deleted rows) includes it by default.
    if incluir_incompletos is None:
        incluir_incompletos = incluir_borrados
    if tipo == "medicos" and not incluir_incompletos:
        out = [d for d in out if d.get("incompleto") is not True]

    return out, meta


def _merged(tipo, incluir_borrados=False, strict=False, incluir_incompletos=None):
    """Same as `merge_with_overlay`, but loading this module's own base
    (webdata/*.json via _load_json) — what centers()/services()/doctors()/
    admin_list() use."""
    extra = {} if incluir_incompletos is None else {"incluir_incompletos": incluir_incompletos}
    return merge_with_overlay(tipo, _base_items_raw(tipo),
                              incluir_borrados=incluir_borrados, strict=strict, **extra)


def edited_dates(tipo):
    """{id: "YYYY-MM-DD"} of the items whose data the admin actually edited
    (an overlay row with its own `updated` timestamp), for the sitemap's
    <lastmod>. A base-JSON-only item has no real modification date —
    `_BASE_CREATED_ISO` is a placeholder — so it is left out rather than
    advertised with an invented one."""
    from datetime import datetime
    from app.core import tz
    _, meta = _merged(tipo)
    out = {}
    for iid, m in meta.items():
        iso = m.get("_updated")
        if m.get("_origen") not in ("json+db", "db") or not iso or iso == _BASE_CREATED_ISO:
            continue
        try:
            when = datetime.fromisoformat(iso)
        except ValueError:
            continue
        # The installation's own calendar day, never the offset Postgres
        # handed back (CLAUDE.md: datetimes always go through tz.py).
        if when.tzinfo is not None:
            when = when.astimezone(tz.tzinfo())
        out[iid] = when.date().isoformat()
    return out


def known_ids(tipo):
    """Every id the catalog of `tipo` ("centros", "servicios", "medicos") has
    ever resolved — the disabled and the still-incomplete ones included: a row
    keeps the id of something that was switched off, and it still reads. Strict
    (a catalog that cannot be read raises instead of answering «none»), for
    `catalog_refs`'s check that the ids stored in the database are still there."""
    key = id_key(tipo)
    extra = {"incluir_incompletos": True} if tipo == "medicos" else {}
    out, _ = _merged(tipo, incluir_borrados=True, strict=True, **extra)
    return {str(it[key]) for it in out if isinstance(it, dict) and it.get(key)}


def live_ids(tipo):
    """The ids of `tipo` that are live right now — what `centers()`,
    `services()` and `doctors()` answer: not switched off or deleted (the
    overlay's `borrado`), and for doctors not still waiting for onboarding.
    Strict like `known_ids` (a catalog that cannot be read raises). For a check
    that something may still be assigned a request, where `known_ids` only says
    the id once existed."""
    key = id_key(tipo)
    out, _ = _merged(tipo, strict=True)
    return {str(it[key]) for it in out if isinstance(it, dict) and it.get(key)}


def expire_center_locations(rows, today=None):
    """A center that is in a place only until a date (`location_until`, the
    mobile unit's school) loses that place the day after: the address and the
    map link go, the rest of the center stays (its `location_note` says the
    place varies). By the installation's calendar day (core/clock.py), so
    nobody has to remember to remove it. Rows without a date come back as they
    are; an unreadable date is left alone, never hides a place."""
    from app.core import clock
    day = (today or clock.today()).isoformat()
    out = []
    for c in rows:
        until = str(c.get("location_until") or "")[:10] if isinstance(c, dict) else ""
        if until and until < day:
            c = {k: v for k, v in c.items() if k not in ("address", "maps_url", "location_until")}
        out.append(c)
    return out


def centers():
    out, _ = _merged("centros")
    return expire_center_locations(out)


def services():
    out, _ = _merged("servicios")
    return out


def doctors(incluir_incompletos=False, incluir_deshabilitados=False):
    """Live doctors. `incluir_incompletos=True` adds the ones still waiting
    for onboarding (no name/specialty/center) — only account code wants
    those; every patient-facing reader keeps the default.
    `incluir_deshabilitados=True` adds the disabled ones too — only for
    matching history (scripts/medico_por_id.py)."""
    out, _ = _merged("medicos", incluir_borrados=bool(incluir_deshabilitados),
                     incluir_incompletos=True if incluir_incompletos else None)
    return out


# A test doctor (the owner's own «Laura Prueba (test)», a staff member trying the
# doctor's screens) is a complete catalog row: name, specialty, center and a
# schedule, so the `incompleto` cut lets it through. It exists for the panel,
# the desk and its own account, never for patients (pinamar review 2026-11-15,
# item 1: it was bookable on the public site and counted on the home page).
# Two signals, either is enough: the explicit flag `prueba: true`, or the
# marker the staff already write into the name — «(test)» / «(prueba)».
_TEST_NAME_RE = re.compile(r"\(\s*(?:test|prueba)\s*\)", re.IGNORECASE)


def is_test_doctor(doctor):
    """True for a doctor row that must never reach a public surface (lists,
    counts, search, sitemap, the doctor's page, the slots a patient is offered,
    a patient's booking). The panel and the doctor's own account still see it."""
    if not isinstance(doctor, dict):
        return False
    if doctor.get("prueba") is True:
        return True
    return bool(_TEST_NAME_RE.search(str(doctor.get("nombre") or "")))


def public_doctors():
    """`doctors()` minus the test doctors: what a patient-facing reader that
    does not go through `site_catalog._doctors()` lists (sitemap, chat)."""
    return [m for m in doctors() if not is_test_doctor(m)]


def doctor_doc(medico_id, incluir_incompletos=False, tolerant=False):
    """The live catalog doctor whose id is `medico_id` (compared as text), or
    None — no id, no match. The ONE id lookup over `doctors()`; callers that
    must also see disabled doctors use `doctor_by_id`. `tolerant=True` is for
    a best-effort read that must never break its caller: a catalog that cannot
    be read answers None instead of raising."""
    if not medico_id:
        return None
    try:
        rows = doctors(incluir_incompletos=True) if incluir_incompletos else doctors()
        for m in rows:
            if str(m.get("id")) == str(medico_id):
                return m
    except Exception:  # noqa: BLE001 — only swallowed when the caller asked for it
        if not tolerant:
            raise
    return None


def doctor_specialty(medico_id):
    """The specialty of a live catalog doctor, trimmed, or None: no id, no such doctor, an unreadable catalog
    (best-effort, never raises) or no usable specialty text."""
    value = (doctor_doc(medico_id, tolerant=True) or {}).get("especialidad")
    return (value.strip() or None) if isinstance(value, str) else None


def license_number(doctor):
    """The licence number of a catalog doctor (`matricula` is a bare string or a `{"numero": ...}` dict), or
    None when there is none."""
    mat = doctor.get("matricula")
    if isinstance(mat, dict):
        return mat.get("numero") or None
    if isinstance(mat, str) and mat.strip():
        return mat.strip()
    return None


# ------------------------------------------------------- readable labels ----
#
# A booking row keeps the service id and the center slug forever; the catalog
# does not. When the service is later deleted, or the row came from another
# installation's data package, `services()` no longer knows it and every
# screen that trusted the lookup fell back to printing the raw id
# ("cardiologia-sampleton-hospital") at the front desk (the product owner, 2026-08-01).
# These two resolve the best readable name available and NEVER return an id:
#   1. the live catalog,
#   2. the catalog including deleted entries (a deleted service still has its
#      name — that appointment happened),
#   3. for services only, the specialty catalog, which is where the properly
#      accented Spanish name lives ("cardiologia-…" -> "Cardiología"),
#   4. the id itself, humanized (dashes out, first letter up).


def _find(tipo, campo, valor):
    for incluir in (False, True):
        items, _ = _merged(tipo, incluir_borrados=incluir)
        for it in items:
            if it.get(campo) == valor:
                return it
    return None


def _humanize(raw):
    texto = re.sub(r"[-_]+", " ", str(raw or "")).strip()
    return texto[:1].upper() + texto[1:] if texto else ""


def service_by_id(sid):
    """The service, deleted ones included — for `consultorio` and friends."""
    return _find("servicios", "id", sid) if sid else None


def service_name(sid):
    if not sid:
        return ""
    svc = service_by_id(sid)
    if svc and svc.get("nombre"):
        return svc["nombre"]
    # The id is built as "<especialidad>-<centro>": the specialty catalog has
    # the accented name, so a service the catalog forgot still reads right.
    mejor = ""
    for esp in specialties() or []:
        slug = _slug(esp.get("nombre"))
        if not slug or not str(sid).startswith(slug):
            continue
        if len(str(sid)) == len(slug) or str(sid)[len(slug)] == "-":
            if len(slug) > len(_slug(mejor)):
                mejor = esp.get("nombre") or ""
    return mejor or _humanize(sid)


def doctor_by_id(mid):
    """The doctor behind a request's id: the live catalog with the incomplete
    ones, then the disabled ones — a request keeps its doctor's id after the
    doctor is disabled (2026-10-24). None for an id the catalog never had."""
    if not mid:
        return None
    for incluir in (False, True):
        items, _ = _merged("medicos", incluir_borrados=incluir, incluir_incompletos=True)
        for it in items:
            if it.get("id") == mid and it.get("nombre"):
                return it
    return None


def doctor_name(mid):
    """A request keeps the doctor's id after the doctor is disabled, and a
    doctor still waiting for onboarding has a name already: the live catalog
    with the incomplete ones, then the disabled ones, then the id humanized —
    never the raw id."""
    if not mid:
        return ""
    medico = doctor_by_id(mid)
    return medico["nombre"] if medico else _humanize(mid)


def center_name(slug):
    if not slug:
        return ""
    centro = _find("centros", "slug", slug)
    return (centro.get("name") if centro else None) or _humanize(slug)


def center_hsi_institution(slug):
    """The center's own institution number in HSI ("1086"), or "" when the
    center has none: each center is its own institution there, and the desk's
    HSI extension opens the one of the row it loads."""
    if not slug:
        return ""
    centro = _find("centros", "slug", slug)
    return str((centro or {}).get("hsi_institution") or "")


def preparations():
    out, _ = _merged("preparaciones")
    return out


def links():
    """Informational search results (telemedicina, zoonosis, donar sangre…):
    same override-pack contract as centros/servicios/medicos. Missing
    enlaces.json (a pack that hasn't defined any) -> no results, never a
    crash — search must never break because a locality has none of these
    yet."""
    out, _ = _merged("enlaces")
    return out


def link_by_id(lid):
    if not lid:
        return None
    for e in links():
        if e.get("id") == lid:
            return e
    return None


def knowledge():
    """The assistant's editorial tree (categories and RAG articles)."""
    out, _ = _merged("conocimiento")
    return out


def specialties():
    """Canonical specialty -> cluster catalog (especialidades.json). The
    admin picks the specialty from a closed list; the doctor's cluster is
    ALWAYS derived from here (server-side, never trust a cluster the client
    sends) — see _validate_doctor.

    A COMPOSABLE file (data_packs.py's compose registry, phase 3 2026-07-30):
    merged across the active pack chain by `nombre`, not a whole-file
    override — see data_packs.py's module docstring."""
    return data_packs.compose("especialidades.json")


def _specialty_cluster(nombre):
    for e in specialties():
        if e.get("nombre") == nombre:
            return e.get("cluster")
    return None


def doctor_clusters(m):
    """Every cluster a doctor belongs to: the first specialty's, then the
    second specialty's when it lands in another cluster. The one place that
    answers «is this doctor in cluster X» — never compare `m["cluster"]`."""
    out = []
    for key in ("cluster", "cluster_2"):
        cid = (m or {}).get(key)
        if cid and cid not in out:
            out.append(cid)
    return out


def admin_list(tipo):
    """List for the admin's edit screen: every merged doc (including
    deleted ones) + per-item metadata (_origen, _borrado). Strict mode: if
    the base isn't available, lets DbUnavailable through — we don't want
    the admin editing over a stale snapshot thinking it's current."""
    key = id_key(tipo)
    out, meta = _merged(tipo, incluir_borrados=True, strict=True)
    items = []
    for it in out:
        d = dict(it)
        m = meta.get(it.get(key)) or {
            "_origen": "json",
            "_borrado": False,
            "_created": _BASE_CREATED_ISO,
            "_updated": _BASE_CREATED_ISO,
        }
        d["_origen"] = m["_origen"]
        d["_borrado"] = m["_borrado"]
        d["_created"] = m["_created"]
        d["_updated"] = m["_updated"]
        items.append(d)
    return items


# ---------------------------------------------------------------- slugs

def _slug(s):
    s = text_fold.ascii_fold(str(s or "")).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# ---------------------------------------------------------------- validation

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s


def _validate_quotas(cupos):
    """Explicit capacity per time band.

    Only ``web`` enables a booking on the public site. ``reservado`` and
    ``sobreturnos`` are deliberately kept out of that offer: they're
    capacity for staff, to be assigned from the admin agenda. Until that
    assignment exists, they never turn into a second web appointment.
    """
    if cupos is None:
        return None, None
    if not isinstance(cupos, dict):
        return None, "cupos debe ser un objeto {web, reservado, sobreturnos, overbooking_percent}"
    desconocidos = set(cupos) - {"web", "reservado", "sobreturnos", "overbooking_percent"}
    if desconocidos:
        return None, "cupos sólo admite web, reservado, sobreturnos y overbooking_percent"
    out = {}
    for clave in ("web", "reservado", "sobreturnos", "overbooking_percent"):
        # The percentage is additive.  Its absence deliberately preserves the
        # legacy per-minute ``sobreturnos`` behavior instead of normalizing an
        # old catalog row into an explicit disabled percentage.
        if clave == "overbooking_percent" and clave not in cupos:
            continue
        valor = cupos.get(clave)
        if clave == "web" and (valor is None or valor == ""):
            out[clave] = None
            continue
        if isinstance(valor, bool):
            return None, f"cupos.{clave} debe ser un entero"
        if isinstance(valor, float):
            if not math.isfinite(valor) or not valor.is_integer():
                return None, f"cupos.{clave} debe ser un entero"
        try:
            numero = int(valor)
        except (TypeError, ValueError, OverflowError):
            return None, f"cupos.{clave} debe ser un entero"
        if numero < 0:
            return None, f"cupos.{clave} no puede ser negativo"
        out[clave] = numero
    return out, None


def _validate_schedule(agenda):
    """{dias, desde, hasta, cada_min} -> (normalized_schedule, error|None).
    `dias` use the JS `Date.getDay()` convention (0=Sunday..6=Saturday) —
    that's how the admin's editor sends it (admin-contenido.js: DIAS_UI,
    confirmed against horarios.js/semana.js and demo-aguirre-marcelo's
    agenda_demo.dias=[1,4]=Monday,Thursday, which matches both conventions
    except for Sunday). site_availability._demo_next matches with
    `isoweekday() % 7` to line up with this (Python's isoweekday() gives
    Sunday=7, not 0)."""
    if not isinstance(agenda, dict):
        return None, "agenda debe ser un objeto {dias, desde, hasta, cada_min}"
    dias = agenda.get("dias")
    if not isinstance(dias, list) or not dias:
        return None, "agenda.dias debe ser una lista no vacía de días (0=domingo..6=sábado)"
    try:
        dias_norm = sorted({int(d) for d in dias})
    except (TypeError, ValueError):
        return None, "agenda.dias debe contener números"
    if any(d < 0 or d > 6 for d in dias_norm):
        return None, "agenda.dias debe ser 0 (domingo) a 6 (sábado)"
    desde = _str(agenda.get("desde")) or "09:00"
    hasta = _str(agenda.get("hasta")) or "12:00"
    if not _HHMM_RE.match(desde) or not _HHMM_RE.match(hasta):
        return None, "agenda.desde/hasta deben tener formato HH:MM"
    # With the HH:MM format already validated, string comparison sorts
    # correctly. An inverted window (12:00→09:00) would pass silently and
    # produce zero slots in _demo_next — better to reject it here with a
    # clear message.
    if desde >= hasta:
        return None, "agenda.desde debe ser anterior a agenda.hasta"
    cada_raw = agenda.get("cada_min")
    if cada_raw is None or cada_raw == "":
        cada_raw = 20
    try:
        cada = int(cada_raw)
    except (TypeError, ValueError):
        return None, "agenda.cada_min debe ser un entero"
    if cada <= 0:
        return None, "agenda.cada_min debe ser mayor a 0"
    out = {"dias": dias_norm, "desde": desde, "hasta": hasta, "cada_min": cada}
    cupos, err = _validate_quotas(agenda.get("cupos"))
    if err:
        return None, err
    if cupos is not None:
        out["cupos"] = cupos
    return out, None


def _validate_time_bands(franjas):
    """List of a doctor's time bands at ONE center — each band has the same
    shape as `_validate_schedule` ({dias,desde,hasta,cada_min}), but here
    there can be SEVERAL: so a doctor can see patients Monday/Thursday 9-12
    AND Tuesday 14-17 at the same center (the product owner, 2026-07-11: "they might have
    different schedules on different days"). Can come in empty (a doctor who
    only sees patients on ad-hoc exceptions, with no fixed weekly band)."""
    if franjas is None:
        return [], None
    if not isinstance(franjas, list):
        return None, "franjas debe ser una lista de {dias, desde, hasta, cada_min}"
    out = []
    for i, fr in enumerate(franjas):
        norm, err = _validate_schedule(fr)
        if err:
            return None, f"franja {i + 1}: {err}"
        # Band's service (2026-07-24): the same doctor does DIFFERENT
        # services at different times/centers (Lazcano: contraception on
        # Mondays, general medicine on Tuesdays, testing another day).
        # Optional — with no service the band behaves as always. If it's
        # set, it has to be a real catalog id (the editor's combo suggests
        # one, but here a typo or a deleted id gets caught).
        if isinstance(fr, dict) and _str(fr.get("servicio")):
            svc = _str(fr.get("servicio"))
            if not any(str(s.get("id")) == svc for s in services()):
                return None, f"franja {i + 1}: servicio desconocido '{svc}'"
            norm = dict(norm)
            norm["servicio"] = svc
        out.append(norm)
    # Percentage budgets are per band.  Unlike legacy fixed extras, two
    # bands sharing a weekday and clock range have no stable
    # identity in stored appointments, so reject that ambiguous configuration.
    for i, left in enumerate(out):
        for j, right in enumerate(out[i + 1:], start=i + 1):
            percentage_enabled = any(int((band.get("cupos") or {}).get("overbooking_percent") or 0) > 0
                                     for band in (left, right))
            if (percentage_enabled and set(left.get("dias") or ()) & set(right.get("dias") or ())
                    and left.get("desde") < right.get("hasta")
                    and right.get("desde") < left.get("hasta")):
                return None, _overlap_message(left, right)
    return out, None


def _band_label(band):
    """The band as the person typed it — days, hours, interval and service —
    so an error names the rows to fix instead of their index (on-site tester,
    2026-10-21: "franjas 1 y 2" did not say which hours collide)."""
    from app.core import format as fmt
    line = fmt.schedule_band(band)
    svc = _str(band.get("servicio"))
    if svc:
        nombre = next((_str(s.get("nombre")) for s in services() if str(s.get("id")) == svc), "")
        if nombre:
            line += f" ({nombre})"
    return line


def _overlap_message(left, right):
    from app.core import i18n
    return i18n.t_or(
        "admin.content.hours.overlap_overbooking",
        "Los horarios «{first}» y «{second}» comparten horas. Para dar sobreturnos "
        "por porcentaje, cada horario necesita sus propias horas: cambiá las horas "
        "de uno de los dos o apagá Sobreturnos.",
        i18n.active(), first=_band_label(left), second=_band_label(right))


_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXCEPTION_TYPES = ("cerrado", "extra", "bloqueado")


def _validate_exception(exc):
    """Schedule exceptions: a one-off or ranged closure, an extra band, and
    a partial block. Blocks only pull those time slots off the public site;
    they don't create additional capacity or change the rest of the day."""
    if not isinstance(exc, dict):
        return None, "excepción debe ser un objeto {fecha o rango, tipo, ...}"
    fecha = _str(exc.get("fecha"))
    tipo = _str(exc.get("tipo"))
    if tipo not in _EXCEPTION_TYPES:
        return None, "excepcion.tipo debe ser 'cerrado', 'extra' o 'bloqueado'"
    desde_fecha = _str(exc.get("desde_fecha"))
    hasta_fecha = _str(exc.get("hasta_fecha"))
    if fecha:
        if not _FECHA_RE.match(fecha):
            return None, "excepcion.fecha debe tener formato YYYY-MM-DD"
        if desde_fecha or hasta_fecha:
            return None, "una excepción usa fecha o desde_fecha/hasta_fecha, no ambas"
        out = {"fecha": fecha, "tipo": tipo}
    else:
        if tipo != "cerrado":
            return None, "las excepciones extra o bloqueado necesitan una fecha"
        if (not desde_fecha or not hasta_fecha or not _FECHA_RE.match(desde_fecha)
                or not _FECHA_RE.match(hasta_fecha)):
            return None, "desde_fecha y hasta_fecha deben tener formato YYYY-MM-DD"
        if desde_fecha > hasta_fecha:
            return None, "desde_fecha debe ser anterior o igual a hasta_fecha"
        out = {"desde_fecha": desde_fecha, "hasta_fecha": hasta_fecha, "tipo": tipo}
    motivo = _str(exc.get("motivo"))
    if motivo:
        out["motivo"] = motivo
    if tipo in ("extra", "bloqueado"):
        desde = _str(exc.get("desde")) or "09:00"
        hasta = _str(exc.get("hasta")) or "12:00"
        if not _HHMM_RE.match(desde) or not _HHMM_RE.match(hasta):
            return None, "excepcion.desde/hasta deben tener formato HH:MM"
        if desde >= hasta:
            return None, "excepcion.desde debe ser anterior a excepcion.hasta"
        out.update({"desde": desde, "hasta": hasta})
        if tipo == "extra":
            cada_raw = exc.get("cada_min")
            if cada_raw is None or cada_raw == "":
                cada_raw = 20
            try:
                cada = int(cada_raw)
            except (TypeError, ValueError):
                return None, "excepcion.cada_min debe ser un entero"
            if cada <= 0:
                return None, "excepcion.cada_min debe ser mayor a 0"
            out["cada_min"] = cada
    return out, None


def _validate_exceptions(excepciones):
    if excepciones is None:
        return [], None
    if not isinstance(excepciones, list):
        return None, "excepciones debe ser una lista"
    out = []
    for i, exc in enumerate(excepciones):
        norm, err = _validate_exception(exc)
        if err:
            return None, f"excepción {i + 1}: {err}"
        out.append(norm)
    return out, None


def _validate_doctor_center(entry, centros_validos):
    """An item of `centros[]`: where they see patients + their own schedule
    at that center (bands + exceptions) — so a doctor who sees patients at
    two centers can have different hours at each."""
    if not isinstance(entry, dict):
        return None, "cada centro debe ser un objeto {centro, franjas, excepciones}"
    centro = _str(entry.get("centro"))
    if not centro:
        return None, "falta centro"
    if centro not in centros_validos:
        return None, f"el centro '{centro}' no existe"
    franjas, err = _validate_time_bands(entry.get("franjas"))
    if err:
        return None, err
    excepciones, err = _validate_exceptions(entry.get("excepciones"))
    if err:
        return None, err
    out = {"centro": centro, "franjas": franjas, "excepciones": excepciones}
    # This doctor's booking window AT this center (empty = inherit the doctor's,
    # the service's, the site's): same 1-365 rule as the other levels.
    raw = entry.get("dias_anticipacion")
    if raw is not None and not (isinstance(raw, str) and not raw.strip()):
        from app.modules.turnos import booking_horizon
        n = booking_horizon._clamp(raw)
        if n is None or str(n) != str(raw).strip():
            return None, "dias_anticipacion va de 1 a 365"
        out["dias_anticipacion"] = n
    return out, None


def _validate_center(doc, is_new):
    nombre = _str(doc.get("name"))
    if not nombre:
        return None, "falta nombre"
    doc["name"] = nombre
    slug = _str(doc.get("slug")) or _slug(nombre)
    if not slug:
        return None, "no se pudo generar un slug del nombre"
    doc["slug"] = slug
    if "bookable" in doc and doc["bookable"] is not None:
        doc["bookable"] = bool(doc["bookable"])
    if "jobs" in doc and doc["jobs"] is not None:
        if not isinstance(doc["jobs"], list):
            return None, "jobs debe ser una lista de strings"
        doc["jobs"] = [x for x in (_str(j) for j in doc["jobs"]) if x]
    for k in ("address", "phone", "whatsapp", "email", "description", "jtbd", "maps_url", "locality"):
        if k in doc and doc[k] is not None:
            doc[k] = _str(doc[k])
    # Until when the place is valid (a mobile unit's school): an ISO day, or
    # blank for a permanent place.
    until = _str(doc.get("location_until"))
    if not until:
        doc.pop("location_until", None)
    else:
        try:
            datetime.date.fromisoformat(until)
        except ValueError:
            return None, "la fecha «hasta» de la ubicación debe ser un día válido (AAAA-MM-DD)"
        doc["location_until"] = until
    # Its institution in HSI: the number in HSI's own addresses
    # (…/institucion/1086/…). Blank = not known yet. ASCII digits only —
    # `str.isdigit()` also accepts Unicode digits HSI never uses.
    hsi = re.sub(r"\s+", "", _str(doc.get("hsi_institution")) or "")
    if not hsi:
        doc.pop("hsi_institution", None)
    elif not re.fullmatch(r"[0-9]{1,9}", hsi) or not hsi.strip("0"):
        return None, "la institución en HSI es un número (por ejemplo 1086)"
    else:
        # One spelling per institution: «01086» and «1086» are the same
        # address in HSI, and the extension compares the text.
        doc["hsi_institution"] = str(int(hsi))
    # Coordinates (the home's route button goes by them when present): both or
    # neither, real numbers on the globe.
    lat, lng = doc.get("lat"), doc.get("lng")
    if lat in (None, "") and lng in (None, ""):
        doc.pop("lat", None)
        doc.pop("lng", None)
    else:
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return None, "las coordenadas deben ser dos números (latitud y longitud)"
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None, "las coordenadas están fuera de rango"
        doc["lat"], doc["lng"] = lat, lng
    return doc, None


def _validate_service(doc, is_new):
    nombre = _str(doc.get("nombre"))
    if not nombre:
        return None, "falta nombre"
    doc["nombre"] = nombre

    centro = _str(doc.get("centro"))
    if not centro:
        return None, "falta centro"
    centros_validos = {c.get("slug") for c in centers()}
    if centro not in centros_validos:
        return None, f"el centro '{centro}' no existe"
    doc["centro"] = centro

    sid = _str(doc.get("id")) or _slug(nombre)
    if not sid:
        return None, "no se pudo generar un id del nombre"
    doc["id"] = sid

    modo = _str(doc.get("modo"))
    if modo not in SERVICE_MODES:
        return None, "modo inválido: debe ser uno de %s" % ", ".join(SERVICE_MODES)
    doc["modo"] = modo

    if "edad" in doc and doc["edad"] is not None:
        edad = doc["edad"]
        if not isinstance(edad, dict):
            return None, "edad debe ser un objeto {min, max}"
        norm = {}
        for k in ("min", "max"):
            v = edad.get(k)
            if v is None or v == "":
                norm[k] = None
            else:
                try:
                    norm[k] = int(v)
                except (TypeError, ValueError):
                    return None, f"edad.{k} debe ser un entero"
        doc["edad"] = norm

    if "palabras" in doc and doc["palabras"] is not None:
        if not isinstance(doc["palabras"], list):
            return None, "palabras debe ser una lista de strings"
        doc["palabras"] = [x for x in (_str(p) for p in doc["palabras"]) if x]

    # palabras_excluidas: searches where the row must NOT appear (2026-07-17
    # municipal meeting: "so their office doesn't show up for a Pap smear
    # search"). Same format as `palabras`; search.js consumes it (excluida()).
    if "palabras_excluidas" in doc and doc["palabras_excluidas"] is not None:
        if not isinstance(doc["palabras_excluidas"], list):
            return None, "palabras_excluidas debe ser una lista de strings"
        doc["palabras_excluidas"] = [
            x for x in (_str(p) for p in doc["palabras_excluidas"]) if x
        ]

    for k in ("horario", "cartel", "pami", "categoria", "cluster", "sede", "top_label"):
        if k in doc and doc[k] is not None:
            doc[k] = _str(doc[k])

    if "top" in doc:
        if doc["top"] is None or doc["top"] == "":
            doc["top"] = None
        else:
            try:
                doc["top"] = int(doc["top"])
            except (TypeError, ValueError):
                return None, "top debe ser un entero"

    # Auto-confirmation (2026-07-24 hospital meeting: "with an appointment,
    # no referral needed, and no hospital validation" — e.g. a health
    # checkup): the chosen slot is approved instantly, without going
    # through the queue. The product owner (2026-07-24, round 2): referral / appointment
    # / approval are THREE independent knobs — with a referral it can also
    # auto-confirm (the AI still reads the referral and the hospital can
    # cancel later). The only combination that makes no sense is walk-in:
    # there's no appointment to confirm.
    if "auto_confirmar" in doc:
        if not doc["auto_confirmar"]:
            doc.pop("auto_confirmar", None)
        else:
            doc["auto_confirmar"] = True
            if modo in _NO_APPOINTMENT_MODES:
                return None, ("auto_confirmar necesita turnos: un servicio "
                              "walk-in no tiene turno que confirmar")

    # Same-day opening (2026-07-24 hospital meeting, reference-deployment counterpart: "the
    # first-come-first-served one, that opens the same day, so people can
    # book it remotely, but only that day"). This is the ONLY exception to
    # the "walk-in never offers appointments" invariant — and it's the
    # hospital's call, not ours: the office opens its list for the day and
    # whoever already knows they're going reserves a spot without waiting
    # in line. Only makes sense for walk-in: a service with an appointment
    # is already booked a month ahead.
    if "apertura_mismo_dia" in doc:
        if not doc["apertura_mismo_dia"]:
            doc.pop("apertura_mismo_dia", None)
        else:
            doc["apertura_mismo_dia"] = True
            if modo not in _NO_APPOINTMENT_MODES:
                return None, ("apertura_mismo_dia es sólo para servicios por orden "
                              "de llegada: uno con turno ya se saca con anticipación")

    # Máximo de turnos activos por paciente para ESTE servicio (kinesiología
    # son diez sesiones, una consulta es una) — mismo campo que el médico,
    # ver booking_limits.py.
    doc, err = _normalizar_booking_window(doc)
    if err:
        return None, err
    doc, err = _normalizar_turnos_max(doc)
    if err:
        return None, err

    # Walk-in closures are catalog facts, not an appointment agenda. Keep
    # them across mode changes and validate with the same exception contract.
    if "excepciones" in doc:
        excepciones, err = _validate_exceptions(doc.get("excepciones"))
        if err:
            return None, f"cierres: {err}"
        doc["excepciones"] = excepciones

    agenda = doc.get("agenda")
    if agenda is not None:
        # Hard project invariant: walk-in NEVER offers appointments (not in
        # the wizard, not on the doctor's page, not here — the product owner, 2x). The
        # only exception is same-day opening, and it stays bounded: the
        # schedule exists, but `_agenda_slots` only lets TODAY's slots out.
        if modo in _NO_APPOINTMENT_MODES and not doc.get("apertura_mismo_dia"):
            return None, "un servicio walk-in no puede tener agenda (walk-in nunca ofrece turnos)"
        if isinstance(agenda, dict) and "franjas" in agenda:
            # multi-band form {franjas, excepciones} — same shape as a
            # doctor's schedule; used by the hospital's import when a
            # service has different windows depending on the day
            # (site_availability._demo_next already understands it).
            franjas, err = _validate_time_bands(agenda.get("franjas"))
            if err:
                return None, f"agenda: {err}"
            excepciones, err = _validate_exceptions(agenda.get("excepciones"))
            if err:
                return None, f"agenda: {err}"
            doc["agenda"] = {"franjas": franjas, "excepciones": excepciones}
        else:
            agenda_norm, err = _validate_schedule(agenda)
            if err:
                return None, f"agenda: {err}"
            doc["agenda"] = agenda_norm

    return doc, None


def _doctor_name(doc, allow_incomplete, missing):
    """`nombre` from the editable parts (nombre_pila + apellido) or from the plain one. Returns the refusal or None."""
    # nombre_pila+apellido are the editable source from the admin panel
    # (2026-07-11); `nombre` still gets composed/persisted the same as
    # always so nothing that already consumes it breaks (patient_web/site_*.py,
    # avatars, MSD matching).
    nombre_pila = _str(doc.get("nombre_pila"))
    apellido = _str(doc.get("apellido"))
    if nombre_pila and apellido:
        doc["nombre_pila"] = nombre_pila
        doc["apellido"] = apellido
        doc["nombre"] = f"{nombre_pila} {apellido}".strip()
    else:
        doc.pop("nombre_pila", None)
        doc.pop("apellido", None)
        nombre = _str(doc.get("nombre")) or " ".join(x for x in (nombre_pila, apellido) if x)
        if not nombre:
            if not allow_incomplete:
                return "falta nombre"
            missing.append("nombre")
        doc["nombre"] = nombre
    return None


def _doctor_specialties(doc, allow_incomplete, missing):
    """The specialty (and the optional second one) from the closed catalog, each with its DERIVED cluster."""
    # The specialty comes from a closed catalog (webdata/especialidades.json)
    # — the cluster is ALWAYS derived here, never accepted from the client
    # (so clusters and specialties can't drift apart, and there's no manual
    # "Cluster" field in the form).
    especialidad = _str(doc.get("especialidad"))
    if especialidad:
        cluster = _specialty_cluster(especialidad)
        if cluster is None:
            return f"especialidad desconocida: '{especialidad}' (no está en especialidades.json)"
        doc["especialidad"] = especialidad
        doc["cluster"] = cluster
    elif not allow_incomplete:
        return "falta especialidad"
    else:
        missing.append("especialidad")
        doc.pop("especialidad", None)
        doc.pop("cluster", None)

    # An optional second specialty (hospital TASK-35): same closed catalog,
    # its cluster derived here too (`cluster_2`), never taken from the client.
    # It needs the first one and must differ from it.
    especialidad_2 = _str(doc.get("especialidad_2"))
    if especialidad_2:
        cluster_2 = _specialty_cluster(especialidad_2)
        if cluster_2 is None:
            return f"especialidad_2 desconocida: '{especialidad_2}' (no está en especialidades.json)"
        if not doc.get("especialidad"):
            return "especialidad_2 necesita la primera especialidad"
        if especialidad_2 == doc["especialidad"]:
            return "especialidad_2 debe ser distinta de la primera"
        doc["especialidad_2"] = especialidad_2
        doc["cluster_2"] = cluster_2
    else:
        doc.pop("especialidad_2", None)
        doc.pop("cluster_2", None)
    return None


def _doctor_contact_and_words(doc):
    """Email (lowercased) and the two search-word lists. Returns the refusal or None."""
    email = _str(doc.get("email"))
    if email:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return "email inválido"
        doc["email"] = email.lower()
    else:
        doc.pop("email", None)

    if "palabras" in doc and doc["palabras"] is not None:
        if not isinstance(doc["palabras"], list):
            return "palabras debe ser una lista de strings"
        doc["palabras"] = [x for x in (_str(p) for p in doc["palabras"]) if x]

    # palabras_excluidas: searches where the row must NOT appear (2026-07-17
    # municipal meeting: "so their office doesn't show up for a Pap smear
    # search"). Same format as `palabras`; search.js consumes it (excluida()).
    if "palabras_excluidas" in doc and doc["palabras_excluidas"] is not None:
        if not isinstance(doc["palabras_excluidas"], list):
            return "palabras_excluidas debe ser una lista de strings"
        doc["palabras_excluidas"] = [
            x for x in (_str(p) for p in doc["palabras_excluidas"]) if x
        ]
    return None


def _doctor_centers(doc, allow_incomplete, missing):
    """`centros[]` (each with its own schedule) and the flat centro/agenda of the primary one. Returns the refusal."""
    centros_validos = {c.get("slug") for c in centers()}

    # `centros` is the new shape (a list, a doctor can see patients at more
    # than one center with their own schedule at each). Compat: if it's not
    # given, we build it from the old flat shape {centro, agenda} — a
    # single center.
    centros_in = doc.get("centros")
    if not centros_in:
        centro_legacy = _str(doc.get("centro"))
        if centro_legacy:
            agenda_legacy = doc.get("agenda")
            centros_in = [{"centro": centro_legacy,
                           "franjas": [agenda_legacy] if agenda_legacy else [],
                           "excepciones": []}]
        elif not allow_incomplete:
            return "falta centro"
        else:
            centros_in = []

    if not isinstance(centros_in, list) or (not centros_in and not allow_incomplete):
        return "centros debe ser una lista no vacía de {centro, franjas, excepciones}"

    centros_out = []
    for i, entry in enumerate(centros_in):
        norm, err = _validate_doctor_center(entry, centros_validos)
        if err:
            # Name the center the person sees, not its position in the list.
            slug = _str(entry.get("centro")) if isinstance(entry, dict) else ""
            nombre = next((_str(c.get("name")) for c in centers() if c.get("slug") == slug), "")
            return f"{nombre or f'centro {i + 1}'}: {err}"
        centros_out.append(norm)
    doc["centros"] = centros_out

    # flat centro/agenda = the PRIMARY center (centros[0]) — all public code
    # (patient_web/site_*.py) still reads these two fields with no idea about
    # multi-center; see "Known scope limit" in the plan.
    if centros_out:
        principal = centros_out[0]
        doc["centro"] = principal["centro"]
        if principal["franjas"] or principal["excepciones"]:
            doc["agenda"] = {"franjas": principal["franjas"], "excepciones": principal["excepciones"]}
        else:
            doc.pop("agenda", None)
    else:
        missing.append("centro")
        doc.pop("centro", None)
        doc.pop("agenda", None)
    return None


def _doctor_identity_and_flags(doc, missing):
    """The derived `incompleto` flag, the stable id, the note and the additive flags (no refusals)."""
    if missing:
        doc["incompleto"] = True
    else:
        doc.pop("incompleto", None)

    # A nameless doctor still needs a stable id from day one (its account
    # points at it); the id never changes once the name arrives.
    mid = _str(doc.get("id")) or _slug(doc["nombre"]) or ("medico-" + secrets.token_hex(3))
    doc["id"] = mid

    # General/permanent note on the professional (replaces the old, bounded
    # "demo note" — see 2026-07-11 plan). `demo` stays its own flag,
    # independent of the note.
    nota = _str(doc.get("nota"))
    if nota:
        doc["nota"] = nota
    else:
        doc.pop("nota", None)
    doc.pop("demo_nota", None)

    if "demo" in doc and doc["demo"] is not None:
        doc["demo"] = bool(doc["demo"])
    # `prueba`: a test doctor, kept off every public surface (is_test_doctor).
    if doc.get("prueba") is True:
        doc["prueba"] = True
    else:
        doc.pop("prueba", None)

    # Token opaco del cartel de puerta (modules/intake/, fase 6): un campo
    # aditivo más, generado por intake_admin.py con secrets.token_urlsafe —
    # nunca tipeado por el admin, así que sólo se normaliza a string/None,
    # mismo criterio que `nota` arriba.
    qr_token = _str(doc.get("entrevista_qr_token"))
    if qr_token:
        doc["entrevista_qr_token"] = qr_token
    else:
        doc.pop("entrevista_qr_token", None)

    # Override de habilitación de la entrevista previa (modules/intake/,
    # fase 7): tri-state — True/False explícito pisa el default del sistema
    # (intake_admin.enabled_for); cualquier otra cosa (string, número,
    # ausente) se normaliza a "sin override" (hereda), nunca se guarda
    # basura en la columna. Mismo criterio aditivo que `entrevista_qr_token`
    # arriba: el formulario de médicos no tiene un campo para esto (lo
    # escribe intake_admin.py desde #entrevistas/la cola), pero si el
    # formulario reenvía el doc mergeado entero, esto lo deja intacto.
    if isinstance(doc.get("entrevista_habilitada"), bool):
        doc["entrevista_habilitada"] = doc["entrevista_habilitada"]
    else:
        doc.pop("entrevista_habilitada", None)


def _validate_doctor(doc, is_new):
    # Médico = usuario (the product owner, 2026-10-19): a doctor can be created — or
    # invited — with every field empty and complete the rest in onboarding.
    # Such a doctor carries the DERIVED flag `incompleto` (never accepted
    # from the client) and stays off every public surface (doctors() and the
    # patient site skip it) until name, specialty and a center are all there.
    # A doctor that is already complete keeps the strict rules: an edit can't
    # silently unpublish one by clearing its specialty or center.
    # The steps normalise `doc` in place, in this order, and the first refusal answers.
    allow_incomplete = is_new or doc.get("incompleto") is True
    missing = []
    err = (_doctor_name(doc, allow_incomplete, missing)
           or _doctor_specialties(doc, allow_incomplete, missing)
           or _doctor_contact_and_words(doc)
           or _doctor_centers(doc, allow_incomplete, missing))
    if err:
        return None, err
    _doctor_identity_and_flags(doc, missing)

    doc, err = _normalizar_booking_window(doc)
    if err:
        return None, err
    doc, err = _normalizar_turnos_max(doc, minimo=2)
    if err:
        return None, err

    return doc, None


def _normalizar_booking_window(doc):
    from app.modules.turnos import booking_horizon
    for key in ("dias_anticipacion", "apertura_turnos"):
        raw = doc.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            doc.pop(key, None)
            continue
        if key == "dias_anticipacion":
            n = booking_horizon._clamp(raw)
            if n is None or str(n) != str(raw).strip():
                return None, "dias_anticipacion va de 1 a 365"
            doc[key] = n
        elif not isinstance(raw, str) or not booking_horizon._TIME.fullmatch(raw):
            return None, "apertura_turnos debe tener formato HH:MM"
    return doc, None


def _normalizar_turnos_max(doc, minimo=1):
    """Override del máximo de turnos activos por paciente (booking_limits.py).
    Vive igual en el servicio y en el médico: vacío = hereda (el servicio del
    default del sitio, el médico del servicio). En el médico el mínimo es 2:
    un máximo de 1 lo da "sin límite explícito" (hereda), no un número que
    alguien tipea. Se valida acá y no en el formulario porque el catálogo
    también se carga por import."""
    from app.modules.turnos import booking_limits
    if "turnos_activos_max" not in doc:
        return doc, None
    raw = doc.get("turnos_activos_max")
    # Vacío = hereda. Un 0 NO es vacío: es un número que alguien escribió, y
    # "cero turnos" no es una regla que el hospital quiera — se rechaza abajo.
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        doc.pop("turnos_activos_max", None)
        return doc, None
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, "turnos_activos_max tiene que ser un número"
    if not minimo <= n <= booking_limits.MAX_LIMIT:
        return None, f"turnos_activos_max va de {minimo} a {booking_limits.MAX_LIMIT}"
    doc["turnos_activos_max"] = n
    return doc, None


def _phrase_list(doc, clave, errores):
    """One preparation section: a list of phrases, no blanks. A bare string
    is accepted as a one-item list — whoever's editing types one line and
    submits it, and bouncing the format back doesn't teach them anything."""
    v = doc.get(clave)
    if v is None:
        return
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        errores.append(f"{clave} debe ser una lista de frases")
        return
    doc[clave] = [_str(x) for x in v if _str(x)]


def _validate_preparation(doc, is_new):
    """A "How to prepare" entry. The only thing required is what it takes for
    the preparation to REACH someone: an id, a name, and at least one
    keyword to match against. Every section is optional — a preparation
    that just says "you don't need to do anything" is a valid preparation
    and, for several types of care, the correct answer."""
    nombre = _str(doc.get("nombre"))
    if not nombre:
        return None, "falta el nombre"
    doc["nombre"] = nombre

    id_ = _str(doc.get("id")) or _slug(nombre)
    if not _slug(id_):
        return None, "el id no puede quedar vacío"
    doc["id"] = _slug(id_)

    match = doc.get("match")
    if isinstance(match, str):
        # the form sends one line per word
        match = [x for x in re.split(r"[\n,]", match)]
    if not isinstance(match, list):
        return None, "las palabras para reconocerla deben ser una lista"
    # lowercase and no accents: text from a scanned referral can lose them
    # (same rule as _meta.diseno_match in the base JSON)
    limpias = []
    for kw in match:
        k = text_fold.ascii_fold(str(kw or "")).lower()
        k = " ".join(k.split())
        if k and k not in limpias:
            limpias.append(k)
    if not limpias:
        return None, "poné al menos una palabra por la cual reconocerla"
    doc["match"] = limpias

    resumen = _str(doc.get("resumen"))
    if resumen is not None:
        doc["resumen"] = resumen

    ay = doc.get("ayuno")
    if ay is not None:
        if not isinstance(ay, dict):
            return None, "ayuno debe ser un objeto"
        horas = ay.get("horas")
        if horas not in (None, ""):
            try:
                horas = int(horas)
            except (TypeError, ValueError):
                return None, "las horas de ayuno tienen que ser un número"
            if not 0 < horas <= 48:
                return None, "las horas de ayuno tienen que estar entre 1 y 48"
        else:
            horas = None
        doc["ayuno"] = {
            "required": bool(ay.get("required")),
            "horas": horas,
            "agua_permitida": None if ay.get("agua_permitida") is None else bool(ay.get("agua_permitida")),
            "nota": _str(ay.get("nota")) or None,
        }
        if doc["ayuno"]["required"] and not horas:
            # with no hours the phrase reads as "fasting required", which is
            # correct but weaker: asking for it here is cheaper than at the
            # front desk
            doc["ayuno"]["horas"] = None

    errores = []
    for clave, _titulo, _tono in prep_model.SECCIONES:
        _phrase_list(doc, clave, errores)
    if errores:
        return None, errores[0]
    # the old field name isn't persisted: it migrates to `antes`
    if doc.get("preparacion") and not doc.get("antes"):
        doc["antes"] = [_str(x) for x in doc.pop("preparacion") if _str(x)]
    doc.pop("preparacion", None)

    if "no_preparation_needed" in doc:
        doc["no_preparation_needed"] = bool(doc["no_preparation_needed"])

    fuentes = doc.get("sources")
    if fuentes is not None:
        if not isinstance(fuentes, list):
            return None, "fuentes debe ser una lista"
        doc["sources"] = [
            {"title": _str(f.get("title")) or "", "url": _str(f.get("url")) or ""}
            for f in fuentes if isinstance(f, dict) and (_str(f.get("title")) or _str(f.get("url")))
        ]

    return doc, None


_KNOWLEDGE_TYPES = ("categoria", "articulo", "pagina")

# Jurisdiction of a knowledge article. Legacy: four Spanish literals baked
# in when this repo only ever meant Pinamar ("pinamar"/"provincia"/"nacion"
# for the three geographic levels + "interno" for non-geographic/internal
# notes). Generalized (phase 3, 2026-07-30, see docs/architecture/
# data-packs.md "Knowledge jurisdiction -> pack levels"): "l2"/"l3"/"l4" are
# the SAME three geographic rungs, named after data_packs.py's chain levels
# (country/province/town) instead of a specific town's name, so a
# from-scratch deployment never has to spell "pinamar" into its own data.
# Both spellings stay valid FOREVER (ADDITIVE, not a migration) --
# knowledge.py's ranking treats an old and a new spelling of the same rung
# identically (see _JURISDICTION_LEVEL_ALIASES / _jurisdiction_level()).
# Existing conocimiento.json content and DB rows (all still "pinamar"/
# "provincia"/"nacion"/"interno") need no change and rank exactly as before.
#
# "global"/"l1" (phase 3b, 2026-08-06, conocimiento.json becomes a
# `data_packs._COMPOSABLE` file): a FOURTH, brand-new rung -- not an alias
# of an existing one -- for content that's true regardless of country
# (packs/global/conocimiento.json). It ranks like "interno" (weight 0 in
# knowledge.py's `_LEVEL_WEIGHT`, no official-source requirement below) but
# stays a distinct, real geographic-ish level so a from-scratch deployment
# can still tell "engine-wide" apart from "internal/editorial note".
_KNOWLEDGE_JURISDICTIONS = ("pinamar", "provincia", "nacion", "interno",
                            "l2", "l3", "l4", "global", "l1")
_KNOWLEDGE_STATUSES = ("publicado", "borrador")

# Jurisdictions that don't require an official https source in
# `_validate_knowledge` below: "interno" (non-geographic editorial note) and
# now "global"/"l1" (engine-wide content, phase 3b 2026-08-06) — there's no
# single country's official domain to cite for something true everywhere.
_KNOWLEDGE_NO_SOURCE_REQUIRED = ("interno", "global", "l1")

# Legacy Spanish literal -> its generic pack-level equivalent. "interno"
# has no geographic level (it's excluded on purpose) and maps to itself.
_JURISDICTION_LEVEL_ALIASES = {"nacion": "l2", "provincia": "l3", "pinamar": "l4",
                                "global": "l1"}


def jurisdiction_level(value):
    """Canonical geographic rung for a knowledge article's `jurisdiction`
    value: `"l1"`/`"l2"`/`"l3"`/`"l4"` (engine/country/province/town),
    `"interno"`, or None for anything unrecognized. Reads BOTH spellings
    (see `_KNOWLEDGE_JURISDICTIONS`'s docstring) so an old "pinamar" article
    and a new "l4" one land on the SAME rung — knowledge.py's ranking
    (weight + keyword preference) uses this instead of comparing raw
    strings."""
    v = str(value or "")
    if v in ("l1", "l2", "l3", "l4", "interno"):
        return v
    return _JURISDICTION_LEVEL_ALIASES.get(v)

# Trusted-domain ladder for validating a knowledge article's source URL.
# USED to be two independently hardcoded copies of the same tuple (here and
# in knowledge.py) — now ONE pack-fed list (Phase 2c, 2026-07-30, see
# docs/architecture/data-packs.md): unions "domains" from every level's own
# trusted-domains.json in the active chain (packs/ar/, packs/ar-b/,
# configured private town pack), read once at import (same read-once +
# literal-fallback pattern as format.py's PHONE / tz.py). Falls back to the
# OLD hardcoded tuple (this repo's only real deployment, so never wrong in
# prod) if packs/ is missing, mid-deploy, or the active chain has no
# trusted-domains.json at any level (e.g. a from-scratch country/town pack
# that hasn't defined one yet — no official domain is safer than a stale one).
_FALLBACK_OFFICIAL_SOURCES = (
    "pinamar.gob.ar", "gba.gob.ar", "ms.gba.gov.ar", "argentina.gob.ar",
    "servicios.infoleg.gob.ar",
)


def _load_official_sources():
    domains = []
    try:
        chain = data_packs.active_chain() or ()
        for pack_id in chain:
            path = os.path.join(data_packs.pack_dir(pack_id),
                                "trusted-domains.json")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                doc = json.load(f) or {}
            for d in doc.get("domains") or []:
                if d not in domains:
                    domains.append(d)
    except (OSError, ValueError, AttributeError):
        return tuple(_FALLBACK_OFFICIAL_SOURCES)
    return tuple(domains) if domains else tuple(_FALLBACK_OFFICIAL_SOURCES)


_OFFICIAL_SOURCES = _load_official_sources()


def official_sources():
    """The active chain's trusted-domain tuple — the single source knowledge.py
    (and anything else that needs to judge "is this URL an official source")
    should read, instead of keeping its own copy."""
    return _OFFICIAL_SOURCES


def _url_oficial(url):
    p = urlparse(url)
    host = (p.hostname or "").lower().rstrip(".")
    return p.scheme == "https" and any(host == d or host.endswith("." + d) for d in _OFFICIAL_SOURCES)


def _validate_knowledge(doc, is_new):
    """Normalizes the editable content and rejects non-official sources."""
    clean = {
        k: v for k, v in doc.items()
        if k in {"id", "type", "title", "parent_id", "jurisdiction", "summary",
                 "content", "tags", "status", "source", "valid_from", "valid_to",
                 # type "pagina" only (patient_web/info_pages.py): public path,
                 # page meta and the sourced blocks. Dropped for the rest.
                 "slug", "blocks", "page"}
    }
    clean["id"] = _slug(clean.get("id") or clean.get("title") or "")
    if not clean["id"]:
        return None, "el id es obligatorio"
    clean["type"] = _str(clean.get("type"))
    if clean["type"] not in _KNOWLEDGE_TYPES:
        return None, "tipo debe ser categoria, articulo o pagina"
    clean["title"] = _str(clean.get("title"))
    if not clean["title"]:
        return None, "el título es obligatorio"
    parent = _str(clean.get("parent_id"))
    clean["parent_id"] = _slug(parent) if parent else None
    if clean["parent_id"] == clean["id"]:
        return None, "una entrada no puede ser su propia categoría"
    clean["jurisdiction"] = _str(clean.get("jurisdiction"))
    if clean["jurisdiction"] not in _KNOWLEDGE_JURISDICTIONS:
        return None, "jurisdicción inválida"
    clean["status"] = _str(clean.get("status")) or "borrador"
    if clean["status"] not in _KNOWLEDGE_STATUSES:
        return None, "estado debe ser publicado o borrador"
    clean["summary"] = _str(clean.get("summary"))
    tags = clean.get("tags") or []
    if not isinstance(tags, list) or any(not _str(x) for x in tags):
        return None, "tags debe ser una lista de textos"
    clean["tags"] = [_str(x) for x in tags][:30]
    for key in ("valid_from", "valid_to"):
        value = _str(clean.get(key))
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return None, f"{key} debe usar AAAA-MM-DD"
        if value:
            clean[key] = value
        else:
            clean.pop(key, None)
    if clean.get("valid_from") and clean.get("valid_to") and clean["valid_from"] > clean["valid_to"]:
        return None, "valid_from no puede ser posterior a valid_to"
    if clean["type"] == "categoria":
        clean.pop("content", None)
        clean.pop("source", None)
        return clean, None
    if clean["type"] == "pagina":
        slug = _str(clean.get("slug"))
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            return None, "una página requiere slug (minúsculas, números y guiones)"
        clean["slug"] = slug
        if not isinstance(clean.get("blocks"), list) or not isinstance(clean.get("page") or {}, dict):
            return None, "una página requiere blocks (lista) y page (objeto)"
    else:
        for key in ("slug", "blocks", "page"):
            clean.pop(key, None)
    clean["content"] = _str(clean.get("content"))
    if not clean["content"]:
        return None, "un artículo requiere contenido"
    source = clean.get("source")
    if clean["jurisdiction"] not in _KNOWLEDGE_NO_SOURCE_REQUIRED:
        if not isinstance(source, dict):
            return None, "un artículo público requiere fuente oficial"
        url = _str(source.get("url"))
        if not _url_oficial(url):
            return None, "la fuente debe ser una URL https oficial"
        if not _str(source.get("title")) or not _str(source.get("agency")):
            return None, "la fuente requiere título y organismo"
        verificado = _str(source.get("verified_at"))
        if not verificado or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verificado):
            return None, "la fuente requiere verificado_en en formato AAAA-MM-DD"
        try:
            dias = int(source.get("review_every_days"))
        except (TypeError, ValueError):
            return None, "la fuente requiere revisar_cada_dias"
        if not 1 <= dias <= 3650:
            return None, "revisar_cada_dias inválido"
        clean["source"] = {"title": _str(source.get("title")), "url": url,
                            "agency": _str(source.get("agency")),
                            "verified_at": verificado,
                            "review_every_days": dias}
    elif isinstance(source, dict):
        clean["source"] = {k: _str(source.get(k)) for k in ("title", "url", "agency", "verified_at") if _str(source.get(k))}
    return clean, None


_VALIDATORS = {
    "centros": _validate_center,
    "servicios": _validate_service,
    "medicos": _validate_doctor,
    "preparaciones": _validate_preparation,
    "conocimiento": _validate_knowledge,
}


def validate(tipo, doc, is_new):
    """(normalized_doc, error|None). `doc` isn't mutated — a copy gets
    validated/normalized. Unknown fields (not listed here) pass through
    intact, EXCEPT the ones starting with "_": those are read-only metadata
    that admin_list() adds (_origen, _borrado) and the client sends back
    as-is on PUT/POST (admin-contenido.js builds the body with
    Object.assign of the item); without this strip they'd end up persisted
    inside the doc's jsonb."""
    if tipo not in _VALIDATORS:
        return None, f"tipo desconocido: {tipo}"
    if not isinstance(doc, dict):
        return None, "el documento debe ser un objeto"
    limpio = {k: v for k, v in doc.items() if not str(k).startswith("_")}
    return _VALIDATORS[tipo](limpio, is_new)
