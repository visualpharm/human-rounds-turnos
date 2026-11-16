"""analytics_export.py — the anonymous analytics stream: from `POST /api/ev` and the search beacon into the
installation's own database (core/db_stream.py), plus the daily public-schedule snapshot.

Off unless `TURNOS_ANALYTICS=1` (core/settings.py `AnalyticsSettings`). Nothing leaves the installation by
itself: the data is read by the Reportes screens (analytics_report.py) and by the read-only export API
(analytics_api_router.py). Nothing here may break the site: every public entry point swallows its own failure.

  ingest(body, ...)        a decoded `POST /api/ev` batch -> cleaned (analytics_events.py) -> rows
  record_search(...)       the existing search beacon's text, cut down to the public catalog vocabulary
  start()                  the daily thread: schedule snapshot after 04:00 (installation zone), pruning
"""

import hashlib
import secrets
import threading
import time

from app.core import clock, db_stream, rate_window
from app.core import settings as _settings
from app.modules.turnos import analytics_events as ev

_LOCK = threading.Lock()
_RATE_LOCK = threading.RLock()  # the rate table `_HITS`: `_admit` writes it, `_bound` walks it
_STATE = {"thread": None}
_HITS = {}  # hash(address) or visit id -> [epoch, ...] batches in the last minute (memory only, never stored)
_SALT = secrets.token_hex(8)  # per process: the hashed address cannot be looked up from outside
_RATE_PER_MIN = 1200  # per address: one NAT can be a whole hospital (was 120, which cut it off)
_RATE_PER_VISIT = 60  # per visit id: a visit sends a handful of batches a minute
_MAX_KEYS = 20000  # ceiling of the rate table (addresses + visits), see `_bound`
_EMPTY = {}  # day -> epoch of the last empty snapshot attempt (memory only)
_EMPTY_RETRY = 2 * 3600
_VOCAB = {"at": 0.0, "words": frozenset()}
_VOCAB_TTL = 600
_TICK = 600


def _cfg():
    return _settings.AnalyticsSettings.current()


def enabled():
    try:
        return bool(_cfg().enabled)
    except Exception:  # noqa: BLE001 — a settings failure means «off»
        return False


# ---- vocabulary of the public catalog ----------------------------------------------------------------------

def _collect(value, out):
    if isinstance(value, str):
        out.update(w for w in ev.words(value) if len(w) >= 2)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, out)


def vocabulary(now=None):
    """The folded words of the public catalog: service names and their terms, specialties, centers, the
    doctors' SURNAMES (never given names). Cached ten minutes; empty when the catalog cannot be read, and
    then no search text is stored at all."""
    now = now if now is not None else time.time()
    if now - _VOCAB["at"] < _VOCAB_TTL and _VOCAB["words"]:
        return _VOCAB["words"]
    words = set()
    try:
        from app.patient_web import catalog
        for service in catalog.services():
            for key in ("nombre", "top_label", "palabras"):
                _collect(service.get(key), words)
        for center in catalog.centers():
            for key in ("nombre", "titulo", "name"):
                _collect(center.get(key), words)
        for doctor in catalog.doctors():
            for key in ("apellido", "especialidad"):
                _collect(doctor.get(key), words)
    except Exception:  # noqa: BLE001
        words = set()
    _VOCAB["at"], _VOCAB["words"] = now, frozenset(words)
    return _VOCAB["words"]


# ---- accepting events -------------------------------------------------------------------------------------

def _admit(ip, user_agent, jid):
    """The gate both beacons pass: a robot is refused (no device class), then a ceiling per address and one
    per visit (`jid`). The ceiling per address is high on purpose: the hospital's network is ONE address for
    everybody in the building; what stops a single browser that loops is the limit per visit. The keys are
    a salted hash of the address, in memory only — the address itself is never kept."""
    if ev.device_of(user_agent) is None:
        return False
    who = "a" + hashlib.sha256((_SALT + str(ip or "?")).encode()).hexdigest()[:20]
    visit = ev._ident(jid)
    # One lock around the table: request threads write it and `_bound` walks it, and a dict that changes
    # size during a walk raises. prune_above=None: bounded by `_bound`, not by a scan on each hit.
    with _RATE_LOCK:
        if not rate_window.allow(_HITS, who, _RATE_PER_MIN, 60, prune_above=None):
            return False
        allowed = not visit or rate_window.allow(_HITS, "v" + visit, _RATE_PER_VISIT, 60, prune_above=None)
        _bound()
    return allowed


def _bound():
    """Keeps the in-memory rate table from growing with the number of distinct visit ids (a client that sends
    a new `jid` per request, or a rotation of addresses). Over `_MAX_KEYS`: drop what has no hit left in the
    window; still over: forget the per-visit counters (the per-address ceiling stays); still over: start
    again. The cost is a looser limit while under flood, never memory (accepted: a flood that opens
    20000 live keys in a minute loosens the limit for that minute; before, the table just grew)."""
    if len(_HITS) <= _MAX_KEYS:
        return
    with _RATE_LOCK:
        rate_window.sweep(_HITS, time.time(), 60)
        if len(_HITS) > _MAX_KEYS:
            for key in [k for k in _HITS if k.startswith("v")]:
                del _HITS[key]
        if len(_HITS) > _MAX_KEYS:
            _HITS.clear()


def ingest(body, *, ip, user_agent, own_host=""):
    """Handles one decoded `POST /api/ev` body. Never raises; returns the number of events written."""
    if not enabled():
        return 0
    try:
        items = body.get("events") if isinstance(body, dict) else None
        first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        if not _admit(ip, user_agent, first.get("jid")):
            return 0
        events, _bots = ev.clean_batch(body, user_agent=user_agent, own_host=own_host)
        events = [e for e in events if ev.conforms(e)]
        return db_stream.insert_events(events)
    except Exception:  # noqa: BLE001
        return 0


def record_search(q, results, origin, sid, jid, *, ip="", user_agent=""):
    """Called by the search beacon after its own log line. The text goes through the sanitizer first, and the
    sender through the same gate as `POST /api/ev`: no user agent (or a robot's) means nothing is stored."""
    if not enabled():
        return False
    try:
        if not _admit(ip, user_agent, jid):
            return False
        e = ev.search(q, results, origin, jid, sid, vocab=vocabulary())
        return bool(e and ev.conforms(e) and db_stream.insert_events([e]))
    except Exception:  # noqa: BLE001
        return False


# ---- the daily thread -------------------------------------------------------------------------------------

def snapshot_once(now=None):
    """The public schedule snapshot of today, once per day after 04:00. True when it was written."""
    now = now or clock.now()
    day = now.date()
    if now.hour < 4 or db_stream.has_snapshot(day):
        return False
    # The thread wakes every ten minutes: a build that came back empty (an empty catalog) is not repeated
    # until a couple of hours later — building walks every doctor's agenda for 60 days.
    stamp = now.timestamp()
    if stamp - _EMPTY.get(day, -_EMPTY_RETRY) < _EMPTY_RETRY:
        return False
    from app.modules.turnos import analytics_schedule
    rows = analytics_schedule.build(now)
    if not rows:
        _EMPTY.clear()
        _EMPTY[day] = stamp
        return False
    db_stream.replace_schedule(day, rows)
    return True


def sweep_once(now=None):
    """One pass of the daily work; never raises."""
    now = now or clock.now()
    try:
        snapshot_once(now)
    except Exception:  # noqa: BLE001
        pass
    try:
        db_stream.prune(now, _cfg().retention_days)
    except Exception:  # noqa: BLE001
        pass


def _loop():
    while True:
        if enabled():
            sweep_once()
        time.sleep(_TICK)


def start():
    """Starts the daily thread once per process when the stream is on. Safe to call twice."""
    if not enabled():
        return False
    with _LOCK:
        if _STATE["thread"] is not None and _STATE["thread"].is_alive():
            return True
        t = threading.Thread(target=_loop, name="analytics-stream", daemon=True)
        _STATE["thread"] = t
        t.start()
    return True
