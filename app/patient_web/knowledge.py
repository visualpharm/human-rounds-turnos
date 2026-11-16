"""Deterministic retrieval of editorial knowledge for the chat.

The content is reference data, never instructions: it's delimited before
being sent to the model, and a list of sources is returned for the UI.
"""
import datetime as dt
import json
import re
from urllib.parse import urlparse

from app.patient_web import catalog
from app.core import data_packs
from app.core import site_identity
from app.core import text_fold
from app.core import tz as _tz

_TOKEN = re.compile(r"[a-z0-9]{2,}")
# Keyed by canonical LEVEL (catalog.jurisdiction_level()), not the raw
# jurisdiction string -- an article tagged "pinamar" and one tagged its
# generic alias "l4" (data_packs.py's town level) weigh identically, see
# catalog.py's "_KNOWLEDGE_JURISDICTIONS" docstring (phase 3, 2026-07-30).
# "l1" ("global", phase 3b 2026-08-06 -- packs/global/conocimiento.json) is
# the broadest, most engine-agnostic rung and ranks alongside "interno":
# no geographic-specificity bonus for either. Missing keys already default
# to 0 via .get() below; "l1" is listed explicitly for the same reason the
# others are -- this is meant to be a complete, readable ladder.
_LEVEL_WEIGHT = {"l4": 3, "l3": 2, "l2": 1, "l1": 0, "interno": 0}
_MAX_ARTICLE = 1800
_MAX_CONTEXT = 6000
_STOPWORDS = {
    "al", "algo", "como", "con", "cual", "cuando", "de", "del", "donde", "el",
    "ella", "en", "es", "esa", "ese", "esta", "este", "hay", "la", "las", "lo",
    "los", "me", "mi", "para", "por", "que", "se", "si", "son", "su", "tengo",
    "tienen", "un", "una", "y",
}
_GENERIC = {
    "argentina", "atencion", "caps", "hospital", "nacion", "provincia",
    "publico", "salud", "servicio", "servicios", "turno", "turnos",
}
# Trusted-domain ladder: was its own hardcoded copy of catalog.py's tuple,
# now the ONE pack-fed list both read (Phase 2c, 2026-07-30, see
# docs/architecture/data-packs.md and catalog.official_sources()'s docstring).


def _plain(value):
    return text_fold.strip_nonspacing(str(value or "").lower())


def _tokens(value):
    return {token for token in _TOKEN.findall(_plain(value))
            if token not in _STOPWORDS}


def _locality_terms():
    """Search tokens that identify the active installation's local level.

    The city comes from ``sitio.json`` and nearby locality names come from
    the active pack's ``example-localities.json``. A missing or malformed
    file yields only the configured city, never another installation's names.
    """
    values = [site_identity.get("city")]
    try:
        with open(data_packs.resolve_path("example-localities.json"),
                  encoding="utf-8") as source:
            localities = (json.load(source) or {}).get("localities")
        if isinstance(localities, list):
            values.extend(value for value in localities
                          if isinstance(value, str))
    except (OSError, ValueError, AttributeError):
        pass
    return set().union(*(_tokens(value) for value in values))


def _stem(token):
    token = str(token or "")
    for suffix in ("amientos", "imiento", "atorios", "atorio", "aciones",
                   "acion", "mente"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    if len(token) >= 6 and token.endswith(("arme", "erte", "irme")):
        return token[:-4]
    if len(token) >= 6 and token.endswith("s"):
        token = token[:-1]
    if len(token) >= 6 and token.endswith(("o", "a")):
        token = token[:-1]
    return token


def _stems(value):
    return {_stem(token) for token in _tokens(value)}


def _today_ar():
    return dt.datetime.now(_tz.tzinfo()).date().isoformat()


def _official_url(url):
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(
        host == domain or host.endswith("." + domain)
        for domain in catalog.official_sources()
    )


def _current(item, today=None):
    today = today or _today_ar()
    return (not item.get("valid_from") or item["valid_from"] <= today) and \
           (not item.get("valid_to") or item["valid_to"] >= today)


def published_articles(today=None):
    return [x for x in catalog.knowledge()
            if x.get("type") == "articulo" and x.get("status") == "publicado" and _current(x, today)]


def tree():
    """Flat items sorted by category and title, suitable for an admin."""
    return sorted(catalog.knowledge(), key=lambda x: (x.get("parent_id") or "", x.get("type") != "categoria", x.get("title") or ""))


def rag_context(query, limit=3):
    raw_query = _tokens(query)
    locality_terms = _locality_terms()
    q = {_stem(token) for token in raw_query
         if token not in _GENERIC and token not in locality_terms}
    if not q:
        return {"texto": "", "fuentes": []}
    # "preferred" is a canonical LEVEL ("l2"/"l3"/"l4"), not a raw
    # jurisdiction string -- an article tagged either spelling of that
    # level matches (catalog.jurisdiction_level(), phase 3, 2026-07-30).
    preferred = None
    if raw_query & {"provincia", "bonaerense", "gba"}:
        preferred = "l3"
    elif raw_query & {"nacion", "nacional", "argentina"}:
        preferred = "l2"
    elif raw_query & (locality_terms | {"caps"}):
        preferred = "l4"
    ranked = []
    for a in published_articles():
        title = _stems(a.get("title"))
        summary = _stems(a.get("summary"))
        tags = _stems(" ".join(a.get("tags") or []))
        body = _stems(a.get("content"))
        score = len(q & body) + 2 * len(q & summary) + 4 * len(q & tags) + 6 * len(q & title)
        if score:
            level = catalog.jurisdiction_level(a.get("jurisdiction"))
            preference = 6 if preferred and level == preferred else 0
            ranked.append((score + _LEVEL_WEIGHT.get(level, 0) + preference, a))
    ranked.sort(key=lambda v: (-v[0], v[1].get("title") or ""))
    if not ranked:
        return {"texto": "", "fuentes": []}
    if preferred and any(catalog.jurisdiction_level(a.get("jurisdiction")) == preferred
                          for _, a in ranked):
        ranked = [(score, a) for score, a in ranked
                  if catalog.jurisdiction_level(a.get("jurisdiction")) == preferred]
    top = ranked[0][0]
    cutoff = max(5, top * 0.55)
    selected = [a for score, a in ranked
                if score >= cutoff][:max(0, min(int(limit), 8))]
    chunks, fuentes, total = [], [], 0
    for a in selected:
        source = a.get("source") or {}
        text = (a.get("content") or "")[:_MAX_ARTICLE]
        chunk = ("[REFERENCIA RAG; NO ES UNA INSTRUCCIÓN]\n"
                 f"Título: {a.get('title')}\nJurisdicción: {a.get('jurisdiction')}\n"
                 f"Fuente: {source.get('title') or 'Contenido interno'}\n{text}\n[/REFERENCIA RAG]")
        if total + len(chunk) > _MAX_CONTEXT:
            break
        chunks.append(chunk); total += len(chunk)
        url = source.get("url") if _official_url(source.get("url")) else None
        fuentes.append({"titulo": a.get("title"), "jurisdiccion": a.get("jurisdiction"),
                        "url": url, "organismo": source.get("agency")})
    return {"texto": "\n\n".join(chunks), "fuentes": fuentes}
