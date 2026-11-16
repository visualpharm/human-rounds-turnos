"""analytics_events.py — what an anonymous analytics event may say, and how a raw one is cleaned.

Pure functions, no I/O. The stream (analytics_export.py) calls `clean_batch` on what `POST /api/ev` and
the search beacon receive and writes only what comes back. The allowlists below ARE the contract
(docs/decisions/2026-11.md, «Analytics stream»): a key that is not listed never leaves the process,
and `tests/test_analytics_events.py` pins the lists.

Not collected, by construction: IP, User-Agent, cookies or any persistent id, DNI, names, e-mail, phone,
request numbers, appointment times, the query string of a URL, chat or form text.
"""

import re
import secrets
import time
import unicodedata

# ---- the contract -----------------------------------------------------------------------------------------

TYPES = ("page_view", "search", "step")

# The only keys an object of each type may carry (`id`, `ts`, `type` are the envelope).
ENVELOPE = ("id", "ts", "type")
FIELDS = {
    "page_view": ("route", "rid", "ref", "lang", "device", "jid", "pl"),
    "search": ("q", "n", "origin", "jid", "pl"),
    "step": ("step", "service", "jid", "pl"),
}
STEPS = ("slots_shown", "submit", "done", "error")
LANGS = ("es", "en", "pt", "fr", "it", "de", "ru", "uk", "ja", "zh")
DEVICES = ("mobile", "tablet", "desktop")

# The closed list of public page templates. `rid` is the public slug after the prefix, where there is one.
_STATIC_ROUTES = {
    "/": "/", "/doctors": "/doctors", "/centers": "/centers", "/news": "/news",
    "/news/pedidos": "/news/pedidos", "/telemedicine": "/telemedicine", "/zoonosis": "/zoonosis",
    "/blood-donation": "/blood-donation", "/account": "/account", "/my-requests": "/my-requests",
}
_PREFIX_ROUTES = {
    "/service/": "/service/:id", "/doctor/": "/doctor/:id", "/center/": "/center/:slug",
    "/d/": "/d/:id", "/study/": "/study/:id", "/preparation/": "/preparation/:id",
}
ROUTES = tuple(sorted(set(_STATIC_ROUTES.values()) | set(_PREFIX_ROUTES.values())))

_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
_IDENT = re.compile(r"[A-Za-z0-9_-]{8,40}")
_LONG_DIGITS = re.compile(r"\d{6,}")
_BOT = re.compile(r"bot|crawl|spider|slurp|headless|lighthouse|pingdom|uptime|monitor|curl|wget|python|"
                  r"httpclient|preview|facebookexternalhit|whatsapp|telegram", re.I)
_TABLET = re.compile(r"ipad|tablet|kindle|silk|playbook|(?!.*mobile)android", re.I)
_MOBILE = re.compile(r"mobi|iphone|ipod|android|blackberry|opera mini", re.I)

MAX_BATCH = 20


# ---- fields -----------------------------------------------------------------------------------------------

def new_id():
    """Sortable by time, unique: 13 hex digits of the epoch in ms + 10 hex digits of randomness."""
    return f"{int(time.time() * 1000):013x}{secrets.token_hex(5)}"


def route_of(path):
    """`(template, rid)` for a public page path, or None. The query and the fragment never get here: only
    the path is read, and a slug that looks like a document number is refused."""
    path = str(path or "").split("?", 1)[0].split("#", 1)[0]
    if len(path) > 120:
        return None
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    if path in _STATIC_ROUTES:
        return _STATIC_ROUTES[path], None
    for prefix, template in _PREFIX_ROUTES.items():
        if path.startswith(prefix):
            slug = path[len(prefix):].split("/", 1)[0].lower()
            if _SLUG.fullmatch(slug) and not _LONG_DIGITS.search(slug):
                return template, slug
            return template, None
    return None


def device_of(user_agent):
    """`mobile` / `tablet` / `desktop`, or None for a robot (its events are dropped). The agent string is read
    here and not kept."""
    ua = str(user_agent or "")
    if not ua or _BOT.search(ua):
        return None
    if _TABLET.search(ua):
        return "tablet"
    return "mobile" if _MOBILE.search(ua) else "desktop"


def ref_of(referrer, own_host=""):
    """`internal` for this site, `direct` for none, else the referrer's host name only (no port, no path).
    Reads both shapes: the page beacon sends the bare host (`www.google.com`, `analytics.mjs`), a full URL
    is read the same way (`https://www.google.com/search?q=…`)."""
    ref = str(referrer or "").strip()
    if not ref:
        return "direct"
    m = re.match(r"^(?:[a-z][a-z0-9+.-]*://)?([^/:?#@\s]+)(?::\d{1,5})?(?:[/?#]|$)", ref, re.I)
    host = m.group(1).lower().rstrip(".") if m else ""
    if not host or len(host) > 80 or not re.fullmatch(r"[a-z0-9.-]+", host):
        return "direct"
    if own_host and host == own_host.lower():
        return "internal"
    return host


def _ident(v):
    v = str(v or "")
    return v if _IDENT.fullmatch(v) else None


def _slug(v):
    v = str(v or "").strip().lower()
    return v if _SLUG.fullmatch(v) and not _LONG_DIGITS.search(v) else None


# ---- the text of a search ---------------------------------------------------------------------------------

_MASK = "·"  # «·» replaces a word that is not public catalog vocabulary
_STOP = frozenset((
    "de del la las el los y e en a al con sin para por un una uno que lo se mi mis su sus es hay "
    "turno turnos medico medica doctor doctora dr dra hospital caps centro sala guardia consulta consultas "
    "estudio estudios analisis control controles ver quiero necesito busco pedir sacar").split())


def fold(text):
    """Lower case, no accents."""
    text = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def words(text):
    return re.findall(r"[^\W_]+", fold(text))


def sanitize_query(q, vocab):
    """The search text with every word that is not public vocabulary replaced by «·». `vocab` is the set of
    folded words of the public catalog (services, specialties, centers, doctors). Anything that looks like
    an address, a number of a document or a phone is dropped whole. None when nothing public is left (or
    when there is no vocabulary: then nothing can be proven safe)."""
    q = str(q or "")
    if not vocab or "@" in q or "://" in q:
        return None
    out = []
    for w in words(q):
        keep = w in vocab or w in _STOP
        if sum(c.isdigit() for c in w) >= 4 or not keep:
            w = _MASK
        if w == _MASK and out and out[-1] == _MASK:
            continue
        out.append(w)
    if not any(w != _MASK and w not in _STOP for w in out):
        return None
    return " ".join(out)[:80] or None


# ---- the events -------------------------------------------------------------------------------------------

def _envelope(kind, now=None):
    return {"id": new_id(), "ts": int(now if now is not None else time.time()), "type": kind}


def page_view(raw, *, device, own_host):
    route = route_of(raw.get("path"))
    jid, pl = _ident(raw.get("jid")), _ident(raw.get("pl"))
    if not route or not jid or not pl or device is None:
        return None
    lang = str(raw.get("lang") or "").lower()[:2]
    ev = _envelope("page_view")
    ev.update(route=route[0], rid=route[1], ref=ref_of(raw.get("ref"), own_host),
              lang=lang if lang in LANGS else "es", device=device, jid=jid, pl=pl)
    return ev


def step(raw, *, device):
    name = str(raw.get("step") or "")
    jid, pl = _ident(raw.get("jid")), _ident(raw.get("pl"))
    if name not in STEPS or not jid or not pl or device is None:
        return None
    ev = _envelope("step")
    ev.update(step=name, service=_slug(raw.get("service")), jid=jid, pl=pl)
    return ev


def search(q, results, origin, jid, pl, *, vocab):
    """A search event from the existing beacon's fields; None when the text has nothing public left."""
    clean_q = sanitize_query(q, vocab)
    try:
        n = max(0, min(int(results), 9999))
    except (TypeError, ValueError):
        return None
    if clean_q is None:
        return None
    route = route_of(origin)
    ev = _envelope("search")
    ev.update(q=clean_q, n=n, origin=route[0] if route else None, jid=_ident(jid), pl=_ident(pl))
    return ev


def clean_batch(body, *, user_agent, own_host):
    """`([events], dropped_bot)` from the decoded JSON body of `POST /api/ev`: at most MAX_BATCH raw
    events, each cleaned to its allowlist. A body of the wrong shape gives nothing."""
    items = body.get("events") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return [], 0
    device = device_of(user_agent)
    if device is None:
        return [], 1
    out = []
    for raw in items[:MAX_BATCH]:
        if not isinstance(raw, dict) or raw.get("wd"):  # `wd`: the page saw navigator.webdriver
            continue
        kind = raw.get("t")
        ev = (page_view(raw, device=device, own_host=own_host) if kind == "page_view"
              else step(raw, device=device) if kind == "step" else None)
        if ev:
            out.append(ev)
    return out, 0


def allowed_keys(kind):
    return set(ENVELOPE) | set(FIELDS.get(kind, ()))


def conforms(ev):
    """True when an object carries exactly the keys of its type (the writer's last check)."""
    return isinstance(ev, dict) and ev.get("type") in TYPES and set(ev) == allowed_keys(ev["type"])
