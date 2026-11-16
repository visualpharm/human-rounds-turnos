"""Real request peer compatibility for both serving topologies.

Direct serving receives the client socket peer. Native ASGI controllers pass
their headers and client host explicitly and deliberately fail closed while
``TURNOS_ASGI=1`` unless a valid legacy transition marker is present. No
current production component emits that marker after proxy retirement; the
retained verifier keeps rolling-revision and test compatibility without
trusting a client-supplied forwarding header.
"""

import hashlib
import hmac
from typing import Mapping

from app.core import config
from app.core import settings

HEADER = "X-Turnos-Proxy-Peer"

# Header value format: "<ip>|<hmac-sha256 hex of <ip> with _secret()>".
# A pipe never appears in a valid v4/v6 IP, so a single rsplit is enough.


def _secret():
    """HMAC secret for retained legacy-transition marker verification.

    No current production component signs this marker after proxy retirement.
    `TURNOS_PROXY_TRUST_SECRET` is explicit and preferred;
    without it, it derives from what ALREADY exists
    (`TURNOS_ADMIN_SECRET`/`TURNOS_ADMIN_PASS`) so a fresh install doesn't
    need one more env just for this — same degradation pattern as
    `admin_auth._signing_key()`. Never empty: with no env configured it
    falls to a fixed literal, but that literal is NOT accepted as proof
    under the proxy — `_hay_secreto_real()` rejects it, so `real_peer` fails
    closed when only the fallback exists (the literal is in the source and
    would be forgeable by anyone reaching the internal port). Its remaining
    purpose is only deterministic signing (a stable value so sign/verify
    agree within a process); it never grants trust in ASGI mode. In every
    real deployment `TURNOS_ADMIN_PASS`/`_SECRET` is set, so the fallback is
    never reached — see the docstring above."""
    explicit = config.get("TURNOS_PROXY_TRUST_SECRET", "")
    if explicit:
        base = explicit
    else:
        base = (config.get("TURNOS_ADMIN_SECRET", "")
                or config.get("TURNOS_ADMIN_PASS", "")
                or "turnos-proxy-trust-default")
    return hashlib.sha256(("turnos-proxy-trust$" + base).encode("utf-8")).digest()


def sign(peer_ip):
    """Build a transition marker for compatibility tests and old revisions."""
    peer_ip = str(peer_ip or "")
    mac = hmac.new(_secret(), peer_ip.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{peer_ip}|{mac}"


def header_value(headers: Mapping[str, str] | None, name: str) -> str:
    """`headers[name]`, `""` when it is absent, empty or `headers` cannot be read."""
    try:
        return (headers or {}).get(name) or ""
    except Exception:  # noqa: BLE001
        return ""


def _asgi_activo():
    """True when native ASGI serving is active (`TURNOS_ASGI=1`) — the same
    signal `asgi_main.main()` reads. Taken from the process environment
    (`core.settings.ProcessSettings`), never from `config`/DB: the serving
    mode belongs to the process, not to a setting overridable from the panel."""
    return settings.ProcessSettings.current().asgi


def asgi_activo():
    """Public form of `_asgi_activo` for callers that fail closed under ASGI."""
    return _asgi_activo()


def _hay_secreto_real():
    """True if a signing secret is CONFIGURED (not `_secret()`'s fixed
    fallback literal). A retained transition marker is accepted only with a
    real secret: the literal is in the source, so accepting it would make the
    fail-closed boundary forgeable."""
    return bool(config.get("TURNOS_PROXY_TRUST_SECRET", "")
                or config.get("TURNOS_ADMIN_SECRET", "")
                or config.get("TURNOS_ADMIN_PASS", ""))


def client_host(request) -> str:
    """The socket peer address of a native request, `""` when it has none: the value the routers pass as
    `client_host` to `real_peer_from` and to the login and rate-limit functions."""
    client = getattr(request, "client", None)
    return client.host if client else ""


def real_peer_from(headers: Mapping[str, str] | None, client_host: str) -> str:
    """Resolve the verified peer from explicit request values.

    Native controllers use this value-only boundary so peer verification does
    not require a request-shaped adapter.
    """
    if not _asgi_activo():
        return str(client_host or "")
    raw = header_value(headers, HEADER)
    if raw and _hay_secreto_real():
        peer_ip, _, mac = raw.rpartition("|")
        if peer_ip and mac:
            expected = hmac.new(_secret(), peer_ip.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(mac, expected):
                return peer_ip
    return ""  # fail-closed: see the module docstring
