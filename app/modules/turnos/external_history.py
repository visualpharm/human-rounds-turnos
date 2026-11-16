"""Optional, on-demand clinical reads. Memory only; never an AI source."""
import copy
import threading
import time

from app import connectors
from app.core import access_log, config

_cache = {}
_lock = threading.Lock()
_TTL = 300
_MAX_CACHE = 128


def unavailable(reason):
    return {"available": False, "reason": reason, "items": []}


def read(identity, dni):
    # An explicit role check supplements the generic gate, whose permission
    # overrides can otherwise admit non-clinical staff.
    if identity.get("rol") not in {"admin", "medico"}:
        return unavailable("forbidden")
    if not config.external_history_enabled():
        with _lock:
            _cache.clear()
        return unavailable("flag_off")
    user = identity.get("usuario")
    if not user:
        return unavailable("forbidden")
    try:
        # A doctor reads only their own patient (access_log.may_see: assigned
        # or booked in their agenda, or an interview addressed to them), and
        # every read or refused attempt lands in the clinical access log —
        # never the plain DNI (K10, 2026-10-23; this used to write the DNI
        # into the login-panel A/B table, purged after 180 days). A row that
        # can't be written raises, and the except below refuses the read.
        allowed = access_log.may_see(identity, dni)
        access_log.record(identity, "historia_externa", dni, allowed=allowed)
        if not allowed:
            return unavailable("forbidden")
        key = (user, dni)
        now = time.monotonic()
        with _lock:
            for expired in [k for k, (until, _) in _cache.items() if until <= now]:
                del _cache[expired]
            cached = _cache.get(key)
            if cached:
                return copy.deepcopy(cached[1])
        adapter = connectors.adapter_for(connectors.EMR_SYNC)
        if adapter is None:
            return unavailable("not_supported")
        result = adapter.external_clinical_history(dni)
        if (not isinstance(result, dict) or not isinstance(result.get("items"), list)
                or not all(isinstance(item, dict) for item in result["items"])
                or type(result.get("available")) is not bool
                or result.get("reason") not in {"", "flag_off", "forbidden", "not_deployed", "not_supported", "error"}):
            return unavailable("error")
        if not result["available"]:
            return unavailable(result["reason"] or "error")
        if result["reason"]:
            return unavailable("error")
        result = {"available": True, "reason": "", "items": result["items"]}
        # Only successful reads are cached: a Retry really tries again.
        if result.get("available") is True:
            with _lock:
                if len(_cache) >= _MAX_CACHE:
                    del _cache[next(iter(_cache))]
                _cache[key] = (time.monotonic() + _TTL, copy.deepcopy(result))
        return result
    except Exception:
        # PHI can occur even in exception strings from auth/search/providers.
        return unavailable("error")
