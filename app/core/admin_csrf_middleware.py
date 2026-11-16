"""admin_csrf_middleware.py — fail-closed origin check for the staff console.

The `st_admin` session cookie is `SameSite=Lax` (auth/admin_auth.py), which
browsers send on cross-site top-level GET navigations and never on cross-site
POSTs from modern browsers — but Lax is a browser default that older clients
relax and some embedders do not enforce. Nothing else checked where a
state-changing admin request came from. This ASGI middleware adds the
defence-in-depth layer OWASP recommends: verify the Fetch Metadata and
Origin request headers on every POST/PUT/PATCH/DELETE aimed at the admin
panel (`/admin`, `/admin/*`) or its API (`/api/admin*`).

Decision (`verdict()`, transport-neutral, unit-tested on its own):

  * `Sec-Fetch-Site: cross-site`  → denied. The browser itself says the
    request was initiated by another site.
  * `Origin` present              → its host must equal the request `Host`
    (case-insensitive; a default port — 443 for https, 80 for http — is the
    same as no port), or be exactly the installation's `TURNOS_PUBLIC_ORIGIN`
    (behind the municipal portal gateway the `Host` the app sees need not be
    the public name the browser used). `Origin: null` (sandboxed frames,
    redirects from data: URLs) is denied. The app reads `Host` directly
    everywhere else (passkeys' rpId, the Turnstile dev-host rule) and only
    `X-Forwarded-Proto` for the scheme, so this check compares against the
    SAME `Host` and consults the same proto header to decide which port is
    the default. `TURNOS_PUBLIC_ORIGIN` is read from the process environment
    and the .env file only, never the database overrides, like
    `core.config.government_installation()`: nothing edited from the panel
    can widen the check.
  * Neither header present        → allowed. Scripted logins
    (`scripts/smoke_prod.py`, the seed scripts), server-to-server calls and
    the test clients send no Origin; a browser always sends one on a
    cross-origin POST, so their absence is not a bypass for the attack this
    guards against.

Everything else — GET/HEAD/OPTIONS, the patient site, the WhatsApp/Meta
webhooks (`/api/whatsapp/webhook`), `/api/account/*`, `/api/requests`,
`/mcp` — is untouched: those are either read-only or deliberately
cross-origin. The HSI Humanizador extension's content script posts from the
panel's own page (same origin), so it passes.

Composition: added in core/middleware_stack.py right after `HeadAsGetMiddleware` and before
`LegacyUrlRewriteMiddleware`, so it runs INSIDE the rewrite and sees the path
after a historical alias has been canonicalised — a legacy `/api/...`
spelling of an admin endpoint cannot sidestep the prefix match. The 403
reuses the flat JSON error shape every admin consumer already parses
(`{"error": ...}`, `Cache-Control: no-store`).
"""
import json
from urllib.parse import urlsplit

from app.core import settings

_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_PORT = {"https": "443", "http": "80"}
ERROR_BODY = {"error": "origen_no_permitido"}


# Patient-side writes that hand out a credential: rotating the MCP token is
# the one a forged cross-site request must not be able to trigger (2026-11-13).
_PATIENT_PATHS = ("/api/account/mcp/regenerate", "/api/account/mcp/revoke")

# The patient's own login (security review 2026-11, item 18, login CSRF): a page of another site could POST
# a form or a `text/plain` fetch (no preflight) and sign the visitor into the attacker's account. It takes
# JSON only and the same Origin / Fetch Metadata check as the panel.
_JSON_ONLY_PATHS = ("/api/account/login",)


def is_admin_path(path):
    """True for the admin panel and its API, plus the few patient-side
    credential writes in `_PATIENT_PATHS` and `_JSON_ONLY_PATHS`; everything else is out of scope."""
    if not path:
        return False
    if path.rstrip("/") in _PATIENT_PATHS or path.rstrip("/") in _JSON_ONLY_PATHS:
        return True
    return path == "/admin" or path.startswith("/admin/") or path.startswith("/api/admin")


def _host_key(authority, scheme):
    """Normalise `host[:port]` for comparison: lowercase, default port dropped."""
    authority = (authority or "").strip().lower()
    if not authority:
        return ""
    if authority.startswith("["):  # IPv6 literal
        host, _, rest = authority.partition("]")
        host += "]"
        port = rest[1:] if rest.startswith(":") else ""
    else:
        host, _, port = authority.partition(":")
    if port and port == _DEFAULT_PORT.get(scheme or ""):
        port = ""
    return host + (":" + port if port else "")


def _public_origin_key():
    """`(scheme, host key)` of TURNOS_PUBLIC_ORIGIN, or None when unset or
    unparseable (then only the request Host counts)."""
    origin = settings.InstallationSettings.current().public_origin
    if not origin:
        return None
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in _DEFAULT_PORT or not parts.netloc:
        return None
    return scheme, _host_key(parts.netloc, scheme)


def verdict(method, path, headers):
    """`(allowed, reason)` for one request. `headers` is any case-insensitive
    mapping with `.get()` (a Starlette `Headers`, or a plain dict with
    lowercase keys in tests). `reason` is `None` when allowed, else a short
    machine tag — never echoed to the client."""
    if method.upper() not in _METHODS or not is_admin_path(path):
        return True, None
    if path.rstrip("/") in _JSON_ONLY_PATHS:
        kind = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            return False, "content_type_not_json"
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site == "cross-site":
        return False, "sec_fetch_site_cross_site"
    origin = (headers.get("origin") or "").strip()
    if not origin:
        return True, None
    if origin.lower() == "null":
        return False, "origin_null"
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False, "origin_unparseable"
    if not parts.netloc or parts.path not in ("", "/") or parts.query or parts.fragment:
        return False, "origin_malformed"
    origin_scheme = (parts.scheme or "").lower()
    origin_key = _host_key(parts.netloc, origin_scheme)
    public = _public_origin_key()
    if public and (origin_scheme, origin_key) == public:
        return True, None
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    request_scheme = proto or origin_scheme
    expected = _host_key(headers.get("host"), request_scheme)
    if not expected:
        return False, "host_missing"
    if origin_key != expected:
        return False, "origin_host_mismatch"
    return True, None


def _header(scope, name):
    wanted = name.encode("latin-1")
    for key, value in scope.get("headers") or ():
        if key.lower() == wanted:
            return value.decode("latin-1")
    return ""


class _ScopeHeaders:
    """Minimal case-insensitive `.get()` over raw ASGI headers."""

    def __init__(self, scope):
        self._scope = scope

    def get(self, name):
        return _header(self._scope, name)


class AdminCsrfMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        allowed, _reason = verdict(
            scope.get("method") or "GET", scope.get("path") or "", _ScopeHeaders(scope),
        )
        if allowed:
            await self.app(scope, receive, send)
            return
        body = json.dumps(ERROR_BODY, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
