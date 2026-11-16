"""turnstile.py — dueño único del gate de Cloudflare Turnstile.

Una sola pantalla de Turnstile en Cloudflare sirve a todo el sitio (admin,
login de paciente, /api/analyze-order, /api/preparacion). Antes cada módulo
(admin_auth.py, accounts.py, abuse_guard.py) tenía su copia del par de envs + su
verify contra Cloudflare; ahora todos delegan acá.

  activo_from(headers)                  ¿corre el gate para ESTE request?
  sitekey_from(headers)                 sitekey a servir, o None si no corre
  verificar_from(headers, peer, token)  valida contra Cloudflare
  client_ip_from(headers, peer)         resuelve la IP del cliente

Turnstile es un desafío anti-bot A PROPÓSITO: un curl o un browser headless no
puede resolverlo (no hay usuario real detrás), así que ninguna verificación
automatizada de prod puede ejercitar el flujo de login completo sin ayuda.
`TURNOS_TURNSTILE_TEST_BYPASS_SECRET` (env de prod, nunca en el repo) es esa
ayuda: si está seteado y el request trae el header `X-Turnstile-Test-Bypass`
con el mismo valor, `verificar()` pasa sin llamar a Cloudflare. Sirve SOLO
como credencial — igual de sensible que la contraseña de admin — nunca se
sirve al cliente ni se referencia desde JS. Sin el env seteado (default en
cualquier instalación que no lo agregue) esta rama no existe.

El gate NO corre cuando:
  - el Host del request es de desarrollo (localhost, 127.0.0.1, un IP privado,
    *.local, o un hostname de una sola etiqueta como `devbox`), o
  - no hay TURNOS_TURNSTILE_SECRET configurado, o
  - TURNOS_TURNSTILE_OFF=1, o
  - está apagado desde Configuración en el panel (setting `config/turnstile`).

El host de dev se evalúa PRIMERO y no lo revierte ningún setting: en local el
gate está apagado siempre, pase lo que pase en la base (responsable del producto, 2026-07-19).

El chequeo por Host existe porque el sitekey de Cloudflare está atado a los
hostnames del dominio de producción: en `devbox:7700` el widget nunca resuelve y
el usuario queda trabado en "Esperá a que cargue la verificación anti-bot".
Cliente y servidor tienen que coincidir en si el gate corre — por eso la misma
función decide qué sitekey se sirve y si se exige el token.
"""

import hmac
import ipaddress
import json
import time
import urllib.parse
import urllib.request
from typing import Mapping
from app.core import config
from app.core import proxy_trust

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

_DEV_SUFIJOS = (".local", ".lan", ".internal", ".test", ".localhost")


def _env(key, default=""):
    return config.get(key, default)


def _secret():
    return _env("TURNOS_TURNSTILE_SECRET")


def _sitekey_env():
    return _env("TURNOS_TURNSTILE_SITEKEY")


def _apagado():
    return (_env("TURNOS_TURNSTILE_OFF") or "").strip().lower() in ("1", "true", "yes")


_header_value = proxy_trust.header_value


def host_from(headers: Mapping[str, str] | None) -> str:
    h = _header_value(headers, "Host")
    h = h.strip().lower()
    if h.startswith("["):  # IPv6 literal: [::1]:7700
        return h.split("]", 1)[0].lstrip("[")
    return h.split(":", 1)[0]


def host_de_dev_from(headers: Mapping[str, str] | None) -> bool:
    """Value-only development-host decision for native controllers."""
    h = host_from(headers)
    if not h:
        return False
    if h.endswith(_DEV_SUFIJOS):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback or ip.is_private
    except ValueError:
        pass
    return "." not in h


def _peer_is_public(headers, client_host) -> bool:
    """Is the verified peer a public address (or unknown under ASGI, fail
    closed)? The Host header is typed by the client; only a peer on the
    loopback or a private network may claim to be a development host."""
    peer = proxy_trust.real_peer_from(headers, client_host)
    if not peer:
        return proxy_trust.asgi_activo()
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return True  # unparseable peer: fail closed, the gate runs
    return (getattr(ip, "ipv4_mapped", None) or ip).is_global


def activo_from(headers: Mapping[str, str] | None, client_host: str | None = None) -> bool:
    """Value-only Turnstile activation decision for native controllers.

    With `client_host` (every path that ENFORCES the gate) a development Host
    exempts the request only when the verified peer is not public: a stranger
    sending `Host: localhost` to the origin still faces the gate (security
    review 2026-11, M-5). Without it (display of the widget) the Host alone
    decides, as before."""
    if host_de_dev_from(headers) and (client_host is None or not _peer_is_public(headers, client_host)):
        return False
    if not _secret() or not _sitekey_env() or _apagado():
        return False
    return encendido()


# ---- setting editable desde el panel (Configuración) -----------------------
# Mismo patrón que auth_mode.py/theme.py: un solo valor global en la tabla
# `catalogo` (tipo="config", id="turnstile", doc={"on": bool}), sin tabla
# nueva. El env TURNOS_TURNSTILE_OFF sigue siendo el interruptor de
# emergencia: si está en 1, no hay setting que lo encienda.

OPCIONES = {
    "on": {
        "id": "on",
        "label": "Activada",
        "desc": "Cloudflare Turnstile revisa que del otro lado haya una persona antes de dejar entrar o subir una orden.",
    },
    "off": {
        "id": "off",
        "label": "Desactivada",
        "desc": "Nadie ve la verificación. Los límites por IP (intentos de login, análisis de órdenes) siguen funcionando igual.",
    },
}
DEFAULT_ON = True

_cache = {"v": None, "t": 0.0}
_TTL = 3.0


def encendido():
    """¿Está encendido el gate según Configuración? El valor guardado gana; si
    falta —o la base no está— vale el default. La base caída NO apaga el gate:
    se falla del lado seguro. Cacheado ~3 s para no pegarle a la DB por request."""
    now = time.time()
    if _cache["v"] is not None and now - _cache["t"] < _TTL:
        return _cache["v"]
    val = DEFAULT_ON
    try:
        from app.core import db_catalog
        row = db_catalog.catalog_get("config", "turnstile")
        if row:
            doc = row.get("doc") or {}
            if "on" in doc:
                val = bool(doc["on"])
    except Exception:  # noqa: BLE001 — DB caída => default, el gate nunca se cae por esto
        pass
    _cache["v"] = val
    _cache["t"] = now
    return val


def guardar(on):
    """Persiste el encendido/apagado del gate. Invalida el cache local."""
    from app.core import db_catalog
    val = bool(on)
    db_catalog.catalog_upsert("config", "turnstile", {"on": val})
    _cache["v"] = None
    _cache["t"] = 0.0
    return val


def invalidar_cache():
    """Para los tests: olvida lo cacheado y vuelve a leer la base."""
    _cache["v"] = None
    _cache["t"] = 0.0


def sitekey_from(headers: Mapping[str, str] | None) -> str | None:
    """Value-only sitekey lookup for native controllers."""
    if not activo_from(headers):
        return None
    return _sitekey_env() or None


def client_ip_from(headers: Mapping[str, str] | None, client_host: str) -> str:
    # FAIL-CLOSED: el peer verificado por proxy_trust es el único valor confiable.
    # Los headers CF-Connecting-IP y X-Forwarded-For son controlables por el
    # cliente cuando el request viene directo al origen (sin CDN delante), así
    # que no se pueden confiar para el throttle de login. Ver proxy_trust.py.
    #
    # Si proxy_trust.real_peer_from devuelve vacío (sin verificación real),
    # usamos client_host como fallback — no es ideal pero es mejor que aceptar
    # headers falseables, y al menos el throttle sigue funcionando en modo
    # desarrollo donde el proxy no está activo.
    verified = proxy_trust.real_peer_from(headers, client_host)
    if verified:
        return verified
    # Fallback sólo para desarrollo/local: client_host viene del socket peer.
    # En producción con ASGI activo, real_peer_from ya habría devuelto el IP
    # verificado si el header X-Turnos-Proxy-Peer estaba presente y firmado.
    return str(client_host or "")


def _bypass_secret():
    return _env("TURNOS_TURNSTILE_TEST_BYPASS_SECRET")


def bypass_activo_from(headers: Mapping[str, str] | None) -> bool:
    """True si el request trae el header de bypass de testing y coincide con
    el secret configurado. Sin TURNOS_TURNSTILE_TEST_BYPASS_SECRET seteado,
    siempre False. Recibe sólo el mapping de headers para que los controladores
    nativos no necesiten fabricar un objeto request-like."""
    secret = _bypass_secret()
    if not secret:
        return False
    got = _header_value(headers, "X-Turnstile-Test-Bypass").strip()
    return bool(got) and hmac.compare_digest(got, secret)


def verificar_from(headers: Mapping[str, str] | None, client_host: str,
                   token: str, blando: bool = False) -> bool:
    """Validate Turnstile using explicit native request values."""
    if bypass_activo_from(headers):
        return True
    if not activo_from(headers, client_host):
        return True
    if not token:
        return bool(blando)
    data = urllib.parse.urlencode({
        "secret": _secret(), "response": token,
        "remoteip": client_ip_from(headers, client_host),
    }).encode("ascii")
    try:
        req = urllib.request.Request(_VERIFY_URL, data=data)
        with urllib.request.urlopen(req, timeout=8) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        return bool(out.get("success"))
    except Exception:  # noqa: BLE001
        return bool(blando)
