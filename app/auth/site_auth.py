"""site_auth.py — HTTP Basic Auth gate delante de TODO el sitio.

Pre-lanzamiento: el sitio corre en un dominio público real pero todavía no
se anunció a nadie — este gate evita que alguien lo encuentre (Google,
curiosidad, el link de Coolify) antes del lanzamiento oficial. NO reemplaza
`admin_auth` (ese sigue protegiendo `/admin` con su propio login); esto tapa
el sitio ENTERO, estáticos incluidos, con una sola credencial compartida.

Env:
  TURNOS_BASIC_AUTH_USER / TURNOS_BASIC_AUTH_PASS — si AMBAS están seteadas,
  el gate exige Basic Auth en cada request. Sin ellas (dev local) el sitio
  queda abierto — mismo patrón de degradación graciosa que turnstile/admin_auth.

Excepciones (2026-07-23): rutas que YA tienen su propia autenticación y que
un tercero externo (Meta/WAHA pegándole al webhook, un cliente MCP, el job
nocturno de prompt-learning) tiene que poder alcanzar SIN la credencial
compartida del sitio — el Basic Auth de pre-lanzamiento los bloquearía con
un 401 antes de que su propio gate corra.
  /api/whatsapp/webhook  la firma X-Hub-Signature-256 de Meta (META_WA_APP_SECRET),
                        el secreto del webhook (TURNOS_WA_WEBHOOK_SECRET) o, en
                        GET, el handshake de verificación de Meta, SON su auth.
  /mcp                  el Bearer token del paciente ES su auth.
  /api/chat-transcripts  el Bearer TURNOS_CHAT_EXPORT_TOKEN ES su auth
                        (chat_api.py) — mismo motivo que /mcp.
  /health, /ready       Tier 4 slice 7 (docs/roadmap/tier4-replatform-
                        design.md) — el healthcheck de despliegue (Coolify
                        hoy no tiene uno configurado contra este container,
                        ver docs/architecture/deploy.md, pero cuando lo
                        tenga no va a poder cargar la credencial
                        compartida). Coincide con lo que slice 0 ya
                        embarcó: el `/health` de app.py (modo proxy) se
                        monta ANTES del catch-all legacy, así que nunca
                        pasó por este gate — esta exención hacía que el
                        modo oscuro (server.py, retirado el 2026-08-27) se
                        comportara igual.
NO exentar `/admin` (probado en prod el 2026-07-27 y revertido en el día): la
página quedaba accesible pero sus ~20 `.js` seguían detrás del Basic Auth, así
que quien no pasó la credencial compartida veía un panel a medio cargar en vez
de un pedido de contraseña. Exentar también los estáticos es exentar el sitio
entero. Que el panel esté detrás de las dos paredes NO desloguea a nadie: el
401 de acá viene sin nuestro JSON `{"error":"no_autenticado"}` y admin.html
(`sesionMurio`) ya no lo confunde con una sesión vencida.

One native transport, one decision
----------------------------------
`SiteAuthMiddleware` runs before every router.  The policy and response bytes
stay transport-neutral so the middleware does not duplicate security logic:

That is why the decision and the 401 bytes live here, transport-neutral, and
the middleware calls them instead of re-implementing:

  - `authorize(path, authorization_header)` — the ONE decision (active + the
    exemptions + header parse + compare_digest). The middleware does not
    duplicate it. `path` MUST be the RAW request path (undecoded)
    — the middleware passes it that way (see `_clean_path`), so an encoded
    delimiter like `%2F` stays a literal and can never reshape a protected
    path into an exemption.
  - `challenge_body()` / `challenge_headers()` — the exact 401 bytes (body,
    realm, Content-Type) emitted by the middleware.

There is exactly one gate per request in either topology.
"""

import base64
import hmac
import os

_USER_ENV = "TURNOS_BASIC_AUTH_USER"
_PASS_ENV = "TURNOS_BASIC_AUTH_PASS"

# 2026-11-13: the two read-only routes of the analytics export carry their own Bearer key (a Basic header
# and a Bearer cannot share the one Authorization header).
_EXENTAS = ("/api/whatsapp/webhook", "/mcp", "/api/chat-transcripts", "/health", "/ready",
            "/api/analytics/events", "/api/analytics/schedule",
            # 2026-11-13: the assistant's OAuth sign-in (ChatGPT cannot answer a Basic challenge)
            "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp",
            "/.well-known/openid-configuration", "/oauth/register", "/oauth/authorize", "/oauth/token")

# The 401 body — fixed text (NOT a translatable string: it must read the same
# in any visitor language, same as the realm). The two functions below are the
# only places that touch it, so the native middleware has one byte contract.
_BODY_TEXT = "Autenticación requerida"
_CONTENT_TYPE = "text/plain; charset=utf-8"


def activo():
    return bool(os.environ.get(_USER_ENV) and os.environ.get(_PASS_ENV))


def _clean_path(path):
    """Canonical path for the exemption check: no query string, no trailing
    slash. The ONE place that normalizes. Operates on the RAW request path
    the ASGI middleware passes in (`scope['raw_path']`; see
    site_auth_middleware._raw_request_path), so an
    encoded delimiter (`%2F` -> `/`, `%3F` -> `?`) stays literal and can never
    reshape a protected path into an exemption. `?` here is a LITERAL `?`
    (the raw request-line query separator), so an encoded `%3F` is NOT split
    off — matching legacy exactly."""
    return str(path or "").split("?", 1)[0].rstrip("/")


def _creds_from_header(authorization_header):
    """(user, pwd) parsed from an Authorization header, or ("", "") if it is
    not valid Basic / corrupt base64 / missing the colon. Shared by
    `authorize` (the one decision) — never duplicate this parse."""
    header = authorization_header or ""
    if not header.startswith("Basic "):
        return "", ""
    try:
        raw = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:  # noqa: BLE001 — corrupt token = empty credentials
        return "", ""
    user, _, pwd = raw.partition(":")
    return user, pwd


def authorize(path, authorization_header):
    """The ONE transport-neutral decision. True if the request may proceed,
    False if it must end in 401. Sends no response — the ASGI middleware owns
    the wire and emits the 401 with `challenge_body()` / `challenge_headers()`.

    `path` MUST be the RAW (undecoded) request path supplied by the ASGI
    middleware from `scope['raw_path']`. Do NOT
    pass a percent-decoded path — that lets an encoded delimiter reshape the
    path after decoding and match an exemption it should not (e.g. `/mcp%2F`
    decoded to `/mcp/` then cleaned to `/mcp`).

    Byte-identical to the semantics `check` had inline before the extraction:
    degrades open with no env, exempts `_EXENTAS`, a corrupt or non-Basic
    header resolves to empty credentials (=> 401), and the match is
    `hmac.compare_digest` constant-time on BOTH user and pass."""
    if not activo():
        return True
    if _clean_path(path) in _EXENTAS:
        return True
    user, pwd = _creds_from_header(authorization_header)
    return (
        hmac.compare_digest(user, os.environ[_USER_ENV])
        and hmac.compare_digest(pwd, os.environ[_PASS_ENV])
    )


def challenge_body():
    """The exact 401 body bytes emitted by the native middleware."""
    return _BODY_TEXT.encode("utf-8")


def challenge_headers():
    """The exact 401 headers WITHOUT Content-Length (each transport computes
    that from `challenge_body()`). Order preserved: WWW-Authenticate,
    Content-Type — in the order the native response emits them."""
    from app.core import theme
    return [
        ("WWW-Authenticate", 'Basic realm="%s"' % theme.brand_name()),
        ("Content-Type", _CONTENT_TYPE),
    ]
