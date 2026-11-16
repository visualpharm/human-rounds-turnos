"""ip_exempt.py — IPs exempt from Cloudflare Turnstile at the admin login.

Only a VERIFIED loopback peer can be exempt. History: the list used to also
seed the product owner's home IPv4/IPv6 and honor them via the old request-bound IP resolver
(CF-Connecting-IP first, then X-Forwarded-For). That was retired on
2026-08-08 (Tier 4 cutover readiness): there is NO CDN in front of either
site (deploy.md, verified 2026-08-05), so those headers are client-suppliable
end to end — any public client could claim the product owner's home IP and skip Turnstile
at the admin login, in both serving topologies. Scripted/monitoring logins
now use the signed bypass header instead (`X-Turnstile-Test-Bypass` vs the
`TURNOS_TURNSTILE_TEST_BYPASS_SECRET` env, constant-time — see
the signed bypass check), which is unforgeable without the secret.

The list itself (exact IPs, never CIDRs — "a CIDR would exempt all of
Pinamar", product owner, explicitly rejected) stays configurable from the panel
(Settings → Seguridad, `settings_admin.py`), stored under
`TURNOS_ADMIN_IP_EXEMPT` (CSV) in the same Settings storage (config.py).
Since the retirement, only loopback entries can ever match — the panel list
works as a kill switch for the localhost exemption, not as a grant for
remote IPs.

`exento_from(headers, client_host)` resolves the peer through the explicit
value boundary in `proxy_trust`. With TURNOS_ASGI=1 the check fails closed
without a valid transition marker. A forwarded-IP header is never consulted.
"""

import ipaddress
from typing import Mapping

from app.core import config
from app.core import proxy_trust

CONFIG_KEY = "TURNOS_ADMIN_IP_EXEMPT"

# Loopback only (2026-08-08). The product owner's home IPs were seeded here from
# 2026-08-03 until the header-trust retirement described in the module
# docstring — an entry that is not a verified loopback peer can no longer
# take effect, so seeding one would only mislead.
DEFAULT_IPS = (
    "127.0.0.1",
    "::1",
)


def _normalizar(raw):
    """Representación canónica de una IP v4/v6 EXACTA, o None si `raw` no es
    una IP válida (`ipaddress.ip_address` ya rechaza cualquier cosa con "/",
    así que una CIDR nunca pasa). Un v4-mapped IPv6 (`::ffff:192.0.2.4`, común
    en sockets dual-stack) se normaliza a su IPv4 equivalente, para que
    compare igual contra el v4 puro guardado en la lista."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    return str(ip)


def parse_lista(raw):
    """Parsea el textarea del panel (una IP por línea, o separadas por
    coma) a una lista de IPs normalizadas y sin duplicados. Devuelve
    (lista, None) o (None, mensaje) si algún token no es una IP exacta
    válida."""
    tokens = [t.strip() for t in str(raw or "").replace(",", "\n").splitlines() if t.strip()]
    out = []
    for t in tokens:
        ip = _normalizar(t)
        if not ip:
            return None, f"«{t}» no es una IP válida (sólo IPs exactas, nunca rangos ni subredes)"
        if ip not in out:
            out.append(ip)
    return out, None


def list_exempt():
    """La lista guardada, o el seed por defecto si nunca se tocó. Cualquier
    token corrupto que hubiera quedado guardado (no debería pasar, `_save`
    ya valida) se descarta en silencio en vez de romper el login."""
    raw = config.get(CONFIG_KEY, default=",".join(DEFAULT_IPS))
    ips, _ = parse_lista(raw or "")
    return ips or []


def set_exempt(ips):
    """Persiste la lista (ya validada por el caller — ver settings_admin._guardar_seguridad)."""
    config.guardar({CONFIG_KEY: ",".join(ips)})


def _es_loopback(ip_str):
    try:
        return ipaddress.ip_address(ip_str).is_loopback
    except ValueError:
        return False


def exento_from(headers: Mapping[str, str] | None, client_host: str) -> bool:
    """Value-only loopback exemption check for native request controllers."""
    exentas = set(list_exempt())
    if not exentas:
        return False
    peer = _normalizar(proxy_trust.real_peer_from(headers, client_host))
    return bool(peer and peer in exentas and _es_loopback(peer))
