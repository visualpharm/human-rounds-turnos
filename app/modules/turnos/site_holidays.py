"""Site-level holidays: dates the whole installation closes, on top of every agenda.

La contraparte técnica (hospital, 2026-10-15): «bloqueo masivo de los turnos programados — por
ejemplo cerrar de antemano los feriados nacionales del año». Until then a
closure lived only in each agenda's own ``excepciones``; closing a national
holiday meant editing every doctor and every service.

One list per installation, edited by an admin in Settings («Feriados»):
``[{"fecha": "YYYY-MM-DD", "nombre": "..."}]``, stored in the catalog overlay
row ``("config", "feriados")``. It is read in ONE place —
``site_availability._day_exceptions`` — which hands every slot consumer a synthetic
full-day ``cerrado`` exception for a holiday, exactly as if each agenda carried
it. Appointments already given on a holiday are never touched: the admin sees
how many there are per date (``appointment_counts``) and the desk reschedules.

The country's official list is a data-pack file (``feriados-nacionales.json``,
``packs/ar/`` for Argentina) copied verbatim from the official calendar; this
module never computes a holiday date.
"""
import datetime
import json
import os
import re
import threading
import time

from app.core import sitedata, tz

CATALOG_TYPE = "config"
CATALOG_ID = "feriados"
NATIONAL_FILE = "feriados-nacionales.json"
MAX_ITEMS = 400
MAX_NAME = 120
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TTL = 3.0
_cache = {"v": None, "t": 0.0}
_lock = threading.Lock()
_generation = 0


class HolidayError(ValueError):
    """A list an admin cannot save; ``str(exc)`` is the message key's fallback."""

    def __init__(self, key, fallback, **vars):
        super().__init__(fallback)
        self.key, self.fallback, self.vars = key, fallback, vars


def _valid_date(value):
    value = str(value or "").strip()
    if not _DATE.fullmatch(value):
        return None
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return value


def clean(items):
    """Sorted, one entry per date (the last one given wins). Raises HolidayError."""
    if not isinstance(items, list):
        raise HolidayError("admin.settings.holidays.invalid", "La lista de feriados no es válida.")
    by_date = {}
    for item in items:
        if not isinstance(item, dict):
            raise HolidayError("admin.settings.holidays.invalid", "La lista de feriados no es válida.")
        if not str(item.get("fecha") or "").strip() and not str(item.get("nombre") or "").strip():
            continue  # a row added and left blank is not a holiday
        fecha = _valid_date(item.get("fecha"))
        if not fecha:
            raise HolidayError("admin.settings.holidays.bad_date", "Cada feriado necesita una fecha válida.")
        nombre = " ".join(str(item.get("nombre") or "").split())
        if not nombre:
            raise HolidayError("admin.settings.holidays.no_name", "Cada feriado necesita un nombre.")
        if len(nombre) > MAX_NAME:
            raise HolidayError("admin.settings.holidays.long_name",
                               "El nombre de un feriado puede tener hasta {max} caracteres.", max=MAX_NAME)
        by_date[fecha] = nombre
    if len(by_date) > MAX_ITEMS:
        raise HolidayError("admin.settings.holidays.too_many",
                           "Se pueden cargar hasta {max} feriados.", max=MAX_ITEMS)
    return [{"fecha": f, "nombre": by_date[f]} for f in sorted(by_date)]


def _read():
    try:
        from app.core import db_catalog
        row = db_catalog.catalog_get(CATALOG_TYPE, CATALOG_ID)
    except Exception:  # noqa: BLE001 — DB down/missing: no site holidays, never a 500
        return {}
    doc = (row or {}).get("doc") or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    try:
        return {h["fecha"]: h["nombre"] for h in clean(items or [])}
    except HolidayError:
        return {}


def by_date():
    """{iso_date: name} of every site holiday, cached ~3 s (read on every slot day)."""
    while True:
        now = time.time()
        with _lock:
            if _cache["v"] is not None and now - _cache["t"] < _TTL:
                return _cache["v"]
            generation = _generation
        value = _read()
        with _lock:
            if generation != _generation:
                continue  # a save superseded this read
            _cache["v"], _cache["t"] = value, now
            return value


def invalidate():
    global _generation
    with _lock:
        _generation += 1
        _cache["v"], _cache["t"] = None, 0.0


def name_for(dia):
    """The holiday's name when ``dia`` (ISO date) is a site holiday, else None."""
    return by_date().get(str(dia or "")[:10])


def closures_for(dia):
    """The synthetic full-day closure a holiday adds to ANY agenda's day.

    Same shape as an agenda's own ``cerrado`` exception, plus ``feriado`` so
    a view can tell the two apart. Empty on an ordinary day."""
    nombre = name_for(dia)
    if not nombre:
        return []
    return [{"tipo": "cerrado", "fecha": str(dia)[:10], "motivo": nombre, "feriado": True}]


def items():
    return [{"fecha": f, "nombre": n} for f, n in sorted(by_date().items())]


def upcoming(today=None, days=366):
    """Holidays from ``today`` (installation timezone) through the next year."""
    today = today or datetime.datetime.now(tz.tzinfo()).date()
    last = (today + datetime.timedelta(days=days)).isoformat()
    first = today.isoformat()
    return [h for h in items() if first <= h["fecha"] <= last]


def upcoming_labeled(today=None):
    """`upcoming()` plus `dia`, the friendly date staff read in the hours views."""
    from app.patient_web import site_schedule
    return [dict(h, dia=site_schedule._friendly_date(h["fecha"])) for h in upcoming(today)]


def save(new_items):
    """Replace the whole list. Returns the cleaned list. Raises HolidayError."""
    cleaned = clean(new_items)
    from app.core import db_catalog
    db_catalog.catalog_upsert(CATALOG_TYPE, CATALOG_ID, {"items": cleaned})
    invalidate()
    return cleaned


def national():
    """The country's official list from the data pack: {"source", "items"}.

    ``items`` keep the official name; an installation with no country file
    (or a broken one) gets an empty list and the UI hides the action."""
    path = sitedata.path(NATIONAL_FILE)
    if not path or not os.path.isfile(path):
        return {"source": "", "items": []}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        out = [{"fecha": _valid_date(h.get("date")), "nombre": str(h.get("name") or "").strip()}
               for h in doc.get("holidays") or []]
    except (OSError, ValueError, AttributeError):
        return {"source": "", "items": []}
    return {"source": str(doc.get("source") or ""),
            "items": [h for h in out if h["fecha"] and h["nombre"]]}


def merge_national(current, today=None):
    """Current list plus every official holiday from today on that it lacks.

    A date the admin already named keeps the admin's name; past official
    dates are not re-added (a removed past day stays removed)."""
    today = (today or datetime.datetime.now(tz.tzinfo()).date()).isoformat()
    have = {h["fecha"] for h in current}
    extra = [h for h in national()["items"] if h["fecha"] >= today and h["fecha"] not in have]
    return list(current) + extra


def appointment_counts(dates):
    """{iso_date: n} of pending or approved appointments already given on each date."""
    dates = [d for d in (_valid_date(x) for x in dates or []) if d]
    if not dates:
        return {}
    try:
        from app.core import db_slots
        return db_slots.count_appointments_on_dates(dates)
    except Exception:  # noqa: BLE001 — counts are advisory; the list still renders
        return {}
