"""Bounded, content-addressed cache for expensive referral AI reads.

The cache deliberately contains only the structured AI result, never an
uploaded file, token, account id, or request metadata. A caller still gets a
fresh pending token and file, and account-specific reconciliation runs again.
The key includes the document bytes, selected service and analysis contract.

This is filesystem-backed because Sana already has a persistent ``/app/data``
volume; Redis is not a runtime dependency. Files are private, short-lived and
bounded in count.
"""

import copy
import hashlib
import json
import os
import tempfile
import time

from app.core import config


_FORMAT = "orden-analysis-cache-v1"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_MAX_ENTRIES = 512
_KEY_LEN = 64


def _root():
    configured = str(config.get("TURNOS_ORDEN_ANALYSIS_CACHE_DIR", "") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ordenes", "analysis-cache")


def _bounded_int(name, default, minimum, maximum):
    try:
        value = int(config.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _ttl_seconds():
    return _bounded_int("TURNOS_ORDEN_ANALYSIS_CACHE_TTL_SECONDS", _DEFAULT_TTL_SECONDS,
                        60, 7 * 24 * 60 * 60)


def _max_entries():
    return _bounded_int("TURNOS_ORDEN_ANALYSIS_CACHE_MAX_ENTRIES", _DEFAULT_MAX_ENTRIES,
                        1, 4096)


def key_for(orden_bytes, orden_mime, servicio_id, analysis_contract):
    """Opaque SHA-256 key. No original filename or patient data is retained."""
    digest = hashlib.sha256()
    for part in (_FORMAT, str(orden_mime or "").lower().strip(), str(servicio_id or "").strip(),
                 str(analysis_contract or "")):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    digest.update(bytes(orden_bytes or b""))
    return digest.hexdigest()


def _path(key):
    if not isinstance(key, str) or len(key) != _KEY_LEN or any(c not in "0123456789abcdef" for c in key):
        return None
    return os.path.join(_root(), f"{key}.json")


def _is_cacheable(ai_json):
    if not isinstance(ai_json, dict):
        return False
    # Temporary provider failures must not become durable cache entries.
    if ai_json.get("verdict") in {"pendiente_ia", "revision_manual"}:
        return False
    if not ai_json.get("pases"):
        return False
    return str(ai_json.get("modelo") or "") != "fake"


def get(key, now=None):
    """Return a detached cached AI result, or ``None`` when absent/expired."""
    path = _path(key)
    if not path:
        return None
    now = time.time() if now is None else now
    try:
        if now - os.path.getmtime(path) > _ttl_seconds():
            os.remove(path)
            return None
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("format") != _FORMAT or payload.get("key") != key:
            return None
        result = payload.get("ai")
        if not _is_cacheable(result):
            return None
        return copy.deepcopy(result)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def put(key, ai_json, now=None):
    """Best-effort private write. Returns whether a cacheable result was kept."""
    path = _path(key)
    if not path or not _is_cacheable(ai_json):
        return False
    now = time.time() if now is None else now
    try:
        os.makedirs(_root(), mode=0o700, exist_ok=True)
        os.chmod(_root(), 0o700)
        # Cached results must never be interpreted as the cost of this upload.
        result = copy.deepcopy(ai_json)
        result["costo_usd"] = 0.0
        payload = {"format": _FORMAT, "key": key, "created": now, "ai": result}
        fd, tmp = tempfile.mkstemp(prefix=".analysis-", dir=_root(), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        cleanup(now)
        return True
    except (OSError, TypeError, ValueError):
        return False


def cleanup(now=None):
    """Remove expired entries and cap storage by least-recently-written files."""
    now = time.time() if now is None else now
    try:
        entries = []
        for name in os.listdir(_root()):
            if not name.endswith(".json") or _path(name[:-5]) is None:
                continue
            path = os.path.join(_root(), name)
            try:
                mtime = os.path.getmtime(path)
                if now - mtime > _ttl_seconds():
                    os.remove(path)
                else:
                    entries.append((mtime, path))
            except OSError:
                continue
        for _mtime, path in sorted(entries)[:-_max_entries()]:
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError:
        pass
