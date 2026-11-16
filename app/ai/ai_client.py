"""ai_client.py — single AI inference HTTP call path for every AI caller.

Before this module, four call sites (`ai_referral.py`, `chat_agent.py`,
`prompts.py`, `ocr_dni.py` — the last one reuses ai_referral's) each opened
their own `urllib.request` to OpenRouter with their own headers/body shape.
This module owns ONLY the wire-level POST + the backup-key failover; each
caller keeps building its own request body (different `response_format`,
`max_tokens`, `tools`, `X-Title`) and its own model-level fallback (the
":free" model ai_referral/ocr_dni retry when the paid one has no readings —
that stays where it is, it's a MODEL choice, not a transport concern).

Backup key failover (the product owner's item 6, 2026-07-30): OpenRouter keys carry a
spend cap; when the active key hits it, OpenRouter answers 402 ("requires
balance") or 403 ("Key limit exceeded"). Those two codes — and ONLY those —
are worth retrying with a second key (`OPENROUTER_API_KEY_BACKUP`, admin-
settable via config.py same as the primary). Everything else that ai_health
already treats as "the service is down" (401/408/409/429/5xx, timeouts,
network) would fail the SAME way on a second key, so it is not retried here
— that stays ai_health's job to detect and degrade gracefully.

Sticky active key (avoids double-hitting a dead key on every single call):
once a call proves the primary is out of quota, subsequent calls go straight
to the backup for `_STICKY_TTL` seconds instead of re-trying the primary
first every time. After the TTL, the primary is tried again (a daily cap
resets at some point; there's no cheaper way to notice than trying).

TURNOS_FAKE_AI short-circuits stay in each caller (they return BEFORE even
building a request body, since the shapes differ) — this module is only ever
reached with a real network call in mind.
"""

import ipaddress
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core import ai_policy, config

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_PROFILES = frozenset({"cloud_shared", "hosted_jurisdiction", "self_hosted_vllm"})

# 402 "requires at least $X in balance" / 403 "Key limit exceeded" — the
# KEY's own quota/auth, worth trying a second key. Broader outage codes
# (429/5xx/etc) are ai_health's "service is down" concern, not a key switch.
_KEY_FAILOVER_CODES = frozenset({402, 403})

_STICKY_TTL = 300.0  # 5 min

_lock = threading.Lock()
_active = {"kind": "primary", "since": 0.0}


def profile():
    """The selected inference profile, or ``""`` for invalid runtime config.

    An installation with none of the Tier 6 fields continues on cloud_shared.
    Invalid environment configuration fails closed rather than repointing a
    credential at an unexpected destination.
    """
    value = (config.get("AI_INFERENCE_PROFILE") or "cloud_shared").strip().lower()
    return value if value in _PROFILES else ""


def _is_private_http_host(host):
    """Whether ``host`` is a non-public destination safe for plain HTTP.

    Do not accept arbitrary names here: resolving an internal hostname before
    connecting leaves a DNS-rebinding gap. ``localhost`` and literal loopback
    or RFC1918 addresses have a stable, local-only meaning for this transport.
    """
    if (host or "").lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return (isinstance(address, ipaddress.IPv4Address) and
            (address in ipaddress.ip_network("10.0.0.0/8") or
             address in ipaddress.ip_network("172.16.0.0/12") or
             address in ipaddress.ip_network("192.168.0.0/16")))


def validate_destination(profile_name, base_url):
    """Returns a value-free validation error, or ``None`` for a safe URL.

    The configured value is a complete OpenAI-compatible chat-completions
    endpoint, never a host prefix. User info, queries and fragments have no
    safe use in that endpoint and are rejected before storage or egress.
    """
    if profile_name not in _PROFILES:
        return "AI inference profile is invalid."
    raw = str(base_url or "").strip()
    if profile_name == "cloud_shared":
        return None if not raw else "Cloud shared endpoint cannot be changed."
    parsed = urllib.parse.urlparse(raw)
    if (not raw or parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path in ("", "/")):
        return "AI inference endpoint is invalid."
    try:
        _ = parsed.port
    except ValueError:
        return "AI inference endpoint is invalid."
    if profile_name == "hosted_jurisdiction" and parsed.scheme != "https":
        return "Hosted inference requires HTTPS."
    if profile_name == "self_hosted_vllm" and parsed.scheme == "http" and not _is_private_http_host(parsed.hostname):
        return "Self-hosted HTTP requires localhost, loopback, or RFC1918."
    return None


def _destination_for(profile_name):
    """Validated destination for one already-selected profile."""
    if not profile_name:
        raise ValueError("AI inference profile is invalid.")
    if profile_name == "cloud_shared":
        return OPENROUTER_URL
    base_url = (config.get("AI_INFERENCE_BASE_URL") or "").strip()
    error = validate_destination(profile_name, base_url)
    if error:
        raise ValueError(error)
    return base_url


def destination():
    """The selected endpoint after runtime validation, or raises safely."""
    return _destination_for(profile())


def _config_value(key):
    return (config.get(key) or "").strip()


def _keys(profile_name=None):
    """Active profile's key pair. Non-cloud profiles never see OpenRouter keys."""
    profile_name = profile() if profile_name is None else profile_name
    if profile_name == "cloud_shared":
        primary = _config_value("AI_INFERENCE_API_KEY") or _config_value("OPENROUTER_API_KEY")
        backup = _config_value("AI_INFERENCE_API_KEY_BACKUP") or _config_value("OPENROUTER_API_KEY_BACKUP")
        return primary, backup
    if profile_name in ("hosted_jurisdiction", "self_hosted_vllm"):
        return (_config_value("AI_INFERENCE_API_KEY"),
                _config_value("AI_INFERENCE_API_KEY_BACKUP"))
    return "", ""


def primary_key():
    return _keys()[0]


def backup_key():
    return _keys()[1]


def _transport_snapshot():
    """One immutable destination/key snapshot for a single outbound call."""
    profile_name = profile()
    base_url = _destination_for(profile_name)
    primary, backup = _keys(profile_name)
    return profile_name, base_url, primary, backup


def transport_snapshot():
    """One coherent inference snapshot for internal status/probe consumers.

    It is intentionally not a response payload: its key fields are only for
    the process that will make a bounded provider-status request. A caller
    must not re-read ``profile()`` or ``primary_key()`` after taking it.
    """
    try:
        profile_name, base_url, primary, backup = _transport_snapshot()
    except ValueError:
        return {"profile": "", "destination": "", "primary_key": "",
                "backup_key": "", "configured": False}
    return {"profile": profile_name, "destination": base_url,
            "primary_key": primary, "backup_key": backup,
            "configured": bool(primary or backup)}


def available():
    """True when the active profile has a safe destination and a key.

    This is the profile-neutral guard every AI caller uses before attempting a
    request. It deliberately does not consult ai_health's circuit breaker.
    """
    return not ai_policy.disabled() and bool(transport_snapshot()["configured"])


def _sticky_backup_active():
    with _lock:
        if _active["kind"] != "backup":
            return False
        return (time.time() - _active["since"]) < _STICKY_TTL


def _mark_active(kind):
    with _lock:
        if kind == "backup":
            _active["kind"] = "backup"
            _active["since"] = time.time()  # refresh stickiness on every use
        elif _active["kind"] != "primary":
            _active["kind"] = "primary"
            _active["since"] = 0.0


def active_key_kind():
    """'primary' | 'backup' | 'none' — for probes/status displays (ai_health,
    system_status). Reflects the sticky in-memory state; makes no network
    call."""
    if not available():
        return "none"
    pk, bk = primary_key(), backup_key()
    if not pk and not bk:
        return "none"
    if bk and _sticky_backup_active():
        return "backup"
    return "primary" if pk else "backup"


def reset_sticky_for_tests():
    """Only for tests: forces the next call() to re-try the primary key."""
    with _lock:
        _active["kind"] = "primary"
        _active["since"] = 0.0


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Keep credentials on the configured destination for every profile."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _post(body, api_key, timeout, headers_extra=None, base_url=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if headers_extra:
        headers.update(headers_extra)
    req = urllib.request.Request(
        base_url or destination(), data=json.dumps(body).encode("utf-8"),
        method="POST", headers=headers,
    )
    opener = urllib.request.build_opener(_RefuseRedirects())
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call(body, timeout=45, headers_extra=None, api_key=None):
    """POSTs `body` (the already-built OpenRouter chat/completions payload)
    with backup-key failover.

    api_key: explicit override (a caller that already resolved its own key —
    e.g. a test, or a caller migrated before this existed). None (the normal
    case) resolves from config, honoring the sticky active key so a run of
    calls while the primary is out of quota doesn't re-fail on it first.

    On a 402/403 with the key actually used, retries ONCE with
    OPENROUTER_API_KEY_BACKUP if one is configured and different from the
    key that just failed, and marks it as the sticky active key on success.

    Raises the LAST exception if every attempt fails — same contract every
    caller already had (their except/ai_health.report blocks are unchanged).
    """
    if ai_policy.disabled():
        raise ValueError("AI inference is disabled.")
    profile_name, base_url, pk, bk = _transport_snapshot()
    if not (pk or bk) and (api_key is None or profile_name != "cloud_shared"):
        raise ValueError("AI inference is not configured.")
    if api_key is not None and profile_name == "cloud_shared":
        key = api_key
    elif bk and _sticky_backup_active():
        key = bk
    else:
        key = pk or bk

    try:
        data = _post(body, key, timeout, headers_extra, base_url)
        _mark_active("backup" if (bk and key == bk) else "primary")
        return data
    except urllib.error.HTTPError as e:
        if e.code in _KEY_FAILOVER_CODES and bk and key != bk:
            # Swallowed here, so close it here: HTTPError is file-like and
            # keeps the response body open. The re-raise path stays open on
            # purpose — callers read e's body for the error detail.
            e.close()
            data = _post(body, bk, timeout, headers_extra, base_url)
            _mark_active("backup")
            return data
        raise
