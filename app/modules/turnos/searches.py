"""searches.py — server-side logging of what patients search for (Pinamar Turnos).

Feeds a future admin "Búsquedas" report (db_analytics.top_searches). The search
control lives in listing.js (only drawn on tables of >15 rows) and also
ranks the home page's search via search.js — from there, once the query
"settles" (the user stops typing for ~1500ms) a beacon is sent with the
final query and the result count visible at that moment.

Owner of this module:
  POST /api/search-log   {q, resultados, origen} — no session, no PII, no
                          IP stored. Always responds 204 as long as the body
                          is valid (even if the DB is down: log_search
                          no-ops silently, never a 500).

Anti-abuse of its OWN — NOT abuse_guard.py/Turnstile (that's for the
expensive AI endpoints backed by OpenRouter; this is a cheap INSERT).
In-memory cap per IP, 300/day by default (env TURNOS_SEARCHES_PER_DAY, ex
TURNOS_BUSQUEDAS_PER_DAY). At the cap: still 204, just not persisted — not
worth telling the client about an analytics limit.

Import-safe: stdlib + db only (lazy/import-safe by design).
"""

import os
import re
import json

from app.core import db_analytics
from app.core import rate_window

DEFAULT_PER_DAY = 300
_DAY = 86400

_HITS = rate_window.Hits(_DAY)  # ip -> [epoch, ...] attempts that consumed quota in the last 24h
_sweep = _HITS.sweep  # an IP that exhausts its quota and never comes back must not stay in _HITS forever


def _per_day():
    try:
        v = os.environ.get("TURNOS_SEARCHES_PER_DAY") or os.environ.get("TURNOS_BUSQUEDAS_PER_DAY")
        return int(v) if v else DEFAULT_PER_DAY
    except ValueError:
        return DEFAULT_PER_DAY


def allowed(ip):
    """True if `ip` still has room today; if it does, records the attempt."""
    return _HITS.allow(ip, _per_day())


# ---- POST /api/search-log ----------------------------------------------------

def build_search_log(ip, raw_body, user_agent=""):
    """Pure domain logic for `POST /api/search-log`.

    `ip`: the caller's real IP, already resolved from explicit headers and
    client host by the transport. `raw_body`: the raw request body bytes;
    an empty body is treated as `{}`.

    Returns `(status, payload)`. `payload` is `None` for the 204 case —
    there's no body at all then, or the JSON error dict for a 400."""
    if not allowed(ip):
        return 204, None  # analytics cap: silently ignored, not stored

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:  # noqa: BLE001  (malformed JSON, odd encoding, etc.)
        return 400, {"error": "JSON inválido"}
    if not isinstance(body, dict):
        return 400, {"error": "JSON inválido"}

    q = re.sub(r"\s+", " ", str(body.get("q") or "").strip())
    if not (2 <= len(q) <= 80):
        return 400, {"error": "q inválido"}

    try:
        resultados = int(body.get("resultados"))
    except (TypeError, ValueError):
        return 400, {"error": "resultados inválido"}
    if resultados < 0:
        return 400, {"error": "resultados inválido"}

    origen = str(body.get("origen") or "").strip()[:80] or None

    # sid: random id the page makes up for itself (never stored client-side);
    # it only lets the report glue the prefixes of ONE typing session.
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("sid") or ""))[:40] or None

    db_analytics.log_search(q, resultados, origen, sid)
    # The anonymous stream (off unless TURNOS_ANALYTICS=1): the same search, with its words cut down to the
    # public catalog vocabulary. It never raises and never touches the answer.
    from app.modules.turnos import analytics_export
    analytics_export.record_search(q, resultados, origen, sid, body.get("jid"), ip=ip, user_agent=user_agent)
    return 204, None
