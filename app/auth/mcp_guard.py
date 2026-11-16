"""Hard resource budgets for the public MCP connector.

Bearer auth says *who* may act; this module limits how much work one token, or
all tokens together, may make the server do. The limits are deliberately
weighted: reading an order costs much more than looking up a service.

All volatile counters are protected by one lock because mcp_api runs under
ThreadingHTTPServer. The daily AI reservation is durable in Postgres (db.py),
so restarting or scaling the app cannot reset the paid-work ceiling.
"""

import os
import re
import threading
import time

_MINUTE = 60
_HOUR = 3600

WEIGHTS = {
    "whoami": 1,
    "search_services": 1,
    "available_appointments": 2,
    "my_requests": 3,
    "cancel_request": 5,
    "request_appointment": 10,
    "analyze_order": 60,
}

_ALLOWED = {
    "whoami": set(),
    "search_services": {"query"},
    "available_appointments": {"service_id"},
    "my_requests": set(),
    "cancel_request": {"id"},
    "request_appointment": {
        "service_id", "date", "time", "preference", "patient_dni", "orden_token",
    },
    "analyze_order": {"file", "data_b64", "filename", "mime", "service_id"},
}

_STRING_LIMITS = {
    "query": 160,
    "service_id": 128,
    "date": 10,
    "time": 5,
    "preference": 500,
    "patient_dni": 16,
    "orden_token": 128,
    "data_b64": 245_000,
    "filename": 255,
    "mime": 100,
}
_FILE_LIMITS = {
    "download_url": 4096,
    "file_id": 256,
    "mime_type": 100,
    "file_name": 255,
}

_LOCK = threading.Lock()
_CALLS = {}          # token -> [epoch]
_TOKEN_WORK = {}     # token -> [(epoch, weight)]
_GLOBAL_WORK = []    # [(epoch, weight)]
_AI_ACTIVE = 0


def _int_env(key, default, minimum=0):
    try:
        value = int(os.environ.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _limits():
    return {
        "per_minute": _int_env("TURNOS_MCP_PER_MINUTE", 30, 1),
        "per_hour": _int_env("TURNOS_MCP_PER_HOUR", 240, 1),
        "token_work_hour": _int_env("TURNOS_MCP_WORK_PER_HOUR", 180, 1),
        "global_work_minute": _int_env("TURNOS_MCP_GLOBAL_WORK_PER_MINUTE", 240, 1),
        "global_work_hour": _int_env("TURNOS_MCP_GLOBAL_WORK_PER_HOUR", 3000, 1),
        "ai_concurrency": _int_env("TURNOS_MCP_AI_CONCURRENCY", 2, 1),
    }


def _trim(now):
    global _GLOBAL_WORK
    for token, xs in list(_CALLS.items()):
        kept = [t for t in xs if now - t < _HOUR]
        if kept:
            _CALLS[token] = kept
        else:
            _CALLS.pop(token, None)
    for token, xs in list(_TOKEN_WORK.items()):
        kept = [(t, w) for t, w in xs if now - t < _HOUR]
        if kept:
            _TOKEN_WORK[token] = kept
        else:
            _TOKEN_WORK.pop(token, None)
    _GLOBAL_WORK = [(t, w) for t, w in _GLOBAL_WORK if now - t < _HOUR]


def validate(name, args):
    """None if valid; otherwise a patient-safe reason."""
    if name not in WEIGHTS:
        return f"tool desconocida: {name}"
    if not isinstance(args, dict):
        return "arguments debe ser un objeto"
    extras = set(args) - _ALLOWED[name]
    if extras:
        return "argumentos no permitidos: " + ", ".join(sorted(extras))
    for key, limit in _STRING_LIMITS.items():
        if key in args and not isinstance(args[key], str):
            return f"{key} debe ser texto"
        if key in args and len(args[key]) > limit:
            return f"{key} es demasiado largo"
    if name == "cancel_request" and (
            isinstance(args.get("id"), bool) or not isinstance(args.get("id"), int)):
        return "id debe ser un número entero"
    if name == "analyze_order":
        has_file = bool(args.get("file"))
        has_b64 = bool(args.get("data_b64"))
        if has_file == has_b64:
            return "mandá exactamente uno: file o data_b64"
        if has_file:
            ref = args["file"]
            if not isinstance(ref, dict):
                return "file debe ser un objeto"
            extras = set(ref) - set(_FILE_LIMITS)
            if extras:
                return "file tiene campos no permitidos"
            if not ref.get("download_url") or not ref.get("file_id"):
                return "file requiere download_url y file_id"
            for key, limit in _FILE_LIMITS.items():
                if key in ref and not isinstance(ref[key], str):
                    return f"file.{key} debe ser texto"
                if key in ref and len(ref[key]) > limit:
                    return f"file.{key} es demasiado largo"
    if name in ("search_services", "available_appointments", "request_appointment"):
        required = "query" if name == "search_services" else "service_id"
        if not str(args.get(required) or "").strip():
            return f"falta {required}"
    if name == "request_appointment":
        if args.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args["date"]):
            return "date debe tener formato YYYY-MM-DD"
        if args.get("time") and not re.fullmatch(r"\d{2}:\d{2}", args["time"]):
            return "time debe tener formato HH:MM"
    return None


def admit(token, name, args, now=None):
    """Return (ok, reason, retry_seconds, ai_lease).

    Invalid calls are rejected before consuming a budget. An ai_lease means
    the caller must invoke release_ai() in a finally block.
    """
    global _AI_ACTIVE
    reason = validate(name, args)
    if reason:
        return False, reason, 0, False
    now = time.time() if now is None else now
    weight = WEIGHTS[name]
    limits = _limits()
    with _LOCK:
        _trim(now)
        calls = _CALLS.get(token, [])
        if sum(1 for t in calls if now - t < _MINUTE) >= limits["per_minute"]:
            return False, "demasiadas llamadas por minuto", 60, False
        if len(calls) >= limits["per_hour"]:
            return False, "demasiadas llamadas por hora", 3600, False
        token_work = _TOKEN_WORK.get(token, [])
        if sum(w for _, w in token_work) + weight > limits["token_work_hour"]:
            return False, "presupuesto de trabajo de esta conexión agotado", 3600, False
        if sum(w for t, w in _GLOBAL_WORK if now - t < _MINUTE) + weight > limits["global_work_minute"]:
            return False, "el servidor está recibiendo demasiado trabajo", 60, False
        if sum(w for _, w in _GLOBAL_WORK) + weight > limits["global_work_hour"]:
            return False, "presupuesto horario del servidor agotado", 3600, False
        if name == "analyze_order" and _AI_ACTIVE >= limits["ai_concurrency"]:
            return False, "hay otros análisis de órdenes en curso", 15, False

        _CALLS.setdefault(token, []).append(now)
        _TOKEN_WORK.setdefault(token, []).append((now, weight))
        _GLOBAL_WORK.append((now, weight))
        lease = name == "analyze_order"
        if lease:
            _AI_ACTIVE += 1
        return True, "", 0, lease


def release_ai():
    global _AI_ACTIVE
    with _LOCK:
        _AI_ACTIVE = max(0, _AI_ACTIVE - 1)


def allow_call_only(token, now=None):
    """Compatibility helper for the old mcp_api._permitido unit contract."""
    now = time.time() if now is None else now
    limits = _limits()
    with _LOCK:
        _trim(now)
        calls = _CALLS.get(token, [])
        if len(calls) >= limits["per_hour"]:
            return False
        calls.append(now)
        _CALLS[token] = calls
        return True


def reserve_ai_daily():
    """Durable, global reservation immediately before a paid MCP image call."""
    limit = _int_env("TURNOS_MCP_AI_PER_DAY", 50, 0)
    if limit <= 0:
        return False, 0, limit
    from app.core import db_ai
    used = db_ai.mcp_ai_reserve(limit)
    return bool(used), int(used or 0), limit


def _reset_for_tests():
    global _GLOBAL_WORK, _AI_ACTIVE
    with _LOCK:
        _CALLS.clear()
        _TOKEN_WORK.clear()
        _GLOBAL_WORK = []
        _AI_ACTIVE = 0
