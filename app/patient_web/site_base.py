"""patient_web/site_base.py — the shared plumbing of the public site.

The pack-file loader with its mtime cache (`_load`), the editable overlay (`_with_overlay`), the installation clock
and country, and the tiny text helpers (`_esc`, `_json_inline`, `_slug`, `_norm_name`) every `patient_web/site_*.py`
domain module builds on. It imports no sibling: the domain modules import it, never the other way round.
"""

import html as _html
import json
import os
import re
import unicodedata
import datetime as _datetime

from app.core import clock
from app.core import sitedata
from app.core import paths
from app.core import data_packs
from app.core import tz as _tz


def _slug(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

_DIR = paths.PROJECT_ROOT
_CACHE = {}
_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)

# Country for schema.org markup (doctor page's JSON-LD "areaServed"/
# "addressCountry") — packs/ar/country.json, same read-once-at-import +
# literal-fallback pattern as format.py's PHONE block (2026-07-30, see
# docs/architecture/data-packs.md). NOT the DNI-length hardcodes elsewhere in
# this file, or seo.py's own literals — those are still open, tracked in
# docs/architecture/data-packs.md's Phase 2c remaining list.
_FALLBACK_COUNTRY = {"name": "Argentina", "iso2": "AR"}


def _load_country():
    doc = data_packs.value("country.json") or {}
    return {"name": doc.get("name") or _FALLBACK_COUNTRY["name"],
            "iso2": doc.get("iso2") or _FALLBACK_COUNTRY["iso2"]}


_COUNTRY = _load_country()


def _now_art():
    """Argentina wall-clock time, independent of the server host timezone.

    Availability helpers historically use naive datetimes, so keep that
    contract while deriving the value from the explicit hospital timezone.
    """
    return clock.now().astimezone(_ART).replace(tzinfo=None)


def _today_art():
    return _now_art().date()


# ---------------------------------------------------------------- data
def _load(name, center=None):
    # Cache key is the RESOLVED path, not the bare filename — see
    # catalog._load_json's identical comment (2026-07-30, layered data
    # packs): a pack-chain change or a file moving between levels must
    # invalidate, which a filename-only key would miss. `center`: an L5
    # center pack's file (if any) outranks the rest of the chain for this
    # one lookup — see data_packs.py, "Center packs (L5)".
    path = sitedata.path(name, center=center)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _CACHE[path] = (mtime, data)
    return data


def _with_overlay(tipo, base):
    """Adds the admin's editable overlay (catalog.py, table in Postgres) on
    top of `base` (already loaded by `_load`, so the tests' monkeypatch of
    site_base._load keeps working); if that blows up for any reason,
    returns the base as-is — the site must never go down because of this."""
    try:
        from app.patient_web import catalog
        out, _ = catalog.merge_with_overlay(tipo, base)
        return out
    except Exception:  # noqa: BLE001
        return base


def _norm_name(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


# ---------------------------------------------------------------- helpers
def _esc(s):
    return _html.escape("" if s is None else str(s), quote=True)


def _json_inline(obj):
    """JSON safe to embed inside <script>…</script>."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
